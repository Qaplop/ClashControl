# Tracker #0114 — "Ersatzbank / Bench" CWL sign-up status (passive participation)

Status: **plan, revision 3**. All of the project owner's review decisions are folded in (§9). Ready
to implement.

## 1. Request

Reporter (Lucas, forwarding Eren): players want to sign up "nur als Mitnahme", i.e. on the CWL
roster without attacking. The project owner's spec (2026-09-22):

- A new sign-up status meaning **"I want to be part of this CWL but not actively attack."** Two
  real uses: passive players still get the base CWL season reward, and clans want backfill players
  in case a regular participant drops out.
- **Per-guild mode** in the CWL configuration: **Standard** (Confirm / Opt Out only, the default)
  or **Extended** (adds the new status per CoC account). Only an explicit switch to Extended
  introduces the new option.
- A speaking name, a short explanation in the DMs, a new icon.
- Review decisions (§9): German name **Ersatzbank**; an **"Always Bench"** standing preference;
  the board's active/bench column split and a roster-announcement marker; **"generally user based
  where reasonable"**: DMs and the Player Hub follow the player, and every screen shows a player's
  **real** status; unanswered DMs are upgraded automatically when a server enables Extended.

## 2. Naming and icons

| | German | English |
|---|---|---|
| Status label | **Ersatzbank** | **Bench** |
| Auto status (from the standing preference) | Ersatzbank (automatisch) | Auto-Bench |
| DM / Hub button | 🪑 Ersatzbank | 🪑 Bench |
| Standing preference | Immer Ersatzbank | Always Bench |
| Guild mode | Erweiterte Anmeldung (mit Ersatzbank) | Extended sign-up (with Bench) |

Internal values: status `passive`, auto status `auto_passive`, DM/Hub action `passive`, preference
mode `bench` (column `user_players.cwl_permanent_bench`), guild column
`guild_config.cwl_signup_mode = 'standard'|'extended'`. Labels live in i18n only.

**DM explanation** (whenever a DM offers the Bench button):

> ✅ **Bestätigen** — du spielst mit und greifst an.
> 🪑 **Ersatzbank** — du möchtest im Kader sein (für die Saison-Belohnung oder als Ersatz), willst aber nicht regelmäßig angreifen.
> ❌ **Abmelden** — diese Saison nicht.

EN: "✅ Confirm — you play and attack. 🪑 Bench — you'd like to be on the roster (for the season
rewards or as a backup) but don't plan to attack regularly. ❌ Opt out — not this season."

**Icons** (`activity/client/src/assets/`, same style as gcheck/redx/pending/autoconfirmed: a 44×44
rounded square with a white glyph):
- `bench.svg`: blue `#5b8def`, white bench glyph (seat plank, backrest, two legs).
- `autobench.svg`: the same glyph on light blue `#9dbcf5`, relating to `bench.svg` the way
  `autoconfirmed.svg` relates to `gcheck.svg` (same meaning, no manual click yet).
- Discord buttons and text use 🪑.

The Manage Teams board is English-only by policy (`signupStatus.ts` header), so it shows
"Bench"/"Auto-Bench" even on German servers. Player Hub, DMs and the Hub message are localized.

## 3. Core design

### 3.1 One status truth, shown as it is

A player's response is **global across guilds**: `cwl_player_season_status` is the single source
of truth, and `propagate_cwl_player_response()` copies it into every pooling guild's local mirror
(`cwl_signups.status`, `cwl_shared_clan_players.status`). `passive` / `auto_passive` are stored
and displayed **as they are, on every server** — no per-server masking. A Bench player shows the
🪑 icon even on a Standard server's board, because that is what the player actually chose.

### 3.2 When is the Bench *option* offered — one rule

```python
def cwl_bench_enabled_for(discord_id: Optional[str], guild_id: int) -> bool:
    """True if guild_id's cwl_signup_mode is 'extended', OR the Discord user is a member of any
    guild with Extended mode (player-based, project owner's Q4 decision)."""
```

