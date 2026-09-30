"""CWL promotion/demotion counts per league, versioned by season (tracker #0146).

Supercell's 2026-09-29 announcement: from the October season the higher leagues go back to the
standard numbers after the revamp. The same announcement states the revamp-period numbers for
Titan/Legend, which the 2026-05 table previously only had placeholders for.
"""
from __future__ import annotations

import pytest

from QBhelperfunctions import _get_cwl_promo_rules


@pytest.mark.parametrize("league, expected", [
    ("Master League I", (1, 2)),
    ("Champion League III", (1, 2)),
    ("Champion League II", (1, 2)),
    ("Champion League I", (1, 2)),
    ("Titan League III", (1, 2)),
    ("Titan League II", (1, 2)),
    ("Titan League I", (1, 2)),
    ("Legend League", (0, 2)),
    ("Master League II", (2, 2)),   # unchanged
    ("Crystal League I", (2, 2)),   # unchanged
    ("Bronze League I", (3, 1)),    # unchanged
])
def test_rules_from_2026_10(league, expected):
    assert _get_cwl_promo_rules("2026-10", league) == expected
    assert _get_cwl_promo_rules("2027-03", league) == expected


@pytest.mark.parametrize("league, expected", [
    ("Master League I", (2, 2)),
    ("Champion League II", (2, 2)),
    ("Champion League I", (4, 1)),
    ("Titan League I", (4, 1)),
    ("Legend League", (0, 1)),
])
def test_revamp_rules_2026_05_to_2026_09(league, expected):
    assert _get_cwl_promo_rules("2026-05", league) == expected
    assert _get_cwl_promo_rules("2026-09", league) == expected
