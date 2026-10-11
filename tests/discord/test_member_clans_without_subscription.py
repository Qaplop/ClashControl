"""Member clans without /subscribe (qaplop 2026-10-11, /setup on a brand-new server).

Subscriptions (leaderboards) and member clans are independent: "Member Clans" must open on a
server with no subscription, its "Add clan or family" button is the primary way in, and /subscribe
asks whether a clan that isn't a member clan yet should become one.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import ui_clan_management as ucm  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402

GID = 4242


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {})
    monkeypatch.setattr(CACHE, "subscriptions", {})
    monkeypatch.setattr(CACHE, "clan_families", {})
    monkeypatch.setattr(CACHE, "clan_name_cache", {"#NEW": {"name": "Fresh Clan"}})
    monkeypatch.setattr(CACHE, "persist_server_config", AsyncMock())
    monkeypatch.setattr(CACHE, "persist_clan", AsyncMock())
    monkeypatch.setattr(CACHE, "update_clan_subscription_status", AsyncMock())


def _interaction():
    it = MagicMock()
    it.guild = MagicMock()
    it.guild.id = GID
    it.user.id = 1
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.response.is_done = MagicMock(return_value=False)
    it.followup.send = AsyncMock(return_value=MagicMock())
    it.edit_original_response = AsyncMock()
    return it


@pytest.mark.asyncio
async def test_member_clans_opens_without_any_subscription():
    it = _interaction()
    parent = SimpleNamespace(guild_clans=[], _check_admin_permission=AsyncMock(return_value=True))
    await ucm.ClanManagementView._on_config_manage_member_clans(parent, it)  # type: ignore[arg-type]
    it.response.send_message.assert_not_awaited()          # no "No Clans Available" any more
    view = it.followup.send.await_args.kwargs["view"]
    assert isinstance(view, ucm.MemberClansConfigurationView)
    add = [c for c in view.children if getattr(c, "custom_id", None) == "add_clan_family"]
    assert len(add) == 1 and add[0].style == discord.ButtonStyle.success
    assert "Add clan or family" in add[0].label


def test_added_candidate_gets_its_toggle_button():
    parent = SimpleNamespace(guild_clans=[], sent_message=MagicMock())
    guild = MagicMock()
    guild.id = GID
    view = ucm.MemberClansConfigurationView(guild, parent, MagicMock(), [], [], {}, timeout=60)  # type: ignore[arg-type]
    view.add_member_clan_candidate("#NEW")
    view.add_member_clan_candidate("#NEW")                  # idempotent
    assert view.member_clans == ["#NEW"] and parent.guild_clans == ["#NEW"]


@pytest.mark.asyncio
async def test_subscribe_asks_only_for_a_clan_that_is_not_a_member_yet():
    it = _interaction()
    await ucm.ask_add_as_member_clan(it, "#NEW")
    kwargs = it.followup.send.await_args.kwargs
    assert isinstance(kwargs["view"], ucm.AddAsMemberClanView) and kwargs["ephemeral"] is True
    assert "Fresh Clan" in it.followup.send.await_args.args[0]

    it2 = _interaction()
    CACHE.server_config[str(GID)] = {"member_clans": ["#NEW"]}
    await ucm.ask_add_as_member_clan(it2, "#NEW")
    it2.followup.send.assert_not_awaited()

    it3 = _interaction()
    CACHE.server_config[str(GID)] = {"member_families": ["#FAM"]}
    CACHE.clan_families["#FAM"] = {"name": "F", "clans": ["#NEW"]}
    await ucm.ask_add_as_member_clan(it3, "#NEW")           # covered by a member family
    await ucm.ask_add_as_member_clan(it3, "#FAM")           # a family subscription: no question
    it3.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_yes_adds_the_member_clan_and_tracks_it(monkeypatch):
    lock = AsyncMock()
    monkeypatch.setattr(ucm, "lock_buttons", lock)
    it = _interaction()
    view = ucm.AddAsMemberClanView("#NEW", GID, "1")
    await view._on_yes(it)
    assert CACHE.server_config[str(GID)]["member_clans"] == ["#NEW"]
    CACHE.persist_server_config.assert_awaited_once_with(str(GID))
    CACHE.update_clan_subscription_status.assert_awaited_once_with("#NEW")
    assert "Fresh Clan" in it.edit_original_response.await_args.kwargs["content"]
    await ucm.add_member_clan(it, "#NEW")                   # already a member: no change
    CACHE.persist_server_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_keeps_only_the_subscription():
    it = _interaction()
    view = ucm.AddAsMemberClanView("#NEW", GID, "1")
    await view._on_no(it)
    assert "member_clans" not in CACHE.server_config.get(str(GID), {})
    assert it.response.edit_message.await_args.kwargs["view"] is None
