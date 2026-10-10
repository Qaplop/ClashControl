"""Tracker #0150 follow-ups found in the Build 133 PROD week (2026-10-03 .. 10-10).

- Log throttling for failures only a guild admin can fix (leaderboard / war-notification 403s,
  discord_retry 4xx): 310 identical leaderboard ERRORs with tracebacks in 28 h.
- The CoC gateway breaker held cycles to ~200 s on 2026-10-04, but Phase 2 and the raid update
  still fanned out one doomed call (and log line) per war/clan: 64,055 + 1,562 warnings.
- A member joining during nightly DB maintenance lost their pending tracker access grant.
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import inspect
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import log_throttle  # noqa: E402
from clashcontrol.log_throttle import find_discord_forbidden, log_throttled, reset_log_throttle  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_throttle():
    reset_log_throttle()
    yield
    reset_log_throttle()


def _forbidden(code: int = 50013) -> discord.Forbidden:
    resp = MagicMock(status=403)
    return discord.Forbidden(resp, {"code": code, "message": "Missing Permissions"})


class TestLogThrottled:
    def test_first_logged_at_level_repeats_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG):
            assert log_throttled(logging.WARNING, "k", "boom") is True
            assert log_throttled(logging.WARNING, "k", "boom") is False
        levels = [r.levelno for r in caplog.records if "boom" in r.getMessage()]
        assert levels == [logging.WARNING, logging.DEBUG]

    def test_logs_again_after_interval(self, caplog):
        clock = [1000.0]
        with patch.object(log_throttle.time, "monotonic", side_effect=lambda: clock[0]):
            with caplog.at_level(logging.WARNING):
                log_throttled(logging.WARNING, "k", "again", interval_s=60)
                clock[0] += 61
                log_throttled(logging.WARNING, "k", "again", interval_s=60)
        assert len([r for r in caplog.records if "again" in r.getMessage()]) == 2

    def test_keys_are_independent(self):
        assert log_throttled(logging.WARNING, ("a", 1), "x")
        assert log_throttled(logging.WARNING, ("a", 2), "x")

    def test_find_forbidden_through_wrapper_context(self):
        try:
            try:
                raise _forbidden()
            except Exception as inner:
                raise RuntimeError(f"Error posting leaderboard: {inner}")
        except RuntimeError as wrapped:
            assert isinstance(find_discord_forbidden(wrapped), discord.Forbidden)
        assert find_discord_forbidden(RuntimeError("plain")) is None


class TestDiscordRetry403:
    @pytest.mark.asyncio
    async def test_403_logged_once_per_hour_as_warning(self, caplog):
        from clashcontrol.discord_health import discord_retry
        op = AsyncMock(side_effect=_forbidden())
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                with pytest.raises(discord.Forbidden):
                    await discord_retry(op, "send_short_leaderboard")
        recs = [r for r in caplog.records if "send_short_leaderboard failed" in r.getMessage()]
        assert len(recs) == 1 and recs[0].levelno == logging.WARNING
        assert op.await_count == 3  # still raised to the caller every time, never retried

    @pytest.mark.asyncio
    async def test_other_4xx_still_error_every_time(self, caplog):
        from clashcontrol.discord_health import discord_retry
        bad = discord.HTTPException(MagicMock(status=400), "bad request")
        op = AsyncMock(side_effect=bad)
        with caplog.at_level(logging.ERROR):
            for _ in range(2):
                with pytest.raises(discord.HTTPException):
                    await discord_retry(op, "op400")
        assert len([r for r in caplog.records if "op400 failed" in r.getMessage()]) == 2


class TestOutageFanOutSkipped:
    def teardown_method(self):
        from clashcontrol.coc_health import clear_gateway_outage_detection
        clear_gateway_outage_detection()

    @pytest.mark.asyncio
    async def test_raid_update_skipped_when_breaker_tripped(self, monkeypatch, caplog):
        import QBhelperfunctions
        import clashcontrol.coc_health as ch
        from clashcontrol.cache_manager import CACHE
        monkeypatch.setattr(CACHE, "db_manager", MagicMock())
        monkeypatch.setattr("clashcontrol.QBdiscocmdshelper_cwl.all_member_clan_tags", lambda: ["#A", "#B"])
        monkeypatch.setattr("clashcontrol.constants.is_capital_raid_window", lambda _now=None: True)
        per_clan = AsyncMock()
        monkeypatch.setattr(QBhelperfunctions, "update_capital_raid_for_clan", per_clan)
        ch._gateway_outage_detected = True
        with caplog.at_level(logging.WARNING):
            totals = await QBhelperfunctions.update_capital_raids_for_member_clans()
        per_clan.assert_not_awaited()
        assert totals["errors"] == 2
        assert len([r for r in caplog.records if "[RAID-UPDATE]" in r.getMessage()]) == 1

    def test_orphan_fetch_guarded_before_fan_out(self):
        """process_orphaned_cwl_wars() is too entangled with the temp-file scan to drive here,
        so pin the structure: the breaker check returns before the per-war gather()."""
        import QBhelperfunctions
        src = inspect.getsource(QBhelperfunctions.process_orphaned_cwl_wars)
        guard = src.index("is_gateway_outage_detected()")
        fan_out = src.index("results = await asyncio.gather(*tasks")
        assert guard < fan_out
        assert "Skipping non-tuple result" in src and "first 3 logged above" in src


class TestPendingAccessAfterMaintenance:
    @pytest.mark.asyncio
    async def test_retries_once_maintenance_ends(self, monkeypatch):
        import ClashControl
        import QBcore
        monkeypatch.setattr(QBcore, "maintenance_mode", False)
        monkeypatch.setattr(QBcore, "db_maintenance_mode", True)
        # Maintenance stays on for two polls, then ends.
        remaining = iter([True, False])

        async def _sleep(_s):
            QBcore.db_maintenance_mode = next(remaining)

        monkeypatch.setattr(ClashControl.asyncio, "sleep", _sleep)
        apply = AsyncMock()
        monkeypatch.setattr("clashcontrol.ui_tracker.apply_pending_requestor_access", apply)
        member = MagicMock(id=42)
        await ClashControl._apply_pending_requestor_access_after_maintenance(member, poll_s=1, give_up_s=100)
        apply.assert_awaited_once_with(member)

    @pytest.mark.asyncio
    async def test_gives_up_if_maintenance_never_ends(self, monkeypatch):
        import ClashControl
        import QBcore
        monkeypatch.setattr(QBcore, "db_maintenance_mode", True)
        monkeypatch.setattr(QBcore, "maintenance_mode", False)
        monkeypatch.setattr(ClashControl.asyncio, "sleep", AsyncMock())
        apply = AsyncMock()
        monkeypatch.setattr("clashcontrol.ui_tracker.apply_pending_requestor_access", apply)
        await ClashControl._apply_pending_requestor_access_after_maintenance(MagicMock(id=1), poll_s=10, give_up_s=30)
        apply.assert_not_awaited()

    def test_join_handler_defers_on_db_maintenance(self):
        import ClashControl
        src = inspect.getsource(ClashControl.on_member_join)
        assert "except DatabaseMaintenanceError" in src
        assert "_apply_pending_requestor_access_after_maintenance" in src
