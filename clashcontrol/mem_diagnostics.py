"""Memory diagnostics that name the HOLDER of retained memory, not just the allocation site.

Tracker #0150. Since 2026-09-29 PROD has shown a ~4 GB RSS ramp lasting a few hours (usually in
the morning) that ends in an RSS-triggered restart. Every memory profile written so far could
only say *where* the retained objects were allocated (tracemalloc, ``nframe=1``: the JSON decode
line in aiohttp) and how many of each *type* exist. Neither answers the question that decides the
fix: **what is keeping them alive?**

Two tools:

``format_mem_gauges()``
    One cheap line per cycle (``[MEM-GAUGES]``) with the counters that separate the candidate
    explanations: pymalloc's live block count (Python objects growing => retention; flat while
    RSS climbs => allocator), glibc in-use vs. freed-not-returned, coc.py's HTTP response cache,
    our own caches, live asyncio tasks and scheduled loop timers.

``build_retention_report()``
    For the memory profile. Counts gc-tracked dicts by *shape* (their first keys, in insertion
    order — API JSON from one endpoint always has the same key order), diffs that census against
    the one taken when the trace was armed, and for the fastest-growing shapes walks
    ``gc.get_referrers()`` upward from a few sample objects until it reaches something nameable
    (a ``CACHE`` attribute, a module global, an object attribute, coc.py's cache). One
    ``get_referrers`` heap walk per level serves every chain at once, and the walk stops at a
    time budget, so the cost is bounded.

Both are diagnostic-only and must never raise into the caller.
"""
from __future__ import annotations

import asyncio
import gc
import sys
import time
import types
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Keys of a dict used as its "shape" — enough to tell API payloads apart, short enough to be cheap.
_SHAPE_KEYS = 8
#: Dicts with fewer keys are too generic to be informative (kwargs, tiny lookup tables, ...).
_MIN_SHAPE_KEYS = 3

ShapeKey = Tuple[str, ...]


# ---------------------------------------------------------------------------------------------
# Per-cycle gauges
# ---------------------------------------------------------------------------------------------

def format_mem_gauges(cache: Any, coc_client: Any = None) -> str:
    """Build the per-cycle ``[MEM-GAUGES]`` line (without the tag). Never raises.

    Every field is O(1) or a single cheap call — this runs every cycle on the Celeron.
    ``sys.getallocatedblocks()`` is the key one: it counts live interpreter memory blocks, so it
    rises with retained Python objects and stays flat when RSS grows for allocator reasons.
    """
    parts: List[str] = []

    def _add(label: str, fn: Any) -> None:
        try:
            value = fn()
            if value is not None:
                parts.append(f"{label}={value}")
        except Exception:
            pass

    _add("py_blocks", lambda: f"{sys.getallocatedblocks() / 1e6:.2f}M")

    def _malloc() -> Optional[str]:
        from clashcontrol.QBdiscocmdshelper_admin_command import _get_malloc_info
        info = _get_malloc_info()
        if not info:
            return None
        return f"{info['in_use_mb']:.0f}/{info['free_mb']:.0f}MB"
    _add("malloc_inuse/free", _malloc)

    def _coc_http() -> Optional[str]:
        from clashcontrol.coc_health import get_coc_http_cache_stats
        stats = get_coc_http_cache_stats(coc_client)
        if not stats:
            return None
        text = f"{stats['entries']}/{stats['max']}"
        if stats.get("deque"):
            text += f"(deque={stats['deque']})"  # non-zero => the bounded-cache shim is NOT in effect
        return text
    _add("coc_http", _coc_http)

    _add("coc_clan", lambda: len(cache.coc_clan_cache.cache))
    _add("temp_war_objects", lambda: len(cache.temp_war_objects))
    _add("temp_war_stats", lambda: len(cache.temp_war_stats))
    _add("temp_war_meta", lambda: len(cache.temp_war_metadata))
    _add("notif_state", lambda: len(cache.notification_state))
    _add("history_cache", lambda: len(cache.history_cache))

    def _tasks() -> int:
        return len(asyncio.all_tasks(asyncio.get_running_loop()))
    _add("tasks", _tasks)

    def _timers() -> int:
        # Private, but the only way to see call_later() handles piling up (coc.py schedules one
        # per cached response). Absent on non-default loops -> field simply omitted.
        return len(asyncio.get_running_loop()._scheduled)  # type: ignore[attr-defined]
    _add("timers", _timers)

    return " ".join(parts)


