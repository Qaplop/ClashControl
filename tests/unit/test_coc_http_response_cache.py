"""Tests for the bounded coc.py HTTP response cache shim (tracker #0150).

Uses the REAL coc.py classes on purpose: the question under test is how the installed library
behaves, and a mock would keep passing after a coc.py upgrade changed it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import coc  # type: ignore[import]
import coc.http as coc_http  # type: ignore[import]
import coc.utils as coc_utils  # type: ignore[import]
import pytest

from clashcontrol.coc_health import (
    _BoundedResponseCache,
    _install_bounded_response_cache,
    apply_coc_library_patches,
    get_coc_http_cache_stats,
)


class TestUpstreamFifoCanary:
    """Pins the coc.py bug the shim exists for. If this starts failing, coc.py fixed its FIFO:
    re-evaluate whether _BoundedResponseCache is still needed (keep it until then)."""

    def test_upstream_fifo_raises_keyerror_after_timer_removal(self):
        fifo = coc_utils.FIFO(2)
        fifo["a"] = 1
        fifo["b"] = 2
        del fifo["a"]  # what HTTPClient._cache_remove does when max-age expires
        fifo["c"] = 3  # len 2, fine
        with pytest.raises(KeyError):
            fifo["d"] = 4  # len 3 > 2 -> popleft() returns the dead key 'a'

    def test_upstream_fifo_key_deque_grows_while_under_cap(self):
        fifo = coc_utils.FIFO(10)
        for i in range(100):
            fifo[f"k{i % 3}"] = i  # only 3 distinct keys, cache never exceeds its cap
        assert len(fifo) == 3
        assert len(fifo._FIFO__keys) == 100  # one deque entry per store, never trimmed


class TestBoundedResponseCache:
    def test_is_a_fifo_for_coc_isinstance_checks(self):
        assert isinstance(_BoundedResponseCache(5), coc_utils.FIFO)

    def test_removal_then_overflow_does_not_raise(self):
        cache = _BoundedResponseCache(2)
        cache["a"] = 1
        cache["b"] = 2
        del cache["a"]
        cache["c"] = 3
        cache["d"] = 4  # upstream FIFO raises KeyError here
        assert list(cache.data) == ["c", "d"]

    def test_bounded_oldest_first(self):
        cache = _BoundedResponseCache(3)
        for i in range(10):
            cache[f"k{i}"] = i
        assert list(cache.data) == ["k7", "k8", "k9"]

    def test_refreshed_key_becomes_newest(self):
        cache = _BoundedResponseCache(2)
        cache["a"] = 1
        cache["b"] = 2
        cache["a"] = 3  # refresh
        cache["c"] = 4  # evicts the oldest, which is now 'b'
        assert dict(cache.data) == {"a": 3, "c": 4}

    def test_key_deque_stays_empty(self):
        cache = _BoundedResponseCache(10)
        for i in range(100):
            cache[f"k{i % 3}"] = i
        assert len(cache._FIFO__keys) == 0

    def test_contains_getitem_and_missing_key(self):
        cache = _BoundedResponseCache(2)
        cache["a"] = 1
        assert "a" in cache and cache["a"] == 1
        assert "z" not in cache
        with pytest.raises(KeyError):
            cache["z"]

    def test_copy_returns_same_instance(self):
        # HTTPClient._cache_remove does `self.cache = self.cache.copy()`.
        cache = _BoundedResponseCache(2)
        cache["a"] = 1
        assert cache.copy() is cache
        assert cache["a"] == 1


class TestInstall:
    def test_patch_rebinds_coc_http_fifo(self):
        apply_coc_library_patches()
        assert coc_http.FIFO is _BoundedResponseCache
        _install_bounded_response_cache()  # idempotent
        assert coc_http.FIFO is _BoundedResponseCache

    def test_real_http_client_builds_bounded_cache(self):
        apply_coc_library_patches()
        loop = asyncio.new_event_loop()
        try:
            http = coc_http.HTTPClient(
                client=None, loop=loop, email=None, password=None, key_names="t",
                key_count=1, key_scopes="clash", throttle_limit=10, cache_max_size=5,
            )
        finally:
            loop.close()
        assert type(http.cache) is _BoundedResponseCache
        assert http.cache.max_size == 5

    def test_zero_disables_cache(self):
        apply_coc_library_patches()
        loop = asyncio.new_event_loop()
        try:
            http = coc_http.HTTPClient(
                client=None, loop=loop, email=None, password=None, key_names="t",
                key_count=1, key_scopes="clash", throttle_limit=10, cache_max_size=0,
            )
        finally:
            loop.close()
        assert not isinstance(http.cache, coc_utils.FIFO)  # request() skips caching entirely


class TestCacheStats:
    def test_stats_for_bounded_cache(self):
        cache = _BoundedResponseCache(4)
        cache["a"] = 1
        client = SimpleNamespace(http=SimpleNamespace(cache=cache))
        assert get_coc_http_cache_stats(client) == {"entries": 1, "max": 4, "deque": 0}

    def test_stats_empty_when_unavailable(self):
        assert get_coc_http_cache_stats(None) == {}
        assert get_coc_http_cache_stats(SimpleNamespace(http=SimpleNamespace(cache=0))) == {}

    def test_coc_client_accepts_cache_max_size(self):
        # ClashControl passes CONFIG.coc_http_cache_max_entries through coc.Client.
        loop = asyncio.new_event_loop()
        try:
            client = coc.Client(key_count=1, cache_max_size=7, loop=loop)
        finally:
            loop.close()
        assert client.cache_max_size == 7
