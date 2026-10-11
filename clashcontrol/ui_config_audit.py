"""``/setup`` — the server configuration checklist (tracker #0151).

One ephemeral message, edited in place (Cardinal Rule 7): the overview lists every audit step of
``clashcontrol.config_audit.STEPS`` with its status; the select opens a step's detail page; Back
returns to the overview; Re-check runs the checks again. Everything is read-only, so the handlers
need no double-click claim: each one computes synchronously and answers with ONE edit.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord

from clashcontrol.config_audit import (
    STATUS_ICON, STEPS, STEPS_BY_ID, AuditStatus, StepResult, run_audit,
)
from clashcontrol.i18n import t  # type: ignore[attr-defined]

#: Below Discord's 15-minute interaction-token lifetime (Pitfall 41): the timeout restarts on every
#: click, and every click answers with an edit, so the latest token is always still valid.
SETUP_VIEW_TIMEOUT_SECONDS = 600

_EMBED_DESCRIPTION_LIMIT = 4096


def _tr(guild_id: int, user_id: str, key: str, **kw: Any) -> str:
    return t(key, guild_id=guild_id, user_id=user_id, **kw)


def build_overview_embed(guild: discord.Guild, user_id: str, results: Dict[str, StepResult]) -> discord.Embed:
    """The checklist: one line per step — icon, number, title, one-line summary."""
    gid = guild.id
    lines = []
    for step in STEPS:
        r = results[step.id]
        title = _tr(gid, user_id, step.title_key)
        summary = _tr(gid, user_id, r.summary_key, **r.summary_params)
        if r.status == AuditStatus.PENDING:
            lines.append(f"{STATUS_ICON[r.status]} {step.order}. {title} — *{summary}*")
        else:
            lines.append(f"{STATUS_ICON[r.status]} **{step.order}. {title}** — {summary}")
    description = _tr(gid, user_id, "config_audit.overview_intro") + "\n\n" + "\n".join(lines)
    embed = discord.Embed(
        title=_tr(gid, user_id, "config_audit.overview_title", guild=guild.name)[:256],
        description=description[:_EMBED_DESCRIPTION_LIMIT],
        color=_overall_color(results),
    )
    embed.set_footer(text=_tr(gid, user_id, "config_audit.legend")[:2048])
    return embed


def build_step_embed(guild: discord.Guild, user_id: str, step_id: str, result: StepResult) -> discord.Embed:
    """A step's detail page. Steps without a renderer yet show their description and "not
    checked yet"."""
    gid = guild.id
    step = STEPS_BY_ID[step_id]
    title = f"{STATUS_ICON[result.status]} {step.order}. {_tr(gid, user_id, step.title_key)}"
    summary = _tr(gid, user_id, result.summary_key, **result.summary_params)
    embed = discord.Embed(title=title[:256], color=_status_color(result.status))
    if step.detail is None or result.status in (AuditStatus.PENDING, AuditStatus.BLOCKED):
        embed.description = (f"{_tr(gid, user_id, step.desc_key)}\n\n**{summary}**")[:_EMBED_DESCRIPTION_LIMIT]
        return embed
    detail = step.detail(guild, result, gid, user_id)
    embed.description = f"**{summary}**"
    for heading, body in detail.sections:
        embed.add_field(name=(heading or "​")[:256], value=(body or "​")[:1024], inline=False)
    return embed


def _status_color(status: AuditStatus) -> discord.Color:
    if status == AuditStatus.OK:
        return discord.Color.green()
    if status == AuditStatus.BROKEN:
        return discord.Color.red()
    if status in (AuditStatus.INCOMPLETE, AuditStatus.BLOCKED):
        return discord.Color.orange()
    return discord.Color.light_grey()


def _overall_color(results: Dict[str, StepResult]) -> discord.Color:
    statuses = {r.status for r in results.values()}
    if AuditStatus.BROKEN in statuses:
        return discord.Color.red()
    if statuses & {AuditStatus.INCOMPLETE, AuditStatus.BLOCKED}:
        return discord.Color.orange()
    return discord.Color.green()


def _log_audit(guild: discord.Guild, user_id: str, results: Dict[str, StepResult], what: str) -> None:
    counts: Dict[str, int] = {}
    for r in results.values():
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    logging.info(f"[SETUP] {what} for guild {guild.name} ({guild.id}) by user {user_id}: {counts}")


