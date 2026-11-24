"""HTTP API and static dashboard.

    GET  /                       dashboard (static/index.html)
    GET  /api/state              current readings; keeps live LAN traffic running
    GET  /api/history?range=day  logged history (day | week)
    POST /api/pause | /api/resume | /api/restart-router
    POST /api/speedtest          starts a speed test (409 while one runs or monitoring is paused)
    PUT  /api/password           {"password": "…"} for the identified router (sets it up)
    DELETE /api/password
    PUT  /api/routers/{serial}   {"choice": "setUp" | "ignored"}
    DELETE /api/routers/{serial}
    PUT  /api/settings           {"routerUrl", "statusInterval", "detailsInterval", "speedtestInterval"}

Endpoints are async: the monitor must only be used from the event loop.

If a dashboard password is configured, every route requires HTTP basic auth
(any user name). Without one the dashboard is open: meant for a trusted LAN.
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from routerlink.app.data_log import DataLog
from routerlink.app.monitor import Monitor
from routerlink.app.store import (
    DETAILS_INTERVAL_RANGE,
    SPEEDTEST_INTERVAL_RANGE,
    SPEEDTEST_OFF,
    STATUS_INTERVAL_RANGE,
    Choice,
    Settings,
)
from routerlink.web.state import history, state

_STATIC = Path(__file__).parent / "static"
_ALLOWED_SCHEMES = ("https", "http")
_MAX_PASSWORD_LENGTH = 256
# Browsers revalidate dashboard files (ETag) on every load, so updates apply at once.
_NO_CACHE = {"Cache-Control": "no-cache"}


class _RevalidatedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers.update(_NO_CACHE)
        return response


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LENGTH)


class ChoiceBody(BaseModel):
    choice: Choice


class SettingsBody(BaseModel):
    routerUrl: str = Field(max_length=200)
    statusInterval: int = Field(ge=STATUS_INTERVAL_RANGE.start, le=STATUS_INTERVAL_RANGE.stop - 1)
    detailsInterval: int = Field(ge=DETAILS_INTERVAL_RANGE.start, le=DETAILS_INTERVAL_RANGE.stop - 1)
    speedtestInterval: int = SPEEDTEST_OFF
    """Minutes; 0 = off."""


def create_app(monitor: Monitor, data_log: DataLog, dashboard_password: str | None) -> FastAPI:
    basic = HTTPBasic(auto_error=False)

    def authorize(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> None:
        if not dashboard_password:
            return
        given = credentials.password.encode() if credentials else b""
        if not secrets.compare_digest(given, dashboard_password.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required",
                                headers={"WWW-Authenticate": 'Basic realm="Routerlink"'})

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        monitor.start()
        yield
        await monitor.stop()

    app = FastAPI(title="Routerlink", lifespan=lifespan, dependencies=[Depends(authorize)],
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", _RevalidatedStaticFiles(directory=_STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html", headers=_NO_CACHE)

    @app.get("/api/state")
    async def get_state() -> dict:
        # An open dashboard polls this; live LAN traffic runs only meanwhile.
        monitor.keep_traffic_alive()
        return state(monitor)

    @app.get("/api/history")
    async def get_history(range_name: Annotated[str, Query(alias="range", pattern="^(day|week)$")] = "day") -> dict:
        return history(data_log, range_name)

    @app.post("/api/pause", status_code=status.HTTP_204_NO_CONTENT)
    async def pause() -> None:
        monitor.pause()

    @app.post("/api/resume", status_code=status.HTTP_204_NO_CONTENT)
    async def resume() -> None:
        monitor.resume()

    @app.post("/api/speedtest", status_code=status.HTTP_202_ACCEPTED)
    async def start_speedtest() -> None:
        if not monitor.start_speedtest():
            raise HTTPException(status.HTTP_409_CONFLICT, "A speed test is running, or monitoring is paused.")

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

    @app.put("/api/settings", status_code=status.HTTP_204_NO_CONTENT)
    async def update_settings(body: SettingsBody) -> None:
        parts = urlsplit(body.routerUrl)
        if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname or parts.path not in ("", "/"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Router address must be like https://speedport.ip")
        if body.speedtestInterval != SPEEDTEST_OFF and body.speedtestInterval not in SPEEDTEST_INTERVAL_RANGE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Speed test interval: 0 (off) or 30–1440 minutes.")
        monitor.update_settings(Settings(f"{parts.scheme}://{parts.netloc}", body.statusInterval, body.detailsInterval,
                                         body.speedtestInterval))

    return app
