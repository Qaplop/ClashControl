# Tracker #0125 + #0137 — Player CWL Hub: cross-server season block

Both tickets touch the same screen (`/cwl preferences`, `activity/client/src/playerPrefs.ts`) and
were done together.

## Problem

- #0137 (bug): the "this season" block came only from the server the Hub was opened on
  (`get_current_cwl_event_sync(guild_id)`). On a server that had not started its season it said
  "No CWL season is set up yet", even while another server had already invited the same accounts.
- #0125 (feature):
  1. Block I: show each account's current clan right after the account name.
  2. Block II: an uninvited account shows "Not invited yet" instead of disabled buttons, and a
     new column between Account and Status names the server(s) that invited the account.

## Design

- `WarHistoryDB.find_cwl_invitations_for_players_sync(tags)`: every non-cancelled
  `cwl_signups` row for the accounts, joined with its event (guild, season, phase).
- `web_bridge._resolve_player_prefs_season_sync(guild_id, tags)`: season = newest of the opening
  server's current event and all invitations. Per account, its invitations in that season,
  opening server first, then by guild name.
- Season rows carry `invited_by` and a per-row `event_status` (the first invitation's phase),
  since servers can be in different phases. `signup_status` is the first invitation's; answers
  already propagate to every inviting server (`propagate_cwl_player_response`).
  The assignment is taken from the first inviting event that has placed the account.
- `POST /api/cwl/player-prefs/status` resolves its event the same way (the user's accounts plus
  the requested tag), so a click acts on the inviting server's event. Ownership is still
  checked only by `_apply_cwl_signup_response`.
- Accounts carry `current_clan_tag`/`current_clan_name` (`user_players.current_clan_tag`).
- Client: block I gets a "Current clan" column; block II gets "Invited by", "Not invited yet",
  and per-row actionability. The block-level "enrollment not open" note now considers invited rows
  only. `status_action_tooltip_not_invited` is retired (no disabled buttons left to explain).

## Tests

`tests/discord/test_web_bridge.py`: cross-server season GET (season, invited_by, not-invited
row, current clan) and status POST acting on the inviting server's event.
