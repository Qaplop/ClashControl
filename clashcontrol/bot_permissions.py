"""Bot permission checks for every automation that posts messages or manages roles.

Tracker #0150 follow-up (2026-10-10). Two guilds configured automations the bot could not carry
out — channels it could not see (50001) or post in (50013) — and nothing told them: the settings
were saved with a normal success reply and then failed silently every cycle. The default install
requests Administrator, which would bypass every channel override, but installers can untick it,
and from then on the bot depends on per-channel permissions.

This module is the single place that knows what each automation needs:

- ``CHANNEL_FEATURES``: Discord permissions each posting feature uses, derived from what the code
  actually does (embeds, reading history to find/delete its own previous message, ...), so a
  check never warns about a permission the feature does not use.
- Role automations need *Manage Roles*, and every role they assign must sit below the bot's
  highest role and must not be integration-managed — otherwise assignment fails even with the
  permission.

Used by:
- the config-time checks (``warn_missing_permissions``) run whenever a user enables an
  automation, picks a channel for one or links a role, and
- ``/admin Permission Audit`` (``audit_guild`` over every guild the bot is in).

Checks only read the bot's own cached member/channel state — no API calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, cast

import discord

# ---------------------------------------------------------------------------------------------
# What each automation needs
# ---------------------------------------------------------------------------------------------

#: Posting features -> Discord permission attribute names (discord.Permissions). In a thread,
#: ``send_messages`` is checked as ``send_messages_in_threads``.
CHANNEL_FEATURES: Dict[str, Tuple[str, ...]] = {
    # Anchored messages: post, read the channel history (bump check: "is my message still the
    # newest?") and fetch their own previous message to delete it before reposting.
    "registration": ("view_channel", "send_messages", "read_message_history"),
    # The two CWL hubs post an embed.
    "cwl_management": ("view_channel", "send_messages", "embed_links", "read_message_history"),
    "cwl_player_hub": ("view_channel", "send_messages", "embed_links", "read_message_history"),
    # War / raid reminders post content + an embed.
    "war_notifications": ("view_channel", "send_messages", "embed_links"),
    "raid_notifications": ("view_channel", "send_messages", "embed_links"),
    # Leaderboards post text and fetch their previous messages to delete them.
    "leaderboard": ("view_channel", "send_messages", "read_message_history"),
    # Welcome message: plain text into the server's system channel.
    "welcome": ("view_channel", "send_messages"),
    # /clan management posts its interactive embed into the channel it is used in.
    "clan_management": ("view_channel", "send_messages", "embed_links"),
}

#: Role automations (feature keys used in messages).
ROLE_FEATURES: Tuple[str, ...] = ("role_system", "coc_roles", "clan_roles", "cwl_coordinator_role")

# Issue kinds
MISSING_CHANNEL_PERMS = "missing_channel_perms"   # perms: the missing permission names
CHANNEL_NOT_FOUND = "channel_not_found"           # configured channel is not in the guild
NO_SYSTEM_CHANNEL = "no_system_channel"           # welcome message has nowhere to post
MISSING_MANAGE_ROLES = "missing_manage_roles"
ROLE_ABOVE_BOT = "role_above_bot"
ROLE_MANAGED = "role_managed"
ROLE_NOT_FOUND = "role_not_found"


@dataclass(frozen=True)
class PermissionIssue:
    """One thing the bot cannot do for an automation."""
    feature: str
    kind: str
    target_id: Optional[int] = None          # channel or role id
    perms: Tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------------------------
# Primitive checks
# ---------------------------------------------------------------------------------------------

def _me(guild: Any) -> Optional[discord.Member]:
    """The bot's own member in ``guild``. Typed Member by discord.py, but None in practice until
    the guild's member cache is ready — every caller here must tolerate that."""
    return cast(Optional[discord.Member], getattr(guild, "me", None))


def missing_channel_permissions(channel: Any, member: Any, required: Sequence[str]) -> Tuple[str, ...]:
    """Permissions from ``required`` that ``member`` lacks in ``channel``.

    Without *View Channel* every other permission is moot (Discord treats the channel as absent),
    so that case reports only ``view_channel``.
    """
    perms = channel.permissions_for(member)
    is_thread = isinstance(channel, discord.Thread)
    missing: List[str] = []
    for name in required:
        attr = "send_messages_in_threads" if (name == "send_messages" and is_thread) else name
        if not getattr(perms, attr, False):
            missing.append(attr)
    if "view_channel" in missing:
        return ("view_channel",)
    return tuple(missing)


