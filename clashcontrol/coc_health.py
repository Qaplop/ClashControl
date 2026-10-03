"""
CoC API health monitoring with rate limit tracking and retry logic.

Provides:
- Retry wrapper for CoC API calls with rate limit handling
- Statistics tracking for all CoC API calls
- Per-cycle rate limit monitoring
- Automatic retry with exponential backoff

Usage:
    from clashcontrol.coc_health import coc_retry, get_coc_stats, reset_cycle_stats
    
    # Wrap all CoC API calls
    clan = await coc_retry(
        lambda: coc_client.get_clan(clan_tag),
        operation_name=f"get_clan({clan_tag})"
    )
    
    # Get statistics
    stats = get_coc_stats()
    print(f"Rate limits this cycle: {stats['cycle_rate_limits']}")
"""
import asyncio
import logging
import time
from collections import deque
from typing import TypeVar, Callable, Any, Dict, Optional
import aiohttp
import coc  # type: ignore[import]
import coc.http as coc_http_module  # type: ignore[import]
import coc.utils as coc_utils  # type: ignore[import]
from clashcontrol.config import CONFIG

T = TypeVar('T')

# Global statistics
_stats: Dict[str, Any] = {
    'total_calls': 0,
    'successful_calls': 0,
    'rate_limits': 0,
    'api_errors': 0,
    'cycle_rate_limits': 0,  # Resets each cycle
    'cycle_total_calls': 0,  # Resets each cycle
    'total_sleep_time': 0.0,  # Total seconds spent sleeping due to rate limits
    'cycle_sleep_time': 0.0,  # Sleep time in current cycle
    'cycle_calls_by_op': {},  # Resets each cycle — e.g. {'get_current_war': 261, 'get_league_war': 2000}
    'total_calls_by_op': {},  # Lifetime — same bucketing
}

# DEV-mode API throttle: minimum seconds between consecutive CoC API calls
# (global across all concurrent tasks).  Set to 0.0 to disable.
# Only applies when CONFIG.is_dev_mode is True.
# The Lock serialises entry so only one call sleeps at a time, guaranteeing
# at least _DEV_API_THROTTLE_S between the *start* of consecutive API calls.
_DEV_API_THROTTLE_S: float = 0.022   # 22 ms  →  max ~45 req/s globally
_dev_throttle_lock: Optional[asyncio.Lock] = None

# Optional reconnect callback — set by startup_login() so that coc_retry can
# re-authenticate the client if the aiohttp session is unexpectedly closed.
_reconnect_callback: Optional[Callable[[], Any]] = None

# Per-cycle CoC API maintenance detection flag.
# Set by the first coc_retry() call that receives coc.Maintenance.
# All subsequent concurrent coc_retry() calls check this flag and raise
# immediately — no 10-20s retry sleep — because CoC maintenance is a
# global outage: retrying per-clan is pointless and causes multi-thousand-
# second cycles (1889 clans × 30s/slot / 20 concurrent ≈ 2833s extra).
# Cleared at the start of each update cycle via clear_maintenance_detection().
_maintenance_detected: bool = False

# Per-cycle DNS failure detection flag.
# Set by the first coc_retry() call that encounters a DNS resolution failure.
# All subsequent concurrent coc_retry() calls with a DNS error fast-fail
# immediately — no retry sleep — because a DNS outage affects all hostnames
# equally: retrying per-clan wastes time and floods the log.
# Cleared at the start of each update cycle via clear_dns_detection().
_dns_failure_detected: bool = False

