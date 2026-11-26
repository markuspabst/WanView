"""HTTP API and static dashboard.

    GET  /                       dashboard (static/index.html)
    GET  /api/state              current readings; keeps live LAN traffic running
    GET  /api/history?range=day  logged history (day | week | month)
    POST /api/pause | /api/resume | /api/restart-router
    POST /api/speedtest          starts a speed test (409 while one runs or monitoring is paused)
    PUT  /api/password           {"password": "…"} for the identified router (sets it up)
    DELETE /api/password
    PUT  /api/routers/{serial}   {"choice": "setUp" | "ignored"}
    DELETE /api/routers/{serial}
    PUT  /api/settings           {"routerUrl", "statusInterval", "detailsInterval", "speedtestInterval"}
    PUT  /api/dashboard-access   {"password": "…"} requires it from now on; {"password": null}: open
    PUT  /api/dns                {"servers": [IP | "router"], "hostnames": [...], "interval": minutes, 0 = off}
    POST /api/dnstest            starts a DNS test (409 while one runs, paused or no router identified)

Endpoints are async: the monitor must only be used from the event loop.

With a dashboard password (set on first open or in Settings), every route requires
HTTP basic auth (any user name). Otherwise the dashboard is open: meant for a trusted LAN.
"""

import ipaddress
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wanview.app.access import DashboardAccess
from wanview.app.monitor import Monitor
from wanview.app.store import (
    DETAILS_INTERVAL_RANGE,
    DNS_INTERVAL_RANGE,
    DNS_TEST_OFF,
    ROUTER_DNS,
    SPEEDTEST_INTERVAL_RANGE,
    SPEEDTEST_OFF,
    STATUS_INTERVAL_RANGE,
    Choice,
)
from wanview.web.state import history, state

_STATIC = Path(__file__).parent / "static"
_ALLOWED_SCHEMES = ("https", "http")
_MAX_PASSWORD_LENGTH = 256
_MAX_DNS_ENTRIES = 8
_MAX_HOSTNAME_LENGTH = 253
# RFC 1123 host names: dot-separated labels of letters, digits and inner hyphens.
_HOSTNAME = re.compile(r"(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?")
# Browsers revalidate dashboard files (ETag) on every load, so updates apply at once.
_NO_CACHE = {"Cache-Control": "no-cache"}


class _RevalidatedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers.update(_NO_CACHE)
        return response


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LENGTH)


class DashboardAccessBody(BaseModel):
    password: str | None = Field(min_length=1, max_length=_MAX_PASSWORD_LENGTH)
    """None: no login."""


class DnsBody(BaseModel):
    servers: list[str] = Field(min_length=1, max_length=_MAX_DNS_ENTRIES)
    hostnames: list[str] = Field(min_length=1, max_length=_MAX_DNS_ENTRIES)
    interval: int = DNS_TEST_OFF
    """Minutes; 0 = off."""


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


class ChoiceBody(BaseModel):
    choice: Choice


class SettingsBody(BaseModel):
    routerUrl: str = Field(max_length=200)
    statusInterval: int = Field(ge=STATUS_INTERVAL_RANGE.start, le=STATUS_INTERVAL_RANGE.stop - 1)
    detailsInterval: int = Field(ge=DETAILS_INTERVAL_RANGE.start, le=DETAILS_INTERVAL_RANGE.stop - 1)
    speedtestInterval: int = SPEEDTEST_OFF
    """Minutes; 0 = off."""


