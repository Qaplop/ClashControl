"""Tracker #0092 — per-clan CWL coordinator roles.

A guild chooses between ONE coordinator role for every clan (#0086's behaviour, the default) and
ONE ROLE PER CLAN (guild_config.cwl_coordinator_role_mode = 'per_clan', links in
cwl_clan_coordinator_roles). In per-clan mode a coordinator of several clans holds each of those
clans' roles, and losing one clan never strips another clan's role.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _member(user_id: int, roles: List[Any] | None = None) -> MagicMock:
    m = MagicMock()
    m.id = user_id
    m.display_name = f"User{user_id}"
    m.roles = roles if roles is not None else []
    return m


def _role(role_id: int, name: str) -> MagicMock:
    r = MagicMock()
    r.id = role_id
    r.name = name
    r.mention = f"@{name}"
    r.members = []
    return r


def _guild(guild_id: int, roles: Dict[int, MagicMock], members: Dict[int, MagicMock]) -> MagicMock:
    guild = MagicMock()
    guild.id = guild_id
    guild.name = "The QCrew"
    guild.get_role = MagicMock(side_effect=lambda rid: roles.get(int(rid)))
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(uid))
    guild.get_channel = MagicMock(return_value=None)
    return guild


# ---------------------------------------------------------------------------
# cwl_coordinator_role_targets — both modes reduce to role -> user ids
# ---------------------------------------------------------------------------

def test_single_mode_targets_union_of_every_clan():
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    config = {
        "cwl_coordinator_role_id": "500",
        "cwl_clan_coordinators": {"#A": ["1", "2"], "#B": ["2", "3"]},
        # Per-clan links exist but are inactive in single mode.
        "cwl_clan_coordinator_roles": {"#A": "601"},
    }
    assert cwl_coordinator_role_targets(config) == {500: {1, 2, 3}}


def test_per_clan_mode_targets_each_clans_own_coordinators():
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    config = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_coordinator_role_id": "500",  # inactive in per-clan mode
        "cwl_clan_coordinators": {"#A": ["1"], "#B": ["1", "2"], "#C": ["3"]},
        "cwl_clan_coordinator_roles": {"#A": "601", "#B": "602"},  # #C has no role linked
    }
    assert cwl_coordinator_role_targets(config) == {601: {1}, 602: {1, 2}}


def test_per_clan_mode_two_clans_sharing_a_role_union_their_coordinators():
    """A shared role must never be stripped from someone still coordinating the other clan."""
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    config = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinators": {"#A": ["1"], "#B": ["2"]},
        "cwl_clan_coordinator_roles": {"#A": "700", "#B": "700"},
    }
    assert cwl_coordinator_role_targets(config) == {700: {1, 2}}


def test_linked_clan_without_coordinators_targets_nobody():
    """A linked role for a clan with no coordinators is still reconciled — to empty — so the last
    coordinator removed from that clan loses its role."""
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    config = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinators": {},
        "cwl_clan_coordinator_roles": {"#A": "601"},
    }
    assert cwl_coordinator_role_targets(config) == {601: set()}


def test_nothing_linked_targets_nothing():
    from clashcontrol.guild_role_manager import cwl_coordinator_role_targets

    assert cwl_coordinator_role_targets({"cwl_clan_coordinators": {"#A": ["1"]}}) == {}
    assert cwl_coordinator_role_targets(
        {"cwl_coordinator_role_mode": "per_clan", "cwl_clan_coordinators": {"#A": ["1"]}}
    ) == {}


# ---------------------------------------------------------------------------
# sync_cwl_coordinator_role — per-clan mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_clan_sync_gives_a_multi_clan_coordinator_every_clan_role(monkeypatch):
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.guild_role_manager as grm

    role_a, role_b = _role(601, "CWL Koordinator StayCalm"), _role(602, "CWL Koordinator StayMad")
    lucas = _member(111)
    guild = _guild(9921, {601: role_a, 602: role_b}, {111: lucas})
    CACHE.server_config["9921"] = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinators": {"#A": ["111"], "#B": ["111"]},
        "cwl_clan_coordinator_roles": {"#A": "601", "#B": "602"},
    }
    assign = AsyncMock(return_value=True)
    monkeypatch.setattr(grm, "assign_role_to_member", assign)
    monkeypatch.setattr(grm, "remove_role_from_member", AsyncMock(return_value=True))

    assert await grm.sync_cwl_coordinator_role(guild) == (2, 0)
    assert {c.args[1].id for c in assign.await_args_list} == {601, 602}


@pytest.mark.asyncio
async def test_per_clan_sync_removing_one_clan_keeps_the_other_clans_role(monkeypatch):
    """111 was coordinator of #A and #B and is removed from #A only: loses #A's role, keeps #B's."""
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.guild_role_manager as grm

    role_a, role_b = _role(601, "A"), _role(602, "B")
    coordinator = _member(111, roles=[role_a, role_b])
    role_a.members = [coordinator]
    role_b.members = [coordinator]
    guild = _guild(9922, {601: role_a, 602: role_b}, {111: coordinator})
    CACHE.server_config["9922"] = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinators": {"#A": [], "#B": ["111"]},
        "cwl_clan_coordinator_roles": {"#A": "601", "#B": "602"},
    }
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(grm, "assign_role_to_member", AsyncMock(return_value=True))
    monkeypatch.setattr(grm, "remove_role_from_member", remove)

    assert await grm.sync_cwl_coordinator_role(guild) == (0, 1)
    remove.assert_awaited_once()
    assert remove.await_args is not None
    assert remove.await_args.args[1] is role_a


