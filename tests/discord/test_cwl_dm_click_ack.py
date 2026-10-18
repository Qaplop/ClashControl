"""CWL DM answer buttons acknowledge the click before any DB work.

2026-10-17 (PROD): three clicks failed with 10062 — the answer's DB write ran before the first
response and queued behind the update cycle's bulk war writes (~1 s each, back to back), so the
3 s deadline passed. Now: defer first, write, then edit through the interaction token; if even the
defer fails, the answer is still saved and the DM edited directly.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import clashcontrol.ui_cwl_roster as roster


def _ok_result() -> dict:
    return {"code": "ok", "guild_id": 1, "season": "2026-10", "player_name": "P"}


@pytest.fixture
def patched(monkeypatch):
    calls: list = []
    rerender = AsyncMock()

    async def _apply(*_a, **_k):
        calls.append("apply")
        return _ok_result()

    monkeypatch.setattr(roster, "_apply_cwl_signup_response", _apply)
    monkeypatch.setattr(roster, "rerender_cwl_dm_after_response", rerender)
    return calls, rerender


def _interaction(calls: list, defer_exc: Exception | None = None) -> MagicMock:
    inter = MagicMock()
    inter.user.id = 70

    async def _defer(*_a, **_k):
        calls.append("defer")
        if defer_exc is not None:
            raise defer_exc

    inter.response.defer = _defer
    inter.response.send_message = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


@pytest.mark.asyncio
@pytest.mark.parametrize("button_cls", [roster.CwlSignupResponseButton, roster.CwlReminderResponseButton])
async def test_click_is_acknowledged_before_the_db_write(patched, button_cls):
    calls, rerender = patched
    inter = _interaction(calls)

    await button_cls("confirm", 35, "#P1").callback(inter)

    assert calls == ["defer", "apply"]
    kwargs = rerender.await_args.kwargs
    assert kwargs["deferred"] is True and kwargs["interaction"] is inter


@pytest.mark.asyncio
@pytest.mark.parametrize("button_cls", [roster.CwlSignupResponseButton, roster.CwlReminderResponseButton])
async def test_answer_is_saved_even_when_the_ack_fails(patched, button_cls):
    calls, rerender = patched
    expired = discord.NotFound(MagicMock(status=404), {"code": 10062, "message": "Unknown interaction"})
    inter = _interaction(calls, defer_exc=expired)

    await button_cls("optout", 35, "#P1").callback(inter)

    assert calls == ["defer", "apply"]  # the answer still went to the DB
    kwargs = rerender.await_args.kwargs
    assert kwargs["interaction"] is None and kwargs["deferred"] is False  # DM edited directly


@pytest.mark.asyncio
async def test_refused_answer_goes_out_as_followup_after_the_ack(monkeypatch):
    calls: list = []

    async def _apply(*_a, **_k):
        return {"code": "wrong_account", "guild_id": 1, "season": "", "player_name": ""}

    monkeypatch.setattr(roster, "_apply_cwl_signup_response", _apply)
    inter = _interaction(calls)

    await roster.CwlSignupResponseButton("confirm", 35, "#P1").callback(inter)

    inter.followup.send.assert_awaited_once()
    assert inter.followup.send.await_args.kwargs.get("ephemeral") is True
    inter.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_rerender_with_deferred_edits_through_the_interaction_token(monkeypatch):
    monkeypatch.setattr(roster.CACHE, "db_manager", None)
    inter = MagicMock()
    inter.edit_original_response = AsyncMock()
    inter.response.edit_message = AsyncMock()
    message = MagicMock(id=1, content="")
    message.edit = AsyncMock()

    await roster.rerender_cwl_dm_after_response(
        message, 35, "2026-10", "70", action="confirm", player_name="P", interaction=inter, deferred=True,
    )

    inter.edit_original_response.assert_awaited_once()
    inter.response.edit_message.assert_not_awaited()
    message.edit.assert_not_awaited()
