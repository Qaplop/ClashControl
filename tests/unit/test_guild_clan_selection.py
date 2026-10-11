"""One clan order in every server-context clan selector (qaplop 2026-10-11).

New server: The QCrew added as member clan, "Hohenloher Land" only subscribed in a channel. The
/subscribe autocomplete offered only Hohenloher Land — member clans without a subscription were
missing. Now: member clans (incl. member families' clans) first, then the other subscribed clans.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from clashcontrol import QBdiscocmdshelper as helper  # noqa: E402
from clashcontrol.cache_manager import CACHE  # noqa: E402

GID = 777


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    monkeypatch.setattr(CACHE, "server_config", {str(GID): {"member_clans": ["#QC"], "member_families": ["#FAM"]}})
    monkeypatch.setattr(CACHE, "subscriptions", {str(GID): {"100": [{"clan_tag": "#HL", "subscription_type": "attack"},
                                                                    {"clan_tag": "#SUBFAM", "subscription_type": "attack"}]}})
    monkeypatch.setattr(CACHE, "clan_families", {"#FAM": {"name": "Member Fam", "clans": ["#F1"]},
                                                  "#SUBFAM": {"name": "Sub Fam", "clans": ["#S1"]}})
    monkeypatch.setattr(CACHE, "clan_name_cache", {"#QC": {"name": "The QCrew"}, "#HL": {"name": "Hohenloher Land"},
                                                   "#F1": {"name": "Alpha"}, "#S1": {"name": "Beta"},
                                                   "#OTHER": {"name": "Somewhere"}})
    monkeypatch.setattr(CACHE, "temp_war_metadata", {})


def test_member_clans_first_then_subscribed():
    clans, families = helper.guild_clan_selection([GID])
    assert clans[:2] == ["#F1", "#QC"]                     # member clans + member family's clan, by name
    assert set(clans[2:]) == {"#HL", "#S1"}                 # then subscribed (incl. a subscribed family's clan)
    assert families == ["#FAM", "#SUBFAM"]                  # member family first
    assert helper.get_guild_clans_including_member_config(GID) == clans


def test_subscribe_autocomplete_offers_the_member_clan_without_subscription():
    choices = asyncio.run(helper.get_clan_family_autocomplete_choices("", guild_id=str(GID), mode="guild_first"))
    values = [c.value for c in choices]
    assert values.index("#QC") < values.index("#HL")        # The QCrew offered, and before Hohenloher Land
    assert "#OTHER" not in values                           # empty input: only this server's clans
    typed = asyncio.run(helper.get_clan_family_autocomplete_choices("some", guild_id=str(GID), mode="guild_first"))
    assert [c.value for c in typed] == ["#OTHER"]           # typing searches all tracked clans
