# Tracker #0128 — `/cwl preferences` in DMs

## Background

- `/cwl preferences` opens the CWL Activity on its `player_prefs` screen via LAUNCH_ACTIVITY
  (`_launch_cwl_activity()`, `ui_cwl_roster.py`).
- `@app_commands.guild_only()` on it is a no-op (discord.py ignores it on subcommands), so the
  command was always offered in DMs. It used to return silently there. Build 82 made it reply
  "server only".
- Build 83 ran an admin-only test on PROD: **Discord does open the Activity in the bot DM.** It
  then stopped at main.ts's "must be launched from inside a guild" check, because
  `discordSdk.guildId` is null in a DM.
- Every Activity → Worker → bridge call already takes `guild_id` from the client. Identity
  (`discord_user_id`) is verified by the Worker. `player-prefs` only ever returns the caller's own
  accounts, so a client-chosen guild exposes nothing new.

## Design

The bot picks the server; the Activity asks the bot for it when Discord gives it none.

### Bot (Python)

1. `cache_manager.py`: new in-memory `pending_cwl_dm_guild: Dict[str, int]`
   (discord_user_id → guild_id). Same lifetime rules as `pending_cwl_activity_screen`: a launch
   hint, not persisted, read non-destructively (Discord's pop-out re-runs the Activity's boot).
2. `QBdiscordcmds.cwl_preferences`, DM branch (replaces the Build 82/83 experiment):
   - Guilds = `get_dm_caller_matched_guild_ids()` (same rule as every other DM command).
   - 0 → ephemeral `commands.errors.dm_not_linked`.
   - 1 → record the DM guild, launch directly (first response).
   - More → the existing DM server picker. Its selection must itself answer with LAUNCH_ACTIVITY
     (type 12 has to be an interaction's first response), so `_prompt_dm_guild_picker()` gets an
     optional `on_pick` callback that replaces its default "Got it" edit. Ours records the DM
     guild, launches from the pick interaction, then clears the picker via the command
     interaction's `edit_original_response()`.
   - Drop the no-op `@app_commands.guild_only()`, so `/help` no longer marks the command 🟠.
3. `web_bridge.py`: `GET /api/cwl/dm-guild?discord_user_id=` → `{"guild_id": "<str>"}` or 404.
   A string, because snowflakes exceed JS's safe integer range.

### Activity

4. Worker (`activity/server/src/index.ts`): `GET /cwl/dm-guild`, verify the user, proxy to the
   bridge. Same shape as `/cwl/screen`.
5. Client (`activity/client/src/main.ts`): when `discordSdk.guildId` is null, fetch
   `/api/cwl/dm-guild` and use its guild. Only if that fails too, show the existing message
   (e.g. the Activity was started from Discord's own app launcher in a DM, not via the command).

### Docs / help

6. `/help` → `cwl preferences.detailed`: note that it also works in a DM (with a server picker
   when accounts belong to several servers), all 5 languages.
7. `CWL_ROSTER_PLANNING_PLAN.md` "Player self-service": same note.

## Deploy

Bot (user deploys) + Worker + Pages. Order: bot first (the new bridge route), then Worker, then
client. An old client never calls the new route; a new client against an old bridge just gets a
404 and shows the existing message.

## Tests

- Bot: DM + 0 / 1 / many guilds; the picker's `on_pick` path launches from the pick interaction;
  bridge route 200/404; `/help` no longer marks `cwl preferences` server-only.
- Manual (PROD, since DEV commands are guild-registered): single-server account and multi-server
  account in the DM; pop-out window; the same command inside a server unchanged.