# ---------------------------------------------------------------------------------------------
# Dict-shape census
# ---------------------------------------------------------------------------------------------

def _shape_of(d: Dict[Any, Any]) -> Optional[ShapeKey]:
    if len(d) < _MIN_SHAPE_KEYS:
        return None
    keys: List[str] = []
    for k in d:
        if not isinstance(k, str):
            return None
        keys.append(k)
        if len(keys) >= _SHAPE_KEYS:
            break
    return tuple(keys)


def dict_shape_census(objects: Optional[Iterable[Any]] = None) -> "Counter[ShapeKey]":
    """Count gc-tracked dicts by shape. Pass ``objects`` to reuse an existing ``gc.get_objects()``.

    Only dicts the collector tracks are visible — CPython untracks dicts holding nothing but
    atomic values — but every API payload's *outer* dict holds a list or a nested dict, so the
    payloads that matter are always counted.
    """
    census: "Counter[ShapeKey]" = Counter()
    for obj in (gc.get_objects() if objects is None else objects):
        if type(obj) is dict:
            shape = _shape_of(obj)
            if shape is not None:
                census[shape] += 1
    return census


def _is_data_shape(shape: ShapeKey) -> bool:
    """True for shapes that look like data (API payloads, our caches), False for interpreter
    internals — module/class namespaces, enum members, ``__slots__`` tables — whose keys start
    with an underscore. Those dominate any census by count but are never what is growing."""
    return sum(1 for k in shape if k.startswith("_")) * 2 < len(shape)


def _fmt_shape(shape: ShapeKey) -> str:
    return "{" + ", ".join(shape) + ("" if len(shape) < _SHAPE_KEYS else ", ...") + "}"


# ---------------------------------------------------------------------------------------------
# Referrer chains
# ---------------------------------------------------------------------------------------------

def _named_roots(cache: Any, coc_client: Any) -> Dict[int, str]:
    """id() -> name for containers we can name outright (chain terminals)."""
    roots: Dict[int, str] = {}
    try:
        # vars(), not dir()+getattr(): a property on CACHE could compute or mutate something.
        cache_vars = vars(cache)
        roots[id(cache_vars)] = "CACHE.__dict__"
        for attr, value in cache_vars.items():
            if isinstance(value, (dict, list, set)) or (
                hasattr(value, "__dict__") and not callable(value)
            ):
                roots[id(value)] = f"CACHE.{attr}"
                inner = getattr(value, "__dict__", None)
                if isinstance(inner, dict):
                    roots.setdefault(id(inner), f"CACHE.{attr}.__dict__")
    except Exception:
        pass
    try:
        http_cache = getattr(getattr(coc_client, "http", None), "cache", None)
        data = getattr(http_cache, "data", None)
        if data is not None:
            roots[id(data)] = "coc.py HTTP response cache (client.http.cache.data)"
            roots[id(http_cache)] = "coc.py HTTP response cache (client.http.cache)"
    except Exception:
        pass
    for mod_name, mod in list(sys.modules.items()):
        mod_dict = getattr(mod, "__dict__", None)
        if isinstance(mod_dict, dict):
            roots.setdefault(id(mod_dict), f"module {mod_name} globals")
    return roots