Membership comes from `CACHE.server_config` (which guilds are Extended) and
`bot.get_guild(g).get_member(uid)` (the member cache #0092's coordinator sync already relies on).
In-memory only, no API call. This one helper decides every "offer Bench?" question:

| Question | Answered by |
|---|---|
| Does this DM get the Bench button + explanation? | `cwl_bench_enabled_for(recipient, sending_guild)` |
| Does the Player Hub show the Bench button / "Always Bench"? | `cwl_bench_enabled_for(viewer, guild)` |
| Does the board's right-click menu offer Bench for this card? | guild Extended, or `cwl_bench_enabled_for(card owner, guild)` |
| Will the bridge accept `passive` from an admin / the Hub? | same as the two rows above |

### 3.3 What the server mode still controls

- DMs **sent by** an Extended server offer Bench to every recipient.
- Its members count as "Bench-enabled" on every server (§3.2).
- Admin-screen chrome on an Extended server is always on: legend rows, Hub count lines, column
  split. On a Standard server the same chrome appears **only when a Bench/Auto-Bench player is
  actually present** (the legend explains icons that are on screen; a count/split of zero isn't
  shown). With no Extended server anywhere, no Bench status can exist, so every screen looks
  exactly as today.

## 4. Change inventory

### Phase 0a — fix: CWL preferences wiped on every `save_user()` (`qapbot/db_manager.py`)

`get_user()` (~10490) loads only `cwl_permanent_optout` + `cwl_default_preferred_league_rank` into
the CACHE player dict, and `_replace_user_players_rows()` (~10615) — DELETE + re-INSERT of all of a
Discord user's rows — writes only those two. So **every `save_user()` silently resets
`cwl_permanent_optin` and `cwl_optout_send_dm_anyway` to 0** (a link change, a player-info refresh,
anything that saves the user). The PROD copy holds 0 opt-ins and 0 "DM anyway" flags against 1
opt-out, which fits. A new `cwl_permanent_bench` column would be wiped the same way.

Fix: load and re-insert every preference column. Regression guards: a **structural** test that the
INSERT column list covers every `cwl_%` column of `user_players` (`PRAGMA table_info`), and a
round-trip test (opt-in → `save_user()` → still opted in). Own changelog entry.

### Phase 0b — fix: dead buttons after enrollment closes (`ui_cwl_roster.py`, `QBdiscocmdshelper_cwl.py`)

`send_cwl_roster_updates` gives a never-asked player (added to a roster during Preparation)
confirm/opt-out buttons plus "Please confirm below…", but `_apply_cwl_signup_response` refuses every
click once the event has left `signup_open`. Decision: **accept answers after enrollment closes from
players who haven't answered yet**.

- `_apply_cwl_signup_response`: `signup_open` → accept as today. `announced`/`war` → accept only
  if the player's current status is `pending`; otherwise `signup_closed` as today. `draft` /
  `cancelled` → refused as today.
- Record the DM's message/channel ID in that send (`mark_cwl_player_dm_sent_sync(..., None, None)`
  at ~4212 → the real IDs), so it can be retracted by Delete Season, re-rendered after an answer,
  and upgraded by §6.1.
- Player Hub: `enrollmentOpen` currently hides the status buttons after enrollment; also show them
  for a still-`pending` row in `announced`/`war`, so the Hub matches what the DM allows.
- Tests: pending answer accepted in `announced`; settled answer still refused; `draft`/`cancelled`
  refused; message ID recorded. Own changelog entry.

### Phase 1 — storage, guild mode, preference

- `guild_config.cwl_signup_mode TEXT NOT NULL DEFAULT 'standard'`: CREATE TABLE,
  `_add_column_if_missing`, `get_guild_config`, `save_guild_config` (INSERT/UPDATE/params).
