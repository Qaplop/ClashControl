"""Configured guilds the bot is no longer in (tracker #0150 follow-up, Build 136).

The 2026-10-10 permission audit showed two guilds with stored automations the bot had been
removed from; every cycle kept serving them. Policy: keep the config, pause the automations,
log once. These tests pin the presence tri-state (unknown before the guild list is loaded!),
the skips, the logging, the audit section, and the plain-text report formatting.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import dataclasses
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
import ClashControl  # noqa: E402
import QBcore  # noqa: E402
from clashcontrol import guild_presence as gp  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402
from clashcontrol.log_throttle import reset_log_throttle  # noqa: E402

PRESENT = 111
GONE = 1537876277988429834


def _bot(ready: bool = True, present=(PRESENT,)) -> MagicMock:
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=ready)
    bot.get_guild = MagicMock(side_effect=lambda gid: SimpleNamespace(id=gid) if gid in present else None)
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(side_effect=AssertionError("must not be called for an absent guild"))
    return bot


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(CACHE, "persist_server_config", AsyncMock())
    monkeypatch.setattr(ClashControl, "CONFIG", dataclasses.replace(ClashControl.CONFIG, is_dev_mode=False))
    reset_log_throttle()
    yield
    reset_log_throttle()


class TestPresence:
    def test_unknown_until_ready(self, monkeypatch):
        monkeypatch.setattr(QBcore, "bot", _bot(ready=False))
        assert gp.bot_guild_presence(GONE) is None
        assert gp.is_guild_absent(GONE) is False  # never pause on "unknown"

    def test_present_and_absent(self, monkeypatch):
        monkeypatch.setattr(QBcore, "bot", _bot())
        assert gp.bot_guild_presence(PRESENT) is True
        assert gp.bot_guild_presence(str(GONE)) is False
        assert gp.bot_guild_presence("not-an-id") is None

    def test_absent_logs_once_then_rearms_on_join(self, monkeypatch, caplog):
        monkeypatch.setattr(QBcore, "bot", _bot())
        with caplog.at_level(logging.INFO):
            for _ in range(5):
                assert gp.is_guild_absent(GONE)
            gp.mark_guild_present(GONE)
            gp.is_guild_absent(GONE)
        lines = [r for r in caplog.records if "[GUILD-ABSENT]" in r.getMessage() and r.levelno == logging.INFO]
        assert len(lines) == 2


class TestAutomationsSkip:
    @pytest.mark.asyncio
    async def test_anchored_messages_skip_absent_guild(self, monkeypatch):
        monkeypatch.setattr(QBcore, "bot", _bot())
        CACHE.server_config[str(GONE)] = {
            "registration_message_enabled": True, "registration_channel_id": "1556177199801835540",
            "registration_message_id": "1",
        }
        await ClashControl.repost_playerregistration_messages(only_if_not_bottom=True)
        QBcore.bot.fetch_channel.assert_not_awaited()
        CACHE.persist_server_config.assert_not_awaited()
        assert CACHE.server_config[str(GONE)]["registration_channel_id"] == "1556177199801835540"  # kept

    def test_raid_tracking_excludes_absent_guild(self, monkeypatch):
        from clashcontrol import QBdiscocmdshelper_cwl as cwl
        monkeypatch.setattr(QBcore, "bot", _bot())
        CACHE.server_config[str(PRESENT)] = {}
        CACHE.server_config[str(GONE)] = {}
        monkeypatch.setattr(cwl, "resolve_guild_member_clan_tags",
                            lambda gid: ["#P"] if gid == PRESENT else ["#G"])
        assert cwl.all_member_clan_tags() == ["#P"]
        assert cwl.guild_ids_for_member_clan("#G") == []


class TestReconciliationLogging:
    def test_startup_lists_absent_configured_guilds(self, monkeypatch, caplog):
        monkeypatch.setattr(QBcore, "bot", _bot())
        CACHE.server_config[str(GONE)] = {"registration_message_enabled": True, "channel_war_notifications_enabled": True}
        CACHE.subscriptions["1400585784347594882"] = {"5": [], "6": []}
        CACHE.server_config[str(PRESENT)] = {"registration_message_enabled": True}
        with caplog.at_level(logging.WARNING):
            absent = gp.log_absent_configured_guilds()
        assert absent == sorted([str(GONE), "1400585784347594882"])
        msg = next(r.getMessage() for r in caplog.records if "[GUILD-ABSENT]" in r.getMessage())
        assert "registration, war reminders" in msg and "2 subscription channel(s)" in msg

    def test_nothing_reported_while_unknown(self, monkeypatch):
        monkeypatch.setattr(QBcore, "bot", _bot(ready=False))
        CACHE.server_config[str(GONE)] = {"registration_message_enabled": True}
        assert gp.log_absent_configured_guilds() == []

    @pytest.mark.asyncio
    async def test_on_guild_remove_keeps_config(self, caplog):
        CACHE.server_config[str(GONE)] = {"registration_message_enabled": True}
        guild = SimpleNamespace(id=GONE, name="Odyssey COC")
        with caplog.at_level(logging.WARNING):
            await QBcore.on_guild_remove(guild)
        assert any("[GUILD-REMOVE]" in r.getMessage() and "config kept" in r.getMessage() for r in caplog.records)
        assert CACHE.server_config[str(GONE)] == {"registration_message_enabled": True}

    @pytest.mark.asyncio
    async def test_on_guild_join_logs_administrator_and_restored_config(self, caplog):
        CACHE.server_config[str(GONE)] = {"channel_war_notifications_enabled": True}
        me = SimpleNamespace(guild_permissions=discord.Permissions(administrator=True))
        guild = SimpleNamespace(id=GONE, name="Odyssey COC", owner_id=5, me=me)
        with caplog.at_level(logging.INFO):
            await QBcore.on_guild_join(guild)
        msg = next(r.getMessage() for r in caplog.records if "[GUILD-JOIN]" in r.getMessage())
        assert "Administrator: yes" in msg and "active again" in msg and "war reminders" in msg


class TestAuditReport:
    def test_lists_absent_configured_servers_and_plain_names(self, monkeypatch):
        from clashcontrol import bot_permissions as bp
        monkeypatch.setattr(QBcore, "bot", _bot())
        ch = MagicMock(spec=discord.TextChannel)
        ch.id = 77
        ch.name = "leaderboards"
        ch.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
        me = SimpleNamespace(guild_permissions=discord.Permissions(), top_role=SimpleNamespace(name="Bots", position=4))
        guild = SimpleNamespace(
            id=PRESENT, name="INTL BootCamp", owner_id=9, me=me, system_channel=None,
            get_channel_or_thread=lambda cid: ch if cid == 77 else None,
            get_channel=lambda cid: ch if cid == 77 else None, get_role=lambda rid: None,
        )
        CACHE.subscriptions[str(PRESENT)] = {"77": [{"clan_tag": "#2JQUJPVQU"}]}
        CACHE.server_config[str(GONE)] = {"registration_message_enabled": True}
        report, _, total = bp.build_audit_report([guild])
        assert total == 1
        assert "#leaderboards (77)" in report and "<#" not in report and "**" not in report
        assert "missing at server level: " in report and "Embed Links" in report
        assert "no longer in (1)" in report and str(GONE) in report and "registration" in report
