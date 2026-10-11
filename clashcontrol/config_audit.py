"""Server configuration audit — the checklist behind ``/setup`` (tracker #0151).

One registry of audit steps (``STEPS``) in the order an admin sets ClashControl up. Each step has
a cache-only ``check`` that returns a ``StepResult`` (status + findings) and a ``detail`` renderer
for the step's own page. ``/setup`` shows the checklist and the detail pages
(``clashcontrol/ui_config_audit.py``); the guided setup and the tutorials of tracker #0121 are
planned to walk the same registry, so a step is defined once and every surface agrees on whether
it is done. Plan: plans/tracker-0151-setup-config-audit.md.

Checks never call an API (same rule as ``bot_permissions.audit_guild``): they read the bot's own
cached guild state and CACHE. A step whose check is not implemented yet reports ``PENDING``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

import discord

from clashcontrol import bot_permissions as bp


class AuditStatus(str, Enum):
    """Status of one audit step (plan §2)."""
    OK = "ok"                  # configured and workable
    BROKEN = "broken"          # enabled/configured but cannot work
    INCOMPLETE = "incomplete"  # core item not (fully) set up
    OFF = "off"                # optional feature not in use — no judgement
    BLOCKED = "blocked"        # a prerequisite step is not set up
    INFO = "info"              # nothing to check, or nothing can be said yet
    PENDING = "pending"        # check not implemented yet


STATUS_ICON: Dict[AuditStatus, str] = {
    AuditStatus.OK: "✅",
    AuditStatus.BROKEN: "❌",
    AuditStatus.INCOMPLETE: "⚠️",
    AuditStatus.OFF: "⏸️",
    AuditStatus.BLOCKED: "🔒",
    AuditStatus.INFO: "ℹ️",
    AuditStatus.PENDING: "⚪",
}

#: A dependent step shows BLOCKED while a prerequisite has one of these. A BROKEN prerequisite
#: (e.g. one clan with a private war log) does not block everything after it.
_BLOCKING = (AuditStatus.INCOMPLETE, AuditStatus.BLOCKED)


@dataclass(frozen=True)
class Finding:
    """One machine-readable observation of a check (the guided setup / tutorials count and
    branch on these; the detail page renders its own text)."""
    status: AuditStatus
    code: str                                   # e.g. "missing_permission", "no_administrator"
    params: Tuple[Tuple[str, Any], ...] = ()


@dataclass
class StepResult:
    status: AuditStatus
    summary_key: str
    summary_params: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    # Check-specific data the detail renderer needs (kept out of Finding to keep that small).
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StepDetail:
    """The rendered detail page of one step."""
    sections: List[Tuple[str, str]]             # (heading, body); heading "" = no heading
    links: List[Tuple[str, str]] = field(default_factory=list)   # (button label, url)


CheckFn = Callable[[discord.Guild], StepResult]
DetailFn = Callable[[discord.Guild, StepResult, Optional[int], Optional[str]], StepDetail]


@dataclass(frozen=True)
class AuditStep:
    id: str                                     # stable — will be stored by the guided setup
    order: int                                  # 1-based display number
    audience: Literal["admin", "user", "both"]
    requires: Tuple[str, ...] = ()
    check: Optional[CheckFn] = None
    detail: Optional[DetailFn] = None
    #: bot_permissions feature keys whose channel/role problems this step reports. Step 1 lists
    #: only the problems of features no implemented step owns, so one problem is red once.
    permission_features: Tuple[str, ...] = ()

    @property
    def title_key(self) -> str:
        return f"config_audit.step.{self.id}.title"

    @property
    def desc_key(self) -> str:
        return f"config_audit.step.{self.id}.desc"


# ---------------------------------------------------------------------------------------------
# Step 1: bot permissions
# ---------------------------------------------------------------------------------------------

def check_bot_permissions(guild: discord.Guild) -> StepResult:
    """Server-level install permissions, Administrator, and the permission problems of the
    automations this server has configured (``bot_permissions.audit_guild``)."""
    me = bp._me(guild)  # pyright: ignore[reportPrivateUsage]
    missing = bp.missing_install_permissions(guild)
    if me is None or missing is None:
        return StepResult(AuditStatus.INFO, "config_audit.bot_permissions.summary_not_ready")

    admin = bool(me.guild_permissions.administrator)
    owned_elsewhere = {f for s in STEPS if s.check is not None for f in s.permission_features}
    issues = [i for i in _permission_issues(guild) if i.feature not in owned_elsewhere]

    findings: List[Finding] = []
    if not admin:
        findings.append(Finding(AuditStatus.INCOMPLETE, "no_administrator"))
    findings += [Finding(AuditStatus.BROKEN, "missing_permission", (("perm", p),)) for p in missing]
    findings += [Finding(AuditStatus.BROKEN, "feature_issue", (("kind", i.kind), ("feature", i.feature)))
                 for i in issues]

    data: Dict[str, Any] = {"administrator": admin, "missing": missing, "issues": issues}
    if missing or issues:
        return StepResult(AuditStatus.BROKEN, "config_audit.bot_permissions.summary_problems",
                          {"count": len(missing) + len(issues)}, findings, data)
    if not admin:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.bot_permissions.summary_no_admin",
                          findings=findings, data=data)
    return StepResult(AuditStatus.OK, "config_audit.bot_permissions.summary_ok", findings=findings, data=data)


def _permission_issues(guild: discord.Guild, features: Optional[Set[str]] = None) -> List[bp.PermissionIssue]:
    """``bot_permissions.audit_guild`` for the active automations (``features`` None = all);
    never raises."""
    try:
        return bp.audit_guild(guild, features=features)
    except Exception as e:
        logging.warning(f"[SETUP] Permission audit failed for guild {guild.id}: {e}")
        return []


def _join_limited(lines: List[str], limit: int, more_line: Callable[[int], str]) -> str:
    """Join ``lines`` with newlines, stopping before ``limit`` characters with a "… and N more"."""
    out: List[str] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 40:
            out.append(more_line(len(lines) - i))
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def detail_bot_permissions(
    guild: discord.Guild, result: StepResult, guild_id: Optional[int], user_id: Optional[str],
) -> StepDetail:
    """Detail page of step 1: Administrator, each missing permission with why it is needed,
    problems of configured features, the bot's role position, and how to fix."""
    from clashcontrol.i18n import t

    def tr(key: str, **kw: Any) -> str:
        return t(key, guild_id=guild_id, user_id=user_id, **kw)

    def perm_name(p: str) -> str:
        return tr(f"permissions.perm.{p}")

    if result.status == AuditStatus.INFO:
        return StepDetail([("", tr("config_audit.bot_permissions.not_ready"))])

    admin: bool = result.data.get("administrator", False)
    missing: Tuple[str, ...] = result.data.get("missing", ())
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    sections: List[Tuple[str, str]] = []

    # Administrator
    if admin:
        sections.append((f"✅ {perm_name('administrator')}", tr("config_audit.bot_permissions.admin_granted")))
    else:
        sections.append((f"⚠️ {perm_name('administrator')}", tr("config_audit.bot_permissions.admin_missing")))

    # The other install permissions (only meaningful without Administrator)
    if not admin:
        if missing:
            lines = [f"❌ **{perm_name(p)}** — {tr(f'permissions.why.{p}')}" for p in missing]
            body = _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))
            sections.append((tr("config_audit.bot_permissions.missing_title", count=len(missing)), body))
        else:
            sections.append(("", tr("config_audit.bot_permissions.all_present")))

    # Problems of configured features (channel overrides, role hierarchy, deleted targets)
    if issues:
        lines = ["• " + bp.format_issue(i, guild_id=guild_id, user_id=user_id) for i in issues]
        body = _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))
        sections.append((tr("config_audit.bot_permissions.issues_title", count=len(issues)), body))

    # The bot's role position — always relevant for role automations
    me = bp._me(guild)  # pyright: ignore[reportPrivateUsage]
    if me is not None:
        sections.append((
            tr("config_audit.bot_permissions.role_title"),
            tr("config_audit.bot_permissions.role_position", role=me.top_role.mention),
        ))

    links: List[Tuple[str, str]] = []
    if missing or not admin:
        sections.append((
            tr("config_audit.bot_permissions.fix_title"),
            tr("config_audit.bot_permissions.fix_text", button=tr("config_audit.bot_permissions.button_reauthorize")),
        ))
        try:
            import QBcore
            from clashcontrol.constants import bot_install_url
            links.append((
                tr("config_audit.bot_permissions.button_reauthorize"),
                bot_install_url(QBcore.bot.application_id, guild.id, bp.install_permissions().value),
            ))
        except Exception as e:  # never let a missing app id break the page
            logging.info(f"[SETUP] No re-authorize link for guild {guild.id}: {e}")
    return StepDetail(sections, links)


