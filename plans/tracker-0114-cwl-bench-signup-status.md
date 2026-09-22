# Tracker #0114 — "Ersatzbank / Bench" CWL sign-up status (passive participation)

Status: **plan, revision 2**. Decisions from the project owner's review are folded in (§9). One
question from the consistency review is still open (§9, item 5).

## 1. Request

Reporter (Lucas, forwarding Eren): players want to sign up "nur als Mitnahme", i.e. on the CWL
roster without attacking. The project owner's spec (2026-09-22):

- A new sign-up status meaning **"I want to be part of this CWL but not actively attack."** Two
  real uses: passive players still get the base CWL season reward, and clans want backfill players
  in case a regular participant drops out.
- **Per-guild mode**, set in the CWL configuration: **Standard** (today: Confirm / Opt Out only,
  default for every guild) or **Extended** (adds the new status per CoC account).
- Only when Extended is explicitly enabled does anything new appear on that server's screens.
- A speaking name, a short explanation in the DMs, a new icon.
- Review decisions: German name **Ersatzbank**; add an **"Always Bench"** standing preference;
  include the board's active/bench column split and the roster-announcement marker; **DMs follow
  the player**, not the sending server (§3.2).

## 2. Naming and icons

| | German | English |
|---|---|---|
| Status label | **Ersatzbank** | **Bench** |
| Auto status label (from the standing preference) | Ersatzbank (automatisch) | Auto-Bench |
| DM / Hub button | 🪑 Ersatzbank | 🪑 Bench |
| Standing preference | Immer Ersatzbank | Always Bench |
| Guild mode | Erweiterte Anmeldung (mit Ersatzbank) | Extended sign-up (with Bench) |

Internal values: status `passive`, auto status `auto_passive`, DM/Hub action `passive`, standing
preference mode `bench` (column `user_players.cwl_permanent_bench`), guild column
`guild_config.cwl_signup_mode = 'standard'|'extended'`. Labels live in i18n only.

**DM explanation** (shown whenever the DM offers the Bench button, §3.2):

> ✅ **Bestätigen** — du spielst mit und greifst an.
> 🪑 **Ersatzbank** — du möchtest im Kader sein (für die Saison-Belohnung oder als Ersatz), willst aber nicht regelmäßig angreifen.
> ❌ **Abmelden** — diese Saison nicht.

EN: "✅ Confirm — you play and attack. 🪑 Bench — you'd like to be on the roster (for the season
rewards or as a backup) but don't plan to attack regularly. ❌ Opt out — not this season."

**Icons** (`activity/client/src/assets/`, same style as gcheck/redx/pending/autoconfirmed: a 44×44
rounded square with a white glyph):
- `bench.svg`: blue `#5b8def`, white bench glyph (seat plank, backrest, two legs).
- `autobench.svg`: the same glyph on light blue `#9dbcf5`. It relates to `bench.svg` the way
  `autoconfirmed.svg` relates to `gcheck.svg`: same meaning, lighter because no manual click.
- Discord buttons and text use 🪑.

Note: the Manage Teams board is English-only by policy (`signupStatus.ts` header, Phase 6e), so
the board shows "Bench"/"Auto-Bench" even on German servers. The Player Hub, DMs and Hub message
are localized and show "Ersatzbank".

## 3. Core design

### 3.1 Store the raw truth, map on admin screens

A player's response is **global across guilds**: `cwl_player_season_status` is the single source
of truth, and `propagate_cwl_player_response()` copies it into every pooling guild's local mirror
(`cwl_signups.status`, `cwl_shared_clan_players.status`). The mode is per guild.

**Decision:** always store `passive` / `auto_passive` as-is, everywhere. One helper maps them for
a Standard guild's **admin-facing** output:

```python
def effective_cwl_signup_status(status: Optional[str], guild_id: int) -> Optional[str]:
    """In a guild whose cwl_signup_mode is not 'extended': 'passive' -> 'confirmed',
    'auto_passive' -> 'auto_confirmed'. Everything else, and every status in an Extended
    guild, passes through unchanged."""
```

