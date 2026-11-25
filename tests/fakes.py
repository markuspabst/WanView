"""Fake router for monitor and web tests: no network."""

import json

from wanview.speedport.engineer_page import EngineerPage
from wanview.speedport.varlist import VarList
from wanview.speedtest import Measurement


LAN_STEP_BYTES = 10_000_000


class FakeTester:
    """Speed test without network: fixed results, phase hooks called like the real one."""

    def __init__(self, fail: Exception | None = None) -> None:
        self._fail = fail

    async def run(self, around_phase=None):  # type: ignore[no-untyped-def]
        if self._fail:
            raise self._fail
        for phase in ("download", "upload"):
            if around_phase:
                await around_phase(phase, "start")
                await around_phase(phase, "end")
        return Measurement(latency_ms=20.0, down_bps=100_000_000, up_bps=40_000_000)


class FakeRouter:
    """Records which passwords would have been sent and which pages were read."""

    def __init__(self, serial: str = "SN-A", receiver: bool = False) -> None:
        self.serial = serial
        self.receiver = receiver
        self.engineer_reads: list[tuple[str | None, str]] = []
        # Receiver port byte counters; each LAN page read adds LAN_STEP_BYTES.
        self.lan_bytes = 0

    def client(self, base_url: str, password: str | None) -> "FakeClient":
        return FakeClient(self, password)


class FakeClient:
    def __init__(self, router: FakeRouter, password: str | None) -> None:
        self._router = router
        self._password = password

    @property
    def has_password(self) -> bool:
        return bool(self._password)

    async def fetch_public(self, name: str) -> VarList:
        return VarList.from_json(json.dumps([
            {"varid": "title", "varvalue": "Speedport Smart 4 - Konfigurationsprogramm"},
            {"varid": "serial_number", "varvalue": self._router.serial},
            {"varid": "onlinestatus", "varvalue": "online"},
            {"varid": "use_lte", "varvalue": "1" if self._router.receiver else "0"},
            {"varid": "ex5g_model_name", "varvalue": "5G-EmpfaengerSE" if self._router.receiver else ""},
        ]))

    async def fetch_start_page(self) -> str:
        return "<title>Speedport</title>"

    async def fetch_engineer_page(self, page: str) -> EngineerPage:
        self._router.engineer_reads.append((self._password, page))
        if page == "interfaces_hidden_lan.stm":
            self._router.lan_bytes += LAN_STEP_BYTES
            bytes_ = self._router.lan_bytes
            return EngineerPage(f"var max_interface = 1;\ninte_face_rx[0][1] = {bytes_};\ninte_face_tx[0][1] = {bytes_};")
        return EngineerPage('downstream_status[2] = " 8.2 dB";')

    async def logout(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def reboot(self) -> None:
        pass
