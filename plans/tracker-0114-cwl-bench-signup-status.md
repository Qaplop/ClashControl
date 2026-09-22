# Tracker #0114 — "Bench" CWL sign-up status (passive participation)

Status: **plan, awaiting the project owner's review of the open decisions (§9)**. Nothing implemented.

## 1. Request

Reporter (Lucas, forwarding Eren): players want to sign up "nur als Mitnahme" — on the CWL roster
without attacking. The project owner's spec (2026-09-22):

- A new sign-up status meaning **"I want to be part of this CWL but not actively attack."** Two
  real uses: passive players still get the base CWL season reward, and clans want backfill players
  in case a regular participant drops out.
- **Per-guild mode**, set in the CWL configuration: **Standard** (today: Confirm / Opt Out only,
  the default for every guild) or **Extended** (adds the new status per CoC account).
- Only when Extended is explicitly enabled does anything new appear: DM buttons, Activity screens,
  legends, context menus, admin override.
- A speaking name, a short explanation in the DMs, and a new icon.

## 2. Naming and icon (proposal)

| | German | English |
|---|---|---|
| Status label | **Mitnahme** | **Bench** |
| DM / Hub button | 🪑 Nur Mitnahme | 🪑 Bench only |
| Guild mode | Erweiterte Anmeldung (mit Mitnahme) | Extended sign-up (with Bench) |

"Mitnahme" is the reporter's own word ("nur als Mitnahme dabei sein", "mitgenommen werden ohne
anzugreifen"), and German CoC players already use it. "Bench" is the closest English equivalent
and also covers the backfill meaning.

Internal value: `passive` (status), `passive` (DM button action), `cwl_signup_mode =
'standard'|'extended'` (guild config). Labels live in i18n only, so renaming later is text-only.

**DM explanation** (Extended only), appended under the existing `cwl.template.dm_body`:

> ✅ **Bestätigen** — du spielst mit und greifst an.
> 🪑 **Nur Mitnahme** — du möchtest im Kader sein (für die Saison-Belohnung oder als Ersatz), willst aber nicht regelmäßig angreifen.
> ❌ **Abmelden** — diese Saison nicht.

(EN: "✅ Confirm — you play and attack. 🪑 Bench only — you'd like to be on the roster (for the
season rewards or as a backup) but don't plan to attack regularly. ❌ Opt out — not this season.")

**Icon**: new `activity/client/src/assets/bench.svg` in the existing icon style (44×44 rounded
square, white glyph, same as gcheck/redx/pending/autoconfirmed): a **blue** square (`#5b8def`,
clearly apart from the greens, the yellow and the red already in use) with a white bench glyph
(seat plank, backrest, two legs). Discord buttons and text use the 🪑 emoji.

## 3. Core design decision: store raw truth, map at display time

A player's response is **global across guilds**: `cwl_player_season_status` is the single source
of truth, `propagate_cwl_player_response()` fans it out to every pooling guild's local mirror
(`cwl_signups.status`, `cwl_shared_clan_players.status`). The mode is **per guild**. So a player
can pick Bench through an Extended guild while also being pooled by a Standard guild.

**Decision:** always store `passive` as-is everywhere (global row and every mirror). A Standard
guild never *sees* it: one Python helper maps it to `confirmed` when building anything a
Standard guild displays or counts.

```python
def effective_cwl_signup_status(status: Optional[str], guild_id: int) -> Optional[str]:
    """'passive' reads as 'confirmed' in a guild whose cwl_signup_mode is not 'extended'."""
```

