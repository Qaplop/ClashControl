"""Tests for /magnus (tracker #0116): replies to the invoking user with an ephemeral,
translated hello-world message, from a guild or a DM.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import QBdiscordcmds  # noqa: E402


def test_magnus_is_dm_invokable():
    assert QBdiscordcmds.magnus.guild_only is False
    assert QBdiscordcmds.magnus.name == "magnus"


@pytest.mark.discord
@pytest.mark.asyncio
async def test_magnus_replies_hello_world_ephemerally(mock_interaction):
    await QBdiscordcmds.magnus.callback(mock_interaction)  # type: ignore[arg-type]

    mock_interaction.response.send_message.assert_awaited_once()
    call = mock_interaction.response.send_message.await_args
    assert call.kwargs.get("ephemeral") is True
    assert "Hello, World!" in call.args[0]


@pytest.mark.discord
@pytest.mark.asyncio
async def test_magnus_works_from_dm(mock_interaction):
    mock_interaction.guild = None
    mock_interaction.guild_id = None

    await QBdiscordcmds.magnus.callback(mock_interaction)  # type: ignore[arg-type]

    mock_interaction.response.send_message.assert_awaited_once()
