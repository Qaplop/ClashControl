# Tracker #0119 — `/dm me` and `/admin DM User`: greeting DM

## Goal
A new user can ask the bot for a private greeting DM (`/dm me`) that says what ClashControl is,
lists the main features and points to `/help`. A server admin can send the same greeting to a
member (`/admin` → "DM User", standard Discord user picker).

## Design
- **One builder, two callers:** `build_greeting_dm_embed(user_id, guild_id, display_name)` in
  `clashcontrol/greeting_dm.py`, plus `send_greeting_dm(user_id, guild_id, display_name)` which
  sends it through `CACHE.send_user_dm_detailed()` (the single DM choke point, logged) and returns
  its outcome (`sent` / `blocked` / `no_mutual_guild` / `failed`).
- **Content reuses the landing page's texts** (`activity.landing.*`, already in all 5 languages):
  tagline, intro, the five feature bullets, the language line, the three "get started" commands
  (registration, cwl preferences, help). New keys only for the greeting line, the `/help` +
  `/about` hint and the confirmation/error replies (`commands.dm.*`). Commands are rendered with
  `command_mention()` so they are clickable in the DM.
- **Language:** the recipient's own language (`t(user_id=<recipient>, guild_id=<invoking guild>)`).
- **`/dm me`:** new top-level group `dm` with subcommand `me`, works in a server and in the DM.
  Ephemeral reply: "sent, see your DMs" (jump link to the DM channel) or "couldn't DM you —
  allow DMs from server members".
- **`/admin DM User`** (`DM_USER`): guild admin (`check_admin_permissions`), server-only because
  `discord.ui.UserSelect` needs a guild (Pitfall 40). Single ephemeral message: user picker →
  result text edited in place; double-click guarded with `claim_action`/`lock_buttons`
  (Cardinal Rule 7). Bots are rejected.
- `/help`: new entry `dm me` (Player Setup block, after `cwl preferences`), README command list.

## Test cases (tracker)
- `/dm me` in a server → greeting DM arrives in your language, commands clickable; ephemeral
  confirmation.
- `/dm me` with DMs from server members disabled → ephemeral error.
- `/admin DM User` → pick a member → they receive the greeting in their language; pick a bot →
  refusal.