Why not write `confirmed` into Standard mirrors: the mirror would contradict the global truth
(Pitfall 25), a Standard → Extended switch mid-season would show wrong data, and mapping at read
time is reversible where a write is not. "Wants to be part of CWL" is what both `confirmed` and
`passive` mean, so the mapping is correct, not lossy.

Mapping happens **server-side** (payload builders, Hub counts). A Standard guild's admin payloads
therefore stay byte-identical to today's output, which keeps the blast radius on every existing
guild at zero until an admin opts in.

### 3.2 Player-facing vs admin-facing (Q4: "more player related than server related")

- **Admin/leader surfaces follow the server's mode**: Manage Teams board (icons, legend,
  right-click menu, column split), CWL Management Hub counts, CWL Settings.
- **DMs follow the player**: a DM offers the Bench button (and the three-line explanation) when
  the **sending guild is Extended OR the recipient Discord user is a member of any guild with
  Extended mode**. One helper, `cwl_bench_offered_to(discord_id, sending_guild_id) -> bool`,
  checks `CACHE.server_config` for Extended guilds and `bot.get_guild(g).get_member(uid)` (the same
  member cache #0092's coordinator sync uses). Pure in-memory, no API call.
- **Player Hub (`/cwl preferences`)**: see open question §9.5. Proposed: follow the player too
  (same helper), for the reasons given there.

Every DM builder calls that one helper, so the enrollment DM, Remind Pending, the roster-update
DM's `never_asked` buttons, the re-rendered reminder DM and the roster-announcement marker can
never disagree about one player.

## 4. Change inventory

### Phase 0 — fix a live bug the new preference depends on (`qapbot/db_manager.py`)

`get_user()` (~10490) loads only `cwl_permanent_optout` + `cwl_default_preferred_league_rank`
into the CACHE player dict, and `_replace_user_players_rows()` (~10615) — a DELETE + re-INSERT of
every row of that Discord user — writes only those two. So **every `save_user()` silently resets
`cwl_permanent_optin` and `cwl_optout_send_dm_anyway` to 0** for that user (a link change, a
player-info refresh, anything that saves the user). The PROD copy holds 0 opt-ins and 0 "DM
anyway" flags against 1 opt-out, which fits. A `cwl_permanent_bench` column would be wiped the same
way.

Fix: load and re-insert all four preference columns (`optout`, `optin`, `bench`,
`send_dm_anyway`) plus the league rank. Regression guard: a **structural** test asserting that the
INSERT column list in `_replace_user_players_rows` covers every `cwl_%` column of
`user_players` (per `PRAGMA table_info`), so the next preference column can't be forgotten again —
same reasoning as Cardinal Rule 14's structural-test note. Plus a behavioural round-trip test
(set opt-in → `save_user()` → still opted in). Ships as its own changelog entry; it's a fix
regardless of #114.

### Phase 1 — storage and the guild mode

- `guild_config.cwl_signup_mode TEXT NOT NULL DEFAULT 'standard'`: CREATE TABLE,
  `_add_column_if_missing`, `get_guild_config` dict, `save_guild_config` INSERT/UPDATE/params.
- `user_players.cwl_permanent_bench INTEGER NOT NULL DEFAULT 0`: CREATE TABLE +
  `_add_column_if_missing`; added to the SELECT and result dict of the three link/member readers
  (~5445, ~5516, ~5580 — the ones already returning `cwl_permanent_optin`), `get_user()`,
  `_replace_user_players_rows()`.
- `set_cwl_preferences_sync` (~5625): mode `'bench'` → optout=0, optin=0, bench=1; every other
  mode clears bench. Mutually exclusive by construction, as the three-flag form already is for two.
  Read precedence where flags are combined (defensive, same as today's opt-out-wins rule):
  **optout > bench > optin**.
- **No status-column migration**: the three `status` columns are plain `TEXT DEFAULT 'pending'`
  without a CHECK constraint (verified), so `passive`/`auto_passive` need no DDL.
- Neither table is hot/history-mirrored (Cardinal Rule 1 N/A).
- CWL Settings (`ui_cwl_roster.add_cwl_settings_components` + `format_clan_management_cwl_settings`):
  activate/deactivate toggle button in the `include_all_accounts` pattern, plus a readout block
  "Anmeldemodus: 🟢 Erweitert / 🔴 Standard" with a one-line description. Check the component-row
  budget before picking a row. Toggling refreshes the Hub message and bumps the enrollment board
  version, so open boards re-render in the new mode.

### Phase 2 — status semantics, mapping, validation (Python)

- `QBdiscocmdshelper_cwl.py`: `effective_cwl_signup_status()`, `is_cwl_extended_signup(guild_id)`,
  `cwl_bench_offered_to(discord_id, sending_guild_id)`.
- `resolve_seeded_cwl_signup_status` (~2720): new branch `permanent_bench -> ('auto_passive',
  'auto_bench')`, after opt-out and before opt-in (the precedence above). Signature gains the bench
  flag; its three callers (~1281, ~2942, ~3524) pass it. An existing global response still wins, as
  today. The account is still DMed (same as `auto_confirmed`).
- `settled_statuses` in `resolve_cwl_pool_tags_missing_dm_sync` (~970) += `passive`,
  `auto_passive`. Without it "Notify New Pool Members" re-invites Bench players.
- Every `status == "pending"` filter (remind targets, pending split, DM re-render, the ownership
  handover at ~5046) is already correct, since both new statuses are answers. No change, covered by
  tests.
- Hub overview counts (~584): Extended adds "Ersatzbank (automatisch)" and "Ersatzbank" lines
  (same order logic as auto_confirmed/confirmed); Standard folds them into Auto-Confirmed /
  Confirmed. New keys `cwl.management.signup_status_passive` / `_auto_passive`.
- **Auto-assignment stays status-agnostic** (verified: placement comes from attack history). Bench
  players are placed like everyone else; the admin decides.
- `web_bridge.py`:
  - `_build_enrollment_payload_sync`: map all four `signup_status` sites (~483, ~539, ~570, ~640)
    and add `"signup_mode"` to the payload.
  - `_build_player_prefs_payload_sync` (~1498, ~1544): `mode` gains `'bench'` (precedence above);
    map `signup_status` per §9.5's outcome; add `"bench_offered": bool` for the Hub UI.
  - `ADMIN_SETTABLE_ENROLLMENT_STATUSES` becomes per-mode: Standard unchanged, Extended adds
    `passive` (never `auto_passive`, for the same reason `auto_confirmed` is excluded).
    `handle_post_cwl_enrollment_status` validates against the guild's own set.
  - `handle_post_cwl_player_prefs_status` (~1675): accept `passive` when `bench_offered`.
  - The preferences POST (~1581–1628): accept mode `'bench'`.
- The Worker (`activity/server`) passes these routes through (verified): no code change.

### Phase 3 — DMs (`ui_cwl_roster.py`, `QBdiscocmdshelper_cwl.py`)

- `CWL_SIGNUP_RESPONSE_TEMPLATE` / `CWL_REMINDER_RESPONSE_TEMPLATE`: `confirm|optout` →
  `confirm|passive|optout`. Additive, so every DM already sent keeps working.
- `build_cwl_signup_response_view(...)` and `build_cwl_reminder_response_view(...)` gain a
  `bench: bool` parameter; the callers compute it with `cwl_bench_offered_to()`. Button order:
  Confirm (green), Bench (blue/primary), Opt Out (grey). Reminder rows: three labeled buttons
  (✅/🪑/❌ + name), still within Discord's 5-per-row limit; the 5-account-per-message cap stays.
- Callers: `_send_cwl_enrollment_dm_batch` (~3586), `send_cwl_reminder_dm_group` (~3636), the
  roster-update DM's `never_asked` view (~4198), `rerender_cwl_dm_after_response` (~3154 — it must
  recompute the same decision, or re-rendering a reminder would drop the Bench button).
- DM texts: `cwl.template.dm_body_bench`, `cwl.reminder.dm_intro_body_bench`,
  `cwl.update.dm_confirm_prompt_bench` (Bench variants with the explanation), chosen by the same
  boolean. Finalize text `cwl.template.passive_msg` ("🪑 Alles klar — **{player_name}** sitzt diese
  Saison auf der CWL-Ersatzbank.") in `rerender_cwl_dm_after_response`.
- `_apply_cwl_signup_response`: `passive` → (`'passive'`, `'template_passive'`). **Always accepted**
  — the bot itself offered the button, and a stored `passive` reads as Confirmed on any Standard
  screen, so there is nothing to protect by refusing it.

### Phase 4 — Activity client (`activity/client/src`)

- `signupStatus.ts`: `VisibleStatus` += `'passive' | 'auto_passive'`; icons, English labels
  (Bench / Auto-Bench), `STATUS_LABEL_KEY` (`status_passive`, `status_auto_passive`),
  `isVisibleStatus`.
- `types.ts`: both `signup_status` unions, `AdminSettableStatus` += `'passive'`,
  `PlayerPrefsStatusAction` += `'passive'`, preference `mode` unions += `'bench'`, payload fields
  `signup_mode` / `bench_offered`.
- `enrollmentBoard.ts` (all Extended only):
  - legend (~756): Bench and Auto-Bench rows with a one-line explanation;
  - right-click menu (~585): "Bench" between Confirmed and Declined;
  - clan column header: split the fill count, e.g. `12 + 3🪑 / 15` (bench = `passive` +
    `auto_passive`), so the admin sees how many *active* players a roster really has;
  - `isOptedOut` and sort order unchanged (Bench is not opted out).
- `playerPrefs.ts` (shown when `bench_offered`):
  - third "🪑 Ersatzbank" button between "I'm in" and "I'm out"; disabled when already `passive`;
    `auto_passive` stays clickable (same reasoning as `auto_confirmed`, tracker #0051); the error
    path re-enables all three buttons correctly;
  - `buildModeSelect` and the "apply to all accounts" select: "Immer Ersatzbank" option; if an
    account already has `bench` while `bench_offered` is false, the option is still rendered so
    the select never displays a wrong value or silently overwrites it on save;
  - status label/icon/tooltip for both new statuses.
- i18n (`cwl.activity.*` via `/api/i18n`): `status_passive`, `status_auto_passive`,
  `status_tooltip_auto_passive`, `button_bench`, `bench_tooltip_default`,
  `bench_tooltip_already_bench`, `bench_tooltip_auto_bench`, `mode_bench`.
- `npm run typecheck && npm run build`; deploy both halves.

### Phase 5 — roster announcement, docs, housekeeping

- `_build_cwl_roster_account_lines` / `announce_cwl_rosters`: "(🪑 Ersatzbank)" after a Bench
  player's line, decided by `cwl_bench_offered_to()` like every other DM (the announcement is sent
  by the clan's owning guild; the marker follows the recipient, per §3.2).
- Docs: `CWL_ROSTER_PLANNING_PLAN.md` (§2 status list, §6 DMs, §7 board, the two-audience rule of
  §3.2), `DATABASE_ARCHITECTURE.md` (two new columns, two new status values),
  `CODE_STRUCTURE.md` (the three helpers), CWL help text (one line about Extended sign-up).
- en.json + de.json in the same pass, parity check (Cardinal Rule 6).
- `BOT_BUILD` bump, changelog, tracker test cases.

## 5. Visibility matrix

| Surface | Standard guild | Extended guild |
|---|---|---|
| Enrollment / reminder / roster-update DM | Bench button only if the recipient is on an Extended server | Bench button + explanation |
| Board card icon | Bench → ✅, Auto-Bench → Auto-Confirmed icon | 🪑 icons |
| Board legend, right-click menu | unchanged | + Bench (menu: Bench only, never Auto-Bench) |
| Board column fill | unchanged | `active + bench / size` |
| Hub overview counts | Bench folded into Confirmed / Auto-Confirmed | separate lines |
| CWL Settings | "Standard" readout + Enable button | "Extended" readout + Disable button |
| Player Hub | per §9.5 | Bench button, Always Bench option, 🪑 labels |
| Roster announcement | marker only if the recipient is on an Extended server | marker |

## 6. Mode-switch and cross-guild behaviour

- **Extended → Standard mid-season**: every stored `passive`/`auto_passive` stays and shows as
  Confirmed/Auto-Confirmed on admin screens. Switching back restores them. Nothing is rewritten.
- **Standard → Extended while enrollment is open** (project owner, 2026-09-22): still-unanswered
  DMs are **upgraded automatically** — see §6.1.
- **Player pooled by an Extended and a Standard server**: their DM offers Bench whichever server
  sends it (§3.2). A Bench answer shows as Bench on the Extended server and as Confirmed on the
  Standard one. If the Standard server's admin then sets Confirmed/Declined, that overwrites the
  global status for both — today's "last action wins" rule, unchanged.
- **"Always Bench" preference in a Standard-only world**: the seed is `auto_passive`, displayed as
  Auto-Confirmed. The preference is account-wide, not per server.

### 6.1 Upgrading unanswered DMs when a server enables Extended

Feasible without new bookkeeping: every DM send already records, per account,
`cwl_player_season_status.dm_sent_via_message_id` / `dm_sent_via_channel_id` / `dmed_discord_id`
(written by `mark_cwl_player_dm_sent_sync`), and the bot may edit its own DM messages.

New `upgrade_pending_cwl_dms_for_bench(guild_id)` (`QBdiscocmdshelper_cwl.py`), started by the
CWL Settings toggle when it switches **to** Extended:

1. Only while that guild's current event is `signup_open` — DM buttons answer
   `signup_closed` in every later phase, so upgrading them then would only add dead buttons.
2. Candidates: this season's global rows with `status = 'pending'` and `dm_sent = 1` whose
   recipient now qualifies under §3.2 — i.e. DMs sent by this guild, **and** DMs sent by any other
   guild to someone who is a member of this guild (Q4: the DM follows the player).
3. Grouped by message ID (one Remind Pending / roster-update DM can cover up to 5 accounts), per
   message: fetch it, rebuild the view from the accounts of that message that are still pending
   (the scope `rerender_cwl_dm_after_response` already derives), and append the Bench explanation
   to the existing content unless it's already there. Appending instead of replacing matters: a
   roster-update DM's text (where to play, when) must survive the edit — re-using
   `rerender_cwl_dm_after_response` as-is would replace it with the reminder intro.
4. Runs as a tracked background task (`QBcore.spawn_tracked`), sequential with a short pause per
   message and Discord-rate-limit handling via the existing retry wrapper. A deleted or
   unreachable DM (`NotFound`/`Forbidden`) is logged and skipped, never fatal (Pitfall 13).
5. The toggle's confirmation says how many unanswered invitations are being updated.
6. Idempotent: running it twice changes nothing the second time (the explanation check, and the
   view is rebuilt from live state).

**Switching back to Standard does not touch sent DMs**: a Bench button left in a DM is harmless
(an answer is stored as `passive` and shows as Confirmed there), and under §3.2 the player may
still be on another Extended server anyway.

Tests: candidates (own-guild DM, other-guild DM to a member, non-member excluded, answered
excluded, non-`signup_open` → no-op); one edit per message for a multi-account DM; content
appended once; `NotFound` skipped.

## 7. Blast radius and rollout

- Default Standard everywhere; Standard admin payloads stay identical (server-side mapping),
  guarded by a test comparing a Standard guild's payload for a `passive` player with a
  `confirmed` one.
- **One exception, by the Q4 decision**: a player who is on an Extended server gets the Bench
  button in DMs sent by *any* server. Before any server enables Extended, nobody gets it.
- Phase 0 changes CACHE write-through for every user save: covered by the structural + round-trip
  tests; it only ever *preserves* values that were previously dropped.
- Deploy order: bot first (inert while every guild is Standard), then Activity client + Worker
  (both halves per the deploy rule), then enable Extended on one server. The client must be live
  before any server enables Extended, or an old client would render `passive` without an icon.
- DynamicItem regex change is additive.

## 8. Tests

- Phase 0: structural column-coverage test; opt-in / DM-anyway / bench survive `save_user()`.
- `effective_cwl_signup_status` (both modes, every status); `cwl_bench_offered_to` (sending guild
  Extended; recipient on another Extended guild; neither); precedence optout > bench > optin in
  `resolve_seeded_cwl_signup_status` and the prefs payload; `set_cwl_preferences_sync('bench')`
  clears the other flags.
- Settled set: a Bench / Auto-Bench player is not re-invited; Hub counts fold vs split.
- DMs: view builders 2 vs 3 buttons; regex parses `passive`; `_apply_cwl_signup_response('passive')`
  writes globally and to every mirror; re-render keeps the Bench button; finalize text.
- Bridge: admin override rejects `passive` in Standard (400) and `auto_passive` everywhere;
  player-prefs status and mode `bench`; payload mapping and `signup_mode` / `bench_offered`.
- Cross-guild: Extended A + Standard B pooling one player; Bench via A's DM → A shows `passive`,
  B shows `confirmed`.
- Client: typecheck + build.

## 9. Decisions

1. Names: **Ersatzbank** (DE) / Bench (EN), 🪑, blue bench icon. ✅ decided.
2. **Always Bench** standing preference: in scope. ✅ decided.
3. Board column split + roster-announcement marker: in scope. ✅ decided.
4. DMs follow the player: Bench offered if the recipient is on any Extended server. ✅ decided.
5. **Open — Player Hub**: the original spec says Standard servers show only standard statuses on
   Activity screens, but decision 4 makes DMs player-based. If the Hub stays server-based, two
   concrete problems follow on a Standard server:
   - a player who picked Bench from a DM sees "Confirmed" in that server's Hub with "I'm in"
     disabled, so they can't switch from Bench to active there;
   - an account with "Always Bench" (set on another server) shows a preference the select can't
     display.
   **Proposal:** the Player Hub is player-facing like the DMs and follows the same rule
   (`bench_offered`), while the board, legend, context menu, Hub counts and settings stay strictly
   server-based.

## 9a. Pre-existing bugs found during this review (not caused by #114)

1. **CWL preferences wiped on every `save_user()`** — "always in" and "send DM anyway" are
   dropped by the CACHE load/save round-trip. Fixed in Phase 0 (it would break "Always Bench" too).
2. **Dead buttons in the roster-update DM**: `send_cwl_roster_updates` gives a never-asked player
   (added to a roster during Preparation) confirm/opt-out buttons plus "Please confirm below…",
   but `_apply_cwl_signup_response` refuses every click once the event has left `signup_open`
   (→ "Sign-up isn't open for this season anymore"). The same send also records no DM message ID
   (`mark_cwl_player_dm_sent_sync(..., None, None)`, ~4212), so the DM can't be retracted by
   Delete Season, re-rendered, or upgraded by §6.1. Options: (a) accept responses during
   `announced`/`war` for players with no settled answer, (b) drop the buttons from that DM. Either
   way the message ID should be recorded. Needs the project owner's call; independent of #114.

## 10. Phasing

0. `user_players` preference round-trip fix (own changelog entry).
1. Storage, guild mode toggle, settings readout, `cwl_permanent_bench` + preference write path.
2. Helpers, seeding, settled/counts, bridge mapping and validation.
3. DM buttons and texts, plus the automatic DM upgrade on enabling Extended (§6.1).
4. Activity client.
5. Roster-announcement marker, docs, help text, changelog, tracker test cases.

Each phase ends with `.\run_tests.ps1` green. Phases 0–3 are Python-only and safe to ship before
the client.
