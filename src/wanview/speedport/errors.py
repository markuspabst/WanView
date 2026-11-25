"""Errors raised by the Speedport client, and user-facing text for failed connections."""

import errno
import socket
import ssl
from urllib.parse import urlsplit

import httpx


class SpeedportError(Exception):
    """Base class; the message is shown to the user."""


class MalformedResponse(SpeedportError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Unexpected router response: {detail}.")


class HTTPStatusError(SpeedportError):
    def __init__(self, code: int, path: str) -> None:
        super().__init__(f"Router answered HTTP {code} for {path}.")
        self.code = code


class PasswordRequired(SpeedportError):
    def __init__(self) -> None:
        super().__init__("Device password required for this data.")


class LoginRejected(SpeedportError):
    def __init__(self) -> None:
        super().__init__("Login rejected: wrong device password.")


class LoginLocked(SpeedportError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"Login rejected; router blocks retries for {seconds} s. Check the device password.")
        self.seconds = seconds


class OtherUserLoggedIn(SpeedportError):
    def __init__(self, address: str) -> None:
        super().__init__(f"Another session is logged in from {address}. Log out there first.")


class SessionExpired(SpeedportError):
    def __init__(self) -> None:
        super().__init__("Router session expired.")


# Default address of a Speedport; suggested when its name does not resolve.
_SPEEDPORT_IP_URL = "https://192.168.2.1"
_CHANGE_ADDRESS = "Check the address in Settings → Router address (or ROUTER_URL)."
_UNREACHABLE = (errno.EHOSTUNREACH, errno.ENETUNREACH)
_DEFAULT_PORTS = {"https": 443, "http": 80}


def _causes(error: BaseException) -> list[BaseException]:
    """`error` and what it wraps; httpx wraps the socket error twice."""
    chain = []
    while error is not None and error not in chain:
        chain.append(error)
        error = error.__cause__ or error.__context__
    return chain


def connection_problem(error: Exception, base_url: str) -> str:
    """What went wrong with a router request and what to change, for log and dashboard.

    Example: "Cannot resolve speedport.ip: …" instead of "[Errno -2] Name or service not known".
    """
    if isinstance(error, SpeedportError):
        return str(error)

    parts = urlsplit(base_url)
    host = parts.hostname or base_url
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    causes = _causes(error)
    if any(isinstance(cause, socket.gaierror) for cause in causes):
        return (f"Cannot resolve {host}: the DNS server of this host does not know the name "
                f"(common in Docker). Use the router's IP address instead, e.g. {_SPEEDPORT_IP_URL}. "
                + _CHANGE_ADDRESS)
    if isinstance(error, httpx.ConnectTimeout):
        return f"{host} did not answer: wrong address, or this host is not in the router's network. " + _CHANGE_ADDRESS
    if any(isinstance(cause, ConnectionRefusedError) for cause in causes):
        return f"{host} refused the connection on port {port}: wrong address or port, or not a router. " + _CHANGE_ADDRESS
    if any(isinstance(cause, OSError) and cause.errno in _UNREACHABLE for cause in causes):
        return f"No route to {host}: this host is not in the router's network, or the address is wrong. " + _CHANGE_ADDRESS
    if any(isinstance(cause, ssl.SSLError) for cause in causes):
        return f"TLS connection to {host} failed ({error}): is it a Speedport? They answer on https. " + _CHANGE_ADDRESS
    if isinstance(error, httpx.TimeoutException):
        return f"{host} accepted the connection but did not answer in time; the router may be busy or restarting."
    return f"Request to {host} failed: {error}"