# ---------------------------------------------------------------------------------------------
# Shared helpers for the configuration steps (Phase 2+)
# ---------------------------------------------------------------------------------------------

Tr = Callable[..., str]


def _translator(guild_id: Optional[int], user_id: Optional[str]) -> Tr:
    from clashcontrol.i18n import t

    def tr(key: str, **kw: Any) -> str:
        return t(key, guild_id=guild_id, user_id=user_id, **kw)
    return tr


def _config(guild: discord.Guild) -> Dict[str, Any]:
    from clashcontrol.cache_manager import CACHE
    return CACHE.server_config.get(str(guild.id), {}) or {}


def _channel_exists(guild: discord.Guild, channel_id: Any) -> bool:
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return False
    getter = getattr(guild, "get_channel_or_thread", None) or guild.get_channel
    return getter(cid) is not None


def _channel_name(guild: discord.Guild, channel_id: Any) -> str:
    """``#name`` for text that can't render mentions (select descriptions, overview summaries)."""
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return "#?"
    getter = getattr(guild, "get_channel_or_thread", None) or guild.get_channel
    channel = getter(cid)
    return f"#{getattr(channel, 'name', '?')}"


def _clan_name(tag: str) -> str:
    from clashcontrol.cache_manager import CACHE
    return CACHE.get_clan_name(tag, tag)  # tag as fallback, never "Unknown" (Pitfall 39)


