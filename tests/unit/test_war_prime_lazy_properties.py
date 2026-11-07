"""Shared coc.py war objects must be fully evaluated before two threads can read them.

PROD 2026-11-01 15:08 and 2026-11-02 09:49: ``save_war_object`` failed with
``ValueError: generator already executing`` for a CWL war. ``get_league_war()`` hands the SAME
object to both clans of a match, Phase 1 saves each clan in its own ``asyncio.to_thread``, and
coc.py's ``WarClan.members`` / ``ClanWarMember.attacks`` are cached properties backed by one-shot
generators — two threads meeting inside one generator raise. ``prime_war_lazy_properties()``
evaluates them once on the loop thread (called from ``get_league_war``).

Also pins the blank-tag guard in ``get_player_list`` (PROD 2026-11-01 14:40: /clan management in a
guild without clans asked the CoC API for clan "" three times and logged an ERROR).
"""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clashcontrol.cache_manager import CacheManager, prime_war_lazy_properties


class _GeneratorBackedSide:
    """Mimics coc.war_clans.WarClan: a cached property fed by a one-shot generator."""

    def __init__(self, hold_generator: bool = False) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        if not hold_generator:
            self.release.set()
        self._iter = self._gen()
        self._members = None
        self.attacks_read = 0

    def _gen(self):
        self.started.set()
        self.release.wait(5)
        yield "m1"
        yield "m2"

    @property
    def members(self):
        if self._members is None:
            self._members = list(self._iter)
        return self._members

    @property
    def attacks(self):
        self.attacks_read += 1
        return list(self.members)

    @property
    def defenses(self):
        return []


def _war(**kw) -> SimpleNamespace:
    return SimpleNamespace(clan=_GeneratorBackedSide(**kw), opponent=_GeneratorBackedSide(**kw))


def test_unprimed_object_really_raises_when_two_threads_meet() -> None:
    """Documents the failure the priming exists to prevent (so the test below means something)."""
    side = _GeneratorBackedSide(hold_generator=True)
    first = threading.Thread(target=lambda: side.members)
    first.start()
    try:
        assert side.started.wait(5)
        with pytest.raises(ValueError, match="generator already executing"):
            _ = side.members
    finally:
        side.release.set()
        first.join(5)


def test_priming_consumes_every_generator_before_threads_share_the_object() -> None:
    war = _war()
    prime_war_lazy_properties(war)

    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for side in (war.clan, war.opponent):
                assert side.members == ["m1", "m2"]
                assert side.attacks == ["m1", "m2"]
        except BaseException as ex:  # noqa: BLE001
            errors.append(ex)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5)

    assert errors == []
    # the generators were drained by priming, not by a racing reader
    assert next(war.clan._iter, None) is None
    assert next(war.opponent._iter, None) is None


def test_priming_tolerates_none_and_broken_sides() -> None:
    prime_war_lazy_properties(None)
    prime_war_lazy_properties(SimpleNamespace(clan=None, opponent=None))

    class _Broken:
        @property
        def members(self):
            raise RuntimeError("partial war object")

    prime_war_lazy_properties(SimpleNamespace(clan=_Broken(), opponent=_GeneratorBackedSide()))


@pytest.mark.asyncio
async def test_get_league_war_primes_before_returning_and_caching() -> None:
    cm = CacheManager()
    cm.coc_client = MagicMock()
    war = _war()
    war.state = "inWar"
    with patch("clashcontrol.cache_manager.coc_retry", new_callable=AsyncMock, return_value=war):
        result = await cm.get_league_war("#WAR123")

    assert result is war
    assert war.clan._members == ["m1", "m2"]
    assert war.opponent._members == ["m1", "m2"]
    assert cm._league_war_cache["#WAR123"][0] is war


@pytest.mark.asyncio
async def test_get_player_list_live_merge_skips_blank_clan_tags() -> None:
    from clashcontrol import QBdiscocmdshelper as helper

    fake_cache = MagicMock()
    fake_cache.coc_clan_cache.get_clan = AsyncMock(side_effect=AssertionError("must not hit the API"))
    with patch.object(helper, "CACHE", fake_cache), \
            patch.object(helper, "_gather_players_from_cache", lambda *_a, **_k: None):
        result = await helper.get_player_list(["", "#", "  "], exclude_registered_players=False, use_live_api=True)

    assert result == []
    fake_cache.coc_clan_cache.get_clan.assert_not_called()
