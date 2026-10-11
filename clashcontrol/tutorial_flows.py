"""Member-side flows shared by /setup's guided setup and the planned /tutorial (tracker #0151 /
#0121): link an account, show my accounts, my war reminders, my CWL preferences.

Each flow runs the REAL existing screen — the registration hub's buttons (RegistrationView) or
the CWL preferences Activity — never a copy of it, so the guided setup shows an admin exactly what
their members see, and a change to those screens reaches the tutorials automatically (qaplop,
2026-10-11: "similar functionality in one shared function, no code redundancy").

Every function must be the FIRST response to the interaction (a button click): the registration
buttons open modals or send their own ephemeral messages, and the Activity launch
(LAUNCH_ACTIVITY) is only valid as a first response.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import discord

from clashcontrol.cache_manager import CACHE


def user_has_linked_accounts(user_id: str) -> bool:
    """True when the Discord user has at least one Clash account linked."""
    players: List[Dict[str, Any]] = (CACHE.user_accounts.get(user_id) or {}).get("players") or []
    return bool(players)


async def _registration_button(interaction: discord.Interaction, guild_id: int, button: str) -> None:
    """Run one RegistrationView button for this click, behind the same gate the hub itself uses
    (interaction_check: startup/maintenance guard + user metadata update)."""
    from clashcontrol.ui_registration import RegistrationView

    view = RegistrationView(guild_id)
    try:
        if not await view.interaction_check(interaction):
            return
        await getattr(view, button).callback(interaction)
    finally:
        view.stop()  # never sent: nothing to dispatch to it afterwards


async def open_link_account(interaction: discord.Interaction, guild_id: int) -> None:
    """The registration hub's "Link Account" flow (player search, or verify/link choice)."""
    logging.info(f"[TUTORIAL-FLOW] link_account user={interaction.user.id} guild={guild_id}")
    await _registration_button(interaction, guild_id, "link_account_button")


async def open_my_accounts(interaction: discord.Interaction, guild_id: int) -> None:
    """The registration hub's "My Accounts" overview."""
    logging.info(f"[TUTORIAL-FLOW] my_accounts user={interaction.user.id} guild={guild_id}")
    await _registration_button(interaction, guild_id, "my_accounts_button")


async def open_war_reminders(interaction: discord.Interaction, guild_id: int) -> None:
    """The registration hub's "War Notifications" settings for the clicking user."""
    logging.info(f"[TUTORIAL-FLOW] war_reminders user={interaction.user.id} guild={guild_id}")
    await _registration_button(interaction, guild_id, "war_notifications_button")


async def open_cwl_preferences(interaction: discord.Interaction, guild_id: int) -> None:
    """The "Your CWL Preferences" Activity, exactly as the player hub button / /cwl preferences
    open it."""
    from clashcontrol.ui_cwl_roster import _launch_cwl_activity  # pyright: ignore[reportPrivateUsage]  # shared on purpose

    logging.info(f"[TUTORIAL-FLOW] cwl_preferences user={interaction.user.id} guild={guild_id}")
    if interaction.guild is None:
        # In the DM the Activity has no server of its own; it fetches this one (GET
        # /api/cwl/dm-guild), exactly as /cwl preferences records it there (#0128).
        CACHE.pending_cwl_dm_guild[str(interaction.user.id)] = guild_id
    await _launch_cwl_activity(interaction, guild_id, "player_prefs")
