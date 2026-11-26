# Installing WanView on a Synology NAS

Step by step with Container Manager. Menu names are from DSM 7.2. On the first Synology install, the data folder permission (step 2) and the router address (step 3) needed fixing; both are explained below.

## Requirements

| Requirement | Check |
|---|---|
| DSM 7.2 or newer with **Container Manager** | Package Center → search "Container Manager" → Install. DSM 7.0/7.1: the package is called "Docker"; the steps are similar. |
| x86-64 (Intel/AMD) or 64-bit ARM model | If Package Center offers Container Manager for your model, the image runs. |
| NAS in the Speedport's network | The NAS gets its address from the Speedport (typically `192.168.2.x`). Not through another router, VLAN or guest network. |
| Speedport device password (optional) | On the router's label; needed for DSL details, 5G receiver, bonding and LAN traffic. |

## 1. Create the folders

File Station → shared folder `docker` (create it in Control Panel → Shared Folder if missing) → create folder `wanview`, inside it `data`.

Result: `/volume1/docker/wanview/data` (the volume number may differ: File Station → right-click the folder → Properties shows the full path).

## 2. Let the container write to the data folder

The container runs as user ID 1000, which is usually not a DSM user. Without this step the log shows `Cannot write to /data: permission denied` and the container keeps restarting.

**Option A: Task Scheduler (no SSH needed)**

1. Control Panel → Task Scheduler → Create → Triggered Task → User-defined script.
2. General: Task `wanview permissions`, User `root`, Event `Boot-up`, untick "Enabled" (it only needs to run once).
3. Task Settings → Run command:
   ```sh
   chown -R 1000:1000 /volume1/docker/wanview/data
   ```
4. OK, select the task, **Run**. Afterwards the task can be deleted.

**Option B: SSH**

Control Panel → Terminal & SNMP → enable SSH, then:

```sh
sudo chown -R 1000:1000 /volume1/docker/wanview/data
```

**Option C: run the container as your DSM user**

Get the IDs over SSH with `id <your user>` (often `uid=1026`, `gid=100`) and add `user: "1026:100"` with your values to the compose file in step 4. The folder then needs no change.

## 3. Find the router's IP address

Docker on DSM usually cannot resolve `speedport.ip`; only the Speedport's own DNS knows that name. Use the IP address instead.

Control Panel → Network → General → **Default gateway**. That is the Speedport; its default is `192.168.2.1`.

## 4. Create the project

Container Manager → Project → Create:

- Project name: `wanview`
- Path: `docker/wanview`
- Source: **Create docker-compose.yml**, paste:

```yaml
services:
  wanview:
    image: ghcr.io/markuspabst/wanview:1.7
    container_name: wanview
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      ROUTER_URL: https://192.168.2.1   # your router's IP from step 3
      STATUS_INTERVAL: 30
      DETAILS_INTERVAL: 300
      SPEEDTEST_INTERVAL: 0
      LOG_LEVEL: INFO
    volumes:
      - ./data:/data
    # user: "1026:100"     # option C of step 2
    # network_mode: host   # see "Optional settings"
```

Next → skip the Web Station portal settings → Next → Done. Container Manager pulls the image and starts the container.

`:1.7` receives fixes within version 1.7; `:latest` follows every change. Passwords do not belong in this file: both are entered in the dashboard.

## 5. First start

1. Container Manager → Container → `wanview` → Log. Expected: `Application startup complete` and `Identified router: Speedport Smart 4`, no warnings.
2. Open `http://<NAS IP>:8080` **right away** and choose the dashboard password (enter it twice) or "Keep open". Whoever opens a new installation first chooses. The browser then asks for a login: any user name, your password.
3. The "New router" card appears: enter the device password and "Set up" (or set up without password: then only status values are read).

The router's address can also be changed later in the dashboard: Settings → Router address. That setting takes precedence over `ROUTER_URL`.

## Updating

Project → `wanview` → Action → **Build**: pulls the current image of the configured tag and restarts the container. For a new minor version (e.g. 1.8), change the tag in the project's YAML first (Project → `wanview`, edit the compose file there). The data folder is kept.

## Optional settings

| Setting | Effect |
|---|---|
| `SPEEDTEST_INTERVAL: 60` | Automatic speed test every 60 minutes (30–1440; 0 = off). Each test loads the line for about 20 s. |
| `network_mode: host` | The speed test can tell whether the NAS is wired or on Wi-Fi. The `ports` mapping is then ignored; port 8080 must be free on the NAS. |
| `"8090:8080"` under `ports` | Dashboard on port 8090 when 8080 is taken. |
| `LOG_LEVEL: DEBUG` | More detail in the log when looking for a problem. |

## Troubleshooting

Logs: Container Manager → Container → `wanview` → Log. Router problems are also shown at the top of the dashboard.

| Message or symptom | Cause and fix |
|---|---|
| `Cannot write to /data…: permission denied`, container restarts | Step 2 missing or not effective. Run it again and check the path (volume number). |
| `Cannot resolve speedport.ip` | Step 3: use the router's IP address (`ROUTER_URL` or Settings → Router address). |
| `… did not answer` / `No route to …` | Wrong IP address, or the NAS is not in the Speedport's network. |
| `… refused the connection on port …` | Something answers at that address, but no web server on that port: check the address and `https://`. |
| `TLS connection to … failed` | The address is not a Speedport, or `http://` instead of `https://`. |
| Dashboard not reachable | Check that the container is running; DSM firewall (Control Panel → Security → Firewall) may need a rule for port 8080. |
| Dashboard password forgotten | Delete `docker/wanview/data/dashboard.json` in File Station and restart the container; the next open asks again. |
| `DASHBOARD_PASSWORD is no longer used` | Remove that line from the compose file. |
| `Unsupported router` in the dashboard | WanView only supports the Speedport Smart 4. |

## Security

- The dashboard login is sent unencrypted over HTTP: fine in the home network. For access from outside, use DSM's reverse proxy with HTTPS (Control Panel → Login Portal → Advanced → Reverse Proxy) instead of forwarding port 8080 on the router.
- `data/secrets.json` holds the router's device password in plain text (file mode 600). Do not share `docker/wanview` with other users, and keep it out of unencrypted or public backups.