def _ui_path(tr: Tr, *label_keys: str) -> str:
    """'/clan management → ⚙️ Basic Configuration → …' from the real (translated) labels, so the
    hint can never drift from what the admin sees."""
    from clashcontrol.QBdiscocmdshelper import command_mention
    return " → ".join([command_mention("clan management"), *(tr(k) for k in label_keys)])


def _where_section(tr: Tr, *paths: str) -> Tuple[str, str]:
    return (tr("config_audit.where_title"), "\n".join(f"• {p}" for p in paths))


def _issues_section(tr: Tr, issues: List[bp.PermissionIssue], guild_id: Optional[int],
                    user_id: Optional[str]) -> Tuple[str, str]:
    lines = ["• " + bp.format_issue(i, guild_id=guild_id, user_id=user_id) for i in issues]
    body = _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))
    return (tr("config_audit.permission_issues_title", count=len(issues)), body)


_BASIC = "ui_components.clan_management.mode_config_label"


# ---------------------------------------------------------------------------------------------
# Step 2: language and timezone
# ---------------------------------------------------------------------------------------------

def check_basics(guild: discord.Guild) -> StepResult:
    """Always OK: both have working defaults (Q-3: ✅ + hint, defaults can't be told apart from
    a deliberate choice)."""
    from clashcontrol.i18n import get_language_display_name
    config = _config(guild)
    language = config.get("language") or "en"
    timezone = config.get("timezone_name") or "UTC"
    findings: List[Finding] = []
    if language == "en":
        findings.append(Finding(AuditStatus.INFO, "default_language"))
    if timezone == "UTC":
        findings.append(Finding(AuditStatus.INFO, "default_timezone"))
    return StepResult(AuditStatus.OK, "config_audit.basics.summary",
                      {"lang_name": get_language_display_name(language), "timezone": timezone},
                      findings, {"language": language, "timezone": timezone})


