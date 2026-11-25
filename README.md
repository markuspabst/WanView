# WanView

Line monitor for the Telekom Speedport Smart 4 (DSL + external 5G receiver) as a Docker container for a NAS, Synology or any Linux host. It polls the router continuously, logs every poll to CSV and shows current values and history in a web dashboard.

Server counterpart of the macOS menu bar app Linkbar; same protocol, same ratings, same CSV format (Linkbar logs can be copied into `data/logs`).

```text
browser ──HTTP──> FastAPI (web/)  ──> Monitor (app/: polling, state, CSV log)
                                        └──> Speedport client (speedport/) ──HTTPS──> router
data volume /data: logs/*.csv, settings.json, routers.json, secrets.json, dashboard.json
```

## Requirements

User requirements with acceptance criteria and status: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md). System requirements:

| Requirement | Needed for |
|---|---|
| Docker (amd64 or arm64), e.g. Synology Container Manager; or Python 3.12+ | running |
| Telekom Speedport Smart 4 (Typ A/B, Plus) | all data; other routers are shown as unsupported |
| Host in the router's network, router reachable at `https://speedport.ip` (or `ROUTER_URL`) | all data |
| Router device password (env or dashboard), bound to the router's serial number | DSL line details, 5G receiver cells, hybrid bonding, 5G overflow, LAN traffic, router restart |
| External 5G receiver on LAN1 ("Link") with hybrid bonding | 5G sections, overflow and DSL estimate |

## Install with Docker

Image: `ghcr.io/markuspabst/wanview` for `linux/amd64` and `linux/arm64`, built by GitHub Actions (`.github/workflows/docker.yml`): `latest` follows `main`, version tags (`v1.2.3`) give `1.2.3` and `1.2`. Current release: `1.3.0`. Up to 1.0.0 the image was `ghcr.io/markuspabst/routerlink`; replace it with `wanview` in `docker run` or `docker-compose.yml` (the data folder stays compatible).

### docker run

```sh
mkdir -p wanview/data && sudo chown 1000 wanview/data   # the container runs as uid 1000
docker run -d --name wanview --restart unless-stopped -p 8080:8080 \
  -v "$PWD/wanview/data:/data" \
  -e ROUTER_PASSWORD='device password' \
  ghcr.io/markuspabst/wanview:latest
```

