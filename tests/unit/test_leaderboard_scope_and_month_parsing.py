"""Tests for the /leaderboard "scope" feature and the flexible `month` argument parser.

Covers:
- parse_month_argument(): single month, range, list, trailing "-N" (incl. year rollover)
- _format_periods_label(): display formatting for contiguous/non-contiguous/cross-year periods
- _load_history_rows(): scope "own" / "members" / "all" dispatch, dedupe, graceful fallback
- calculate_leaderboard(): "members" credits a current member for wars fought under a clan
  that is no longer tracked/subscribed; "all" (default since 2026-09-23) adds past members
"""
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnusedImport=false
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _mock_cache(families=None, temp_stats=None, db_manager=None):
    mock = MagicMock()
    mock.clan_families = families or {}
    mock.get_temp_war_stats = MagicMock(return_value=temp_stats or {})
    mock.clan_name_cache = {}
    mock.history_cache = {}
    mock.db_manager = db_manager
    return mock


# ---------------------------------------------------------------------------
# parse_month_argument
# ---------------------------------------------------------------------------

class TestParseMonthArgument:
    NOW = datetime(2026, 1, 15)  # January — best month to exercise year rollover

    def _parse(self):
        from QBhelperfunctions import parse_month_argument
        return parse_month_argument

    def test_single_month(self):
        assert self._parse()("6", self.NOW) == [(6, 2026)]

    def test_single_month_with_explicit_year(self):
        assert self._parse()("6", self.NOW, explicit_year=2024) == [(6, 2024)]

    def test_range(self):
        assert self._parse()("6-7", self.NOW) == [(6, 2026), (7, 2026)]

    def test_reversed_range_still_ascending(self):
        assert self._parse()("7-6", self.NOW) == [(6, 2026), (7, 2026)]

    def test_list(self):
        assert self._parse()("1;3;5", self.NOW) == [(1, 2026), (3, 2026), (5, 2026)]

    def test_list_out_of_order_is_sorted(self):
        assert self._parse()("5;1;3", self.NOW) == [(1, 2026), (3, 2026), (5, 2026)]

    def test_trailing_count_crosses_year_boundary(self):
        # "now" is January 2026 — the trailing 2 months are Dec 2025 + Jan 2026.
        assert self._parse()("-2", self.NOW) == [(12, 2025), (1, 2026)]

    def test_trailing_count_of_one_is_current_month(self):
        assert self._parse()("-1", self.NOW) == [(1, 2026)]

    def test_trailing_count_rejects_explicit_year(self):
        with pytest.raises(ValueError):
            self._parse()("-2", self.NOW, explicit_year=2025)

    def test_out_of_range_month_rejected(self):
        with pytest.raises(ValueError):
            self._parse()("13", self.NOW)

    def test_zero_month_rejected(self):
        with pytest.raises(ValueError):
            self._parse()("0", self.NOW)

    def test_garbage_input_rejected(self):
        with pytest.raises(ValueError):
            self._parse()("not-a-month", self.NOW)

    def test_empty_input_rejected(self):
        with pytest.raises(ValueError):
            self._parse()("", self.NOW)

    def test_trailing_count_zero_rejected(self):
        with pytest.raises(ValueError):
            self._parse()("-0", self.NOW)


# ---------------------------------------------------------------------------
# _format_periods_label
# ---------------------------------------------------------------------------

class TestFormatPeriodsLabel:
    def _fmt(self):
        from QBhelperfunctions import _format_periods_label
        return _format_periods_label

    def test_single_period(self):
        assert self._fmt()([(6, 2026)]) == "06/2026"

    def test_contiguous_same_year(self):
        assert self._fmt()([(6, 2026), (7, 2026)]) == "06-07/2026"

    def test_contiguous_crossing_year(self):
        assert self._fmt()([(12, 2025), (1, 2026)]) == "2025-12 to 2026-01"

    def test_non_contiguous_same_year(self):
        assert self._fmt()([(1, 2026), (3, 2026), (5, 2026)]) == "01+03+05/2026"

    def test_non_contiguous_crossing_year(self):
        assert self._fmt()([(11, 2025), (2, 2026)]) == "2025-11+2026-02"


# ---------------------------------------------------------------------------
# _load_history_rows — scope dispatch
# ---------------------------------------------------------------------------