def check_channel_feature(guild: discord.Guild, feature: str, channel_id: Any) -> List[PermissionIssue]:
    """Issues for posting feature ``feature`` in channel ``channel_id`` of ``guild``."""
    required = CHANNEL_FEATURES[feature]
    me = _me(guild)
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return []
    channel = guild.get_channel_or_thread(cid) if hasattr(guild, "get_channel_or_thread") else guild.get_channel(cid)
    if channel is None:
        return [PermissionIssue(feature, CHANNEL_NOT_FOUND, cid)]
    if me is None:
        return []
    missing = missing_channel_permissions(channel, me, required)
    return [PermissionIssue(feature, MISSING_CHANNEL_PERMS, cid, missing)] if missing else []


def check_role_feature(guild: discord.Guild, feature: str, role_ids: Iterable[Any]) -> List[PermissionIssue]:
    """Issues for role automation ``feature`` assigning ``role_ids`` in ``guild``.

    *Manage Roles* is reported once per feature; per role, Discord additionally refuses roles at
    or above the bot's highest role and integration-managed roles (bots, boosts, subscriptions).
    """
    me = _me(guild)
    if me is None:
        return []
    issues: List[PermissionIssue] = []
    if not me.guild_permissions.manage_roles:
        issues.append(PermissionIssue(feature, MISSING_MANAGE_ROLES, perms=("manage_roles",)))
    seen: Set[int] = set()
    for raw in role_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid in seen:
            continue
        seen.add(rid)
        role = guild.get_role(rid)
        if role is None:
            issues.append(PermissionIssue(feature, ROLE_NOT_FOUND, rid))
        elif role.managed or role.is_default():
            issues.append(PermissionIssue(feature, ROLE_MANAGED, rid))
        elif role >= me.top_role:
            issues.append(PermissionIssue(feature, ROLE_ABOVE_BOT, rid))
    return issues


# ---------------------------------------------------------------------------------------------
# Guild audit
# ---------------------------------------------------------------------------------------------

def _channel_feature_targets(config: Dict[str, Any], guild: discord.Guild) -> List[Tuple[str, str, Any]]:
    """(feature, enabled_flag_key or "", channel id) for every channel-based automation."""
    war_ch = config.get("war_notification_channel_id")
    return [
        ("registration", "registration_message_enabled", config.get("registration_channel_id")),
        ("cwl_management", "cwl_management_message_enabled", config.get("cwl_management_channel_id")),
        ("cwl_player_hub", "cwl_player_hub_message_enabled", config.get("cwl_player_hub_channel_id")),
        ("war_notifications", "channel_war_notifications_enabled", war_ch),
        # Raid reminders fall back to the war channel when no raid channel is set (#0115).
        ("raid_notifications", "channel_raid_notifications_enabled",
         config.get("raid_notification_channel_id") or war_ch),
    ]


def _role_feature_targets(config: Dict[str, Any]) -> List[Tuple[str, str, List[Any]]]:
    """(feature, enabled_flag_key or "", role ids) for every role automation."""
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    coc_roles = [config.get(k) for k in (
        "coc_role_member_id", "coc_role_elder_id", "coc_role_coleader_id", "coc_role_leader_id",
    ) if config.get(k)]
    clan_roles = [rid for rid in (config.get("clan_roles") or {}).values() if rid]
    system_roles = [config.get(k) for k in ("newbie_role_id", "member_role_id") if config.get(k)]
    try:
        coordinator_roles: List[Any] = list(cwl_coordinator_role_targets(config).keys())
    except Exception:
        coordinator_roles = []
    return [
        ("role_system", "role_system_enabled", system_roles),
        ("coc_roles", "coc_role_enabled", coc_roles),
        ("clan_roles", "clan_role_enabled", clan_roles),
        # No on/off flag: linking a coordinator role is what activates the sync.
        ("cwl_coordinator_role", "", coordinator_roles),
    ]


