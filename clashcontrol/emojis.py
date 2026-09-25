"""Centralized emoji definitions for Discord custom emojis.

This module provides a single source of truth for all Discord custom emoji IDs
used throughout ClashControl. Centralizing emoji definitions makes updates easier and
ensures consistency across the codebase.

Usage:
    from clashcontrol.emojis import BotEmojis

    message = f"{BotEmojis.ENABLED} Notifications enabled"

Application emojis (2026-09-23, plans/app-emoji-migration.md): every icon below is now also an
emoji the bot OWNS — uploaded by the bot itself from clashcontrol/icons/emoji/<name>.webp, listed in
clashcontrol/icons/emoji_manifest.json. Attribute access on BotEmojis returns that application emoji
once it is resolved at startup (ensure_application_emojis(), INIT-STEP-6b); the literal values
written below are the FALLBACK — the original hand-uploaded guild emojis, which keep working, so
nothing renders differently before the first resolution or if Discord can't be reached.

To add an icon: put its master in clashcontrol/icons/, add one manifest entry and one BotEmojis
fallback line here, run `npm run build:emoji` in activity/client, commit. The bot uploads it on
its next start.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

# Resolved application emojis, BotEmojis attribute name -> "<:name:id>". Filled at startup.
_resolved: Dict[str, str] = {}


class _ResolvingEmojiMeta(type):
    """Makes every upper-case BotEmojis attribute resolve at LOOKUP time: the application emoji
    once it is known, else the class's own literal (the guild-emoji fallback).

    Resolving on access rather than rewriting the class keeps all ~44 call sites — plain
    `BotEmojis.X` and `getattr(BotEmojis, "THxx", default)` alike — unchanged. It relies on no
    caller copying a value at import time or into a default argument; both were audited when this
    was introduced (none existed).
    """

    def __getattribute__(cls, name: str) -> Any:
        value = super().__getattribute__(name)
        if name.isupper() and isinstance(value, str):
            return _resolved.get(name, value)
        return value


class BotEmojis(metaclass=_ResolvingEmojiMeta):
    """Custom emoji constants for ClashControl.

    All Discord custom emoji strings are defined here. When Discord emojis need
    to be updated, changes only need to be made in this single location.

    The literals are the guild-emoji fallbacks; see the module docstring.
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

    # CWL bench / "Ersatzbank" (tracker #0114/#0117). The first icon that was never a guild
    # emoji, so its fallback is the unicode chair rather than a guild-emoji id.
    CWL_BENCH = "🪑"


_CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w+:(\d+)>$")


def emoji_cdn_url(emoji: str) -> Optional[str]:
    """Discord's own CDN URL for a `<:name:id>`/`<a:name:id>` custom emoji string (one of the
    BotEmojis constants above) — for contexts that can't render Discord's native emoji markup
    at all, e.g. plain HTML in the CWL "Manage Enrollment" web Activity board
    (CWL_ROSTER_PLANNING_PLAN.md). Returns None if `emoji` isn't a recognizable custom-emoji
    string (a plain unicode emoji, for instance). Works identically for guild and application
    emojis — the CDN serves both by id."""
    match = _CUSTOM_EMOJI_RE.match(emoji)
    if not match:
        return None
    return f"https://cdn.discordapp.com/emojis/{match.group(1)}.png"


def th_icon_url(th_level: int) -> Optional[str]:
    """CDN image URL for a Town Hall level's icon, or None if no BotEmojis.THxx constant exists
    for it (e.g. a level newer than the last one ClashControl's emoji set covers)."""
    emoji = getattr(BotEmojis, f"TH{th_level:02d}", None)
    return emoji_cdn_url(emoji) if emoji else None


# ---------------------------------------------------------------------------
# Bench helpers (tracker #0117) — thin wrappers kept for their call sites
# ---------------------------------------------------------------------------

def bench_emoji() -> str:
    """The bench icon for Discord text: the application emoji once resolved, else 🪑."""
    return BotEmojis.CWL_BENCH


