"""Fake router for monitor and web tests: no network."""

import asyncio
import json
import time

from wanview.speedport.engineer_page import EngineerPage
from wanview.speedport.varlist import VarList
from wanview.speedtest import Measurement


LAN_STEP_BYTES = 10_000_000
# Length of each fake throughput phase: one LAN_STEP_BYTES on the receiver port per phase is 50 Mbit/s.
PHASE_SECONDS = 1.6
# Real duration of a fake phase for the rate trace (the reported duration stays PHASE_SECONDS).
TRACE_PHASE_SECONDS = 0.02


class FakeTester:
    """Speed test without network: fixed results, phase hooks called like the real one."""

    def __init__(self, fail: Exception | None = None) -> None:
        self._fail = fail
        # Router whose receiver port carries LAN_STEP_BYTES during each phase, if set.
        self.router: "FakeRouter | None" = None

    async def run(self, around_phase=None):  # type: ignore[no-untyped-def]
        if self._fail:
            raise self._fail
        traces = []
        for phase in ("download", "upload"):
            if around_phase:
                await around_phase(phase, "start")
            # Real (short) time passes, so router readings fall into the phase like on a real test.
            start = time.monotonic()
            await asyncio.sleep(TRACE_PHASE_SECONDS)
            if self.router:
                self.router.lan_bytes += LAN_STEP_BYTES
            traces.append(((start, 0), (time.monotonic(), LAN_STEP_BYTES)))
            if around_phase:
                await around_phase(phase, "end")
        return Measurement(latency_ms=20.0, down_bps=100_000_000, up_bps=40_000_000,
                           down_seconds=PHASE_SECONDS, up_seconds=PHASE_SECONDS,
                           down_trace=traces[0], up_trace=traces[1])


class FakeRouter:
    """Records which passwords would have been sent and which pages were read."""

    def __init__(self, serial: str = "SN-A", receiver: bool = False, unreachable: Exception | None = None) -> None:
        self.serial = serial
        self.receiver = receiver
        # Raised by every public request, like a failed connection.
        self.unreachable = unreachable
        self.online_since = "2026-11-24 10:00:00"
        self.dsl_state = "0x801 (showtime)"
        self.engineer_reads: list[tuple[str | None, str]] = []
        # Receiver port byte counters, moved by FakeTester during its phases.
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
        if self._router.unreachable:
            raise self._router.unreachable
        return VarList.from_json(json.dumps([
            {"varid": "title", "varvalue": "Speedport Smart 4 - Konfigurationsprogramm"},
            {"varid": "serial_number", "varvalue": self._router.serial},
            {"varid": "onlinestatus", "varvalue": "online"},
            {"varid": "inet_uptime", "varvalue": self._router.online_since},
            {"varid": "use_lte", "varvalue": "1" if self._router.receiver else "0"},
            {"varid": "ex5g_model_name", "varvalue": "5G-EmpfaengerSE" if self._router.receiver else ""},
        ]))

    async def fetch_start_page(self) -> str:
        if self._router.unreachable:
            raise self._router.unreachable
        return "<title>Speedport</title>"

    async def fetch_engineer_page(self, page: str) -> EngineerPage:
        self._router.engineer_reads.append((self._password, page))
        if page == "interfaces_hidden_lan.stm":
            bytes_ = self._router.lan_bytes
            return EngineerPage(f"var max_interface = 1;\ninte_face_rx[0][1] = {bytes_};\ninte_face_tx[0][1] = {bytes_};")
        return EngineerPage(f'var dsl_opmode = "VDSL2 Annex B";\nvar dsl_state = "{self._router.dsl_state}";\n'
                            'downstream_status[2] = " 8.2 dB";')

    async def logout(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def reboot(self) -> None:
        pass
