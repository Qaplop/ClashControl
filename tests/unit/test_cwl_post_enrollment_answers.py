"""Phase 0b (plans/tracker-0114-cwl-bench-signup-status.md) — a player who was never asked during
the sign-up window can still answer after it closes.

send_cwl_roster_updates() gives a late-added player confirm/opt-out buttons plus "Please confirm
below", but that DM goes out while the event is already 'announced', so every click used to come
back "sign-up isn't open for this season anymore". Answers are now accepted in announced/war for
accounts whose status is still 'pending'; a settled answer stays final once rosters went out.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest


def _wire(monkeypatch, *, event_status: str, signup_status: str) -> Dict[str, Any]:
    """Patch the DB reads _apply_cwl_signup_response makes, and capture what it writes."""
    from qapbot.cache_manager import CACHE

    written: Dict[str, Any] = {}
    db = MagicMock()
    db.get_cwl_event_by_id_sync = MagicMock(
        return_value={"id": 7, "guild_id": "900", "cwl_season": "2026-10", "status": event_status}
    )
    db.get_cwl_signup_sync = MagicMock(return_value={
        "player_tag": "#P1", "player_name": "Alpha", "dmed_discord_id": "55",
        "preferred_league_rank": None, "status": signup_status,
    })
    db.get_player_links_sync = MagicMock(return_value={"#P1": {"discord_id": "55"}})

    def _upsert(event_id, player_tag, name, owner, rank, source, status, responded_at=None):
        written.update({"status": status, "source": source, "player_tag": player_tag})

    db.upsert_cwl_signup_sync = MagicMock(side_effect=_upsert)
    monkeypatch.setattr(CACHE, "db_manager", db)

    import qapbot.QBdiscocmdshelper_cwl as helper
    monkeypatch.setattr(helper, "propagate_cwl_player_response", AsyncMock(return_value=[]))
    import qapbot.web_bridge as wb
    monkeypatch.setattr(wb, "bump_enrollment_version", AsyncMock())
    return written


@pytest.mark.asyncio
@pytest.mark.parametrize("event_status", ["announced", "war"])
async def test_pending_player_may_still_answer_after_enrollment_closed(monkeypatch, event_status):
    from qapbot.ui_cwl_roster import _apply_cwl_signup_response

    written = _wire(monkeypatch, event_status=event_status, signup_status="pending")
    result = await _apply_cwl_signup_response(7, "#P1", "confirm", "55")

    assert result["code"] == "ok"
    assert written["status"] == "confirmed"


@pytest.mark.asyncio
async def test_already_answered_player_cannot_change_it_after_rosters_went_out(monkeypatch):
    from qapbot.ui_cwl_roster import _apply_cwl_signup_response

    written = _wire(monkeypatch, event_status="announced", signup_status="confirmed")
    result = await _apply_cwl_signup_response(7, "#P1", "optout", "55")

    assert result["code"] == "signup_closed"
    assert written == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_status", ["draft", "cancelled"])
async def test_draft_and_cancelled_stay_closed_for_everyone(monkeypatch, event_status):
    from qapbot.ui_cwl_roster import _apply_cwl_signup_response

    written = _wire(monkeypatch, event_status=event_status, signup_status="pending")
    result = await _apply_cwl_signup_response(7, "#P1", "confirm", "55")

    assert result["code"] == "signup_closed"
    assert written == {}


@pytest.mark.asyncio
async def test_open_enrollment_is_unchanged(monkeypatch):
    from qapbot.ui_cwl_roster import _apply_cwl_signup_response

    written = _wire(monkeypatch, event_status="signup_open", signup_status="confirmed")
    result = await _apply_cwl_signup_response(7, "#P1", "optout", "55")

    assert result["code"] == "ok"
    assert written["status"] == "declined"


@pytest.mark.asyncio
async def test_roster_update_dm_records_its_message_id(monkeypatch):
    """Without the ids this one DM was unfindable: it could not be retracted by Delete Season,
    re-rendered after an answer, or upgraded when a guild switches on extended sign-up."""
    import qapbot.QBdiscocmdshelper_cwl as helper
    from qapbot.cache_manager import CACHE

    message = MagicMock()
    message.id = 4242
    message.channel.id = 99

    async def fake_send(discord_id, content, view=None, sent_message_out=None):
        if sent_message_out is not None and view is not None:
            sent_message_out.append(message)
        return True, "sent"

    monkeypatch.setattr(CACHE, "send_user_dm_detailed", fake_send)

    ref: list[Any] = []
    sent, _outcome = await helper._send_cwl_dm_chunks(
        "55", "intro", ["line"], view=MagicMock(), sent_message_out=ref
    )

    assert sent is True
    assert ref and str(ref[0].id) == "4242" and str(ref[0].channel.id) == "99"


@pytest.mark.asyncio
async def test_dm_chunks_without_a_view_report_no_message(monkeypatch):
    import qapbot.QBdiscocmdshelper_cwl as helper
    from qapbot.cache_manager import CACHE

    async def fake_send(discord_id, content, view=None, sent_message_out=None):
        assert sent_message_out is None
        return True, "sent"

    monkeypatch.setattr(CACHE, "send_user_dm_detailed", fake_send)

    ref: list[Any] = []
    sent, _ = await helper._send_cwl_dm_chunks("55", "intro", ["line"], sent_message_out=ref)

    assert sent is True and ref == []
