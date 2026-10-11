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
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

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
    try:
        issues = bp.audit_guild(guild)
    except Exception as e:
        logging.warning(f"[SETUP] Permission audit failed for guild {guild.id}: {e}")
        issues = []

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
# Registry (plan §3). Steps 2–13 are listed so the checklist shows the whole structure; their
# checks come in later phases and report PENDING until then.
# ---------------------------------------------------------------------------------------------

STEPS: Tuple[AuditStep, ...] = (
    AuditStep("bot_permissions", 1, "admin", check=check_bot_permissions, detail=detail_bot_permissions),
    AuditStep("basics", 2, "admin"),
    AuditStep("clans", 3, "admin"),
    AuditStep("welcome", 4, "admin", requires=("clans",)),
    AuditStep("registration", 5, "both", requires=("clans",)),
    AuditStep("war_notifications", 6, "both", requires=("clans",)),
    AuditStep("channel_war_notifications", 7, "admin", requires=("clans",)),
    AuditStep("raid_reminders", 8, "admin", requires=("clans",)),
    AuditStep("roles", 9, "admin", requires=("clans",)),
    AuditStep("cwl_settings", 10, "admin", requires=("clans",)),
    AuditStep("cwl_player_hub", 11, "both", requires=("cwl_settings",)),
    AuditStep("cwl_season", 12, "admin", requires=("cwl_settings",)),
    AuditStep("leaderboards", 13, "admin", requires=("clans",)),
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
        if result.status != AuditStatus.PENDING and any(
            results.get(req) is not None and results[req].status in _BLOCKING for req in step.requires
        ):
            result = StepResult(AuditStatus.BLOCKED, "config_audit.summary_blocked",
                                {"steps": ", ".join(str(STEPS_BY_ID[r].order) for r in step.requires)},
                                result.findings, result.data)
        results[step.id] = result
    return results