def detail_basics(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                  user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    codes = {f.code for f in result.findings}
    language = tr("config_audit.basics.language_text", lang_name=result.summary_params["lang_name"])
    if "default_language" in codes:
        language += "\n" + tr("config_audit.basics.language_default_hint")
    timezone = tr("config_audit.basics.timezone_text", timezone=result.data["timezone"])
    if "default_timezone" in codes:
        timezone += "\n" + tr("config_audit.basics.timezone_utc_hint")
    return StepDetail([
        (tr("config_audit.basics.language_title"), language),
        (tr("config_audit.basics.timezone_title"), timezone),
        _where_section(
            tr,
            _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_language"),
            _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_timezone"),
        ),
    ])


# ---------------------------------------------------------------------------------------------
# Step 3: member clans and families
# ---------------------------------------------------------------------------------------------

def check_clans(guild: discord.Guild) -> StepResult:
    """Member clans (individual + via families). Per clan: deleted by Supercell, or a private war
    log (no war tracking, war reminders or war leaderboards for that clan) → BROKEN."""
    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags

    config = _config(guild)
    tags = resolve_guild_member_clan_tags(guild.id)
    families = [f for f in config.get("member_families", []) if f in CACHE.clan_families]
    guests = sorted(CACHE.get_guild_guest_clan_tags(guild.id))
    data: Dict[str, Any] = {"families": families, "guests": guests, "clans": []}
    if not tags:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.clans.summary_none", data=data)

    findings: List[Finding] = []
    for tag in tags:
        entry = CACHE.clan_name_cache.get(tag)
        if not isinstance(entry, dict):
            state = "no_data"
        elif entry.get("is_deleted"):
            state = "deleted"
        elif entry.get("warlog_is_public", True) is False:
            state = "warlog_private"
        else:
            state = "ok"
        data["clans"].append((tag, state))
        if state in ("deleted", "warlog_private"):
            findings.append(Finding(AuditStatus.BROKEN, state, (("clan_tag", tag),)))
    params = {"clans": len(tags), "families": len(families)}
    if findings:
        return StepResult(AuditStatus.BROKEN, "config_audit.clans.summary_problems",
                          {**params, "count": len(findings)}, findings, data)
    return StepResult(AuditStatus.OK, "config_audit.clans.summary_ok", params, findings, data)


def detail_clans(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                 user_id: Optional[str]) -> StepDetail:
    from clashcontrol.cache_manager import CACHE
    tr = _translator(guild_id, user_id)
    sections: List[Tuple[str, str]] = []
    clans: List[Tuple[str, str]] = result.data.get("clans", [])
    if not clans:
        sections.append(("", tr("config_audit.clans.none_text")))
    else:
        icon = {"ok": "✅", "deleted": "❌", "warlog_private": "❌", "no_data": "ℹ️"}
        lines = []
        for tag, state in clans:
            line = f"{icon[state]} **{_clan_name(tag)}** `{tag}`"
            if state != "ok":
                line += " — " + tr(f"config_audit.clans.state_{state}")
            lines.append(line)
        sections.append((tr("config_audit.clans.list_title", count=len(clans)),
                         _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))))
    families = result.data.get("families", [])
    if families:
        lines = [f"🏰 **{CACHE.clan_families[f].get('name') or f}** — "
                 + tr("config_audit.clans.family_clans", count=len(CACHE.clan_families[f].get('clans', [])))
                 for f in families if f in CACHE.clan_families]
        sections.append((tr("config_audit.clans.families_title"), "\n".join(lines)))
    guests = result.data.get("guests", [])
    if guests:
        sections.append(("", tr("config_audit.clans.guests", count=len(guests))))
    if any(state == "warlog_private" for _, state in clans):
        sections.append((tr("config_audit.clans.warlog_fix_title"), tr("config_audit.clans.warlog_fix")))
    sections.append(_where_section(
        tr,
        _ui_path(tr, _BASIC, "ui_components.clan_management.button_member_clans"),
        _ui_path(tr, "ui_components.clan_management.mode_families_label"),
    ))
    return StepDetail(sections)


