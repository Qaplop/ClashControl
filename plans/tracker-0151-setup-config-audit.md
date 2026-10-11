# /setup (config audit + admin setup guide) and /tutorial (#0121): concept & stepped plan

Status: **Concept agreed 2026-10-11. Phase 1 implemented 2026-10-11 (Build 151, v1.3.43), Phase 2 implemented 2026-10-11 (Build 152), Phase 3 implemented 2026-10-11 (Build 155), Phase 4 implemented 2026-10-11 (Build 156), Phase 5 implemented 2026-10-11 (Build 157); Phases 6–7 open.**
Tracker: #0151 (this feature), #0121 (tutorials). Owner: qaplop. Builds on `/admin Permission Audit` (Build 135, `clashcontrol/bot_permissions.py`)
and is the foundation for tracker #0121 (tutorials).

---

## 1. Goal and the three layers

One registry of **audit steps** feeds three surfaces, built in this order:

Decided 2026-10-11 (qaplop): **one command per audience** — `/setup` for admins, `/tutorial`
for users. The audit is `/setup`'s first screen, not a command of its own.

| Layer | Where | Audience | What it does |
|---|---|---|---|
| A. Config Audit | `/setup` landing screen | guild admins | Read-only checklist: each step ✅/❌/… with explanations of what is wrong and how to fix it. Ephemeral, current guild only. |
| B. Admin setup guide | `/setup` → *Start guided setup* | guild admins | Walks the same steps in order, stops at every step that is not OK, explains it and opens the existing settings screen to fix it; "Re-check" → next step. = the **admin tutorial** of #0121. Includes user-tutorial flows where seeing the user side helps (e.g. the admin links one of their own accounts through the real registration flow). |
| C. User tutorial | `/tutorial` | everyone | #0121's user part (registration, war notifications, CWL preferences). |

**No duplicated code between B and C** (qaplop, 2026-10-11): a user-side flow (link an account,
set war notifications, open CWL preferences) is ONE shared function/step object, called by both
`/setup` and `/tutorial`. The flows themselves reuse the existing registration / notification /
CWL-preferences UIs — the tutorial wraps them, it never re-implements them.

The audit is the source of truth for "is this step done?". The setup guide does not store "step
3 done" — it re-runs the step's check. It only stores what a check cannot know: "admin decided
not to use this feature" (dismissed) and where the admin last stopped.

## 2. Status model

| Icon | Status | Meaning |
|---|---|---|
| ✅ | `ok` | Configured and workable. |
| ❌ | `broken` | Enabled/configured but cannot work (missing permission, deleted channel/role, private war log for a tracked clan…). |
| ⚠️ | `incomplete` | Core item not set up, or set up only partly (no member clans, registration hub off, half of the members unlinked). |
| ⏸️ | `off` | Optional feature not in use — no judgement. Also used for a step the admin dismissed. |
| 🔒 | `blocked` | A prerequisite step is not OK (no member clans → most steps). Not shown red, to avoid 10 red crosses for one root cause. |
| ℹ️ | `info` | Nothing to check, explanation only (how a hub works for users). |

Overview line count stays at 13 (+ a header line with the guild summary), fits one embed.

## 3. The completed checklist

User's list (1–13) kept in order; **bold** = added by this review.

