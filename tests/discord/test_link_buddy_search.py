# pyright: reportArgumentType=false, reportPrivateUsage=false
"""Link Buddy name search (2026-09-23 live report: "Zim" and "gen" were refused).

The search list came from war data only (get_player_list(use_live_api=False)), so a clan member
who never fought a recorded war could not be found by name. It now includes the guild clans'
current members; and a hit that is already watched / an own account gets its proper message
instead of "not found".
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

GUILD_ID = 987654321
USER_ID = "123"


@pytest.fixture
def buddy_env(monkeypatch):
    """Guild with one clan; returns (get_player_list mock, user_data, persist mock)."""
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.cache_manager import CACHE

    user_data: dict[str, Any] = {
        "players": [{"player_tag": "#OWN1", "player_name": "Qaplop"}],
        "watched_players": [{"player_tag": "#WATCHED1", "player_name": "Qaplop jr."}],
    }
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "user_accounts", {USER_ID: user_data})
    monkeypatch.setattr(CACHE, "get_player", AsyncMock(return_value=None))
    persist = AsyncMock()
    monkeypatch.setattr(CACHE, "persist_user", persist)
    monkeypatch.setattr(helper, "get_guild_clans_including_member_config", lambda _gid: ["#CLAN1"])
    monkeypatch.setattr(helper, "format_notification_settings", lambda *a, **k: "settings")
    player_list = AsyncMock(return_value=[
        {"tag": "#ZIM1", "name": "Zim"},  # current member, no war history
        {"tag": "#OWN1", "name": "Qaplop"},
        {"tag": "#WATCHED1", "name": "Qaplop jr."},
    ])
    monkeypatch.setattr(helper, "get_player_list", player_list)
    return player_list, user_data, persist


def _modal(query: str):
    from qapbot.ui_notifications import LinkBuddyModal

    modal = LinkBuddyModal(user_id=USER_ID, parent_view=MagicMock(), original_interaction=None)
    modal.guild_id = GUILD_ID
    cast(discord.ui.TextInput, modal.search_input.component)._value = query
    return modal


def _interaction() -> AsyncMock:
    interaction = AsyncMock()
    interaction.guild_id = GUILD_ID
    interaction.user = SimpleNamespace(id=int(USER_ID), display_name="Qaplop")
    return interaction


def _sent(interaction: AsyncMock) -> str:
    return interaction.followup.send.call_args.args[0]


@pytest.mark.discord
@pytest.mark.asyncio
async def test_current_clan_member_without_war_history_is_found_by_name(buddy_env):
    player_list, user_data, persist = buddy_env

    await _modal("zim").on_submit(_interaction())

    assert player_list.call_args.kwargs["use_live_api"] is True  # current members are searched
    assert {"player_tag": "#ZIM1", "player_name": "Zim"} in user_data["watched_players"]
    persist.assert_awaited_once()


@pytest.mark.discord
@pytest.mark.asyncio
async def test_searching_an_own_account_says_so_instead_of_not_found(buddy_env):
    from qapbot.i18n import t

    _, user_data, persist = buddy_env
    interaction = _interaction()

    await _modal("qaplop").on_submit(interaction)  # matches "Qaplop" and "Qaplop jr." — both taken

    assert _sent(interaction) != t('warnotifications.buddy_not_found', user_id=USER_ID, guild_id=GUILD_ID, player_tag="qaplop")
    assert _sent(interaction) == t('warnotifications.buddy_own_account', user_id=USER_ID, guild_id=GUILD_ID, player_tag="#OWN1")
    persist.assert_not_awaited()
    assert len(user_data["watched_players"]) == 1


@pytest.mark.discord
@pytest.mark.asyncio
async def test_searching_an_already_watched_buddy_says_so(buddy_env):
    from qapbot.i18n import t

    _, _, persist = buddy_env
    interaction = _interaction()

    await _modal("jr.").on_submit(interaction)

    assert _sent(interaction) == t('warnotifications.buddy_already_added', user_id=USER_ID, guild_id=GUILD_ID, player_tag="#WATCHED1")
    persist.assert_not_awaited()


@pytest.mark.discord
@pytest.mark.asyncio
async def test_unknown_name_still_reports_not_found(buddy_env):
    from qapbot.i18n import t

    _, _, persist = buddy_env
    interaction = _interaction()

    await _modal("nobody").on_submit(interaction)

    assert _sent(interaction) == t('warnotifications.buddy_not_found', user_id=USER_ID, guild_id=GUILD_ID, player_tag="nobody")
    persist.assert_not_awaited()
