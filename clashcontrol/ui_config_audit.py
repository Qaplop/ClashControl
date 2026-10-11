"""``/setup`` — the server configuration checklist and guided setup (tracker #0151).

One ephemeral message, edited in place (Cardinal Rule 7): the overview lists every audit step of
``clashcontrol.config_audit.STEPS`` with its status; the select opens a step's detail page; Back
returns to the overview; Re-check runs the checks again. "Start guided setup" walks the steps in
order (Phase 6), with the member-side flows of ``clashcontrol.tutorial_flows`` on steps 5, 6 and
11. Navigation is read-only and answers with ONE edit; the only writes ("we don't use this" /
"use it after all") claim the view first (claim_action).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import discord

from clashcontrol.config_audit import (
    STATUS_ICON, STEPS, STEPS_BY_ID, AuditStatus, StepResult, can_dismiss, run_audit, summary_text,
)
from clashcontrol.i18n import t  # type: ignore[attr-defined]
from clashcontrol.ui_common import claim_action, release_action

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
        summary = summary_text(r, gid, user_id)
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
    """A step's detail page. A blocked step shows its description and what it waits for."""
    gid = guild.id
    step = STEPS_BY_ID[step_id]
    title = f"{STATUS_ICON[result.status]} {step.order}. {_tr(gid, user_id, step.title_key)}"
    summary = summary_text(result, gid, user_id)
    embed = discord.Embed(title=title[:256], color=_status_color(result.status))
    if result.status == AuditStatus.BLOCKED:
        embed.description = (f"{_tr(gid, user_id, step.desc_key)}\n\n**{summary}**")[:_EMBED_DESCRIPTION_LIMIT]
        return embed
    detail = step.detail(guild, result, gid, user_id)
    if detail.problem is not None:
        # Problem first (qaplop 2026-10-11): the dominant block at the top — what's wrong and the
        # numbered fix — and the information about the step below it.
        heading, body = detail.problem
        embed.description = f"## {heading}\n{body}"[:_EMBED_DESCRIPTION_LIMIT]
        if detail.sections:
            embed.add_field(name="\u200b", value=f"**{_tr(gid, user_id, 'config_audit.about_title')}**", inline=False)
    else:
        enabled = step.toggle_enabled(gid)
        if enabled is None:
            embed.description = f"**{summary}**"
        else:
            # An on/off feature: /clan management's own status line (one "off", not two). When
            # on, the summary (where it posts) follows.
            embed.description = toggle_status_line(gid, user_id, enabled) + (f"\n**{summary}**" if enabled else "")
    for heading, body in detail.sections:
        embed.add_field(name=(heading or "\u200b")[:256], value=(body or "\u200b")[:1024], inline=False)
    return embed


def toggle_status_line(guild_id: int, user_id: str, enabled: bool) -> str:
    """'Status: 🟢 Enabled' / 'Status: 🔴 Disabled' — the texts /clan management's Basic
    Configuration uses for the same switches."""
    state = _tr(guild_id, user_id, "ui_components.basic_config.config_status_enabled" if enabled
                else "ui_components.basic_config.config_status_disabled")
    return _tr(guild_id, user_id, "ui_components.basic_config.config_welcome_status",
               status=f"{'🟢' if enabled else '🔴'} {state}")


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


#: Guided-setup page after the last step.
_DONE = "__done__"

#: Member-side flows per step (guided setup, Q-11): (button key, tutorial_flows function name).
_MEMBER_FLOWS: Dict[str, List[Tuple[str, str]]] = {
    "registration": [("config_audit.guided.button_link_account", "open_link_account"),
                     ("config_audit.guided.button_my_accounts", "open_my_accounts")],
    "war_notifications": [("config_audit.guided.button_war_reminders", "open_war_reminders")],
    "cwl_player_hub": [("config_audit.guided.button_cwl_preferences", "open_cwl_preferences")],
}


def needs_attention(result: StepResult) -> bool:
    """A step the guided setup should stop at: broken, incomplete, blocked, or an optional
    feature not in use that the guild hasn't dismissed."""
    if result.status in (AuditStatus.BROKEN, AuditStatus.INCOMPLETE, AuditStatus.BLOCKED):
        return True
    return result.status == AuditStatus.OFF and not result.data.get("dismissed")


