"""Persistent state in the data volume: settings, known routers, router passwords.

    settings.json   router address and poll intervals (UI changes override env defaults)
    routers.json    routers the user set up or ignored, by serial number
    secrets.json    device passwords by serial number (file mode 600)
    dashboard.json  dashboard access chosen on first open (file mode 600)

A device password is only valid for one router, so it is stored per serial number
and only ever sent to the router reporting that serial number.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)

_SECRET_FILE_MODE = 0o600
_ACCESS_FILE = "dashboard.json"


class Choice(StrEnum):
    SET_UP = "setUp"
    IGNORED = "ignored"


@dataclass(frozen=True)
class KnownRouter:
    serial_number: str
    name: str
    added: str
    """ISO 8601 UTC."""
    choice: Choice


@dataclass(frozen=True)
class Settings:
    router_url: str
    status_interval: int
    details_interval: int
    speedtest_interval: int = 0
    """Minutes between automatic speed tests; 0 = off."""


@dataclass(frozen=True)
class StoredAccess:
    """Dashboard access; without a password hash the dashboard is open."""
    salt: str | None = None
    """Hex."""
    password_hash: str | None = None
    """Hex, scrypt."""


STATUS_INTERVAL_RANGE = range(10, 601)
DETAILS_INTERVAL_RANGE = range(60, 3601)
# Each test saturates the line for ~20 s, so automatic tests run at most every 30 min.
SPEEDTEST_INTERVAL_RANGE = range(30, 1441)
SPEEDTEST_OFF = 0


class Store:
    def __init__(self, folder: Path, defaults: Settings) -> None:
        self._folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._defaults = defaults

    # Settings

    def settings(self) -> Settings:
        stored = self._read_json("settings.json", {})
        return replace(self._defaults, **{key: value for key, value in stored.items()
                                          if key in Settings.__dataclass_fields__})

    def save_settings(self, settings: Settings) -> None:
        self._write_json("settings.json", asdict(settings))

    # Known routers

    def routers(self) -> list[KnownRouter]:
        routers = []
        for item in self._read_json("routers.json", []):
            try:
                routers.append(KnownRouter(item["serial_number"], item["name"], item["added"], Choice(item["choice"])))
            except (KeyError, ValueError, TypeError) as error:
                log.error("Skipping unreadable router entry: %s", error)
        return routers

    def save_routers(self, routers: list[KnownRouter]) -> None:
        self._write_json("routers.json", [asdict(router) for router in routers])

    @staticmethod
    def new_router(serial_number: str, name: str, choice: Choice) -> KnownRouter:
        return KnownRouter(serial_number, name, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), choice)

    # Passwords

    def password(self, serial_number: str) -> str | None:
        return self._read_json("secrets.json", {}).get(serial_number)

    def save_password(self, serial_number: str, password: str) -> None:
        secrets = self._read_json("secrets.json", {})
        secrets[serial_number] = password
        self._write_json("secrets.json", secrets, mode=_SECRET_FILE_MODE)

    def delete_password(self, serial_number: str) -> None:
        secrets = self._read_json("secrets.json", {})
        if secrets.pop(serial_number, None) is not None:
            self._write_json("secrets.json", secrets, mode=_SECRET_FILE_MODE)

    # Dashboard access

    def dashboard_access(self) -> StoredAccess | None:
        """None until chosen. An unreadable file raises instead of opening the dashboard."""
        path = self._folder / _ACCESS_FILE
        if not path.exists():
            return None
        try:
            return StoredAccess(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError) as error:
            raise ValueError(f"Unreadable {path}; delete it to choose the dashboard access again") from error

    def save_dashboard_access(self, access: StoredAccess) -> None:
        self._write_json(_ACCESS_FILE, asdict(access), mode=_SECRET_FILE_MODE)

    # Files

    def _read_json(self, name: str, default: dict | list) -> dict | list:
        path = self._folder / name
        if not path.exists():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            # Hand-edited or truncated file: keep running with defaults, but say so.
            log.error("Unreadable %s, using defaults: %s", path, error)
            return default
        return data if isinstance(data, type(default)) else default

    def _write_json(self, name: str, data: dict | list, mode: int | None = None) -> None:
        """Atomic replace, so a crash never leaves a half-written file."""
        path = self._folder / name
        temporary = path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(temporary, flags, mode if mode is not None else 0o644)
        if mode is not None:
            # os.open applies the mode only when it creates the file.
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, path)
