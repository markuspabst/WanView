"""Talks to the Speedport Smart web UI the same way the browser does.

Login flow (from `js/pages/login.js`):

    GET  /html/login/login.html          -> _httoken (CSRF token in page source)
    POST /data/Login.json  [default key] getChallenge=1&httoken=…  -> challenge (64 hex)
    POST /data/Login.json  [challenge]   password=sha256(challenge:pw)&showpw=0&httoken=…
    GET  /engineer/html/<page>.stm      HTML with values as JS variables, session cookie required

The router allows one session at a time: callers log out after use so the web
UI stays usable from a browser.

The router serves a self-signed certificate. This client only ever connects to
the configured router address, so certificate verification is disabled for it.

Thread-safety: use from one event loop; requests are serialized by a lock.
"""

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass

import httpx

from routerlink.speedport.cipher import DEFAULT_KEY_HEX, AuthenticationFailed, SpeedportCipher
from routerlink.speedport.engineer_page import EngineerPage
from routerlink.speedport.errors import (
    HTTPStatusError,
    LoginLocked,
    LoginRejected,
    MalformedResponse,
    OtherUserLoggedIn,
    PasswordRequired,
    SessionExpired,
    SpeedportError,
)
from routerlink.speedport.varlist import VarList

DEFAULT_BASE_URL = "https://speedport.ip"
# Time the web UI waits after a restart (`routerConfig.hardware.rebootTime` in `js/global.js`).
REBOOT_SECONDS = 180

_LOGIN_PAGE = "/html/login/login.html"
_LOGIN = "/data/Login.json"
_REBOOT = "/data/Reboot.json"
_ENGINEER_PAGES = "/engineer/html"
_SESSION_TIMEOUT_PAGE = "wait0.stm"
_LOGIN_SUCCESS = "success"
_HTTP_OK = 200
_REQUEST_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "Routerlink"
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_HTTOKEN = re.compile(r"_httoken\s*=\s*(\d+)")


@dataclass(frozen=True)
class Page:
    body: str
    final_path: str


class SpeedportClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, password: str | None = None) -> None:
        self._password = password
        self._http = httpx.AsyncClient(
            base_url=base_url,
            verify=False,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        self._session_cipher: SpeedportCipher | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def has_password(self) -> bool:
        return bool(self._password)

    async def fetch_public(self, name: str) -> VarList:
        """A data page readable without login (e.g. `Status`). Does not disturb browser sessions."""
        async with self._lock:
            return await self._get_data(name, SpeedportCipher(DEFAULT_KEY_HEX))

    async def fetch_start_page(self) -> str:
        """HTML of `/`, used to name devices that are no Speedport Smart."""
        async with self._lock:
            return (await self._get("/")).body

    async def fetch_engineer_page(self, page: str) -> EngineerPage:
        """An engineer menu page, logging in first or again when the session expired."""
        async with self._lock:
            if self._session_cipher is None:
                await self._login()
            try:
                return await self._fetch_engineer_page_with_session(page)
            except SessionExpired:
                await self._login()
                return await self._fetch_engineer_page_with_session(page)

    async def reboot(self) -> None:
        """Restarts the router like Settings → Restart in the web UI
        (`js/pages/settings/restart.js`: POST `Reboot.json`, `reboot_device=true`).
        Logs in if needed; the session ends with the restart."""
        async with self._lock:
            if self._session_cipher is None:
                await self._login()
            cipher = self._session_cipher
            if cipher is None:
                raise SessionExpired()
            token = await self._fetch_httoken()
            await self._post(_REBOOT, {"reboot_device": "true", "httoken": token}, cipher)
            self._end_session()

    async def logout(self) -> None:
        """Ends the router session so the web UI can be used from a browser again.
        Best effort: a failed logout only delays the router's own session timeout."""
        async with self._lock:
            cipher = self._session_cipher
            self._end_session()
            if cipher is None:
                return
            try:
                token = await self._fetch_httoken()
                await self._post(_LOGIN, {"logout": "byby", "httoken": token}, cipher)
            except (httpx.HTTPError, SpeedportError):
                return

    def _end_session(self) -> None:
        self._session_cipher = None
        self._http.cookies.clear()

    async def _login(self) -> None:
        self._end_session()
        if not self._password:
            raise PasswordRequired()
        token = await self._fetch_httoken()

        challenge_response = await self._post(
            _LOGIN, {"getChallenge": "1", "httoken": token}, SpeedportCipher(DEFAULT_KEY_HEX)
        )
        challenge = challenge_response.string("challenge")
        if not challenge:
            raise MalformedResponse("no login challenge")

        cipher = SpeedportCipher(challenge)
        password_hash = hashlib.sha256(f"{challenge}:{self._password}".encode()).hexdigest()
        response = await self._post(
            _LOGIN, {"password": password_hash, "showpw": "0", "httoken": token}, cipher
        )
        if response.string("login") != _LOGIN_SUCCESS:
            raise _login_failure(response)
        self._session_cipher = cipher

    async def _fetch_engineer_page_with_session(self, page: str) -> EngineerPage:
        """Without a valid session the router redirects engineer pages to `wait0.stm`."""
        if self._session_cipher is None:
            raise SessionExpired()
        result = await self._get(f"{_ENGINEER_PAGES}/{page}")
        if result.final_path.rsplit("/", 1)[-1] == _SESSION_TIMEOUT_PAGE:
            raise SessionExpired()
        return EngineerPage(result.body)

    async def _get_data(self, name: str, cipher: SpeedportCipher) -> VarList:
        # Same cache busting as the web UI ($.loadJSON).
        path = f"/data/{name}.json?_time={int(time.time() * 1000)}"
        return _decode((await self._get(path)).body, cipher)

    async def _fetch_httoken(self) -> str:
        match = _HTTOKEN.search((await self._get(_LOGIN_PAGE)).body)
        if match is None:
            raise MalformedResponse("no CSRF token on login page")
        return match.group(1)

    async def _post(self, path: str, form: dict[str, str], cipher: SpeedportCipher) -> VarList:
        body = cipher.encrypt(_form_encode(form))
        response = await self._http.post(path, content=body, headers={"Content-Type": _FORM_CONTENT_TYPE})
        _check_status(response, path)
        return _decode(response.text, cipher)

    async def _get(self, path: str) -> Page:
        response = await self._http.get(path)
        _check_status(response, path)
        return Page(body=response.text, final_path=response.url.path)


def _check_status(response: httpx.Response, path: str) -> None:
    if response.status_code != _HTTP_OK:
        raise HTTPStatusError(response.status_code, path.split("?", 1)[0])


def _decode(body: str, cipher: SpeedportCipher) -> VarList:
    """Responses are plain JSON or hex AES-CCM under the session or the default key
    (same fallback as `decryptccm`). An HTML page means the session is gone."""
    try:
        return VarList.from_json(body)
    except MalformedResponse:
        pass
    if "<html" in body or "<!DOCTYPE" in body:
        raise SessionExpired()
    for candidate in (cipher, SpeedportCipher(DEFAULT_KEY_HEX)):
        try:
            return VarList.from_json(candidate.decrypt(body))
        except AuthenticationFailed:
            continue
    raise SessionExpired()


def _login_failure(response: VarList) -> Exception:
    other = response.string("login_other")
    if other:
        return OtherUserLoggedIn(other)
    locked = response.string("login_locked")
    if locked and locked.isdigit() and int(locked) > 0:
        return LoginLocked(int(locked))
    return LoginRejected()


def _form_encode(form: dict[str, str]) -> str:
    """Keys sorted; everything but ASCII letters and digits percent-encoded (as the Swift client)."""
    return "&".join(f"{_percent(key)}={_percent(value)}" for key, value in sorted(form.items()))


def _percent(value: str) -> str:
    return "".join(char if char.isascii() and char.isalnum() else "".join(f"%{byte:02X}" for byte in char.encode())
                   for char in value)
