# SearXNG VPN Egress (Gluetun + Proton WireGuard)

> **Opt-in, not default.** SearXNG ships **without** a VPN — the base
> `docker-compose.yml` runs it on the host's normal connection and works with zero
> configuration (this is what `Deploy-Homelab.ps1` boots). VPN routing is a separate,
> complete stack in `docker-compose.vpn.yml`, enabled by the **`Enable-SearxngVpn.ps1`**
> helper (at the repo root). Bare-metal deploys never ship a `vpn.env`, so they simply
> run the no-VPN base; nothing breaks. Turn the VPN on only if Google starts 403-ing you.
>
> ```powershell
> ..\Enable-SearxngVpn.ps1 -ConfPath .\searxng-us.conf   # enable (writes vpn.env)
> ..\Enable-SearxngVpn.ps1 -Disable                       # revert to no-VPN base
> ```

## Problem
Google was returning `SearxEngineAccessDeniedException` / `HTTP error 403` on image
search. Cause: Google flags the homelab's outbound IP (Costa Rica node /
datacenter-ish IP) as a bot. Requests routed through a **US** Proton node go
through. We need only SearXNG's outbound traffic on the US node — not the whole
host, which normally uses the fast Costa Rica node.

## Design
A `gluetun` container holds a WireGuard tunnel to a Proton VPN **US** server.
The `core` (SearXNG) container joins gluetun's network namespace
(`network_mode: "service:gluetun"`), so every request it makes exits through the
US tunnel. The host and all other containers are unaffected.

```
            ┌────────────────────────────────────────────┐
 LAN client │  searxng-gluetun  (WireGuard → Proton US)    │
   :8080 ───┼─▶ published port 8080                        │
            │     └── searxng-core (shares netns)          │──▶ Google etc.
            └────────────────────────────────────────────┘
 logo-rotator + vpn-rotator run on the default network (use docker.sock only).
```

> Valkey was removed from the stack — SearXNG only used it for the limiter (bot
> protection for *public* instances), which a private LAN instance doesn't need, and
> it provides no caching benefit (SearXNG's caches are SQLite-based and it doesn't
> cache search results). To re-add it, see "Re-enabling Valkey (optional)" below.

### Key consequences of the shared namespace
- The web UI port is published on **gluetun**, NOT on `core`. `core` must have no
  `ports:` block (Docker forbids `ports` + `network_mode: service:*` together).
- `logo-rotator` is untouched — it only uses the Docker socket and restarts
  `searxng-core` by name, which is network-independent.

## VPN exit-IP rotation (self-contained, in-stack)
Proton's US IPs get flagged by Google periodically, and a 403 does **not** make
gluetun switch servers on its own (it only re-selects on restart or a tunnel-health
failure). To cycle IPs automatically without any host scripts or scheduled tasks,
the stack includes a **`vpn-rotator`** sidecar (same pattern as `logo-rotator`):
it controls Docker via the socket and, every `VPN_ROTATE_INTERVAL` seconds:
1. restarts `searxng-gluetun` → gluetun picks a new random US server;
2. waits (up to ~90s) for gluetun to report **healthy**;
3. restarts `searxng-core`.

Step 3 is mandatory: `core` shares gluetun's netns, so a gluetun restart strands it
until it cycles too (manifests as a 502 from Caddy). The rotator does this in the
correct order so SearXNG self-heals.

- **Interval:** defaults to `3600` (hourly). Override with `VPN_ROTATE_INTERVAL`
  in `.env` (seconds).
- **Cost:** each rotation is a brief (~15s) SearXNG blip while containers cycle.
- **Manual rotate:** `docker restart searxng-vpn-rotator` won't rotate immediately
  (it sleeps first). To force one now: `docker restart searxng-gluetun` then
  `docker restart searxng-core`.

## Re-enabling Valkey (optional)
Valkey was removed (it only backs the limiter, useful for public instances, and adds
no caching). If you later expose SearXNG publicly and want the limiter, add it back.
No script is needed — it's a small, one-time paste. Do it in **both** compose files if
you use both stacks.

