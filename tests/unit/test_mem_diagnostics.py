"""Tests for clashcontrol/mem_diagnostics.py (tracker #0150).

The retention report exists to NAME what keeps memory alive. These tests build real object
graphs (no mocks of gc) with API-payload-shaped dicts held in known places and assert the report
names each holder — the property the next PROD profile depends on.
"""
from __future__ import annotations

import asyncio
import sys
import types
from collections import Counter
from types import SimpleNamespace

import pytest

from clashcontrol.coc_health import _BoundedResponseCache
from clashcontrol.mem_diagnostics import (
    build_retention_report,
    dict_shape_census,
    format_mem_gauges,
)


def _war_payload(i: int) -> dict:
    # Shape mimics a currentwar response: outer dict holds nested containers -> gc-tracked.
    return {
        "zzState": "inWar", "zzTeamSize": 15, "zzPrep": f"2026{i}", "zzClan": {"tag": f"#A{i}"},
        "zzOpponent": {"tag": f"#B{i}"}, "zzMembers": [{"tag": f"#M{i}"}],
    }


def _clan_payload(i: int) -> dict:
    return {"yyTag": f"#C{i}", "yyName": f"clan{i}", "yyLevel": i, "yyMemberList": [{"t": i}]}


def _fake_cache() -> SimpleNamespace:
    return SimpleNamespace(
        coc_clan_cache=SimpleNamespace(cache={}),
        temp_war_objects={}, temp_war_stats={}, temp_war_metadata={},
        notification_state={}, history_cache={},
    )


class TestShapeCensus:
    def test_counts_payload_shapes(self):
        held = [_war_payload(i) for i in range(50)]
        census = dict_shape_census()
        shape = tuple(list(held[0])[:8])
        assert census[shape] >= 50

    def test_ignores_tiny_and_non_str_key_dicts(self):
        held = [{1: "a", 2: "b", 3: "c"} for _ in range(10)] + [{"a": 1} for _ in range(10)]
        census = dict_shape_census(held)
        assert sum(census.values()) == 0


class TestRetentionReport:
    # Each holder test passes a shape baseline taken before its payloads exist, as the
    # RSS-restart path does: growth ranking is what selects the shapes to chase. Without one the
    # report chases the most numerous data shapes on the whole heap, which in a full test run
    # are other tests' leftovers.
    def test_names_cache_attribute_holder(self):
        baseline = dict_shape_census()
        cache = _fake_cache()
        cache.temp_war_objects = {f"#T{i}": _war_payload(i) for i in range(300)}
        text = "\n".join(build_retention_report(cache, shape_baseline=baseline, budget_s=30))
        assert "zzState" in text
        assert "CACHE.temp_war_objects" in text

    def test_names_coc_http_cache_holder(self):
        baseline = dict_shape_census()
        cache = _fake_cache()
        http_cache = _BoundedResponseCache(1000)
        for i in range(300):
            http_cache[f"/clans/%23C{i}"] = _clan_payload(i)
        client = SimpleNamespace(http=SimpleNamespace(cache=http_cache))
        text = "\n".join(build_retention_report(cache, client, baseline, budget_s=30))
        assert "yyTag" in text
        assert "coc.py HTTP response cache" in text

    def test_names_module_global_holder(self):
        baseline = dict_shape_census()
        mod = types.ModuleType("fake_leaky_module_0150")
        mod.LEAK = [{"xxA": i, "xxB": i, "xxC": i, "xxD": [i]} for i in range(300)]
        sys.modules[mod.__name__] = mod
        try:
            text = "\n".join(
                build_retention_report(_fake_cache(), shape_baseline=baseline, budget_s=30)
            )
        finally:
            del sys.modules[mod.__name__]
        assert "module fake_leaky_module_0150 globals ['LEAK']" in text

    def test_growth_section_ranks_new_shape_first(self):
        baseline = dict_shape_census()
        cache = _fake_cache()
        cache.temp_war_stats = {f"#G{i}": {"wwA": i, "wwB": i, "wwC": [i]} for i in range(500)}
        lines = build_retention_report(cache, shape_baseline=baseline, budget_s=30)
        growth_idx = next(i for i, l in enumerate(lines) if "Growth since the trace was armed" in l)
        assert "wwA" in lines[growth_idx + 1]
        assert "+      500" in lines[growth_idx + 1]

    def test_zero_budget_still_returns_and_says_so(self):
        baseline = dict_shape_census()
        cache = _fake_cache()
        cache.temp_war_objects = {f"#T{i}": _war_payload(i) for i in range(50)}
        text = "\n".join(build_retention_report(cache, shape_baseline=baseline, budget_s=0))
        assert "time budget reached" in text
        assert "retention analysis took" in text

    def test_never_raises(self):
        class Hostile:
            def __getattribute__(self, name):
                raise RuntimeError("boom")
        lines = build_retention_report(Hostile(), Hostile(), budget_s=5)
        assert lines[-1].startswith("  (retention analysis took")


class TestMemGauges:
    def test_reports_cache_sizes_without_loop(self):
        cache = _fake_cache()
        cache.temp_war_objects = {"#a": 1, "#b": 2}
        line = format_mem_gauges(cache)
        assert "temp_war_objects=2" in line
        assert "py_blocks=" in line
        assert "tasks=" not in line  # no running loop -> field omitted, not an error

    @pytest.mark.asyncio
    async def test_includes_loop_and_coc_http_fields(self):
        http_cache = _BoundedResponseCache(2000)
        http_cache["/x"] = {"a": 1}
        client = SimpleNamespace(http=SimpleNamespace(cache=http_cache))
        line = format_mem_gauges(_fake_cache(), client)
        assert "coc_http=1/2000" in line
        assert "tasks=" in line and "timers=" in line

    def test_never_raises_on_broken_cache(self):
        assert isinstance(format_mem_gauges(object(), object()), str)
