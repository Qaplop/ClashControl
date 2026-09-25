# Tracker #0136 — Launch button always opens the landing page + `/about`

Follow-up to #0135 (plans/implemented/tracker-0135-activity-landing-page.md).

## Problem

`CACHE.pending_cwl_activity_screen` / `pending_cwl_dm_guild` are read non-destructively (so
Discord's pop-out reload of the same launch keeps its screen). Side effect: they persist until the
next bot-button click, so after e.g. `/cwl preferences` once, Discord's own Launch button keeps
reopening that screen instead of the landing page.

## Design — claim per Activity instance

The client sends `discordSdk.instanceId` with `/api/cwl/screen` and `/api/cwl/dm-guild`
(Worker passes it through). Bridge, per endpoint:

1. A recorded pending value exists → **pop** it, remember it as the claim of
   (instance, user[, guild]), return it. A fresh click always wins, even inside an instance that
   already claimed something (clicking another CWL button while the Activity is open).
2. No pending value, but this instance already claimed one → return the claim (pop-out reload).
3. Neither → `landing` / 404 (→ landing page).

No `instance_id` (Activity client older than this change) → previous non-destructive read, so
bridge and Activity can be deployed in either order.

Claims live in `CACHE.cwl_activity_instance_claims` (in memory, capped at 1000 entries, oldest
dropped). Residual edge: a click whose Activity never loaded leaves an unclaimed value that the
next launch in that server/DM picks up once.

## `/about`

- New top-level command, DM + server. In a server: records `landing` for (guild, user) and
  launches via `_launch_cwl_activity`. In the DM: drops `pending_cwl_dm_guild[user]` and launches.
- `_launch_cwl_activity` accepts `guild_id=None` (DM, nothing to record) and a `landing` fallback
  text (`commands.about.fallback`: install link + first commands) for a refused launch — also for
  the unverified-app DM refusal (50106), whose existing text points at a server.
- `/help` entry (`commands.help.about`, Bot Info category), README command list, registered in
  `QapBot.py` COMMANDS.

## Bookkeeping
changelog, BOT_BUILD, tests (bridge claim semantics, /about), CWL_CLAN_CONFIG_ACTIVITY_PLAN.md
landing paragraph, cache_manager field comments. Deploy client + Worker to DEV; PROD only on
request; Python deployed by the project owner.
