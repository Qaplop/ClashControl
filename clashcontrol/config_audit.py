"""Server configuration audit — the checklist behind ``/setup`` (tracker #0151).

One registry of audit steps (``STEPS``) in the order an admin sets ClashControl up. Each step has
a cache-only ``check`` that returns a ``StepResult`` (status + findings) and a ``detail`` renderer
for the step's own page. ``/setup`` shows the checklist and the detail pages
(``clashcontrol/ui_config_audit.py``); the guided setup and the tutorials of tracker #0121 are
planned to walk the same registry, so a step is defined once and every surface agrees on whether
it is done. Plan: plans/implemented/tracker-0151-setup-config-audit.md.

Checks never call an API (same rule as ``bot_permissions.audit_guild``): they read the bot's own
cached guild state and CACHE.
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


STATUS_ICON: Dict[AuditStatus, str] = {
    AuditStatus.OK: "✅",
    AuditStatus.BROKEN: "❌",
    AuditStatus.INCOMPLETE: "⚠️",
    AuditStatus.OFF: "⏸️",
    AuditStatus.BLOCKED: "🔒",
    AuditStatus.INFO: "ℹ️",
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
    #: Plain values, or a ``LazyText`` for any text that needs translating: a check runs without
    #: a reader, so it never translates itself — the server language would leak into an admin's
    #: own-language page ("2 Stunden before the war ends", tracker #0152). See summary_text().
    summary_params: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    # Check-specific data the detail renderer needs (kept out of Finding to keep that small).
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StepDetail:
    """The rendered detail page of one step."""
    sections: List[Tuple[str, str]]             # (heading, body); heading "" = no heading
    links: List[Tuple[str, str]] = field(default_factory=list)   # (button label, url)
    #: (heading, body) of the dominant "what's wrong → how to fix" block (problem-first layout);
    #: None when the step is fine.
    problem: Optional[Tuple[str, str]] = None


CheckFn = Callable[[discord.Guild], StepResult]
DetailFn = Callable[[discord.Guild, StepResult, Optional[int], Optional[str]], StepDetail]


@dataclass(frozen=True)
class AuditStep:
    id: str                                     # stable — will be stored by the guided setup
    order: int                                  # 1-based display number
    audience: Literal["admin", "user", "both"]
    requires: Tuple[str, ...] = ()
    check: CheckFn = field(kw_only=True)
    detail: DetailFn = field(kw_only=True)
    #: bot_permissions feature keys whose channel/role problems this step reports. Step 1 lists
    #: only the problems of features no implemented step owns, so one problem is red once.
    permission_features: Tuple[str, ...] = ()
    #: /clan management mode that fixes this step ("⚙️ Open settings", Phase 5) — chosen per
    #: result, since one step can need different screens; None = no settings screen.
    fix_mode: Optional[Callable[["StepResult"], Optional[str]]] = None
    #: The guild may mark this step "we don't use this" (Phase 6). Not for the basics every
    #: server needs (permissions, language, clans).
    dismissable: bool = True
    #: guild_config key of the step's on/off switch: its page then opens with the same
    #: "Status: 🟢 Enabled / 🔴 Disabled" line as /clan management (qaplop 2026-10-11).
    toggle_key: Optional[str] = None

    def toggle_enabled(self, guild_id: Any) -> Optional[bool]:
        """The switch's state, or None for a step without one."""
        if self.toggle_key is None:
            return None
        from clashcontrol.cache_manager import CACHE
        return bool(CACHE.server_config.get(str(guild_id), {}).get(self.toggle_key))

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
    """Step 1: missing permissions (with why) or Administrator as the problem block; the
    Administrator explanation and the role position as information."""
    tr = _translator(guild_id, user_id)

    def perm_name(p: str) -> str:
        return bp.perm_label(p, guild_id=guild_id, user_id=user_id, scope="server")

    if result.status == AuditStatus.INFO:
        return StepDetail([("", tr("config_audit.bot_permissions.not_ready"))])

    admin: bool = result.data.get("administrator", False)
    missing: Tuple[str, ...] = result.data.get("missing", ())
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    fix = tr("config_audit.bot_permissions.fix_text", button=tr("config_audit.bot_permissions.button_reauthorize"))
    problem: Optional[Tuple[str, str]] = None
    sections: List[Tuple[str, str]] = []
    if missing:
        lines = [f"❌ **{perm_name(p)}** — {tr(f'permissions.why.{p}')}" for p in missing]
        problem = (tr("config_audit.bot_permissions.missing_title", count=len(missing)),
                   "\n".join(lines) + f"\n\n**{tr('config_audit.bot_permissions.fix_title')}**\n{fix}")
    elif issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif not admin:
        problem = (f"⚠️ {perm_name('administrator')}",
                   tr("config_audit.bot_permissions.admin_missing")
                   + f"\n\n**{tr('config_audit.bot_permissions.fix_title')}**\n{fix}")
    if admin:
        sections.append((f"✅ {perm_name('administrator')}", tr("config_audit.bot_permissions.admin_granted")))
    elif missing or issues:
        sections.append((f"⚠️ {perm_name('administrator')}", tr("config_audit.bot_permissions.admin_missing")))
    if not admin and not missing:
        sections.append(("", tr("config_audit.bot_permissions.all_present")))
    me = bp._me(guild)  # pyright: ignore[reportPrivateUsage]
    if me is not None:
        sections.append((tr("config_audit.bot_permissions.role_title"),
                         tr("config_audit.bot_permissions.role_position", role=me.top_role.mention)))

    links: List[Tuple[str, str]] = []
    if missing or not admin:
        try:
            import QBcore
            from clashcontrol.constants import bot_install_url
            links.append((
                tr("config_audit.bot_permissions.button_reauthorize"),
                bot_install_url(QBcore.bot.application_id, guild.id, bp.install_permissions().value),
            ))
        except Exception as e:  # never let a missing app id break the page
            logging.info(f"[SETUP] No re-authorize link for guild {guild.id}: {e}")
    return StepDetail(sections, links, problem)


# ---------------------------------------------------------------------------------------------
# Shared helpers for the configuration steps (Phase 2+)
# ---------------------------------------------------------------------------------------------

Tr = Callable[..., str]


def _translator(guild_id: Optional[int], user_id: Optional[str]) -> Tr:
    from clashcontrol.i18n import t

    def tr(key: str, **kw: Any) -> str:
        return t(key, guild_id=guild_id, user_id=user_id, **kw)
    return tr


#: A summary parameter translated when the summary is shown: called with (guild_id, user_id).
LazyText = Callable[[Optional[int], Optional[str]], str]


def summary_text(result: StepResult, guild_id: Optional[int], user_id: Optional[str]) -> str:
    """A step's one-line summary in the reader's language, LazyText parameters included."""
    params = {k: (v(guild_id, user_id) if callable(v) else v) for k, v in result.summary_params.items()}
    return _translator(guild_id, user_id)(result.summary_key, **params)


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


_BASIC = "ui_components.clan_management.mode_config_label"

# ---------------------------------------------------------------------------------------------
# Problem-first page layout (qaplop, 2026-10-11): a step with a problem opens with ONE dominant
# block — what's wrong, then numbered fix steps — and only then the information about the step.
# ---------------------------------------------------------------------------------------------

def _open_path(tr: "Tr", *label_keys: str) -> str:
    """'**⚙️ Open settings** → 🌐 Select Channels' — the path from /setup's own button, which
    already opens the right /clan management screen (so its mode label is not repeated)."""
    return " → ".join([f"**{tr('config_audit.button_open_settings')}**", *(tr(k) for k in label_keys)])