# Per-cycle CoC gateway-outage circuit breaker (tracker #0150).
# coc.py raises GatewayError when the API keeps timing out (or keeps answering 500/502/504)
# AFTER its own 5 internal attempts — with the default 30 s client timeout plus 1+3+5+7 s of
# backoff, one GatewayError already represents ~170 s of waiting. On 2026-10-03 the CoC API timed
# out for five hours; with no breaker every clan cost ~9 min (3 wrapper attempts x ~170 s), 50 at
# a time, and a single update cycle ran for 11,376 s while logging 5,300 lines of per-clan errors.
#
# Unlike Maintenance (a fast 503) and DNS failures (fail instantly), gateway timeouts are SLOW, so
# flagging alone is not enough: once tripped, coc_retry() refuses new calls up front (the
# pre-check at the top of its retry loop) instead of letting each one sit out its own timeout.
#
# Trip threshold: _GATEWAY_TRIP_COUNT failures within _GATEWAY_TRIP_WINDOW_S. Measured on PROD:
# the 2026-10-01 flakiness peaked at 11 gateway errors in any 60 s window and every one of them
# recovered on a single retry, while the 2026-10-03 outage hit 50 in 60 s (the entire Phase-1
# fetch concurrency failing together). 25 in 120 s separates the two with margin on both sides.
_GATEWAY_TRIP_COUNT: int = 25
_GATEWAY_TRIP_WINDOW_S: float = 120.0
_gateway_failure_times: "deque[float]" = deque()
_gateway_outage_detected: bool = False
_gateway_fast_failed: int = 0  # calls refused while tripped, this cycle (for the Phase-1 summary)

# Count of clan payloads repaired by apply_coc_library_patches()'s clanCapital
# shim (see that function).  Lifetime counter, surfaced via get_coc_stats() so a
# renewed spike is visible without grepping the log — the shim itself only logs
# the first occurrence, since it fires once per affected clan per cycle.
_capital_districts_fixups: int = 0


def apply_coc_library_patches() -> None:
    """
    Apply ClashControl's compatibility shims to the vendored ``coc.py`` library.

    Idempotent — safe to call repeatedly (``startup_login()`` doubles as the
    CoC-client reconnect callback, so it can run many times per process).

    Two shims. The second, ``_BoundedResponseCache`` (tracker #0150), is documented on that
    class. The first:

    ``coc.Clan._from_data`` — tolerate ``clanCapital`` without ``districts``.
        ``coc.py`` 4.0.0 (``coc/clans.py``, in ``Clan._from_data``) does::

            if data_get("clanCapital"):
                self._iter_capital_districts = (... for cddata in data_get("clanCapital")["districts"])

        i.e. it guards on ``clanCapital`` being truthy but then indexes
        ``["districts"]`` unconditionally.  From 2026-08-31 15:40 the CoC API
        started returning a non-empty ``clanCapital`` object with no
        ``districts`` key for some clans, so that subscript raises ``KeyError:
        'districts'``.  It raises during ``Clan`` construction (a generator
        expression evaluates its outermost iterable eagerly), so the *entire*
        clan fetch fails — not just the capital part.

        Impact before this shim (2026-09-01 PROD logs): 438 distinct clans,
        42,871 failed ``get_clan()`` calls in one day, each burning 3 HTTP
        requests plus 3s of retry backoff in one of only 20 concurrency slots
        (~65s added to every PHASE-1, roughly tripling it) — and those 438
        clans got no war tracking at all for the duration.

        The fix normalises the payload to ``districts: []`` before the original
        parser sees it, which lands ``capital_districts`` on the same empty list
        the library's own ``else`` branch produces.  Clan Capital data is not
        used by ClashControl (no DB table stores it), so an empty district list is a
        complete, lossless outcome here — the clan's war data, which is what the
        bot actually needs, parses normally.

        Patching ``Clan`` alone is sufficient: no other class in ``coc.py``
        subclasses it (``RankedClan``/``PlayerClan``/``WarClan``/
        ``ClanWarLeagueClan`` all derive from ``BaseClan``), and none of them
        touch ``clanCapital``.
    """
    # Second shim (tracker #0150), installed first so the Clan shim's early return below can
    # never skip it: replace coc.py's leaky HTTP response cache. Idempotent on its own.
    _install_bounded_response_cache()

    if getattr(coc.Clan, "_clashcontrol_capital_districts_patched", False):
        return

    # Reaching into coc.Clan's internals is the entire point of a compatibility shim, so
    # the protected-access findings here are suppressed narrowly rather than project-wide.
    _original_from_data = coc.Clan._from_data  # pyright: ignore[reportPrivateUsage]

    def _from_data_tolerating_missing_districts(self: Any, data: Dict[str, Any]) -> None:
        global _capital_districts_fixups
        capital = data.get("clanCapital")
        # Mutated in place rather than copied: this runs on every clan fetch, and
        # coc.py's own _from_data already mutates the payload in place (it writes
        # `builderBaseRank` into each memberList entry), so this is consistent with
        # how the library treats the dict it is handed.
        if isinstance(capital, dict) and not isinstance(capital.get("districts"), list):
            capital["districts"] = []
            _capital_districts_fixups += 1
            if _capital_districts_fixups == 1:
                logging.info(
                    "[COC-COMPAT] CoC API returned a clanCapital object without a 'districts' "
                    "key (clan %s) — normalising to an empty district list. This would otherwise "
                    "raise KeyError: 'districts' inside coc.py and fail the whole clan fetch. "
                    "Only logged once; running total is in get_coc_stats()['capital_districts_fixups'].",
                    data.get("tag", "<unknown>"),
                )
            else:
                logging.debug(
                    "[COC-COMPAT] clanCapital without 'districts' for clan %s (fixup #%d)",
                    data.get("tag", "<unknown>"), _capital_districts_fixups,
                )
        return _original_from_data(self, data)

    coc.Clan._from_data = _from_data_tolerating_missing_districts  # pyright: ignore[reportPrivateUsage]
    # Marker is a plain class attribute — Clan defines __slots__, which restricts
    # *instance* attributes only, so this is legal and makes the call idempotent.
    # pyright flags it because the attribute is (by design) not declared on Clan.
    coc.Clan._clashcontrol_capital_districts_patched = True  # pyright: ignore[reportAttributeAccessIssue]

    logging.info(
        "[COC-COMPAT] Applied coc.py compatibility shims (clanCapital.districts tolerance, "
        "bounded HTTP response cache)."
    )