class SetupView(discord.ui.View):
    """``/setup``: overview ⇄ step detail (``selected``), and the guided setup (``guided``)
    that walks the steps in order. One ephemeral message, every click answers with one edit."""

    def __init__(self, guild: discord.Guild, user_id: str, results: Dict[str, StepResult],
                 selected: Optional[str] = None) -> None:
        super().__init__(timeout=SETUP_VIEW_TIMEOUT_SECONDS)
        self.guild = guild
        self.user_id = user_id
        self.results = results
        self.selected = selected
        self.guided = False
        # #0121: the admin tutorial builds on the member tutorial — a soft tip on the first
        # guided page for admins who haven't finished /tutorial (never a block).
        self._tutorial_hint = False
        self._last_interaction: Optional[discord.Interaction] = None
        self._rebuild()

    # -- rendering ---------------------------------------------------------------------------

    def current_embed(self) -> discord.Embed:
        gid = self.guild.id
        if self.selected is None:
            return build_overview_embed(self.guild, self.user_id, self.results)
        if self.selected == _DONE:
            open_steps = sum(needs_attention(r) for r in self.results.values())
            key = "config_audit.guided.done_open" if open_steps else "config_audit.guided.done_all"
            return discord.Embed(
                title=_tr(gid, self.user_id, "config_audit.guided.done_title")[:256],
                description=_tr(gid, self.user_id, key, count=open_steps)[:_EMBED_DESCRIPTION_LIMIT],
                color=discord.Color.orange() if open_steps else discord.Color.green(),
            )
        embed = build_step_embed(self.guild, self.user_id, self.selected, self.results[self.selected])
        if self.guided:
            step = STEPS_BY_ID[self.selected]
            embed.set_author(name=_tr(gid, self.user_id, "config_audit.guided.header",
                                      n=step.order, total=len(STEPS))[:256])
            result = self.results[self.selected]
            if self.selected in _MEMBER_FLOWS and result.status != AuditStatus.BLOCKED:
                embed.add_field(name=_tr(gid, self.user_id, "config_audit.guided.member_view_title")[:256],
                                value=_tr(gid, self.user_id, f"config_audit.guided.member_view_{self.selected}")[:1024],
                                inline=False)
            if result.status == AuditStatus.OK:
                embed.add_field(name="\u200b", value=_tr(gid, self.user_id, "config_audit.guided.ok_next")[:1024],
                                inline=False)
            if self._tutorial_hint:
                from clashcontrol.QBdiscocmdshelper import command_mention
                from clashcontrol.tutorial import server_ready_for_tutorial
                # Without a member clan the tour has nothing to show yet: clan first (step 3).
                key = ("config_audit.guided.tutorial_hint" if server_ready_for_tutorial(gid)
                       else "config_audit.guided.tutorial_hint_no_clans")
                # At the top of the page (qaplop 2026-10-11) — read before the step, not after it.
                tip = _tr(gid, self.user_id, key, tutorial=command_mention("tutorial"))
                embed.description = f"{tip}\n\n{embed.description or ''}"[:_EMBED_DESCRIPTION_LIMIT]
        return embed

    def _button(self, key: str, callback: Any, custom_id: str, row: int,
                style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=_tr(self.guild.id, self.user_id, key)[:80], style=style, custom_id=custom_id, row=row,
        )
        button.callback = callback  # type: ignore[assignment]
        self.add_item(button)

    def _rebuild(self) -> None:
        """Re-create the components for the current page (Rule 8: the select's current entry is
        marked default=True, since string selects have no default_values)."""
        self.clear_items()
        if self.guided:
            self._rebuild_guided()
            return
        gid = self.guild.id
        options = []
        for step in STEPS:
            r = self.results[step.id]
            options.append(discord.SelectOption(
                label=f"{step.order}. {_tr(gid, self.user_id, step.title_key)}"[:100],
                value=step.id,
                description=summary_text(r, gid, self.user_id)[:100],
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
            self._button("config_audit.button_back", self._on_back, "setup_back", 1)
        self._button("config_audit.button_recheck", self._on_recheck, "setup_recheck", 1, discord.ButtonStyle.primary)
        if self.selected is None:
            self._button("config_audit.guided.button_start", self._on_start_guided, "setup_guided_start", 1,
                         discord.ButtonStyle.success)
            return
        self._add_step_actions(row=1, dismiss_row=2)

    def _rebuild_guided(self) -> None:
        index = self._guided_index()
        self._button("config_audit.guided.button_previous", self._on_previous, "setup_guided_prev", 0)
        if self.selected != _DONE:
            self._button("config_audit.guided.button_next", self._on_next, "setup_guided_next", 0,
                         discord.ButtonStyle.primary)
        self._button("config_audit.button_recheck", self._on_recheck, "setup_recheck", 0)
        self._button("config_audit.guided.button_overview", self._on_back, "setup_back", 0)
        if index == 0:
            self.children[0].disabled = True  # type: ignore[attr-defined]  # no step before step 1
        if self.selected == _DONE:
            return
        self._add_step_actions(row=1, dismiss_row=1)
        result = self.results[self.selected]  # type: ignore[index]
        if result.status != AuditStatus.BLOCKED:
            for n, (key, _flow) in enumerate(_MEMBER_FLOWS.get(self.selected or "", [])):
                self._button(key, self._make_flow_callback(n), f"setup_flow_{n}", 2, discord.ButtonStyle.success)

    def _add_step_actions(self, row: int, dismiss_row: int) -> None:
        """"Open settings", "we don't use this" / "use it after all", and the detail page's link
        buttons (step 1's re-authorize link) for the selected step."""
        if self._fix_mode() is not None:
            self._button("config_audit.button_open_settings", self._on_open_settings, "setup_open_settings", row,
                         discord.ButtonStyle.success)
        step = STEPS_BY_ID[self.selected]  # type: ignore[index]
        result = self.results[self.selected]  # type: ignore[index]
        if result.data.get("dismissed"):
            self._button("config_audit.button_undismiss", self._on_undismiss, "setup_undismiss", dismiss_row)
        elif can_dismiss(step, result):
            self._button("config_audit.button_dismiss", self._on_dismiss, "setup_dismiss", dismiss_row,
                         discord.ButtonStyle.danger)
        if result.status != AuditStatus.BLOCKED:
            for label, url in step.detail(self.guild, result, self.guild.id, self.user_id).links:
                self.add_item(discord.ui.Button(label=label[:80], url=url, row=row))

    def _guided_index(self) -> int:
        """Position of the guided page: 0..len(STEPS)-1, or len(STEPS) for the finish page."""
        if self.selected == _DONE:
            return len(STEPS)
        return next((i for i, s in enumerate(STEPS) if s.id == self.selected), 0)

    def _fix_mode(self) -> Optional[str]:
        """The /clan management mode for the selected step, or None (overview, finish page, no
        settings screen, or a step that is blocked)."""
        if self.selected is None or self.selected == _DONE:
            return None
        step = STEPS_BY_ID[self.selected]
        result = self.results[self.selected]
        if step.fix_mode is None or result.status == AuditStatus.BLOCKED:
            return None
        return step.fix_mode(result)

    async def _show(self, interaction: discord.Interaction) -> None:
        self._last_interaction = interaction
        self._rebuild()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    # -- callbacks: navigation -----------------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []  # type: ignore[union-attr]
        self.selected = str(values[0]) if values else None
        await self._show(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.selected = None
        self.guided = False
        await self._show(interaction)

    async def _on_recheck(self, interaction: discord.Interaction) -> None:
        self.results = run_audit(self.guild)
        _log_audit(self.guild, self.user_id, self.results, "Re-check")
        await self._show(interaction)

    async def _on_start_guided(self, interaction: discord.Interaction) -> None:
        """Start at the first step that needs attention (nothing is stored — the checks say where
        the guild stands), or at step 1 when everything is fine: then it is a tour."""
        from clashcontrol.tutorial import is_finished
        self.results = run_audit(self.guild)
        self.guided = True
        self._tutorial_hint = not is_finished(self.user_id)
        self.selected = next((s.id for s in STEPS if needs_attention(self.results[s.id])), STEPS[0].id)
        logging.info(f"[SETUP] Guided setup started at '{self.selected}' in guild {self.guild.id} by user {self.user_id}")
        await self._show(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self._tutorial_hint = False
        index = self._guided_index() + 1
        self.selected = STEPS[index].id if index < len(STEPS) else _DONE
        await self._show(interaction)

    async def _on_previous(self, interaction: discord.Interaction) -> None:
        self._tutorial_hint = False
        index = max(self._guided_index() - 1, 0)
        self.selected = STEPS[index].id
        await self._show(interaction)

    # -- callbacks: actions ----------------------------------------------------------------------

    async def _on_open_settings(self, interaction: discord.Interaction) -> None:
        """Open the /clan management panel in the step's mode as a separate ephemeral message.
        The /setup message stays as it is; "Check again" there shows the result afterwards."""
        mode = self._fix_mode()
        if mode is None:
            await interaction.response.defer()
            return
        await open_settings_panel(interaction, self.guild, mode)

    async def _on_dismiss(self, interaction: discord.Interaction) -> None:
        """"We don't use this" — stored per server (Q-12), shown as ⏸️ from now on."""
        if not await claim_action(self, interaction):
            return
        await self._set_dismissed(interaction, True)

    async def _on_undismiss(self, interaction: discord.Interaction) -> None:
        """"Use it after all" — undoes a dismissal (Q-12: needs change)."""
        if not await claim_action(self, interaction):
            return
        await self._set_dismissed(interaction, False)

    async def _set_dismissed(self, interaction: discord.Interaction, dismissed: bool) -> None:
        from clashcontrol.cache_manager import CACHE
        step_id = self.selected
        try:
            if step_id and step_id != _DONE:
                await CACHE.set_setup_step_dismissed(self.guild.id, step_id, dismissed)
                logging.info(f"[SETUP] Step '{step_id}' {'dismissed' if dismissed else 'restored'} "
                              f"in guild {self.guild.id} by user {self.user_id}")
            self.results = run_audit(self.guild)
        finally:
            release_action(self)
        await self._show(interaction)

    def _make_flow_callback(self, n: int) -> Any:
        """Callback for the n-th member flow of the selected step. The flow must be the click's
        first response (modal / own message / Activity launch), so nothing is awaited before."""
        async def _callback(interaction: discord.Interaction) -> None:
            from clashcontrol import tutorial_flows
            flows = _MEMBER_FLOWS.get(self.selected or "", [])
            if n >= len(flows):
                await interaction.response.defer()
                return
            await getattr(tutorial_flows, flows[n][1])(interaction, self.guild.id)
        return _callback

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


#: The ephemeral settings panel is edited through the webhook token of the click that opened it,
#: which dies 15 minutes later (Pitfall 41) — the panel's own buttons rebuild it in place through
#: that same token. So it is closed with a notice at 14 minutes instead of failing silently.
SETTINGS_PANEL_LIFETIME_SECONDS = 840


async def open_settings_panel(interaction: discord.Interaction, guild: discord.Guild, mode: str) -> None:
    """Send the /clan management panel (``build_clan_management_panel``) for ``mode`` as an
    ephemeral message — the same panel /clan management posts publicly — and close it before its
    token expires. Shared by /setup's detail pages and the guided setup."""
    import QBcore
    from clashcontrol.ui_clan_management import build_clan_management_panel

    user_id = str(interaction.user.id)
    gid = guild.id
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        embeds, panel = await build_clan_management_panel(guild, mode=mode, timeout=SETTINGS_PANEL_LIFETIME_SECONDS)
    except Exception as e:
        logging.warning(f"[SETUP] Could not build the {mode} panel for guild {gid}: {e}", exc_info=True)
        await interaction.followup.send(_tr(gid, user_id, "config_audit.open_failed"), ephemeral=True)
        return
    message = await interaction.followup.send(
        content=_tr(gid, user_id, "config_audit.panel_hint"), embeds=embeds, view=panel,
        ephemeral=True, wait=True,
    )
    # A follow-up comes from a partial webhook: discord.py gives it no guild (Pitfall 47). The
    # panel's refresh rebuilds itself from sent_message.guild — without one, every CWL button
    # vanished and the labels fell back to English (tracker #0153).
    if getattr(message, "guild", None) is None:
        message.guild = guild  # type: ignore[misc]
    panel.sent_message = message  # type: ignore[assignment]
    logging.info(f"[SETUP] Opened the {mode} panel for user {user_id} in guild {gid}")

    async def _expire() -> None:
        await asyncio.sleep(SETTINGS_PANEL_LIFETIME_SECONDS)
        try:
            await message.edit(content=_tr(gid, user_id, "config_audit.panel_expired"), embeds=[], view=None)
        except discord.HTTPException as e:
            logging.debug(f"[SETUP] Could not close expired {mode} panel: {e}")

    QBcore.spawn_tracked(f"setup-panel-expiry-{gid}-{user_id}", _expire())
