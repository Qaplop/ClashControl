"""A role configured for two jobs must not flip every cycle.

PROD 2026-10-16..19: Breachers uses one Discord role as both member_role_id and
coc_role_member_id. For every elder/co-leader the CoC step removed it ("rank is not Member") and
the member-role step re-added it ("registered clan member") — twice per 5-minute cycle, ~2,900
role changes a day across 7 members, plus occasional Discord 503s. Spartans shares its member role
with a clan role and its newbie role with the CoC "Member" role. The sync now collects every
system's want/don't-want first and applies them once; a role any system wants is kept.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _role(role_id: int, name: str) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    return role


def _guild(roles: List[MagicMock]) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1560355189674999818
    guild.name = "Breachers"
    guild.roles = roles
    guild.get_role = MagicMock(side_effect=lambda rid: next((r for r in roles if r.id == rid), None))
    return guild


def _member(guild: MagicMock, roles: List[MagicMock]) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 42
    member.display_name = "Star"
    member.guild = guild
    member.roles = list(roles)

    async def _add(role, reason=None):
        member.roles.append(role)

    async def _remove(role, reason=None):
        member.roles.remove(role)

    member.add_roles = AsyncMock(side_effect=_add)
    member.remove_roles = AsyncMock(side_effect=_remove)
    guild.get_member = MagicMock(return_value=member)
    return member


def _cache(config: dict, players: list) -> MagicMock:
    cache = MagicMock()
    cache.server_config = {"G1": config}
    cache.user_accounts = {"42": {"players": players}}
    cache.clan_families = {}
    cache.get_guild_guest_clan_tags = MagicMock(return_value=[])
    return cache


async def _sync_twice(monkeypatch, guild, cache) -> None:
    from clashcontrol.guild_role_manager import sync_roles_for_user
    monkeypatch.setattr("clashcontrol.cache_manager.CACHE", cache)
    await sync_roles_for_user(guild, "G1", 42)
    await sync_roles_for_user(guild, "G1", 42)


@pytest.mark.asyncio
async def test_breachers_elder_keeps_the_shared_member_role(monkeypatch):
    shared, elder = _role(111, "CoC Member"), _role(222, "CoC Elder")
    guild = _guild([shared, elder])
    member = _member(guild, [shared, elder])
    cache = _cache(
        {"coc_role_enabled": True, "role_system_enabled": True, "coc_role_member_id": "111",
         "coc_role_elder_id": "222", "member_role_id": "111", "member_clans": ["#C1"]},
        [{"coc_role": "elder", "current_clan_tag": "#C1"}],
    )

    await _sync_twice(monkeypatch, guild, cache)

    member.remove_roles.assert_not_awaited()
    member.add_roles.assert_not_awaited()
    assert set(member.roles) == {shared, elder}


@pytest.mark.asyncio
async def test_new_elder_gets_both_roles_once_and_then_nothing_changes(monkeypatch):
    shared, elder = _role(111, "CoC Member"), _role(222, "CoC Elder")
    guild = _guild([shared, elder])
    member = _member(guild, [])
    cache = _cache(
        {"coc_role_enabled": True, "role_system_enabled": True, "coc_role_member_id": "111",
         "coc_role_elder_id": "222", "member_role_id": "111", "member_clans": ["#C1"]},
        [{"coc_role": "elder", "current_clan_tag": "#C1"}],
    )

    await _sync_twice(monkeypatch, guild, cache)

    assert member.add_roles.await_count == 2  # elder + shared member role, each once
    member.remove_roles.assert_not_awaited()
    assert set(member.roles) == {shared, elder}


@pytest.mark.asyncio
async def test_spartans_member_role_shared_with_a_clan_role_is_kept(monkeypatch):
    member_and_clan = _role(333, "Member")
    guild = _guild([member_and_clan])
    member = _member(guild, [member_and_clan])
    cache = _cache(
        {"clan_role_enabled": True, "role_system_enabled": True, "member_role_id": "333",
         "clan_roles": {"#2YRCYVR8R": "333"}, "member_clans": ["#2YRCYVR8R", "#OTHER"]},
        [{"coc_role": "member", "current_clan_tag": "#OTHER"}],  # in the guild, not in that clan
    )

    await _sync_twice(monkeypatch, guild, cache)

    member.remove_roles.assert_not_awaited()
    assert member.roles == [member_and_clan]


@pytest.mark.asyncio
async def test_a_role_no_system_wants_is_still_removed(monkeypatch):
    coc_member, elder, member_role = _role(111, "CoC Member"), _role(222, "CoC Elder"), _role(444, "Member")
    guild = _guild([coc_member, elder, member_role])
    member = _member(guild, [coc_member, member_role])
    cache = _cache(
        {"coc_role_enabled": True, "role_system_enabled": True, "coc_role_member_id": "111",
         "coc_role_elder_id": "222", "member_role_id": "444", "member_clans": ["#C1"]},
        [{"coc_role": "elder", "current_clan_tag": "#C1"}],  # promoted to elder
    )

    await _sync_twice(monkeypatch, guild, cache)

    member.remove_roles.assert_awaited_once()
    assert set(member.roles) == {elder, member_role}