def _problem(tr: "Tr", heading: str, what: str, fixes: List[str], either: bool = False) -> Tuple[str, str]:
    """(heading, body) for StepDetail.problem: the explanation, then the numbered fix steps and
    "then click Check again". ``either``: the fixes are alternatives, not a sequence."""
    title = tr("config_audit.fix_title_either" if either else "config_audit.fix_title")
    lines = [what.strip(), "", f"**{title}**"] if what else [f"**{title}**"]
    lines += [f"{n}. {fix}" for n, fix in enumerate(fixes, 1)]
    lines.append(tr("config_audit.fix_recheck", button=tr("config_audit.button_recheck")))
    return heading, "\n".join(lines)


def _issue_lines(issues: List[bp.PermissionIssue], guild_id: Optional[int], user_id: Optional[str]) -> str:
    return "\n".join("• " + bp.format_issue(i, guild_id=guild_id, user_id=user_id) for i in issues)


def _permission_problem(tr: "Tr", issues: List[bp.PermissionIssue], guild_id: Optional[int],
                        user_id: Optional[str]) -> Tuple[str, str]:
    """The shared ❌ block for a feature whose channel/role the bot can't use."""
    kinds = {i.kind for i in issues}
    fixes = []
    if bp.MISSING_CHANNEL_PERMS in kinds:
        fixes.append(tr("config_audit.fix.channel_perms"))
    if bp.MISSING_MANAGE_ROLES in kinds:
        fixes.append(tr("permissions.warning_fix_manage_roles"))
    if kinds - {bp.MISSING_CHANNEL_PERMS, bp.MISSING_MANAGE_ROLES}:
        fixes.append(tr("config_audit.fix.see_lines"))
    return _problem(tr, tr("config_audit.permission_issues_title", count=len(issues)),
                    _issue_lines(issues, guild_id, user_id), fixes)



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
    change = tr("config_audit.change_line", path=_open_path(
        tr, "ui_components.basic_config.button_select_language") + " · " + tr("ui_components.basic_config.button_select_timezone"))
    return StepDetail([
        (tr("config_audit.basics.language_title"), language),
        (tr("config_audit.basics.timezone_title"), timezone),
        ("", change),
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
    clans: List[Tuple[str, str]] = result.data.get("clans", [])
    member_clans = "ui_components.clan_management.button_member_clans"
    problem: Optional[Tuple[str, str]] = None
    if not clans:
        problem = _problem(tr, tr("config_audit.fix.clans_none_title"), tr("config_audit.clans.none_text"), [
            tr("config_audit.fix.clans_add", path=_open_path(tr, member_clans)),
            tr("config_audit.fix.clans_family", families=tr("ui_components.clan_management.mode_families_label")),
        ])
    else:
        bad = [(t, s) for t, s in clans if s in ("deleted", "warlog_private")]
        if bad:
            lines = [f"❌ **{_clan_name(t)}** `{t}` — {tr(f'config_audit.clans.state_{s}')}" for t, s in bad]
            fixes = []
            if any(s == "warlog_private" for _, s in bad):
                fixes.append(tr("config_audit.clans.warlog_fix"))
            if any(s == "deleted" for _, s in bad):
                fixes.append(tr("config_audit.fix.clans_remove_deleted", path=_open_path(tr, member_clans)))
            problem = _problem(tr, tr("config_audit.fix.clans_bad_title", count=len(bad)),
                               _join_limited(lines, 1500, lambda n: tr("permissions.warning_more", count=n)), fixes)
    sections: List[Tuple[str, str]] = []
    if clans:
        # What member clans and families are (qaplop 2026-10-11) — without clans the problem
        # block already says it (none_text), so only here.
        from clashcontrol.QBdiscocmdshelper import command_mention
        sections.append((tr("config_audit.clans.what_title"),
                         tr("config_audit.clans.what", subscribe=command_mention("subscribe"))))
        icon = {"ok": "✅", "deleted": "❌", "warlog_private": "❌", "no_data": "ℹ️"}
        lines = [f"{icon[s]} **{_clan_name(t)}** `{t}`" + (" — " + tr("config_audit.clans.state_no_data") if s == "no_data" else "")
                 for t, s in clans]
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
    return StepDetail(sections, problem=problem)


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
    if result.status == AuditStatus.OFF:
        return StepDetail([("", tr("config_audit.welcome.off_text")), ("", tr("config_audit.change_line", path=_open_path(
            tr, "ui_components.basic_config.button_activate_welcome_message")))])
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    codes = {f.code for f in result.findings}
    configure = _open_path(tr, "ui_components.basic_config.button_configure_welcome_message")
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif "apply_channel_missing" in codes:
        problem = _problem(tr, tr("config_audit.fix.welcome_apply_missing_title"), tr("config_audit.welcome.mode_apply_missing"),
                           [tr("config_audit.fix.welcome_apply", path=configure)])
    elif "no_apply_channel" in codes:
        problem = _problem(tr, tr("config_audit.fix.welcome_apply_unset_title"), tr("config_audit.welcome.mode_apply_unset"),
                           [tr("config_audit.fix.welcome_apply", path=configure)])
    sections: List[Tuple[str, str]] = []
    system_channel = guild.system_channel
    if system_channel is not None:
        sections.append((tr("config_audit.welcome.posts_title"),
                         tr("config_audit.welcome.posts_in", channel=system_channel.mention)))
    if result.data.get("mode") == "apply_channel":
        apply_channel = config.get("welcome_apply_channel_id")
        if apply_channel and _channel_exists(guild, apply_channel):
            sections.append((tr("config_audit.welcome.content_title"),
                             tr("config_audit.welcome.mode_apply", channel=f"<#{apply_channel}>")))
    else:
        tags = _welcome_clan_tags(config)
        sections.append((tr("config_audit.welcome.content_title"),
                         tr("config_audit.welcome.mode_clan_link", clans=", ".join(_clan_name(t) for t in tags))
                         if tags else tr("config_audit.welcome.mode_clan_link_none")))
    if not config.get("registration_message_enabled"):
        sections.append(("", tr("config_audit.welcome.registration_hint")))
    return StepDetail(sections, problem=problem)


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
                       "time": lambda g, u: format_war_threshold_label(hours, g, u)}, data=data)


def detail_channel_war_notifications(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                                     user_id: Optional[str]) -> StepDetail:
    from clashcontrol.ui_clan_management import format_war_threshold_label
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif any(f.code == "no_channel" for f in result.findings):
        problem = _problem(tr, tr("config_audit.fix.no_channel_title"), "", [tr(
            "config_audit.fix.pick_channel", path=_open_path(tr, "ui_components.basic_config.button_select_channels"))])
    sections: List[Tuple[str, str]] = [("", tr("config_audit.channel_war.what"))]
    if result.status != AuditStatus.OFF and result.data.get("channel_id"):
        hours = float(config.get("war_notification_threshold_hours", 2.0) or 2.0)
        sections.append((tr("config_audit.settings_title"), tr(
            "config_audit.channel_war.settings", channel=f"<#{result.data['channel_id']}>",
            time=format_war_threshold_label(hours, guild_id, user_id))))
    if result.status in (AuditStatus.OFF, AuditStatus.OK):
        sections.append(("", tr("config_audit.change_line", path=_open_path(
            tr, "ui_components.basic_config.button_select_channels") + " · " + tr(
            "ui_components.basic_config.button_select_notification_time") + " · " + tr(
            "ui_components.basic_config.button_activate_channel_notifications"))))
    return StepDetail(sections, problem=problem)


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
                       "time": lambda g, u: _raid_settings_params(config, _translator(g, u))["time"],
                       "scope": lambda g, u: _raid_settings_params(config, _translator(g, u))["scope"]},
                      data=data)


