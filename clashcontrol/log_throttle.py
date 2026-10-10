"""Log a recurring, actionable-only-by-someone-else condition once per interval (tracker #0150).

Some failures repeat every update cycle and nothing the bot does can fix them: a guild removed
the bot's permission on a channel, deleted a channel, etc. Logging each occurrence buries
everything else (2026-10-09: 310 identical leaderboard 403 ERRORs in 28 h, each with a full
traceback). These helpers log the first occurrence per key at the requested level and the
repeats at DEBUG until the interval has passed.

State is a module-level dict of last-logged times. It is log-throttle state, not runtime data:
losing it (a restart) only means the next occurrence is logged again, which is the desired
behaviour after a restart anyway. That is why it does not live in CACHE (Cardinal Rule 3 is
about data that must not diverge).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Hashable, Optional

import discord

#: Default interval: once a day per key.
DAY_S = 24 * 3600

_last_logged: Dict[Hashable, float] = {}


def log_throttled(
    level: int,
    key: Hashable,
    message: str,
    *,
    interval_s: float = DAY_S,
    repeat_suffix: Optional[str] = None,
) -> bool:
    """Log ``message`` at ``level`` at most once per ``interval_s`` for ``key``.

    Repeats inside the interval go to DEBUG unchanged. Returns True when the message was logged
    at ``level``. ``repeat_suffix`` defaults to a note saying how long repeats are suppressed.
    """
    now = time.monotonic()
    last = _last_logged.get(key)
    if last is not None and now - last < interval_s:
        logging.debug(message)
        return False
    _last_logged[key] = now
    if repeat_suffix is None:
        hours = interval_s / 3600
        repeat_suffix = f" (repeats logged at DEBUG for {hours:g} h)"
    logging.log(level, message + repeat_suffix)
    return True


def reset_log_throttle() -> None:
    """Forget every key (tests)."""
    _last_logged.clear()


def find_discord_forbidden(exc: BaseException) -> Optional[discord.Forbidden]:
    """Return the ``discord.Forbidden`` that caused ``exc`` (itself, or anywhere in its
    ``__cause__``/``__context__`` chain), else None. Our wrappers (e.g. LeaderboardPostingError)
    re-raise inside ``except``, which keeps the original reachable via ``__context__``."""
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, discord.Forbidden):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None
