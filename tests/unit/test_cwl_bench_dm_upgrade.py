"""Tracker #0114 — unanswered sign-up DMs gain the Bench button when a guild enables extended
sign-up (project owner: "the DMs that are not answered yet should be updated automatically").

Covers which DMs qualify (own guild's and another guild's, as long as the recipient is now
bench-enabled), that one multi-account message is edited once, that the explanation is appended
rather than replacing the existing text, and that an unreachable DM is skipped.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _fake_message(content: str, custom_ids: List[str]) -> MagicMock:
    """A DM as the upgrade sees it: text plus one action row of buttons."""
    message = MagicMock()
    message.content = content
    message.components = [MagicMock(children=[MagicMock(custom_id=cid) for cid in custom_ids])]
    return message


def _row(tag: str, *, user: str, message_id: str, guild: str = "1", status: str = "pending") -> Dict[str, Any]:
    return {
        "player_tag": tag, "player_name": tag.strip("#"), "dmed_discord_id": user,
        "status": status, "dm_sent_via_message_id": message_id, "dm_sent_via_channel_id": "10",
        "dm_sent_via_event_id": 7, "dm_sent_via_guild_id": guild,
    }


def _wire(monkeypatch, rows: List[Dict[str, Any]], *, modes: Dict[str, str], members: Dict[str, List[int]],
          event_status: str = "signup_open", message_content: str = "Please confirm below"):
    from clashcontrol.cache_manager import CACHE
    import clashcontrol.QBdiscocmdshelper_cwl as helper

    CACHE.server_config.clear()
    for guild_id, mode in modes.items():
        CACHE.server_config[guild_id] = {"cwl_signup_mode": mode}

    db = MagicMock()
    db.get_cwl_pending_dm_rows_for_season_sync = MagicMock(return_value=rows)
    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(
        helper, "get_current_cwl_event_sync",
        lambda guild_id: {"id": 7, "cwl_season": "2026-10", "status": event_status},
    )

    edits: List[Dict[str, Any]] = []
    message = _fake_message(message_content, [])

    def _apply_edit(**kwargs):
        # A real message takes on the text and buttons it was edited to; the upgrade's
        # "already offers Bench" check reads exactly those.
        edits.append(kwargs)
        message.content = kwargs["content"]
        message.components = [MagicMock(children=list(kwargs["view"].children))]

    message.edit = AsyncMock(side_effect=_apply_edit)

    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)

    def _get_guild(gid: int):
        if str(gid) not in modes:
            return None
        guild = MagicMock()
        guild.get_member = MagicMock(
            side_effect=lambda uid: MagicMock() if uid in members.get(str(gid), []) else None
        )
        return guild

    fake_bot = MagicMock()
    fake_bot.get_guild = MagicMock(side_effect=_get_guild)
    fake_bot.get_channel = MagicMock(return_value=channel)
    monkeypatch.setitem(sys.modules, "QBcore", MagicMock(bot=fake_bot))
    monkeypatch.setattr(helper.asyncio, "sleep", AsyncMock())
    return edits, message


@pytest.mark.asyncio
async def test_pending_dm_of_this_guilds_member_is_upgraded(monkeypatch):
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, _ = _wire(
        monkeypatch, [_row("#P1", user="777", message_id="100")],
        modes={"1": "extended"}, members={"1": [777]},
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1
    assert len(edits) == 1
    view = edits[0]["view"]
    assert [c.custom_id.split(":")[2] for c in view.children] == ["confirm", "passive", "optout"]


@pytest.mark.asyncio
async def test_explanation_is_appended_not_replacing_existing_text(monkeypatch):
    """A roster-update DM also says where and when the player plays — a wholesale re-render would
    throw that away."""
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, _ = _wire(
        monkeypatch, [_row("#P1", user="777", message_id="100")],
        modes={"1": "extended"}, members={"1": [777]},
        message_content="You play for **StayCalm**, CWL starts tomorrow.",
    )

    await upgrade_pending_cwl_dms_for_bench(1)
    content = edits[0]["content"]
    assert content.startswith("You play for **StayCalm**, CWL starts tomorrow.")
    assert "🪑" in content


@pytest.mark.asyncio
async def test_running_twice_is_a_no_op_the_second_time(monkeypatch):
    """Idempotent: a DM that already offers Bench is neither edited again nor counted, so the
    admin isn't told that N invitations were updated when none were."""
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, message = _wire(
        monkeypatch, [_row("#P1", user="777", message_id="100")],
        modes={"1": "extended"}, members={"1": [777]},
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1
    message.content = edits[0]["content"]  # the message now carries the explanation

    assert await upgrade_pending_cwl_dms_for_bench(1) == 0
    assert len(edits) == 1


@pytest.mark.asyncio
async def test_one_multi_account_dm_is_edited_once_with_every_pending_account(monkeypatch):
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, _ = _wire(
        monkeypatch,
        [_row("#P1", user="777", message_id="100"), _row("#P2", user="777", message_id="100")],
        modes={"1": "extended"}, members={"1": [777]},
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1
    assert len(edits) == 1
    assert len(edits[0]["view"].children) == 6  # two accounts x three buttons


@pytest.mark.asyncio
async def test_dm_sent_by_another_guild_is_upgraded_for_a_member_of_this_one(monkeypatch):
    """The Bench rule is player-based, so the DM another guild sent still gets the option."""
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    _wire(
        monkeypatch, [_row("#P1", user="777", message_id="100", guild="2")],
        modes={"1": "extended", "2": "standard"}, members={"1": [777]},
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1


@pytest.mark.asyncio
async def test_non_member_of_the_switching_guild_is_left_alone(monkeypatch):
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, _ = _wire(
        monkeypatch, [_row("#P1", user="888", message_id="100", guild="2")],
        modes={"1": "extended", "2": "standard"}, members={"1": [777]},
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 0
    assert edits == []


@pytest.mark.asyncio
async def test_nothing_happens_while_the_event_is_still_a_draft(monkeypatch):
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, _ = _wire(
        monkeypatch, [_row("#P1", user="777", message_id="100")],
        modes={"1": "extended"}, members={"1": [777]}, event_status="draft",
    )

    assert await upgrade_pending_cwl_dms_for_bench(1) == 0
    assert edits == []


@pytest.mark.asyncio
async def test_a_deleted_dm_is_skipped_not_fatal(monkeypatch):
    from clashcontrol.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    edits, message = _wire(
        monkeypatch,
        [_row("#P1", user="777", message_id="100"), _row("#P2", user="778", message_id="101")],
        modes={"1": "extended"}, members={"1": [777, 778]},
    )
    original_edit = message.edit
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise discord.NotFound(MagicMock(status=404), "gone")
        return await original_edit(**kwargs)

    message.edit = flaky

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1
    assert len(edits) == 1