def audit_guild(
    guild: discord.Guild,
    *,
    features: Optional[Set[str]] = None,
    include_disabled: bool = False,
) -> List[PermissionIssue]:
    """Every permission issue for the automations configured in ``guild``.

    Args:
        guild: the guild to audit (the bot's own view of it).
        features: restrict to these feature keys (config-time checks pass the one(s) just changed).
        include_disabled: also check automations whose on/off flag is off. Config-time checks for
            a freshly picked channel use this — the admin picked it to use it — while the audit
            reports only what is active.
    """
    from clashcontrol.cache_manager import CACHE

    config = CACHE.server_config.get(str(guild.id), {}) or {}
    wanted = (lambda f: f in features) if features is not None else (lambda f: True)
    issues: List[PermissionIssue] = []

    for feature, flag, channel_id in _channel_feature_targets(config, guild):
        if not wanted(feature) or not channel_id:
            continue
        if flag and not include_disabled and not config.get(flag):
            continue
        issues.extend(check_channel_feature(guild, feature, channel_id))

    # Welcome and auto-created CoC roles have no channel/role setting whose presence shows intent,
    # so when they are off they are only checked if the caller asked for them by name — never as
    # a side effect of include_disabled over every feature.
    def _explicit(feature: str) -> bool:
        return include_disabled and features is not None and feature in features

    if wanted("welcome") and (config.get("welcome_message_enabled") or _explicit("welcome")):
        system_channel = guild.system_channel
        if system_channel is None:
            issues.append(PermissionIssue("welcome", NO_SYSTEM_CHANNEL))
        else:
            issues.extend(check_channel_feature(guild, "welcome", system_channel.id))

    if wanted("leaderboard"):
        for channel_id in sorted((CACHE.subscriptions.get(str(guild.id)) or {}).keys()):
            issues.extend(check_channel_feature(guild, "leaderboard", channel_id))

    for feature, flag, role_ids in _role_feature_targets(config):
        if not wanted(feature):
            continue
        if flag and not include_disabled and not config.get(flag):
            continue
        if not role_ids:
            # coc_roles with no ids yet: the bot creates them itself, which needs Manage Roles
            # too — but only worth saying when that automation is on or was just requested.
            if feature != "coc_roles" or not (config.get(flag) or _explicit(feature)):
                continue
        issues.extend(check_role_feature(guild, feature, role_ids))
    return issues


# ---------------------------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------------------------

def _perm_names(perms: Iterable[str], *, guild_id: Optional[int], user_id: Optional[str]) -> str:
    from clashcontrol.i18n import t
    return ", ".join(
        t(f"permissions.perm.{p}", guild_id=guild_id, user_id=user_id) for p in perms
    )


def format_issue(
    issue: PermissionIssue, *, guild_id: Optional[int], user_id: Optional[str] = None,
    plain_guild: Optional[Any] = None,
) -> str:
    """One bullet line for ``issue`` in the guild's (or user's) language.

    Discord messages get ``<#id>`` / ``<@&id>`` mentions and Markdown. For a plain-text file
    (the audit report) pass ``plain_guild``: channels and roles are then written as
    ``#name (id)`` / ``@name (id)`` resolved from that guild, and the ``**`` markers are dropped.
    """
    from clashcontrol.i18n import t

    feature = t(f"permissions.feature.{issue.feature}", guild_id=guild_id, user_id=user_id)
    if plain_guild is not None and issue.target_id:
        ch = None
        try:
            ch = plain_guild.get_channel_or_thread(issue.target_id)
        except Exception:
            ch = None
        rl = None
        try:
            rl = plain_guild.get_role(issue.target_id)
        except Exception:
            rl = None
        channel = f"#{getattr(ch, 'name', '?')} ({issue.target_id})"
        role = f"@{getattr(rl, 'name', '?')} ({issue.target_id})"
    else:
        channel = f"<#{issue.target_id}>" if issue.target_id else ""
        role = f"<@&{issue.target_id}>" if issue.target_id else ""
    key = f"permissions.issue.{issue.kind}"
    text = t(
        key, guild_id=guild_id, user_id=user_id, feature=feature, channel=channel, role=role,
        perms=_perm_names(issue.perms, guild_id=guild_id, user_id=user_id),
    )
    return text.replace("**", "") if plain_guild is not None else text


def format_issues_message(
    issues: Sequence[PermissionIssue], *, guild_id: Optional[int], user_id: Optional[str] = None,
    limit_chars: int = 1900,
) -> str:
    """The full warning: header, one line per issue (truncated to fit a message), fix hint."""
    from clashcontrol.i18n import t

    header = t("permissions.warning_header", guild_id=guild_id, user_id=user_id)
    footer = t("permissions.warning_footer", guild_id=guild_id, user_id=user_id)
    lines: List[str] = []
    budget = limit_chars - len(header) - len(footer) - 40
    for i, issue in enumerate(issues):
        line = "• " + format_issue(issue, guild_id=guild_id, user_id=user_id)
        if budget - len(line) - 1 < 0:
            lines.append(t("permissions.warning_more", guild_id=guild_id, user_id=user_id, count=len(issues) - i))
            break
        lines.append(line)
        budget -= len(line) + 1
    return "\n".join([header, *lines, footer])


# ---------------------------------------------------------------------------------------------
# Config-time warning
# ---------------------------------------------------------------------------------------------