**1. Add the service.** In `docker-compose.yml` (base) add a plain service:

```yaml
  valkey:
    container_name: searxng-valkey
    image: docker.io/valkey/valkey:9-alpine
    command: valkey-server --save 30 1 --loglevel warning
    restart: always
    mem_limit: 128m
    volumes:
      - valkey-data:/data/
```

In `docker-compose.vpn.yml` (VPN) add the **same** block plus the two lines that put it
in the tunnel namespace (so `core` reaches it on localhost):

```yaml
    network_mode: "service:gluetun"
    depends_on:
      gluetun:
        condition: service_healthy
```

**2. Add the volume.** Under the top-level `volumes:` in each file, add `valkey-data:`.

**3. Point SearXNG at it.** In `core-config/settings.yml`, set the `valkey` url:
- base stack: `url: valkey://valkey:6379/0` (reaches it by service name)
- VPN stack:  `url: valkey://127.0.0.1:6379/0` (shared netns — localhost)

**4. (VPN only) Re-include it in the rotator restart.** In `docker-compose.vpn.yml`'s
`vpn-rotator` command, change `docker restart searxng-core` back to
`docker restart searxng-core searxng-valkey` so it cycles with the tunnel.

Then `docker compose ... up -d` the relevant stack. Enable the limiter itself per the
[SearXNG limiter docs](https://docs.searxng.org/admin/searx.limiter.html).

## Files
| File | Purpose | Committed? |
|------|---------|-----------|
| `docker-compose.yml` | **Base** stack, **no VPN** (port on `core`). The default. | yes |
| `docker-compose.vpn.yml` | **VPN variant** (gluetun + vpn-rotator + routed core) | yes |
| `../Enable-SearxngVpn.ps1` | Helper that writes `vpn.env` and switches base ⇄ VPN | yes |
| `vpn.env` | `WIREGUARD_PRIVATE_KEY` (secret), written by the helper | **no — gitignored** |
| `vpn.env.example` | Committed template for `vpn.env` | yes |
| `.env` | SearXNG runtime vars + optional `VPN_ROTATE_INTERVAL` (no secrets) | yes (tracked) |

### Switching modes
The two compose files share `name: searxng`, so they target the same project and
volumes. The helper switches between them; manually it's:
```sh
docker compose -f docker-compose.yml down        # stop base
docker compose -f docker-compose.vpn.yml up -d    # start VPN variant
# ...and the reverse to go back.
```
Because the published port moves from `core` (base) to `gluetun` (VPN), this must be a
file *swap* (`down` then `up -d`), not an override layer — Compose can't unset `ports`.

### Secret handling
The WireGuard private key lives **only** in `vpn.env`, which is listed in the
repo-root `.gitignore` (`searxng/vpn.env`). The non-secret tunnel parameters
(country, address, MTU) are inline in `docker-compose.vpn.yml` — these are not
sensitive. The key is pulled into gluetun via `env_file: ./vpn.env`.

> Note: `searxng/.env` is already tracked in git, which is why secrets go in
> `vpn.env` instead. Do not move the private key into `.env`.

## Gluetun configuration (Proton provider / auto-rotate mode)
gluetun picks a working US WireGuard server from its built-in list — only the
private key is required. More resilient than pinning one endpoint.

| Variable | Value | Notes |
|----------|-------|-------|
| `VPN_SERVICE_PROVIDER` | `protonvpn` | — |
| `VPN_TYPE` | `wireguard` | — |
| `SERVER_COUNTRIES` | `United States` | rotates among US servers |
| `WIREGUARD_ADDRESSES` | `10.2.0.2/32` | from the Proton conf `[Interface] Address` |
| `WIREGUARD_PRIVATE_KEY` | *(in vpn.env)* | from the Proton conf `[Interface] PrivateKey` |
| `WIREGUARD_MTU` | `1320` | **required on WSL2** — see below |
| `FIREWALL_OUTBOUND_SUBNETS` | LAN ranges | lets LAN clients reach the UI |

### ⚠️ WSL2 MTU gotcha (the thing that cost us an afternoon)
On Docker Desktop / WSL2, WireGuard path-MTU discovery fails (`PMTUD failed with
both ICMP and TCP` in the logs). The tunnel connects and small packets work (you
even get a US public IP), but larger packets — TLS handshakes, DNS-over-TLS, the
healthcheck dials to `cloudflare.com:443` / `github.com:443` — silently time out
(`i/o timeout`). gluetun then declares itself **unhealthy** and restart-loops, so
`core`/`valkey` never start.

**Fix:** pin `WIREGUARD_MTU: 1320` (instead of relying on discovery). This alone
resolves the unhealthy loop. Do NOT bother with `DOT: off` — it's deprecated in
recent gluetun (renamed `DNS_SERVER`) and an `off` value errors out with
`upstream type dot must be plain if the built-in DNS server is disabled`.

### Optional `.env` overrides
- `LAN_SUBNET` — set to your real LAN CIDR (e.g. `192.168.1.0/24`) to tighten the
  firewall instead of the broad default.
- `TZ` — defaults to `America/Costa_Rica`.

## Deploy
The deploy script and a plain `docker compose up -d` run the **no-VPN base**. To enable
the VPN variant, use the helper (preferred) or the explicit `-f` swap:
```powershell
..\Enable-SearxngVpn.ps1 -ConfPath .\searxng-us.conf
```
```sh
# manual equivalent
docker compose -f docker-compose.yml down
docker compose -f docker-compose.vpn.yml pull
docker compose -f docker-compose.vpn.yml up -d
```
Bring-up order in the VPN variant is automatic: `core` and `valkey` wait for gluetun's
healthcheck (tunnel up) via `depends_on: condition: service_healthy`.

## Testing criteria
1. **Tunnel up & correct exit IP**
   ```sh
   docker exec searxng-gluetun wget -qO- https://ipinfo.io/ip
   ```
   → must return a **US** IP (expect `146.70.183.130` or the Proton US range),
   NOT a Costa Rica IP.

2. **SearXNG exits via the tunnel** (shares gluetun netns):
   ```sh
   docker exec searxng-core wget -qO- https://ipinfo.io/ip
   ```
   → same US IP as above.

3. **Google image search works**: open `http://<host>:8080`, search images,
   then check engine stats (Preferences → Engine stats) — `google images`
   reliability should climb above 0 with no `403 / SearxEngineAccessDeniedException`.

4. **UI reachable on LAN**: `http://<host>:8080` loads from another LAN device.

5. **Secret not leaked**:
   ```sh
   git status --porcelain          # vpn.env must NOT appear
   git check-ignore searxng/vpn.env # must print the path
   ```

## Rollback
Restore the previous `docker-compose.yml` (remove `gluetun`, move the `ports:`
block back onto `core`, delete the two `network_mode`/`depends_on` blocks), then
`docker compose up -d`. The `vpn.env` and gitignore additions are harmless if left.

## Maintenance / rotation
- **Rotate key / change server**: generate a new WireGuard config at
  account.protonvpn.com, update `WIREGUARD_PRIVATE_KEY` in `vpn.env` and the
  endpoint/public-key/address values in `docker-compose.yml`, then
  `docker compose up -d gluetun`.
- **Auto-rotate across US servers instead of pinning**: switch gluetun to Proton
  provider mode — set `VPN_SERVICE_PROVIDER=protonvpn`,
  `SERVER_COUNTRIES=United States`, keep `WIREGUARD_PRIVATE_KEY` + 
  `WIREGUARD_ADDRESSES`, and drop the `VPN_ENDPOINT_*` / `WIREGUARD_PUBLIC_KEY`
  lines (gluetun resolves those from its built-in server list). More resilient if
  a single server goes down; pinning is more deterministic.