def _slot_or_key(holder: Any, target: Any) -> str:
    """Where inside ``holder`` the reference to ``target`` sits (dict key / index / attribute)."""
    try:
        if isinstance(holder, dict):
            for k, v in holder.items():
                if v is target:
                    return f"[{k!r}]"[:60]
                if k is target:
                    return "<as key>"
        elif isinstance(holder, (list, tuple)):
            for i, v in enumerate(holder):
                if v is target:
                    return f"[{i}] of {len(holder)}"
        else:
            for cls in type(holder).__mro__:
                for slot in getattr(cls, "__slots__", ()) or ():
                    if isinstance(slot, str) and getattr(holder, slot, None) is target:
                        return f".{slot}"
            if getattr(holder, "__dict__", None) is target:
                return ".__dict__"
    except Exception:
        pass
    return ""


def _describe(obj: Any) -> str:
    try:
        if isinstance(obj, dict):
            shape = _shape_of(obj)
            return f"dict(len={len(obj)}{', ' + _fmt_shape(shape) if shape else ''})"
        if isinstance(obj, (list, tuple, set, frozenset)):
            return f"{type(obj).__name__}(len={len(obj)})"
        if isinstance(obj, types.FrameType):
            return f"frame {obj.f_code.co_qualname}"
        if isinstance(obj, (types.GeneratorType, types.CoroutineType)):
            return f"{type(obj).__name__} {obj.__qualname__}"
        if isinstance(obj, types.CellType):
            return "closure cell"
        if isinstance(obj, (types.FunctionType, types.MethodType)):
            return f"function {getattr(obj, '__qualname__', '?')}"
        if isinstance(obj, asyncio.Task):
            return f"Task {obj.get_name()} ({getattr(obj.get_coro(), '__qualname__', '?')})"
        cls = type(obj)
        return f"{cls.__module__}.{cls.__qualname__}"
    except Exception:
        return str(type(obj).__name__)


