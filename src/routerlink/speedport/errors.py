"""Errors raised by the Speedport client."""


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
