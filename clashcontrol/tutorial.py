"""/tutorial — the member tutorial (tracker #0121, plans/implemented/tracker-0151-setup-config-audit.md §4.2).

A storybook of steps that climbs from simple to more involved: what ClashControl is, /help,
linking an account, My Accounts, war reminders, /whois, /leaderboard, CWL preferences. A step can
ask the member to run a command; when they do (``on_command_completed``, called from QBcore's
on_app_command_completion) the bot reacts right there and offers to continue — #0121's
"enter the command, get the reaction, resume with the next step".

Progress is stored per user and track (``user_tutorial_progress``), so a member can stop and come
back with /tutorial, or start over. The admin side is /setup's guided setup (#0151 Phase 6), whose
state comes from the server's configuration itself and needs no stored progress.

The member-side screens are never re-implemented here: steps open them through
``clashcontrol.tutorial_flows`` (shared with /setup).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import discord

from clashcontrol.cache_manager import CACHE

TRACK_USER = "user"

#: A started tutorial counts as active (reacts to commands) this long after the last progress.
ACTIVE_FOR = timedelta(days=7)


@dataclass(frozen=True)
class TutorialStep:
    id: str
    emoji: str
    #: Command (qualified name, as /help lists it) whose use completes this step, or None.
    command: Optional[str] = None
    #: (button text key, tutorial_flows function) — the real screens this step opens.
    flows: Tuple[Tuple[str, str], ...] = ()
    #: State that shows the step is done without a command (e.g. an account is linked).
    done_check: Optional[Callable[[str], bool]] = None

    @property
    def title_key(self) -> str:
        return f"tutorial.step.{self.id}.title"

    @property
    def text_key(self) -> str:
        return f"tutorial.step.{self.id}.text"

    @property
    def task_key(self) -> str:
        return f"tutorial.step.{self.id}.task"


def _has_account(user_id: str) -> bool:
    from clashcontrol.tutorial_flows import user_has_linked_accounts
    return user_has_linked_accounts(user_id)


def _war_reminders_on(user_id: str) -> bool:
    settings = (CACHE.user_accounts.get(user_id) or {}).get("notification_settings") or {}
    return bool(settings.get("war_reminders"))


#: The member storybook, in order (simple → more involved). Text: tutorial.step.<id>.*
STEPS: Tuple[TutorialStep, ...] = (
    TutorialStep("welcome", "👋"),
    TutorialStep("help", "📖", command="help"),
    TutorialStep("link_account", "🔗", flows=(("tutorial.button_link_account", "open_link_account"),),
                 done_check=_has_account),
    TutorialStep("my_accounts", "📋", flows=(("tutorial.button_my_accounts", "open_my_accounts"),)),
    TutorialStep("war_reminders", "🔔", flows=(("tutorial.button_war_reminders", "open_war_reminders"),),
                 done_check=_war_reminders_on),
    TutorialStep("whois", "🔍", command="whois"),
    TutorialStep("leaderboard", "📊", command="leaderboard"),
    TutorialStep("cwl_preferences", "🛡️", command="cwl preferences",
                 flows=(("tutorial.button_cwl_preferences", "open_cwl_preferences"),)),
    TutorialStep("done", "🎉"),
)
STEP_IDS: List[str] = [s.id for s in STEPS]
STEPS_BY_ID: Dict[str, TutorialStep] = {s.id: s for s in STEPS}


# ---------------------------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------------------------

def get_progress(user_id: str, track: str = TRACK_USER) -> Optional[Dict[str, Any]]:
    """{"step": id, "done": set of ids, "updated_at": iso} or None if never started."""
    return (CACHE.tutorial_progress.get(str(user_id)) or {}).get(track)


def is_step_done(step: TutorialStep, user_id: str) -> bool:
    progress = get_progress(user_id) or {}
    if step.id in progress.get("done", set()):
        return True
    return bool(step.done_check and step.done_check(user_id))


def is_finished(user_id: str) -> bool:
    progress = get_progress(user_id)
    return bool(progress and progress.get("step") == STEPS[-1].id)


async def save_progress(user_id: str, step_id: str, done: Optional[set] = None,  # type: ignore[type-arg]
                        track: str = TRACK_USER) -> None:
    """Persist where the user is (write-through: DB, then CACHE)."""
    progress = get_progress(user_id, track) or {}
    done_set = set(progress.get("done", set())) if done is None else set(done)
    await CACHE.set_tutorial_progress(str(user_id), track, step_id, done_set)


async def mark_done(user_id: str, step_id: str) -> None:
    progress = get_progress(user_id) or {"step": step_id, "done": set()}
    await save_progress(user_id, progress.get("step") or step_id, set(progress.get("done", set())) | {step_id})


def dm_hint_key(user_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The tutorial line of the bot's reply to a free-text DM (tracker #0122): (key, params) —
    "new here?" before the first start, "you're at step N" while under way, None once finished."""
    progress = get_progress(user_id)
    if progress is None or progress.get("step") not in STEP_IDS:
        return "commands.dm.fallback_tutorial_hint", {}
    if is_finished(user_id):
        return None
    return "commands.dm.fallback_tutorial_continue_hint", {
        "step": STEP_IDS.index(progress["step"]) + 1, "total": len(STEP_IDS)}


