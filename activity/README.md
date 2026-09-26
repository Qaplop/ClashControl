# CWL Clan-Config Activity

Phase A skeleton of the Discord Activity described in
`../clashcontrol/docs/CWL_CLAN_CONFIG_ACTIVITY_PLAN.md`. Two independent Node/TypeScript projects,
deployed to Cloudflare:

- **`server/`** — Cloudflare Worker (Hono). OAuth2 code→token exchange, plus a thin proxy to
  ClashControl's own bridge API (not wired up until Phase B).
- **`client/`** — Cloudflare Pages. Plain TypeScript, no framework (see the plan doc for why).
  Currently just proves the OAuth round-trip: shows "Hello, guild {id}" once launched from
  inside Discord.

Both currently install/typecheck/build clean with **zero `npm audit` findings** (last verified
2026-09-23 against wrangler 4.137, hono 4.13.8, sharp 0.35.4; first verified 2026-08-09 against
wrangler 4.x, vite 6.x, `@discord/embedded-app-sdk` 2.5.x — the versions
originally scaffolded pulled in known-vulnerable transitive deps via wrangler 3.x; bumped past
them rather than carrying that forward).

## One-time account setup (you, not me)

1. **Sign up for Cloudflare** at https://dash.cloudflare.com/sign-up — free, no credit card
   required for the tiers this plan uses (Workers: 100k requests/day free; Pages: unlimited
   requests, 500 builds/month free).
2. **Install and authenticate Wrangler** (already added as a dev dependency in both projects,
   so no global install needed):
   ```
   cd activity/server && npx wrangler login
   ```
   This opens a browser window to authorize the CLI against your new Cloudflare account —
   only needs to happen once per machine.
3. **Discord Developer Portal**, for *each* application (DEV and PROD both, per the plan):
   - This step needs a deployed URL first (chicken-and-egg) — see "First deploy" below, then
     come back here.
   - Developer Portal → your application → **Activities → Settings** → enable.
   - **URL Mapping**: root (`/`) → your `*.pages.dev` URL, `/api` → the Worker's own hostname
     (`api-dev.clashcontrol.uk` / `api.clashcontrol.uk`, see "Current hostnames and tunnels" below).
   - Note the **OAuth2 Client ID** (not secret, goes in `wrangler.toml`) and generate a
     **Client Secret** (real secret — goes in Wrangler secrets, never a file in this repo).

## First deploy (per environment: `dev` first, `prod` later)

```
cd activity/server
npm run deploy:dev          # first deploy — creates the Worker on its wrangler.toml route (+ workers.dev)
wrangler secret put CLIENT_SECRET --env dev   # paste the Client Secret from the Developer Portal

cd ../client
# copy .env.example to .env.local, fill in VITE_CLIENT_ID with the DEV application's Client ID
npm run deploy:dev          # gives you the *.pages.dev URL
```

Now go back to the Developer Portal step above and fill in the URL Mapping with the two URLs
you just got.

**Client Client-ID note**: `deploy:dev`/`deploy:prod` (in `client/package.json`) build with
`vite --mode dev`/`--mode prod`, which load `.env.dev.local`/`.env.prod.local` respectively
(copy `.env.example` to each, gitignored) — so both environments' `VITE_CLIENT_ID` can coexist
without manually swapping one shared file before every deploy. Plain `npm run dev`/`npm run
build` (no mode flag) still fall back to `.env.local`.

## This is two separately-deployed pieces — deploying one doesn't deploy the other