class TestLoadHistoryRowsScope:
    def test_scope_own_uses_per_clan_history(self, monkeypatch):
        mock = _mock_cache()
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        calls = []

        def fake_filtered(tag, month, year, cwl_season):
            calls.append(tag)
            return [{"WarID": f"W-{tag}", "PlayerID": "#P1", "Player": "A", "Stars": 1,
                     "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2}]

        monkeypatch.setattr("QBhelperfunctions._load_history_filtered", fake_filtered)
        from QBhelperfunctions import _load_history_rows
        rows = _load_history_rows("#CLAN1", 6, 2026, None, scope="own", member_player_tags={"#P1"})
        assert calls == ["#CLAN1"]
        assert len(rows) == 1

    def test_scope_members_uses_player_query_only(self, monkeypatch):
        """scope="members" (the pre-2026-09-23 "all"): current members' wars in any clan, and
        nothing else — the clan's own history (with its past members) is not read."""
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[
            {"WarID": "#OLD::W1", "PlayerID": "#P1", "Player": "A", "Stars": 2,
             "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2},
        ])
        mock = _mock_cache(db_manager=db)
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        monkeypatch.setattr("QBhelperfunctions._load_history_filtered",
                            MagicMock(side_effect=AssertionError("members must not read own history")))
        from QBhelperfunctions import _load_history_rows
        rows = _load_history_rows("#CLAN1", 6, 2026, None, scope="members", member_player_tags={"#P1"})
        db.get_player_attack_history_sync.assert_called_once_with(["#P1"], 6, 2026)
        assert len(rows) == 1
        assert rows[0]["WarID"] == "#OLD::W1"

    def test_scope_all_is_own_history_plus_current_members_elsewhere(self, monkeypatch):
        """scope="all" (default since 2026-09-23): past member #PAST (only in the clan's own
        history) is kept, current member #P1 also gets their war for #OLD, and #P1's war for
        #CLAN1 — returned by both queries — is counted once."""
        own = [
            {"WarID": "W1", "PlayerID": "#PAST", "Player": "Gone", "Stars": 3, "Attacks": 1},
            {"WarID": "W1", "PlayerID": "#P1", "Player": "A", "Stars": 2, "Attacks": 1},
        ]
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[
            {"WarID": "#CLAN1::W1", "PlayerID": "#P1", "Player": "A", "Stars": 2, "Attacks": 1},
            {"WarID": "#OLD::W9", "PlayerID": "#P1", "Player": "A", "Stars": 1, "Attacks": 1},
        ])
        monkeypatch.setattr("QBhelperfunctions.CACHE", _mock_cache(db_manager=db))
        monkeypatch.setattr("QBhelperfunctions._load_history_filtered", lambda tag, m, y, s: own)
        from QBhelperfunctions import _load_history_rows

        rows = _load_history_rows("#CLAN1", 6, 2026, None, scope="all", member_player_tags={"#P1"})

        assert sorted((r["PlayerID"], r["WarID"]) for r in rows) == [
            ("#P1", "#OLD::W9"), ("#P1", "W1"), ("#PAST", "W1"),
        ]

    def test_scope_all_dedupes_per_family_clan(self, monkeypatch):
        """In a family the own rows come per constituent clan — the dedupe key must use the
        clan the row came from, so the same war_id in two clans isn't mistaken for one war."""
        own = {
            "#C1": [{"WarID": "W1", "PlayerID": "#P1", "Player": "A", "Stars": 2, "Attacks": 1}],
            "#C2": [],
        }
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[
            {"WarID": "#C1::W1", "PlayerID": "#P1", "Player": "A", "Stars": 2, "Attacks": 1},  # duplicate
            {"WarID": "#C2::W1", "PlayerID": "#P1", "Player": "A", "Stars": 1, "Attacks": 1},  # own clan, other war
        ])
        monkeypatch.setattr("QBhelperfunctions.CACHE",
                            _mock_cache(families={"FAM": {"clans": ["#C1", "#C2"]}}, db_manager=db))
        monkeypatch.setattr("QBhelperfunctions._load_history_filtered", lambda tag, m, y, s: own[tag])
        from QBhelperfunctions import _load_history_rows

        rows = _load_history_rows("FAM", 6, 2026, None, scope="all", member_player_tags={"#P1"})

        assert sorted(r["WarID"] for r in rows) == ["#C2::W1", "W1"]

    def test_scope_all_without_member_tags_falls_back_to_own(self, monkeypatch):
        mock = _mock_cache()
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        calls = []

        def fake_filtered(tag, month, year, cwl_season):
            calls.append(tag)
            return []

        monkeypatch.setattr("QBhelperfunctions._load_history_filtered", fake_filtered)
        from QBhelperfunctions import _load_history_rows
        _load_history_rows("#CLAN1", 6, 2026, None, scope="all", member_player_tags=None)
        assert calls == ["#CLAN1"]  # fell back to the per-clan path, not the player query


