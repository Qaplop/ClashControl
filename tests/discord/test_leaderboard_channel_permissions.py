"""/leaderboard in a channel the bot can't post in (2026-10-18, PROD).

A slash command reaches the bot even in a channel it can't see; the board is then posted as a
normal message and Discord answers 403 (50001 Missing Access). That surfaced as an ERROR with a
traceback and an "unexpected error" reply. Now /leaderboard names the missing permission up front,
and the global handler treats any 403 — also one wrapped in our own exceptions — as a WARNING plus
an "access denied" reply.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

import QBdiscordcmds
from clashcontrol.bot_permissions import MISSING_CHANNEL_PERMS, PermissionIssue
from clashcontrol.exceptions import LeaderboardPostingError


@pytest.mark.discord
@pytest.mark.asyncio
async def test_leaderboard_names_missing_channel_permission_and_posts_nothing(mock_interaction, monkeypatch):
    monkeypatch.setattr(QBdiscordcmds, "CACHE", SimpleNamespace(
        user_accounts={}, clan_families={}, subscriptions={}, get_all_subscriptions_flat=lambda: {},
    ))
    issue = PermissionIssue("leaderboard", MISSING_CHANNEL_PERMS, 1561404687201079357, ("view_channel",))
    monkeypatch.setattr("clashcontrol.bot_permissions.check_channel_feature", lambda guild, feature, cid: [issue])
    monkeypatch.setattr("clashcontrol.bot_permissions.format_issues_message", lambda issues, **_k: "MISSING VIEW CHANNEL")
    generate = MagicMock(side_effect=AssertionError("must not build a board it cannot post"))
    monkeypatch.setattr(QBdiscordcmds, "generate_leaderboard_text", generate)

    await QBdiscordcmds.leaderboard.callback(mock_interaction, clan="#2C0QGVL09")  # type: ignore[arg-type]

    mock_interaction.followup.send.assert_awaited_once_with("MISSING VIEW CHANNEL", ephemeral=True)
    generate.assert_not_called()


def _wrapped_403() -> LeaderboardPostingError:
    forbidden = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), {"code": 50001, "message": "Missing Access"})
    try:
        try:
            raise forbidden
        except discord.Forbidden as e:
            raise LeaderboardPostingError("Error posting leaderboard for #2C0QGVL09") from e
    except LeaderboardPostingError as wrapped:
        return wrapped


@pytest.mark.discord
@pytest.mark.asyncio
async def test_wrapped_403_is_a_warning_and_access_denied(mock_interaction, caplog):
    import QBcore

    command = MagicMock()
    command.name = "leaderboard"
    mock_interaction.command = command
    error = app_commands.CommandInvokeError(command, _wrapped_403())

    with caplog.at_level(logging.DEBUG):
        await QBcore.on_app_command_error(mock_interaction, error)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("Slash command 403" in r.getMessage() and "50001" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    sent = mock_interaction.response.send_message.await_args
    assert sent.kwargs.get("ephemeral") is True
    assert "Access denied" in sent.args[0] or "access_denied" in sent.args[0]
