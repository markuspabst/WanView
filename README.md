# Routerlink

Line monitor for the Telekom Speedport Smart 4 (DSL + external 5G receiver) as a Docker container for a NAS, Synology or any Linux host. It polls the router continuously, logs every poll to CSV and shows current values and history in a web dashboard.

Server counterpart of the macOS menu bar app Linkbar; same protocol, same ratings, same CSV format (Linkbar logs can be copied into `data/logs`).

```text
browser ──HTTP──> FastAPI (web/)  ──> Monitor (app/: polling, state, CSV log)
                                        └──> Speedport client (speedport/) ──HTTPS──> router
data volume /data: logs/*.csv, settings.json, routers.json, secrets.json
```

## Requirements

| Requirement | Needed for |
|---|---|
| Docker (amd64 or arm64), e.g. Synology Container Manager; or Python 3.12+ | running |
| Telekom Speedport Smart 4 (Typ A/B, Plus) | all data; other routers are shown as unsupported |
| Host in the router's network, router reachable at `https://speedport.ip` (or `ROUTER_URL`) | all data |
| Router device password (env or dashboard), bound to the router's serial number | DSL line details, 5G receiver cells, hybrid bonding, 5G overflow, LAN traffic, router restart |
| External 5G receiver on LAN1 ("Link") with hybrid bonding | 5G sections, overflow and DSL estimate |

## Run with Docker Compose

```sh
cp .env.example .env          # set ROUTER_PASSWORD and DASHBOARD_PASSWORD
mkdir -p data && sudo chown 1000 data   # the container runs as uid 1000
docker compose up -d --build
```

Dashboard: `http://<host>:8080`. Logs: `docker compose logs -f`.

If `speedport.ip` does not resolve inside the container, set `ROUTER_URL` to the router's IP address (e.g. `https://192.168.2.1`).

### Synology (Container Manager)

1. Copy this folder to the NAS, e.g. `/volume1/docker/routerlink`, and create `.env` from `.env.example`.
2. Create the `data` subfolder and give uid 1000 write access (Control Panel or `sudo chown 1000 data` via SSH).
3. Container Manager → Project → Create → path `/volume1/docker/routerlink`, source: existing `docker-compose.yml` → Build and start.

For another architecture than the host's: `docker buildx build --platform linux/amd64,linux/arm64 -t routerlink .`

## Run without Docker

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=./data PYTHONPATH=src .venv/bin/python -m routerlink
```

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest`

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_URL` | `https://speedport.ip` | router address (dashboard setting takes precedence) |
| `ROUTER_PASSWORD` / `ROUTER_PASSWORD_FILE` | – | device password for the first router identified |
| `DASHBOARD_PASSWORD` / `DASHBOARD_PASSWORD_FILE` | – | dashboard login (HTTP basic auth, any user name); empty: no login |
| `STATUS_INTERVAL` | `30` | status poll interval in seconds (10–600) |
| `DETAILS_INTERVAL` | `300` | line details interval in seconds (60–3600) |
| `SPEEDTEST_INTERVAL` | `0` | automatic speed test every N minutes (30–1440); 0 = off |
| `DATA_DIR` | `/data` | logs, settings, routers, passwords |
| `PORT` | `8080` | dashboard port |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Routers and passwords

A device password is only valid for one router, so the router is identified by its serial number (`serial_number` in `Status.json`, readable without login) and the password is stored per serial number in `/data/secrets.json` (file mode 600, plain text: protect the volume). It is only sent to the router reporting that serial number.

- `ROUTER_PASSWORD` is assigned to the first router identified while no router is known yet.
- A Speedport not seen before is new: the dashboard offers "Set up" (password optional) or "Ignore"; until then only status values (no login) are read.
- Ignored routers are not monitored. Settings lists set-up and ignored routers; each can be switched or removed (removing deletes its password).

## Polling and the router's single login

