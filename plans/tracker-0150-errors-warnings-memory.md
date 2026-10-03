# Tracker #0150 — Errors/warnings since 2026-09-28, memory restarts: analysis + fix plan

**Status:** ANALYSIS COMPLETE, fixes not started. Work the items in the order of §3, one per
commit/build, each with its own test case on the tracker.

**Data:** PROD logs 2026-09-13 → 2026-10-03 08:00 (synced to DEV), the 9 memory profiles in
`data/logs/memprofile_*.txt`, DEV copy of the PROD DB. Read together with
`plans/implemented/tracker-0009-memory-analysis.md`, `plans/tracker-0009-phase1-war-payload-retention.md`
and `clashcontrol/docs/PERFORMANCE_TUNING.md` (§ GC policy, § memory pressure / swap, 2026-09-07).

---

## 1. What the daily summaries are actually made of

| Category | Per day | New since | Verdict |
|---|---|---|---|
| `Could not find registration channel 1436391672400576663 for guild 1400585784347594882` | 250–285 | 2026-09-28 02:55 | **New, real bug** (§2.1) |
| `[LOOP-LAG]` | 190–400 | always (Sep 20: 508) | Baseline, = gen-2 GC pauses (PERFORMANCE_TUNING). Not new — **except Oct 3**: 101 s GC / 129 s stall from swap (§2.3) |
| `[DISCORD-WS] Gateway disconnected` | 9–19 | always (Sep 20: 15) | Baseline, auto-resumes. No action |
| `[RSS-RESTART]` | 1 on Sep 29, Sep 30, Oct 2, Oct 3 | see §2.2 | **New pattern: a daily ~4 GB ramp** |
| `[COC-API-ERROR] … HTTP error 0` | Oct 1: 117 W; **Oct 3: 2,807 W + 2,521 E** | external CoC outages | **External trigger, bad handling on our side** (§2.3) |
| `[DB-RECONNECT] database is locked` / `[DAILY-LOG-SUMMARY] failed` | Sep 28 only | — | Already fixed by tracker #0149 (build 131). No recurrence since build 132 went live on Sep 30 |
| `refresh_cwl_management_hub_message … 429 (30046 max edits)` | Sep 28, once | — | Low priority (§3, item 6) |
| Discord 503 on delete / leaderboard (Oct 1 21:02) | once | external | Retried successfully. No action |

Every run analysed is build 130 (src `330ffe99`) or 132 (src `6708ebfc`). Build numbers and fingerprints are consistent (Rule 17).

## 2. Root causes

### 2.1 Registration channel gone — warning every bump cycle forever

- The guild's registration message was last bumped 2026-09-27 11:18 UTC. From 2026-09-28 02:55 local,
  `bot.get_channel(1436391672400576663)` returns `None`.
- The bot is still in the guild: `Connected to 9 guild(s)` at every restart before and after.
  So the channel was deleted, or the bot lost access to it.
- `repost_anchored_message()` (ClashControl.py ~L5000) logs a warning and `continue`s, and nothing
  ever resolves it. `on_guild_channel_delete()` (QBcore.py ~L850) only cleans subscriptions and
  leaderboard messages, not the anchored-message configs (registration, CWL Management Hub,
  Player CWL Hub). It only logs when subscriptions were removed, which is why there is no trace of
  the delete itself.
- The same blind spot covers all three anchored-message features (they share the driver).

### 2.2 The memory restarts: a daily ~4 GB ramp from ~06:15 to ~10:00 local, since 2026-09-29

The restarts are not a slow creep. Hourly RSS per run shows:

- Sep 18–21 (build 46): flat at 2.6–2.8 GB for 3 days. Nightly maintenance adds only ~+150 MB.
- Sep 28 (build 130): flat (2.3 GB) all morning.
- **Sep 29, Sep 30, Oct 1, Oct 2 (builds 130/132): RSS climbs ~+700 to +1,100 MB/hour from ~06:15
  until ~10:00, then plateaus and is never returned.** Oct 1: 1.4 GB at 05:00 → 5.6 GB at 10:00 →
  flat 5.2–5.3 GB for the rest of the day. The next morning's ramp crosses 6,144 MB, which is why
  every restart happens at 09:37–10:06.

What it is **not** (each checked against the logs):