# ---------------------------------------------------------------------------
# calculate_leaderboard(scope="all") — the actual feature end to end
# ---------------------------------------------------------------------------

class TestCalculateLeaderboardScopeAll:
    def test_credits_stats_from_a_no_longer_tracked_clan(self, monkeypatch):
        """
        Alice is currently in #NEW (a member clan) but earned stars in #OLD
        earlier in the month, before switching. #OLD is no longer subscribed
        anywhere, so scope="own" would miss those stars; scope="all" must not.
        """
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[
            {"WarID": "#OLD::W1", "PlayerID": "#P1", "Player": "Alice", "Stars": 2,
             "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2,
             "Date": "2026-06-05T10:00", "TH_lvl": 15},
            {"WarID": "#NEW::W2", "PlayerID": "#P1", "Player": "Alice", "Stars": 3,
             "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2,
             "Date": "2026-06-20T10:00", "TH_lvl": 15},
        ])
        mock = _mock_cache(db_manager=db)
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        from QBhelperfunctions import calculate_leaderboard

        result = calculate_leaderboard(
            "#NEW", month=6, year=2026, mode="attack",
            scope="members", member_player_tags={"#P1"},
        )

        assert "#P1" in result
        assert result["#P1"]["Stars"] == 5
        assert result["#P1"]["Wars_Count"] == 2

    def test_scope_all_lists_past_members_and_credits_current_members_elsewhere(self, monkeypatch):
        """The 2026-09-23 default: Bob left #NEW (not in the roster) but fought for it in June —
        he stays on the board; Alice (current member) gets her #OLD war too, her #NEW war once."""
        history_new = [
            {"WarID": "W2", "PlayerID": "#P1", "Player": "Alice", "Stars": 3, "Attacks": 1,
             "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2, "Date": "2026-06-20T10:00", "TH_lvl": 15},
            {"WarID": "W2", "PlayerID": "#BOB", "Player": "Bob", "Stars": 1, "Attacks": 1,
             "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2, "Date": "2026-06-20T10:00", "TH_lvl": 14},
        ]
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[
            {"WarID": "#OLD::W1", "PlayerID": "#P1", "Player": "Alice", "Stars": 2, "Attacks": 1,
             "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2, "Date": "2026-06-05T10:00", "TH_lvl": 15},
            {"WarID": "#NEW::W2", "PlayerID": "#P1", "Player": "Alice", "Stars": 3, "Attacks": 1,
             "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2, "Date": "2026-06-20T10:00", "TH_lvl": 15},
        ])
        mock = _mock_cache(db_manager=db)
        mock.get_clan_history = MagicMock(return_value=history_new)
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        from QBhelperfunctions import calculate_leaderboard

        result = calculate_leaderboard("#NEW", month=6, year=2026, mode="attack",
                                       scope="all", member_player_tags={"#P1"})

        assert result["#BOB"]["Stars"] == 1  # past member still listed
        assert result["#P1"]["Stars"] == 5  # 2 (#OLD) + 3 (#NEW, counted once)
        assert result["#P1"]["Wars_Count"] == 2

    def test_scope_own_only_sees_current_clan_stats(self, monkeypatch):
        """Same setup as above, but scope="own" (default) — only #NEW's own history counts."""
        history_new = [
            {"WarID": "W2", "PlayerID": "#P1", "Player": "Alice", "Stars": 3,
             "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 2,
             "Date": "2026-06-20T10:00", "TH_lvl": 15},
        ]
        mock = _mock_cache()
        mock.get_clan_history = MagicMock(return_value=history_new)
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        from QBhelperfunctions import calculate_leaderboard

        result = calculate_leaderboard("#NEW", month=6, year=2026, mode="attack")

        assert result["#P1"]["Stars"] == 3  # the #OLD stint's 2 stars are NOT counted


# ---------------------------------------------------------------------------
# get_recent_cwl_player_stats — the Manage Enrollment hover pop-up's missed-attacks/
# attack-defense-ratio stat (2026-08-16, project owner's spec, verbatim: "They should be
# calculated exactly as the /leaderboard command would do it with the modes missedattacks and
# attackdefratio and with the options cwl_only=true and month=-3" / "the option scope=ALL is
# also important"). Reuses _load_history_rows()/_merge_entries() directly, so these tests mock
# the exact same db.get_player_attack_history_sync() seam TestCalculateLeaderboardScopeAll above
# already relies on for scope="all".
# ---------------------------------------------------------------------------

class TestGetRecentCwlPlayerStats:
    NOW = datetime(2026, 7, 15)  # trailing 3 months -> May, June, July 2026

    def test_no_history_returns_null_fields(self, monkeypatch):
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(return_value=[])
        monkeypatch.setattr("QBhelperfunctions.CACHE", _mock_cache(db_manager=db))
        from QBhelperfunctions import get_recent_cwl_player_stats

        result = get_recent_cwl_player_stats("#P1", now=self.NOW)
        assert result == {"seasons": [], "attacks": None, "missed_attacks": None, "attack_defense_ratio": None}

    def test_sums_across_trailing_three_months_cwl_only_scope_all(self, monkeypatch):
        rows_by_period = {
            (5, 2026): [
                {"WarID": "#C::W5", "PlayerID": "#P1", "Player": "Alice", "Stars": 2,
                 "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 1, "Max_Attacks": 1,
                 "Date": "2026-05-01T08:00", "TH_lvl": 15},
            ],
            (6, 2026): [
                # A missed real attack (sentinel row) — counts toward Missed_Attacks.
                {"WarID": "#C::W6", "PlayerID": "#P1", "Player": "Alice", "Stars": 0,
                 "Attacks": 0, "Missed_Attacks": 1, "Defensive_Stars": 2, "Max_Attacks": 1,
                 "Date": "2026-06-01T08:00", "TH_lvl": 15},
                # A regular (non-CWL) war the same month — cwl_only must exclude it entirely.
                {"WarID": "#C::W6R", "PlayerID": "#P1", "Player": "Alice", "Stars": 10,
                 "Attacks": 2, "Missed_Attacks": 0, "Defensive_Stars": 5, "Max_Attacks": 2,
                 "Date": "2026-06-15T08:00", "TH_lvl": 15},
            ],
            (7, 2026): [
                {"WarID": "#C::W7", "PlayerID": "#P1", "Player": "Alice", "Stars": 3,
                 "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 1,
                 "Date": "2026-07-01T08:00", "TH_lvl": 15},
            ],
        }

        def fake_history(player_tags, month, year):
            assert player_tags == ["#P1"]
            return rows_by_period.get((month, year), [])

        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(side_effect=fake_history)
        mock = _mock_cache(db_manager=db)
        # The player-only scope ("members", called "all" when this was written) must never fall
        # back to the per-clan path (project owner: "the option scope=ALL is also important").
        mock.get_clan_history = MagicMock(side_effect=AssertionError("scope=members must not use get_clan_history"))
        monkeypatch.setattr("QBhelperfunctions.CACHE", mock)
        from QBhelperfunctions import get_recent_cwl_player_stats

        result = get_recent_cwl_player_stats("#P1", now=self.NOW)

        assert result["seasons"] == ["2026-05", "2026-06", "2026-07"]
        assert result["attacks"] == 2  # the two real attacks (May + July); the sentinel/regular-war rows don't count
        assert result["missed_attacks"] == 1
        # Attack stars = 2 + 0 + 3 = 5 (the regular war's 10 excluded);
        # defensive stars = 1 + 2 + 0 = 3 (the regular war's 5 excluded) -> 5/3.
        assert result["attack_defense_ratio"] == pytest.approx(1.67)

    def test_ratio_is_none_when_no_defensive_stars_recorded(self, monkeypatch):
        rows = [{"WarID": "#C::W7", "PlayerID": "#P1", "Player": "Alice", "Stars": 3,
                 "Attacks": 1, "Missed_Attacks": 0, "Defensive_Stars": 0, "Max_Attacks": 1,
                 "Date": "2026-07-01T08:00", "TH_lvl": 15}]
        db = MagicMock()
        db.get_player_attack_history_sync = MagicMock(
            side_effect=lambda tags, m, y: rows if (m, y) == (7, 2026) else []
        )
        monkeypatch.setattr("QBhelperfunctions.CACHE", _mock_cache(db_manager=db))
        from QBhelperfunctions import get_recent_cwl_player_stats

        result = get_recent_cwl_player_stats("#P1", now=self.NOW)
        assert result["missed_attacks"] == 0
        assert result["attack_defense_ratio"] is None