# ---------------------------------------------------------------------------------------------
# Step 4: welcome message
# ---------------------------------------------------------------------------------------------

def _welcome_clan_tags(config: Dict[str, Any]) -> List[str]:
    """The clans the welcome message links to (selected clans + clans of selected families)."""
    from clashcontrol.cache_manager import CACHE
    tags: List[str] = []
    for family_id in config.get("welcome_family_tags", []):
        for tag in CACHE.clan_families.get(family_id, {}).get("clans", []):
            if tag not in tags:
                tags.append(tag)
    for tag in config.get("welcome_clan_tags", []):
        if tag not in tags:
            tags.append(tag)
    return tags


def check_welcome(guild: discord.Guild) -> StepResult:
    config = _config(guild)
    if not config.get("welcome_message_enabled"):
        return StepResult(AuditStatus.OFF, "config_audit.welcome.summary_off")
    issues = _permission_issues(guild, {"welcome"})
    mode = config.get("welcome_message_mode") or "clan_link"
    findings = [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues]
    apply_channel = config.get("welcome_apply_channel_id")
    if mode == "apply_channel":
        if not apply_channel:
            findings.append(Finding(AuditStatus.INCOMPLETE, "no_apply_channel"))
        elif not _channel_exists(guild, apply_channel):
            findings.append(Finding(AuditStatus.BROKEN, "apply_channel_missing"))
    elif not _welcome_clan_tags(config):
        findings.append(Finding(AuditStatus.INFO, "no_clan_links"))
    data = {"issues": issues, "mode": mode}
    if any(f.status == AuditStatus.BROKEN for f in findings):
        return StepResult(AuditStatus.BROKEN, "config_audit.welcome.summary_problems",
                          {"count": sum(f.status == AuditStatus.BROKEN for f in findings)}, findings, data)
    if any(f.status == AuditStatus.INCOMPLETE for f in findings):
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.welcome.summary_no_apply_channel", findings=findings, data=data)
    system_channel = guild.system_channel
    return StepResult(AuditStatus.OK, "config_audit.welcome.summary_ok",
                      {"channel": f"#{getattr(system_channel, 'name', '?')}"}, findings, data)


