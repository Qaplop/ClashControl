"""Control-flow tests for ``ClashControl.periodic_main``.

The full periodic loop (and ``main()``) is a large, deeply side-effectful
orchestrator that is exercised end-to-end by the live/smoke environment rather
than unit tests.  Its *control-flow guards*, however, are deterministic and
worth pinning down so a regression in the shutdown/early-exit handling is caught
cheaply.  This module covers the pre-initialisation early-exit path: when a
shutdown is requested before the bot finishes initialising, ``periodic_main``
must return immediately without entering the update loop.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnusedImport=false, reportAttributeAccessIssue=false
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

# ClashControl's module-level code only configures logging and defines functions; the
# bot is started under an ``if __name__ == "__main__"`` guard, so importing it
# here is side-effect-light.  A placeholder token avoids any env lookups failing.
os.environ.setdefault("DISCORD_TOKEN", "test-token")
import ClashControl  # noqa: E402


class _Event:
    """Minimal asyncio.Event stand-in with a fixed ``is_set`` result."""

    def __init__(self, is_set: bool):
        self._is_set = is_set

    def is_set(self) -> bool:
        return self._is_set


@pytest.mark.asyncio
async def test_returns_immediately_when_shutdown_requested_before_init(monkeypatch):
    """Shutdown set + bot not yet initialised → early return, no loop entry."""
    # bot lacks ``fully_initialized`` → first while-condition is True.
    fake_bot = SimpleNamespace()
    fake_qbcore = SimpleNamespace(
        bot=fake_bot,
        shutdown_event=_Event(is_set=True),
    )
    monkeypatch.setattr(ClashControl, "QBcore", fake_qbcore)

    # Guard: if the early-exit path is broken, the function would fall through to
    # asyncio.sleep / the main loop.  Make sleep explode so any fall-through fails
    # loudly instead of hanging.
    async def _boom(*_a, **_k):  # pragma: no cover - only hit on regression
        raise AssertionError("periodic_main fell through to the update loop")

    monkeypatch.setattr(ClashControl.asyncio, "sleep", _boom)

    # Should return promptly without raising.
    await ClashControl.periodic_main()


@pytest.mark.asyncio
async def test_returns_when_shutdown_set_while_uninitialised_flag_false(monkeypatch):
    """``fully_initialized=False`` + shutdown set also triggers early return."""
    fake_bot = SimpleNamespace(fully_initialized=False)
    fake_qbcore = SimpleNamespace(
        bot=fake_bot,
        shutdown_event=_Event(is_set=True),
    )
    monkeypatch.setattr(ClashControl, "QBcore", fake_qbcore)

    async def _boom(*_a, **_k):  # pragma: no cover - only hit on regression
        raise AssertionError("periodic_main fell through to the update loop")

    monkeypatch.setattr(ClashControl.asyncio, "sleep", _boom)

    await ClashControl.periodic_main()


def test_maintenance_gate_runs_before_every_cycle_path():
    """The wait for nightly DB maintenance sits at the top of the loop, before [CYCLE-START]
    and before DEV's NO_DEV_MODE_UPDATES skip branch (2026-10-17: that branch logged a cycle
    start + "Sleeping" in the middle of a maintenance run because the gate came after it)."""
    import inspect
    src = inspect.getsource(ClashControl.periodic_main)
    gate = src.index("await QBcore.db_maintenance_idle_event.wait()")
    assert gate < src.index("[CYCLE-START] Beginning update cycle")
    assert gate < src.index("if NO_DEV_MODE_UPDATES and CONFIG.is_dev_mode")