def create_app(monitor: Monitor, access: DashboardAccess) -> FastAPI:
    basic = HTTPBasic(auto_error=False)

    def authorize(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
        if not access.allows(credentials.password if credentials else None):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required",
                                headers={"WWW-Authenticate": 'Basic realm="WanView"'})

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        monitor.start()
        yield
        await monitor.stop()

    app = FastAPI(title="WanView", lifespan=lifespan, dependencies=[Depends(authorize)],
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", _RevalidatedStaticFiles(directory=_STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html", headers=_NO_CACHE)

    @app.get("/api/state")
    async def get_state() -> dict:
        # An open dashboard polls this; live LAN traffic runs only meanwhile.
        monitor.keep_traffic_alive()
        return {**state(monitor), "dashboardAccess": access.mode}

    @app.get("/api/history")
    async def get_history(range_name: Annotated[str, Query(alias="range", pattern="^(day|week|month)$")] = "day") -> dict:
        return history(monitor.data_log, range_name, monitor.normal_sync)

    @app.post("/api/pause", status_code=status.HTTP_204_NO_CONTENT)
    async def pause() -> None:
        monitor.pause()

    @app.post("/api/resume", status_code=status.HTTP_204_NO_CONTENT)
    async def resume() -> None:
        monitor.resume()

    @app.post("/api/speedtest", status_code=status.HTTP_202_ACCEPTED)
    async def start_speedtest() -> None:
        if not monitor.start_speedtest():
            raise HTTPException(status.HTTP_409_CONFLICT, "A speed test is running, monitoring is paused, or no router is identified yet.")

    @app.post("/api/restart-router", status_code=status.HTTP_204_NO_CONTENT)
    async def restart_router() -> None:
        if not monitor.can_restart_router:
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a set-up router with device password can be restarted.")
        monitor.restart_router()

    @app.put("/api/password", status_code=status.HTTP_204_NO_CONTENT)
    async def save_password(body: PasswordBody) -> None:
        if monitor.serial is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No router identified yet.")
        monitor.save_password(body.password)

    @app.delete("/api/password", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_password() -> None:
        monitor.delete_password()

    @app.put("/api/routers/{serial}", status_code=status.HTTP_204_NO_CONTENT)
    async def set_choice(serial: str, body: ChoiceBody) -> None:
        if serial != monitor.serial and all(router.serial_number != serial for router in monitor.routers):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown router.")
        monitor.set_choice(serial, body.choice)

    @app.delete("/api/routers/{serial}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_router(serial: str) -> None:
        monitor.remove_router(serial)

    @app.put("/api/dashboard-access", status_code=status.HTTP_204_NO_CONTENT)
    async def set_dashboard_access(body: DashboardAccessBody) -> None:
        if body.password is None:
            access.set_open()
        else:
            access.set_password(body.password)

    @app.put("/api/dns", status_code=status.HTTP_204_NO_CONTENT)
    async def update_dns(body: DnsBody) -> None:
        if not all(server == ROUTER_DNS or _is_ip(server) for server in body.servers):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, 'DNS servers: IP addresses or "router".')
        if not all(len(name) <= _MAX_HOSTNAME_LENGTH and _HOSTNAME.fullmatch(name) for name in body.hostnames):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Test names must be host names like telekom.de.")
        if body.interval != DNS_TEST_OFF and body.interval not in DNS_INTERVAL_RANGE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "DNS test interval: 0 (off) or 5–1440 minutes.")
        monitor.update_settings(replace(monitor.settings, dns_servers=tuple(body.servers),
                                        dns_hostnames=tuple(body.hostnames), dns_interval=body.interval))

    @app.post("/api/dnstest", status_code=status.HTTP_202_ACCEPTED)
    async def start_dnstest() -> None:
        if not monitor.start_dns_test():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "A DNS test is running, monitoring is paused, or no router is identified yet.")

    @app.put("/api/settings", status_code=status.HTTP_204_NO_CONTENT)
    async def update_settings(body: SettingsBody) -> None:
        parts = urlsplit(body.routerUrl)
        if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname or parts.path not in ("", "/"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Router address must be like https://speedport.ip")
        if body.speedtestInterval != SPEEDTEST_OFF and body.speedtestInterval not in SPEEDTEST_INTERVAL_RANGE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Speed test interval: 0 (off) or 30–1440 minutes.")
        # replace(): the DNS settings are saved separately and stay as they are.
        monitor.update_settings(replace(monitor.settings, router_url=f"{parts.scheme}://{parts.netloc}",
                                        status_interval=body.statusInterval, details_interval=body.detailsInterval,
                                        speedtest_interval=body.speedtestInterval))

    return app