class _BoundedResponseCache(coc_utils.FIFO):  # type: ignore[misc]
    """Drop-in replacement for coc.py's HTTP response cache (``coc.utils.FIFO``) — tracker #0150.

    coc.py 4.0.0's ``FIFO`` tracks insertion order in a private ``deque`` that only shrinks when
    the dict exceeds ``max_size``. But ``HTTPClient`` also removes entries itself when their
    Cache-Control max-age expires (``loop.call_later(delta, _cache_remove, key)``), and those
    removals never touch the deque. Two consequences, both verified against the real class:

    1. **Unbounded growth.** While the cache stays under its cap — every normal cycle — the deque
       is never popped, so it keeps one URL string per cached response forever (~1,100 per
       cycle, ~300k per day on PROD).
    2. **Silent KeyError on eviction.** Once the dict does exceed the cap, ``popleft()`` hands
       back keys whose entries the timers already deleted, and ``del`` raises ``KeyError``.
       ``HTTPClient.request()`` stores into the cache inside a ``try ... except (KeyError, ...)``
       meant for missing headers, so the error is swallowed — and the ``call_later`` that would
       expire the entry is skipped, leaving it to linger until FIFO eviction reaches it.

    This class keeps the same contract (bounded, oldest-first eviction, ``copy()`` returns
    ``self``) using the dict's own insertion order, so removals from either path stay
    consistent. It subclasses ``FIFO`` because ``HTTPClient.request()`` gates every lookup and
    store on ``isinstance(cache, FIFO)``.
    """

    def __init__(self, max_size: int) -> None:
        super().__init__(max_size)  # sets max_size, data={} and FIFO's (now unused) deque

    def __setitem__(self, key: Any, value: Any) -> None:
        # Re-insert at the end so a refreshed key counts as newest, like a fresh FIFO entry.
        self.data.pop(key, None)
        self.data[key] = value
        while len(self.data) > self.max_size:
            del self.data[next(iter(self.data))]

    def __getitem__(self, key: Any) -> Any:
        return self.data[key]

    def __contains__(self, key: object) -> bool:
        return key in self.data

    def copy(self) -> "_BoundedResponseCache":  # type: ignore[override]
        # Same intent as FIFO.copy(): rebuild the backing dict so a long-lived dict's freed
        # slots are released, while callers keep using this same instance.
        self.data = dict(self.data)
        return self