async def warn_missing_permissions(
    interaction: discord.Interaction,
    *,
    features: Set[str],
    include_disabled: bool = True,
    only_channel_id: Optional[int] = None,
) -> List[PermissionIssue]:
    """Check ``features`` for the interaction's guild and, if anything is missing, tell the user
    with an ephemeral message. The setting itself is never blocked — it is saved either way and
    starts working as soon as an admin grants what is missing.

    ``only_channel_id`` limits the report to that one channel (``/subscribe`` subscribes the
    channel it runs in; other leaderboard channels are not what the user just changed).

    Call it right AFTER the change is persisted (so the audit sees the new config). Works whether
    or not the interaction has been responded to yet. Never raises: a failed check must never
    break the config flow that called it.
    """
    guild = interaction.guild
    if guild is None:
        return []
    try:
        issues = audit_guild(guild, features=features, include_disabled=include_disabled)
        if only_channel_id is not None:
            issues = [i for i in issues if i.target_id == int(only_channel_id)]
    except Exception as e:
        logging.warning(f"[BOT-PERMS] Permission check failed for guild {guild.id} ({features}): {e}")
        return []
    if not issues:
        return []
    logging.info(
        f"[BOT-PERMS] Guild {guild.id} ({guild.name}): {len(issues)} permission issue(s) for "
        f"{sorted(features)} — user {interaction.user.id} informed"
    )
    text = format_issues_message(issues, guild_id=guild.id, user_id=str(interaction.user.id))
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception as e:
        logging.warning(f"[BOT-PERMS] Could not send permission warning in guild {guild.id}: {e}")
    return issues


# ---------------------------------------------------------------------------------------------
# /admin Permission Audit
# ---------------------------------------------------------------------------------------------

def build_audit_report(guilds: Iterable[discord.Guild]) -> Tuple[str, int, int]:
    """Plain-text report over ``guilds`` for ``/admin Permission Audit`` (bot admins, English).

    Returns ``(report, guilds_with_issues, total_issues)``.
    """
    from clashcontrol.cache_manager import CACHE

    out: List[str] = []
    guilds_with_issues = 0
    total = 0
    for guild in sorted(guilds, key=lambda g: g.name.lower()):
        me = _me(guild)
        gp = me.guild_permissions if me is not None else None
        admin = bool(gp and gp.administrator)
        configured = str(guild.id) in CACHE.server_config
        try:
            issues = audit_guild(guild)
        except Exception as e:
            out.append(f"\n## {guild.name} ({guild.id}) — audit failed: {e}")
            continue
        total += len(issues)
        if issues:
            guilds_with_issues += 1
        missing_server_level = []
        if gp is not None and not admin:
            for p in sorted({p for perms in CHANNEL_FEATURES.values() for p in perms} | {"manage_roles"}):
                if not getattr(gp, p, False):
                    missing_server_level.append(p)
        status = "OK" if not issues else f"{len(issues)} issue(s)"
        out.append(f"\n## {guild.name} ({guild.id}) — {status}")
        out.append(
            f"   owner: {guild.owner_id}  |  Administrator: {'yes' if admin else 'NO'}"
            f"  |  bot's top role: {me.top_role.name if me is not None else '?'} "
            f"(position {me.top_role.position if me is not None else '?'})"
            f"  |  configured: {'yes' if configured else 'no'}"
        )
        if missing_server_level:
            out.append(
                f"   missing at server level: "
                f"{_perm_names(missing_server_level, guild_id=None, user_id=None)}"
            )
        for issue in issues:
            out.append("   - " + format_issue(issue, guild_id=None, plain_guild=guild))

    # (a) Configured guilds the bot is no longer in: their settings are kept but paused
    # (clashcontrol/guild_presence.py). Listed so a stale config is visible here, not only as a
    # log line. The bot cannot see these guilds any more, so there is no name or owner to show.
    from clashcontrol.guild_presence import absent_configured_guilds, configured_guild_summary
    present_ids = {str(g.id) for g in guilds}
    absent = [g for g in absent_configured_guilds() if g not in present_ids]
    if absent:
        out.append(f"\n## Configured servers the bot is no longer in ({len(absent)}) — config kept, automations paused")
        for gid in absent:
            s = configured_guild_summary(gid)
            out.append(
                f"   - {gid}: enabled: {', '.join(s['enabled']) or 'nothing'}; "
                f"{s['subscription_channels']} subscription channel(s)"
            )
    summary = (
        f"{guilds_with_issues} guild(s) with issues, {total} issue(s) in total; "
        f"{len(absent)} configured server(s) the bot is no longer in."
    )
    report = "ClashControl permission audit\n" + summary + "\n" + "\n".join(out)
    return report, guilds_with_issues, total