- `user_players.cwl_permanent_bench INTEGER NOT NULL DEFAULT 0`: CREATE TABLE +
  `_add_column_if_missing`; the three link/member readers (~5445, ~5516, ~5580), `get_user()`,
  `_replace_user_players_rows()` (covered by Phase 0a's structural test automatically).
- `set_cwl_preferences_sync` (~5625): mode `'bench'` → optout=0, optin=0, bench=1; every other mode
  clears bench. Read precedence wherever flags combine: **optout > bench > optin**.
- **No status-column migration**: the three `status` columns are plain `TEXT DEFAULT 'pending'`
  without CHECK constraints (verified).
- Neither table is hot/history-mirrored (Cardinal Rule 1 N/A).
- CWL Settings (`add_cwl_settings_components`, `format_clan_management_cwl_settings`): toggle in
  the `include_all_accounts` pattern and a readout "Anmeldemodus: 🟢 Erweitert / 🔴 Standard" with a
  one-line description. Check the component-row budget before picking a row. Toggling refreshes
  the Hub message, bumps the enrollment board version, and on Standard → Extended starts §6.1.

### Phase 2 — status semantics and bridge (Python)

- `QBdiscocmdshelper_cwl.py`: `is_cwl_extended_signup(guild_id)`, `cwl_bench_enabled_for(...)`.
- `resolve_seeded_cwl_signup_status` (~2720): `permanent_bench -> ('auto_passive', 'auto_bench')`
  between opt-out and opt-in. Its three callers (~1281, ~2942, ~3524) pass the new flag. An existing
  global response still wins; the account is still DMed (as with `auto_confirmed`).
- `settled_statuses` (~970) += `passive`, `auto_passive` — or "Notify New Pool Members" would
  re-invite Bench players.
- `status == "pending"` filters (remind targets, pending split, DM re-render, ownership handover
  ~5046) are already correct: both new statuses are answers. No change, covered by tests.
- Hub overview counts (~584): "Ersatzbank (automatisch)" / "Ersatzbank" lines after the confirmed
  lines; on a Standard server only when > 0 (§3.3). Keys `cwl.management.signup_status_passive` /
  `_auto_passive`.
- **Auto-assignment stays status-agnostic** (verified: placement comes from attack history).
- `web_bridge.py`:
  - `_build_enrollment_payload_sync`: statuses pass through unchanged; add `signup_mode` and, per
    player, `bench_enabled` (§3.2, for the context menu).
  - `_build_player_prefs_payload_sync`: preference `mode` gains `'bench'`; add `bench_enabled` for
    the viewer.
  - Admin override (`handle_post_cwl_enrollment_status`): `passive` accepted when the guild is
    Extended or the player's owner is Bench-enabled; `auto_passive` never (same reasoning as
    `auto_confirmed`). Standard/unlinked otherwise → 400.
  - `handle_post_cwl_player_prefs_status`: `passive` accepted when the viewer is Bench-enabled.
  - Preferences POST: mode `'bench'` accepted when the viewer is Bench-enabled.
- The Worker (`activity/server`) passes these routes through (verified): redeploy only.

### Phase 3 — DMs

- `CWL_SIGNUP_RESPONSE_TEMPLATE` / `CWL_REMINDER_RESPONSE_TEMPLATE`: `confirm|optout` →
  `confirm|passive|optout` (additive; every sent DM keeps working).
- `build_cwl_signup_response_view(...)` / `build_cwl_reminder_response_view(...)` gain a
  `bench: bool` parameter, computed by callers with `cwl_bench_enabled_for(recipient,
  sending_guild)`. Order: Confirm (green), Bench (blue), Opt Out (grey). Reminder rows: three
  labeled buttons (✅/🪑/❌ + name), within Discord's 5-per-row limit.
- Callers: `_send_cwl_enrollment_dm_batch` (~3586), `send_cwl_reminder_dm_group` (~3636),
  `send_cwl_roster_updates`' `never_asked` view (~4198), and `rerender_cwl_dm_after_response`
  (~3154), which must recompute the same decision or a re-render would drop the button.
