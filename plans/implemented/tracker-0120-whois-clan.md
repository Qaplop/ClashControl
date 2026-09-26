# Tracker #0120 — `/whois clan:<clan>`

## Goal
A third `/whois` mode next to `user` and `player`: pick a clan and see what the bot has stored
about it, then flip through its leaderboards (standard parameters) from a dropdown.

## Design
- New option `clan` on `/whois` (autocomplete = `/link clan`'s). Resolved like `/link clan`
  (`_get_clan_tag`); a clan the bot doesn't track has no DB data, so it gets a clear error instead
  of being auto-added.
- **DB:** `WarHistoryDB.get_clan_whois_stats_sync(clan_tag)` — the `clans` row (explicit columns)
  plus war aggregates from `main.war_summary` and `history.war_summary`, each aggregated on its
  own with explicit columns and summed in Python (Cardinal Rule 1): CW / CWL counts, W/L/D per
  type, first/last war date, distinct CWL seasons.
- **Report embed** (`t()` keys `commands.whois.clan_*`): name + tag + in-game link, badge
  thumbnail; "Tracked since" (`clans.created_at`), CWL league, members (live cache), clan level,
  capital league, war-log visibility, win streak; wars CW/CWL with records, first/last war, CWL
  seasons; war tracking status (`track_war_updates`, `is_deleted`), last war update / API check;
  member of which servers (member_clans + member_families, Pitfall 18), clan families,
  subscription count.
- **Leaderboard dropdown**: every `MODE_REGISTRY` mode (18 ≤ 25). Picking one edits the report
  message's second part in place with that leaderboard for the current month, scope "all" (raid
  modes: latest weekend; `cwlgroup`: latest season; `currentwar`/`currentraid` refresh first,
  like `/leaderboard`). Text modes go into an embed (`ansi` code block, plain sentinel sections
  as plain text); `cwlinfo(_comp)` use their own embeds; `cwlgroup` its image.
- **Visibility:** ephemeral, one message (report embed + dropdown, the leaderboard embed added
  below it). Unlike the player report there is nothing to track or clean up in the channel, and
  it works identically in the DM.

## Test cases (tracker)
- `/whois clan:<member clan>` → embed with all sections; servers list shows the guild.
- Dropdown: attack, currentwar, cwlgroup, raid → each renders; switching keeps one message.
- `/whois clan:#UNTRACKED` → "not tracked" error.
