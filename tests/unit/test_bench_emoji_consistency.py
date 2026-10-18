"""Tracker #0117 — one bench icon everywhere.

The Activity renders cwl_bench.svg, and Discord can't render an SVG at all, which is why the text
used 🪑: a brown chair sitting next to the board's blue bench. The bot now ships the icon as an
application emoji (clashcontrol/icons/emoji/cwl_bench.webp, BotEmojis.CWL_BENCH) and uses it in DMs,
buttons and settings text, falling back to 🪑 only if that never resolves. The upload/resolution
machinery itself is covered by test_application_emojis.py.
"""
from __future__ import annotations

import glob
import json
from typing import Any, cast

import pytest

CHAIR = "\U0001fa91"


@pytest.fixture(autouse=True)
def _reset_resolved_emoji():
    """Every test starts from "not resolved yet" and leaves no resolved id behind."""
    import clashcontrol.emojis as emojis

    saved = dict(emojis._resolved)
    emojis._resolved.clear()
    yield
    emojis._resolved.clear()
    emojis._resolved.update(saved)


# ---------------------------------------------------------------------------
# No literal chair emoji is left in any user-facing string
# ---------------------------------------------------------------------------

def test_no_translation_string_contains_the_chair_emoji():
    for path in glob.glob("clashcontrol/translations/*.json"):
        data = json.load(open(path, encoding="utf-8"))

        def walk(node, trail=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{trail}.{key}" if trail else key)
            elif isinstance(node, str):
                assert CHAIR not in node, f"{path}: {trail} still uses the chair emoji"

        walk(data)


def test_bench_strings_carry_the_placeholder_instead():
    en = json.load(open("clashcontrol/translations/en.json", encoding="utf-8"))
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
    from clashcontrol.emojis import bench_button_emoji, bench_emoji

    assert bench_emoji() == CHAIR
    assert bench_button_emoji() == CHAIR


def test_resolved_emoji_is_used_for_text_and_buttons():
    import discord

    import clashcontrol.emojis as emojis

    emojis._resolved["CWL_BENCH"] = "<:cwl_bench:1455513859715629076>"
    assert emojis.bench_emoji() == "<:cwl_bench:1455513859715629076>"
    partial = emojis.bench_button_emoji()
    assert isinstance(partial, discord.PartialEmoji) and partial.id == 1455513859715629076


# ---------------------------------------------------------------------------
# The Discord surfaces actually use it
# ---------------------------------------------------------------------------

def _answer_buttons(view: Any) -> list[tuple[str, Any]]:
    """(action, Button) per answer button — they are DynamicItem wrappers; the Button is .item."""
    import discord

    result: list[tuple[str, Any]] = []
    for child in view.children:
        assert isinstance(child, discord.ui.DynamicItem)
        wrapper = cast(discord.ui.DynamicItem[discord.ui.Button[Any]], child)
        result.append((wrapper.custom_id.split(":")[2], wrapper.item))
    return result


def test_every_answer_button_carries_its_app_icon():
    """2026-09-23: confirm and opt-out got their app icons too (gcheck/redx), so all three buttons
    match the icons in the DM text above them."""
    import clashcontrol.emojis as emojis
    from clashcontrol.ui_cwl_roster import build_cwl_reminder_response_view, build_cwl_signup_response_view

    emojis._resolved.update({
        "GCHECK": "<:gcheck:1552060584176914601>",
        "CWL_BENCH": "<:cwl_bench:1455513859715629077>",
        "REDX": "<:redx:1552060584176914602>",
    })
    expected = {"confirm": 1552060584176914601, "passive": 1455513859715629077, "optout": 1552060584176914602}

    signup = build_cwl_signup_response_view(1, "#P1", 5, bench=True)
    for action, button in _answer_buttons(signup):
        assert button.emoji is not None and button.emoji.id == expected[action], action
        assert "<:" not in (button.label or "")  # never inside the label text

    reminder = build_cwl_reminder_response_view(
        1, [{"player_tag": "#P1", "player_name": "Alpha"}], 5, bench=True
    )
    for action, button in _answer_buttons(reminder):
        assert button.emoji is not None and button.emoji.id == expected[action], action
        assert button.label == "Alpha"  # the name only; the icon rides in emoji=


def test_dm_texts_use_the_app_icons_not_unicode():
    import clashcontrol.emojis as emojis
    from clashcontrol.i18n import t

    emojis._resolved.update({"GCHECK": "<:gcheck:1552060584176914601>", "REDX": "<:redx:1552060584176914602>"})
    body = t('cwl.template.dm_body_bench', guild_id=None, season="2026-10", player_name="Alpha",
             **emojis.signup_dm_icons())
    confirmed = t('cwl.template.confirmed_msg', guild_id=None, player_name="Alpha", **emojis.signup_dm_icons())

    assert "<:gcheck:1552060584176914601>" in body and "<:redx:1552060584176914602>" in body
    assert confirmed.startswith("<:gcheck:1552060584176914601>")
    for text in (body, confirmed):
        assert "✅" not in text and "❌" not in text


def test_opt_out_reply_uses_the_opt_out_app_icon_in_every_language():
    """2026-09-23 live report: the "has opted out" DM reply still showed a 👍 while Confirm and
    Bench replies used their app icons — it must carry the same icon as the Opt Out button."""
    import clashcontrol.emojis as emojis
    from clashcontrol.i18n import _translation_manager

    emojis._resolved.update({"REDX": "<:redx:1552060584176914602>"})
    for lang in ("en", "de", "es", "zh", "la"):
        declined = _translation_manager.get_translation(
            'cwl.template.declined_msg', lang, player_name="Alpha", **emojis.signup_dm_icons()
        )
        assert declined.startswith("<:redx:1552060584176914602>"), lang
        assert "👍" not in declined and "{optout}" not in declined, lang


def test_dm_body_and_finalize_text_render_the_resolved_emoji():
    import clashcontrol.emojis as emojis
    from clashcontrol.i18n import t

    emojis._resolved["CWL_BENCH"] = "<:cwl_bench:1455513859715629077>"

    body = t('cwl.template.dm_body_bench', guild_id=None, season="2026-10",
             player_name="Alpha", **emojis.signup_dm_icons())
    finalize = t('cwl.template.bench_msg', guild_id=None, player_name="Alpha",
                 **emojis.signup_dm_icons())

    for text in (body, finalize):
        assert "<:cwl_bench:1455513859715629077>" in text
        assert CHAIR not in text and "{bench}" not in text
