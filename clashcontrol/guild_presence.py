"""Is the bot still a member of a guild it has configuration for? (tracker #0150 follow-up)

The 2026-10-10 permission audit found two guilds (Odyssey COC 1537876277988429834 and
1400585784347594882) with registration / CWL hub / war-reminder settings and leaderboard
subscriptions still stored, although the bot was no longer in either. Nothing reacted to the
removal, so every cycle kept trying to serve them and logged "Could not reach ... channel" daily.

Policy: **keep the configuration, pause the automations** — for ``GUILD_ABSENCE_PURGE_DAYS``
(30, qaplop 2026-10-13, tracker #0154). A server that re-adds the bot within that time gets
everything back without setting it up again (The QCrew was removed and re-added three times on
2026-10-10). After it, the nightly maintenance deletes the guild's data
(``purge_expired_absent_guilds()``); the clock is the ``guild_absences`` table.

``bot_guild_presence()`` answers True / False / None. None means "not known yet": before the
gateway has delivered the guild list, ``get_guild()`` returns None for EVERY guild, and treating
that as "removed" would pause everything at startup. Callers only skip on a definite False.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from clashcontrol.log_throttle import forget_log_throttle, log_throttled

_ABSENT_KEY = "guild-absent"

#: Days a removed guild's data is kept before the nightly purge deletes it (tracker #0154).
GUILD_ABSENCE_PURGE_DAYS = 30


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


def expected_absence(guild_id: Any) -> bool:
    """True if the bot is not meant to be in ``guild_id`` at all: on DEV, every guild except the
    DEV guild. DEV runs on a PROD-DB copy and is only invited to its own guild, so every other
    configured guild is absent by design. Without this, each DB copy re-reported all of them
    (DEV 2026-10-10: 6 INFO lines plus a startup WARNING). Absence of the DEV guild itself is still
    reported."""
    from clashcontrol.config import CONFIG

    return bool(CONFIG.is_dev_mode) and str(guild_id) != str(CONFIG.discord_guild_id)


def is_guild_absent(guild_id: Any, *, context: str = "") -> bool:
    """True only if the bot is definitely NOT in ``guild_id``. The first time per process (per
    guild) this is True, one INFO line says the guild's automations are paused; after that it is
    silent, so a loop can call it every cycle."""
    if bot_guild_presence(guild_id) is not False:
        return False
    log_throttled(
        logging.DEBUG if expected_absence(guild_id) else logging.INFO, (_ABSENT_KEY, str(guild_id)),
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
    while it was offline (or earlier, before this check existed). Returns their ids. On DEV the
    guilds it was never meant to join (``expected_absence``) only get one INFO count line and are
    not returned."""
    absent = absent_configured_guilds()
    expected = [g for g in absent if expected_absence(g)]
    if expected:
        logging.info(
            f"[GUILD-ABSENT] DEV: {len(expected)} configured guild(s) from the PROD-DB copy are not "
            f"joined by this bot (expected) — their automations are skipped"
        )
        absent = [g for g in absent if g not in expected]
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


# ---------------------------------------------------------------------------------------------
# Absence clock and 30-day purge (tracker #0154)
# ---------------------------------------------------------------------------------------------

def purge_due_date(absent_since: str) -> str:
    """The UTC date (YYYY-MM-DD) from which a guild absent since ``absent_since`` is purged."""
    since = datetime.fromisoformat(absent_since)
    return (since + timedelta(days=GUILD_ABSENCE_PURGE_DAYS)).date().isoformat()


async def record_guild_removed(guild_id: Any, guild_name: Optional[str]) -> Optional[str]:
    """Start (or keep) the absence clock for a guild with stored data. Returns absent_since."""
    from clashcontrol.cache_manager import CACHE

    if expected_absence(guild_id):
        return None
    return await CACHE.mark_guild_absent(guild_id, guild_name)


async def reconcile_guild_absences() -> None:
    """Startup: start the clock for configured guilds the bot left while it was offline, and stop
    it for guilds it is back in. Does nothing while presence is unknown (bot not ready)."""
    from clashcontrol.cache_manager import CACHE

    for gid in absent_configured_guilds():
        if gid not in CACHE.guild_absences and not expected_absence(gid):
            since = await CACHE.mark_guild_absent(gid, None)
            if since:
                logging.warning(
                    f"[GUILD-ABSENT] Guild {gid} was left while the bot was offline — its data is "
                    f"deleted on {purge_due_date(since)} unless the bot is re-added before then"
                )
    for gid in list(CACHE.guild_absences):
        if bot_guild_presence(gid) is True:
            await CACHE.clear_guild_absent(gid)
            logging.info(f"[GUILD-ABSENT] Bot is back in guild {gid} — scheduled data deletion cancelled")