Why not write `confirmed` into Standard guilds' mirrors instead:
- the mirror would contradict the global truth (Pitfall 25's two-facts-one-column trap, again);
- a guild switching Standard → Extended mid-season would silently show wrong data;
- mapping at read time is reversible, a write is not.

"Want to be part of CWL" is the meaning of both `confirmed` and `passive`, so mapping `passive` →
`confirmed` in Standard mode is semantically correct, not a lossy approximation.

Mapping is done **server-side** (payload builders and Hub counts), never in the client: Standard
guilds then receive byte-identical payloads to today, which is what keeps the blast radius on
Standard guilds (every guild, by default) at zero. The client only needs one flag
(`signup_mode`) to decide whether to show the Bench menu entry, legend row and Hub button.

## 4. Change inventory

### 4.1 Storage — `qapbot/db_manager.py`
- `guild_config.cwl_signup_mode TEXT NOT NULL DEFAULT 'standard'`: CREATE TABLE +
  `_add_column_if_missing` + `get_guild_config` dict + `save_guild_config` INSERT/UPDATE/params
  (all four places, same as #0092's `cwl_coordinator_role_mode`).
- **No status-column migration**: `cwl_signups.status`, `cwl_shared_clan_players.status` and
  `cwl_player_season_status.status` are plain `TEXT DEFAULT 'pending'` with no CHECK constraint
  (verified), so `'passive'` needs no DDL.
- `get_cwl_signup_status_counts_sync` (GROUP BY status) already returns a `passive` key untouched.
- `guild_config` is not one of the hot/history mirrored tables (Cardinal Rule 1 N/A).

### 4.2 Settings UI — CWL Settings screen
- `add_cwl_settings_components` (`ui_cwl_roster.py`): toggle button in the same
  activate/deactivate pattern as `include_all_accounts` ("Enable Extended Sign-up (Bench)" /
  "Disable ..."), `_make_cwl_settings_toggle_signup_mode_callback`. Check the button-row budget
  (row 3 already holds retention, include-all, coordinator role; row 1 holds channels + 2 hub
  toggles) and put it where there's room.
- `format_clan_management_cwl_settings` (`QBdiscocmdshelper_cwl.py`): readout block
  "Sign-up mode: 🟢/🔴 Standard/Extended" + one-line description, like the enrollment-pool block.
- Toggle mid-season is allowed (see §6 for what happens), and refreshes the Hub message and bumps
  the enrollment board version so open boards re-render.

### 4.3 DM buttons — `qapbot/ui_cwl_roster.py`
- `CWL_SIGNUP_RESPONSE_TEMPLATE` / `CWL_REMINDER_RESPONSE_TEMPLATE`: action group
  `confirm|optout` → `confirm|passive|optout`. Existing DMs keep matching (additive regex).
- `build_cwl_signup_response_view(event_id, player_tag, guild_id)`: add the middle
  `passive` button **only when that guild is Extended**. Order: Confirm (green), Bench only
  (blue/primary), Opt Out (grey).
- `build_cwl_reminder_response_view(...)`: same, per account row. Three labeled buttons per row
  (✅/🪑/❌ + name) still fit Discord's 5-per-row limit; the 5-row account cap is unchanged. Used by
  Remind Pending, `rerender_cwl_dm_after_response`, and the roster-update DM's `never_asked`
  buttons (`QBdiscocmdshelper_cwl.py` ~4198) — all three get it through this one builder.
- `CwlSignupResponseButton` / `CwlReminderResponseButton`: label/style for `passive`.
- `_apply_cwl_signup_response`: `new_status`/`source` mapping gains `passive` →
  (`'passive'`, `'template_passive'`). **Accepted even if the guild has since switched back to
  Standard** — storing `passive` is harmless there (§3 mapping), and rejecting a button the bot
  itself sent would be worse.
- `rerender_cwl_dm_after_response`: finalize text `cwl.template.passive_msg` for `action ==
  "passive"` ("🪑 Got it — **{player_name}** is on the bench for this season's CWL roster pool.").
- DM text: `cwl.template.dm_body` gets the three-line explanation (§2) **only in Extended guilds**
  — a separate key `dm_body_extended`, chosen by the sending guild's mode. Same for
  `cwl.reminder.dm_intro_body` ("confirm, bench or opt out").
- `_send_cwl_enrollment_dm_batch` (~3586) and `send_cwl_reminder_dm_group` (~3636) pick the
  body key by mode; the view builders already receive `guild_id`.

### 4.4 Status semantics — `qapbot/QBdiscocmdshelper_cwl.py`
- New `effective_cwl_signup_status()` + `is_cwl_extended_signup(guild_id)` helpers (one place).
- `settled_statuses` in `resolve_cwl_pool_tags_missing_dm_sync` (~970) gains `passive`, or a Bench
  player would be re-invited by "Notify New Pool Members". Same audit for the two docstring-level
  "settled" descriptions at ~899/~945.
- Every `status == "pending"` filter (remind targets, pending split, DM re-render, handover at
  ~5046) is already correct: `passive` is an answer, not pending. No change, but covered by tests.
- Hub season overview counts (~584): Extended shows a separate "🪑 Bench: N" line after Confirmed;
  Standard folds `passive` into Confirmed. New i18n key `cwl.management.signup_status_passive`.
- `resolve_seeded_cwl_signup_status` carries an existing global `passive` into new local seeds
  unchanged. Correct, no change.
- **Auto-assignment stays status-agnostic** (verified: placement is driven by attack history,
  not by status). Bench players are placed like everyone else; the admin decides.

### 4.5 Web bridge — `qapbot/web_bridge.py`
- `_build_enrollment_payload_sync`: apply `effective_cwl_signup_status` at all four
  `signup_status` sites (~483 `_owner_signup_status_for`, ~539, ~570, ~640) and add
  `"signup_mode": "standard"|"extended"` to the payload.
- `_build_player_prefs_payload_sync` (~1544): same mapping + `signup_mode`.
- `ADMIN_SETTABLE_ENROLLMENT_STATUSES` → per-mode: Standard `("confirmed","declined","pending")`
  unchanged; Extended adds `"passive"`. `handle_post_cwl_enrollment_status` validates against the
  **guild's** set, so a Standard guild can't be made to write `passive` even by a crafted request.
- `handle_post_cwl_player_prefs_status` (~1675): accept `passive` only when the guild is Extended
  (the Hub never shows the button otherwise; this is the defensive check).
- The Worker (`activity/server`) is a pass-through for these routes (verified): no change.

### 4.6 Activity client — `activity/client/src`
- `signupStatus.ts`: `VisibleStatus` += `'passive'`, `STATUS_ICON.passive = bench.svg`,
  `STATUS_LABEL.passive = 'Bench'`, `STATUS_LABEL_KEY.passive = 'status_passive'`,
  `isVisibleStatus` accepts it.
- `types.ts`: `signup_status` unions += `'passive'` (both), `AdminSettableStatus` += `'passive'`,
  `PlayerPrefsStatusAction` += `'passive'`, payload types += `signup_mode`.
- `enrollmentBoard.ts`:
  - legend (~756): Bench row **only when `signup_mode === 'extended'`**, explanation text
    "Wants to be on the roster for the season rewards or as a backup, not to attack regularly."
  - right-click menu (~585): "Bench" entry between Confirmed and Declined, Extended only.
  - `isOptedOut` unchanged (Bench is not opted out); sort order unchanged.
  - Clan column header (recommended, Extended only): split the fill count, e.g. `12 + 3🪑 / 15`,
    so the admin sees how many *active* players a roster really has — the actual reason this
    status is useful for backfill planning.
- `playerPrefs.ts`: third "🪑 Bench only" button between "I'm in" and "I'm out", Extended only;
  disabled when already `passive`; tooltips; `status_passive` label; `fireStatusChange('passive')`.
  In Standard nothing changes (server already maps any `passive` to `confirmed`).
- i18n (`cwl.activity.*` via `/api/i18n`): `status_passive`, `button_bench`,
  `bench_tooltip_default`, `bench_tooltip_already_bench`, `status_tooltip_passive`.

### 4.7 Roster announcement (Start Preparation) — recommended, Extended only
`_build_cwl_roster_account_lines` / `announce_cwl_rosters`: for a Bench player add "(🪑 Bench)"
to their line, so a player who chose Bench and still got placed knows the leader registered that.
No logic change, text only.

### 4.8 i18n, docs, housekeeping
- en.json + de.json in the same pass, parity check (Cardinal Rule 6).
- `CWL_ROSTER_PLANNING_PLAN.md` §6/§7 (status vocabulary, mode, mapping rule), the `cwl_signups`
  status list in its §2; `DATABASE_ARCHITECTURE.md` (new column, new status value);
  `CODE_STRUCTURE.md` (new helper); CWL help text (`cwl.management.help_details_description`) gets
  one line about Extended mode.
- `BOT_BUILD` bump, changelog, manual tracker test cases.

## 5. Visibility matrix

| Surface | Standard guild | Extended guild |
|---|---|---|
| Enrollment DM | Confirm / Opt Out, current text | + Bench only, 3-line explanation |
| Remind Pending / roster-update DM | 2 buttons per account | 3 buttons per account |
| Board card icon | a Bench player shows ✅ Confirmed | 🪑 Bench icon |
| Board legend | unchanged | + Bench row |
| Board right-click menu | Confirmed / Declined / Pending | + Bench |
| Board column fill | unchanged | `active + bench / size` |
| Player Hub | I'm in / I'm out, Bench shown as Confirmed | + Bench only button, Bench label |
| Hub overview counts | Bench folded into Confirmed | separate Bench line |
| CWL Settings | mode readout + "Enable" button | mode readout + "Disable" button |

## 6. Mode-switch and cross-guild behaviour

- **Extended → Standard mid-season**: every `passive` response stays stored and shows as
  Confirmed. Switching back restores the Bench display. Nothing is lost or rewritten.
- **Standard → Extended mid-season**: DMs already sent keep their 2 buttons (a sent message is
  not rebuilt). Players can pick Bench in the Player Hub; admins via the board menu; still-pending
  players get the 3-button version with the next Remind Pending.
- **Player pooled by an Extended and a Standard guild**: the one enrollment DM per season is sent
  through whichever guild's event gets there first, and **that guild's mode decides the buttons**.
  A Bench answer via the Extended guild shows as Confirmed in the Standard one. If the Standard
  guild's admin then sets Confirmed/Declined, it overwrites the global status for both guilds.
  That is today's "last action wins" rule, unchanged.
- **Button of a sent DM clicked after the guild switched to Standard**: stored as `passive`,
  shown as Confirmed (§4.3).

## 7. Blast radius and rollout

- Default is Standard for every guild, and Standard payloads stay identical to today's output
  (server-side mapping). No existing guild sees a difference until an admin enables Extended.
  Guarded by a test comparing a Standard guild's payload for a `passive` player with a
  `confirmed` one.
- Deploy order: **bot first** (the Python side is inert in Standard mode), then **Activity client
  + Worker** (Worker unchanged but re-deploy per the both-halves rule), then enable Extended on one
  guild. An old client receiving `passive` would render no icon, which is why the client must be
  live before any guild enables Extended.
- DynamicItem regex change is additive: every existing DM button keeps matching.

## 8. Tests

- Unit: `effective_cwl_signup_status` (both modes, every status); `settled_statuses` includes
  `passive` (a Bench player is not re-invited); Hub counts fold vs split.
- DM: view builders produce 2 vs 3 buttons by mode; regex parses `passive`; `_apply_cwl_signup_
  response('passive')` writes `passive` globally and to every mirror; accepted after a switch to
  Standard; finalize text.
- Bridge: admin override rejects `passive` in Standard (400), accepts in Extended; player-prefs
  status same; enrollment and player-prefs payloads map in Standard, pass through in Extended,
  carry `signup_mode`.
- Cross-guild: Extended guild A + Standard guild B pooling one player; Bench via A → A's payload
  `passive`, B's `confirmed`.
- DB: `cwl_signup_mode` default + round-trip.
- Client: `npm run typecheck && npm run build`.

## 9. Open decisions for the project owner

1. **Names**: "Mitnahme" / "Bench" (+ 🪑, blue bench icon) — OK, or prefer e.g. "Reserve"/"Ersatz"?
2. **Standing preference**: should the Player Hub's permanent preference (today none / opt-in /
   opt-out, stored per account in `user_players`) also get "always Bench"? It needs a new column
   and an `auto_passive` seed status, and it interacts with the per-guild mode. **Recommendation:
   leave it out of v1**, add it if players ask.
3. **Board column split** (`12 + 3🪑 / 15`, §4.6) and **roster-announcement marker** (§4.7):
   recommended, but optional. Include in v1?
4. **Cross-guild DM buttons** (§6): the sending guild's mode decides. Recommendation: accept this.
   The alternative (show Bench if *any* pooling guild is Extended) would leak one guild's setting
   into another guild's DM.

## 10. Phasing

1. Storage + mode toggle + settings readout (no visible change yet).
2. Status helper, settled/counts, bridge mapping + validation (Standard payloads proven identical).
3. DM buttons + texts.
4. Activity client (icon, legend, menu, Player Hub, column split).
5. Roster-announcement marker, docs, help text, changelog, tracker test cases.

Each phase ends with `.\run_tests.ps1` green. Phases 1–3 are Python-only and safe to ship before
the client.