def build_retention_report(
    cache: Any,
    coc_client: Any = None,
    shape_baseline: Optional["Counter[ShapeKey]"] = None,
    *,
    budget_s: float = 60.0,
    top_shapes: int = 15,
    chase_shapes: int = 5,
    samples_per_shape: int = 2,
    max_depth: int = 10,
) -> List[str]:
    """Report lines: dict-shape census (+ growth since ``shape_baseline``) and referrer chains.

    Holds the GIL for its whole run (every step is a C-level heap walk), so ``budget_s`` bounds
    the stall; chains that did not reach a named holder in time say so. Never raises.
    """
    lines: List[str] = []
    t0 = time.monotonic()
    try:
        census = dict_shape_census()
        samples: Dict[ShapeKey, List[Any]] = {}

        if shape_baseline is not None:
            growth = Counter({s: n - shape_baseline.get(s, 0) for s, n in census.items()})
            ranked = [(s, d) for s, d in growth.most_common(top_shapes) if d > 0]
            lines.append(f"  Growth since the trace was armed — top {top_shapes} dict shapes by count increase:")
            if not ranked:
                lines.append("    (no dict shape grew)")
            for shape, delta in ranked:
                lines.append(f"    +{delta:>9,}  (now {census[shape]:>9,})  {_fmt_shape(shape)}")
            chase = [s for s, _ in ranked[:chase_shapes]]
        else:
            chase = []
        lines.append(f"  Top {top_shapes} dict shapes by count (gc-tracked dicts only):")
        for shape, count in census.most_common(top_shapes):
            lines.append(f"    {count:>10,}  {_fmt_shape(shape)}")
        for shape, _ in census.most_common():
            if len(chase) >= chase_shapes:
                break
            if shape not in chase and _is_data_shape(shape):
                chase.append(shape)

        # Pick samples spread across the population: gc.get_objects() lists the youngest
        # generation first, so mid-list and late-list picks cover both fresh and long-lived ones.
        wanted = set(chase)
        seen_per_shape: Dict[ShapeKey, int] = Counter()
        targets = {s: max(1, census[s] // (samples_per_shape + 1)) for s in chase}
        for obj in gc.get_objects():
            if type(obj) is dict:
                shape = _shape_of(obj)
                if shape in wanted:
                    seen_per_shape[shape] += 1
                    bucket = samples.setdefault(shape, [])
                    if seen_per_shape[shape] % targets[shape] == 0 and len(bucket) < samples_per_shape:
                        bucket.append(obj)
        roots = _named_roots(cache, coc_client)
        lines.append(
            f"  Referrer chains (sample -> holder -> ...; stops at a named holder, "
            f"budget {budget_s:.0f}s):"
        )
        lines.extend(_walk_chains(samples, roots, t0, budget_s, max_depth))
    except Exception as exc:  # diagnostic only — never break the profile
        lines.append(f"  (retention analysis failed: {type(exc).__name__}: {exc})")
    lines.append(f"  (retention analysis took {time.monotonic() - t0:.1f}s)")
    return lines


def _walk_chains(
    samples: Dict[ShapeKey, List[Any]],
    roots: Dict[int, str],
    t0: float,
    budget_s: float,
    max_depth: int,
) -> List[str]:
    """Walk every sample's referrer chain level by level, one heap walk per level for all."""
    chains: List[Dict[str, Any]] = []
    for shape, objs in samples.items():
        for obj in objs:
            chains.append({"shape": shape, "cur": obj, "steps": [_describe(obj)], "done": None,
                           "visited": {id(obj)}})
    # Our own bookkeeping must never be reported as a holder.
    own_ids = {id(chains), id(samples), id(roots)}
    own_ids.update(id(c) for c in chains)
    own_ids.update(id(c["steps"]) for c in chains)
    own_ids.update(id(c["visited"]) for c in chains)
    own_ids.update(id(v) for v in samples.values())
    frame = sys._getframe()
    while frame is not None:
        own_ids.add(id(frame))
        frame = frame.f_back

    for _depth in range(max_depth):
        active = [c for c in chains if c["done"] is None]
        if not active:
            break
        if time.monotonic() - t0 > budget_s:
            for c in active:
                c["done"] = "(time budget reached)"
            break
        cur_ids = {id(c["cur"]): c for c in active}
        currents = [c["cur"] for c in active]
        picked: Dict[int, Any] = {}
        own_ids.update((id(cur_ids), id(currents), id(picked), id(active)))
        referrers = gc.get_referrers(*currents)
        own_ids.add(id(referrers))
        n_cur = len(currents)
        for ref in referrers:
            if id(ref) in own_ids:
                continue
            # get_referrers(*currents) reports its own argument tuple as a referrer of every
            # current object; it is ours, not a holder.
            if type(ref) is tuple and len(ref) == n_cur and all(a is b for a, b in zip(ref, currents)):
                continue
            for child in gc.get_referents(ref):
                chain = cur_ids.get(id(child))
                if chain is None or id(chain) in picked or id(ref) in chain["visited"]:
                    continue
                picked[id(chain)] = ref
        del referrers, currents
        for chain in active:
            ref = picked.get(id(chain))
            if ref is None:
                chain["done"] = "(no gc-tracked referrer — held from C code or an untracked owner)"
                continue
            where = _slot_or_key(ref, chain["cur"])
            name = roots.get(id(ref))
            chain["steps"].append(f"{name or _describe(ref)}{(' ' + where) if where else ''}")
            chain["visited"].add(id(ref))
            chain["cur"] = ref
            if name is not None:
                chain["done"] = ""
    out: List[str] = []
    for chain in chains:
        out.append(f"    {_fmt_shape(chain['shape'])}:")
        out.append("      " + "\n        <- ".join(chain["steps"]))
        if chain["done"]:
            out.append(f"        {chain['done']}")
        elif chain["done"] is None:
            out.append(f"        (max depth {max_depth} reached)")
        chain["cur"] = None  # drop the sample reference promptly
    return out