def detail_raid_reminders(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                          user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif any(f.code == "no_channel" for f in result.findings):
        problem = _problem(tr, tr("config_audit.fix.no_channel_title"), "", [tr(
            "config_audit.fix.pick_channel", path=_open_path(tr, "ui_components.basic_config.button_select_channels"))])
    sections: List[Tuple[str, str]] = [("", tr("config_audit.raid.what"))]
    channel_id, fallback = _raid_channel(config)
    if result.status != AuditStatus.OFF and channel_id:
        text = tr("config_audit.raid.settings", channel=f"<#{channel_id}>", **_raid_settings_params(config, tr))
        if fallback:
            text += "\n" + tr("config_audit.raid.fallback_hint")
        sections.append((tr("config_audit.settings_title"), text))
    if result.status in (AuditStatus.OFF, AuditStatus.OK):
        sections.append(("", tr("config_audit.change_line", path=_open_path(
            tr, "ui_components.basic_config.button_select_channels") + " · " + tr(
            "ui_components.basic_config.button_select_notification_time") + " · " + tr(
            "ui_components.basic_config.button_activate_raid_notifications"))))
    return StepDetail(sections, problem=problem)


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
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    deleted: List[str] = result.data.get("deleted", [])
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif deleted:
        problem = _problem(tr, tr("config_audit.fix.leaderboards_deleted_title", count=len(deleted)), tr(
            "config_audit.leaderboards.deleted", clans=", ".join(f"{_clan_name(t)} `{t}`" for t in deleted),
            unsubscribe=command_mention("unsubscribe")), [])
    sections: List[Tuple[str, str]] = []
    subs: Dict[str, List[Dict[str, Any]]] = result.data.get("subs", {})
    if not subs:
        sections.append(("", tr("config_audit.leaderboards.off_text")))
    else:
        lines = [f"<#{cid}>: " + ", ".join(f"{_clan_name(s['clan_tag'])} ({s.get('mode') or '?'})" for s in lst)
                 for cid, lst in sorted(subs.items())]
        sections.append((tr("config_audit.leaderboards.list_title", count=len(subs)),
                         _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))))
    sections.append((tr("config_audit.leaderboards.how_title"), tr(
        "config_audit.leaderboards.how", subscribe=command_mention("subscribe"),
        unsubscribe=command_mention("unsubscribe"), subscriptions=command_mention("subscriptions"))))
    return StepDetail(sections, problem=problem)


# ---------------------------------------------------------------------------------------------
# Member-side visibility (Phase 3): can the people a hub is for actually see it?
# ---------------------------------------------------------------------------------------------

def _role(guild: discord.Guild, role_id: Any) -> Optional[discord.Role]:
    try:
        return guild.get_role(int(role_id))
    except (TypeError, ValueError):
        return None


def _newcomer_groups(guild: discord.Guild, config: Dict[str, Any]) -> List[List[discord.Role]]:
    """Who a brand-new member is: @everyone, or the newbie role when Simple Roles gives it on
    join (on_member_join → assign_newbie_role). One role per group."""
    groups = [[guild.default_role]]
    if config.get("role_system_enabled"):
        newbie = _role(guild, config.get("newbie_role_id"))
        if newbie is not None:
            groups.append([newbie])
    return groups


def _member_groups(guild: discord.Guild, config: Dict[str, Any]) -> List[List[discord.Role]]:
    """The role groups through which clan members reach a channel (qaplop, 2026-10-11). Members
    are covered as soon as ONE group fully works:
      - @everyone;
      - Simple Roles on → its member role;
      - CoC Roles on → ALL four CoC roles (a member holds only one of them);
      - Clan Roles on → the clan role of EVERY member clan.
    A group with a role that isn't set up (missing id, deleted role) doesn't count."""
    from clashcontrol.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags

    groups: List[List[discord.Role]] = [[guild.default_role]]
    if config.get("role_system_enabled"):
        member = _role(guild, config.get("member_role_id"))
        if member is not None:
            groups.append([member])
    if config.get("coc_role_enabled"):
        coc = [_role(guild, config.get(k)) for k in ("coc_role_member_id", "coc_role_elder_id",
                                                      "coc_role_coleader_id", "coc_role_leader_id")]
        if all(r is not None for r in coc):
            groups.append(coc)  # type: ignore[arg-type]
    if config.get("clan_role_enabled"):
        mapped = config.get("clan_roles") or {}
        tags = resolve_guild_member_clan_tags(guild.id)
        clan = [_role(guild, mapped.get(t)) if mapped.get(t) else None for t in tags]
        if clan and all(r is not None for r in clan):
            groups.append(clan)  # type: ignore[arg-type]
    return groups


def _channel(guild: discord.Guild, channel_id: Any) -> Any:
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    getter = getattr(guild, "get_channel_or_thread", None) or guild.get_channel
    return getter(cid)


def _channel_permissions(channel: Any, role: discord.Role) -> discord.Permissions:
    """What ``role`` may do in ``channel``: the role's permissions plus @everyone's and its own
    channel overrides, as Discord resolves them.

    For text channels this deliberately bypasses discord.py's ``TextChannel.permissions_for()``,
    which clears every "voice" permission — and discord.py still counts "Use Activities" as one,
    although Discord offers and honours it in text channels (the player hub's CWL Preferences
    button opens an Activity there). Through discord.py's own call every text channel looked
    like it lacked "Use Activities" (STAY server, 2026-10-11). A thread inherits its parent's."""
    target = getattr(channel, "parent", None) if isinstance(channel, discord.Thread) else channel
    if target is not None and type(target) is discord.TextChannel:
        return discord.abc.GuildChannel.permissions_for(target, role)
    return channel.permissions_for(role)


def _groups_can(channel: Any, groups: List[List[discord.Role]], *perms: str) -> bool:
    """True when at least one group has every permission in ``perms`` in ``channel`` for EVERY
    role of the group."""
    def role_can(role: discord.Role) -> bool:
        try:
            p = _channel_permissions(channel, role)
        except Exception:
            return False
        return all(getattr(p, name, False) for name in perms)
    return any(group and all(role_can(r) for r in group) for group in groups)


# ---------------------------------------------------------------------------------------------
# Step 5: player registration
# ---------------------------------------------------------------------------------------------

def link_coverage(guild: discord.Guild) -> Tuple[int, int, int]:
    """(linked, total, verified) player accounts currently in this server's member clans.

    Every member of a tracked clan sits in some account — a Discord user's or the UNASSIGNED pool
    (coc_cache.update_player_info_in_user_accounts) — with ``current_clan_tag`` kept current, so
    this needs no API call. Counts accounts, not people: one member with three accounts counts
    three times, as in Manage Registrations.
    """
    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags

    tags = set(resolve_guild_member_clan_tags(guild.id))
    linked = total = verified = 0
    if not tags:
        return 0, 0, 0
    for user_id, account in CACHE.user_accounts.items():
        for player in (account or {}).get("players", []) or []:
            if player.get("current_clan_tag") not in tags:
                continue
            total += 1
            if user_id != "UNASSIGNED":
                linked += 1
                if player.get("verified"):
                    verified += 1
    return linked, total, verified


