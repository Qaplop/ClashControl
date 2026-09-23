"""Tracker #0117 — one bench icon everywhere.

The Activity renders cwl_bench.svg, and Discord can't render an SVG at all, which is why the text
used 🪑: a brown chair sitting next to the board's blue bench. The bot now ships the icon as an
application emoji (qapbot/icons/emoji/cwl_bench.webp, BotEmojis.CWL_BENCH) and uses it in DMs,
buttons and settings text, falling back to 🪑 only if that never resolves. The upload/resolution
machinery itself is covered by test_application_emojis.py.
"""
from __future__ import annotations

import glob
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

CHAIR = "\U0001fa91"


@pytest.fixture(autouse=True)
def _reset_resolved_emoji():
    """Every test starts from "not resolved yet" and leaves no resolved id behind."""
    import qapbot.emojis as emojis

    saved = dict(emojis._resolved)
    emojis._resolved.clear()
    yield
    emojis._resolved.clear()
    emojis._resolved.update(saved)


# ---------------------------------------------------------------------------
# No literal chair emoji is left in any user-facing string
# ---------------------------------------------------------------------------

def test_no_translation_string_contains_the_chair_emoji():
    for path in glob.glob("qapbot/translations/*.json"):
        data = json.load(open(path, encoding="utf-8"))

        def walk(node, trail=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{trail}.{key}" if trail else key)
            elif isinstance(node, str):
                assert CHAIR not in node, f"{path}: {trail} still uses the chair emoji"

        walk(data)


def test_bench_strings_carry_the_placeholder_instead():
    en = json.load(open("qapbot/translations/en.json", encoding="utf-8"))
    assert "{bench}" in en["cwl"]["template"]["dm_body_bench"]
    assert "{bench}" in en["cwl"]["template"]["bench_msg"]
    assert "{bench}" in en["cwl"]["start"]["dm_line_bench_suffix"]
    # Button labels must NOT carry it — a custom emoji only renders through Button(emoji=...).
    assert "{bench}" not in en["cwl"]["template"]["bench_button"]
    assert "{bench}" not in en["cwl"]["reminder"]["bench_button_labeled"]


# ---------------------------------------------------------------------------
# Resolution and fallback
# ---------------------------------------------------------------------------

def test_falls_back_to_the_chair_until_resolved():
    from qapbot.emojis import bench_button_emoji, bench_emoji

    assert bench_emoji() == CHAIR
    assert bench_button_emoji() == CHAIR


def test_resolved_emoji_is_used_for_text_and_buttons():
    import discord

    import qapbot.emojis as emojis

    emojis._resolved["CWL_BENCH"] = "<:cwl_bench:1455513859715629076>"
    assert emojis.bench_emoji() == "<:cwl_bench:1455513859715629076>"
    partial = emojis.bench_button_emoji()
    assert isinstance(partial, discord.PartialEmoji) and partial.id == 1455513859715629076


# ---------------------------------------------------------------------------
# The Discord surfaces actually use it
# ---------------------------------------------------------------------------

def test_only_the_bench_button_carries_an_emoji():
    import qapbot.emojis as emojis
    from qapbot.ui_cwl_roster import build_cwl_reminder_response_view, build_cwl_signup_response_view

    emojis._resolved["CWL_BENCH"] = "<:cwl_bench:1455513859715629077>"

    # These are DynamicItem wrappers; the real Button is on .item.
    signup = build_cwl_signup_response_view(1, "#P1", 5, bench=True)
    by_action = {c.custom_id.split(":")[2]: c.item for c in signup.children}
    assert by_action["passive"].emoji is not None
    assert by_action["passive"].emoji.id == 1455513859715629077
    assert by_action["confirm"].emoji is None and by_action["optout"].emoji is None
    # …and never inside the label text, which Discord would show as raw markup.
    assert "<:" not in (by_action["passive"].label or "")

    reminder = build_cwl_reminder_response_view(
        1, [{"player_tag": "#P1", "player_name": "Alpha"}], 5, bench=True
    )
    bench_button = next(c.item for c in reminder.children if ":passive:" in (c.custom_id or ""))
    assert bench_button.emoji is not None and bench_button.emoji.id == 1455513859715629077
    assert bench_button.label == "Alpha"


def test_dm_body_and_finalize_text_render_the_resolved_emoji():
    import qapbot.emojis as emojis
    from qapbot.i18n import t

    emojis._resolved["CWL_BENCH"] = "<:cwl_bench:1455513859715629077>"

    body = t('cwl.template.dm_body_bench', guild_id=None, season="2026-10",
             player_name="Alpha", bench=emojis.bench_emoji())
    finalize = t('cwl.template.bench_msg', guild_id=None, player_name="Alpha",
                 bench=emojis.bench_emoji())

    for text in (body, finalize):
        assert "<:cwl_bench:1455513859715629077>" in text
        assert CHAIR not in text and "{bench}" not in text
