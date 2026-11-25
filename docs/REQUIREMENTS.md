# User requirements

Requirements as stated by the user, with acceptance criteria and verification. Status: **met** (verified as stated), **partly** (implemented, not fully verifiable), **open** (not implemented).

Verification: *test* = automated test in `tests/` (`pytest`); *manual* = checked against a Speedport Smart 4 Typ A (firmware 010139.5.0.001.1) with 5G receiver, from macOS.

## Operation

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-01 | Runs continuously in Docker on a NAS, Synology or Linux host, installable from a registry. | `docker compose up -d` with the published image (`ghcr.io/markuspabst/wanview`, amd64 + arm64) starts the service; data survives container restarts in `/data`. | GitHub Actions: tests passed, image built and published for amd64 and arm64 (`latest`, `1.1.0`, `1.1`); tags verified anonymously via the registry API. Started on a Synology NAS (data folder permission and router DNS needed fixes, now explained in the log); monitoring from there not yet confirmed. | partly |
| UR-02 | Complete line monitoring. | Status, line details, 5G receiver, bonding, overflow, LAN traffic, ratings and CSV logs. | test (domain and client tests), manual | met |
| UR-03 | Every poll is logged constantly. | Status, line details, overflow and speed tests are appended to daily CSV files. | test (`test_data_log_*`, CSV round trips); manual: 1,633 logged rows re-serialized byte-identically | met |
| UR-04 | Stats are shown in a web view. | Dashboard at `http://<host>:8080` with current values and history. | manual | met |
| UR-05 | The web view refreshes automatically. | Current values every 5 s, history every 30 s while visible, no reload needed. | manual | met |
| UR-06 | Optional protection of the dashboard; the password is defined on first open. | First open asks for a password or "Keep open"; with a password every route requires it, stored hashed, changeable and removable in Settings. | test (`test_dashboard_asks_for_access_choice_on_first_open`, `test_dashboard_password_*`); API and dashboard flows scripted (jsdom) against the running app; the browser's login prompt after setting the password not checked in a real browser | partly |

## Router identity and passwords

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-10 | A device password belongs to one specific router. | The password is stored per router serial number and only sent to the router reporting it. | test (`test_password_is_not_sent_to_another_router`), manual | met |
| UR-11 | A newly detected router offers a choice: set up or ignore. | Unknown router: prompt with "Set up" (password optional) and "Ignore"; until then only status values without login. | test (`test_unknown_router_gets_setup_prompt_and_no_login`, `test_saving_password_sets_up_router_and_logs_in`), manual | met |
| UR-12 | Set-up and ignored routers are listed in the settings. | Settings show both lists; each router can be switched or removed (removing deletes its password). | test (`test_ignored_router_is_not_monitored`), manual | met |
| UR-13 | Known routers are stored persistently. | Lists and passwords survive restarts (`routers.json`, `secrets.json` with mode 600). | test (`test_secrets_file_is_private`), manual restart | met |
| UR-14 | All stored data is connected to the router it belongs to. | Logs, interval and speed test settings per serial number (`routers/<serial>/`); history shows only the connected router; a router swapped at the same address is identified again before any login; older logs move to the first router identified. | test (`test_each_router_has_its_own_logs_and_settings`, `test_swapped_router_never_gets_the_old_password`, `test_logs_from_before_per_router_storage_move_to_the_first_router`, `test_unsafe_serial_cannot_escape_the_data_folder`) | met |

## Router control

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-20 | Monitoring can be paused so the router can be configured manually (it allows one login). | Pause stops all polling and logs out; resume restarts it. | test (`test_pause_stops_polling`), manual | met |
| UR-21 | The router's web interface can be opened from the dashboard. | "Router web interface" pauses first, then opens the router. | manual | met |
| UR-22 | The router can be restarted from the dashboard. | After confirmation the same request as the router's own restart button is sent; polling waits 3 min. | Request verified in the router's `restart.js`; restart itself not triggered during development. | partly |
| UR-23 | The router's e-mail notification on logins is checked at setup and can be disabled in the settings. | Setting read and switchable. | Settings pages return empty data to this client (cause unknown); not implemented. | open |