- Texts: `cwl.template.dm_body_bench`, `cwl.reminder.dm_intro_body_bench`,
  `cwl.update.dm_confirm_prompt_bench` (with the explanation), chosen by the same boolean;
  finalize text `cwl.template.passive_msg` ("🪑 Alles klar — **{player_name}** sitzt diese Saison
  auf der CWL-Ersatzbank.").
- `_apply_cwl_signup_response`: `passive` → (`'passive'`, `'template_passive'`), always accepted
  from a DM button (the bot offered it; the status is displayed truthfully everywhere).
- §6.1 upgrade of unanswered DMs.

### Phase 4 — Activity client (`activity/client/src`)

- `signupStatus.ts`: `VisibleStatus` += `'passive' | 'auto_passive'`; icons, English labels,
  `STATUS_LABEL_KEY` (`status_passive`, `status_auto_passive`), `isVisibleStatus`.
- `types.ts`: both `signup_status` unions, `AdminSettableStatus` += `'passive'`,
  `PlayerPrefsStatusAction` += `'passive'`, preference `mode` unions += `'bench'`, payload fields
  `signup_mode`, per-player `bench_enabled`, viewer `bench_enabled`.
- `enrollmentBoard.ts`:
  - cards: Bench/Auto-Bench icons always (the real status);
  - legend: Bench and Auto-Bench rows when Extended, or when such a player is on the board;
  - right-click menu: "Bench" between Confirmed and Declined when the guild is Extended or that
    card's `bench_enabled` is true;
  - column header split `12 + 3🪑 / 15` (bench = `passive` + `auto_passive`) when Extended, or when
    that column holds a Bench player;
  - `isOptedOut` and sort order unchanged.
- `playerPrefs.ts` (Player Hub, player-based via `bench_enabled`):
  - status labels/icons/tooltips for both new statuses **always** (real status);
  - "🪑 Ersatzbank" button between "I'm in" and "I'm out" when `bench_enabled`; disabled when
    already `passive`; `auto_passive` stays clickable (tracker #0051 reasoning); the error path
    re-enables all three buttons correctly;
  - "Immer Ersatzbank" in `buildModeSelect` and the "apply to all accounts" select when
    `bench_enabled`, and always for an account whose current mode is `bench` (the select must never
    show a wrong value or overwrite it on save);
  - a status legend/tooltips listing Bench only when `bench_enabled` or shown on screen.
- i18n (`cwl.activity.*`): `status_passive`, `status_auto_passive`, `status_tooltip_auto_passive`,
  `button_bench`, `bench_tooltip_default`, `bench_tooltip_already_bench`,
  `bench_tooltip_auto_bench`, `mode_bench`.
- `npm run typecheck && npm run build`; deploy both halves.

### Phase 5 — roster announcement, docs, housekeeping

- `_build_cwl_roster_account_lines` / `announce_cwl_rosters`: "(🪑 Ersatzbank)" after a Bench /
  Auto-Bench player's line — the real status, so it always shows when that's their status.
- Docs: `CWL_ROSTER_PLANNING_PLAN.md` (§2 status list, §6 DMs + the §3.2 rule, §7 board),
  `DATABASE_ARCHITECTURE.md` (two columns, two status values), `CODE_STRUCTURE.md` (helpers), CWL
  help text (one line on Extended sign-up).
- en.json + de.json in the same pass, parity check (Cardinal Rule 6).
- `BOT_BUILD` bump, changelog, tracker test cases.

## 5. Visibility matrix

"Bench-enabled" = the relevant person passes `cwl_bench_enabled_for` (§3.2).

| Surface | Standard server | Extended server |
|---|---|---|
| Enrollment / reminder / roster-update DM | Bench button + explanation if the recipient is Bench-enabled | always |
| Board card icon | real status (🪑 if they chose Bench) | real status |
| Board legend | Bench rows only if such a player is shown | always |
| Board right-click menu | Bench only for Bench-enabled players | always |
| Board column fill | split only for a column holding a Bench player | always split |
| Hub overview counts | Bench lines only when > 0 | always |
| Player Hub | Bench button, "Always Bench", legend if the viewer is Bench-enabled; real status always | always |
| Roster announcement | marker when the status is Bench | same |
| CWL Settings | "Standard" readout + Enable | "Extended" readout + Disable |

## 6. Mode switches and cross-server behaviour

- **Extended → Standard**: nothing stored changes. Bench players keep their status and icon.
  The server's own DMs stop offering Bench to recipients who aren't Bench-enabled via another
  server. Sent DMs are not touched: a leftover Bench button is harmless (it records a real,
  displayed status).
- **Standard → Extended while enrollment is open**: unanswered DMs are upgraded (§6.1).
- **Player on an Extended and a Standard server**: Bench-enabled everywhere (§3.2). If the
  Standard server's admin sets Confirmed/Declined, it overwrites the global status for both —
  today's "last action wins", unchanged.
- **"Always Bench" set, then the player leaves every Extended server**: the preference stays and
  keeps seeding `auto_passive` (shown as Auto-Bench). The Hub still shows the option for that
  account so it can be changed (§4, Phase 4).

### 6.1 Upgrading unanswered DMs when a server enables Extended

Every DM send records, per account, `cwl_player_season_status.dm_sent_via_message_id` /
`dm_sent_via_channel_id` / `dmed_discord_id` (after Phase 0b, all three senders do), and the bot may
edit its own DMs.

New `upgrade_pending_cwl_dms_for_bench(guild_id)` (`QBdiscocmdshelper_cwl.py`), started by the
Settings toggle on Standard → Extended:

1. Runs while the guild's current event is `signup_open`, `announced` or `war` (after Phase 0b,
   pending answers are accepted in all three). Nothing for `draft`/`cancelled`.
2. Candidates: this season's global rows with `status = 'pending'`, `dm_sent = 1` and a recorded
   message, whose recipient is now Bench-enabled — DMs sent by this server **and** DMs sent by any
   other server to a member of this server (DMs follow the player).
3. Grouped by message ID (one reminder/roster-update DM covers up to 5 accounts). Per message:
   fetch it, rebuild the view from that message's still-pending accounts, and **append** the Bench
   explanation to the existing content unless already present. Appending, not replacing, keeps a
   roster-update DM's "where and when you play" text intact (re-using
   `rerender_cwl_dm_after_response` as-is would replace it with the reminder intro).