class SetupView(discord.ui.View):
    """Overview ⇄ detail navigation of ``/setup``. ``selected`` None = overview."""

    def __init__(self, guild: discord.Guild, user_id: str, results: Dict[str, StepResult],
                 selected: Optional[str] = None) -> None:
        super().__init__(timeout=SETUP_VIEW_TIMEOUT_SECONDS)
        self.guild = guild
        self.user_id = user_id
        self.results = results
        self.selected = selected
        self._last_interaction: Optional[discord.Interaction] = None
        self._rebuild()

    # -- rendering ---------------------------------------------------------------------------

    def current_embed(self) -> discord.Embed:
        if self.selected is None:
            return build_overview_embed(self.guild, self.user_id, self.results)
        return build_step_embed(self.guild, self.user_id, self.selected, self.results[self.selected])

    def _rebuild(self) -> None:
        """Re-create the components for the current page (Rule 8: the select's current entry is
        marked default=True, since string selects have no default_values)."""
        self.clear_items()
        gid = self.guild.id
        options = []
        for step in STEPS:
            r = self.results[step.id]
            options.append(discord.SelectOption(
                label=f"{step.order}. {_tr(gid, self.user_id, step.title_key)}"[:100],
                value=step.id,
                description=_tr(gid, self.user_id, r.summary_key, **r.summary_params)[:100],
                emoji=STATUS_ICON[r.status],
                default=(step.id == self.selected),
            ))
        select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=_tr(gid, self.user_id, "config_audit.select_placeholder")[:150],
            options=options, custom_id="setup_step_select", row=0,
        )
        select.callback = self._on_select  # type: ignore[assignment]
        self.add_item(select)

        if self.selected is not None:
            back: discord.ui.Button[Any] = discord.ui.Button(
                label=_tr(gid, self.user_id, "config_audit.button_back")[:80],
                style=discord.ButtonStyle.secondary, custom_id="setup_back", row=1,
            )
            back.callback = self._on_back  # type: ignore[assignment]
            self.add_item(back)
        recheck: discord.ui.Button[Any] = discord.ui.Button(
            label=_tr(gid, self.user_id, "config_audit.button_recheck")[:80],
            style=discord.ButtonStyle.primary, custom_id="setup_recheck", row=1,
        )
        recheck.callback = self._on_recheck  # type: ignore[assignment]
        self.add_item(recheck)

        # Link buttons the step's detail page offers (e.g. step 1's re-authorize link).
        if self.selected is not None:
            step = STEPS_BY_ID[self.selected]
            result = self.results[self.selected]
            if step.detail is not None and result.status not in (AuditStatus.PENDING, AuditStatus.BLOCKED):
                for label, url in step.detail(self.guild, result, gid, self.user_id).links:
                    self.add_item(discord.ui.Button(label=label[:80], url=url, row=1))

    async def _show(self, interaction: discord.Interaction) -> None:
        self._last_interaction = interaction
        self._rebuild()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    # -- callbacks -----------------------------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []  # type: ignore[union-attr]
        self.selected = str(values[0]) if values else None
        await self._show(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.selected = None
        await self._show(interaction)

    async def _on_recheck(self, interaction: discord.Interaction) -> None:
        self.results = run_audit(self.guild)
        _log_audit(self.guild, self.user_id, self.results, "Re-check")
        await self._show(interaction)

    async def on_timeout(self) -> None:
        """Grey out the components; the checklist itself stays readable."""
        for item in self.children:
            if hasattr(item, "disabled") and not getattr(item, "url", None):
                item.disabled = True  # type: ignore[attr-defined]
        if self._last_interaction is not None:
            try:
                await self._last_interaction.edit_original_response(view=self)
            except discord.HTTPException as e:
                logging.debug(f"[SETUP] Could not disable expired /setup view: {e}")


async def send_setup_overview(interaction: discord.Interaction) -> None:
    """Run the audit for the interaction's guild and send the checklist ephemerally. The caller
    has deferred the interaction (ephemeral) and checked the admin permission."""
    guild = interaction.guild
    assert guild is not None
    user_id = str(interaction.user.id)
    results = run_audit(guild)
    _log_audit(guild, user_id, results, "Audit")
    view = SetupView(guild, user_id, results)
    view._last_interaction = interaction  # pyright: ignore[reportPrivateUsage]  # its token edits the followup
    await interaction.followup.send(embed=view.current_embed(), view=view, ephemeral=True)