def _install_bounded_response_cache() -> None:
    """Make coc.py's ``HTTPClient`` build ``_BoundedResponseCache`` instead of ``FIFO``.

    ``HTTPClient.__init__`` and ``request()`` both resolve the name ``FIFO`` from the
    ``coc.http`` module namespace (``from .utils import FIFO``), and nothing else in coc.py
    uses it, so rebinding that one name is the whole patch. Must run before ``coc.Client``
    creates its HTTP client — ``startup_login()`` calls ``apply_coc_library_patches()`` first.
    Idempotent.
    """
    if coc_http_module.FIFO is not _BoundedResponseCache:
        coc_http_module.FIFO = _BoundedResponseCache  # pyright: ignore[reportAttributeAccessIssue]


def get_coc_http_cache_stats(client: Any) -> Dict[str, Any]:
    """Size of coc.py's HTTP response cache, for ``[MEM-GAUGES]`` and the memory profile.

    Returns ``{'entries': int, 'max': int, 'deque': int}`` — ``deque`` is FIFO's private key
    deque length (stays 0 with ``_BoundedResponseCache``; any other value means the shim is not
    in effect). Returns ``{}`` if the client or its cache is unavailable (no login yet,
    ``NO_COC_API``, cache disabled). Never raises.
    """
    try:
        cache = getattr(getattr(client, "http", None), "cache", None)
        if not isinstance(cache, coc_utils.FIFO):
            return {}
        deque_len = len(getattr(cache, "_FIFO__keys", ()) or ())
        return {"entries": len(cache.data), "max": cache.max_size, "deque": deque_len}
    except Exception:
        return {}


def set_reconnect_callback(callback: Callable[[], Any]) -> None:
    """Register an async callable that re-authenticates the CoC client."""
    global _reconnect_callback
    _reconnect_callback = callback

def clear_maintenance_detection() -> None:
    """Reset the per-cycle CoC maintenance flag.  Called at the start of each update cycle."""
    global _maintenance_detected
    _maintenance_detected = False

def clear_dns_detection() -> None:
    """Reset the per-cycle DNS failure flag.  Called at the start of each update cycle."""
    global _dns_failure_detected
    _dns_failure_detected = False

def is_dns_failure_detected() -> bool:
    """Return True if a DNS resolution failure was seen during the current cycle."""
    return _dns_failure_detected

def clear_gateway_outage_detection() -> None:
    """Reset the per-cycle CoC gateway-outage breaker.  Called at the start of each update cycle,
    so every cycle re-probes the API once instead of staying tripped indefinitely."""
    global _gateway_outage_detected, _gateway_fast_failed
    _gateway_outage_detected = False
    _gateway_fast_failed = 0
    _gateway_failure_times.clear()

def is_gateway_outage_detected() -> bool:
    """Return True if the gateway-outage breaker tripped during the current cycle."""
    return _gateway_outage_detected

def get_gateway_fast_failed_count() -> int:
    """Number of CoC calls refused without a network attempt since the breaker tripped."""
    return _gateway_fast_failed