`client/` (Cloudflare Pages) and `server/` (Cloudflare Worker) ship independently via their own
`deploy:dev`/`deploy:prod` npm scripts. A request to "deploy the Activity" (or just "deploy the
frontend") means checking both, not whichever half the wording literally names — they're one
feature to the person using it.

**2026-08-23 incident**: the server's `/cwl/enrollment/status` route (tracker #0014) was committed
2026-08-22, but the PROD Worker's last deploy was 2026-08-19 — three days stale. A "deploy the
frontend to prod" request only redeployed `client/`, leaving the Worker still missing that route.
The right-click "Set enrollment status" admin action then failed live with `Action failed: not
found` (Hono's own 404 catch-all — the route genuinely didn't exist in the running Worker), with no
build error anywhere to catch it, since `client/`'s own `npm run build`/`typecheck` know nothing
about `server/`'s deployed state. **Before calling a deploy done, check `cd activity/server &&
npx wrangler deployments list --env <dev|prod>` for the last deploy time and compare against
`git log -1 -- activity/server/src` — if server source changed more recently than its last deploy,
redeploy it too, even if only the client was asked for.**

## Editing `client/src/*` does not ship anything by itself

The live Discord Activity iframe loads the built Cloudflare Pages bundle in `client/dist/`, not
the TypeScript source — `npm run dev`/`tsc --noEmit` only validate the source, they don't touch
what's deployed. After any change to `client/src/*.ts` (or its `index.html`/CSS) that needs to be
testable live, redeploy in the same step:

```
cd activity/client && npm run deploy:dev
```

(2026-08-19 incident: a same-session fix to `clanConfigTable.ts` was implemented and
typecheck-verified correctly, but the bundle was never rebuilt/redeployed. The user tested it live
hours later, saw the old broken behavior, and reasonably reported it as a fresh regression — it
was actually just an undeployed fix. `ls -la dist/assets` vs. `src/*.ts` mtimes is the fast way to
confirm/rule this out if a "regression" is reported for something that was supposedly just fixed.)

PROD (`npm run deploy:prod`) still needs explicit user confirmation before every deploy — this
redeploy-on-edit habit applies to DEV only.

**Cloudflare Pages "Preview" vs "Production" trap (2026-08-23 incident):** `client/`'s
`deploy:dev`/`deploy:prod` scripts always pass `wrangler pages deploy dist --branch=main`. Do
not remove that flag or run `wrangler pages deploy` manually without it. Cloudflare Pages only
publishes a deploy to the bare `*.pages.dev` domain (the one Discord's Activity URL Mapping and
`wrangler.toml`'s `REDIRECT_URI` actually point at) when the deployed branch equals the
project's configured production branch (`main`) — `wrangler pages deploy` otherwise
auto-detects the *locally checked-out git branch* and tags the deploy with that name instead,
which lands as an inert "Preview" at a branch-scoped URL nobody's Activity ever loads. This repo
develops CWL Activity work on feature branches (e.g. `cwl-player-hub`) while `main` stays
untouched until merge, so a plain `deploy:dev` run from a feature branch silently ships to a URL
Discord never sees — the build succeeds, `npx wrangler pages deploy` reports success, and the
live Activity keeps serving the previous bundle with no error anywhere. `--branch=main` forces
the deploy to be tagged as `main`/Production regardless of which branch is actually checked out
locally, without requiring an actual `git checkout main` (which would swap every tracked file on
disk and could break a bot currently running off the feature branch — see `git worktree` instead
if you ever need both checked out at once).

## Legal pages (Terms of Service / Privacy Policy)

`client/public/terms.html` and `client/public/privacy.html` are copied as-is into `dist/` by Vite
and served by Pages at `/terms` and `/privacy`. The PROD URLs
(`https://cwl-clan-config-prod.pages.dev/terms` and `/privacy`) are entered in the Developer
Portal under General Information, where Discord requires them for app verification. They go live
only with a client `deploy:prod`. Keep `privacy.html` in line with what the bot actually stores (see
copilot-instructions Rule 15).

## Local development

Discord cannot embed `localhost` URLs directly — for local iteration once Phase A's spike is
validated, use a `cloudflared` tunnel to your Vite dev server and point a *second* URL Mapping
(or a temporary swap of the existing one) at the tunnel URL. Not needed to get the initial
skeleton deployed and working — `npm run dev` in each project is enough to catch build errors
before deploying.

## Current hostnames and tunnels (since 2026-09-26)

| Piece | DEV | PROD |
|---|---|---|
| Pages (`/` URL Mapping, legal pages) | `cwl-clan-config-dev.pages.dev` | `cwl-clan-config-prod.pages.dev` |
| Worker (`/api` URL Mapping) | `api-dev.clashcontrol.uk` | `api.clashcontrol.uk` |
| Worker fallback (workers.dev, kept enabled) | `cwl-clan-config-server-dev.clashcontrol.workers.dev` | `cwl-clan-config-server-prod.clashcontrol.workers.dev` |
| Bridge hostname (Worker secret `BRIDGE_URL`) | `https://bridge-dev.clashcontrol.uk` | `https://bridge-prod.clashcontrol.uk` |
| Named tunnel | `clashcontrol-dev-bridge` (Windows, `%USERPROFILE%\.cloudflared\config.yml`, started by hand) | `clashcontrol-prod-bridge` (NAS, `/root/.cloudflared/config.yml`, supervisor below) |
| Bot bridge port (`.env`) | `WEB_BRIDGE_PORT_DEV=8788` | `WEB_BRIDGE_PORT=8789` |

The Worker hostnames are Custom Domains declared as `routes` in `server/wrangler.toml` (wrangler
creates DNS + certificate on deploy). The tracker MCP server reaches PROD's bridge via
`TRACKER_BRIDGE_URL=https://bridge-prod.clashcontrol.uk` in `.env`.

DEV tunnel, run from the project root (the DEV bot must be running for `/api/health` to answer --
a Cloudflare `502` means the tunnel is up but nothing listens on the port, `1033` means no tunnel
connector is running at all):
```powershell
cloudflared tunnel --config "$env:USERPROFILE\.cloudflared\config.yml" run clashcontrol-dev-bridge
curl.exe -sS https://bridge-dev.clashcontrol.uk/api/health
```

### Moving to a different domain (runbook from the qapbot.uk -> clashcontrol.uk switch)

Named tunnels can't be renamed, so a domain move means new tunnels running side by side with the
old ones until the new path is verified:

1. `cloudflared tunnel create <new-name>` (Windows for DEV; on the NAS as root with `HOME=/root`
   for PROD) -> note the Tunnel ID.
2. **Create the DNS record by hand** in the dashboard (new zone -> DNS -> Add record: `CNAME`,
   name `bridge-dev`/`bridge-prod`, target `<tunnel-id>.cfargotunnel.com`, **Proxied**). Do *not*
   use `cloudflared tunnel route dns` here: it creates the record in the zone `cert.pem` was issued
   for at `tunnel login` time, so a hostname in a different zone silently ends up as
   `bridge-prod.newdomain.tld.olddomain.tld`.
3. Back up `config.yml`, point it at the new Tunnel ID / credentials JSON / hostname, and use
   `http://127.0.0.1:<port>` rather than `localhost` (cloudflared tries IPv6 `[::1]` first; the
   bridge only binds IPv4 `127.0.0.1`).
4. Run the new tunnel in the foreground next to the old one, check `/api/health` on the new
   hostname, then `wrangler secret put BRIDGE_URL --env <dev|prod>` (no trailing slash) and test
   the Activity.
5. PROD: change the tunnel name in `/volume1/@cloudflared/cloudflared-supervisor.sh`, then
   `kill $(cat /volume1/@cloudflared/supervisor.pid)` and start the supervisor again
   (`nohup bash /volume1/@cloudflared/cloudflared-supervisor.sh >/dev/null 2>&1 &`).
6. Worker hostnames: change `routes` in `server/wrangler.toml`, keep `workers_dev = true` (with
   `routes` present wrangler otherwise disables workers.dev on deploy and cuts off a URL Mapping
   that still points there), deploy, then switch the `/api` URL Mapping in the Developer Portal.
   A brand-new hostname can be `NXDOMAIN` in the *local* resolver cache for a while if it was
   queried before it existed -- check with `Resolve-DnsName <host> -Server 1.1.1.1` before
   assuming the deploy failed.
7. Update `TRACKER_BRIDGE_URL` in `.env`, then clean up: `cloudflared tunnel cleanup <old>` +
   `cloudflared tunnel delete <old>`, delete the old zone's DNS records.

## PROD rollout — NAS bridge & named tunnel (Phase D)

DEV originally used `cloudflared`'s free **quick tunnel** (a dev restarts it by hand anyway) and
has used a named tunnel too since 2026-09-26 (see above). PROD runs unattended on the NAS
(`PROD_BOT_ROOT`/`PROD_SSD_UNC` in `.env`). A quick tunnel mints a brand-new random
`*.trycloudflare.com` URL on every restart with no notification — after any NAS reboot the
bridge silently goes dark until someone notices the Activity is broken and manually re-runs
`wrangler secret put BRIDGE_URL`. A **named tunnel** gets a permanent hostname that survives
restarts, at the cost of needing one domain in the same Cloudflare account.

1. **Get a domain** (skip if you already own one anywhere — you can add it to this Cloudflare
   account as a zone for free and just use a subdomain, no need to buy a second one):
   Cloudflare dashboard → **Domain Registration → Register a Domain** → search → buy (sold at
   cost, no markup; nameservers auto-configured, no manual DNS delegation step). Any cheap TLD
   is fine — nothing here is user-facing, only the Worker's `BRIDGE_URL` config ever uses it.
   (Currently `clashcontrol.uk`; `qapbot.uk` until 2026-09-26.)
2. **Generate the bridge secret** (skip if `.env`'s `WEB_BRIDGE_SECRET` — PROD, no `_DEV` suffix
   — is already filled in): a random token, e.g. `openssl rand -base64 32`. Put the same value
   in two places and nowhere else:
   - This repo's `.env` → `WEB_BRIDGE_SECRET=` (already gitignored).
   - The Worker: `cd activity/server && npx wrangler secret put BRIDGE_SECRET --env prod`
     (paste at the prompt).
3. **On the NAS**, install `cloudflared` if not already present (Synology: SSH in, check
   `uname -m` for the CPU architecture, download the matching binary from
   `github.com/cloudflare/cloudflared/releases/latest`, `chmod +x`).
4. **Authenticate and create the named tunnel** (one-time, run on the NAS):
   ```
   cloudflared tunnel login                              # opens a browser auth against your Cloudflare account
   cloudflared tunnel create clashcontrol-prod-bridge     # writes a credentials JSON, prints a Tunnel ID
   cloudflared tunnel route dns clashcontrol-prod-bridge bridge-prod.clashcontrol.uk   # creates the DNS record
   # ^ only if `tunnel login` was done against clashcontrol.uk -- the current cert.pem on both
   #   machines is still from the qapbot.uk login, so create the CNAME by hand (runbook above).
   ```
5. **Config file** (e.g. `~/.cloudflared/config.yml` on the NAS):
   ```yaml
   tunnel: clashcontrol-prod-bridge
   credentials-file: /root/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: bridge-prod.clashcontrol.uk
       service: http://127.0.0.1:8789   # WEB_BRIDGE_PORT from .env (not localhost: see runbook above)
     - service: http_status:404
   ```
6. **Test it**: `cloudflared tunnel run clashcontrol-prod-bridge` (with the PROD bot already running
   and its bridge listening on `127.0.0.1:8789`), confirm `https://bridge-prod.clashcontrol.uk/api/health`
   responds, then stop it and set up auto-start — on Synology DSM,
   **Control Panel → Task Scheduler → Create → Triggered Task → User-defined script**, trigger
   **Boot-up**, user **root** (must be root — a non-root user can't read `/root/.cloudflared/`'s
   credentials at all, confirmed the hard way). Run command:
   ```sh
   sleep 15
   HOME=/root /usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run clashcontrol-prod-bridge >> /var/log/cloudflared-prod-bridge.log 2>&1
   ```
   The `sleep 15` and explicit `HOME`/`--config` aren't optional — DSM's Boot-up trigger can fire
   before the network is fully up, and `cloudflared`'s default `~/.cloudflared/` lookup isn't
   reliable that early either (see "Survive a DSM upgrade" below for a second, related timing
   gotcha this same design has to account for). Redirecting output to a file was necessary
   because DSM's own Task Scheduler "View Result" panel shows nothing useful unless you've
   separately configured an output-log folder in Task Scheduler's settings — the script logging
   itself sidesteps that entirely and is what actually revealed the underlying bug the first
   time this failed silently on a real reboot.
7. **Survive a DSM upgrade** (same reasoning as this project's own `Entware sichern` boot task —
   see `backlog.txt`): anything outside `/volume1` (the actual data volume) is *not* guaranteed
   to survive a DSM upgrade — that's a documented incident on this NAS already (a prior DSM
   upgrade wiped Entware's `/opt` install). `/usr/local/bin/cloudflared` and `/root/.cloudflared/`
   (cert, tunnel credentials, `config.yml`) are both in that same at-risk category. Move both
   onto `/volume1` and symlink back, mirroring Entware's exact `/opt` → `/volume1/@entware`
   pattern:
   ```sh
   mkdir -p /volume1/@cloudflared/bin
   mv /usr/local/bin/cloudflared /volume1/@cloudflared/bin/cloudflared
   ln -sf /volume1/@cloudflared/bin/cloudflared /usr/local/bin/cloudflared

   mv /root/.cloudflared /volume1/@cloudflared/dotcloudflared
   ln -sf /volume1/@cloudflared/dotcloudflared /root/.cloudflared
   ```
   The credentials directory is the more important half to protect — losing the binary just
   needs a re-download, but losing `/root/.cloudflared/` means redoing
   `tunnel login`/`create`/`route dns` and updating the Worker's `BRIDGE_URL` secret again. Then
   make the boot script self-healing (same `[ -L path ] || ln -sf ...` idiom as `Entware
   sichern`'s own script) so a future DSM upgrade that wipes the symlinks gets them back
   automatically on the next boot, before the tunnel start line from step 6:
   ```sh
   [ -L /usr/local/bin/cloudflared ] || ln -sf /volume1/@cloudflared/bin/cloudflared /usr/local/bin/cloudflared
   [ -L /root/.cloudflared ] || ln -sf /volume1/@cloudflared/dotcloudflared /root/.cloudflared
   sleep 15
   HOME=/root /usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run clashcontrol-prod-bridge >> /var/log/cloudflared-prod-bridge.log 2>&1
   ```
   **Resolved by a real reboot test (2026-08-10)**: this migration added a new boot-time
   dependency the original script didn't have — `/volume1` must actually be mounted before these
   symlinks resolve to anything real, and DSM's Boot-up trigger firing before that happens was a
   documented possibility, not just a network-readiness question. The plain `sleep 15` above had
   only ever been validated against the network-timing bug (via a warm Task Scheduler
   "Ausführen" run, not a genuine cold boot); a full NAS restart with this exact script confirmed
   it covers the volume-mount timing too — both the bridge and the tunnel came up cleanly with no
   manual intervention, verified externally via `/api/health` immediately after boot. The
   wait-for-the-actual-dependency version below is therefore unnecessary in practice on this NAS,
   but is kept here as a more defensive fallback in case a slower/busier boot ever pushes past the
   `sleep 15` margin:
   ```sh
   for i in $(seq 1 30); do
     [ -e /volume1/@cloudflared/bin/cloudflared ] && break
     sleep 1
   done
   [ -L /usr/local/bin/cloudflared ] || ln -sf /volume1/@cloudflared/bin/cloudflared /usr/local/bin/cloudflared
   [ -L /root/.cloudflared ] || ln -sf /volume1/@cloudflared/dotcloudflared /root/.cloudflared
   sleep 15
   HOME=/root /usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run clashcontrol-prod-bridge >> /var/log/cloudflared-prod-bridge.log 2>&1
   ```
   Entware's own boot task has the identical theoretical gap (no wait for `/volume1` before its
   `ln -sf`) but has never hit it in practice — its script only *restores a pointer*, it doesn't
   try to *use* anything through it in the same script, so a momentarily-dangling symlink at
   boot is harmless there (whatever eventually calls `zstd` runs much later, by which point
   `/volume1` is certainly mounted). `cloudflared`'s script is different because it tries to
   start a service through the symlink immediately, in the same script — that's what makes the
   timing actually matter here.
8. **Supervise it** (2026-08-14 incident + 2026-08-15 fix — see
   `../clashcontrol/docs/CWL_CLAN_CONFIG_ACTIVITY_PLAN.md`'s Phase D section for the full narrative):
   step 6's raw `cloudflared tunnel ... run ...` line dies silently whenever `cloudflared`'s own
   autoupdate replaces its binary and exits (on by default, ~daily) — DSM's Boot-up trigger
   doesn't notice or restart it, so a routine autoupdate meant 26h of downtime the one time it
   happened. Fixed by never running `cloudflared` directly from Task Scheduler again — instead
   run a small supervisor script that restarts it on any exit:
   ```sh
   sudo vim /volume1/@cloudflared/cloudflared-supervisor.sh
   ```
   ```sh
   #!/bin/bash
   # Supervises the clashcontrol-prod-bridge cloudflared tunnel: restarts it on ANY exit
   # (crash, OOM, or a routine "cloudflared has been updated" self-shutdown -- autoupdate
   # is deliberately left ON) so a silent exit no longer means ~26h of downtime like
   # 2026-08-14. Boot-up-triggered by DSM Task Scheduler; this loop is what actually keeps
   # it alive between exits, not just across reboots.
   #
   # To stop deliberately: kill THIS script's PID (written to $PIDFILE below), NOT
   # cloudflared's PID directly -- killing cloudflared alone just gets it restarted.

   LOG=/var/log/cloudflared-prod-bridge.log
   PIDFILE=/volume1/@cloudflared/supervisor.pid
   STOP=0
   CF_PID=

   echo $$ > "$PIDFILE"
   trap 'STOP=1; echo "$(date -Iseconds) INF supervisor got stop signal, killing cloudflared" >> "$LOG"; [ -n "$CF_PID" ] && kill "$CF_PID" 2>/dev/null' TERM INT

   for i in $(seq 1 30); do
     [ -e /volume1/@cloudflared/bin/cloudflared ] && break
     sleep 1
   done
   [ -L /usr/local/bin/cloudflared ] || ln -sf /volume1/@cloudflared/bin/cloudflared /usr/local/bin/cloudflared
   [ -L /root/.cloudflared ] || ln -sf /volume1/@cloudflared/dotcloudflared /root/.cloudflared
   sleep 15

   while [ "$STOP" -eq 0 ]; do
     echo "$(date -Iseconds) INF supervisor starting cloudflared" >> "$LOG"
     HOME=/root /usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run clashcontrol-prod-bridge >> "$LOG" 2>&1 &
     CF_PID=$!
     wait "$CF_PID"
     if [ "$STOP" -eq 0 ]; then
       echo "$(date -Iseconds) WRN cloudflared exited unexpectedly -- restarting in 5s" >> "$LOG"
       sleep 5
     fi
   done

   rm -f "$PIDFILE"
   echo "$(date -Iseconds) INF supervisor stopped intentionally" >> "$LOG"
   ```
   ```sh
   sudo chmod +x /volume1/@cloudflared/cloudflared-supervisor.sh
   ```
   Kept in `/volume1/@cloudflared/` — same DSM-upgrade-protected location as the binary/
   credentials from step 7, not `/root`. Then edit the DSM Task Scheduler task from step 6
   (**Control Panel → Task Scheduler**, same task, still triggered on **Boot-up**) to run the
   supervisor instead of `cloudflared` directly:
   ```sh
   bash /volume1/@cloudflared/cloudflared-supervisor.sh
   ```
   **To stop the tunnel intentionally** (maintenance etc.): `kill $(cat
   /volume1/@cloudflared/supervisor.pid)` — killing `cloudflared`'s own PID directly does *not*
   count as intentional and just gets it restarted, by design (the supervisor only traps a
   signal sent to *itself*). **Verified live 2026-08-15**: `sudo kill -9 $(pgrep -f "cloudflared
   tunnel --config")` (simulating a hard crash, not just autoupdate's own graceful exit) →
   supervisor logged `WRN cloudflared exited unexpectedly -- restarting in 5s` and a fresh tunnel
   reconnected within seconds; a full NAS reboot afterward also came back up clean through the
   same Boot-up trigger, now pointing at the supervisor.
9. **Wire the Worker to it**: `cd activity/server && npx wrangler secret put BRIDGE_URL --env prod`
   → `https://bridge-prod.clashcontrol.uk` (no trailing slash).
10. **Smoke test**: launch the Activity from a real PROD guild, confirm the clan-config table
    loads real data and Save round-trips through the whole chain.

## Status

All phases (A-E, skeleton through PROD rollout and the workflow redesign) are shipped and
verified live in both DEV and PROD — see `../clashcontrol/docs/CWL_CLAN_CONFIG_ACTIVITY_PLAN.md` for
the full phase-by-phase history. This file's "PROD rollout" section above is Phase D.
