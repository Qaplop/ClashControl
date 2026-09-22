# Capital Raid Weekend tracking + leaderboards — implementation plan

Status: **implemented** 2026-09-22, build 52 (tracker #0115). Tests: tests/unit/test_capital_raids.py.

Goal: track Clan Capital raid weekends for every guild **member clan**, store per-player results
in the DB, and expose them through the existing `/leaderboard` command (single season, month,
month range, year — same period syntax as the war boards), plus a "missed raid weekend" penalty
list and configurable "you haven't raided yet" reminders (DM + channel, like the war reminders).

Project-owner decisions (2026-09-22):
- **Q1:** `raidmissed` over a period **sums missed weekends per player**, sorted by that total
  (highest first).
- **Q2:** Show the medals estimate. `offensive_reward × attacks + defensive_reward` was checked
  in-game and matches exactly.
- **Q3:** A player only shows up in a raid (`members[]`) once they make their first attack,
  which can happen any time while the season is ongoing. So "missed" is only meaningful **once
  the season has ended**. While it's ongoing the same data means **"not attacked yet"**. That
  list is shown on `currentraid` and drives a new reminder, sent 12 h or 24 h before season end
  and configurable like the missed-war notification (§5).
- **Round 2:** reminder scope configurable (0 attacks by default, or any attacks left); exactly
  one reminder per season; optional separate raid channel that falls back to the war channel;
  **only players in the clan before season start are eligible**, so post-start joiners are never
  listed, reminded or penalized (§2.1). No reminders on DEV (intended). Full list in §10.

---

## 1. What the API actually returns (verified against `response_1790071441041.json`)

`GET /clans/{clanTag}/capitalraidseasons` → `{"items": [...], "paging": {"cursors": {...}}}`,
newest season first. The sample (no `limit`) has 20 seasons.

Per season (`items[i]`):

| field | sample | notes |
|---|---|---|
| `state` | `"ended"` | coc.py documents `ongoing` / `ended` |
| `startTime` / `endTime` | `20260918T070000.000Z` / `20260921T070000.000Z` | always Fri 07:00 UTC → Mon 07:00 UTC |
| `capitalTotalLoot`, `raidsCompleted`, `totalAttacks`, `enemyDistrictsDestroyed` | 854876, 10, 198, 78 | clan totals |
| `offensiveReward`, `defensiveReward` | 209, 211 | medal values (see §4.3) |
| `members[]` | 34 entries | **only on `items[0]`** |
| `attackLog[]`, `defenseLog[]` | 9 / 3 clans | per-attack detail (`districts[].attacks[]`) **only on `items[0]`** |

What that means for the design:

1. **`members[]` lists only players who attacked.** All 34 have `attacks >= 2`. Their summed
   `attacks` (198) equals `totalAttacks`, and their summed `capitalResourcesLooted` equals
   `capitalTotalLoot` exactly. The 34 attacker tags in `attackLog` are the same set as `members`.
   **A player who did not attack is not in the response at all.** So the penalty list has to come
   from the clan roster minus `members[]`. It can't be read from the API.
2. **Per-player data exists only for the newest season.** Seasons 2–20 have clan totals only (no
   `members`, no per-attack `attacks[]`). Consequences:
   - Backfilling player history is impossible. Paging through older seasons gets clan totals
     only, so the leaderboards start from the first weekend the bot records.
   - **Data-loss window:** an ended season keeps its `members[]` only until the next season
     becomes `items[0]` (next Friday 07:00 UTC at the latest). A season that isn't finalized by
     then is lost for good. §3 therefore adds a catch-up rule on top of the Tue–Thu skip.
3. `attackLimit` = 5 and `bonusAttackLimit` = 1 for **every** member in the sample, including
   the two with 2 and 4 attacks. So `bonusAttackLimit` isn't a reliable "earned the 6th attack"
   flag from this sample alone. **Resolved (§10 Q-A):** a later live response shows
   `bonusAttackLimit: 0` for 1-attack players, so it is the bonus actually **earned**.
4. We don't store `attackLog` / `defenseLog`. They are ~95% of the payload, and no leaderboard
   here needs them. Per-district analytics could be added later from the same fetch.

### Pagination (`limit` / `after` / `before`)

From coc.py `entry_logs.LogPaginator` and the API contract:

- `limit=N` returns the N newest seasons. **`limit=1` is exactly what we need**: `items[0]` is
  the only entry with per-player data.
- The response's `paging.cursors` then holds `after` (present when older items exist) and
  `before` (present when newer items exist before this page). Both are opaque strings. You pass
  one back as `after=`/`before=` together with `limit`, never both (the API rejects that).
  Without `limit` everything comes back in one page, hence the empty `"cursors": {}` in the sample.
- The Supercell cursors are offset-based: base64 JSON of the form `{"pos": N}`. They are
  **not stable IDs**, so if a new season appears between two page requests, every page shifts
  by one. We don't need pagination at all (see 2 above). *Verify once:* run the dev-portal call
  with `limit=1` and check that `paging.cursors.after` is present and decodes to `{"pos":1}`.
- coc.py's `RaidLog` paginator only follows `after` (`options()` sets `after=_next_page`). It
  never uses `before`.

### coc.py support (installed 4.0.0)

Full support:
`client.get_raid_log(clan_tag, cls=None, page=False, *, limit=0, after="", before="")` →
`RaidLog` (supports iteration and `[i]`), which yields `RaidLogEntry` (`state`, `start_time`,
`end_time`, `total_loot`, `completed_raid_count`, `attack_count`, `destroyed_district_count`,
`offensive_reward`, `defensive_reward`, `members` → `RaidMember` with `tag`, `name`,
`attack_count`, `attack_limit`, `bonus_attack_limit`, `capital_resources_looted`, plus
`attack_log`/`defense_log` → `RaidClan` → `RaidDistrict` → `RaidAttack`). The underlying call is
`client.http.get_clan_raid_log(tag, limit=..)`, which returns the raw dict. The HTTP layer's FIFO
cache respects `Cache-Control: max-age`, and `Route` URL-encodes `#`.

**We call `coc_client.http.get_clan_raid_log(tag, limit=1)` (raw dict), not `get_raid_log()`.**
`RaidLogEntry` is cyclic by construction: its `_iter_members` / `_iter_attack_log` /
`_iter_defense_log` generator expressions close over `self`, and every `RaidMember` /
`RaidClan` holds `raid_log_entry` back to it. Refcounting can therefore never free one, and
automatic GC is disabled (`qapbot/docs/PERFORMANCE_TUNING.md` § GC policy, and
`release_war_object()` for the war-graph version of the same problem). We want a plain dict to
persist anyway, and `http.get_clan_raid_log` is exactly the call coc.py's own
`RaidLog._fetch_endpoint` makes. It still goes through coc.py's throttler and key rotation.
**Don't mutate the returned dict**: it's the object held in coc.py's response cache.

---

## 2. Storage format (new tables, `main` only)

Two small tables. The volume is about 20 member clans × ~50 rows × 52 weeks ≈ 50K rows a year,
so they stay in the **hot DB only**. They are *not* added to `_HOT_HISTORY_MIRRORED_TABLES`, and
the monthly hot→history migration never touches them. That keeps Cardinal Rule 1 out of play:
no `history.` twin, no UNION reads.

```sql
CREATE TABLE IF NOT EXISTS capital_raid_seasons (
    clan_tag                  TEXT    NOT NULL,
    season_start              TEXT    NOT NULL,  -- ISO UTC, '2026-09-18T07:00:00Z' (normalized from startTime)
    season_end                TEXT    NOT NULL,
    state                     TEXT    NOT NULL,  -- 'pending' (roster snapshotted, API not showing this season yet)
                                                 -- | 'ongoing' | 'ended' (raw API values)
                                                 -- | 'no_result' (window closed, API never delivered this season: the clan
                                                 --   didn't start the raid, or the bot was down past the data-loss window)
    roster_snapshot_at        TEXT    NOT NULL,  -- when the eligibility snapshot (§2.1) was taken
    capital_total_loot        INTEGER NOT NULL DEFAULT 0,
    raids_completed           INTEGER NOT NULL DEFAULT 0,
    total_attacks             INTEGER NOT NULL DEFAULT 0,
    enemy_districts_destroyed INTEGER NOT NULL DEFAULT 0,
    offensive_reward          INTEGER NOT NULL DEFAULT 0,
    defensive_reward          INTEGER NOT NULL DEFAULT 0,
    finalized                 INTEGER NOT NULL DEFAULT 0,  -- 1 once state is 'ended' or 'no_result'; row is then frozen
    updated_at                TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (clan_tag, season_start)
);

CREATE TABLE IF NOT EXISTS capital_raid_members (
    clan_tag                 TEXT    NOT NULL,
    season_start             TEXT    NOT NULL,
    player_tag               TEXT    NOT NULL,
    player_name              TEXT,
    attacks                  INTEGER NOT NULL DEFAULT 0,  -- 0 = eligible (on roster at season start), no attack (yet)
    attack_limit             INTEGER NOT NULL DEFAULT 0,
    bonus_attack_limit       INTEGER NOT NULL DEFAULT 0,
    capital_resources_looted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (clan_tag, season_start, player_tag)
);
CREATE INDEX IF NOT EXISTS idx_capital_raid_members_player ON capital_raid_members(player_tag, season_start);
CREATE INDEX IF NOT EXISTS idx_capital_raid_members_season ON capital_raid_members(season_start);
```

Design notes:

- **Season key is `season_start`**, which is always a Friday 07:00 UTC. It's shared across
  clans, so cross-clan checks are simple equality. A weekend belongs to the **month of its
  Friday** (a Fri 30th → Mon 2nd weekend counts toward the Friday's month). This is documented
  in the leaderboard help text.
- **Zero-attack rows** (`attacks = 0`) represent **eligible** players who haven't attacked. The
  API never exposes non-attackers (§1.1), so one data source serves several purposes, depending
  on the season's state:
  - **pending** → the clan hasn't started the raid yet, so nobody can attack. Not shown
    anywhere and not reminded.
  - **ongoing** → "not attacked yet". Used by the `currentraid` footer (§4) and as the reminder
    candidate list (§5). A first attack overwrites the row. A player who left the clan has
    their row deleted.
  - **ended** → "missed this weekend". Frozen, and only these rows count toward `raidmissed`
    (§4).
  - **no_result** → no usable result, so the rows never count as misses.

### 2.1 Eligibility = roster at season start (game mechanic)

Only players who were in the clan **before the season started** (Fri 07:00 UTC) can raid for it.
Anyone who joins later can't participate that weekend, so they must never be listed as missed or
not-attacked-yet, and never reminded. So the zero rows come from a **roster snapshot taken once,
at season start**, not from the live roster:

- **Snapshot:** on the first cycle at or after the computed `season_start` (normally within one
  cycle, ~5 min, of Fri 07:00), and before we even know whether the clan will start the raid,
  write the season row (`state='pending'`, `roster_snapshot_at=now`) plus one zero row per
  player on the clan roster at that moment.
- **After the snapshot, zero rows are never inserted again.** Later joiners only enter the table
  if they appear in `members[]` (so they attacked). By the mechanic above they can't, so in
  practice they never appear.
- **Leavers:** each cycle, zero rows whose player is no longer on the live roster are deleted.
  They aren't "in the clan" any more. Attacker rows are always kept.
- This also retires the earlier plan's "raided in another tracked clan" cross-check. Under this
  mechanic a player eligible here can't raid anywhere else that weekend, so that code isn't
  written.
- **Late snapshot (bot down at Fri 07:00):** the first snapshot is then taken late and may
  include players who joined in between. We keep counting them, since there's no data to tell
  them apart, but `roster_snapshot_at` is stored. When it's more than 30 min after
  `season_start`, the `raidmissed`/`currentraid` output for that season adds one line: "Roster
  snapshot taken 5.2 h after raid start: players who joined in that window may be listed
  incorrectly." A `[RAID-UPDATE]` WARNING is logged too. This is the only residual gap, and it
  only happens after an outage.
- No FK to `clans` (Pitfall 16 is irrelevant here, since member clans always exist in `clans`).
  Plain columns. Always accessed with `row["col"]` (Rule 14).
- DDL goes in the same `initialize()` block as the other `CREATE TABLE IF NOT EXISTS` statements
  (idempotent, Rule 12). Brand-new tables, so there are no `_add_column_if_missing` ordering
  concerns.

### db_manager methods (all new; Rule 11)

| method | kind | purpose |
|---|---|---|
| `get_unfinalized_capital_raid_seasons_sync(clan_tag)` → `List[{season_start, season_end, state}]` | sync read | every not-yet-finalized season for a clan, oldest first (update gate + matching, §3). Normally 0 or 1 rows; 2 only in the Friday catch-up case |
| `snapshot_capital_raid_roster(clan_tag, season_start, season_end, roster)` | async write, `_retry_on_locked` + `_write_lock`, one transaction | §2.1 snapshot: `INSERT OR IGNORE` the season row (`pending`) + zero rows. Idempotent; a second call is a no-op |
| `upsert_capital_raid_season(clan_tag, season, members, roster)` | async write, same pattern | see below. Also used with `state='no_result'` to close a season the API never delivered |
| `get_capital_raid_rows_sync(clan_tags, season_starts=None, months=None, player_tags=None)` → `List[Dict]` | sync read | the **only** reader. Joins members + seasons and returns per-row dicts for the leaderboard layer. Filters: by clan list, by explicit season(s), by `(month, year)` list (`substr(season_start,1,7) IN (...)`), or by player tags (scope="all") |
| `get_latest_capital_raid_season_sync(clan_tags, states)` → `str` or `None` | sync read | newest `season_start` in the given states (`currentraid` default: `('ongoing','ended')`; `raidmissed` default: `('ended',)`) |

`upsert_capital_raid_season` inside one transaction:
1. `INSERT … ON CONFLICT(clan_tag, season_start) DO UPDATE` the season row (keeping
   `roster_snapshot_at`). Set `finalized = state IN ('ended', 'no_result')`.
2. Upsert every `members[]` entry (attackers, overwriting their zero row).
3. **No zero-row inserts.** Eligibility is fixed by the snapshot (§2.1).
4. `DELETE … WHERE attacks = 0 AND player_tag NOT IN (roster)` for that clan+season. This drops
   a non-attacker who left the clan during the weekend (they're not "in the clan" any more, so
   not penalty-worthy). Attacker rows are never deleted.
5. Refuse to write (early return) if the stored row is already `finalized = 1` for the same
   `season_start`. Finalized seasons are frozen.

The caller (`update_capital_raid_for_clan`, §3), on the write that finalizes a season, drops that
season's reminder dedup state: `CACHE.notification_state.pop(raid_key)` plus the existing
`delete_notification_state_for_war(raid_key)`, the same cleanup the war path does when a war
finalizes (§5.3).

---

## 3. Fetch schedule: once per update cycle, raid weekends only

### Gate (pure function, unit-tested)

`is_capital_raid_window(now_utc) -> bool` in `qapbot/constants.py`, next to the other game-clock
helpers (`normalize_cwl_season`). It returns True from **Fri 07:00 UTC to Mon 07:00 UTC**.

A clan is fetched this cycle when **either**:
- the raid window is open, **or**
- the clan's latest stored season is **not finalized**. This is the catch-up rule: it covers the
  Monday post-end fetch (07:00 exactly lands outside the window) and any bot downtime over the
  weekend or Monday. Per §1.2, catching up is only possible until next Friday 07:00, so this rule
  has to hold on Tue–Thu too.

Outside both conditions (the normal Tue–Thu case), the phase makes **zero** API calls and no DB
writes: one `get_capital_raid_season_state_sync` per member clan, fast indexed PK reads.

### Where in the cycle

New step in `QapBot.py`'s cycle, placed **right after `sweep_cwl_ended_flags()`**. Pure API+DB,
no Discord I/O, so it runs during Discord outages, before notifications and before
`post_leaderboards_to_subscribed_channels()`. That way auto-posted raid boards show this cycle's
data. Logged as `[RAID-UPDATE]` with a `[RAID-UPDATE-TIMING]` line, matching the
`[PHASE-1.6-TIMING]` style.

```python
# QBhelperfunctions.py
async def update_capital_raids_for_member_clans() -> Dict[str, int]:
    tags = all_member_clan_tags()                      # union of resolve_guild_member_clan_tags(g) over CACHE.server_config
    now = datetime.now(timezone.utc)
    window = is_capital_raid_window(now)
    due = [t for t in tags if window or db.get_unfinalized_capital_raid_seasons_sync(t)]   # catch-up rule
    results = await asyncio.gather(*(update_capital_raid_for_clan(t) for t in due), return_exceptions=True)
    ...  # count fetched/written/finalized/errors for the log line

async def update_capital_raid_for_clan(clan_tag: str) -> bool:
    now = datetime.now(timezone.utc)
    cur_start, cur_end = current_raid_season_bounds(now)   # computed Fri 07:00 / Mon 07:00 of this (or last) weekend
    db = CACHE.db_manager
    clan = await CACHE.coc_clan_cache.get_clan(clan_tag)   # live roster (warm: member clans are Phase-1 polled)
    roster = {m.tag: m.name for m in clan.members}

    # §2.1 eligibility snapshot: once per clan per season, as early as possible after Fri 07:00.
    if is_capital_raid_window(now):
        await db.snapshot_capital_raid_roster(clan_tag, cur_start, cur_end, roster)   # no-op if it already exists

    open_seasons = db.get_unfinalized_capital_raid_seasons_sync(clan_tag)   # oldest first
    if not open_seasons:
        return False
    data = await CACHE.get_capital_raid_seasons_from_api(clan_tag, limit=1)   # §3.1
    item = (data.get("items") or [None])[0]
    api_start = _coc_ts_to_iso(item["startTime"]) if item else None
    wrote = False
    for s in open_seasons:
        if s["season_start"] == api_start:
            # The API serves exactly this season: store it (ongoing, or ended -> finalized).
            await db.upsert_capital_raid_season(clan_tag, item, item.get("members") or [], roster)
            wrote = True
        elif now >= parse_iso(s["season_end"]) and (api_start is None or api_start != s["season_start"]):
            # Its window is over and the API isn't serving it: the clan never started it (API still shows
            # an older season) or it's gone (API already shows a newer one). Either way, close it.
            await db.upsert_capital_raid_season(clan_tag, {"state": "no_result", ...}, [], roster)
            wrote = True
        # else: still inside its own window, not started yet -> stays 'pending'
    return wrote
```

**Why match against every open season, not just the latest.** Take a Friday after a Monday
outage. The snapshot above has just created this week's `pending` row. Meanwhile **last**
week's season is still unfinalized, and until the clan starts the new raid, the API still
serves last week's full `members[]` as `items[0]`. Matching only the latest row would miss it
and lose that data. The loop finalizes it from the API as usual.

**Tracking starts at the next Friday.** A member clan added mid-week gets its first snapshot the
next Friday. Before that there's no eligibility data, so its current or previous season is
deliberately not imported (a player list without a start-roster would break the §2.1 rule).
A clan added **during** a raid weekend gets a late snapshot, which is flagged per §2.1.

`current_raid_season_bounds(now)` sits next to `is_capital_raid_window()` in `constants.py`. Both
are pure, unit-tested clock helpers.

`all_member_clan_tags()` is a new ~5-line helper next to `resolve_guild_member_clan_tags()`
(`QBdiscocmdshelper_cwl.py`) that unions it over every guild. Check first whether something
equivalent already exists in `cache_manager.update_all_clan_subscription_statuses()` (it walks the
same config at `cache_manager.py:993`) and reuse that if there is one (Rule 4).

Assumption to verify on DEV during the first raid weekend: when a clan **hasn't started** its
raid yet, `items[0]` is still last week's ended season. The flow above covers both ways the API
might behave: an `ongoing` entry for the new weekend just matches the new `pending` row
earlier.

**Leavers after the weekend:** step 4 of the upsert deletes zero rows for players no longer on
the roster, and that includes the finalizing write. A non-attacker who leaves between Mon 07:00
and our finalizing fetch (normally one cycle later) drops off the missed list. That's accepted
("in the clan" is judged at finalization). After a longer outage the window is correspondingly
longer.

### 3.1 API wrapper (Rule 9)

A new method in `cache_manager.py`, modeled on `get_current_war_from_api()` (no in-bot caching;
coc.py's own HTTP cache already honors `max-age`):

```python
async def get_capital_raid_seasons_from_api(self, clan_tag: str, *, limit: int = 1) -> Dict[str, Any]:
    return await coc_retry(
        lambda: self.coc_client.http.get_clan_raid_log(clan_tag, limit=limit),
        operation_name=f"get_capital_raid_seasons({clan_tag})",
    )
```

`coc_retry` gives it maintenance fast-fail, retries and the per-op counter, so it shows up in
`[API-CALL-MIX]` as `get_capital_raid_seasons`. `NotFound`/`Forbidden` are logged at WARNING and
skipped for that clan only.

Load: about 20 member clans × one call per cycle, Fri–Mon only. That's noise next to Phase-1's
~3,000 clans/cycle.

---

## 4. Leaderboards: new modes in the existing `/leaderboard`

We don't add a new command. The raid boards become **modes** in `MODE_REGISTRY`, so they
automatically get everything else: clan/family/channel-subscription targeting, the full `month`
syntax (`6`, `6-7`, `1;3;5`, `-2`), `year` (YTD), `scope` (own/all), highlighting yourself and
`/highlightme`, `/subscribe` auto-posting with content-hash dedup, DM invocation,
`post_leaderboard_to_discord()` splitting, and mode autocomplete.

| mode | rows | sort | default period (no month/year given) |
|---|---|---|---|
| `raid` | Player · Loot · Atk · Ø Loot/Atk · Medals · Wknds | loot desc | current month (same as `attack`) |
| `currentraid` | same columns, one season, plus a clan-totals header line and, **while ongoing**, a "Not attacked yet (N)" footer | loot desc | latest `ongoing` or `ended` season (a `pending` one the clan hasn't started is skipped, so on a Friday morning you still see last weekend) |
| `raidmissed` | Player · Missed (weekends) · Wknds (on roster) | **Missed desc**, then name | **latest ended season** = the penalty list; with month/year/range: **missed weekends summed per player** over the period (Q1) |

`raidmissed` only ever counts seasons with **`state = 'ended'`** (in the SQL). An ongoing
season's zero rows mean "hasn't attacked *yet*" (Q3), and a `no_result` season's zero rows mean
nobody could attack. Neither shows up as a miss. So a `raidmissed` for the current month,
requested on a Saturday, covers the month's earlier weekends only. Late joiners never appear
at all (§2.1).

(Optional, one registry entry each if wanted later: `raidavg` sorted by Ø loot/attack, same
columns.)

### 4.1 Registry + rendering (`qapbot/formatting.py`)

- Add the three entries to `MODE_REGISTRY` with `"source": "raid"` and a `"cells"` lambda that
  returns the non-player cell values. For example, `raid`:
  `"columns": [("Player",17),("Loot",8),("Atk",3),("Ø Loot/Atk",10),("Medals",6),("Wknds",5)]`,
  `"cells": lambda p: (p["Loot"], p["Attacks"], f"{p['Loot']/p['Attacks']:.0f}" if p["Attacks"] else "0", p["Medals"], p["Weekends"])`.
- `render_leaderboard()` gets one generic branch **before** the war-specific
  `if mode == "missedattacks"` chain: `if "cells" in spec: row_parts = [player_cell] + [right_pad_number(v, w) for v, (_, w) in zip(spec["cells"](p), cols[1:])]`.
  That's about 3 lines, and it replaces the need for a per-mode branch. Add the titles to
  `header_label_map` ("Capital Raid Leaderboard", "Current Raid Weekend", "Missed Raid Weekends").
- Add the three modes to `NO_CWL_SUFFIX_MODES`, so `cwl_only`/`season` can't produce
  `raid_cwl`.

### 4.2 Data layer (`QBhelperfunctions.py`)

One new function next to `calculate_leaderboard()`:

```python
def calculate_raid_leaderboard(clan_tag, periods, mode, *, season_start=None,
                               scope="own", member_player_tags=None) -> Dict[str, Dict[str, Any]]:
```

- Resolves family → clans exactly like `_load_history_rows()`. For `scope="all"` with a
  roster, it filters by player tags instead of clans (same semantics as the war boards).
- One `get_capital_raid_rows_sync()` call (rows in `pending`/`no_result` seasons are excluded
  in the SQL), then aggregation by `player_tag` in Python:
  - `Loot` and `Attacks`: summed over `ongoing` + `ended` seasons.
  - `Medals` (§4.3): summed over `ended` seasons only, since rewards aren't final while a season
    is ongoing.
  - `Missed`: `ended` seasons with `attacks == 0`.
  - `Weekends`: in `raid`/`currentraid`, the weekends the player **attacked** in. In
    `raidmissed`, the `ended` weekends the player was **eligible** for, so "Missed 3 of 8"
    reads naturally.
  - `Player` = name from the newest season, `PlayerID` = tag (so highlighting works).
  Seasons are atomic, so none of `generate_leaderboard_text()`'s war-ID cross-month dedup is
  needed.
- No cross-clan exclusion is needed: eligibility comes from the start-of-season snapshot (§2.1).
- `raidmissed` only returns players with `Missed > 0`. `raid`/`currentraid` only return
  players with `Attacks > 0` (zero rows aren't leaderboard entries).

`generate_leaderboard_text()` gets an early branch after the mode validation. It reuses its own
`periods` normalization, `clan_name` resolution and `_format_periods_label()`, then calls
`calculate_raid_leaderboard()` instead of the war aggregation loop. For `currentraid` /
`raidmissed` with no explicit time, it passes `season_start=get_latest_capital_raid_season_sync(...)`
and labels the season as `for raid weekend 18–21 Sep 2026`. For `currentraid`, the
`war_info_line` slot (already a render parameter) carries the clan totals:
`State: ended · Loot 854,876 · Attacks 198 · Raids 10 · Districts 78 · Medals (6 atk) 1,465`,
with medals shown once `state == 'ended'`.

**"Not attacked yet" footer (Q3).** While the season is `ongoing`, `currentraid` appends one
extra section below the table: `Not attacked yet (N): name1, name2, …`, built from the season's
zero rows (eligible players only, so post-start joiners are never listed) and sorted by name.
The late-snapshot note from §2.1 is added here too when it applies. It's appended in
`generate_leaderboard_text()` after `render_leaderboard()` returns, so the renderer needs no new
concept. It's plain text, so `post_leaderboard_to_discord()`'s splitting handles long lists.
Once the season ends the footer disappears, and the same players show up in `raidmissed`.

Empty result: `"… No raid weekends recorded for 09/2026."` The auto-poster's "previous month
fallback" (`QapBot.py` ≈ line 943) currently tests for `"no wars recorded for"`. Widen that
check to `"recorded for"` so a monthly raid subscription falls back to the previous month in the
days before the month's first raid weekend, just like the war boards.

### 4.3 Medals (confirmed, Q2)

`offensiveReward` is the per-attack medal value, so a player's raid medals are
`offensive_reward × attacks + defensive_reward` (209 × 6 + 211 = 1,465; checked in-game by the
project owner). This is computed at read time in `calculate_raid_leaderboard()` and never stored,
since both inputs are already in the tables. It's shown per player in `raid`/`currentraid` for
`ended` seasons ("–" while ongoing, because rewards are only final at the end), and in the
`currentraid` header as the full-6-attack value. It's only computed for players with
`attacks > 0`, so zero rows never get the defensive reward.

### 4.4 `/leaderboard` command (`QBdiscordcmds.py`)

Only exclusions, no new logic:
- Add `'currentraid', 'raid', 'raidmissed'` to the tuple that skips
  `update_clan_war_info_and_stats()` (both branches ≈ lines 691/705). They don't use the war
  endpoint.
- Skip `currentraid` for **families** like `currentwar` (line 772). `raid`/`raidmissed` do work
  for families.
- Optional freshness: for `currentraid`, if `is_capital_raid_window()` is open, call
  `update_capital_raid_for_clan(tag)` first (same idea as the war refresh above it). Otherwise
  the data can be up to one cycle old.
- Update the `mode` describe text. `/help` (`help()` in `QBdiscordcmds.py`) and the README
  command list get the three modes (Rule 15).

### 4.5 i18n

The rendered leaderboard body (titles, column headers, empty-state line) is plain English in
`render_leaderboard()`/`generate_leaderboard_text()` today. The existing boards aren't
translated. The raid modes follow that convention, so they're consistent with the table they sit
in. Any new **ephemeral error** strings go through `t()` with keys in both `en.json` and
`de.json` (Rule 6).

---

## 5. "Not attacked yet" reminders (Q3): reuse the war-reminder pipeline

**Exactly one reminder per season**, sent 12 h or 24 h before season end (Mon 07:00 UTC, so Sun
07:00 or Sun 19:00 UTC). Only **eligible** players (on the roster at season start, §2.1) are ever
reminded. Configurable at the same two levels as the missed-war notification:

| level | war (existing) | raid (new) |
|---|---|---|
| user DM | `war_reminders` on/off, `hours_before_end`, `notification_mode` once/repeated, `notification_type` all/CWL | `raid_reminders` on/off, `raid_hours_before_end` ∈ {12, 24}, `raid_reminder_scope` ∈ {`not_attacked` (**default**), `open_attacks`}. No mode setting: always once per season |
| guild channel | `channel_war_notifications_enabled`, `war_notification_threshold_hours` ∈ {0.5,1,2,4}, `war_notification_channel_id` | `channel_raid_notifications_enabled`, `raid_notification_threshold_hours` ∈ {12, 24}, `raid_notification_scope` (same two values, same default), `raid_notification_channel_id` (**optional**: empty ⇒ falls back to `war_notification_channel_id`) |
| buddies ("Save your Buddy") | watchers with `war_reminders` | watchers with `raid_reminders`, filtered by the **watcher's** scope |

**Scope:**
- `not_attacked` = eligible players with 0 attacks (the default).
- `open_attacks` = every eligible player with attacks left:
  `attacks < attack_limit + bonus_attack_limit` (§10 Q-A: `bonusAttackLimit` is the **earned**
  bonus). For a zero row the limit is `attack_limit` (5). "Attacks left" in the message is
  `limit − attacks`.

**Once per season, always:** after a player (DM) or guild (channel) has been reminded for a
season, nothing more is sent, even if the scope is `open_attacks` and they still have attacks
open later.

### 5.1 Why reuse instead of a parallel module

`qapbot/war_notifications.py` already handles everything a raid reminder needs: the per-user
aggregated DM with own/buddy sections, the DM rate limiting, dedup state that's persisted
write-through (`notification_state` / `channel_notification_state`, keyed by an opaque
`war_key` TEXT), the channel embed with custodian @mentions, the per-guild threshold, the
Discord-outage skip, the DEV skip and the `[NOTIFY-TIMING]` logs. It all runs off a plain
`war_data` dict: `clan.members[].attacks`, `attacks_per_member`, `hours_remaining`, `is_cwl`.
A raid season maps onto that shape directly, so **raids become extra entries in the same loop**.
There's no second notifier.

### 5.2 Changes in `war_notifications.py` (a profile table plus a handful of lookups)

```python
# One row per reminder kind; war_data["kind"] selects it (absent ⇒ "war", so existing callers are untouched).
_REMINDER_KINDS = {
    "war":  {"user_flag": "war_reminders",  "user_hours": "hours_before_end",      "repeat": True,  "user_scope": None,
             "guild_flag": "channel_war_notifications_enabled",  "guild_hours": "war_notification_threshold_hours",
             "guild_scope": None, "channel_key": "war_notification_channel_id",
             "dm_keys": "ui_components.war_notification_dm",  "channel_keys": "ui_components.basic_config.war_channel_notification"},
    "raid": {"user_flag": "raid_reminders", "user_hours": "raid_hours_before_end", "repeat": False, "user_scope": "raid_reminder_scope",
             "guild_flag": "channel_raid_notifications_enabled", "guild_hours": "raid_notification_threshold_hours",
             "guild_scope": "raid_notification_scope", "channel_key": "raid_notification_channel_id",
             "dm_keys": "ui_components.raid_notification_dm", "channel_keys": "ui_components.basic_config.raid_channel_notification"},
}

def _notification_channel_id(guild_config, profile):   # raid: own channel if set, else the war channel
    return guild_config.get(profile["channel_key"]) or guild_config.get("war_notification_channel_id")

def _in_scope(scope, member):                           # None (war) / "open_attacks" -> any open attack; "not_attacked" -> 0 attacks
    return scope != "not_attacked" or not member["attacks"]
```

- **Index build** (top of `check_wars_for_notifications`): build one player index per kind from
  its `user_flag`, instead of the single `war_reminders` one, in the same loop over
  `CACHE.user_accounts`. `_get_player_discord_id(tag)` becomes `(tag, kind)`. The buddy loop
  reads the watcher's `user_flag` for the kind.
- **Candidates:** `active_wars += await asyncio.to_thread(_get_active_raids)` right after
  `_get_active_wars`. `_get_active_raids()` reads the ongoing seasons of member clans whose
  `season_end` is ≤ 24 h away (the largest threshold, so nothing earlier is loaded), and emits
  one `(clan_tag, raid_key, war_data)` per clan:
  `raid_key = f"raid:{clan_tag}:{season_start}"`,
  `war_data = {"kind": "raid", "is_cwl": False, "hours_remaining": …, "attacks_per_member": 5,`
  `"clan": {"tag", "name", "members": [{"tag", "name", "attacks": [None]*attacks, "attacks_per_member": limit} …]}, "opponent": {"name": ""}}`.
  The members are **every eligible player with attacks left** (zero rows plus partial
  attackers). The scope filter is then applied per recipient (below), so one candidate list
  serves both scopes. Data comes from one new sync reader,
  `get_ongoing_capital_raid_candidates_sync(max_hours)`.
- **Per-member attack limit:** the two member loops (`_get_players_needing_reminders`,
  `_get_players_with_attacks_remaining`) read
  `member.get("attacks_per_member", attacks_per_member)` instead of the clan-level value. That's
  a one-token change, and war members never carry the key.
- **Scope filter:** in `_get_players_needing_reminders` (own and buddy loops), skip the member
  when `not _in_scope(user_settings.get(profile["user_scope"]), member)`. In
  `_send_channel_war_notification`, filter `players_to_notify` by the guild's
  `profile["guild_scope"]` before building the embed. For wars both scopes are `None`, so
  there's no behavior change.
- `_should_notify_for_war_type`: `kind == "raid"` → True (the CWL-only filter is war-specific).
- `_should_send_notification`: reads the threshold from `profile["user_hours"]`. For raid
  (`repeat=False`) it's hard-wired to `once`: one DM per player per season, whatever the user's
  war `notification_mode` says. The channel path is already once per `war_key` + guild
  (`_is_channel_notification_sent`), so it needs no change for raids.
- `_format_aggregated_reminder_message`: `t()` keys come from `profile["dm_keys"]`. The raid
  prefix has its own `header`, `matchup` ("Raid weekend — {clan_name}"), `ends_hours`/
  `ends_minutes` and `reminder`. The generic ones (`greeting`, `attack_line_*`,
  `attacks_remaining_*`, buddy section labels) stay shared.
- `_send_channel_war_notification`: flag, threshold, scope and title/description keys come
  from the profile. The channel comes from `_notification_channel_id()`: the raid channel if
  one is set, otherwise the war channel. If neither is set, the guild is skipped, the same as
  today's "no war channel" case. **Guild resolution for raids** = guilds whose `resolve_guild_member_clan_tags()`
  contains the clan (raids are tracked for member clans, not subscriptions), via a small
  `kind`-switched helper around the existing subscribed-guild loop. The custodian @mention line
  applies too. The CWL-coordinator line stays gated on `is_cwl`, so it's already off for raids.

### 5.3 Dedup state

This reuses `notification_state` / `channel_notification_state` unchanged. `raid:`-prefixed
`war_key`s can't collide with war keys. When a season finalizes, §2's caller drops its key
(cache pop + `delete_notification_state_for_war(raid_key)`), the same lifecycle as a finished war.
**Check during implementation:** grep every reader/purger of `notification_state` for code that
parses `war_key` as `<clan>_<opponent>` and would choke on the `raid:` prefix.

### 5.4 Settings storage and UI

- **`users`** (not mirrored, so no Rule 1): `_add_column_if_missing` for
  `raid_reminders_enabled BOOLEAN NOT NULL DEFAULT 0`,
  `raid_hours_before_end INTEGER NOT NULL DEFAULT 24` and
  `raid_reminder_scope TEXT NOT NULL DEFAULT 'not_attacked'`. They map to
  `notification_settings["raid_reminders"|"raid_hours_before_end"|"raid_reminder_scope"]` in
  the existing user load/save (`db_manager.py` ≈ lines 10423/10451). Separate columns are
  needed because `notification_type` has a `CHECK (… IN ('all_wars','cwl_only'))` that can't be
  extended in place. **Default off:** existing registrants don't start getting new DMs
  unannounced.
- **`guild_config`**: `channel_raid_notifications_enabled BOOLEAN NOT NULL DEFAULT 0`,
  `raid_notification_threshold_hours REAL NOT NULL DEFAULT 24`,
  `raid_notification_scope TEXT NOT NULL DEFAULT 'not_attacked'`,
  `raid_notification_channel_id TEXT` (NULL = use the war channel). Wired through the existing
  guild-config load/save (≈ lines 10747/10815) into `CACHE.server_config`.
- **User UI** (`UnifiedNotificationView`, `qapbot/ui_notifications.py`): two extra selects:
  "Raid weekend reminder: Off / 24 h before end / 12 h before end" (sets enabled + hours) and
  "Remind about: accounts with no raid attack yet (default) / accounts with any raid attacks
  left" (sets the scope). Both rebuild with `default=True` on the current option (Rule 8),
  edit the same message in place, and use the view's existing re-entrancy handling (Rule 7).
  Check the row budget (5 rows per view) against the existing buttons/selects when
  implementing. If it's short, put both raid options in one select with 5 values (Off,
  24 h/none yet, 24 h/any left, 12 h/none yet, 12 h/any left).
- **Guild UI** (clan-management basic config, `qapbot/ui_clan_management.py`):
  - A "Raid channel reminder" toggle next to the war one (≈ line 2372). Its "ready" styling
    checks the **effective** channel (`_notification_channel_id()`), not just the war channel.
  - Threshold and scope: **reuse** `NotificationThresholdConfigurationView`. Parametrize it
    with `config_key`, `options` and a title key. Each option's stored value moves from the
    hard-coded `opt["hours"]` to a generic `opt["stored"]`; the war options keep their hour
    floats, so saved war config is unaffected. War: its current `THRESHOLD_OPTIONS`; raid
    threshold: 12 h / 24 h; raid scope: `not_attacked` / `open_attacks`. That's three uses of
    one view instead of three views.
  - **Separate channel:** one new entry in `DEFAULT_CHANNEL_SLOTS` (the data-driven channel
    configurator, "adding a slot needs no new handler code"):
    `ChannelSlotConfig(key="raid", label="Raid (optional, defaults to War)", config_key="raid_notification_channel_id")`
    with **no** `disable_flag_keys`, because clearing it must not switch raid reminders off (it
    just falls back to the war channel). Add `"raid"` to the basic slot group next to
    `registration`/`war`. That's 3 select rows + 1 button row (Apply + 3 Clear), within
    Discord's 5-row limit. The existing per-slot "Clear" button gives "back to default" for
    free.
- i18n: new `raid_notification_dm.*`, `raid_channel_notification_*` and the UI labels, added to
  **both** `en.json` and `de.json` with the parity check (Rule 6).

### 5.5 Residual limitation

Post-start joiners are excluded by construction (§2.1): no zero row, so no reminder and no miss.
The only gap left is a **late snapshot** after bot downtime over Fri 07:00 (§2.1). It's flagged
in the output and in the log.

---

## 6. Code-reuse summary: what's new vs reused

**New (minimal):** 2 tables + 6 db_manager readers/writers; 1 API wrapper;
`is_capital_raid_window()` + `current_raid_season_bounds()`; `update_capital_raid(s)_for_…` (2
small async functions) + `all_member_clan_tags()` (if nothing equivalent exists);
`calculate_raid_leaderboard()`; 3 `MODE_REGISTRY` entries + a ~3-line generic `cells` branch in
`render_leaderboard()`; one cycle hook; `_REMINDER_KINDS` + `_notification_channel_id()` +
`_in_scope()` + `_get_active_raids()`; 7 settings columns; 1–2 user selects, 1 guild toggle,
1 `ChannelSlotConfig` entry.

**Reused unchanged:** `/leaderboard` parsing (`parse_month_argument`, year/YTD, scope, families,
DM path), `generate_leaderboard_text()` scaffolding, `_format_periods_label()`,
`render_leaderboard()` table/width/RTL/highlight machinery, `post_leaderboard_to_discord()`,
`/subscribe` + `post_leaderboards_to_subscribed_channels()` + content-hash dedup, `/highlightme`,
mode autocomplete, `coc_retry`/throttling/`[API-CALL-MIX]`, `coc_clan_cache.get_clan()` rosters,
`resolve_guild_member_clan_tags()`. The whole war-reminder pipeline (DM aggregation, buddies,
rate limiting, dedup tables, channel embed + custodian mentions, outage/DEV skips, timing logs)
plus `NotificationThresholdConfigurationView` (parametrized, not cloned) and the
`ChannelConfigurationView` slot machinery (select, apply, clear).

---

## 7. Tests (`.\run_tests.ps1`)

- `is_capital_raid_window` / `current_raid_season_bounds`: Thu 23:59, Fri 06:59/07:00, Sun,
  Mon 06:59/07:00, Tue.
- `snapshot_capital_raid_roster` + `upsert_capital_raid_season` (in-memory DB fixture): the
  snapshot writes a `pending` row + zero rows for the start roster; a second snapshot call is a
  no-op; **a player who joins after the snapshot never gets a zero row** (not listed, not
  reminded, not missed); a later attacker overwrites their zero row; a non-attacker who left is
  deleted; an attacker who left is kept; a finalized season is frozen.
- `update_capital_raid_for_clan` with a mocked wrapper built from a trimmed copy of the sample
  JSON (`tests/fixtures/`): snapshot on the first in-window call; stale `items[0]` (last week)
  → no season upsert; matching ongoing → write; ended → finalized; window closed and the API
  never showed this season → `no_result` + finalized, and its zero rows don't count as misses.
- **Friday catch-up:** last week's season still unfinalized + this week's fresh `pending`
  snapshot + the API still serving last week's `members[]` → last week is finalized from the API
  and this week stays `pending`. Also: the API already shows a newer season than an open one →
  that one is closed as `no_result`.
- `currentraid` on a Friday before the clan starts the raid shows last weekend, not the
  `pending` one; `pending` rows get no footer and no reminders.
- Late snapshot (`roster_snapshot_at` more than 30 min after start) → note line present in
  `raidmissed`/`currentraid`; on-time → absent.
- Gate: Tue with an unfinalized stored season → fetched; Tue with everything finalized → zero
  API calls.
- `calculate_raid_leaderboard`: month assignment by Friday (weekend across month end), family
  aggregation, scope="all", `raidmissed` default = latest ended season; period `raidmissed`
  sums misses per player and sorts by that total descending; an **ongoing** or `no_result`
  season's zero rows never count as misses; medals =
  `offensive_reward × attacks + defensive_reward`, finalized seasons only.
- `currentraid` ongoing → "Not attacked yet" footer present; ended → absent.
- `render_leaderboard` with a `cells` mode: column alignment, highlight.
- Reminders: `_get_active_raids()` builds the `war_data` shape from eligible players with
  attacks left and skips seasons more than 24 h from their end; a raid entry flows through
  `_process_war_for_notifications` with the raid DM keys; **scope** `not_attacked` reminds only
  0-attack players, `open_attacks` also reminds a 3/5 player ("2 attacks left"), for both DM
  (user scope, including buddy watchers) and channel (guild scope); **one reminder per season**:
  a second cycle after the threshold sends nothing, even under `open_attacks` with attacks
  still open, and even when the user's war mode is `repeated`; the 12 h/24 h thresholds are
  honoured; a user with only `war_reminders` gets no raid DM, and vice versa; channel
  resolution: raid channel set → raid channel, unset → war channel, neither → skipped; channel
  posts only go to member-clan guilds with the raid flag set; the **existing war-notification
  tests stay green unchanged** (the `kind`-less default path).
- Settings round-trip: the new `users`/`guild_config` columns load and save through the cache.
- `apply_cwl_mode_suffix("raid", True) == "raid"`.
- A structural test that the new sync readers use `row["…"]` (Rule 14 pattern, see
  `tests/unit/test_cwl_dm_refs_column_order_immunity.py`).

## 8. Docs / housekeeping (same change, Rule 15/17)

- `qapbot/docs/DATABASE_ARCHITECTURE.md`: the two tables, "hot-only, not mirrored", the
  zero-row semantics (eligibility snapshot at season start; ongoing = not attacked yet,
  ended = missed, no_result = ignored), and the 7 new settings columns.
- `README.md` / `/help`: the raid reminder settings (user notification menu + clan-management
  config).
- `qapbot/docs/COC_GAME_MECHANICS.md` § Clan Capital Raid Weekends (**already written**
  2026-09-22 during planning; on implementation, drop its "not yet implemented" note and add
  the Q-A answer): raid weekend clock (Fri 07:00 → Mon 07:00 UTC),
  `members[]` = attackers only, per-player data only on the newest season (the data-loss window),
  **only players in the clan before season start can raid for it** (so post-start joiners are
  never penalized), and a player appears in `members[]` only after their first attack.
- `qapbot/docs/CLAN_AND_WAR_CYCLE_ARCHITECTURE.md`: the new `[RAID-UPDATE]` cycle step.
- `/help`, README command list, `changelog.txt`, `BOT_BUILD` +1.
- `response_1790071441041.json` (the project owner's untracked dev-portal download) was left in
  place; the tests build their API data inline instead of reading it.

## 9. Manual test cases (for the tracker item)

1. DEV, raid weekend: after one cycle, `/leaderboard clan:<member clan> mode:currentraid` shows
   the ongoing season with running totals and a "Not attacked yet" footer. Loot matches the
   in-game raid log. After a listed player attacks, they move from the footer into the table
   on the next cycle.
2. DEV, Monday after 07:00 UTC: the season flips to `ended`, the row is finalized, the footer is
   gone, medals show (1,465 for a 6-attack player at 209/211), and `[RAID-UPDATE]` shows 0
   fetches from the next cycle on (until Friday).
3. `/leaderboard mode:raidmissed` lists exactly the players who were in the clan at raid start,
   are still in it, and never attacked. **A player who joined during the weekend isn't listed**
   (and wasn't in the `currentraid` footer either). `raidmissed month:-2` sums misses per
   player, highest first.
4. `/leaderboard mode:raid month:-2` / `year:2026` / family target aggregate correctly.
5. `/subscribe` a `raid` board: it auto-posts, and it doesn't repost when unchanged.
6. Tue–Thu: the log shows no `get_capital_raid_seasons` in `[API-CALL-MIX]`.
7. **PROD** (war-style notifications are skipped in DEV by design): a user with the raid
   reminder set to 24 h who hasn't attacked gets exactly one DM between Sun 07:00 and ~07:05
   UTC, and nothing more that season. With 12 h, the DM arrives around Sun 19:00. With scope
   "any raid attacks left", an account at 3/5 attacks is included. With the default scope it
   isn't. A buddy watcher with raid reminders gets the buddy section.
8. PROD: guild raid channel reminder on at 24 h, **no raid channel set** → one embed in the war
   notification channel. **After setting a raid channel** (next weekend) → the embed goes there.
   After "Clear Raid" it falls back to the war channel. Custodian mentions are present. Nothing
   is posted in guilds that only have war channel notifications on.
9. User and guild settings (enabled, hours, scope, raid channel) survive a bot restart.
   Existing users default to raid reminders **off**, scope "no raid attack yet".

## 10. Decisions (project owner, 2026-09-22)

1. **Reminder scope is configurable**: `not_attacked` (default) or `open_attacks`, for user
   DMs and the guild channel separately (§5).
2. **Exactly one reminder per season**, for DMs and channel posts, with no repeat option (§5).
3. **Raid channel is optional**: empty means the war reminder channel; if set, the raid channel
   is used (§5.4, `DEFAULT_CHANNEL_SLOTS`).
4. **Post-start joiners are never penalized or reminded**: eligibility is the roster at season
   start (§2.1, game mechanic).
5. **No test reminders on DEV**: intended; manual reminder tests run on PROD (§9).
6. Raid DM reminders and the guild channel reminder **default off** (opt-in), like the war
   channel reminder.

### Resolved

- **Q-A (bonus attack):** `bonusAttackLimit` is the bonus the player has **earned**. A live
  response (2026-09-22, project owner) shows `bonusAttackLimit: 0` for players with 1 attack
  and 1 for players with 6. So `open_attacks` uses
  `limit = attack_limit + bonus_attack_limit`.
