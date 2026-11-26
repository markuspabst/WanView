"""Persistent state in the data volume. Everything about a router is kept per serial number:

    settings.json   router address; default intervals for routers without own settings
    routers.json    routers the user set up or ignored, by serial number
    secrets.json    device passwords by serial number (file mode 600)
    dashboard.json  dashboard access chosen on first open (file mode 600)
    routers/<serial>/settings.json  this router's poll and speed test intervals
    routers/<serial>/logs/          this router's CSV logs (see data_log)

A device password is only valid for one router, so it is stored per serial number
and only ever sent to the router reporting that serial number.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)

_SECRET_FILE_MODE = 0o600
_ACCESS_FILE = "dashboard.json"
_SETTINGS_FILE = "settings.json"
_ROUTERS_FOLDER = "routers"
# Logs from before per-router storage; moved to the first router identified.
_LEGACY_LOGS_FOLDER = "logs"
_LOGS_FOLDER = "logs"
# The serial number comes from the router: only plain names become folder names.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HASHED_NAME_LENGTH = 16
# Settings that belong to a router; the address is needed to find one, so it is global.
_ROUTER_SETTINGS = ("status_interval", "details_interval", "speedtest_interval", "dns_servers", "dns_hostnames",
                    "dns_interval")
# Stored as JSON lists.
_TUPLE_SETTINGS = ("dns_servers", "dns_hostnames")


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


# DNS server entry meaning the router itself (its address from the router URL).
ROUTER_DNS = "router"
DEFAULT_DNS_SERVERS = (ROUTER_DNS, "1.1.1.1", "8.8.8.8", "9.9.9.9")
DEFAULT_DNS_HOSTNAMES = ("telekom.de", "google.com", "cloudflare.com")


@dataclass(frozen=True)
class Settings:
    router_url: str
    status_interval: int
    details_interval: int
    speedtest_interval: int = 0
    """Minutes between automatic speed tests; 0 = off."""
    dns_servers: tuple[str, ...] = DEFAULT_DNS_SERVERS
    """IP addresses or ROUTER_DNS."""
    dns_hostnames: tuple[str, ...] = DEFAULT_DNS_HOSTNAMES
    dns_interval: int = 0
    """Minutes between automatic DNS tests; 0 = off."""


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
DNS_INTERVAL_RANGE = range(5, 1441)
DNS_TEST_OFF = 0


class Store:
    def __init__(self, folder: Path, defaults: Settings) -> None:
        self._folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._defaults = defaults

    # Settings

    def settings(self, serial_number: str | None = None) -> Settings:
        """Global settings, with the router's own intervals if it has any."""
        stored = self._read_json(_SETTINGS_FILE, {})
        if serial_number is not None:
            stored |= self._read_json(self._router_settings_path(serial_number), {})
        values = {key: value for key, value in stored.items() if key in Settings.__dataclass_fields__}
        for key in _TUPLE_SETTINGS:
            if key in values:
                values[key] = tuple(values[key])
        return replace(self._defaults, **values)

    def save_settings(self, settings: Settings, serial_number: str | None = None) -> None:
        """The intervals go to the router's own settings when a router is given."""
        values = asdict(settings)
        for key in _TUPLE_SETTINGS:
            values[key] = list(values[key])
        if serial_number is None:
            self._write_json(_SETTINGS_FILE, values)
            return
        stored = self._read_json(_SETTINGS_FILE, {})
        stored["router_url"] = settings.router_url
        self._write_json(_SETTINGS_FILE, stored)
        self._write_json(self._router_settings_path(serial_number), {key: values[key] for key in _ROUTER_SETTINGS})

    # Per-router folders

    def router_folder(self, serial_number: str) -> Path:
        name = serial_number if _PLAIN_NAME.fullmatch(serial_number) else (
            "sn-" + hashlib.sha256(serial_number.encode()).hexdigest()[:_HASHED_NAME_LENGTH])
        folder = self._folder / _ROUTERS_FOLDER / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def router_logs_folder(self, serial_number: str) -> Path:
        return self.router_folder(serial_number) / _LOGS_FOLDER

    def adopt_legacy_logs(self, serial_number: str) -> int:
        """Moves logs from before per-router storage to this router; returns the number of files.
        A file the router already has is kept in the old folder."""
        legacy = self._folder / _LEGACY_LOGS_FOLDER
        if not legacy.is_dir():
            return 0
        target = self.router_logs_folder(serial_number)
        target.mkdir(parents=True, exist_ok=True)
        moved = 0
        for path in sorted(legacy.glob("*.csv")):
            if not (target / path.name).exists():
                path.rename(target / path.name)
                moved += 1
        if not any(legacy.iterdir()):
            legacy.rmdir()
        return moved

    def _router_settings_path(self, serial_number: str) -> str:
        return str((self.router_folder(serial_number) / _SETTINGS_FILE).relative_to(self._folder))

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