| Hypothesis | Evidence against |
|---|---|
| Fetch volume / 22 h waves (#0009) | 06–10 h fetch ~550–650 clans/cycle, same as the flat 00–04 h window. Same `[API-CALL-MIX]` |
| `active_wars` count (#0106) | climbs steadily all day (2.7 k → 12.6 k on Oct 1) while RSS is flat in the afternoon |
| GC policy / promotion | `[GC-STATS]` gen0/1/2 counts in the ramp window are the same as at night |
| SQLite page caches | 32 MB / 8 MB × 9 connections, bounded |
| CWL caches | `league_war=0 league_group=0` during the Oct 1 ramp |
| Web bridge / Activity | 0–9 requests/hour in the window |
| Code change | Sep 28 and Sep 29 ran the same build, and only Sep 29 ramps |

What the profiles do show: each profile was traced during a ramp (tracing starts at the RSS crossing,
~8 min before the dump). Each shows **~70–120 MiB of decoded CoC API JSON
(`aiohttp/client_reqrep.py:795`, `loads(...)`) still alive after the traced cycle.** The pre-ramp
Sep 18 profile shows 30 MiB. So roughly one cycle's worth of response data per cycle is being
retained, which matches the ~80 MB/cycle ramp. **The retainer is not identified.** The profile
starts tracing too late, and with `nframe=1` it can only name the decode line, not the holder.
Item 3 is therefore instrumentation first.

Note `[CACHE STRUCTURE SIZES]` cannot see coc.py's own HTTP response cache (`coc/http.py`: FIFO,
`cache_max_size=10000` default, entries expire via `call_later(max-age)`). It is a prime suspect,
since its entries are exactly decoded JSON. But it is capped at 10 k entries and would not explain a
time-of-day window alone, so it needs measuring, not assuming.

### 2.3 Oct 3: CoC API outage → 3 h cycle → post-outage memory explosion → swap → 101 s GC pause

Timeline (local time):

1. **02:01** cycle starts. From 02:03 every CoC request times out.
   - coc.py retries a timeout internally 5× (`ClientTimeout(total=30)` + 1+3+5+7 s backoff ≈ 170 s,
     then `GatewayError`).
   - **Our `coc_retry()` treats `GatewayError` (status 0) as a generic HTTP error and retries it 2 more
     times.** So one clan costs ~9 min. 902 clans / 50 concurrent ≈ 3 h.
   - **Cycle took 11,376 s.** `[COC-MAINTENANCE]` and `[COC-DNS-OUTAGE]` have circuit breakers;
     gateway timeouts don't.
2. **05:10–06:00** nightly maintenance (REINDEX 27 min). Correct and expected.
3. **06:00** cycle: 23,270 overdue inactive clans → capped to 5,000 + 1,224 war-critical. CoC still
   flaky until 07:01. **4,358 s.**
4. **07:13–07:45** catch-up cycles of ~5,300 clans each. **RSS 2.2 → 3.2 → 4.3 → 5.9 → 7.2 GB**
   (+1.1–1.6 GB per cycle).
5. Profile at 07:44:
   - `client_reqrep.py:795` **+1,059 MiB in 13.7 M allocations** in 18 min
   - glibc `arena=3,377 MB`, `free_not_returned=1,355 MB`, `large_mmap=636 MB` (normal: 67 MB)
   - **swap=4,892 MB** (normal: 23–90 MB)
6. That is the 2026-09-07 thrashing incident again (PERFORMANCE_TUNING § memory pressure): gen-2 GC
   pauses 10 s → 15 s → **101 s**, `[LOOP-LAG] 129 s`, Discord unservable. The RSS-restart fired and
   recovered it at 07:45.

The outage is external. The **3-hour stall, the 5,300-errors-in-5-hours log flood and the post-outage
memory blow-up are ours.** The Oct 1 10:27–14:28 `HTTP error 0` episode was a milder version that
recovered on retry.

## 3. Fix plan (in order)

### Item 1 — CoC gateway-timeout circuit breaker (§2.3) — HIGH

- In `coc_retry()` (`clashcontrol/coc_health.py`), handle `coc.GatewayError` explicitly:
  - **no wrapper-level retry**: coc.py already retried 5×, so ours turns 170 s into ~9 min
  - per-cycle trip after N gateway timeouts within a short window (e.g. 10 in 60 s)
  - once tripped, fast-fail all remaining calls this cycle
  - one WARNING, then DEBUG; same shape as `_maintenance_detected` / `_dns_failure_detected`
  - cleared at cycle start
- **Do not just lower coc.py's 30 s client timeout.** On normal days (Sep 29, Oct 2) the
  `[COC-API-SLOW]` calls show p50 11 s, p99 33 s, max 109 s. Some of that is queueing in the
  throttler, but the split is unmeasured. A shorter timeout risks failing healthy-but-queued calls.
  Revisit only with that split measured.
- Phase 1 must treat breaker-fast-failed clans like maintenance-failed ones: not finalised, not
  stamped as checked, retried next cycle.
- Tests: a breaker test mirroring the existing maintenance/DNS ones, plus "GatewayError is not
  retried by the wrapper".

### Item 2 — gentle post-outage catch-up + coc.py response cache sizing (§2.3) — HIGH

- After a cycle in which the breaker (or maintenance/DNS) tripped, or when the overdue backlog is
  far above normal, use a lower generic-inactive cap until the backlog drains. 5,000/cycle is what
  drove +1.1–1.6 GB/cycle on Oct 3.
  - This does not contradict the 2026-09-06 measurement (cap vs RSS anti-correlated in normal
    operation). The Oct 3 cycles were a different regime: 5,300 clans *every* cycle, back to back,
    right after an outage.
- Measure, then set `coc.Client(cache_max_size=…)` deliberately:
  - log `len(client.http.cache)` and its hit rate per cycle (does anything in our call pattern
    actually hit it within max-age?)
  - at ~100 KB of decoded JSON per response, the 10 k default allows ~1 GB
  - `coc_clan_cache` already provides our own clan caching

### Item 3 — identify the daily-ramp retainer (§2.2) — HIGH, instrumentation first

1. Add a scheduled diagnostic trace: start `tracemalloc` (nframe ≥ 8) at nightly-maintenance end,
   snapshot hourly until 11:00, write the diffs grouped by traceback.
   - Plus a per-cycle `[MEM-GAUGES]` line with:
     - `len(coc http cache)`
     - `len(loop._scheduled)`
     - `len(asyncio.all_tasks())`
     - `temp_war_objects` / `temp_war_stats` / `temp_war_metadata` sizes
     - `coc_clan_cache` size
   - Cheap, and it would have answered this ticket directly.
2. Let it run one morning on PROD, then fix whatever holds the decoded JSON.
   - Candidates, in order of suspicion:
     - coc.py HTTP cache (+ its never-trimmed `FIFO.__keys` deque)
     - a coc.py model graph kept alive by a cache (`_iter_members` generator pinning raw JSON;
       Pitfall 33)
     - per-clan accumulation in a CACHE dict
   - Do not ship a fix before the trace names the holder. The #0009/#0106 history is several
     plausible-but-wrong attributions.
3. Operator-side, independent of code (PERFORMANCE_TUNING § operator settings): confirm PROD's launch
   environment has `MALLOC_ARENA_MAX=2` and that `vm.swappiness` was lowered. The Oct 3 profile's
   1.35 GB `free_not_returned` and 4.9 GB swap suggest at least one of them is not in effect.

### Item 4 — anchored-message channel loss (§2.1) — MEDIUM

- In `repost_anchored_message()`, when `get_channel()` misses, confirm with `fetch_channel()`
  (Pitfall 14).
  - `NotFound` → disable that guild's anchored message: clear `channel_id`/`message_id`, persist, log
    once at WARNING naming guild + feature.
  - `Forbidden` / other → keep config; warn at most once per (guild, channel) per day.
- Extend `on_guild_channel_delete()` to do the same cleanup for registration and both CWL hubs when
  their channel is deleted.
- Immediate one-off: the admins of guild 1400585784347594882 need to pick a new registration
  channel (or the bot admin disables it there). The fix above would disable it automatically.
- Tests: NotFound clears config; Forbidden keeps it; the warning is rate-limited.

### Item 5 — outage log hygiene — MEDIUM (fold into item 1)

- Per-clan `[PHASE-1] Exception fetching clan …` ERRORs (603+421+76+… on Oct 3) should collapse into
  one per-cycle summary once the breaker is tripped.
- The daily summary should then show "CoC API outage 02:03–07:01 (N cycles affected)", not 5,000 lines.

### Item 6 — CWL hub edit limit 429/30046 — LOW

- One occurrence (Sep 28 16:29). Discord caps edits to messages older than 1 h.
- Check that `refresh_cwl_management_hub_message()` skips edits when the rendered content is
  unchanged. Otherwise leave it and watch for recurrence during the October CWL.

### Not actionable

- `[DISCORD-WS]` disconnects (baseline)
- Oct 1 Discord 503 (external, retried)
- baseline `[LOOP-LAG]` (documented gen-2 GC behaviour; its counts are *lower* now than on Sep 20)
- `[DB-RECONNECT]` (fixed by #0149)

## 4. Verification per item

- `.\run_tests.ps1` green.
- PROD: no `Could not find registration channel` repeats.
- Next CoC outage: cycle length bounded (minutes, not hours) and one `[COC-GATEWAY-OUTAGE]` line.
- Item 3: a morning trace that names the holder, then a morning with no ramp.
- RSS-restart no longer firing daily at ~09:40.
