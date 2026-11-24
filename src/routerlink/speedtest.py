"""Internet speed test against Cloudflare's speed test endpoints (speed.cloudflare.com):

    GET  /__down?bytes=N   N bytes of payload
    POST /__up             payload is discarded

Latency is the median round trip of small requests. Download and upload each run
several parallel streams for a fixed time, like browser speed tests, because one
TCP stream rarely fills a fast line. Throughput counts bytes moved within that time.

Thread-safety: use from one event loop.
"""

import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

BASE_URL = "https://speed.cloudflare.com"
_LATENCY_PROBES = 8
_STREAMS = 4
_PHASE_SECONDS = 8.0
# Per request; streams repeat requests until the phase ends.
_DOWNLOAD_CHUNK_BYTES = 25_000_000
_UPLOAD_CHUNK_BYTES = 5_000_000
_READ_SIZE = 64 * 1024
_TIMEOUT_SECONDS = 30.0
_BITS_PER_BYTE = 8
_MS_PER_SECOND = 1000


@dataclass(frozen=True)
class Measurement:
    latency_ms: float
    down_bps: int
    up_bps: int


class SpeedTester:
    """`around_phase`, if given, is awaited before and after each throughput phase (e.g. to
    read router counters); it receives "download" or "upload" and "start" or "end"."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base_url = base_url

    async def run(self, around_phase: Callable[[str, str], Awaitable[None]] | None = None) -> Measurement:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=_TIMEOUT_SECONDS) as http:
            latency = await self._latency(http)
            down = await self._timed("download", around_phase, lambda deadline: self._download(http, deadline))
            up = await self._timed("upload", around_phase, lambda deadline: self._upload(http, deadline))
        return Measurement(latency_ms=latency, down_bps=down, up_bps=up)

    async def _latency(self, http: httpx.AsyncClient) -> float:
        rounds = []
        for _ in range(_LATENCY_PROBES):
            start = time.perf_counter()
            response = await http.get("/__down", params={"bytes": 0})
            response.raise_for_status()
            rounds.append((time.perf_counter() - start) * _MS_PER_SECOND)
        return statistics.median(rounds)

    async def _timed(self, name: str, around_phase: Callable[[str, str], Awaitable[None]] | None,
                     stream: Callable[[float], Awaitable[int]]) -> int:
        if around_phase:
            await around_phase(name, "start")
        start = time.perf_counter()
        deadline = start + _PHASE_SECONDS
        moved = sum(await asyncio.gather(*(stream(deadline) for _ in range(_STREAMS))))
        elapsed = time.perf_counter() - start
        if around_phase:
            await around_phase(name, "end")
        return int(moved * _BITS_PER_BYTE / elapsed)

    async def _download(self, http: httpx.AsyncClient, deadline: float) -> int:
        moved = 0
        while time.perf_counter() < deadline:
            async with http.stream("GET", "/__down", params={"bytes": _DOWNLOAD_CHUNK_BYTES}) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(_READ_SIZE):
                    moved += len(chunk)
                    if time.perf_counter() >= deadline:
                        return moved
        return moved

    async def _upload(self, http: httpx.AsyncClient, deadline: float) -> int:
        moved = 0
        payload = bytes(_UPLOAD_CHUNK_BYTES)
        while time.perf_counter() < deadline:
            response = await http.post("/__up", content=payload)
            response.raise_for_status()
            moved += len(payload)
        return moved