def check_registration(guild: discord.Guild) -> StepResult:
    """The registration message only (qaplop 2026-10-11: linking accounts is step 14): on, a
    channel, the bot can post there, and new members can see it."""
    config = _config(guild)
    data: Dict[str, Any] = {"issues": []}
    findings: List[Finding] = []
    enabled = bool(config.get("registration_message_enabled"))
    channel_id = config.get("registration_channel_id")
    data["channel_id"] = channel_id
    if not enabled:
        findings.append(Finding(AuditStatus.INCOMPLETE, "hub_off"))
    elif not channel_id:
        findings.append(Finding(AuditStatus.INCOMPLETE, "no_channel"))
    else:
        issues = _permission_issues(guild, {"registration"})
        data["issues"] = issues
        findings += [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues]
        channel = _channel(guild, channel_id)
        if not issues and channel is not None and not _groups_can(
                channel, _newcomer_groups(guild, config), "view_channel", "read_message_history"):
            findings.append(Finding(AuditStatus.INCOMPLETE, "hidden_from_newcomers"))
    broken = [f for f in findings if f.status == AuditStatus.BROKEN]
    if broken:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems",
                          {"count": len(broken)}, findings, data)
    if findings:
        return StepResult(AuditStatus.INCOMPLETE, f"config_audit.registration.summary_{findings[0].code}",
                          findings=findings, data=data)
    return StepResult(AuditStatus.OK, "config_audit.registration.summary_ok",
                      {"channel": _channel_name(guild, channel_id)}, findings, data)


def detail_registration(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                        user_id: Optional[str]) -> StepDetail:
    from clashcontrol.QBdiscocmdshelper import command_mention
    tr = _translator(guild_id, user_id)
    codes = {f.code for f in result.findings}
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    registration = command_mention("registration")
    channel_id = result.data.get("channel_id")
    select = _open_path(tr, "ui_components.basic_config.button_select_channels")
    activate = _open_path(tr, "ui_components.basic_config.button_activate_registration")
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif "hub_off" in codes:
        problem = _problem(tr, tr("config_audit.fix.registration_off_title"),
                           tr("config_audit.fix.registration_off", registration=registration),
                           [tr("config_audit.fix.registration_pick", path=select),
                            tr("config_audit.fix.registration_activate", path=activate)])
    elif "no_channel" in codes:
        problem = _problem(tr, tr("config_audit.fix.no_channel_title"), "",
                           [tr("config_audit.fix.registration_pick", path=select)])
    elif "hidden_from_newcomers" in codes:
        channel = f"<#{channel_id}>"
        problem = _problem(tr, tr("config_audit.fix.registration_hidden_title"),
                           tr("config_audit.fix.registration_hidden", channel=channel), [
            tr("config_audit.fix.registration_move", path=select),
            tr("config_audit.fix.allow_everyone", channel=channel,
               view=bp.perm_label("view_channel", guild_id=guild_id, user_id=user_id),
               history=bp.perm_label("read_message_history", guild_id=guild_id, user_id=user_id)),
        ], either=True)
    about = (tr("config_audit.registration.hub_in", channel=f"<#{channel_id}>")
             if channel_id and "hub_off" not in codes else tr("config_audit.registration.hub_off"))
    about += "\n" + tr("config_audit.registration.command_hint", registration=registration)
    # Why it matters (qaplop 2026-10-11): linked accounts are what almost every feature runs on.
    why = tr("config_audit.registration.why", whois=command_mention("whois"))
    return StepDetail([(tr("config_audit.registration.why_title"), why), ("", about)], problem=problem)


# ---------------------------------------------------------------------------------------------
# Step 9: server roles
# ---------------------------------------------------------------------------------------------

_ROLE_SYSTEMS = (("role_system", "role_system_enabled"), ("coc_roles", "coc_role_enabled"),
                 ("clan_roles", "clan_role_enabled"))


def check_roles(guild: discord.Guild) -> StepResult:
    """Simple Roles, CoC roles, clan roles: OFF when none is on. Permission/hierarchy problems
    (bot_permissions) → BROKEN; Simple Roles without any role, or a member clan without its clan
    role (creation failed) → INCOMPLETE."""
    from clashcontrol.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags
    config = _config(guild)
    active = [name for name, flag in _ROLE_SYSTEMS if config.get(flag)]
    if not active:
        return StepResult(AuditStatus.OFF, "config_audit.roles.summary_off")
    issues = _permission_issues(guild, set(active))
    findings = [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues]
    if "role_system" in active and not (config.get("newbie_role_id") or config.get("member_role_id")):
        findings.append(Finding(AuditStatus.INCOMPLETE, "simple_roles_unset"))
    missing_clan_roles: List[str] = []
    if "clan_roles" in active:
        mapped = config.get("clan_roles") or {}
        missing_clan_roles = [t for t in resolve_guild_member_clan_tags(guild.id) if not mapped.get(t)]
        if missing_clan_roles:
            findings.append(Finding(AuditStatus.INCOMPLETE, "clan_roles_missing", (("count", len(missing_clan_roles)),)))
    data = {"active": active, "issues": issues, "missing_clan_roles": missing_clan_roles}
    broken = [f for f in findings if f.status == AuditStatus.BROKEN]
    if broken:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems",
                          {"count": len(broken)}, findings, data)
    incomplete = [f for f in findings if f.status == AuditStatus.INCOMPLETE]
    if incomplete:
        return StepResult(AuditStatus.INCOMPLETE, f"config_audit.roles.summary_{incomplete[0].code}",
                          dict(incomplete[0].params), findings, data)
    def systems(g: Optional[int], u: Optional[str]) -> str:
        tr = _translator(g, u)
        return ", ".join(tr(f"config_audit.roles.name_{n}") for n in active)
    return StepResult(AuditStatus.OK, "config_audit.roles.summary_ok", {"systems": systems}, findings, data)


