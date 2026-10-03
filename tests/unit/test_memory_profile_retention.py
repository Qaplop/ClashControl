"""End-to-end: the memory profile names what holds retained data (tracker #0150).

Runs the real arm -> retain -> snapshot path (start_memtrace_baseline + save_memtrace_snapshot)
against a cache that grows a payload-shaped structure between the two, and checks the written
report (a) ranks that shape as grown, (b) names the CACHE attribute holding it, and (c) has a GC
census that was not swamped by the tracemalloc snapshot's own tuples (the 2026-10-03 profile
reported 4,990,878 tuples and 317 dicts because the census ran after take_snapshot()).
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import dataclasses
import os
import tracemalloc
from types import SimpleNamespace

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import pytest

import QBcore
import clashcontrol.QBdiscocmdshelper_admin_command as admin_mod
from clashcontrol.config import CONFIG
from clashcontrol.QBdiscocmdshelper_admin_command import (
    save_memtrace_snapshot,
    start_memtrace_baseline,
)


def _cache() -> SimpleNamespace:
    return SimpleNamespace(
        coc_clan_cache=SimpleNamespace(cache={}, get_memory_usage_mb=lambda: 0.0),
        clan_name_cache={}, subscriptions={}, leaderboard_messages={}, user_accounts={},
        notification_state={}, clan_history={}, history_cache={}, temp_war_stats={},
        temp_war_objects={}, temp_war_metadata={}, server_config={}, coc_client=None,
    )


@pytest.fixture
def clean_trace_state():
    def _reset():
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        QBcore.memtrace_pending = False
        QBcore.memtrace_baseline = None
        QBcore.memtrace_shape_baseline = None
    _reset()
    yield
    _reset()


def test_profile_names_the_holder_of_grown_payloads(tmp_path, monkeypatch, clean_trace_state):
    # CONFIG is frozen: swap the admin module's reference for a copy pointing at tmp_path.
    monkeypatch.setattr(admin_mod, "CONFIG", dataclasses.replace(CONFIG, data_dir=str(tmp_path)))
    cache = _cache()

    asyncio.run(start_memtrace_baseline())
    assert QBcore.memtrace_shape_baseline is not None

    # "The ramp": payload-shaped dicts retained by a cache between arming and the dump.
    cache.temp_war_objects = {
        f"#T{i}": {"vvState": "inWar", "vvTeam": 15, "vvClan": {"t": i}, "vvMembers": [i]}
        for i in range(2000)
    }

    path = save_memtrace_snapshot(cache)
    report = open(path, encoding="utf-8").read()

    assert "[RETENTION" in report
    growth = report.split("Growth since the trace was armed", 1)[1]
    assert "vvState" in growth.splitlines()[1], "grown shape not ranked first"
    assert "CACHE.temp_war_objects" in report, "holder not named"
    assert QBcore.memtrace_shape_baseline is None  # reset with the other trace state

    census = report.split("[GC OBJECT COUNTS — top 15 by type]", 1)[1].split("[GC OBJECT", 1)[0]
    dict_line = next(l for l in census.splitlines() if l.strip().startswith("dict "))
    assert int(dict_line.split()[-1].replace(",", "")) >= 2000, (
        "GC census missed the payload dicts — it is running after take_snapshot() again"
    )
