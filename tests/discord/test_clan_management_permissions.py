# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""/clan management in a channel the bot can't post in (tracker #0150).

The up-front permission check answers with the missing permissions and returns before
channel.send(), so the discord.Forbidden backstop never runs. The admin-facing WARNING has to be
logged on the up-front path too (DEV test #150-12, 2026-10-10: ephemeral shown, no log line).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import QBdiscordcmds
import clashcontrol.bot_permissions as bot_permissions
import clashcontrol.QBdiscocmdshelper as cmdshelper
from clashcontrol.bot_permissions import MISSING_CHANNEL_PERMS, PermissionIssue


@pytest.mark.discord
@pytest.mark.asyncio
async def test_missing_permissions_reply_is_logged_as_warning(mock_interaction, monkeypatch, caplog):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 777
    channel.send = AsyncMock(side_effect=AssertionError("must not try to post"))
    mock_interaction.channel = channel
    mock_interaction.guild = MagicMock()
    mock_interaction.guild_id = 555
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.delete_original_response = AsyncMock()

    monkeypatch.setattr(QBdiscordcmds, "_safe_defer", AsyncMock(return_value=True))
    monkeypatch.setattr(QBdiscordcmds, "CACHE", SimpleNamespace(leaderboard_messages={}))
    monkeypatch.setattr(cmdshelper, "get_guild_clans_including_member_config", lambda gid: ["#CLAN1"])
    monkeypatch.setattr(cmdshelper, "get_most_active_clan_for_guild", lambda gid, clans: "#CLAN1")
    monkeypatch.setattr(
        cmdshelper, "format_clan_management_message",
        AsyncMock(return_value=(discord.Embed(title="x"), None, [], [])),
    )
    issue = PermissionIssue("clan_management", MISSING_CHANNEL_PERMS, 777, ("send_messages", "embed_links"))
    monkeypatch.setattr(bot_permissions, "check_channel_feature", lambda guild, feature, cid: [issue])
    monkeypatch.setattr(bot_permissions, "format_issues_message", lambda issues, **kw: "MISSING PERMS")

    caplog.set_level(logging.INFO)
    await QBdiscordcmds.clan_management.callback(mock_interaction)  # type: ignore[call-arg]

    mock_interaction.followup.send.assert_awaited_once_with("MISSING PERMS", ephemeral=True)
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "[CLAN-MANAGEMENT] No permission to post" in r.getMessage()]
    assert len(warnings) == 1
    assert "channel 777" in warnings[0].getMessage()
    assert "send_messages, embed_links" in warnings[0].getMessage()


@pytest.mark.discord
@pytest.mark.asyncio
@pytest.mark.parametrize("stored_rows, expect_flag", [([SimpleNamespace(children=[1, 2])], False), ([], True)])
async def test_posted_message_logs_components_stored_by_discord(mock_interaction, monkeypatch, caplog,
                                                                stored_rows, expect_flag):
    """2026-10-10: the view was seen without buttons with nothing in the log. The command now logs
    how many components Discord stored (from the edit's response)."""
    import clashcontrol.ui_clan_management as ui_cm
    sent = MagicMock()
    sent.id = 4242
    sent.edit = AsyncMock(return_value=SimpleNamespace(components=stored_rows))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 777
    channel.send = AsyncMock(return_value=sent)
    mock_interaction.channel = channel
    mock_interaction.guild = MagicMock()
    mock_interaction.guild_id = 555
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.delete_original_response = AsyncMock()

    monkeypatch.setattr(QBdiscordcmds, "_safe_defer", AsyncMock(return_value=True))
    monkeypatch.setattr(QBdiscordcmds, "CACHE", SimpleNamespace(leaderboard_messages={}, set_leaderboard_message=AsyncMock()))
    monkeypatch.setattr(cmdshelper, "get_guild_clans_including_member_config", lambda gid: ["#CLAN1"])
    monkeypatch.setattr(cmdshelper, "get_most_active_clan_for_guild", lambda gid, clans: "#CLAN1")
    monkeypatch.setattr(cmdshelper, "format_clan_management_message",
                        AsyncMock(return_value=(discord.Embed(title="x"), None, [], [])))
    monkeypatch.setattr(bot_permissions, "check_channel_feature", lambda guild, feature, cid: [])
    monkeypatch.setattr(ui_cm, "ClanManagementView", lambda **kw: SimpleNamespace(children=[1, 2, 3]))

    caplog.set_level(logging.INFO)
    await QBdiscordcmds.clan_management.callback(mock_interaction)  # type: ignore[call-arg]

    msg = next(r.getMessage() for r in caplog.records if "[CLAN-MANAGEMENT] Posted message 4242" in r.getMessage())
    assert "3 component(s) sent" in msg
    assert ("NO COMPONENTS STORED" in msg) is expect_flag
    assert any("[CMD-DONE] /clan management" in r.getMessage() for r in caplog.records)
