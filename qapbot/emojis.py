"""Centralized emoji definitions for Discord custom emojis.

This module provides a single source of truth for all Discord custom emoji IDs
used throughout QapBot. Centralizing emoji definitions makes updates easier and
ensures consistency across the codebase.

Usage:
    from qapbot.emojis import BotEmojis

    message = f"{BotEmojis.ENABLED} Notifications enabled"
"""
import re
from typing import Any, Optional


class BotEmojis:
    """Custom emoji constants for QapBot.
    
    All Discord custom emoji strings are defined here. When Discord emojis need
    to be updated, changes only need to be made in this single location.
    """
    
    # Status indicators
    ENABLED = "<:enabled:1455513859715629076>"
    DISABLED = "<:disabled:1455509604145434705>"
    VERIFIED = "<:verified:1454869848206213226>"
    GCHECK = "<:gcheck:1455226636415930631>"
    REDX = "<:redx:1454442783128551424>"
    
    # Notification frequency
    ONCE = "<:once:1455219555600171130>"
    REPEATED = "<:repeated:1455221464314679480>"
    
    # War types
    CWL = "<:cwl:1455219160102473779>"
    ALLWARS = "<:allwars:1455283063432024125>"
    
    # Town Hall levels
    TH01 = "<:TH01:1470128897160118272>"
    TH02 = "<:TH02:1470128859646136554>"
    TH03 = "<:TH03:1470128825655496755>"
    TH04 = "<:TH04:1470128772216000533>"
    TH05 = "<:TH05:1470128740309667850>"
    TH06 = "<:TH06:1470128706096730165>"
    TH07 = "<:TH07:1470128660668481659>"
    TH08 = "<:TH08:1470128630742122527>"
    TH09 = "<:TH09:1470128592884338779>"
    TH10 = "<:TH10:1470128541306716301>"
    TH11 = "<:TH11:1470128500974289264>"
    TH12 = "<:TH12:1470128409723277333>"
    TH13 = "<:TH13:1470128319822561441>"
    TH14 = "<:TH14:1470128279691460893>"
    TH15 = "<:TH15:1470128241271640075>"
    TH16 = "<:TH16:1470128189111009382>"
    TH17 = "<:TH17:1470128119833821487>"
    TH18 = "<:TH18:1470128059771257115>"
    
    # Heroes
    HERO_KING = "<:hero_king:1470127404973428880>"
    HERO_QUEEN = "<:hero_queen:1470127547873235065>"
    HERO_WARDEN = "<:hero_warden:1470127843261546496>"
    HERO_RC = "<:hero_RC:1470127680698712084>"
    HERO_MP = "<:hero_MP:1470127934084743211>"
    HERO_DD = "<:hero_DD:1499710549322240200>"


_CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w+:(\d+)>$")


def emoji_cdn_url(emoji: str) -> Optional[str]:
    """Discord's own CDN URL for a `<:name:id>`/`<a:name:id>` custom emoji string (one of the
    BotEmojis constants above) — for contexts that can't render Discord's native emoji markup
    at all, e.g. plain HTML in the CWL "Manage Enrollment" web Activity board
    (CWL_ROSTER_PLANNING_PLAN.md). Returns None if `emoji` isn't a recognizable custom-emoji
    string (a plain unicode emoji, for instance)."""
    match = _CUSTOM_EMOJI_RE.match(emoji)
    if not match:
        return None
    return f"https://cdn.discordapp.com/emojis/{match.group(1)}.png"


def th_icon_url(th_level: int) -> Optional[str]:
    """CDN image URL for a Town Hall level's icon, or None if no BotEmojis.THxx constant exists
    for it (e.g. a level newer than the last one QapBot's emoji set covers)."""
    emoji = getattr(BotEmojis, f"TH{th_level:02d}", None)
    return emoji_cdn_url(emoji) if emoji else None


# ---------------------------------------------------------------------------
# Application-owned emojis resolved at runtime (tracker #0117)
# ---------------------------------------------------------------------------
#
# Every BotEmojis constant above is a hand-uploaded GUILD emoji whose id someone pasted in, with
# its source art in qapbot/icons/. The CWL bench icon is the first one the bot provisions itself:
# qapbot/icons/cwl_bench.png (rendered from cwl_bench.svg, the same artwork the Activity board
# bundles) is uploaded on first start and its id remembered for the session, so the board, the DMs
# and the buttons all show one identical icon instead of a blue bench next to Discord's brown 🪑.
#
# Application emojis (discord.py 2.5+) belong to the app, not to a guild, so one upload works in
# every server the bot is in — no per-guild emoji slots consumed.

BENCH_EMOJI_NAME = "cwl_bench"
BENCH_EMOJI_FALLBACK = "🪑"
BENCH_EMOJI_ASSET = "icons/cwl_bench.png"

_resolved_bench_emoji: Optional[str] = None


def bench_emoji() -> str:
    """The bench icon for Discord text: `<:cwl_bench:id>` once resolved, else the 🪑 fallback.

    Safe to call before (or without) ensure_application_emojis() — every caller renders user-facing
    text, so an unresolved emoji must degrade to something readable rather than raise or print raw
    markup.
    """
    return _resolved_bench_emoji or BENCH_EMOJI_FALLBACK


def bench_button_emoji() -> Any:
    """The same icon for a Button's `emoji=` parameter, which takes a PartialEmoji (or a plain
    unicode string) — a custom emoji cannot be embedded in a button's label text, only passed
    here."""
    import discord

    resolved = _resolved_bench_emoji
    return discord.PartialEmoji.from_str(resolved) if resolved else BENCH_EMOJI_FALLBACK


async def ensure_application_emojis(bot: Any) -> None:
    """Make sure this application owns the bench emoji, uploading it once if it doesn't, and cache
    its markup for bench_emoji()/bench_button_emoji().

    Called once at startup. Never raises: the icon is cosmetic, so any failure (missing asset,
    permissions, a Discord hiccup) is logged and leaves the 🪑 fallback in place rather than
    holding up the rest of the boot sequence.
    """
    global _resolved_bench_emoji

    import logging
    import os

    try:
        existing = await bot.fetch_application_emojis()
        for emoji in existing:
            if emoji.name == BENCH_EMOJI_NAME:
                _resolved_bench_emoji = str(emoji)
                logging.info(f"[EMOJI] Application emoji '{BENCH_EMOJI_NAME}' already present: {emoji.id}")
                return

        path = os.path.join(os.path.dirname(__file__), *BENCH_EMOJI_ASSET.split("/"))
        with open(path, "rb") as handle:
            image = handle.read()
        created = await bot.create_application_emoji(name=BENCH_EMOJI_NAME, image=image)
        _resolved_bench_emoji = str(created)
        logging.info(f"[EMOJI] Uploaded application emoji '{BENCH_EMOJI_NAME}' ({created.id})")
    except Exception as e:
        logging.warning(
            f"[EMOJI] Could not resolve the '{BENCH_EMOJI_NAME}' application emoji, "
            f"falling back to {BENCH_EMOJI_FALLBACK}: {e}"
        )