Dashboard: `http://<host>:8080`. On first open it asks for a dashboard password or to keep the dashboard open (see [Dashboard password](#dashboard-password)). The router password is optional here; it can be entered in the dashboard.

### Docker Compose

Only `docker-compose.yml` is needed (optionally a `.env` from `.env.example`):

```sh
mkdir -p data && sudo chown 1000 data
docker compose up -d          # update: docker compose pull && docker compose up -d
```

### Synology (Container Manager)

Step-by-step guide: [docs/SYNOLOGY.md](docs/SYNOLOGY.md). In short: create `docker/wanview/data`, make it writable for uid 1000 (`chown -R 1000:1000`, e.g. via Task Scheduler as root), create a Container Manager project from `docker-compose.yml` with `ROUTER_URL` set to the router's IP address (Docker on DSM usually cannot resolve `speedport.ip`), then open the dashboard right away and choose the dashboard password.

### Troubleshooting

Problems with the data folder or the router address are logged as one-line warnings that name the fix, and router problems are also shown in the dashboard.

| Log message | Cause and fix |
|---|---|
| `Cannot write to /data…: permission denied` (container stops) | The data folder on the host is not writable for uid 1000: `chown -R 1000:1000 <data folder>`, or set `user:` to the folder's owner. |
| `Cannot resolve speedport.ip` | The host's DNS does not know the Speedport's name (typical in Docker). Settings → Router address or `ROUTER_URL`: `https://<router IP>`, e.g. `https://192.168.2.1`. |
| `… did not answer` / `No route to …` | Wrong address, or the host is not in the router's network (other router, VLAN, guest network). |
| `… refused the connection on port …` | Something answers at that address, but not a web server on that port: check the address and `https://`. |
| `TLS connection to … failed` | Address is not a Speedport, or `http://`/`https://` mixed up. |
| Port 8080 in use | Map another host port: `"8090:8080"`. |
| Dashboard password forgotten | Delete `dashboard.json` in the data folder and restart. |
| `DASHBOARD_PASSWORD is no longer used` | Remove the variable; the password is set in the dashboard. |

### Build the image yourself

```sh
docker build -t ghcr.io/markuspabst/wanview:latest .
# other architecture: docker buildx build --platform linux/amd64,linux/arm64 -t wanview .
```

## Run without Docker

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=./data PYTHONPATH=src .venv/bin/python -m wanview
```

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest`

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_URL` | `https://speedport.ip` | router address (dashboard setting takes precedence) |
| `ROUTER_PASSWORD` / `ROUTER_PASSWORD_FILE` | – | device password for the first router identified |
| `STATUS_INTERVAL` | `30` | status poll interval in seconds (10–600) |
| `DETAILS_INTERVAL` | `300` | line details interval in seconds (60–3600) |
| `SPEEDTEST_INTERVAL` | `0` | automatic speed test every N minutes (30–1440); 0 = off |
| `DATA_DIR` | `/data` | logs, settings, routers, passwords |
| `PORT` | `8080` | dashboard port |
| `HOST` | `0.0.0.0` | listening address; `127.0.0.1` makes the dashboard reachable from this machine only |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Routers and passwords

A device password is only valid for one router, so the router is identified by its serial number (`serial_number` in `Status.json`, readable without login) and the password is stored per serial number in `/data/secrets.json` (file mode 600, plain text: protect the volume). It is only sent to the router reporting that serial number.

- `ROUTER_PASSWORD` is assigned to the first router identified while no router is known yet.
- A Speedport not seen before is new: the dashboard offers "Set up" (password optional) or "Ignore"; until then only status values (no login) are read.
- Ignored routers are not monitored. Settings lists set-up and ignored routers; each can be switched or removed (removing deletes its password).

## Dashboard password

On first open the dashboard asks for a password or to keep it open; Settings → Dashboard password changes or removes it. With a password every page and API call requires HTTP basic auth (any user name). Until a choice is made the dashboard is open.

- Stored as a salted scrypt hash in `/data/dashboard.json` (file mode 600).
- Forgotten password: delete `dashboard.json` and restart the container; the next open asks again.
- Whoever opens a new installation first chooses; set it right after installing.
- HTTP basic auth over plain HTTP: the password crosses the network unencrypted. Use a reverse proxy with HTTPS beyond a trusted LAN.
- `DASHBOARD_PASSWORD` / `DASHBOARD_PASSWORD_FILE` (up to 1.1.0) are ignored; a warning is logged while one is set.

## Polling and the router's single login

The router allows one login at a time. Line details (DSL, receiver, bonding, LAN counters) are read every `DETAILS_INTERVAL` with login and logout per poll; a browser login may be refused for a few seconds then. Live LAN traffic (every 5 s) runs only while a dashboard is open and holds the login meanwhile.

- **Pause** stops all polling and logs out. "Router web interface" pauses first, then opens the router. Resume when done. Pausing is not persisted.
- **Restart router** (with confirmation) sends the same request as the router's Settings → Restart (`POST /data/Reboot.json`, `reboot_device=true`); polling waits 3 minutes. Only for a set-up router with password.

## Speed test

"Run" on the Speed test tab measures latency, download and upload against Cloudflare's speed test endpoints (`speed.cloudflare.com`, plain HTTPS). Cloudflare publishes no terms or limits for third-party or automated use of these endpoints (their own `@cloudflare/speedtest` library uses them by default); users report HTTP 429 after about 10 tests per hour from non-browser clients. WanView identifies itself honestly, respects `Retry-After` on 429 (scheduled tests wait), and schedules at most every 30 minutes. Each throughput phase runs 4 parallel streams for 8 s; the line is saturated for about 20 s and a test moves roughly 100–250 MB, partly over 5G. Automatic tests: Settings → "Speed test every (min)", 0 = off (default, or `SPEEDTEST_INTERVAL`), otherwise 30–1440 minutes.

With a set-up router and a 5G receiver, the receiver port's byte counters are read before and after each phase, so the result is split into DSL and 5G (other traffic during the test counts too). This holds the router login for the test (about 40 s with the readings). Results: `/data/logs/<date>-speedtest.csv` (`time,latency_ms,down_bps,up_bps,mobile_down_bps,mobile_up_bps`) and a History chart. The result measures the host's own connection: a host on Wi-Fi measures Wi-Fi, not the line. Each result is marked:

| Connection | Source | Meaning |
|---|---|---|
| wired / Wi-Fi | the host's own network interface (Linux: default route, `/sys/class/net`; macOS: `route`, `networksetup`) | reliable; in Docker only with `network_mode: host` (the default bridge network shows a virtual interface: unknown) |
| router Wi-Fi | router LAN port counters during the test (needs the router login) | the test traffic did not pass a LAN port: the host is on the router's Wi-Fi |
| LAN port (cable or mesh) | same | the traffic entered through a LAN port: cable, or Wi-Fi via a mesh access point on that port; inconclusive |

Wi-Fi results show "limits the result". In the history, Wi-Fi tests have their own "Download / upload over Wi-Fi" panel in violet/pink (shown only when the range contains any), the main chart shows the other tests, and the latency chart draws Wi-Fi tests in violet.

## Dashboard

Header: DSL sync rate and the receiver signal as bars (1–4, the RSRP rating; "4G" without 5G signal; dBm in the tooltip), as in Linkbar's menu bar. Tabs: Overview (connection, DSL, hybrid bonding, 5G receiver, LAN traffic live), DSL (history), 5G/LTE (history; only while a receiver is reported), Speed test (run, last result, history), Settings. Hybrid bonding and 5G receiver are hidden when no receiver is reported (`ex5g_model_name` empty); LAN1 is then listed under LAN traffic. History for 24 hours, 7 or 30 days; with less logged data (e.g. a new install) the time axis starts at the first value, and a chart without values shows "No data available". DSL tab: sync rate (normal rate dashed, offline periods red), SNR margin, attainable rate (line capacity), CRC and FEC errors per hour, attenuation, internet rate, and connection/DSL events (reconnects, firmware, DSL mode and state). Speed test tab: download/upload and latency per test, and the DSL/5G split per test when measured. 5G/LTE tab: receiver signal (RSRP), signal quality (RSRQ), hybrid bonding (5G overflow per details poll, current DSL limit in the header, periods with a bonding leg down shaded red), DSL overflow limit, and receiver events (state, 5G/LTE cell and band changes); each chart can be collapsed (remembered per browser).

Indicators (dot; poor is a red triangle). Rules of thumb, not standards:

| Value | Excellent | Good | Fair | Poor |
|---|---|---|---|---|
| 5G/LTE RSRP | ≥ −80 dBm | ≥ −90 dBm | ≥ −100 dBm | below |
| 5G/LTE RSRQ | ≥ −10 dB | ≥ −15 dB | ≥ −20 dB | below |
| DSL SNR margin | ≥ 12 dB | ≥ 10 dB | ≥ 6 dB | below |
| DSL CRC errors, increase per hour | 0 | ≤ 60 | ≤ 600 | above |
| DSL sync, share of the normal rate | ≥ 99 % | ≥ 95 % | ≥ 85 % | below |

The normal sync rate is the median of the last 7 days of logged status polls (per direction, recomputed hourly, at least 100 readings; sync 0 is left out); a rating below excellent is also flagged in the header. The receiver cell shows the worse of RSRP and RSRQ. Internet, DSL link and receiver state are up (online/running) or poor.

## Logs

`/data/logs/<date>-status.csv`, `<date>-line.csv`, `<date>-overflow.csv`, one file per UTC day and kind, timestamps in UTC, not pruned (about 300 KB per day). Columns as in Linkbar.

WanView only: `<date>-speedtest.csv` (see Speed test) and `<date>-events.csv` (`time,kind,value`): the first value seen and every change of `online_since` (reconnect), `firmware`, `dsl_mode`, `dsl_state`, `receiver_state`, `nr_cell`, `lte_cell` (physical cell IDs). 5G/LTE band changes come from the status log. LAN traffic per port is live only: logging it continuously would hold the router's single login.

5G overflow: average 5G rate between details polls from the receiver port's byte counters (`interfaces_hidden_lan.stm`, LAN1, `inte_face_rx/tx[0][1]`); after a container restart the last logged poll (at most three intervals old) is the reference. A counter that goes backwards (router reboot) gives no value.

## Data sources

| Router endpoint | Login | Values |
|---|---|---|
| `/data/Status.json` | no | link state, DSL sync, internet rate, online since, firmware, serial number, 5G/LTE signal and band, receiver model |
| `/engineer/html/dsl_hidden_status.stm` | yes | DSL mode/state, attainable rate, SNR margin, attenuation, CRC/FEC errors |
| `/engineer/html/hybrid_receiver.stm` | yes | 5G/LTE RSRP, RSRQ, physical cell ID, receiver state |
| `/engineer/html/hybrid_bonding.stm` | yes | bonding legs, DSL overflow limits |
| `/engineer/html/interfaces_hidden_lan.stm` | yes | LAN1–4 byte counters |

## Limitations

- Tested with Speedport Smart 4 Typ A, firmware 010139.5.0.001.1, from macOS (Python 3.13). The Docker image is built and published by GitHub Actions (amd64, arm64); it has been started on a Synology NAS; monitoring the router from there is not yet confirmed.
- The router's self-signed certificate is not verified (the client only talks to the configured router address).
- The DSL estimate is too low while Wi-Fi clients transfer and too high during local transfers between wired devices; no Wi-Fi or WAN byte counters are available.
- The router occasionally reports DSL sync 0 while online, the DSL page 0.0 dB for missing upstream levels, and the receiver RSRP/RSRQ 0.0 without a measurement; all are treated as no value (also in older log rows shown in the history).
- Router restart: if the router restarts before answering, a failure is shown although it restarts.
- The router's e-mail notification on security events (which may include logins) is not changed by WanView: settings data pages (`EMailNotify.json`, `SystemMessages.json`, `LAN.json`, `Overview.json`) return empty lists to this client although the router reports it as logged in (`loginstate=1`). Tried without success: loading the overview page first, browser headers, POST reads, `challengev`/`derivedk` cookies. Switch the notification off in the router: Einstellungen → E-Mail-Benachrichtigung → "Ein sicherheits-relevantes Ereignis ist aufgetreten".
- The router takes about 5–6 s to answer `Status.json` (about 130 KB with the device list), so identification and every status poll take that long; measured with curl as well.
