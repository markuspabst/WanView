"""Dashboard access, chosen in the dashboard on first open and changeable in Settings.

    unset     nothing chosen yet: the dashboard is open and asks for a choice
    open      no login; meant for a trusted LAN
    password  HTTP basic auth, any user name

Only a salted scrypt hash is stored (dashboard.json). Forgotten password: delete
dashboard.json from the data volume and restart; the next open asks again.

Not thread-safe: use from the event loop only.
"""

import hashlib
import secrets
from enum import StrEnum

from wanview.app.store import Store, StoredAccess

_SALT_BYTES = 16
# scrypt cost (RFC 7914 interactive parameters): about 16 MB and tens of ms per check.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AccessMode(StrEnum):
    UNSET = "unset"
    OPEN = "open"
    PASSWORD = "password"


def _hash(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)


class DashboardAccess:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._stored = store.dashboard_access()
        # Last password that matched: the dashboard polls every few seconds, scrypt runs once.
        self._verified: bytes | None = None

    @property
    def mode(self) -> AccessMode:
        if self._stored is None:
            return AccessMode.UNSET
        return AccessMode.OPEN if self._stored.password_hash is None else AccessMode.PASSWORD

    def allows(self, password: str | None) -> bool:
        if self.mode != AccessMode.PASSWORD:
            return True
        if password is None:
            return False

        given = password.encode()
        if self._verified is not None and secrets.compare_digest(given, self._verified):
            return True
        expected = bytes.fromhex(self._stored.password_hash)
        if not secrets.compare_digest(_hash(given, bytes.fromhex(self._stored.salt)), expected):
            return False
        self._verified = given
        return True

    def set_password(self, password: str) -> None:
        salt = secrets.token_bytes(_SALT_BYTES)
        self._save(StoredAccess(salt.hex(), _hash(password.encode(), salt).hex()))

    def set_open(self) -> None:
        self._save(StoredAccess())

    def _save(self, stored: StoredAccess) -> None:
        self._store.save_dashboard_access(stored)
        self._stored = stored
        self._verified = None