def _record_gateway_failure(operation_name: str) -> None:
    """Count one GatewayError toward the breaker and trip it when the window threshold is hit."""
    global _gateway_outage_detected
    now = time.monotonic()
    _gateway_failure_times.append(now)
    while _gateway_failure_times and now - _gateway_failure_times[0] > _GATEWAY_TRIP_WINDOW_S:
        _gateway_failure_times.popleft()
    if not _gateway_outage_detected and len(_gateway_failure_times) >= _GATEWAY_TRIP_COUNT:
        _gateway_outage_detected = True
        logging.warning(
            f"[COC-GATEWAY-OUTAGE] {len(_gateway_failure_times)} CoC API gateway timeouts within "
            f"{_GATEWAY_TRIP_WINDOW_S:.0f}s (last: {operation_name}). Fast-failing all further "
            f"CoC API calls this cycle; next cycle will re-probe."
        )

def is_maintenance_detected() -> bool:
    """Return True if a coc.Maintenance error was seen during the current cycle."""
    return _maintenance_detected

def reset_cycle_stats() -> None:
    """
    Reset per-cycle statistics at the start of each update cycle.
    
    Called by periodic_main() at the start of each cycle to track
    rate limits and API usage per cycle.
    """
    global _stats
    _stats['cycle_rate_limits'] = 0
    _stats['cycle_total_calls'] = 0
    _stats['cycle_sleep_time'] = 0.0
    _stats['cycle_calls_by_op'] = {}

def get_coc_stats() -> Dict[str, Any]:
    """
    Get current CoC API statistics.
    
    Returns:
        Dictionary containing:
        - total_calls: Total API calls since bot start
        - successful_calls: Successful API calls
        - rate_limits: Total 429 errors encountered
        - api_errors: Total API errors (excluding 429)
        - cycle_rate_limits: 429 errors in current cycle
        - cycle_total_calls: API calls in current cycle
        - total_sleep_time: Total seconds slept due to rate limits
        - cycle_sleep_time: Seconds slept in current cycle
        - capital_districts_fixups: Clan payloads repaired by the clanCapital
          compatibility shim (see apply_coc_library_patches)
    """
    _snapshot = _stats.copy()
    _snapshot['capital_districts_fixups'] = _capital_districts_fixups
    return _snapshot

