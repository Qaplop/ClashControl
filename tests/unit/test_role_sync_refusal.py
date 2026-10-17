"""Role sync and roles the bot can never touch.

2026-10-16 (PROD): a server picked the bot's own integration role "ClashControl" for its role
system. Every sync then tried to add it, Discord answered 403, and the log got 292 identical
ERRORs in 17 h. Now the doomed call is skipped up front and the refusal is logged once a day.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from clashcontrol.bot_permissions import role_assign_blocker
from clashcontrol.log_throttle import reset_log_throttle


def _guild_and_me(manage_roles: bool = True):
    guild = MagicMock()
    guild.id = 42
    guild.name = "Breachers"
    me = MagicMock(spec=discord.Member)
    me.guild_permissions = discord.Permissions(manage_roles=manage_roles)
    me.top_role = 10
    guild.me = me
    return guild


def _role(guild, *, managed: bool = False, position: int = 5) -> MagicMock:
    role = MagicMock()
    role.id = 777
    role.name = "ClashControl"
    role.guild = guild
    role.managed = managed
    role.is_default = MagicMock(return_value=False)
    role.__ge__ = lambda self, other: position >= other
    return role


def _member(guild, roles=()):
    member = MagicMock()
    member.guild = guild
    member.id = 1
    member.display_name = "someone"
    member.roles = list(roles)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture(autouse=True)
def _fresh_throttle():
    reset_log_throttle()
    yield
    reset_log_throttle()


class TestRoleAssignBlocker:
    def test_assignable_role(self):
        assert role_assign_blocker(_role(_guild_and_me())) is None

    def test_managed_role(self):
        assert "integration" in role_assign_blocker(_role(_guild_and_me(), managed=True))

    def test_missing_manage_roles(self):
        assert role_assign_blocker(_role(_guild_and_me(manage_roles=False))) == "missing: manage_roles"

    def test_role_above_bot(self):
        assert "highest role" in role_assign_blocker(_role(_guild_and_me(), position=10))

    def test_bot_member_not_cached_lets_discord_decide(self):
        guild = _guild_and_me()
        guild.me = None
        assert role_assign_blocker(_role(guild)) is None


class TestAssignSkipsBlockedRole:
    @pytest.mark.asyncio
    async def test_managed_role_never_reaches_discord_and_logs_once(self, caplog):
        from clashcontrol.guild_role_manager import assign_role_to_member
        guild = _guild_and_me()
        role = _role(guild, managed=True)
        member = _member(guild)
        with caplog.at_level(logging.DEBUG):
            for _ in range(5):
                assert await assign_role_to_member(member, role) is False
        member.add_roles.assert_not_awaited()
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING and "[ROLE-SYNC]" in r.getMessage()]
        assert len(loud) == 1
        msg = loud[0].getMessage()
        assert "Breachers (42)" in msg and "'ClashControl' (777)" in msg and "integration" in msg
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.asyncio
    async def test_remove_skips_blocked_role(self):
        from clashcontrol.guild_role_manager import remove_role_from_member
        guild = _guild_and_me(manage_roles=False)
        role = _role(guild)
        member = _member(guild, roles=[role])
        assert await remove_role_from_member(member, role) is False
        member.remove_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_403_is_throttled_warning(self, caplog):
        from clashcontrol.guild_role_manager import assign_role_to_member
        guild = _guild_and_me()
        role = _role(guild)
        member = _member(guild)
        member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "nope"))
        with caplog.at_level(logging.DEBUG):
            for _ in range(3):
                assert await assign_role_to_member(member, role) is False
        assert member.add_roles.await_count == 3
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING and "[ROLE-SYNC]" in r.getMessage()]
        assert len(loud) == 1 and "403" in loud[0].getMessage()
