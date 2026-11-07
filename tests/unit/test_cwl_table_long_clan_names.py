"""Tracker #0158: a long / full-width clan name must not stretch the CWL management table."""
from __future__ import annotations

from clashcontrol.QBdiscocmdshelper_cwl import (
    _CWL_TABLE_NAME_MAX_WIDTH,
    _fit_clan_name_cell,
    _pad_display,
)
from clashcontrol.formatting import text_display_width


def test_short_names_are_untouched() -> None:
    assert _fit_clan_name_cell("StayCalm") == "StayCalm"
    assert _fit_clan_name_cell("STY • Academy") == "STY • Academy"


def test_fullwidth_name_is_capped_by_display_width_not_length() -> None:
    name = "！！ＡＫＡＴＳＵＫＩ！！"  # 12 chars, ~2 columns each
    assert len(name) <= _CWL_TABLE_NAME_MAX_WIDTH  # len() alone would have let it through
    cell = _fit_clan_name_cell(name)
    assert cell.endswith("…")
    assert text_display_width(cell) <= _CWL_TABLE_NAME_MAX_WIDTH


def test_long_ascii_name_is_capped() -> None:
    cell = _fit_clan_name_cell("A very long clan name indeed")
    assert cell.endswith("…")
    assert text_display_width(cell) <= _CWL_TABLE_NAME_MAX_WIDTH


def test_pad_display_pads_to_display_columns() -> None:
    assert _pad_display("ab", 5) == "ab   "
    assert _pad_display("toolong", 3) == "toolong"
    wide = "ＡＢ"
    assert text_display_width(_pad_display(wide, 10)) == 10