async def coc_retry(
    operation: Callable[[], T],
    operation_name: str = "coc_api_call",
    max_retries: int = 2
) -> T:
    """
    Retry wrapper for CoC API calls with comprehensive error handling and rate limit tracking.
    
    Handles:
        - coc.HTTPException (including 429 rate limits with automatic retry)
        - coc.Maintenance (automatic retry with backoff)
        - coc.NotFound (no retry, immediate re-raise)
        - coc.PrivateWarLog (no retry, immediate re-raise)
        - Other exceptions (retry with exponential backoff)
    
    Rate Limit Handling:
        - Detects 429 errors via exception attributes or error messages
        - Extracts retry_after duration from exception
        - Automatically sleeps for the specified duration
        - Logs sleep duration and total call time
        - Tracks statistics for monitoring
    
    Args:
        operation: Async function to execute (no arguments)
        operation_name: Name for logging purposes (e.g., "get_clan(#2C9UR9GJY)")
        max_retries: Maximum retry attempts (default: 2)
    
    Returns:
        Result of the operation
        
    Raises:
        The last exception encountered if all retries fail
        coc.NotFound: Immediately if resource not found
        coc.PrivateWarLog: Immediately if war log is private
        
    Features:
        - Automatic retry with exponential backoff
        - Special handling for rate limits (HTTP 429)
        - Respects Retry-After headers
        - Comprehensive statistics tracking
        - Detailed logging for debugging
        
    Example:
        clan = await coc_retry(
            lambda: coc_client.get_clan("#2C9UR9GJY"),
            operation_name="get_clan(#2C9UR9GJY)"
        )
    """
    global _stats
    _stats['total_calls'] += 1
    _stats['cycle_total_calls'] += 1
    # Per-operation bucketing: extract function name before '(' e.g. "get_current_war"
    _op_key = operation_name.split('(')[0].strip()
    _stats['cycle_calls_by_op'][_op_key] = _stats['cycle_calls_by_op'].get(_op_key, 0) + 1
    _stats['total_calls_by_op'][_op_key] = _stats['total_calls_by_op'].get(_op_key, 0) + 1

    call_start_time = time.time()
    
    for attempt in range(max_retries + 1):
        # Gateway-outage breaker pre-check (tracker #0150): once tripped, refuse the call before
        # it reaches the network. Checked on every attempt, so an in-flight call that is about to
        # retry also stops here instead of sitting out another ~170 s timeout.
        if _gateway_outage_detected:
            global _gateway_fast_failed
            _gateway_fast_failed += 1
            _stats['api_errors'] += 1
            logging.debug(f"[COC-GATEWAY-OUTAGE] Fast-fail (outage active): {operation_name}")
            raise coc.GatewayError("fast-fail: CoC API gateway outage detected this cycle")
        try:
            # DEV-only global rate limiter: enforce min gap between API calls
            if _DEV_API_THROTTLE_S > 0 and CONFIG.is_dev_mode:
                global _dev_throttle_lock
                if _dev_throttle_lock is None:
                    _dev_throttle_lock = asyncio.Lock()
                async with _dev_throttle_lock:
                    await asyncio.sleep(_DEV_API_THROTTLE_S)

            result = await operation()  # type: ignore[misc]
            _stats['successful_calls'] += 1
            
            # Log slow calls
            elapsed = time.time() - call_start_time
            if elapsed >= 2.0:
                logging.info(f"[COC-API-SLOW] {operation_name} completed in {elapsed:.2f}s")
            
            return result  # type: ignore[return-value]
            
        except coc.NotFound:
            # Don't retry NotFound errors (specific before HTTPException)
            _stats['api_errors'] += 1
            # debug, not warning: NotFound is routine/expected for several call sites, not just
            # an unusual failure. In particular get_league_group() 404s constantly under normal
            # operation — _find_active_cwl_war_for_clan() (QBhelperfunctions.py) calls it for
            # every actively-tracked clan every cycle, and a 404 there just means "not currently
            # in CWL" (true for the vast majority of clans most of the time, since CWL runs ~1
            # week/month). A brief WARNING-level version of this line (2026-08-09, while chasing
            # the incident in COPILOT_PITFALLS_COOKBOOK.md Pitfall 24) flooded PROD's log at fleet
            # scale for exactly this reason — reverted back to debug.
            logging.debug(f"[COC-API-NOTFOUND] {operation_name} - resource not found (no retry)")
            raise
            
        except coc.PrivateWarLog:
            # Don't retry PrivateWarLog errors - this is expected for clans with private warlogs
            # Count as successful: the API responded correctly with a definitive 403, not an API failure.
            # Not counting this caused success_rate < 100% with 0 errors in /status.
            #
            # Note: for get_league_war() specifically, coc.py relabels ANY 403 from the CWL
            # war-by-tag endpoint as PrivateWarLog — it's not actually gated by a clan's
            # warlog-public setting there. A broken/revoked API key surfaces through this same
            # branch (root-caused 2026-08-09, see clashcontrol/docs/COPILOT_PITFALLS_COOKBOOK.md
            # Pitfall 24) — startup_login()'s _validate_coc_api_keys() (ClashControl.py) now catches
            # that case proactively at startup instead of relying on this log line, so this
            # stays at debug for both call sites.
            _stats['successful_calls'] += 1
            logging.debug(f"[COC-API-INFO] {operation_name} - War log is private (no retry needed)")
            raise
            
        except coc.Maintenance as e:
            # CoC maintenance is a global outage lasting 10-30+ min.
            # Retrying per-clan within the same cycle is pointless — every call
            # will get the same 503 until maintenance ends.  So we:
            #   1. On first detection: set the cycle-wide flag, log one WARNING, raise.
            #   2. On any subsequent detection (flag already set): log at DEBUG, raise.
            # No sleep, no retry, ever.  The next update cycle (≥5 min later) will
            # re-probe and succeed once CoC is back up.
            _stats['api_errors'] += 1
            global _maintenance_detected
            if not _maintenance_detected:
                _maintenance_detected = True
                logging.warning(
                    f"[COC-MAINTENANCE] CoC API under maintenance — detected via "
                    f"{operation_name}. Skipping all remaining API calls this cycle; "
                    f"next cycle will re-probe."
                )
            else:
                logging.debug(f"[COC-MAINTENANCE] Fast-fail (maintenance active): {operation_name}")
            raise

        except coc.GatewayError as e:
            # Tracker #0150. coc.py has ALREADY retried this request 5 times internally
            # (timeouts and 500/502/504 alike) before raising, ~170 s of waiting for a timeout.
            # So: at most ONE wrapper retry (the 2026-10-01 flakiness recovered on exactly one),
            # count it toward the breaker, and never retry once the breaker has tripped.
            _stats['api_errors'] += 1
            _record_gateway_failure(operation_name)
            if attempt < min(max_retries, 1) and not _gateway_outage_detected:
                logging.warning(
                    f"[COC-API-ERROR] {operation_name} gateway error after coc.py's internal "
                    f"retries, retrying once in 1s: {e}"
                )
                await asyncio.sleep(1)
                continue
            if _gateway_outage_detected:
                logging.debug(f"[COC-GATEWAY-OUTAGE] {operation_name} failed: {e}")
            else:
                logging.warning(f"[COC-API-ERROR] {operation_name} gateway error, giving up: {e}")
            raise

        except coc.HTTPException as e:
            # Check if it's a rate limit error (429)
            is_rate_limit = False
            retry_after = None
            
            if hasattr(e, 'status') and e.status == 429:
                is_rate_limit = True
                retry_after = getattr(e, 'retry_after', None)
            
            if is_rate_limit:
                _stats['rate_limits'] += 1
                _stats['cycle_rate_limits'] += 1
                
                # Use retry_after from exception or calculate backoff
                if retry_after is None:
                    retry_after = 2 ** attempt  # Exponential backoff fallback
                
                elapsed = time.time() - call_start_time
                
                if attempt < max_retries:
                    logging.warning(
                        f"⚠️ [COC-RATE-LIMIT] {operation_name} - 429 Rate Limit Hit! "
                        f"Sleeping {retry_after:.1f}s (attempt {attempt + 1}/{max_retries + 1}, "
                        f"elapsed: {elapsed:.1f}s)"
                    )
                    
                    # Track sleep time
                    _stats['total_sleep_time'] += retry_after
                    _stats['cycle_sleep_time'] += retry_after
                    
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    # Max retries exhausted
                    total_elapsed = time.time() - call_start_time
                    logging.error(
                        f"❌ [COC-RATE-LIMIT] {operation_name} - Failed after {max_retries + 1} attempts. "
                        f"Total time: {total_elapsed:.1f}s, Total sleep: {_stats['cycle_sleep_time']:.1f}s"
                    )
                    _stats['api_errors'] += 1
                    raise
            else:
                # Other HTTP error - check if it's 404 (NotFound)
                status_code = getattr(e, 'status', 0)
                if status_code == 404:
                    # Don't retry 404 errors (resource not found)
                    _stats['api_errors'] += 1
                    logging.debug(f"[COC-API-ERROR] {operation_name} HTTP 404 Not Found - not retrying")
                    raise coc.NotFound(e.message if hasattr(e, 'message') else str(e))  # type: ignore[attr-defined]
                
                # Retry other HTTP errors
                _stats['api_errors'] += 1
                if attempt < max_retries:
                    backoff_time = 2 ** attempt
                    logging.warning(
                        f"[COC-API-ERROR] {operation_name} HTTP error {status_code}, "
                        f"retrying in {backoff_time}s (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    logging.error(f"[COC-API-ERROR] {operation_name} failed after {max_retries + 1} attempts: {e}")
                    raise
                    
        except Exception as e:
            # Generic exception handling
            _stats['api_errors'] += 1

            # ValueError/KeyError mean the coc.py library could not parse the API
            # response (e.g. an unknown enum value like a new BattleModifier, or a
            # key the library indexes unconditionally that the API stopped sending).
            # Retrying will produce the exact same error every time — fast-fail
            # immediately.
            # KeyError added 2026-09-01: `KeyError: 'districts'` (clanCapital without
            # a districts key — see apply_coc_library_patches() for the actual fix)
            # produced 42,871 failures in one day, each retried 3x with 1s+2s backoff
            # for no possible benefit, adding ~65s to every PHASE-1. The shim above
            # fixes that specific key; this makes the *next* payload-shape change cost
            # one failed call instead of three plus 3s of a concurrency slot.
            if isinstance(e, (ValueError, KeyError)):
                logging.error(
                    f"[COC-API-ERROR] {operation_name} failed after 1 attempt "
                    f"(parse error, no retry): {type(e).__name__}: {e}"
                )
                raise

            # DNS failure circuit breaker — mirrors the CoC maintenance pattern.
            # During an ISP/resolver outage, every concurrent clan fetch will get the
            # same error.  Retrying is pointless and floods the log.
            if isinstance(e, aiohttp.ClientConnectorDNSError):
                global _dns_failure_detected
                if not _dns_failure_detected:
                    _dns_failure_detected = True
                    logging.warning(
                        f"[COC-DNS-OUTAGE] DNS resolution failure detected via "
                        f"{operation_name}. Fast-failing all CoC API calls this cycle; "
                        f"next cycle will re-probe. Error: {e}"
                    )
                else:
                    logging.debug(f"[COC-DNS-OUTAGE] Fast-fail (DNS outage active): {operation_name}")
                raise

            # Detect closed aiohttp session.  If maintenance is active the session was
            # intentionally closed — abort immediately without retrying or reconnecting.
            # Otherwise attempt re-authentication via the registered reconnect callback.
            if "Session is closed" in str(e):
                _in_maintenance = False
                try:
                    import QBcore as _qbcore
                    _in_maintenance = _qbcore.maintenance_mode
                except ImportError:
                    pass
                if _in_maintenance:
                    logging.debug(
                        f"[COC-API-MAINT] {operation_name} — session closed for "
                        f"maintenance, aborting without retry"
                    )
                    raise e  # fail immediately, don't retry, don't reconnect
            if "Session is closed" in str(e) and _reconnect_callback is not None:
                logging.warning(
                    f"[COC-API-SESSION] {operation_name} - aiohttp session closed, "
                    f"re-authenticating CoC client (attempt {attempt + 1})"
                )
                try:
                    await _reconnect_callback()
                    logging.info("[COC-API-SESSION] CoC client re-authenticated successfully, retrying call.")
                except Exception as reconnect_e:
                    logging.error(f"[COC-API-SESSION] Re-authentication failed: {reconnect_e}")
                    raise e
                continue  # retry the same attempt without consuming a retry slot
            if attempt < max_retries:
                backoff_time = 2 ** attempt
                logging.warning(
                    f"[COC-API-ERROR] {operation_name} error, retrying in {backoff_time}s "
                    f"(attempt {attempt + 1}): {type(e).__name__}: {e}"
                )
                await asyncio.sleep(backoff_time)
            else:
                logging.error(
                    f"[COC-API-ERROR] {operation_name} failed after {max_retries + 1} attempts: "
                    f"{type(e).__name__}: {e}"
                )
                raise
    
    raise RuntimeError("Retry logic error - should not reach here")
