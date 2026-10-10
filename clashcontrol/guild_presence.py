"""Is the bot still a member of a guild it has configuration for? (tracker #0150 follow-up)

The 2026-10-10 permission audit found two guilds (Odyssey COC 1537876277988429834 and
1400585784347594882) with registration / CWL hub / war-reminder settings and leaderboard
subscriptions still stored, although the bot was no longer in either. Nothing reacted to the
removal, so every cycle kept trying to serve them and logged "Could not reach ... channel" daily.

Policy: **keep the configuration, pause the automations.** A server that re-adds the bot gets
everything back without setting it up again; deleting a guild's data needs an explicit decision
by the bot owner and is deliberately not done here.

``bot_guild_presence()`` answers True / False / None. None means "not known yet": before the
gateway has delivered the guild list, ``get_guild()`` returns None for EVERY guild, and treating
that as "removed" would pause everything at startup. Callers only skip on a definite False.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional

from clashcontrol.log_throttle import forget_log_throttle, log_throttled

_ABSENT_KEY = "guild-absent"


def bot_guild_presence(guild_id: Any) -> Optional[bool]:
    """True if the bot is in ``guild_id``, False if it definitely is not, None if unknown
    (bot not ready yet, or an unparseable id)."""
    import QBcore  # type: ignore[import-untyped]

    bot = getattr(QBcore, "bot", None)
    if bot is None:
        return None
    try:
        ready = bot.is_ready()
        if not isinstance(ready, bool):
            # Not a real client (e.g. an AsyncMock in tests returns a coroutine): unknown. Close
            # it so it can't surface as a "never awaited" warning.
            close = getattr(ready, "close", None)
            if callable(close):
                close()
            return None
        if not ready:
            return None
        found = bot.get_guild(int(guild_id))
        if inspect.isawaitable(found):
            close = getattr(found, "close", None)
            if callable(close):
                close()
            return None
        return found is not None
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def is_guild_absent(guild_id: Any, *, context: str = "") -> bool:
    """True only if the bot is definitely NOT in ``guild_id``. The first time per process (per
    guild) this is True, one INFO line says the guild's automations are paused; after that it is
    silent, so a loop can call it every cycle."""
    if bot_guild_presence(guild_id) is not False:
        return False
    log_throttled(
        logging.INFO, (_ABSENT_KEY, str(guild_id)),
        f"[GUILD-ABSENT] Bot is not in guild {guild_id} — its stored config is kept, its "
        f"automations are paused until the bot is re-added"
        + (f" (first skipped: {context})" if context else ""),
        interval_s=float("inf"), repeat_suffix="",
    )
    return True


def mark_guild_present(guild_id: Any) -> None:
    """Re-arm the one-time "paused" log line (the bot was re-added to the guild)."""
    forget_log_throttle((_ABSENT_KEY, str(guild_id)))


def configured_guild_summary(guild_id: str) -> Dict[str, Any]:
    """What is stored for ``guild_id``: enabled automations and subscription channel count."""
    from clashcontrol.cache_manager import CACHE

    config = CACHE.server_config.get(str(guild_id), {}) or {}
    enabled = [label for label, flag in (
        ("registration", "registration_message_enabled"),
        ("CWL hub", "cwl_management_message_enabled"),
        ("player CWL hub", "cwl_player_hub_message_enabled"),
        ("war reminders", "channel_war_notifications_enabled"),
        ("raid reminders", "channel_raid_notifications_enabled"),
        ("welcome", "welcome_message_enabled"),
        ("role system", "role_system_enabled"),
        ("CoC roles", "coc_role_enabled"),
        ("clan roles", "clan_role_enabled"),
    ) if config.get(flag)]
    channels = len((CACHE.subscriptions.get(str(guild_id)) or {}))
    return {"enabled": enabled, "subscription_channels": channels, "has_config": bool(config)}


def absent_configured_guilds() -> List[str]:
    """Guild ids that have stored config or subscriptions but the bot is definitely not in.
    Empty while presence is unknown (bot not ready)."""
    from clashcontrol.cache_manager import CACHE

    ids = {str(g) for g in CACHE.server_config.keys()} | {str(g) for g in CACHE.subscriptions.keys()}
    return sorted(g for g in ids if bot_guild_presence(g) is False)


def log_absent_configured_guilds() -> List[str]:
    """Startup reconciliation: one WARNING listing configured guilds the bot was removed from
    while it was offline (or earlier, before this check existed). Returns their ids."""
    absent = absent_configured_guilds()
    if absent:
        parts = []
        for gid in absent:
            s = configured_guild_summary(gid)
            parts.append(
                f"{gid} (enabled: {', '.join(s['enabled']) or 'nothing'}; "
                f"{s['subscription_channels']} subscription channel(s))"
            )
        logging.warning(
            f"[GUILD-ABSENT] {len(absent)} configured guild(s) the bot is no longer in — config "
            f"kept, automations paused: " + "; ".join(parts)
        )
    return absent