@pytest.mark.asyncio
async def test_per_clan_sync_skips_a_deleted_role_but_syncs_the_rest(monkeypatch):
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.guild_role_manager as grm

    role_b = _role(602, "B")
    guild = _guild(9923, {602: role_b}, {111: _member(111)})  # 601 was deleted in Discord
    CACHE.server_config["9923"] = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinators": {"#A": ["111"], "#B": ["111"]},
        "cwl_clan_coordinator_roles": {"#A": "601", "#B": "602"},
    }
    assign = AsyncMock(return_value=True)
    monkeypatch.setattr(grm, "assign_role_to_member", assign)
    monkeypatch.setattr(grm, "remove_role_from_member", AsyncMock(return_value=True))

    assert await grm.sync_cwl_coordinator_role(guild) == (1, 0)
    assert assign.await_args is not None and assign.await_args.args[1] is role_b


@pytest.mark.asyncio
async def test_per_clan_sync_never_touches_the_inactive_single_role(monkeypatch):
    """Switching mode unlinks the other mode's role: it stops being synced, nobody is stripped."""
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.guild_role_manager as grm

    single = _role(500, "CWL Coordinator")
    holder = _member(999, roles=[single])
    single.members = [holder]
    guild = _guild(9924, {500: single}, {999: holder})
    CACHE.server_config["9924"] = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_coordinator_role_id": "500",
        "cwl_clan_coordinators": {},
        "cwl_clan_coordinator_roles": {},
    }
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(grm, "remove_role_from_member", remove)

    assert await grm.sync_cwl_coordinator_role(guild) == (0, 0)
    remove.assert_not_awaited()


# ---------------------------------------------------------------------------
# CWL Settings readout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwl_settings_embed_shows_per_clan_link_count():
    from clashcontrol.cache_manager import CACHE
    from clashcontrol.QBdiscocmdshelper_cwl import format_clan_management_cwl_settings

    guild = _guild(9931, {601: _role(601, "A")}, {})  # 602 no longer resolves
    CACHE.server_config["9931"] = {
        "cwl_coordinator_role_mode": "per_clan",
        "cwl_clan_coordinator_roles": {"#A": "601", "#B": "602"},
    }

    embed, _, _, _ = await format_clan_management_cwl_settings(guild)

    blocks = "\n".join(f.value or "" for f in embed.fields)
    assert "1 of 2" in blocks


# ---------------------------------------------------------------------------
# DB round trip
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    from clashcontrol.db_manager import WarHistoryDB

    manager = WarHistoryDB()
    await manager.initialize(str(tmp_path / "qapbot_test.db"))
    try:
        yield manager
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mode_and_per_clan_links_round_trip_through_the_db(db):
    await db.save_guild_config("9941", {"cwl_coordinator_role_mode": "per_clan", "cwl_coordinator_role_id": "500"})
    await db.save_cwl_clan_coordinator_roles("9941", {"#A": "601", "#B": "602"})

    config = await db.get_guild_config("9941")
    assert config is not None
    assert config["cwl_coordinator_role_mode"] == "per_clan"
    assert config["cwl_coordinator_role_id"] == "500"
    assert config["cwl_clan_coordinator_roles"] == {"#A": "601", "#B": "602"}

    # Replace-all: a link removed in the UI disappears from the DB too.
    await db.save_cwl_clan_coordinator_roles("9941", {"#B": "603"})
    config = await db.get_guild_config("9941")
    assert config is not None
    assert config["cwl_clan_coordinator_roles"] == {"#B": "603"}