### 1. Bot permissions (server level) — Phase 1
- Administrator granted? Yes → ✅ with note "channel overrides are bypassed, only role
  *position* still matters". No → ⚠️ with the explanation: every function works without it,
  but each channel the bot uses then needs explicit per-channel rights, which makes setup much
  more effort; granting it is safe, the bot only acts on what its features do (+ "verified by
  Discord" — **only once that is true, see Q-7**).
- Each permission of the install set missing at server level → ❌, one line each, with *why*
  (table §3.1).
- **Bot's top role position** — info: "the bot can only assign roles below its own role"; count
  of configured roles above it (details in step 9).
- **Per-channel problems** — count of channel/role issues from `audit_guild()`, each listed
  under its own step (in Phase 1, before those steps exist: listed here directly).
- How to fix: (a) **Re-authorize button** — a link button to the OAuth2 install URL with
  `guild_id=<this guild>&disable_guild_select=true`; re-adding an installed bot updates its
  managed role's permissions in one click (Discord prompts with the install set). (b) Manual:
  Server Settings → Roles → *ClashControl* → Permissions.

#### 3.1 The install set (screenshot) and what each permission is really used for

Verified against the code 2026-10-11 (`grep` over every shipped module):

| Permission | Used for | Customer servers |
|---|---|---|
| View Channels | see every channel it posts in | required |
| Send Messages | hubs, war/raid reminders, leaderboards, welcome | required |
| Send Messages in Threads | same, when a configured channel is a thread | required (if threads used) |
| Embed Links | hubs, reminders, /clan management | required |
| Read Message History | anchored hubs (bump check, delete own previous msg), leaderboards | required |
| Manage Roles | Simple/CoC/Clan/Coordinator roles | required if any role feature is on |
| Attach Files | charts/images (`/leaderboard cwlgroup`, `/analyse`, `/list` image, admin report) | **to verify**: interaction followups may not need it; anchored posts don't attach |
| Use External Emojis | app emojis (`BotEmojis`) in bot messages | **to verify on DEV** which surfaces need it (bot posts vs. interaction replies, where @everyone's permission is reported to matter) |
| Create Public Threads / Manage Threads | today only `ui_tracker.py` (bug tracker, support server) | **required** (decided: future features) |
| Create Invite | today only `ui_tracker.py` (support-server invite) | **required** (decided: future features) |
| ~~Manage Messages~~ | not used anywhere | removed from the install set (Developer Portal, 2026-10-11) |
| ~~Use Slash Commands~~ | members need it; a bot never invokes commands | removed from the install set (Developer Portal, 2026-10-11) |

Q-1 decided (qaplop, 2026-10-11): install set = screenshot minus Manage Messages and Use Slash
Commands; the remaining 12 are required: a missing one is ❌ — except Administrator, which shows
⚠️ (every feature works without it; it only saves per-channel setup). The *why*
text for threads/invite must stay honest: "not used by a feature on your server yet — requested
so upcoming features work without re-authorizing".

### 2. Basic server settings: Language and Timezone — Phase 2
- Show current language and timezone. Defaults (`en`, `UTC`) are indistinguishable from a
  deliberate choice → **Q-3**.
- Timezone matters for CWL start times and reminder timing → hint if `UTC` and CWL is used.

### 3. Clan & Family configuration — Phase 2
- ≥1 member clan or family? None → ⚠️, and steps 4–13 that need clans → 🔒.
- **Per clan: exists / not deleted by Supercell** (`is_deleted`) → ❌.
- **Per clan: war log public** — private log breaks war tracking, war/channel notifications and
  war leaderboards for that clan → ❌ (CoC-side fix: in-game clan settings). Data already cached
  (`coc_cache.py` reads the flag).
- **Guest clans (CWL)** listed as info.

### 4. Welcome message — Phase 2
- Off → ⏸️. On → system channel exists + permissions (existing `audit_guild` "welcome");
  mode `clan_link` needs ≥1 welcome clan/family; apply mode needs an apply channel.

### 5. Player registration — Phase 3
- Registration hub: enabled, channel set, bot permissions (existing check).
- **Member visibility (new check type):** can `@everyone` (or the member role) *view* the hub
  channel and read its history? A hub nobody can see is the most common silent failure that
  the bot-permission audit cannot see.
- **Link coverage:** X of Y current members of the member clans are linked (same data as
  *Manage Registrations*), shown only when > 0; ⚠️ only when nobody is linked (Q-6 — "at
  least the admin should be"). Verified-account count as info.
- Admin functions (Link Accounts / Unlink Player in *Manage Registrations*): ℹ️ how-to text —
  this is tutorial content, also used by layer B.
- User side (hub buttons, `/registration`, My Accounts, API verification): user-audience
  content, reused by layer C.

### 6. War notifications (per-user DMs) — Phase 4
- How many linked users have notifications on (info).
- **Custodians** per clan (*Manage War Notifications → Custodians*): info / ⚠️ if none?
- Admin functions *Whole Clan* / *Single User*: ℹ️ how-to.
- (Not checkable: whether a user's DMs are open — unless DM failures are already recorded; to
  check during Phase 4.)

### 7. Channel war notifications — Phase 2
- Off → ⏸️. On → channel + permissions (existing); notification time (threshold) shown.

### 8. Raid reminders — Phase 2
- Off → ⏸️. On → channel (or fallback to the war channel — say which) + permissions; threshold
  and scope shown.

### 9. Server roles — Phase 3
- Simple Roles (newbie/member, simple/strict mode), CoC Roles, Clan Roles: each off → ⏸️, on →
  Manage Roles + per-role hierarchy/managed/deleted (existing `check_role_feature`).
- **Clan-role completeness:** every member clan has a mapped role when Clan Roles is on.
- **Bot role position** explained with the concrete roles above it and the fix (drag the
  ClashControl role above them).

### 10. CWL Settings (whole screen, top to bottom) — Phase 3
- Channels for CWL Management Hub and Player CWL Settings Hub; both toggles; data retention;
  Include All Linked Accounts; coordinator role (single / per clan, existing role checks);
  Extended Sign-up (Bench).
- Hub channels: bot permissions (existing) + **member visibility** for the player hub.
- **Use Activities** for members in the player-hub channel — "Your CWL Preferences" opens the
  Activity (to verify on DEV whether Discord checks this permission for a LAUNCH_ACTIVITY
  response).

### 11. "Your CWL Preferences" hub (user view) — Phase 4
- **As built (Build 156):** always ℹ️ — whether the player hub works is step 10's job, so a
  problem there is never red twice. Steps 11 and 12 require step 3 (clans) only, not step 10.
- ℹ️ explanation of what users see and can set; ✅/⚠️ only for "hub enabled and visible".
  Content shared with layer C.

### 12. CWL Season Management (admin) — Phase 4
- Season state against the calendar: in the sign-up window (last ~10 days of the month) and
  no season / no participating clans / no start time → ⚠️; otherwise current phase as info.
- **CWL coordinators (people)** assigned? info.
- Workflow how-to (Start Enrollment → Manage Teams → Start Preparation…): ℹ️, tutorial content.

### 13. Leaderboards (subscribe/unsubscribe) — Phase 2
- No subscriptions → ⏸️. Per subscribed channel: bot permissions (existing); subscribed
  clan/family still a member clan and still tracked.
- How-to for `/subscribe` / `/unsubscribe` / `/subscriptions`: ℹ️.

### Not in the audit (deliberately)
- Slash-command permission overrides (Server Settings → Integrations): the bot cannot read
  them without an OAuth bearer token. Mentioned in the tutorial text only.
- Analysis commands (`/analyse`, `/whois`, `/list`, `/leaderboard`): no config → user tutorial.

## 4. Architecture

```
clashcontrol/config_audit.py      (pure logic, no Discord sends; unit-testable)
    AuditStatus (enum), Finding, StepResult, AuditStep, STEPS registry, run_audit(guild)
    └─ reuses bot_permissions.py: audit_guild(features=…), check_role_feature, format_issue
clashcontrol/ui_config_audit.py   (Discord UI)
    ConfigAuditView: overview embed ⇄ step detail embed, one ephemeral message edited in place
QBdiscordcmds.py                  /setup top-level command (@app_commands.guild_only() — a top-level command, so it really applies; Pitfall 40)
```

```python
@dataclass(frozen=True)
class AuditStep:
    id: str                      # "bot_permissions", "clans", … (stable: stored in progress tables)
    order: int
    title_key: str
    audience: Literal["admin", "user", "both"]
    requires: Tuple[str, ...]    # step ids → 🔒 when one is not OK
    check: Callable[[discord.Guild], StepResult]   # sync, cache-only (like audit_guild)
    howto_keys: Tuple[str, ...]  # explanation pages → audit detail + setup guide + tutorial
    fix_action: Optional[str]    # id of an existing settings screen to open (Phase 5)

@dataclass
class StepResult:
    status: AuditStatus
    summary_key: str + params     # one line for the overview
    findings: List[Finding]       # severity, text key + params, fix hint key
```

- **Cache-only** checks (Cardinal Rules 3/9): no API calls during an audit, same as
  `audit_guild`. Link coverage and war-log flag come from caches already filled by the cycle.
- **One ephemeral message** (Rule 7): overview = embed with 13 lines + a select "Show details
  for…" (13 options); detail = embed for one step + Back / Re-check (+ later "Open settings").
  Re-check re-runs only that step. Pitfall 41: keep the latest responding interaction and stop
  editing after ~14 min.
- **Admin check**: `check_admin_permissions()` (Administrator or bot admin), same as
  /clan management. (No `default_permissions` on the group — the project does gating at runtime
  everywhere; and the user-facing `/tutorial` must stay visible to members anyway.)
- **i18n**: all text via `t()`, every key in all 5 languages (Rule 6). Permission names reuse
  `permissions.perm.*`; new keys under `config_audit.*`.
- **Logging**: one INFO line per audit run with the status counts (no WARNING — the admin is
  looking at it; the config-time `[BOT-PERMS]` warnings remain the alerting path).
- **Install set as code**: `bot_permissions.INSTALL_PERMISSIONS` (the screenshot set minus
  Manage Messages and Use Slash Commands, Q-1) — single source for step 1 and the re-authorize URL's `permissions=`.

### 4.1 How layer B builds on it (Phase 6)
- `/setup` → *Start guided setup* walks `STEPS` in order with `audience in (admin, both)`: for the first step
  not `ok`/`off`/`info`-already-seen, show the detail page + howto + buttons *Open settings* /
  *Re-check* / *Skip — we don't use this* / *Next*.
- New table `guild_setup_progress(guild_id, step_id, state ['dismissed'|'seen'], updated_by,
  updated_at, PK(guild_id, step_id))` — idempotent DDL, named columns (Rules 12, 14). A
  dismissed step shows ⏸️ in the audit too.
- Progress for "where did I stop" = first non-OK step, recomputed — no cursor needed.

### 4.2 Shared user flows, and how layer C builds on it (Phase 7, #0121)
- **`TutorialFlow` objects** (new `clashcontrol/tutorial_flows.py`) for the user-side
  experiences: `link_account` (opens the real registration flow), `war_notifications`,
  `cwl_preferences`. Each = howto pages + "try it now" action + per-user done-check. Defined
  once; `/setup`'s guided steps 5/6/11 embed them ("link one of your own accounts so you see
  what your members see"), and `/tutorial` strings them together for users.
- Steps 5, 6, 11 carry user-audience howto pages; the user tutorial shows them and adds
  "do it now" checks per user (has ≥1 linked account? notifications set? CWL preferences
  saved?) — same `check`-shaped functions, keyed by user instead of guild.
- Per-user progress table `user_tutorial_progress(discord_id, track ['user'|'admin'], step_id,
  …)` + "start over" (#0121). Admin tutorial = layer B (#0121: "admin tutorial builds on the
  user tutorial") — layer B can show "complete the user tutorial first" as a soft hint.
- DM constraints from Pitfall 40 apply to layer C only (layers A/B are server-only).

## 5. Stepped implementation plan

Each phase is shippable on its own: own Build, changelog entry, `/help` + README update,
tests (`.\run_tests.ps1`), tracker test cases.

| Phase | Content | Size |
|---|---|---|
| **1** | `/setup` skeleton (command, admin gate, ephemeral overview ⇄ detail view, status model, registry) + **step 1 fully functional** (server perms with per-permission *why*, Administrator explanation, re-authorize link button, role-position info, channel/role issues from `audit_guild`). Steps 2–13 listed as "coming soon" (grey) or hidden (Q-5). | M |
| 2 | Pure config-state steps reusing `audit_guild`: 2 Language/TZ, 3 Clans (+ war log, deleted), 4 Welcome, 7 Channel war notifications, 8 Raid reminders, 13 Leaderboards. `blocked` dependencies. | M |
| 3 | 5 Registration (+ member visibility, link coverage), 9 Roles (+ clan-role completeness), 10 CWL Settings (+ member visibility, Use Activities after DEV check). | M–L |
| 4 | Info-heavy steps 6, 11, 12 (season-state vs calendar, custodians, coordinators). | M |
| 5 | (As built: opens the whole /clan management panel in the right mode as an ephemeral message — the sub-views are bound to that panel and can't open alone.) "Open settings" buttons from a step detail into the existing views (Language, Timezone, Channels, Member Clans, Roles, CWL Settings…) — needs a check per view that it can be opened ephemerally outside `/clan management`. | M |
| 6 | Layer B guided setup inside `/setup` + `guild_setup_progress` (dismiss). | M |
| 7 | Layer C user tutorial (#0121). | L |

### Phase 1 in detail
1. `bot_permissions.py`: `INSTALL_PERMISSIONS` + `missing_install_permissions(guild)` +
   `reauthorize_url(app_id, guild_id)` (extend `constants.add_to_server_url()` — Rule 4).
2. `config_audit.py`: status enum, dataclasses, registry with step 1; steps 2–13 as
   placeholders per Q-5.
3. `ui_config_audit.py`: `ConfigAuditView` (overview, select, detail, Back, Re-check,
   re-authorize link button); `claim_action` not needed (read-only), but re-check guarded
   against double clicks with `lock_buttons`.
4. `QBdiscordcmds.py`: top-level `/setup` command (`@app_commands.guild_only()`), audit as
   its landing screen (no guided setup button yet), `/help` entry, `_get_help_command_dm_status` mapping; README command list.
4b. Nudges to `/setup` (Q-9): `[BOT-PERMS]` warning footer, join greeting (#0141), `/help` +
   `/about`, DM free-text reply (`on_message`). All via `command_mention("setup")`.
5. Translations: `config_audit.*` + `permissions.why.<perm>` in en/de/es/zh/la, parity check.
6. Tests: unit tests for step-1 classification with fake guild/member/permissions
   (Administrator / missing set / no `me`), placeholder steps, URL builder; discord test for
   the view's overview ⇄ detail edit.
7. BOT_BUILD +1, changelog, `CODE_STRUCTURE.md` § bot permissions extended, tracker test cases
   (DEV: guild with Administrator, guild without Administrator and two perms removed,
   re-authorize link restores them).

## 6. Decisions (grill list)

Decided 2026-10-11 (qaplop):
- **Q-1** Install set: Manage Messages and Use Slash Commands removed in the Developer Portal;
  the rest (threads, invite included) stays required → §3.1.
- **Q-2** Optional feature off → ⏸️ neutral.
- **Q-3** Language/timezone defaults → ✅ + hint, no schema change.
- **Q-4** `/setup` (admins; audit = landing screen, guided setup behind it) and `/tutorial`
  (users). Admin guide reuses user-tutorial flows via shared functions → §1, §4.2.

- **Q-5** Phase 1 checklist shows all 13 lines; steps 2–13 greyed "not checked yet".
- **Q-7** The app IS Discord-verified (qaplop, 2026-10-11) → the Administrator explanation says
  so. Pitfall 40 updated the same day.
- **Q-8** `/setup` = Administrator or bot admin only (`check_admin_permissions()`).
- **Q-9** Point to `/setup` from: (a) the config-time `[BOT-PERMS]` warnings
  (`format_issues_message()` footer), (b) the greeting when the bot joins a server (#0141),
  (c) `/help` and `/about`, (d) the DM free-text reply (`on_message`, `ClashControl.py`) —
  there, `/tutorial` too once it exists. (a)–(d) ship in Phase 1 for `/setup`.
Decided 2026-10-11, round 3 (qaplop):
- **Q-6** Link coverage: show "X of Y linked" only when X > 0; ⚠️ only when nobody is linked.
- **Q-10** Ephemeral only, no "post to channel".
- **Q-11** Guided setup asks the admin to link an account, with *Skip* → *My Accounts* instead.
  Never forced.
- **Q-12** Setup decisions ("we don't use this") stored per server, and reversible (an *Undo* /
  *Use it after all* on the step, since needs change).
- **Q-13** Own tracker item for `/setup` (see header); #0121 keeps the tutorial layers.
- **Q-14** Step 1 checks server-level permissions only for now.

No open questions left for Phases 1–5.
