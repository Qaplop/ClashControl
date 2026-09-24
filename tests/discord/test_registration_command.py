"""Tracker #0129 — /registration and the DM server it resolves to.

- QBdiscordcmds.registration: ephemeral hub in a server; a normal DM message for a shared
  server in the bot DM (directly for one, via the DM server picker for several).
- QBdiscocmdshelper.get_dm_registration_guild_ids(): shared servers that have clans.
- QBdiscocmdshelper.get_interaction_guild(): interaction.guild, or the recorded DM server.
- RegistrationView._resolve_guild_id() + link_account_button in the DM.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture()
def pending(monkeypatch):
    from qapbot.cache_manager import CACHE

    fresh: dict = {}
    monkeypatch.setattr(CACHE, "pending_registration_dm_guild", fresh)
    return fresh


def _guild(guild_id: int, name: str = "Clan Server", member_ids=()):
    guild = MagicMock()
    guild.id = guild_id
    guild.name = name
    guild.get_member = MagicMock(side_effect=lambda uid: MagicMock() if uid in member_ids else None)
    return guild


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

@pytest.mark.discord
@pytest.mark.asyncio
async def test_registration_in_server_sends_ephemeral_hub(mock_interaction, pending):
    import QBdiscordcmds
    from qapbot.ui_registration import RegistrationView

    await QBdiscordcmds.registration.callback(mock_interaction)  # type: ignore[arg-type]

    mock_interaction.response.send_message.assert_awaited_once()
    kwargs = mock_interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], RegistrationView)
    assert kwargs["view"].guild_id == mock_interaction.guild.id
    assert pending == {}  # a server invocation never records a DM server


@pytest.mark.discord
@pytest.mark.asyncio
async def test_registration_in_dm_without_shared_server_explains(mock_interaction, monkeypatch, pending):
    import QBdiscordcmds
    import qapbot.QBdiscocmdshelper as helper

    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [])
    mock_interaction.guild = None

    await QBdiscordcmds.registration.callback(mock_interaction)  # type: ignore[arg-type]

    mock_interaction.response.send_message.assert_awaited_once()
    args, kwargs = mock_interaction.response.send_message.await_args
    assert "/registration" in args[0]
    assert kwargs.get("ephemeral") is True
    assert "view" not in kwargs


@pytest.mark.discord
@pytest.mark.asyncio
async def test_registration_in_dm_with_one_server_posts_normal_message(mock_interaction, monkeypatch, pending):
    import QBdiscordcmds
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_registration import RegistrationView

    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [777])
    mock_interaction.guild = None
    mock_interaction.user.id = 555
    mock_interaction.client.get_guild = MagicMock(return_value=_guild(777, "Home Base"))

    await QBdiscordcmds.registration.callback(mock_interaction)  # type: ignore[arg-type]

    args, kwargs = mock_interaction.response.send_message.await_args
    assert "Home Base" in args[0]  # the hub greets with the chosen server's name
    assert kwargs.get("ephemeral") is not True  # stays in the DM like the server hub
    assert isinstance(kwargs["view"], RegistrationView) and kwargs["view"].guild_id == 777
    assert pending == {"555": 777}


@pytest.mark.discord
@pytest.mark.asyncio
async def test_registration_in_dm_with_several_servers_posts_after_pick(mock_interaction, monkeypatch, pending):
    import QBdiscordcmds
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_registration import RegistrationView

    captured = {}

    async def fake_picker(interaction, guild_ids, on_pick=None):
        captured["guild_ids"] = guild_ids
        captured["on_pick"] = on_pick
        return None

    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [111, 222])
    monkeypatch.setattr(helper, "_prompt_dm_guild_picker", fake_picker)
    mock_interaction.guild = None
    mock_interaction.user.id = 555
    mock_interaction.client.get_guild = MagicMock(return_value=_guild(222, "Second"))

    await QBdiscordcmds.registration.callback(mock_interaction)  # type: ignore[arg-type]

    assert captured["guild_ids"] == [111, 222]
    mock_interaction.followup.send.assert_not_awaited()  # nothing posted before the pick

    pick_interaction = AsyncMock()
    await captured["on_pick"](pick_interaction, 222)

    pick_interaction.response.edit_message.assert_awaited_once()  # picker closes
    args, kwargs = mock_interaction.followup.send.await_args
    assert "Second" in args[0]
    assert kwargs.get("ephemeral") is not True
    assert isinstance(kwargs["view"], RegistrationView) and kwargs["view"].guild_id == 222
    assert pending == {"555": 222}


@pytest.mark.discord
def test_registration_is_listed_in_help_and_dm_capable():
    import QBdiscordcmds

    assert "registration" in QBdiscordcmds._get_help_command_names()
    assert "registration" not in QBdiscordcmds._get_help_server_only_commands(is_dm=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.discord
def test_dm_registration_guild_ids_needs_membership_and_clans(monkeypatch):
    import qapbot.QBdiscocmdshelper as helper

    clans = {1: ["#A"], 2: [], 3: ["#C"]}
    monkeypatch.setattr(helper, "get_guild_clans_including_member_config", lambda gid: clans[gid])
    client = MagicMock()
    client.guilds = [_guild(1, member_ids={9}), _guild(2, member_ids={9}), _guild(3, member_ids=())]

    # 1: member + clans; 2: member but no clans; 3: clans but not a member
    assert helper.get_dm_registration_guild_ids(client, 9) == [1]


@pytest.mark.discord
def test_get_interaction_guild_prefers_interaction_guild(pending):
    from qapbot.QBdiscocmdshelper import get_interaction_guild

    interaction = MagicMock()
    pending[str(interaction.user.id)] = 999
    assert get_interaction_guild(interaction) is interaction.guild


@pytest.mark.discord
def test_get_interaction_guild_in_dm_uses_recorded_server(pending):
    from qapbot.QBdiscocmdshelper import get_interaction_guild

    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = 555
    recorded = _guild(777)
    interaction.client.get_guild = MagicMock(return_value=recorded)

    assert get_interaction_guild(interaction) is None  # nothing recorded yet
    pending["555"] = 777
    assert get_interaction_guild(interaction) is recorded
    interaction.client.get_guild.assert_called_with(777)


@pytest.mark.discord
def test_resolve_guild_id_in_dm_message_own_server_wins_and_is_recorded(pending):
    from qapbot.ui_registration import RegistrationView

    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = 555
    pending["555"] = 111  # a newer /registration for another server

    assert RegistrationView(guild_id=222)._resolve_guild_id(interaction) == 222
    assert pending["555"] == 222  # role steps now agree with this message's server


@pytest.mark.discord
def test_resolve_guild_id_in_dm_after_restart(monkeypatch, pending):
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_registration import RegistrationView

    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = 555
    view = RegistrationView()  # the generic add_view() instance after a restart

    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [333])
    assert view._resolve_guild_id(interaction) == 333  # exactly one shared server: re-resolved
    assert pending["555"] == 333

    pending.clear()
    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [333, 444])
    assert view._resolve_guild_id(interaction) == 0  # ambiguous: caller asks for /registration


@pytest.mark.discord
@pytest.mark.asyncio
async def test_link_button_in_dm_without_server_asks_to_rerun(monkeypatch, pending):
    import qapbot.QBdiscocmdshelper as helper
    from qapbot.ui_registration import RegistrationView

    monkeypatch.setattr(helper, "get_dm_registration_guild_ids", lambda _client, _uid: [])
    interaction = MagicMock()
    interaction.guild = None
    interaction.user.id = 555
    interaction.response = AsyncMock()

    view = RegistrationView()
    button = next(c for c in view.children if getattr(c, "custom_id", "") == "registration_link_account")
    await button.callback(interaction)

    args, kwargs = interaction.response.send_message.await_args
    assert "/registration" in args[0]
    assert kwargs.get("ephemeral") is True
    interaction.response.send_modal.assert_not_awaited()
