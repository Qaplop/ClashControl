# Tracker #0135 — Activity landing page for Discord's own Launch button

## Problem

Since the app is listed in the App Directory, its profile shows a **Launch** button (Discord's
auto-created Entry Point command). That opens the Activity without any bot button behind it:

- **DM / app launcher** — `discordSdk.guildId` is null and `/api/cwl/dm-guild` has nothing
  recorded, so `main.ts` stops at "This Activity must be launched from inside a guild."
- **Inside a server** — `/api/cwl/screen` finds no `pending_cwl_activity_screen` entry for
  (guild, user) and falls back to `clan_config`: normal members get the admin clan-config
  screen or a permission error; a server without the bot gets a bridge error.

## Scope (agreed 2026-09-25)

Both cases show a landing page. New users learn what ClashControl is and how to start.

## Design

### Client (`activity/client`)
- New `src/landingPage.ts` → `renderLandingPage(root, t, { onInstall })`:
  - ClashControl icon (`public/clashcontrol-icon.png`, 128 px, derived from
    `artwork/ClashControl_icon_ORIGINAL.png`), title, tagline
  - Feature highlights (short list)
  - **Add ClashControl to your server** button → `discordSdk.commands.openExternalLink` on
    `https://discord.com/oauth2/authorize?client_id=<VITE_CLIENT_ID>` (uses the Developer
    Portal's default install settings, so no permission integer is hard-coded here)
  - **Already on your server?** member steps: `/registration`, `/cwl preferences`, `/help`
    (all of them also work in the DM with the bot)
  - **Server admins** steps: `/subscribe clan:#TAG`, `/clan management`
- `main.ts`: when no guild resolves → landing page (replaces the error text);
  `screen === 'landing'` → landing page. Strings via `GET /api/i18n?ns=activity.landing`, with a
  hard-coded English fallback only if that fetch fails (e.g. bridge down / bot offline), so the
  page never degrades to raw keys.
- `ScreenPayload.screen` gains `'landing'`.

### Worker (`activity/server`)
- `GET /api/i18n`: `guild_id` becomes optional; forwarded only when present.

### Bridge (`qapbot/web_bridge.py`)
- `handle_get_cwl_screen`: default `'landing'` instead of `'clan_config'`. Every bot-side launch
  goes through `_launch_cwl_activity`, which always records a screen first, so the default is
  only hit by launches the bot didn't start.
  - Known trade-off: `pending_cwl_activity_screen` is in memory only; a pop-out re-run after a
    bot restart now lands on the landing page instead of clan config.
- `handle_get_i18n`: `guild_id` optional → `get_user_language(user) or get_guild_language(guild)`
  when present, else `get_user_language(user)` (→ default language).

### Translations
- New top-level `activity.landing` namespace in `en/de/es/zh/la.json`.

### Bookkeeping
- `changelog.txt`, `BOT_BUILD` bump (Python change), tests updated for the new screen default and
  the optional `guild_id`, `CWL_CLAN_CONFIG_ACTIVITY_PLAN.md` / activity README note.
- Deploy: client + Worker to DEV; PROD Activity deploy only on explicit request; the Python bot is
  deployed by the project owner.

## Test cases
- DM / app launcher Launch → landing page, in the user's bot language if set.
- In-server Launch (bot present, no prior button) → landing page, not clan config.
- Server without the bot → landing page; "Add to server" opens the install flow.
- Existing CWL buttons (Configure Participating Clans, Manage Enrollment, CWL preferences) still
  open their own screens.