def detail_roles(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                 user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    active: List[str] = result.data.get("active", [])
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    codes = {f.code for f in result.findings}
    missing_clan_roles: List[str] = result.data.get("missing_clan_roles", [])
    configure = _open_path(tr, "ui_components.clan_management.button_configure_roles")
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif "simple_roles_unset" in codes:
        problem = _problem(tr, tr("config_audit.fix.simple_roles_title"), "",
                           [tr("config_audit.fix.simple_roles", path=configure)])
    elif missing_clan_roles:
        problem = _problem(tr, tr("config_audit.roles.summary_clan_roles_missing", count=len(missing_clan_roles)),
                           ", ".join(_clan_name(t) for t in missing_clan_roles),
                           [tr("config_audit.roles.clan_roles_missing_fix",
                               button=tr("ui_components.clan_management.button_toggle_clan_roles_enable"))])

    def mention(rid: Any) -> str:
        return f"<@&{rid}>" if rid else tr("config_audit.roles.not_set")

    def status(enabled: bool) -> str:
        # /clan management's own status line for the same switch.
        state = tr("ui_components.basic_config.config_status_enabled" if enabled
                   else "ui_components.basic_config.config_status_disabled")
        return tr("ui_components.basic_config.config_welcome_status", status=f"{'🟢' if enabled else '🔴'} {state}")

    sections: List[Tuple[str, str]] = []
    for name, _flag in _ROLE_SYSTEMS:
        title = tr(f"config_audit.roles.name_{name}")
        # What it does, how it works, what it's good for — on or off (qaplop 2026-10-11).
        explain = tr(f"config_audit.roles.explain_{name}")
        if name not in active:
            sections.append((f"⏸️ {title}", f"{explain}\n{status(False)}"))
            continue
        if name == "role_system":
            body = tr("config_audit.roles.simple_roles", newbie=mention(config.get("newbie_role_id")),
                      member=mention(config.get("member_role_id")))
        elif name == "coc_roles":
            ids = [config.get(k) for k in ("coc_role_member_id", "coc_role_elder_id",
                                           "coc_role_coleader_id", "coc_role_leader_id")]
            body = " ".join(f"<@&{i}>" for i in ids if i) if any(ids) else tr("config_audit.roles.coc_roles_auto")
        else:
            lines = [f"{_clan_name(t)}: <@&{rid}>" for t, rid in (config.get("clan_roles") or {}).items() if rid]
            budget = 1024 - len(explain) - len(status(True)) - 2   # one field holds all three
            body = _join_limited(lines, budget, lambda n: tr("permissions.warning_more", count=n)) or tr("config_audit.roles.not_set")
        sections.append((f"✅ {title}", f"{explain}\n{status(True)}\n{body}"[:1024]))
    me = bp._me(guild)  # pyright: ignore[reportPrivateUsage]
    if active and me is not None:
        sections.append((tr("config_audit.bot_permissions.role_title"),
                         tr("config_audit.bot_permissions.role_position", role=me.top_role.mention)))
    return StepDetail(sections, problem=problem)


# ---------------------------------------------------------------------------------------------
# Step 10: CWL settings
# ---------------------------------------------------------------------------------------------

def _player_hub_member_problem(guild: discord.Guild, config: Dict[str, Any], channel: Any) -> Optional[str]:
    """Why members can't use the player hub in ``channel``: "player_hub_hidden" (can't see it or
    read its history), "no_activities" (can't open the Activity), or None. Shared by steps 10/11."""
    members = _member_groups(guild, config)
    if not _groups_can(channel, members, "view_channel", "read_message_history"):
        return "player_hub_hidden"
    if not _groups_can(channel, members, "view_channel", "use_embedded_activities"):
        return "no_activities"
    return None


def check_cwl_settings(guild: discord.Guild) -> StepResult:
    """The CWL Settings screen top to bottom. OFF when neither hub message is on and no
    coordinator role is linked (CWL can still be run from /clan management). Hub without channel,
    or a player hub members can't see / can't open the Activity from → INCOMPLETE."""
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets
    config = _config(guild)
    mgmt_on = bool(config.get("cwl_management_message_enabled"))
    player_on = bool(config.get("cwl_player_hub_message_enabled"))
    try:
        coordinator_roles = list(cwl_coordinator_role_targets(config).keys())
    except Exception:
        coordinator_roles = []
    data: Dict[str, Any] = {"mgmt_on": mgmt_on, "player_on": player_on, "issues": [],
                            "coordinator_roles": coordinator_roles}
    if not (mgmt_on or player_on or coordinator_roles):
        return StepResult(AuditStatus.OFF, "config_audit.cwl_settings.summary_off", data=data)
    findings: List[Finding] = []
    if mgmt_on and not config.get("cwl_management_channel_id"):
        findings.append(Finding(AuditStatus.INCOMPLETE, "mgmt_no_channel"))
    if player_on and not config.get("cwl_player_hub_channel_id"):
        findings.append(Finding(AuditStatus.INCOMPLETE, "player_no_channel"))
    issues = _permission_issues(guild, {"cwl_management", "cwl_player_hub", "cwl_coordinator_role"})
    data["issues"] = issues
    findings += [Finding(AuditStatus.BROKEN, "permission", (("kind", i.kind),)) for i in issues]
    player_channel = _channel(guild, config.get("cwl_player_hub_channel_id")) if player_on else None
    if player_channel is not None:
        member_problem = _player_hub_member_problem(guild, config, player_channel)
        if member_problem:
            findings.append(Finding(AuditStatus.INCOMPLETE, member_problem))
    broken = [f for f in findings if f.status == AuditStatus.BROKEN]
    if broken:
        return StepResult(AuditStatus.BROKEN, "config_audit.summary_permission_problems",
                          {"count": len(broken)}, findings, data)
    incomplete = [f for f in findings if f.status == AuditStatus.INCOMPLETE]
    if incomplete:
        return StepResult(AuditStatus.INCOMPLETE, f"config_audit.cwl_settings.summary_{incomplete[0].code}",
                          findings=findings, data=data)
    def parts(g: Optional[int], u: Optional[str]) -> str:
        tr = _translator(g, u)
        if mgmt_on and player_on:   # both: the short form, so the line fits (tracker #0152)
            return tr("config_audit.cwl_settings.part_both",
                      mgmt=_channel_name(guild, config.get("cwl_management_channel_id")),
                      player=_channel_name(guild, config.get("cwl_player_hub_channel_id")))
        lines = []
        if mgmt_on:
            lines.append(tr("config_audit.cwl_settings.part_mgmt", channel=_channel_name(guild, config.get("cwl_management_channel_id"))))
        if player_on:
            lines.append(tr("config_audit.cwl_settings.part_player", channel=_channel_name(guild, config.get("cwl_player_hub_channel_id"))))
        return " · ".join(lines) or tr("config_audit.cwl_settings.part_no_hubs")
    return StepResult(AuditStatus.OK, "config_audit.cwl_settings.summary_ok", {"parts": parts}, findings, data)


def detail_cwl_settings(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                        user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    config = _config(guild)
    codes = {f.code for f in result.findings}
    issues: List[bp.PermissionIssue] = result.data.get("issues", [])
    channels = _open_path(tr, "cwl.settings.button_configure_channels")
    player_channel = f"<#{config.get('cwl_player_hub_channel_id')}>"
    problem: Optional[Tuple[str, str]] = None
    if issues:
        problem = _permission_problem(tr, issues, guild_id, user_id)
    elif codes & {"mgmt_no_channel", "player_no_channel"}:
        problem = _problem(tr, tr("config_audit.fix.no_channel_title"), "",
                           [tr("config_audit.fix.pick_channel", path=channels)])
    elif "player_hub_hidden" in codes:
        problem = _problem(tr, tr("config_audit.fix.player_hub_hidden_title"),
                           tr("config_audit.cwl_settings.player_hub_hidden"), [
            tr("config_audit.fix.player_hub_move", path=channels),
            tr("config_audit.fix.allow_members", channel=player_channel,
               view=bp.perm_label("view_channel", guild_id=guild_id, user_id=user_id),
               history=bp.perm_label("read_message_history", guild_id=guild_id, user_id=user_id)),
        ], either=True)
    elif "no_activities" in codes:
        perm = bp.perm_label("use_embedded_activities", guild_id=guild_id, user_id=user_id)
        problem = _problem(tr, tr("config_audit.fix.no_activities_title"), tr(
            "config_audit.cwl_settings.no_activities", perm=perm, button=tr("cwl.player_hub.button_preferences")),
            [tr("config_audit.fix.allow_activities", channel=player_channel, perm=perm)])

    def hub(on: bool, channel_key: str) -> str:
        if not on:
            return tr("config_audit.cwl_settings.hub_off")
        cid = config.get(channel_key)
        return f"<#{cid}>" if cid else tr("config_audit.cwl_settings.hub_no_channel")

    months = int(config.get("cwl_retention_months") or 0)
    retention = tr("cwl.settings.retention_months", months=months) if months else tr("cwl.settings.retention_never")
    if (config.get("cwl_coordinator_role_mode") or "single") == "per_clan":
        count = len([r for r in (config.get("cwl_clan_coordinator_roles") or {}).values() if r])
        coordinator = tr("config_audit.cwl_settings.coordinator_per_clan", count=count)
    elif config.get("cwl_coordinator_role_id"):
        coordinator = f"<@&{config.get('cwl_coordinator_role_id')}>"
    else:
        coordinator = tr("config_audit.roles.not_set")
    on_off = {True: tr("config_audit.on"), False: tr("config_audit.off")}
    return StepDetail([
        ("", tr("config_audit.cwl_settings.what")),
        (tr("config_audit.cwl_settings.hubs_title"), tr(
            "config_audit.cwl_settings.hubs",
            mgmt=hub(result.data.get("mgmt_on", False), "cwl_management_channel_id"),
            player=hub(result.data.get("player_on", False), "cwl_player_hub_channel_id"))),
        (tr("config_audit.settings_title"), tr(
            "config_audit.cwl_settings.settings", retention=retention,
            include_all=on_off[bool(config.get("cwl_enrollment_include_all_linked_accounts"))],
            coordinator=coordinator,
            bench=on_off[(config.get("cwl_signup_mode") or "standard") == "extended"])),
    ], problem=problem)


# ---------------------------------------------------------------------------------------------
# Step 6: war notifications (personal DMs)
# ---------------------------------------------------------------------------------------------

def notification_coverage(guild: discord.Guild) -> Tuple[int, int, int]:
    """(members, war_on, raid_on): Discord users with at least one account in this server's
    member clans, and how many of them have DM war / raid reminders switched on
    (``notification_settings.war_reminders`` / ``raid_reminders``, as war_notifications reads them)."""
    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import resolve_guild_member_clan_tags

    tags = set(resolve_guild_member_clan_tags(guild.id))
    members = war_on = raid_on = 0
    if not tags:
        return 0, 0, 0
    for user_id, account in CACHE.user_accounts.items():
        if user_id == "UNASSIGNED" or not account:
            continue
        if not any(p.get("current_clan_tag") in tags for p in account.get("players", []) or []):
            continue
        members += 1
        settings = account.get("notification_settings") or {}
        war_on += bool(settings.get("war_reminders"))
        raid_on += bool(settings.get("raid_reminders"))
    return members, war_on, raid_on


def check_war_notifications(guild: discord.Guild) -> StepResult:
    """Linked members with DM reminders. Same rule as link coverage (Q-6): the number only when
    someone has them on, ⚠️ only when linked members exist and nobody does."""
    members, war_on, raid_on = notification_coverage(guild)
    custodians = {t: ids for t, ids in (_config(guild).get("clan_custodians") or {}).items() if ids}
    data = {"members": members, "war_on": war_on, "raid_on": raid_on, "custodians": custodians}
    if not members:
        return StepResult(AuditStatus.INFO, "config_audit.war_notifications.summary_nobody_linked", data=data)
    if not war_on:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.war_notifications.summary_none_on",
                          findings=[Finding(AuditStatus.INCOMPLETE, "none_on")], data=data)
    return StepResult(AuditStatus.OK, "config_audit.war_notifications.summary_ok",
                      {"war_on": war_on, "members": members}, data=data)


def detail_war_notifications(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                             user_id: Optional[str]) -> StepDetail:
    from clashcontrol.QBdiscocmdshelper import command_mention
    tr = _translator(guild_id, user_id)
    d = result.data
    members_how = tr("config_audit.war_notifications.members_how", registration=command_mention("registration"),
                     button=tr("playerregistration.button_war_notifications"))
    problem: Optional[Tuple[str, str]] = None
    if result.status == AuditStatus.INCOMPLETE:
        problem = _problem(tr, tr("config_audit.fix.no_reminders_title"), tr(
            "config_audit.war_notifications.coverage", members=d["members"], war_on=d["war_on"], raid_on=d["raid_on"]), [
            tr("config_audit.fix.reminders_members", how=members_how),
            tr("config_audit.fix.reminders_admins", path=_open_path(tr, "ui_components.clan_management.button_clan_notifications")),
        ])
    sections: List[Tuple[str, str]] = [("", tr("config_audit.war_notifications.what"))]
    if d["members"] and result.status != AuditStatus.INCOMPLETE:
        sections.append((tr("config_audit.war_notifications.coverage_title"), tr(
            "config_audit.war_notifications.coverage", members=d["members"], war_on=d["war_on"], raid_on=d["raid_on"])))
    elif not d["members"]:
        sections.append(("", tr("config_audit.war_notifications.nobody_linked")))
    if result.status != AuditStatus.INCOMPLETE:
        sections.append((tr("config_audit.war_notifications.members_title"), members_how))
    custodians: Dict[str, List[Any]] = d.get("custodians", {})
    lines = [f"{_clan_name(t)}: " + " ".join(f"<@{u}>" for u in ids) for t, ids in custodians.items()]
    sections.append((tr("config_audit.war_notifications.custodians_title"), tr(
        "config_audit.war_notifications.custodians_what",
        button=tr("ui_components.clan_management.button_custodians")) + "\n" + (
        _join_limited(lines, 800, lambda n: tr("permissions.warning_more", count=n)) if lines
        else tr("config_audit.war_notifications.custodians_none"))))
    return StepDetail(sections, problem=problem)


# ---------------------------------------------------------------------------------------------
# Step 11: "Your CWL Preferences" (what members see)
# ---------------------------------------------------------------------------------------------

def check_cwl_player_hub(guild: discord.Guild) -> StepResult:
    """The member side of CWL. ✅ when the player hub is up and members can use it (qaplop
    2026-10-11: a working hub is green, not ℹ️). The problems themselves stay step 10's (the hub
    is set up there), so a broken hub is ℹ️ "see step 10" here, never a second red line. No hub
    → ℹ️: members use /cwl preferences."""
    config = _config(guild)
    channel_id = config.get("cwl_player_hub_channel_id") if config.get("cwl_player_hub_message_enabled") else None
    if channel_id:
        data = {"channel_id": channel_id}
        channel = _channel(guild, channel_id)
        if (channel is None or _permission_issues(guild, {"cwl_player_hub"})
                or _player_hub_member_problem(guild, config, channel)):
            return StepResult(AuditStatus.INFO, "config_audit.cwl_player_hub.summary_hub_problem",
                              {"step": STEPS_BY_ID["cwl_settings"].order}, data=data)
        return StepResult(AuditStatus.OK, "config_audit.cwl_player_hub.summary_hub",
                          {"channel": _channel_name(guild, channel_id)}, data=data)
    return StepResult(AuditStatus.INFO, "config_audit.cwl_player_hub.summary_command", data={"channel_id": None})


def detail_cwl_player_hub(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                          user_id: Optional[str]) -> StepDetail:
    from clashcontrol.QBdiscocmdshelper import command_mention
    tr = _translator(guild_id, user_id)
    preferences = command_mention("cwl preferences")
    channel_id = result.data.get("channel_id")
    how = (tr("config_audit.cwl_player_hub.how_hub", channel=f"<#{channel_id}>",
              button=tr("cwl.player_hub.button_preferences"), preferences=preferences)
           if channel_id else tr("config_audit.cwl_player_hub.how_command", preferences=preferences))
    return StepDetail([
        ("", tr("config_audit.cwl_player_hub.what")),
        (tr("config_audit.cwl_player_hub.how_title"), how),
        (tr("config_audit.cwl_player_hub.admin_title"), tr(
            "config_audit.cwl_player_hub.admin", path=_open_path(tr, "cwl.settings.button_activate_player_hub"))),
    ])


# ---------------------------------------------------------------------------------------------
# Step 12: CWL season management
# ---------------------------------------------------------------------------------------------

#: CWL starts on the 1st and runs about ten days (resolve_current_cwl_season()'s reasoning): by
#: this day of the month it is over for every clan. Next month's season can be started as soon as
#: this month's is over for all member clans — this day at the latest (qaplop 2026-10-11).
CWL_LATEST_END_DAY = 11
#: Until this day a missing next season is only a hint (ℹ️ "can be started now"); from it on ⚠️.
CWL_SEASON_DUE_DAY = 20


def check_cwl_season(guild: discord.Guild, now: Optional[Any] = None) -> StepResult:
    """Season state against the calendar (DB reads via the sync helpers the CWL screens use; a
    DB in maintenance raises and run_step shows "could not be checked").

    Once this month's CWL is over for all participating clans (its season window has passed, or
    the CWL_LATEST_END_DAY at the latest): next month's season can be started (ℹ️ until the
    CWL_SEASON_DUE_DAY, ⚠️ from it on), and once it exists its participating clans must all have a
    start time → else ⚠️. Before that: the most relevant season's phase
    (resolve_cwl_phase) as ✅, or ℹ️ when there is none. A guild that never ran a season and has
    no CWL hub → ⏸️."""
    from datetime import datetime, timezone

    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import (
        get_current_cwl_event_sync, is_cwl_event_active_or_upcoming, resolve_current_cwl_season,
        resolve_cwl_phase,
    )
    now = now or datetime.now(timezone.utc)
    db = CACHE.db_manager
    if db is None:
        return StepResult(AuditStatus.INFO, "config_audit.summary_check_failed")
    config = _config(guild)
    events = db.list_cwl_events_sync(str(guild.id))
    next_season = resolve_current_cwl_season(now)
    data: Dict[str, Any] = {"next_season": next_season, "event": None, "clans": [], "phase": None,
                            "coordinated": len([t for t, ids in (config.get("cwl_clan_coordinators") or {}).items() if ids])}
    cwl_used = bool(events) or config.get("cwl_management_message_enabled") or config.get("cwl_player_hub_message_enabled")
    if not cwl_used:
        return StepResult(AuditStatus.OFF, "config_audit.cwl_season.summary_off", data=data)

    # Is this month's CWL over for every participating clan? Then next month's season is due.
    this_month = f"{now.year:04d}-{now.month:02d}"
    current = next((e for e in events if str(e.get("cwl_season", ""))[:7] == this_month
                    and e.get("status") != "cancelled"), None)
    in_window = now.day >= CWL_LATEST_END_DAY
    if current is not None and not in_window:
        starts = [c["cwl_start_at"] for c in db.get_cwl_event_clans_sync(int(current["id"]))
                  if c.get("participating", 1) and c.get("cwl_start_at")]
        in_window = not is_cwl_event_active_or_upcoming({**current, "clan_cwl_start_ats": starts}, now)
    event = next((e for e in events if str(e.get("cwl_season", ""))[:7] == next_season), None) if in_window \
        else get_current_cwl_event_sync(guild.id)
    if event is not None and event.get("status") == "cancelled":
        event = None
    if event is None:
        if in_window and now.day < CWL_SEASON_DUE_DAY:
            return StepResult(AuditStatus.INFO, "config_audit.cwl_season.summary_can_start",
                              {"season": next_season}, data=data)
        if in_window:
            return StepResult(AuditStatus.INCOMPLETE, "config_audit.cwl_season.summary_missing",
                              {"season": next_season}, [Finding(AuditStatus.INCOMPLETE, "season_missing")], data)
        return StepResult(AuditStatus.INFO, "config_audit.cwl_season.summary_between",
                          {"day": CWL_LATEST_END_DAY}, data=data)

    clans = db.get_cwl_event_clans_sync(int(event["id"]))
    participating = [c for c in clans if c.get("participating", 1)]
    phase = resolve_cwl_phase(event, clans)
    data.update(event=event, clans=participating, phase=phase)
    season = str(event.get("cwl_season", ""))[:7]
    if in_window and phase["phase"] <= 1:   # still in Setup: is it ready to start enrollment?
        if not participating:
            return StepResult(AuditStatus.INCOMPLETE, "config_audit.cwl_season.summary_no_clans",
                              {"season": season}, [Finding(AuditStatus.INCOMPLETE, "no_clans")], data)
        no_start = [c for c in participating if not c.get("cwl_start_at")]
        if no_start:
            return StepResult(AuditStatus.INCOMPLETE, "config_audit.cwl_season.summary_no_start",
                              {"season": season, "count": len(no_start)},
                              [Finding(AuditStatus.INCOMPLETE, "no_start_time", (("count", len(no_start)),))], data)
    phase_key = phase.get("key")

    def phase_label(g: Optional[int], u: Optional[str]) -> str:
        return _translator(g, u)(f"cwl.phase.step_{phase_key}") if phase_key else "?"
    key = "summary_finished" if phase.get("finished") else "summary_phase"
    return StepResult(AuditStatus.OK, f"config_audit.cwl_season.{key}",
                      {"season": season, "phase": phase_label, "clans": len(participating)}, data=data)


def detail_cwl_season(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                      user_id: Optional[str]) -> StepDetail:
    tr = _translator(guild_id, user_id)
    d = result.data
    codes = {f.code for f in result.findings}
    season = str((d.get("event") or {}).get("cwl_season") or d.get("next_season") or "")[:7]
    clans_path = _open_path(tr, "cwl.management.button_configure_clans")
    problem: Optional[Tuple[str, str]] = None
    if "season_missing" in codes:
        problem = _problem(tr, tr("config_audit.fix.season_missing_title", season=season), "", [
            tr("config_audit.fix.season_new", path=_open_path(tr, "cwl.management.button_add_season")),
            tr("config_audit.fix.season_clans", path=clans_path),
        ])
    elif "no_clans" in codes:
        problem = _problem(tr, tr("config_audit.fix.season_no_clans_title", season=season), "",
                           [tr("config_audit.fix.season_clans", path=clans_path)])
    elif "no_start_time" in codes:
        problem = _problem(tr, tr("config_audit.cwl_season.summary_no_start", season=season,
                                  count=result.summary_params.get("count", 0)), "",
                           [tr("config_audit.fix.season_start", path=clans_path)])
    sections: List[Tuple[str, str]] = [("", tr("config_audit.cwl_season.what"))]
    if d.get("event"):
        lines = [f"{_clan_name(c['clan_tag'])}: " + (str(c['cwl_start_at']).replace("T", " ") + " UTC"
                 if c.get("cwl_start_at") else "⚠️ " + tr("config_audit.cwl_season.no_start_time"))
                 for c in d.get("clans", [])]
        sections.append((tr("config_audit.cwl_season.season_title", season=season),
                         _join_limited(lines, 1024, lambda n: tr("permissions.warning_more", count=n))
                         if lines else tr("config_audit.cwl_season.no_clans")))
    sections.append((tr("config_audit.cwl_season.workflow_title"), tr(
        "config_audit.cwl_season.workflow",
        new=tr("cwl.management.button_add_season"), clans=tr("cwl.management.button_configure_clans"),
        enrollment=tr("cwl.management.button_start_enrollment"), teams=tr("cwl.management.button_manage_assignment"),
        preparation=tr("cwl.management.button_announce_rosters"), day=CWL_LATEST_END_DAY)))
    sections.append((tr("config_audit.cwl_season.coordinators_title"), tr(
        "config_audit.cwl_season.coordinators", count=d.get("coordinated", 0),
        button=tr("cwl.management.button_manage_coordinators"))))
    return StepDetail(sections, problem=problem)


# ---------------------------------------------------------------------------------------------
# Step 14: linked accounts (moved out of step 5 — ongoing work, not configuration)
# ---------------------------------------------------------------------------------------------

def check_linked_accounts(guild: discord.Guild) -> StepResult:
    """How many accounts in the member clans are linked (Q-6: the number when > 0, ⚠️ only when
    nobody is linked; ℹ️ while the clans' members are not known yet)."""
    linked, total, verified = link_coverage(guild)
    data = {"linked": linked, "total": total, "verified": verified}
    if not total:
        return StepResult(AuditStatus.INFO, "config_audit.linked_accounts.summary_no_data", data=data)
    if not linked:
        return StepResult(AuditStatus.INCOMPLETE, "config_audit.linked_accounts.summary_nobody",
                          {"total": total}, [Finding(AuditStatus.INCOMPLETE, "nobody_linked")], data)
    return StepResult(AuditStatus.OK, "config_audit.linked_accounts.summary_ok",
                      {"linked": linked, "total": total}, data=data)


def detail_linked_accounts(guild: discord.Guild, result: StepResult, guild_id: Optional[int],
                           user_id: Optional[str]) -> StepDetail:
    from clashcontrol.QBdiscocmdshelper import command_mention
    tr = _translator(guild_id, user_id)
    d = result.data
    link = tr("ui_components.clan_management.button_link_accounts")
    unlink = tr("ui_components.clan_management.button_unlink_player")
    problem: Optional[Tuple[str, str]] = None
    if result.status == AuditStatus.INCOMPLETE:
        problem = _problem(tr, tr("config_audit.fix.nobody_linked_title"),
                           tr("config_audit.fix.nobody_linked", total=d["total"]), [
            tr("config_audit.fix.link_own", registration=command_mention("registration"),
               button=tr("playerregistration.button_link_account")),
            tr("config_audit.fix.link_members", path=_open_path(tr, "ui_components.clan_management.button_link_accounts")),
        ])
    sections: List[Tuple[str, str]] = [("", tr("config_audit.linked_accounts.what"))]
    if d["linked"]:
        sections.append((tr("config_audit.registration.coverage_title"), tr(
            "config_audit.registration.coverage", linked=d["linked"], total=d["total"], verified=d["verified"])))
    sections.append((tr("config_audit.registration.admin_tools_title"), tr(
        "config_audit.linked_accounts.admin_tools", path=_open_path(tr), link=link, unlink=unlink)))
    return StepDetail(sections, problem=problem)


# ---------------------------------------------------------------------------------------------
# Registry (plan §3): every step in the order an admin sets ClashControl up.
# ---------------------------------------------------------------------------------------------

def _CONFIG(result: StepResult) -> Optional[str]:
    """Settings live in /clan management → ⚙️ Basic Configuration."""
    return "config"


STEPS: Tuple[AuditStep, ...] = (
    AuditStep("bot_permissions", 1, "admin", check=check_bot_permissions, detail=detail_bot_permissions,
              dismissable=False),
    AuditStep("basics", 2, "admin", check=check_basics, detail=detail_basics, fix_mode=_CONFIG,
              dismissable=False),
    AuditStep("clans", 3, "admin", check=check_clans, detail=detail_clans, fix_mode=_CONFIG,
              dismissable=False),
    AuditStep("welcome", 4, "admin", check=check_welcome, detail=detail_welcome,
              permission_features=("welcome",), fix_mode=_CONFIG, toggle_key="welcome_message_enabled"),
    AuditStep("registration", 5, "both", requires=("clans",),
              check=check_registration, detail=detail_registration,
              permission_features=("registration",), fix_mode=_CONFIG, toggle_key="registration_message_enabled"),
    AuditStep("war_notifications", 6, "both", requires=("clans",),
              check=check_war_notifications, detail=detail_war_notifications,
              fix_mode=lambda r: "notifications"),
    AuditStep("channel_war_notifications", 7, "admin", requires=("clans",),
              check=check_channel_war_notifications, detail=detail_channel_war_notifications,
              permission_features=("war_notifications",), fix_mode=_CONFIG,
              toggle_key="channel_war_notifications_enabled"),
    AuditStep("raid_reminders", 8, "admin", requires=("clans",),
              check=check_raid_reminders, detail=detail_raid_reminders,
              permission_features=("raid_notifications",), fix_mode=_CONFIG,
              toggle_key="channel_raid_notifications_enabled"),
    AuditStep("roles", 9, "admin", requires=("clans",), check=check_roles, detail=detail_roles,
              permission_features=("role_system", "coc_roles", "clan_roles"), fix_mode=lambda r: "roles"),
    AuditStep("cwl_settings", 10, "admin", requires=("clans",),
              check=check_cwl_settings, detail=detail_cwl_settings,
              permission_features=("cwl_management", "cwl_player_hub", "cwl_coordinator_role"),
              fix_mode=lambda r: "cwl_settings"),
    # 11 and 12 need clans only: a CWL hub setting problem (step 10) must not lock them.
    AuditStep("cwl_player_hub", 11, "both", requires=("clans",),
              check=check_cwl_player_hub, detail=detail_cwl_player_hub, fix_mode=lambda r: "cwl_settings"),
    AuditStep("cwl_season", 12, "admin", requires=("clans",),
              check=check_cwl_season, detail=detail_cwl_season, fix_mode=lambda r: "cwl_management"),
    # No prerequisite: any clan can be subscribed, member clan or not.
    AuditStep("leaderboards", 13, "admin", check=check_leaderboards, detail=detail_leaderboards,
              permission_features=("leaderboard",)),
    # Linking members' accounts is ongoing work, not configuration — so it comes last, after the
    # roles and reminders it feeds (qaplop 2026-10-11, split out of step 5).
    AuditStep("linked_accounts", 14, "admin", requires=("clans",), check=check_linked_accounts,
              detail=detail_linked_accounts, fix_mode=lambda r: "registrations", dismissable=False),
)

STEPS_BY_ID: Dict[str, AuditStep] = {s.id: s for s in STEPS}


def run_step(step: AuditStep, guild: discord.Guild) -> StepResult:
    """Run one step's check. Never raises: a failing check shows as INFO "could not be checked"
    instead of breaking the whole checklist."""
    try:
        return step.check(guild)
    except Exception as e:
        logging.warning(f"[SETUP] Check '{step.id}' failed for guild {getattr(guild, 'id', '?')}: {e}", exc_info=True)
        return StepResult(AuditStatus.INFO, "config_audit.summary_check_failed")


#: Statuses a guild can answer with "we don't use this". Never BROKEN: an automation that is
#: switched on but can't work must stay visible.
DISMISSABLE_STATUSES = (AuditStatus.OFF, AuditStatus.INCOMPLETE)


def dismissed_steps(guild_id: Any) -> Set[str]:
    """Step ids the guild marked "we don't use this" (CACHE.guild_setup_dismissed)."""
    from clashcontrol.cache_manager import CACHE
    return set(CACHE.guild_setup_dismissed.get(str(guild_id), set()))


def can_dismiss(step: AuditStep, result: StepResult) -> bool:
    """Whether the "we don't use this" button applies to this step right now."""
    return step.dismissable and not result.data.get("dismissed") and result.status in DISMISSABLE_STATUSES


def run_audit(guild: discord.Guild) -> Dict[str, StepResult]:
    """Every step's result for ``guild``, in registry order, with prerequisites applied: a step
    whose prerequisite is not set up shows BLOCKED (one root cause, one red line). A step the
    guild dismissed shows OFF while it would be OFF/INCOMPLETE; OK and BROKEN show as they are
    (configured after all / switched on but broken)."""
    results: Dict[str, StepResult] = {}
    dismissed = dismissed_steps(guild.id)
    for step in STEPS:
        result = run_step(step, guild)
        if step.id in dismissed:
            if result.status in DISMISSABLE_STATUSES:
                result = StepResult(AuditStatus.OFF, "config_audit.summary_dismissed",
                                    findings=result.findings, data=result.data)
            result.data["dismissed"] = True
        # Switched off: nothing to block (a disabled feature stays ⏸️).
        if result.status != AuditStatus.OFF and any(
            results.get(req) is not None and results[req].status in _BLOCKING for req in step.requires
        ):
            result = StepResult(AuditStatus.BLOCKED, "config_audit.summary_blocked",
                                {"steps": ", ".join(str(STEPS_BY_ID[r].order) for r in step.requires)},
                                result.findings, result.data)
        results[step.id] = result
    return results
