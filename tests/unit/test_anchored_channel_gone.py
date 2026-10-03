"""Anchored message whose channel disappeared (tracker #0150).

From 2026-09-28 PROD logged "Could not find registration channel ... for guild ..." ~280 times a
day: the registration channel of one guild was deleted, `get_channel()` returned None every bump
cycle, and nothing ever resolved it. Now a cache miss is confirmed with `fetch_channel()`:
NotFound clears the channel from the guild config once; anything inconclusive keeps the config
(Pitfall 14) and warns at most once a day. `on_guild_channel_delete()` clears it immediately.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import dataclasses
import logging
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
import ClashControl  # noqa: E402
import QBcore  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402
from clashcontrol.constants import ANCHORED_MESSAGE_CHANNEL_KEYS  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "persist_server_config", AsyncMock())
    monkeypatch.setattr(ClashControl, "CONFIG", dataclasses.replace(ClashControl.CONFIG, is_dev_mode=False))
    ClashControl._anchored_unreachable_warned.clear()
    yield
    ClashControl._anchored_unreachable_warned.clear()


def _registration_config(channel_id: str = "1436391672400576663") -> dict:
    return {
        "registration_message_enabled": True,
        "registration_channel_id": channel_id,
        "registration_message_id": "1553727525723705468",
    }


def _bot(fetch_side_effect=None, fetch_return=None) -> MagicMock:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)  # always a cache miss
    bot.fetch_channel = AsyncMock(side_effect=fetch_side_effect, return_value=fetch_return)
    return bot


@pytest.mark.asyncio
async def test_confirmed_deleted_channel_is_cleared_once(monkeypatch, caplog):
    monkeypatch.setattr(QBcore, "bot", _bot(fetch_side_effect=discord.NotFound(MagicMock(status=404), "gone")))
    CACHE.server_config["1400585784347594882"] = _registration_config()

    with caplog.at_level(logging.WARNING):
        await ClashControl.repost_playerregistration_messages(only_if_not_bottom=True)
        await ClashControl.repost_playerregistration_messages(only_if_not_bottom=True)

    cfg = CACHE.server_config["1400585784347594882"]
    assert cfg["registration_channel_id"] is None
    assert cfg["registration_message_id"] is None
    assert cfg["registration_message_enabled"] is True  # re-picking a channel restores it
    CACHE.persist_server_config.assert_awaited_once_with("1400585784347594882")
    gone = [r for r in caplog.records if "ANCHORED-CHANNEL-GONE" in r.getMessage()]
    assert len(gone) == 1  # second cycle: no channel configured -> silently skipped
    assert QBcore.bot.fetch_channel.await_count == 1


@pytest.mark.asyncio
async def test_forbidden_keeps_config_and_warns_once_per_day(monkeypatch, caplog):
    monkeypatch.setattr(QBcore, "bot", _bot(fetch_side_effect=discord.Forbidden(MagicMock(status=403), "no access")))
    CACHE.server_config["900"] = _registration_config("123")

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await ClashControl.repost_playerregistration_messages(only_if_not_bottom=True)

    assert CACHE.server_config["900"]["registration_channel_id"] == "123"
    CACHE.persist_server_config.assert_not_awaited()
    warnings = [r for r in caplog.records if "Could not reach registration channel 123" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_cache_gap_uses_the_fetched_channel(monkeypatch):
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    channel.guild.name = "G"
    channel.name = "registration"
    new_message = MagicMock()
    new_message.id = 4242
    channel.send = AsyncMock(return_value=new_message)
    old_message = MagicMock()
    old_message.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=old_message)
    monkeypatch.setattr(QBcore, "bot", _bot(fetch_return=channel))
    monkeypatch.setattr(
        "clashcontrol.QBdiscocmdshelper.get_playerregistration_message", lambda *a, **k: "hello"
    )
    CACHE.server_config["901"] = _registration_config("456")

    await ClashControl.repost_playerregistration_messages()

    channel.send.assert_awaited_once()
    assert CACHE.server_config["901"]["registration_channel_id"] == "456"
    assert CACHE.server_config["901"]["registration_message_id"] == "4242"


@pytest.mark.asyncio
async def test_channel_delete_event_clears_every_anchored_feature_in_that_channel(monkeypatch):
    CACHE.server_config["910"] = {
        "registration_channel_id": "777", "registration_message_id": "1",
        "cwl_management_channel_id": "777", "cwl_management_message_id": "2",
        "cwl_player_hub_channel_id": "888", "cwl_player_hub_message_id": "3",
    }
    monkeypatch.setattr(CACHE, "get_channel_subscriptions", lambda _cid: [])
    monkeypatch.setattr(CACHE, "remove_channel_subscriptions", AsyncMock(return_value=0))
    monkeypatch.setattr(CACHE, "leaderboard_messages", {})
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 777
    channel.name = "welcome"
    channel.guild = MagicMock()
    channel.guild.id = 910
    channel.guild.name = "G"

    await QBcore.on_guild_channel_delete(channel)

    cfg = CACHE.server_config["910"]
    assert cfg["registration_channel_id"] is None and cfg["registration_message_id"] is None
    assert cfg["cwl_management_channel_id"] is None and cfg["cwl_management_message_id"] is None
    assert cfg["cwl_player_hub_channel_id"] == "888"  # different channel, untouched
    CACHE.persist_server_config.assert_awaited_once_with("910")


@pytest.mark.asyncio
async def test_channel_delete_event_ignores_unrelated_channels(monkeypatch):
    CACHE.server_config["911"] = _registration_config("555")
    monkeypatch.setattr(CACHE, "get_channel_subscriptions", lambda _cid: [])
    monkeypatch.setattr(CACHE, "remove_channel_subscriptions", AsyncMock(return_value=0))
    monkeypatch.setattr(CACHE, "leaderboard_messages", {})
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    channel.name = "other"
    channel.guild = MagicMock()
    channel.guild.id = 911
    channel.guild.name = "G"

    await QBcore.on_guild_channel_delete(channel)

    assert CACHE.server_config["911"]["registration_channel_id"] == "555"
    CACHE.persist_server_config.assert_not_awaited()


def test_key_table_matches_the_repost_wrappers():
    """ANCHORED_MESSAGE_CHANNEL_KEYS (used by on_guild_channel_delete) must name exactly the keys
    the three repost_* wrappers pass to repost_anchored_message()."""
    src = Path(ClashControl.__file__).read_text(encoding="utf-8")
    calls = re.findall(
        r'log_label="([^"]+)",\s*enabled_key="[^"]+",\s*channel_key="([^"]+)",\s*'
        r'message_id_key="([^"]+)",\s*old_channel_key="([^"]+)"',
        src,
    )
    assert len(calls) == 3
    assert set(calls) == set(ANCHORED_MESSAGE_CHANNEL_KEYS)