def button_emoji(value: str) -> Any:
    """Turn a BotEmojis value into what a Button's `emoji=` parameter takes. A custom emoji cannot
    be embedded in a button's label text — it has to be passed here, as a PartialEmoji (a unicode
    fallback can stay a plain string)."""
    import discord

    return discord.PartialEmoji.from_str(value) if _CUSTOM_EMOJI_RE.match(value) else value


def bench_button_emoji() -> Any:
    """The bench icon for a Button's `emoji=` parameter."""
    return button_emoji(BotEmojis.CWL_BENCH)


def signup_dm_icons() -> Dict[str, str]:
    """The three sign-up answer icons for DM text, as t() kwargs — {confirm}, {bench}, {optout}.

    One helper so every sign-up/reminder/roster-update DM shows the same three app icons as the
    buttons underneath (2026-09-23: the text used unicode ✅/❌ while the buttons didn't match)."""
    return {"confirm": BotEmojis.GCHECK, "bench": BotEmojis.CWL_BENCH, "optout": BotEmojis.REDX}


# ---------------------------------------------------------------------------
# Application-emoji resolution and upload
# ---------------------------------------------------------------------------

_ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
MANIFEST_PATH = os.path.join(_ICONS_DIR, "emoji_manifest.json")
UPLOAD_PACING_SECONDS = 0.5


def load_emoji_manifest() -> List[Dict[str, str]]:
    """[{"attr", "name", "source"}] — the single list both the asset generator
    (activity/client/scripts/build-emoji-assets.mjs) and the bot work from."""
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def emoji_asset_path(name: str) -> str:
    """Upload file for one manifest entry: clashcontrol/icons/emoji/<name>.webp (lossless, square)."""
    return os.path.join(_ICONS_DIR, "emoji", f"{name}.webp")


async def _upload_missing(bot: Any, missing: List[Dict[str, str]]) -> int:
    """Upload each missing manifest entry, resolving it as soon as it lands. Paced and
    per-entry fault tolerant: one failed upload is logged and skipped, never fatal."""
    import asyncio

    uploaded = 0
    for entry in missing:
        try:
            with open(emoji_asset_path(entry["name"]), "rb") as handle:
                image = handle.read()
            created = await bot.create_application_emoji(name=entry["name"], image=image)
            _resolved[entry["attr"]] = str(created)
            uploaded += 1
            logging.info(f"[EMOJI] Uploaded application emoji '{entry['name']}' ({created.id})")
        except Exception as e:
            logging.warning(
                f"[EMOJI] Could not upload '{entry['name']}' — keeping its fallback: {e}"
            )
        await asyncio.sleep(UPLOAD_PACING_SECONDS)
    if uploaded:
        logging.info(f"[EMOJI] Uploaded {uploaded}/{len(missing)} missing application emoji(s)")
    return uploaded


async def ensure_application_emojis(bot: Any, *, background: bool = True) -> int:
    """Resolve every manifest emoji the application already owns, and upload the missing ones.

    One fetch_application_emojis() call resolves everything already uploaded — the normal case on
    every start after the first, and fast. Missing entries are uploaded in a tracked BACKGROUND
    task by default, so a first start with ~34 uploads never holds up the boot sequence; until
    each one lands, its BotEmojis fallback (the old guild emoji, or 🪑 for the bench) keeps
    rendering. `background=False` awaits the uploads inline (tests).

    Never raises: the icons are cosmetic. Returns how many emojis were resolved from the
    existing set (not counting background uploads still in flight).
    """
    try:
        manifest = load_emoji_manifest()
        existing = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
    except Exception as e:
        logging.warning(f"[EMOJI] Could not resolve application emojis, using fallbacks: {e}")
        return 0

    missing: List[Dict[str, str]] = []
    for entry in manifest:
        emoji = existing.get(entry["name"])
        if emoji is not None:
            _resolved[entry["attr"]] = str(emoji)
        else:
            missing.append(entry)
    logging.info(
        f"[EMOJI] Resolved {len(manifest) - len(missing)}/{len(manifest)} application emojis"
        + (f"; uploading {len(missing)} in the background" if missing and background else "")
    )

    if missing:
        if background:
            import QBcore

            QBcore.spawn_tracked("upload-application-emojis", _upload_missing(bot, missing))
        else:
            await _upload_missing(bot, missing)
    return len(manifest) - len(missing)
