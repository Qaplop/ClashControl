# Tracker #0129 — `/registration`

Requested by qaplop (2026-09-24) as a follow-up to #0128: the DM "no linked accounts" reply
should point new users to registration, which needs a command.

## Behavior

- **In a server channel:** the registration hub (same text + `RegistrationView` as the anchored
  message), **ephemeral**, so users can't fill channels with their own hub messages.
- **In the bot DM:** a normal DM message for a server the user shares with the bot.
- `commands.errors.dm_not_linked` points to `/registration`.

## Decision: which server in a DM

The registration flow is per server: Link searches that server's clans, and roles are assigned
at link time. A new user has no linked accounts, so the usual DM rule
(`get_dm_caller_matched_guild_ids()`) can't find their server. Options put to qaplop:

1. **Servers the user and the bot share** (only servers with clans configured): one → used
   directly, several → DM server picker. Roles assigned at link time like in the server.
   **Chosen.**
2. No server in the DM: search all tracked clans, roles only on a later sync. Rejected: new
   members would sit without roles.

## Implementation

- `get_dm_registration_guild_ids(client, user_id)`: shared servers with clans (member cache).
- `CACHE.pending_registration_dm_guild` (user → guild), set by the DM `/registration`.
- `get_interaction_guild(interaction)`: `interaction.guild`, else the recorded DM server's
  `Guild`. Used by `complete_account_linking_flow()` (all 32 former `interaction.guild` uses) and
  the verify/role paths in `ui_registration.py` (`_role_guild()`), so no parameter had to be
  threaded through the many registration call sites. Inside a server nothing changes.
- `RegistrationView._resolve_guild_id()` in a DM: the message's own server (recorded as pending
  so role steps agree), else pending, else re-resolve if exactly one shared server (after a
  restart), else 0 → Link answers "run `/registration` again".
- `_prompt_dm_guild_picker(on_pick=...)` (added in #0128) closes the picker, then the hub is
  posted as a followup, so it's a normal DM message.
- `/help` entry, README, `REGISTRATION_MESSAGE_WORKFLOWS.md` section.

## Tests

`tests/discord/test_registration_command.py`: server/DM × 0/1/many servers, helpers, restart
re-resolve, Link rerun hint. Manual: PROD only (DEV commands are guild-registered, Pitfall 40).