def next_step_id(step_id: str) -> str:
    i = STEP_IDS.index(step_id) if step_id in STEP_IDS else 0
    return STEP_IDS[min(i + 1, len(STEP_IDS) - 1)]


def _is_active(progress: Optional[Dict[str, Any]]) -> bool:
    if not progress or progress.get("step") == STEPS[-1].id:
        return False
    try:
        updated = datetime.fromisoformat(str(progress.get("updated_at")))
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated <= ACTIVE_FOR


# ---------------------------------------------------------------------------------------------
# "Run this command" detection
# ---------------------------------------------------------------------------------------------

async def on_command_completed(interaction: discord.Interaction, command: Any) -> None:
    """Called for every completed app command. When the user's active tutorial step asks for
    exactly this command, mark it done, move on, and react in the command's own conversation with
    a "continue" button. Never raises — a tutorial hiccup must not touch the command itself."""
    try:
        user_id = str(interaction.user.id)
        progress = get_progress(user_id)
        if not _is_active(progress):
            return
        step = STEPS_BY_ID.get(progress.get("step", ""))  # type: ignore[union-attr]
        name = getattr(command, "qualified_name", None) or getattr(command, "name", "")
        if step is None or step.command is None or name != step.command:
            return
        await _complete_step(interaction, user_id, progress, step, f"/{name}")  # type: ignore[arg-type]
    except Exception as e:  # e.g. a command whose response type allows no followup
        logging.debug(f"[TUTORIAL] completion reaction for /{getattr(command, 'name', '?')} skipped: {e}")


async def on_flow_completed(interaction: discord.Interaction) -> None:
    """Called after a member finished, for themselves, one of the real flows a tutorial step
    waits for — linking an account, switching war reminders on. Those are buttons, not app
    commands, so on_command_completed never sees them (qaplop 2026-10-11: step 3 didn't move on
    after linking). When the active step's state check now passes: done, move on, and the same
    "continue" prompt. The interaction must have been responded to. Never raises."""
    try:
        user_id = str(interaction.user.id)
        progress = get_progress(user_id)
        if not _is_active(progress):
            return
        step = STEPS_BY_ID.get(progress.get("step", ""))  # type: ignore[union-attr]
        if step is None or step.done_check is None or not step.done_check(user_id):
            return
        await _complete_step(interaction, user_id, progress, step, "flow")  # type: ignore[arg-type]
    except Exception as e:
        logging.debug(f"[TUTORIAL] completion reaction after a flow skipped: {e}")


async def _complete_step(interaction: discord.Interaction, user_id: str, progress: Dict[str, Any],
                         step: TutorialStep, via: str) -> None:
    """Mark ``step`` done, move on, and answer with the "✅ … done! Ready for the next step?"
    prompt, which replaces the step page it answers (one tutorial message at a time)."""
    await save_progress(user_id, next_step_id(step.id), set(progress.get("done", set())) | {step.id})
    logging.info(f"[TUTORIAL] user {user_id} completed '{step.id}' via {via}")
    from clashcontrol.ui_tutorial import ContinueTutorialView, replace_open_message, step_done_text
    message = await interaction.followup.send(
        step_done_text(step, interaction), view=ContinueTutorialView(interaction), ephemeral=True, wait=True,
    )
    await replace_open_message(user_id, message.delete)   # the step page it answers goes away


def resolve_tutorial_guild(interaction: discord.Interaction) -> Optional[int]:
    """The server the member-side flows act for: this server, or in the DM the server the user
    picked for /registration, or the only server they share with the bot that has clans."""
    if interaction.guild is not None:
        return interaction.guild.id
    user_id = str(interaction.user.id)
    pending = CACHE.pending_registration_dm_guild.get(user_id)
    if pending is not None:
        return int(pending)
    from clashcontrol.QBdiscocmdshelper import get_dm_registration_guild_ids
    guild_ids = get_dm_registration_guild_ids(interaction.client, interaction.user.id)
    return guild_ids[0] if len(guild_ids) == 1 else None