def detail_welcome(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                   user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    where = _where_section(
        tr,
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_activate_welcome_message"),
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_configure_welcome_message"),
    )
    if result.status == AuditStatus.OFF:
        return StepDetail([("", tr("config_audit.welcome.off_text")), where])
    sections: List[Tuple[str, str]] = []
    system_channel = guild.system_channel
    if system_channel is not None:
        sections.append((tr("config_audit.welcome.posts_title"),
                         tr("config_audit.welcome.posts_in", channel=system_channel.mention)))
    if result.data.get("mode") == "apply_channel":
        apply_channel = config.get("welcome_apply_channel_id")
        if not apply_channel:
            text = tr("config_audit.welcome.mode_apply_unset")
        elif not _channel_exists(guild, apply_channel):
            text = tr("config_audit.welcome.mode_apply_missing")
        else:
            text = tr("config_audit.welcome.mode_apply", channel=f"<#{apply_channel}>")
    else:
        tags = _welcome_clan_tags(config)
        text = (tr("config_audit.welcome.mode_clan_link", clans=", ".join(_clan_name(t) for t in tags))
                if tags else tr("config_audit.welcome.mode_clan_link_none"))
    sections.append((tr("config_audit.welcome.content_title"), text))
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    if issues:
        sections.append(_issues_section(tr, issues, guild_id, user_id))
    if not config.get("registration_message_enabled"):
        sections.append(("", tr("config_audit.welcome.registration_hint")))
    sections.append(where)
    return StepDetail(sections)


# ---------------------------------------------------------------------------------------------
# Steps 7 and 8: war and raid reminders posted into a channel
# ---------------------------------------------------------------------------------------------

def check_channel_war_notifications(guild: discord.Guild) -> StepResult:
    from clashcontrol.ui_clan_management import format_war_threshold_label
    config = _config(guild)
    if not config.get("channel_war_notifications_enabled"):
        return StepResult(AuditStatus.OFF, "config_audit.channel_war.summary_off")
    channel_id = config.get("war_notification_channel_id")
    if not channel_id:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.channel_war.summary_no_channel",
                          findings=[Finding(AuditStatus.INCOMPLETE, "no_channel")])
    issues = _permission_issues(guild, {"war_notifications"})
    data = {"issues": issues, "channel_id": channel_id}
    if issues:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems", {"count": len(issues)},
                          [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues], data)
    hours = float(config.get("war_notification_threshold_hours", 2.0) or 2.0)
    return StepResult(AuditStatus.OK, "config_audit.channel_war.summary_ok",
                      {"channel": _channel_name(guild, channel_id),
                       "time": format_war_threshold_label(hours, guild.id)}, data=data)


def detail_channel_war_notifications(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                                     user_id: Optional[str]) -> StepDetail:
    from clashcontrol.ui_clan_management import format_war_threshold_label
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    sections: List[Tuple[str, str]] = [("", tr("config_audit.channel_war.what"))]
    if result.status != AuditStatus.OFF and result.data.get("channel_id"):
        hours = float(config.get("war_notification_threshold_hours", 2.0) or 2.0)
        sections.append((tr("config_audit.settings_title"), tr(
            "config_audit.channel_war.settings", channel=f"<#{result.data['channel_id']}>",
            time=format_war_threshold_label(hours, guild_id, user_id))))
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    if issues:
        sections.append(_issues_section(tr, issues, guild_id, user_id))
    sections.append(_where_section(
        tr,
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_channels"),
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_notification_time"),
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_activate_channel_notifications"),
    ))
    return StepDetail(sections)


def _raid_channel(config: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """(channel id, is the war-channel fallback) — raid reminders use the war channel when no
    raid channel is set (#0115), same as bot_permissions._channel_feature_targets."""
    raid = config.get("raid_notification_channel_id")
    if raid:
        return raid, False
    war = config.get("war_notification_channel_id")
    return (war, True) if war else (None, False)


def _raid_settings_params(config: Dict[str, Any], tr: Tr) -> Dict[str, str]:
    return {
        "time": tr("ui_components.basic_config.raid_threshold_hours",
                   hours=int(config.get("raid_notification_threshold_hours", 24) or 24)),
        "scope": tr(f"ui_components.basic_config.raid_scope_{config.get('raid_notification_scope') or 'not_attacked'}"),
    }


def check_raid_reminders(guild: discord.Guild) -> StepResult:
    config = _config(guild)
    if not config.get("channel_raid_notifications_enabled"):
        return StepResult(AuditStatus.OFF, "config_audit.raid.summary_off")
    channel_id, _fallback = _raid_channel(config)
    if not channel_id:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.raid.summary_no_channel",
                          findings=[Finding(AuditStatus.INCOMPLETE, "no_channel")])
    issues = _permission_issues(guild, {"raid_notifications"})
    data = {"issues": issues, "channel_id": channel_id}
    if issues:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems", {"count": len(issues)},
                          [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues], data)
    return StepResult(AuditStatus.OK, "config_audit.raid.summary_ok",
                      {"channel": _channel_name(guild, channel_id),
                       **_raid_settings_params(config, _translator(guild.id, None))}, data=data)