def _families_used_by_other_guilds(guild_id: str) -> Set[str]:
    """Family tags another guild still references (member/welcome families, subscriptions)."""
    from clashcontrol.cache_manager import CACHE

    used: Set[str] = set()
    for gid, cfg in CACHE.server_config.items():
        if str(gid) == guild_id or not isinstance(cfg, dict):
            continue
        used.update(cfg.get("member_families") or [])
        used.update(cfg.get("welcome_family_tags") or [])
    for gid, channels in CACHE.subscriptions.items():
        if str(gid) == guild_id:
            continue
        for subs in (channels or {}).values():
            used.update(str(s.get("clan_tag")) for s in (subs or []) if isinstance(s, dict) and s.get("clan_tag"))
    return used


async def purge_guild_data(guild_id: str) -> Dict[str, Any]:
    """Delete everything stored for a guild the bot left (tracker #0154). Kept: users and their
    accounts, war/raid data, tracker items, cwl_player_season_status audit rows. Returns counts;
    raises if the final config delete fails (the absence row then stays, so the next night retries)."""
    import asyncio

    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import prune_or_detach_shared_clans_before_deletion

    db = CACHE.db_manager
    if db is None:
        raise RuntimeError("Database manager not initialized")
    gid = str(guild_id)
    result: Dict[str, Any] = {}

    # 1. CWL seasons — the shared-clan guard first, as Delete Season does (keeps a shared roster for
    #    the other guilds, repoints ownership), then the event and its cascade.
    events = await asyncio.to_thread(db.list_cwl_events_sync, gid)
    for ev in events:
        await prune_or_detach_shared_clans_before_deletion(int(gid), ev["id"], ev["cwl_season"])
        await asyncio.to_thread(db.delete_cwl_event_sync, ev["id"])
    result["cwl_events"] = len(events)

    # 2. Leaderboard messages tracked in the guild's subscription channels, then the subscriptions.
    channels = {str(c) for c in (CACHE.subscriptions.get(gid) or {})}
    lb_keys = [k for k, v in CACHE.leaderboard_messages.items() if str(v.get("channel_id")) in channels]
    for key in lb_keys:
        await CACHE.delete_leaderboard_message(key)
    result["leaderboard_messages"] = len(lb_keys)
    result["subscription_channels"] = len(channels)
    if channels:
        await CACHE.delete_subscriptions_for_guild(gid)

    # 3. Clan families this guild created, unless another guild still uses them.
    used_elsewhere = _families_used_by_other_guilds(gid)
    deleted_families: List[str] = []
    kept_families: List[str] = []
    for tag, fam in list(CACHE.clan_families.items()):
        if str(fam.get("owned_by_guild") or "") != gid:
            continue
        if tag in used_elsewhere:
            kept_families.append(tag)
        else:
            await CACHE.delete_clan_family(tag)
            deleted_families.append(tag)
    result["families_deleted"] = deleted_families
    result["families_kept"] = kept_families

    # 4. The guild's own config rows (guild_config cascade + guest clans, /setup dismissals,
    #    guild-scoped bot_settings, the absence row).
    counts = await CACHE.purge_guild_rows(gid)
    if counts is None:
        raise RuntimeError(f"config delete failed for guild {gid}")
    result["rows"] = counts
    return result


async def purge_expired_absent_guilds(now: Optional[datetime] = None) -> List[str]:
    """Nightly maintenance Step 0.9: purge guilds absent for GUILD_ABSENCE_PURGE_DAYS or longer.
    Only on a definite absence; a guild the bot is back in has its clock cleared instead, and an
    unknown presence (bot not ready) skips the night. Returns the purged guild ids."""
    from clashcontrol.cache_manager import CACHE

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=GUILD_ABSENCE_PURGE_DAYS)
    purged: List[str] = []
    for gid, rec in sorted(CACHE.guild_absences.items()):
        try:
            since = datetime.fromisoformat(rec["absent_since"])
        except (KeyError, TypeError, ValueError):
            logging.warning(f"[GUILD-PURGE] Guild {gid}: unreadable absent_since {rec!r} — skipped")
            continue
        if since > cutoff:
            continue
        presence = bot_guild_presence(gid)
        if presence is True:
            await CACHE.clear_guild_absent(gid)
            logging.info(f"[GUILD-PURGE] Bot is back in guild {gid} — absence cleared, nothing deleted")
            continue
        if presence is None or expected_absence(gid):
            continue
        name = rec.get("guild_name") or "?"
        try:
            res = await purge_guild_data(gid)
        except Exception as e:
            logging.error(f"[GUILD-PURGE] Failed for guild {name} ({gid}), retried next night: {e}", exc_info=True)
            continue
        purged.append(gid)
        logging.warning(
            f"[GUILD-PURGE] Deleted the data of guild {name} ({gid}), absent since "
            f"{rec['absent_since']} (> {GUILD_ABSENCE_PURGE_DAYS} days): {res['cwl_events']} CWL "
            f"season(s), {res['subscription_channels']} subscription channel(s), "
            f"{res['leaderboard_messages']} tracked leaderboard message(s), families deleted "
            f"{res['families_deleted'] or 'none'}, kept (used by another guild) "
            f"{res['families_kept'] or 'none'}, rows {res['rows']}"
        )
    return purged