4. Tracked background task (`QBcore.spawn_tracked`), sequential with a short pause per message and
   the existing Discord retry wrapper. `NotFound`/`Forbidden` → log and skip (Pitfall 13).
5. The toggle's confirmation states how many unanswered invitations are being updated.
6. Idempotent: a second run changes nothing.

## 7. Blast radius and rollout

- Until the first server enables Extended, no Bench status can be created, so every screen and DM
  is exactly as today. Guarded by a test: with no Extended guild, the enrollment and player-prefs
  payloads and all DM views equal today's output.
- Enabling Extended on one server affects: that server's screens and DMs, and DMs/Player Hub of
  that server's members on **other** servers (the deliberate Q4 behaviour), and other servers'
  boards when they show a member who chose Bench (the real status).
- Phases 0a/0b change existing behaviour: 0a only preserves values that were previously dropped;
  0b only opens answers for players who are still pending.
- Deploy order: bot first (inert until a server enables Extended), then Activity client + Worker
  (both halves), then enable Extended on one server. The client must be live before any server
  enables Extended, or an old client would show `passive` without an icon.

## 8. Tests

- 0a: structural column-coverage test; opt-in / DM-anyway / bench survive `save_user()`.
- 0b: pending answer accepted in `announced`/`war`; settled refused; `draft`/`cancelled` refused;
  roster-update DM records its message ID.
- `cwl_bench_enabled_for`: guild Extended; recipient on another Extended guild; neither.
- Seeding precedence optout > bench > optin; `set_cwl_preferences_sync('bench')` clears the others.
- Settled set: Bench / Auto-Bench not re-invited; Hub count lines per §3.3.
- DMs: 2 vs 3 buttons by the rule; regex parses `passive`; `passive` written globally + every mirror;
  re-render keeps the Bench button; finalize text.
- §6.1: candidates (own DM, other server's DM to a member, non-member excluded, answered excluded,
  draft → no-op); one edit per multi-account message; content appended once; `NotFound` skipped.
- Bridge: admin override accepts `passive` per §3.2, rejects `auto_passive`; player-prefs status and
  mode; payload fields.
- "No Extended server anywhere" equality test (§7).
- Client: typecheck + build.

## 9. Decisions (project owner, 2026-09-22)

1. Names: **Ersatzbank** (DE) / Bench (EN), 🪑, blue bench icon.
2. **Always Bench** standing preference: in scope.
3. Board column split + roster-announcement marker: in scope.
4. DMs follow the player: Bench offered if the recipient is on any Extended server.
5. Player Hub follows the player too, and more generally "user based where reasonable": every
   screen shows a player's real status; the board shows Bench icons even on a Standard server.
6. Unanswered DMs are upgraded automatically when a server enables Extended (§6.1).
7. Dead-button bug: accept answers after enrollment closes from players who are still pending
   (Phase 0b).

## 10. Phasing

0a. `user_players` preference round-trip fix (own changelog entry).
0b. Post-enrollment answers for pending players + message-ID recording (own changelog entry).
1. Storage, guild mode toggle, settings readout, `cwl_permanent_bench` + preference write path.
2. Helpers, seeding, settled/counts, bridge.
3. DMs, texts, §6.1 upgrade.
4. Activity client.
5. Roster-announcement marker, docs, help text, changelog, tracker test cases.

Each phase ends with `.\run_tests.ps1` green. Phases 0–3 are Python-only and safe to ship before the
client.