def detail_raid_reminders(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                          user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    sections: List[Tuple[str, str]] = [("", tr("config_audit.raid.what"))]
    channel_id, fallback = _raid_channel(config)
    if result.status != AuditStatus.OFF and channel_id:
        text = tr("config_audit.raid.settings", channel=f"<#{channel_id}>", **_raid_settings_params(config, tr))
        if fallback:
            text += "\n" + tr("config_audit.raid.fallback_hint")
        sections.append((tr("config_audit.settings_title"), text))
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    if issues:
        sections.append(_issues_section(tr, issues, guild_id, user_id))
    sections.append(_where_section(
        tr,
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_channels"),
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_select_notification_time"),
        _ui_path(tr, _BASIC, "ui_components.basic_config.button_activate_raid_notifications"),
    ))
    return StepDetail(sections)


# ---------------------------------------------------------------------------------------------
# Step 13: leaderboards
# ---------------------------------------------------------------------------------------------

def _leaderboard_subscriptions(guild: discord.Guild) -> Dict[str, List[Dict[str, Any]]]:
    """channel id -> leaderboard subscriptions (the registration-message tracker entries that
    share the table are not leaderboards)."""
    from clashcontrol.cache_manager import CACHE
    out: Dict[str, List[Dict[str, Any]]] = {}
    for channel_id, subs in (CACHE.subscriptions.get(str(guild.id)) or {}).items():
        real = [s for s in subs or [] if s and s.get("clan_tag") and s.get("subscription_type") != "playerregistration"]
        if real:
            out[str(channel_id)] = real
    return out


def check_leaderboards(guild: discord.Guild) -> StepResult:
    from clashcontrol.cache_manager import CACHE
    subs = _leaderboard_subscriptions(guild)
    if not subs:
        return StepResult(AuditStatus.OFF, "config_audit.leaderboards.summary_off")
    issues = [i for i in _permission_issues(guild, {"leaderboard"}) if str(i.target_id) in subs]
    findings = [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues]
    deleted = sorted({s["clan_tag"] for lst in subs.values() for s in lst
                      if isinstance(CACHE.clan_name_cache.get(s["clan_tag"]), dict)
                      and CACHE.clan_name_cache[s["clan_tag"]].get("is_deleted")})
    findings += [Finding(AuditStatus.BROKEN, "clan_deleted", (("clan_tag", t),)) for t in deleted]
    data = {"issues": issues, "deleted": deleted, "subs": subs}
    count = sum(len(v) for v in subs.values())
    if findings:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems",
                          {"count": len(findings)}, findings, data)
    return StepResult(AuditStatus.OK, "config_audit.leaderboards.summary_ok",
                      {"count": count, "channels": len(subs)}, findings, data)


def detail_leaderboards(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                        user_id: Optional[str]) -> StepDetail:
    from clashcontrol.QBdiscocmdshelper import command_mention
    tr = _translator(guild_id, user_id)
    sections: List[Tuple[str, str]] = []
    subs: Dict[str, List[Dict[str, Any]]] = result.data.get("subs", {})
    if not subs:
        sections.append(("", tr("config_audit.leaderboards.off_text")))
    else:
        lines = []
        for channel_id, lst in sorted(subs.items()):
            entries = ", ".join(f"{_clan_name(s['clan_tag'])} ({s.get('mode') or '?'})" for s in lst)
            lines.append(f"<#{channel_id}>: {entries}")
        sections.append((tr("config_audit.leaderboards.list_title", count=len(subs)),
                         _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))))
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    if issues:
        sections.append(_issues_section(tr, issues, guild_id, user_id))
    deleted: List[str] = result.data.get("deleted", [])
    if deleted:
        sections.append(("", tr("config_audit.leaderboards.deleted",
                                clans=", ".join(f"{_clan_name(t)} `{t}`" for t in deleted),
                                unsubscribe=command_mention("unsubscribe"))))
    sections.append((tr("config_audit.leaderboards.how_title"), tr(
        "config_audit.leaderboards.how", subscribe=command_mention("subscribe"),
        unsubscribe=command_mention("unsubscribe"), subscriptions=command_mention("subscriptions"))))
    return StepDetail(sections)


