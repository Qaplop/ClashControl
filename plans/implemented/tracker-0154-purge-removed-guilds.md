# Tracker #0154 — purge a removed guild's data after 30 days

## Decision (qaplop, 2026-10-13)
Keep a removed guild's configuration for **30 days** (re-adding restores everything, as now),
then delete it. Reason for the grace period: The QCrew was removed and re-added three times on
2026-10-10; an immediate purge would have wiped real settings each time.

## Design

### Absence clock — `guild_absences` table (hot DB only, no history mirror)
`guild_id TEXT PRIMARY KEY, guild_name TEXT, absent_since TEXT (UTC ISO)`.
Mirrored in `CACHE.guild_absences` (guild_id -> {"guild_name", "absent_since"}), write-through.

- `on_guild_remove` with stored data → `CACHE.mark_guild_absent(gid, name)` (keeps an existing
  `absent_since`, so a repeated event does not restart the clock).
- `on_guild_join` → `CACHE.clear_guild_absent(gid)`.
- Startup reconciliation (`log_absent_configured_guilds`): absent configured guilds without a row
  get one (clock starts when first noticed — removals while offline); rows for guilds the bot is
  in again are cleared. `expected_absence()` guilds (DEV: every PROD guild) never get a row.

### Purge — nightly Step 0.9 (`guild_presence.purge_expired_absent_guilds()`)
For each row with `absent_since` older than `GUILD_ABSENCE_PURGE_DAYS` (30): purge only if
presence is definitely False and not `expected_absence()`; present → clear the row; unknown → skip.
Never aborts maintenance (same rule as Steps 0.6-0.8).

What one purge deletes (`purge_guild_data(gid)`):
1. Every CWL event of the guild: `prune_or_detach_shared_clans_before_deletion()` first (shared
   clans keep their roster for other guilds / ownership is repointed), then `delete_cwl_event_sync()`
   (cascades signups, assignments, event clans, dropped notices, shared-clan membership).
2. Tracked `leaderboard_messages` in the guild's subscription channels, then the subscriptions.
3. Clan families owned by the guild that no other guild references (member/welcome families,
   subscriptions). A family another guild still uses is kept and logged.
4. `CACHE.purge_guild_rows()`: `guild_config` (FK cascade: member clans/families, welcome
   clans/families, clan roles, custodians, CWL coordinators + coordinator roles), guest clans,
   /setup dismissals, guild-scoped `bot_settings`, the `guild_absences` row; matching CACHE
   entries popped.
5. Clan tracking statuses recalculated.

Kept: users and their accounts, war/raid data, tracker items, `cwl_player_season_status` audit rows,
`channel_notification_state` (per war, cleaned when the war ends).

### Logging
`[GUILD-REMOVE]` says when the data will be deleted. `[GUILD-PURGE]` one WARNING per purged guild
with name (id), absent since, and counts.
