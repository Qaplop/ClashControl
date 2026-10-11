"""/tutorial screens (tracker #0121): the step pages and the "continue" button the bot posts after
a member ran the command a step asked for. Logic and storybook: clashcontrol/tutorial.py.

One ephemeral message edited in place (Cardinal Rule 7). Back / Next / Start over persist the
position, so they claim the view first (claim_action) — a double click must not skip a step.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord

from clashcontrol.i18n import t  # type: ignore[attr-defined]
from clashcontrol.tutorial import (
    STEP_IDS, STEPS, STEPS_BY_ID, TutorialStep, get_progress, is_step_done, next_step_id,
    resolve_tutorial_guild, save_progress,
)
from clashcontrol.ui_common import claim_action, release_action

#: Below the 15-minute interaction-token lifetime (Pitfall 41); every click answers with an edit.
TUTORIAL_VIEW_TIMEOUT_SECONDS = 600


def _mentions() -> Dict[str, str]:
    """Clickable commands for the step texts (unused placeholders are simply ignored)."""
    from clashcontrol.QBdiscocmdshelper import command_mention
    names = {"help": "help", "registration": "registration", "whois": "whois", "leaderboard": "leaderboard",
             "cwl_preferences": "cwl preferences", "about": "about", "tutorial": "tutorial",
             "subscriptions": "subscriptions", "setup": "setup"}
    return {key: command_mention(cmd) for key, cmd in names.items()}


def build_tutorial_embed(step_id: str, user_id: str, guild_id: Optional[int],
                         welcome_back: bool = False) -> discord.Embed:
    step = STEPS_BY_ID[step_id]
    n = STEP_IDS.index(step_id) + 1

    def tr(key: str, **kw: Any) -> str:
        return t(key, guild_id=guild_id, user_id=user_id, **kw)

    mentions = _mentions()
    text = tr(step.text_key, **mentions)
    if welcome_back:
        text = tr("tutorial.welcome_back", n=n) + "\n\n" + text
    embed = discord.Embed(title=f"{step.emoji} {tr(step.title_key)}"[:256], description=text[:4096],
                          color=discord.Color.blurple())
    embed.set_author(name=tr("tutorial.header", n=n, total=len(STEPS))[:256])
    if step_id not in (STEPS[0].id, STEPS[-1].id):
        if is_step_done(step, user_id):
            embed.add_field(name=tr("tutorial.task_title"), value=tr("tutorial.task_done")[:1024], inline=False)
        else:
            embed.add_field(name=tr("tutorial.task_title"), value=tr(step.task_key, **mentions)[:1024], inline=False)
    return embed


def step_done_text(step: TutorialStep, interaction: discord.Interaction) -> str:
    """The reaction after a member ran the command a step asked for."""
    guild_id = interaction.guild.id if interaction.guild else None
    user_id = str(interaction.user.id)
    return t("tutorial.step_completed", guild_id=guild_id, user_id=user_id,
             title=t(step.title_key, guild_id=guild_id, user_id=user_id))


class TutorialView(discord.ui.View):
    """Step pages: Back / Next (Finish) / Start over, plus the step's real flows."""

    def __init__(self, user_id: str, guild_id: Optional[int], step_id: str, welcome_back: bool = False) -> None:
        super().__init__(timeout=TUTORIAL_VIEW_TIMEOUT_SECONDS)
        self.user_id = user_id
        self.guild_id = guild_id
        self.step_id = step_id
        self.welcome_back = welcome_back
        self._last_interaction: Optional[discord.Interaction] = None
        self._rebuild()

    def current_embed(self) -> discord.Embed:
        return build_tutorial_embed(self.step_id, self.user_id, self.guild_id, self.welcome_back)

    def _tr(self, key: str, **kw: Any) -> str:
        return t(key, guild_id=self.guild_id, user_id=self.user_id, **kw)

    def _button(self, key: str, callback: Any, custom_id: str, row: int,
                style: discord.ButtonStyle = discord.ButtonStyle.secondary, disabled: bool = False) -> None:
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=self._tr(key)[:80], style=style, custom_id=custom_id, row=row, disabled=disabled,
        )
        button.callback = callback  # type: ignore[assignment]
        self.add_item(button)

    def _rebuild(self) -> None:
        self.clear_items()
        index = STEP_IDS.index(self.step_id)
        last = index == len(STEP_IDS) - 1
        self._button("tutorial.button_back", self._on_back, "tutorial_back", 0, disabled=index == 0)
        if not last:
            key = "tutorial.button_start" if index == 0 else (
                "tutorial.button_finish" if index == len(STEP_IDS) - 2 else "tutorial.button_next")
            self._button(key, self._on_next, "tutorial_next", 0, discord.ButtonStyle.primary)
        self._button("tutorial.button_restart", self._on_restart, "tutorial_restart", 0)
        for n, (key, _flow) in enumerate(STEPS_BY_ID[self.step_id].flows):
            self._button(key, self._make_flow_callback(n), f"tutorial_flow_{n}", 1, discord.ButtonStyle.success)

    async def _show(self, interaction: discord.Interaction) -> None:
        self._last_interaction = interaction
        self.welcome_back = False
        self._rebuild()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go(self, interaction: discord.Interaction, step_id: str, mark_done: bool, reset: bool = False) -> None:
        progress = get_progress(self.user_id) or {}
        done = set() if reset else set(progress.get("done", set()))
        if mark_done:
            done.add(self.step_id)
        try:
            await save_progress(self.user_id, step_id, done)
            self.step_id = step_id
        finally:
            release_action(self)
        await self._show(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        """Next step. A step without a task (welcome) or with a satisfied state check counts as
        done; a command step only when the command was actually used (on_command_completed)."""
        if not await claim_action(self, interaction):
            return
        step = STEPS_BY_ID[self.step_id]
        done = step.command is None and (step.done_check is None or step.done_check(self.user_id))
        await self._go(interaction, next_step_id(self.step_id), mark_done=done)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not await claim_action(self, interaction):
            return
        await self._go(interaction, STEP_IDS[max(STEP_IDS.index(self.step_id) - 1, 0)], mark_done=False)

    async def _on_restart(self, interaction: discord.Interaction) -> None:
        """Start over (#0121): back to step 1 with nothing marked done."""
        if not await claim_action(self, interaction):
            return
        logging.info(f"[TUTORIAL] user {self.user_id} started over")
        await self._go(interaction, STEP_IDS[0], mark_done=False, reset=True)

    def _make_flow_callback(self, n: int) -> Any:
        """Open the n-th real screen of this step. It must be the click's first response."""
        async def _callback(interaction: discord.Interaction) -> None:
            from clashcontrol import tutorial_flows
            flows = STEPS_BY_ID[self.step_id].flows
            if n >= len(flows):
                await interaction.response.defer()
                return
            guild_id = resolve_tutorial_guild(interaction)
            if guild_id is None:
                from clashcontrol.QBdiscocmdshelper import command_mention
                await interaction.response.send_message(
                    self._tr("tutorial.pick_server", registration=command_mention("registration")), ephemeral=True,
                )
                return
            await getattr(tutorial_flows, flows[n][1])(interaction, guild_id)
        return _callback

    async def on_timeout(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[attr-defined]
        if self._last_interaction is not None:
            try:
                await self._last_interaction.edit_original_response(view=self)
            except discord.HTTPException as e:
                logging.debug(f"[TUTORIAL] Could not disable expired tutorial view: {e}")


class ContinueTutorialView(discord.ui.View):
    """The "continue" button under the reaction to a completed command step."""

    def __init__(self, interaction: discord.Interaction) -> None:
        super().__init__(timeout=TUTORIAL_VIEW_TIMEOUT_SECONDS)
        guild_id = interaction.guild.id if interaction.guild else None
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=t("tutorial.button_continue", guild_id=guild_id, user_id=str(interaction.user.id))[:80],
            style=discord.ButtonStyle.primary, custom_id="tutorial_continue",
        )
        button.callback = self._on_continue  # type: ignore[assignment]
        self.add_item(button)

    async def _on_continue(self, interaction: discord.Interaction) -> None:
        await send_tutorial(interaction)


async def send_tutorial(interaction: discord.Interaction) -> None:
    """Open the tutorial where the user left off (new users at step 1, finished ones on the last
    page with "Start over"). Sent as the interaction's first response."""
    user_id = str(interaction.user.id)
    guild_id = interaction.guild.id if interaction.guild else None
    progress = get_progress(user_id)
    if progress is None or progress.get("step") not in STEP_IDS:
        await save_progress(user_id, STEP_IDS[0], set())
        step_id, welcome_back = STEP_IDS[0], False
    else:
        step_id = progress["step"]
        welcome_back = step_id not in (STEP_IDS[0], STEP_IDS[-1])
    view = TutorialView(user_id, guild_id, step_id, welcome_back)
    view._last_interaction = interaction  # pyright: ignore[reportPrivateUsage]
    await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)
    logging.info(f"[TUTORIAL] user {user_id} opened the tutorial at '{step_id}'")