## Dashboard

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-30 | Values have status indicators where a rating makes sense. | Ratings for RSRP, RSRQ, SNR margin, CRC error increase, link states, sync vs. normal; poor shown with a different shape. | test (rating bands), manual | met |
| UR-31 | DSL sync below the normal sync is indicated. | Sync rated against the 7-day median; below normal flagged on the DSL row and in the header. | test (`test_sync_bands_against_normal`, `test_normal_sync_*`), manual | met |
| UR-32 | The header shows the 5G signal as bars, "4G" when there is no 5G. | 1–4 bars by RSRP rating; dBm in the tooltip. | manual (script run with stub DOM) | met |
| UR-33 | 5G and hybrid sections are hidden when no 5G receiver is attached. | Without `ex5g_model_name`: bonding and receiver sections, 5G bars and the 5G/LTE tab hidden; LAN1 listed under LAN traffic. | test (`test_receiver_needs_hybrid_and_model_name`); unplugged receiver not tested | partly |
| UR-34 | Overview rows fit on one line. | No wrapping; long values end in "…" with the full text on hover. | manual (row lengths with live data) | met |
| UR-35 | Tabs for overview, history and settings. | Tabs Overview, DSL, 5G/LTE, Speed test, Settings; selection kept in the URL. | manual (script run with stub DOM) | met |

## History

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-40 | History is split into DSL and 5G/LTE; the 5G/LTE tab only appears when available. | DSL tab: sync, SNR margin. 5G/LTE tab (only with receiver): signal, hybrid bonding. | manual (script run with stub DOM) | met |
| UR-41 | Each chart can be collapsed. | Collapsed state kept across reloads and refreshes. | manual (script run with stub DOM) | met |
| UR-42 | Charts are visually separated. | Each chart in its own card. | manual | met |
| UR-43 | Hybrid bonding is shown in history instead of plain traffic. | 5G overflow with the DSL limit, periods with a bonding leg down shaded red. | test (`test_history_includes_bonding_state`), manual | met |
| UR-44 | All important values are logged over time and shown in history. | Every logged measurement has a chart (incl. FEC, attenuation, internet rate, DSL overflow limit, speed test DSL/5G split); reconnects, firmware, DSL mode/state, receiver state, cells and bands are logged as change events and listed per tab. LAN traffic per port stays live only (needs the router's single login). | test (`test_changed_values_are_logged_as_events_once`, `test_events_are_not_repeated_after_restart`, `test_history_includes_all_logged_values`); charts and event lists scripted (jsdom) with crafted history; not checked against a live router | partly |

## Speed test

| ID | Requirement | Acceptance | Verification | Status |
|---|---|---|---|---|
| UR-50 | Internet speed test from the dashboard and on a schedule. | "Run" button; Settings interval 30–1440 min or off; results logged and charted. | test (`test_speed_test_*`), manual run | met |
| UR-51 | The speed test shows how much went over DSL and 5G. | Split from the receiver port counters during the test (with password and receiver). | test (`test_speed_test_logs_result_with_5g_split`), manual run | met |
| UR-52 | Speed test in its own tab. | Tab with Run, last result and download/upload and latency charts. | manual | met |
| UR-53 | A speed test run from a host on Wi-Fi is marked. | Result marked Wi-Fi ("limits the result"); in history Wi-Fi tests get their own panel in their own colours (only when present) and Wi-Fi-coloured latency points; from the host's interface (Linux, macOS) or the router's LAN port counters. | test (`test_speed_test_tells_wired_from_wifi`, `tests/test_host_network.py`); host check live on macOS (USB LAN adapter: wired); a Wi-Fi run of the full speed test with the macOS check is pending | partly |
