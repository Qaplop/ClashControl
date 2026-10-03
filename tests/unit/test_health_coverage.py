"""Full coverage tests for coc_health.py and discord_health.py.

Targets:
- coc_retry: all error paths (NotFound, Maintenance, 429, PrivateWarLog, 404, generic)
- get_coc_stats / reset_cycle_stats
- discord_retry: all error paths (429, 10008, generic HTTP, generic exception)
- get_simple_discord_stats / reset_stats
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnusedImport=false, reportAttributeAccessIssue=false, reportReturnType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

# We need a lightweight coc-like exception hierarchy for tests.
# Import real coc module (installed in venv).
import coc  # type: ignore[import]


# ═══════════════════════════════════════════════════════════════════════════
# coc_health.py
# ═══════════════════════════════════════════════════════════════════════════


class TestCocRetrySuccess:
    @pytest.mark.asyncio
    async def test_successful_call_increments_stats(self):
        from clashcontrol.coc_health import coc_retry, get_coc_stats, _stats
        _stats['total_calls'] = 0
        _stats['successful_calls'] = 0
        _stats['cycle_total_calls'] = 0

        op = AsyncMock(return_value="ok")
        result = await coc_retry(op, "test_op")
        assert result == "ok"
        op.assert_awaited_once()
        stats = get_coc_stats()
        assert stats['successful_calls'] >= 1

    @pytest.mark.asyncio
    async def test_slow_call_logged(self):
        """Calls > 2s should trigger debug log."""
        from clashcontrol.coc_health import coc_retry

        async def slow():
            await asyncio.sleep(0)  # instant for test; we mock time
            return "ok"

        with patch("clashcontrol.coc_health.time") as mock_time:
            mock_time.time.side_effect = [0.0, 3.0]  # call_start, elapsed check
            result = await coc_retry(slow, "slow_test")
            assert result == "ok"


class TestCocRetryNotFound:
    @pytest.mark.asyncio
    async def test_raises_immediately_no_retry(self):
        from clashcontrol.coc_health import coc_retry, _stats
        _stats['api_errors'] = 0

        op = AsyncMock(side_effect=coc.NotFound(MagicMock(), "gone"))
        with pytest.raises(coc.NotFound):
            await coc_retry(op, "test_nf", max_retries=2)
        assert op.await_count == 1  # No retry


class TestCocRetryPrivateWarLog:
    @pytest.mark.asyncio
    async def test_raises_immediately_counts_success(self):
        from clashcontrol.coc_health import coc_retry, _stats
        before = _stats['successful_calls']

        op = AsyncMock(side_effect=coc.PrivateWarLog(MagicMock(), "private"))
        with pytest.raises(coc.PrivateWarLog):
            await coc_retry(op, "test_pwl")
        assert op.await_count == 1
        assert _stats['successful_calls'] == before + 1  # Counted as success


class TestCocRetryMaintenance:
    def setup_method(self):
        # Reset the per-cycle global maintenance flag before each test so tests
        # don't bleed state into each other.
        from clashcontrol.coc_health import clear_maintenance_detection
        clear_maintenance_detection()

    @pytest.mark.asyncio
    async def test_raises_immediately_no_retry(self):
        # Maintenance should raise on the very first attempt — no sleep, no retry.
        # CoC outages last 10-30+ min so per-clan retries are pointless.
        from clashcontrol.coc_health import coc_retry
        op = AsyncMock(side_effect=coc.Maintenance(MagicMock(), "maint"))
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(coc.Maintenance):
                await coc_retry(op, "maint_test", max_retries=2)
            assert op.await_count == 1  # called once, no retry
            # No maintenance backoff sleep (DEV-throttle sleeps are all < 1s)
            backoff_calls = [c for c in mock_sleep.call_args_list if c[0][0] >= 1]
            assert len(backoff_calls) == 0

    @pytest.mark.asyncio
    async def test_sets_maintenance_detected_flag(self):
        # After any coc.Maintenance, is_maintenance_detected() must return True.
        from clashcontrol.coc_health import coc_retry, is_maintenance_detected
        assert not is_maintenance_detected()
        op = AsyncMock(side_effect=coc.Maintenance(MagicMock(), "maint"))
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(coc.Maintenance):
                await coc_retry(op, "maint_flag_test", max_retries=1)
        assert is_maintenance_detected()

    @pytest.mark.asyncio
    async def test_subsequent_calls_also_raise_immediately(self):
        # With the flag already set, a second independent call raises without sleeping.
        from clashcontrol.coc_health import coc_retry, is_maintenance_detected
        import clashcontrol.coc_health as _ch
        _ch._maintenance_detected = True  # simulate flag already set by earlier call
        op = AsyncMock(side_effect=coc.Maintenance(MagicMock(), "maint"))
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(coc.Maintenance):
                await coc_retry(op, "maint_second", max_retries=2)
            assert op.await_count == 1
            backoff_calls = [c for c in mock_sleep.call_args_list if c[0][0] >= 1]
            assert len(backoff_calls) == 0


class TestCocRetryGatewayOutage:
    """Tracker #0150: coc.GatewayError circuit breaker.

    coc.py raises GatewayError only after 5 internal attempts (~170 s for a timeout), so the
    wrapper must retry at most once and, once the breaker trips, refuse calls without touching
    the network. 2026-10-03: no breaker -> one update cycle ran 11,376 s.
    """

    def setup_method(self):
        from clashcontrol.coc_health import clear_gateway_outage_detection
        clear_gateway_outage_detection()

    def teardown_method(self):
        # Never leak a tripped breaker into other test classes (it fast-fails EVERY coc_retry).
        from clashcontrol.coc_health import clear_gateway_outage_detection
        clear_gateway_outage_detection()

    @staticmethod
    def _gw():
        return coc.GatewayError("The API timed out waiting for the request.")

    @pytest.mark.asyncio
    async def test_retries_at_most_once_even_with_more_retries_allowed(self):
        from clashcontrol.coc_health import coc_retry
        op = AsyncMock(side_effect=self._gw())
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(coc.GatewayError):
                await coc_retry(op, "gw_once", max_retries=2)
        assert op.await_count == 2  # first try + exactly one retry, not max_retries + 1

    @pytest.mark.asyncio
    async def test_recovers_on_the_single_retry(self):
        # The 2026-10-01 flakiness: every gateway error recovered on one retry.
        from clashcontrol.coc_health import coc_retry, is_gateway_outage_detected
        op = AsyncMock(side_effect=[self._gw(), "ok"])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            assert await coc_retry(op, "gw_recover", max_retries=2) == "ok"
        assert not is_gateway_outage_detected()

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_trip(self):
        import clashcontrol.coc_health as _ch
        op = AsyncMock(side_effect=self._gw())
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            # each call records 2 failures (try + retry); stay strictly below the threshold
            for _ in range((_ch._GATEWAY_TRIP_COUNT - 1) // 2):
                with pytest.raises(coc.GatewayError):
                    await _ch.coc_retry(op, "gw_below", max_retries=1)
        assert not _ch.is_gateway_outage_detected()

    @pytest.mark.asyncio
    async def test_trips_and_then_fast_fails_without_calling(self):
        import clashcontrol.coc_health as _ch
        failing = AsyncMock(side_effect=self._gw())
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            for _ in range(_ch._GATEWAY_TRIP_COUNT):
                with pytest.raises(coc.GatewayError):
                    await _ch.coc_retry(failing, "gw_trip", max_retries=0)
        assert _ch.is_gateway_outage_detected()

        healthy = AsyncMock(return_value="ok")
        with pytest.raises(coc.GatewayError):
            await _ch.coc_retry(healthy, "gw_after_trip")
        healthy.assert_not_awaited()  # refused before reaching the network
        assert _ch.get_gateway_fast_failed_count() == 1

    @pytest.mark.asyncio
    async def test_failures_outside_window_do_not_count(self):
        import clashcontrol.coc_health as _ch
        op = AsyncMock(side_effect=self._gw())
        clock = [1000.0]
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock), \
             patch("clashcontrol.coc_health.time.monotonic", side_effect=lambda: clock[0]):
            for _ in range(_ch._GATEWAY_TRIP_COUNT * 2):
                clock[0] += _ch._GATEWAY_TRIP_WINDOW_S  # every failure lands in a fresh window
                with pytest.raises(coc.GatewayError):
                    await _ch.coc_retry(op, "gw_spread", max_retries=0)
        assert not _ch.is_gateway_outage_detected()

    @pytest.mark.asyncio
    async def test_clear_resets_breaker(self):
        import clashcontrol.coc_health as _ch
        _ch._gateway_outage_detected = True
        _ch.clear_gateway_outage_detection()
        op = AsyncMock(return_value="ok")
        assert await _ch.coc_retry(op, "gw_cleared") == "ok"

    @pytest.mark.asyncio
    async def test_plain_500_still_uses_generic_retry_path(self):
        # A non-Gateway HTTPException keeps the existing max_retries behaviour.
        from clashcontrol.coc_health import coc_retry, is_gateway_outage_detected
        exc = coc.HTTPException(MagicMock(), "server error")
        exc.status = 500
        op = AsyncMock(side_effect=exc)
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(coc.HTTPException):
                await coc_retry(op, "http500_generic", max_retries=2)
        assert op.await_count == 3
        assert not is_gateway_outage_detected()


class TestCocRetryRateLimit:
    def _make_429(self, retry_after=None):
        exc = coc.HTTPException(MagicMock(), "rate limited")
        exc.status = 429
        if retry_after is not None:
            exc.retry_after = retry_after
        return exc

    @pytest.mark.asyncio
    async def test_rate_limit_retries_with_retry_after(self):
        from clashcontrol.coc_health import coc_retry, _stats
        before_rl = _stats['rate_limits']

        exc = self._make_429(retry_after=0.5)
        op = AsyncMock(side_effect=[exc, "ok"])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await coc_retry(op, "rl_test", max_retries=1)
            assert result == "ok"
            # Filter out DEV-throttle sleeps (< 1s) to find the rate-limit sleep
            rl_calls = [c for c in mock_sleep.call_args_list if c[0][0] == 0.5]
            assert len(rl_calls) == 1
        assert _stats['rate_limits'] == before_rl + 1

    @pytest.mark.asyncio
    async def test_rate_limit_exhausted(self):
        from clashcontrol.coc_health import coc_retry

        exc = self._make_429(retry_after=0.1)
        op = AsyncMock(side_effect=[exc, exc])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(coc.HTTPException):
                await coc_retry(op, "rl_fail", max_retries=0)

    @pytest.mark.asyncio
    async def test_rate_limit_no_retry_after_uses_backoff(self):
        from clashcontrol.coc_health import coc_retry

        exc = self._make_429()
        # Remove retry_after attr
        if hasattr(exc, 'retry_after'):
            delattr(exc, 'retry_after')
        op = AsyncMock(side_effect=[exc, "ok"])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await coc_retry(op, "rl_backoff", max_retries=1)
            assert result == "ok"
            # backoff = 2^0 = 1 (filter out DEV-throttle sleeps)
            backoff_calls = [c for c in mock_sleep.call_args_list if c[0][0] >= 1]
            assert len(backoff_calls) == 1
            assert backoff_calls[0][0][0] == 1


class TestCocRetryHTTPError:
    @pytest.mark.asyncio
    async def test_404_converted_to_not_found(self):
        from clashcontrol.coc_health import coc_retry

        exc = coc.HTTPException(MagicMock(), "not found")
        exc.status = 404
        op = AsyncMock(side_effect=exc)
        with pytest.raises(coc.NotFound):
            await coc_retry(op, "http404")
        assert op.await_count == 1

    @pytest.mark.asyncio
    async def test_500_retries_then_raises(self):
        from clashcontrol.coc_health import coc_retry

        exc = coc.HTTPException(MagicMock(), "server error")
        exc.status = 500
        op = AsyncMock(side_effect=exc)
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(coc.HTTPException):
                await coc_retry(op, "http500", max_retries=1)
        assert op.await_count == 2

    @pytest.mark.asyncio
    async def test_500_succeeds_on_retry(self):
        from clashcontrol.coc_health import coc_retry

        exc = coc.HTTPException(MagicMock(), "server error")
        exc.status = 500
        op = AsyncMock(side_effect=[exc, "recovered"])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            result = await coc_retry(op, "http500_ok", max_retries=1)
        assert result == "recovered"


class TestCocRetryGenericException:
    @pytest.mark.asyncio
    async def test_retries_then_raises(self):
        from clashcontrol.coc_health import coc_retry

        # Generic non-parse errors (RuntimeError) are retried up to max_retries.
        op = AsyncMock(side_effect=RuntimeError("bad"))
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="bad"):
                await coc_retry(op, "generic", max_retries=1)
        assert op.await_count == 2

    @pytest.mark.asyncio
    async def test_value_error_no_retry(self):
        """ValueError (e.g. unknown coc enum) must fast-fail without retrying."""
        from clashcontrol.coc_health import coc_retry

        op = AsyncMock(side_effect=ValueError("'minusOne' is not a valid BattleModifier"))
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await coc_retry(op, "generic", max_retries=2)
        # Must be called exactly once — no retries on parse errors.
        assert op.await_count == 1

    @pytest.mark.asyncio
    async def test_recovers_on_retry(self):
        from clashcontrol.coc_health import coc_retry

        op = AsyncMock(side_effect=[RuntimeError("bad"), "ok"])
        with patch("clashcontrol.coc_health.asyncio.sleep", new_callable=AsyncMock):
            result = await coc_retry(op, "generic_ok", max_retries=1)
        assert result == "ok"


class TestCocStats:
    def test_get_coc_stats_returns_copy(self):
        from clashcontrol.coc_health import get_coc_stats, _stats
        stats = get_coc_stats()
        stats['total_calls'] = 999
        assert get_coc_stats()['total_calls'] != 999

    def test_reset_cycle_stats(self):
        from clashcontrol.coc_health import reset_cycle_stats, _stats
        _stats['cycle_rate_limits'] = 10
        _stats['cycle_total_calls'] = 20
        _stats['cycle_sleep_time'] = 5.0
        reset_cycle_stats()
        assert _stats['cycle_rate_limits'] == 0
        assert _stats['cycle_total_calls'] == 0
        assert _stats['cycle_sleep_time'] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# discord_health.py
# ═══════════════════════════════════════════════════════════════════════════


def _make_discord_http_exc(status: int, code: int = 0, retry_after: float | None = None):
    """Build a mock discord.HTTPException with the specified status."""
    resp = MagicMock()
    resp.status = status
    resp.reason = "test"
    exc = discord.HTTPException(resp, "test error")
    exc.status = status
    exc.code = code
    if retry_after is not None:
        exc.retry_after = retry_after
    return exc


class TestDiscordRetrySuccess:
    @pytest.mark.asyncio
    async def test_successful_call(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        op = AsyncMock(return_value="hello")
        result = await dh.discord_retry(op, "test_ok")
        assert result == "hello"
        assert dh._stats['successful_calls'] == 1
        assert dh._stats['total_calls'] == 1


class TestDiscordRetry429:
    @pytest.mark.asyncio
    async def test_rate_limit_retries(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        exc = _make_discord_http_exc(429, retry_after=0.1)
        op = AsyncMock(side_effect=[exc, "ok"])
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await dh.discord_retry(op, "rl_ok", max_retries=1)
        assert result == "ok"
        assert dh._stats['rate_limits'] == 1
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rate_limit_exhausted(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        exc = _make_discord_http_exc(429, retry_after=0.1)
        op = AsyncMock(side_effect=exc)
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(discord.HTTPException):
                await dh.discord_retry(op, "rl_fail", max_retries=0)


class TestDiscordRetry10008:
    @pytest.mark.asyncio
    async def test_unknown_message_raises_immediately(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        exc = _make_discord_http_exc(404, code=10008)
        op = AsyncMock(side_effect=exc)
        with pytest.raises(discord.HTTPException):
            await dh.discord_retry(op, "unknown_msg")
        assert op.await_count == 1  # No retry
        assert dh._stats['api_errors'] == 1


class TestDiscordRetryHTTPError:
    @pytest.mark.asyncio
    async def test_500_retries_then_raises(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        exc = _make_discord_http_exc(500)
        op = AsyncMock(side_effect=exc)
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(discord.HTTPException):
                await dh.discord_retry(op, "http500", max_retries=1)
        assert op.await_count == 2

    @pytest.mark.asyncio
    async def test_http_error_recovers(self):
        import clashcontrol.discord_health as dh
        exc = _make_discord_http_exc(500)
        op = AsyncMock(side_effect=[exc, "recovered"])
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock):
            result = await dh.discord_retry(op, "http500_ok", max_retries=1)
        assert result == "recovered"


class TestDiscordRetryGeneric:
    @pytest.mark.asyncio
    async def test_generic_exception_retries(self):
        import clashcontrol.discord_health as dh
        op = AsyncMock(side_effect=[RuntimeError("boom"), "ok"])
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock):
            result = await dh.discord_retry(op, "generic", max_retries=1)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_generic_exhausted(self):
        import clashcontrol.discord_health as dh
        op = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("clashcontrol.discord_health.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="boom"):
                await dh.discord_retry(op, "generic_fail", max_retries=1)
        assert op.await_count == 2


class TestDiscordStats:
    def test_get_simple_discord_stats(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        dh._stats['total_calls'] = 10
        dh._stats['successful_calls'] = 9
        dh._stats['api_errors'] = 1
        dh._stats['rate_limits'] = 0
        result = dh.get_simple_discord_stats()
        assert result['total_calls'] == 10
        assert result['success_rate'] == 90.0

    def test_stats_zero_total(self):
        import clashcontrol.discord_health as dh
        dh.reset_stats()
        result = dh.get_simple_discord_stats()
        assert result['success_rate'] == 0

    def test_reset_stats(self):
        import clashcontrol.discord_health as dh
        dh._stats['total_calls'] = 50
        dh._stats['successful_calls'] = 40
        dh._stats['api_errors'] = 10
        dh._stats['rate_limits'] = 5
        dh.reset_stats()
        assert dh._stats['total_calls'] == 0
        assert dh._stats['successful_calls'] == 0
        assert dh._stats['api_errors'] == 0
        assert dh._stats['rate_limits'] == 0
