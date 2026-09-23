"""The Bench legend appears once per player, at the top, and an upgraded DM looks exactly like one
sent in extended mode from the start (project owner, 2026-09-23 — with screenshots of both
paths looking different, and of the legend repeated under every account's DM).
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest


def _signup_message(content: str, tag: str) -> MagicMock:
    """A sign-up DM as sent in STANDARD mode: plain question, Confirm + Opt Out."""
    message = MagicMock()
    message.content = content
    message.components = [MagicMock(children=[
        MagicMock(custom_id=f"cwl:signup:confirm:7:{tag}"),
        MagicMock(custom_id=f"cwl:signup:optout:7:{tag}"),
    ])]
    return message


def _row(tag: str, message_id: str, user: str = "777") -> Dict[str, Any]:
    return {
        "player_tag": tag, "player_name": tag.strip("#"), "dmed_discord_id": user,
        "status": "pending", "dm_sent_via_message_id": message_id, "dm_sent_via_channel_id": "10",
        "dm_sent_via_event_id": 7, "dm_sent_via_guild_id": "1",
    }


def _wire(monkeypatch, rows: List[Dict[str, Any]], messages: Dict[str, MagicMock]) -> List[Dict[str, Any]]:
    from qapbot.cache_manager import CACHE
    import qapbot.QBdiscocmdshelper_cwl as helper

    CACHE.server_config.clear()
    CACHE.server_config["1"] = {"cwl_signup_mode": "extended"}
    db = MagicMock()
    db.get_cwl_pending_dm_rows_for_season_sync = MagicMock(return_value=rows)
    monkeypatch.setattr(CACHE, "db_manager", db)
    monkeypatch.setattr(helper, "get_current_cwl_event_sync",
                        lambda guild_id: {"id": 7, "cwl_season": "2026-10", "status": "signup_open"})
    monkeypatch.setattr(helper.asyncio, "sleep", AsyncMock())

    edits: List[Dict[str, Any]] = []
    for message_id, message in messages.items():
        def _apply(message=message, message_id=message_id, **kwargs):
            edits.append({"message_id": message_id, **kwargs})
        message.edit = AsyncMock(side_effect=_apply)

    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=lambda mid: messages[str(mid)])
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    fake_bot = MagicMock(get_channel=MagicMock(return_value=channel), get_guild=MagicMock(return_value=guild))
    monkeypatch.setitem(sys.modules, "QBcore", MagicMock(bot=fake_bot))
    return edits


# ---------------------------------------------------------------------------
# The shared builder
# ---------------------------------------------------------------------------

def test_standard_mode_dm_has_no_legend_and_two_buttons():
    from qapbot.QBdiscocmdshelper_cwl import build_cwl_signup_dm

    content, view = build_cwl_signup_dm(7, 1, "2026-10", "777", "#P1", "Alpha", with_legend=True, bench=False)
    assert "**Bench**" not in content
    assert len(view.children) == 2


def test_extended_mode_legend_only_when_asked_and_at_the_top():
    from qapbot.QBdiscocmdshelper_cwl import build_cwl_signup_dm

    first, view = build_cwl_signup_dm(7, 1, "2026-10", "777", "#P1", "Alpha", with_legend=True, bench=True)
    later, _ = build_cwl_signup_dm(7, 1, "2026-10", "777", "#P2", "Beta", with_legend=False, bench=True)

    assert "**Bench**" in first and "**Bench**" not in later
    # Legend above the question: season line, legend, then "…with Alpha?".
    assert first.index("**Bench**") < first.index("Alpha")
    assert len(view.children) == 3


# ---------------------------------------------------------------------------
# Upgrade == fresh send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_upgraded_signup_dm_is_identical_to_a_fresh_extended_send(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import build_cwl_signup_dm, upgrade_pending_cwl_dms_for_bench

    messages = {"100": _signup_message("📋 Sign-up … Alpha?", "#P1")}
    edits = _wire(monkeypatch, [_row("#P1", "100")], messages)

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1

    fresh_content, fresh_view = build_cwl_signup_dm(
        7, 1, "2026-10", "777", "#P1", "P1", with_legend=True, bench=True
    )
    assert edits[0]["content"] == fresh_content
    # Same sign-up buttons (Confirm / Bench / Opt Out), not the name-labelled reminder ones.
    assert [c.custom_id for c in edits[0]["view"].children] == [c.custom_id for c in fresh_view.children]


@pytest.mark.asyncio
async def test_upgrade_puts_the_legend_on_the_players_oldest_dm_only(monkeypatch):
    from qapbot.QBdiscocmdshelper_cwl import upgrade_pending_cwl_dms_for_bench

    messages = {
        "300": _signup_message("… Gamma?", "#P3"),
        "100": _signup_message("… Alpha?", "#P1"),
        "200": _signup_message("… Beta?", "#P2"),
    }
    rows = [_row("#P3", "300"), _row("#P1", "100"), _row("#P2", "200")]
    edits = _wire(monkeypatch, rows, messages)

    assert await upgrade_pending_cwl_dms_for_bench(1) == 3
    with_legend = [e["message_id"] for e in edits if "**Bench**" in e["content"]]
    assert with_legend == ["100"]  # the topmost one, whatever order the rows came in


@pytest.mark.asyncio
async def test_a_player_whose_legend_dm_is_already_upgraded_gets_no_second_one(monkeypatch):
    """E.g. a partial earlier run: the oldest DM already carries the legend and three buttons."""
    from qapbot.QBdiscocmdshelper_cwl import build_cwl_signup_dm, upgrade_pending_cwl_dms_for_bench

    done_content, done_view = build_cwl_signup_dm(7, 1, "2026-10", "777", "#P1", "P1", with_legend=True, bench=True)
    done = MagicMock()
    done.content = done_content
    done.components = [MagicMock(children=list(done_view.children))]
    messages = {"100": done, "200": _signup_message("… Beta?", "#P2")}
    edits = _wire(monkeypatch, [_row("#P1", "100"), _row("#P2", "200")], messages)

    assert await upgrade_pending_cwl_dms_for_bench(1) == 1
    assert edits[0]["message_id"] == "200" and "**Bench**" not in edits[0]["content"]


# ---------------------------------------------------------------------------
# Answering the legend-bearing account keeps the legend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirming_the_legend_dm_keeps_the_legend_above_the_thanks(monkeypatch):
    from qapbot.cache_manager import CACHE
    from qapbot.emojis import signup_dm_icons
    from qapbot.i18n import t
    from qapbot.ui_cwl_roster import rerender_cwl_dm_after_response

    legend = t('cwl.template.bench_explanation', user_id="777", guild_id=None, **signup_dm_icons())
    db = MagicMock()
    db.get_cwl_player_season_status_rows_by_dm_message_sync = MagicMock(return_value=[
        {"player_tag": "#P1", "player_name": "P1", "status": "confirmed"},
    ])
    db.get_cwl_event_by_id_sync = MagicMock(return_value=None)
    monkeypatch.setattr(CACHE, "db_manager", db)

    message = MagicMock()
    message.id = 100
    message.content = f"📋 Sign-up …\n\n{legend}\n\nDo you want to play … P1?"
    message.edit = AsyncMock()

    await rerender_cwl_dm_after_response(
        message, 7, "2026-10", "777", action="confirm", player_name="P1", interaction=None,
    )

    new_content = message.edit.await_args.kwargs["content"]
    assert new_content.startswith(legend)
    assert "P1" in new_content.split(legend, 1)[1]
