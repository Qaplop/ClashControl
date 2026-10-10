"""The startup re-render of renumbered test-case messages is PROD-only (2026-10-10).

DEV runs on a PROD-DB copy: it runs the same per-ticket renumbering migration and sees PROD's
message ids. Ungated, DEV's first start after a DB copy tried to edit every one of PROD's
test-case messages (15x 403 "Cannot edit a message authored by another user").
"""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

import clashcontrol.ui_tracker as ui_tracker
from clashcontrol.config import CONFIG


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, expected_calls", [(False, 0), (True, 2)])
async def test_refresh_only_when_tracker_enabled(monkeypatch, enabled, expected_calls):
    monkeypatch.setattr("clashcontrol.config.CONFIG", dataclasses.replace(CONFIG, tracker_enabled=enabled))
    refresh = AsyncMock()
    monkeypatch.setattr(ui_tracker, "_refresh_testcase_message", refresh)
    await ui_tracker.refresh_renumbered_testcase_messages([6, 7])
    assert refresh.await_count == expected_calls


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr("clashcontrol.config.CONFIG", dataclasses.replace(CONFIG, tracker_enabled=True))
    refresh = AsyncMock(side_effect=[RuntimeError("403"), None])
    monkeypatch.setattr(ui_tracker, "_refresh_testcase_message", refresh)
    await ui_tracker.refresh_renumbered_testcase_messages([6, 7])
    assert refresh.await_count == 2
