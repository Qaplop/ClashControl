"""[LOOP-LAG] log levels (log-noise reduction, 2026-10-10).

Routine stalls (the gen-2 GC pauses, hundreds a day on PROD, well inside Discord's 3 s ACK
deadline) are logged at INFO; only stalls of 2 s or more, where an interaction is at real risk
of missing its deadline, stay at WARNING. Real blocking sleeps, no mocked clock: the watchdog
measures wall time, so this is what it sees on PROD.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")
import ClashControl  # noqa: E402


@pytest.mark.asyncio
async def test_short_stall_info_long_stall_warning(caplog):
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(ClashControl.event_loop_lag_watchdog())
    try:
        await asyncio.sleep(0.15)  # watchdog started and sleeping
        time.sleep(0.8)  # 0.5 s < stall < 2 s
        await asyncio.sleep(0.25)
        time.sleep(2.1)  # stall >= 2 s
        await asyncio.sleep(0.25)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    stalls = [r for r in caplog.records if "Event loop stalled" in r.getMessage()]
    assert [r.levelno for r in stalls] == [logging.INFO, logging.WARNING]