The router allows one login at a time. Line details (DSL, receiver, bonding, LAN counters) are read every `DETAILS_INTERVAL` with login and logout per poll; a browser login may be refused for a few seconds then. Live LAN traffic (every 5 s) runs only while a dashboard is open and holds the login meanwhile.

- **Pause** stops all polling and logs out. "Router web interface" pauses first, then opens the router. Resume when done. Pausing is not persisted.
- **Restart router** (with confirmation) sends the same request as the router's Settings → Restart (`POST /data/Reboot.json`, `reboot_device=true`); polling waits 3 minutes. Only for a set-up router with password.

## Speed test

"Run" on the Speed test tab measures latency, download and upload against Cloudflare's speed test endpoints (`speed.cloudflare.com`, plain HTTPS; their terms for automated use are not verified). Each throughput phase runs 4 parallel streams for 8 s; the line is saturated for about 20 s and a test moves roughly 100–250 MB, partly over 5G. Automatic tests: Settings → "Speed test every (min)", 0 = off (default, or `SPEEDTEST_INTERVAL`), otherwise 30–1440 minutes.

With a set-up router and a 5G receiver, the receiver port's byte counters are read before and after each phase, so the result is split into DSL and 5G (other traffic during the test counts too). This holds the router login for the test (about 40 s with the readings). Results: `/data/logs/<date>-speedtest.csv` (`time,latency_ms,down_bps,up_bps,mobile_down_bps,mobile_up_bps`) and a History chart. The result measures the host's own connection: a host on Wi-Fi measures Wi-Fi, not the line. Each result is marked:

| Connection | Source | Meaning |
|---|---|---|
| wired / Wi-Fi | the host's own network interface (Linux: default route, `/sys/class/net`; macOS: `route`, `networksetup`) | reliable; in Docker only with `network_mode: host` (the default bridge network shows a virtual interface: unknown) |
| router Wi-Fi | router LAN port counters during the test (needs the router login) | the test traffic did not pass a LAN port: the host is on the router's Wi-Fi |
| LAN port (cable or mesh) | same | the traffic entered through a LAN port: cable, or Wi-Fi via a mesh access point on that port; inconclusive |

Wi-Fi results show "limits the result". In the history, Wi-Fi tests have their own "Download / upload over Wi-Fi" panel in violet/pink (shown only when the range contains any), the main chart shows the other tests, and the latency chart draws Wi-Fi tests in violet.

## Dashboard

Header: DSL sync rate and the receiver signal as bars (1–4, the RSRP rating; "4G" without 5G signal; dBm in the tooltip), as in Linkbar's menu bar. Tabs: Overview (connection, DSL, hybrid bonding, 5G receiver, LAN traffic live), DSL (history), 5G/LTE (history; only while a receiver is reported), Speed test (run, last result, history), Settings. Hybrid bonding and 5G receiver are hidden when no receiver is reported (`ex5g_model_name` empty); LAN1 is then listed under LAN traffic. History for 24 hours or 7 days. DSL tab: sync rate, SNR margin. Speed test tab: download/upload and latency per test. 5G/LTE tab: receiver signal (RSRP) and hybrid bonding (5G overflow per details poll, current DSL limit in the header, periods with a bonding leg down shaded red); each chart can be collapsed (remembered per browser).

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

- Tested with Speedport Smart 4 Typ A, firmware 010139.5.0.001.1, from macOS (Python 3.13). The Docker image itself has not been built or run yet.
- The router's self-signed certificate is not verified (the client only talks to the configured router address).
- The DSL estimate is too low while Wi-Fi clients transfer and too high during local transfers between wired devices; no Wi-Fi or WAN byte counters are available.
- The router occasionally reports DSL sync 0 while online; such readings are treated as no value.
- Router restart: if the router restarts before answering, a failure is shown although it restarts.
- The router's e-mail notification on security events (which may include logins) is not changed by Routerlink; settings pages under `/data/*.json` return empty lists to this client (cause unknown).