# ---------------------------------------------------------------------------------------------
# Registry (plan §3). Every step is listed so the checklist shows the whole structure; a step
# whose check comes in a later phase reports PENDING until then.
# ---------------------------------------------------------------------------------------------

STEPS: Tuple[AuditStep, ...] = (
    AuditStep("bot_permissions", 1, "admin", check=check_bot_permissions, detail=detail_bot_permissions),
    AuditStep("basics", 2, "admin", check=check_basics, detail=detail_basics),
    AuditStep("clans", 3, "admin", check=check_clans, detail=detail_clans),
    AuditStep("welcome", 4, "admin", check=check_welcome, detail=detail_welcome,
              permission_features=("welcome",)),
    AuditStep("registration", 5, "both", requires=("clans",)),
    AuditStep("war_notifications", 6, "both", requires=("clans",)),
    AuditStep("channel_war_notifications", 7, "admin", requires=("clans",),
              check=check_channel_war_notifications, detail=detail_channel_war_notifications,
              permission_features=("war_notifications",)),
    AuditStep("raid_reminders", 8, "admin", requires=("clans",),
              check=check_raid_reminders, detail=detail_raid_reminders,
              permission_features=("raid_notifications",)),
    AuditStep("roles", 9, "admin", requires=("clans",)),
    AuditStep("cwl_settings", 10, "admin", requires=("clans",)),
    AuditStep("cwl_player_hub", 11, "both", requires=("cwl_settings",)),
    AuditStep("cwl_season", 12, "admin", requires=("cwl_settings",)),
    # No prerequisite: any clan can be subscribed, member clan or not.
    AuditStep("leaderboards", 13, "admin", check=check_leaderboards, detail=detail_leaderboards,
              permission_features=("leaderboard",)),
)

STEPS_BY_ID: Dict[str, AuditStep] = {s.id: s for s in STEPS}


def run_step(step: AuditStep, guild: discord.Guild) -> StepResult:
    """Run one step's check. Never raises: a failing check shows as INFO "could not be checked"
    instead of breaking the whole checklist."""
    if step.check is None:
        return StepResult(AuditStatus.PENDING, "config_audit.summary_pending")
    try:
        return step.check(guild)
    except Exception as e:
        logging.warning(f"[SETUP] Check '{step.id}' failed for guild {getattr(guild, 'id', '?')}: {e}", exc_info=True)
        return StepResult(AuditStatus.INFO, "config_audit.summary_check_failed")


def run_audit(guild: discord.Guild) -> Dict[str, StepResult]:
    """Every step's result for ``guild``, in registry order, with prerequisites applied: a step
    whose prerequisite is not set up shows BLOCKED (one root cause, one red line)."""
    results: Dict[str, StepResult] = {}
    for step in STEPS:
        result = run_step(step, guild)
        # Not checked yet, or switched off: nothing to block (a disabled feature stays ⏸️).
        if result.status not in (AuditStatus.PENDING, AuditStatus.OFF) and any(
            results.get(req) is not None and results[req].status in _BLOCKING for req in step.requires
        ):
            result = StepResult(AuditStatus.BLOCKED, "config_audit.summary_blocked",
                                {"steps": ", ".join(str(STEPS_BY_ID[r].order) for r in step.requires)},
                                result.findings, result.data)
        results[step.id] = result
    return results