@pytest.mark.asyncio
async def test_existing_guilds_default_to_single_mode(db):
    await db.save_guild_config("9942", {})
    config = await db.get_guild_config("9942")
    assert config is not None
    assert config["cwl_coordinator_role_mode"] == "single"
    assert config["cwl_clan_coordinator_roles"] == {}


# ---------------------------------------------------------------------------
# CwlCoordinatorRoleConfigurationView
# ---------------------------------------------------------------------------

def _interaction(guild: MagicMock, values: List[str] | None = None) -> MagicMock:
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user.id = 42
    interaction.data = {"values": values or []}
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _view(guild: MagicMock, **kwargs: Any):
    from clashcontrol.ui_cwl_roster import CwlCoordinatorRoleConfigurationView

    return CwlCoordinatorRoleConfigurationView(guild=guild, parent_view=MagicMock(), **kwargs)


@pytest.mark.asyncio
async def test_view_switching_to_per_clan_adds_a_clan_picker():
    guild = _guild(9951, {}, {})
    view = _view(guild, clan_tags=["#A", "#B"])
    assert not any(isinstance(c, discord.ui.Select) and c.custom_id.startswith("cwl_coordinator_role_clan_")
                   for c in view.children)

    await view._on_mode_select(_interaction(guild, ["per_clan"]))

    assert view.mode == "per_clan"
    assert any(isinstance(c, discord.ui.Select) and (c.custom_id or "").startswith("cwl_coordinator_role_clan_")
               for c in view.children)
    assert view.is_dirty


@pytest.mark.asyncio
async def test_view_role_pick_and_clear_only_affect_the_selected_clan():
    guild = _guild(9952, {601: _role(601, "A"), 602: _role(602, "B")}, {})
    view = _view(guild, clan_tags=["#A", "#B"], mode="per_clan", role_ids_by_clan={"#B": "602"})
    assert view.clan_tag == "#A"

    await view._on_role_select(_interaction(guild, ["601"]))
    assert view.role_ids_by_clan == {"#A": "601", "#B": "602"}

    await view._on_clan_select(_interaction(guild, ["#B"]))
    await view._on_clear(_interaction(guild))
    assert view.role_ids_by_clan == {"#A": "601"}


@pytest.mark.asyncio
async def test_view_save_persists_everything_and_syncs(monkeypatch):
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.guild_role_manager as grm

    guild = _guild(9953, {601: _role(601, "A")}, {})
    CACHE.server_config["9953"] = {"cwl_coordinator_role_id": "500"}
    db = MagicMock()
    db.save_guild_config = AsyncMock()
    db.save_cwl_clan_coordinator_roles = AsyncMock()
    monkeypatch.setattr(CACHE, "db_manager", db)
    sync = AsyncMock(return_value=(1, 0))
    monkeypatch.setattr(grm, "sync_cwl_coordinator_role", sync)

    view = _view(guild, clan_tags=["#A"], mode="single", single_role_id="500")
    await view._on_mode_select(_interaction(guild, ["per_clan"]))
    await view._on_role_select(_interaction(guild, ["601"]))
    save_interaction = _interaction(guild)
    await view._on_save(save_interaction)

    saved_config = db.save_guild_config.await_args.args[1]
    assert saved_config["cwl_coordinator_role_mode"] == "per_clan"
    # The single role is kept (inactive) so switching back restores it.
    assert saved_config["cwl_coordinator_role_id"] == "500"
    db.save_cwl_clan_coordinator_roles.assert_awaited_once_with("9953", {"#A": "601"})
    assert CACHE.server_config["9953"]["cwl_clan_coordinator_roles"] == {"#A": "601"}
    sync.assert_awaited_once_with(guild)
    assert not view.is_dirty
    assert "+1 / -0" in save_interaction.followup.send.await_args.args[0]
