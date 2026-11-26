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
_TOO_MANY_REQUESTS = 429
# Bytes moved so far are recorded this often, so the rate over time (e.g. when 5G joins) can be shown.
_TRACE_SECONDS = 0.25
# Used when a 429 carries no usable Retry-After.
_DEFAULT_RETRY_AFTER_SECONDS = 3600


class RateLimited(Exception):
    """Cloudflare refused the test (HTTP 429); retry after `retry_after_seconds`."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Cloudflare rate limit, retry in {retry_after_seconds // 60} min")
        self.retry_after_seconds = retry_after_seconds


def _check(response: httpx.Response) -> None:
    if response.status_code == _TOO_MANY_REQUESTS:
        retry_after = response.headers.get("Retry-After", "")
        raise RateLimited(int(retry_after) if retry_after.isdigit() else _DEFAULT_RETRY_AFTER_SECONDS)
    response.raise_for_status()


@dataclass(frozen=True)
class Measurement:
    latency_ms: float
    down_bps: int
    up_bps: int
    down_seconds: float
    """Duration of the download phase; rates of router counters read around it use this."""
    up_seconds: float
    down_trace: tuple[tuple[float, int], ...] = ()
    """(time.monotonic(), bytes moved so far) during the download phase, from its start."""
    up_trace: tuple[tuple[float, int], ...] = ()


class SpeedTester:
    """`around_phase`, if given, is awaited before and after each throughput phase (e.g. to
    read router counters); it receives "download" or "upload" and "start" or "end"."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base_url = base_url

    async def run(self, around_phase: Callable[[str, str], Awaitable[None]] | None = None) -> Measurement:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=_TIMEOUT_SECONDS) as http:
            latency = await self._latency(http)
            down, down_seconds, down_trace = await self._timed(
                "download", around_phase, lambda deadline, moved: self._download(http, deadline, moved))
            up, up_seconds, up_trace = await self._timed(
                "upload", around_phase, lambda deadline, moved: self._upload(http, deadline, moved))
        return Measurement(latency_ms=latency, down_bps=down, up_bps=up, down_seconds=down_seconds,
                           up_seconds=up_seconds, down_trace=down_trace, up_trace=up_trace)

    async def _latency(self, http: httpx.AsyncClient) -> float:
        rounds = []
        for _ in range(_LATENCY_PROBES):
            start = time.perf_counter()
            response = await http.get("/__down", params={"bytes": 0})
            _check(response)
            rounds.append((time.perf_counter() - start) * _MS_PER_SECOND)
        return statistics.median(rounds)

    async def _timed(self, name: str, around_phase: Callable[[str, str], Awaitable[None]] | None,
                     stream: Callable[[float, list[int]], Awaitable[None]]
                     ) -> tuple[int, float, tuple[tuple[float, int], ...]]:
        """Rate in bit/s, duration in seconds and byte trace of one phase. The streams add
        to one shared byte count, which a sampler records every _TRACE_SECONDS."""
        if around_phase:
            await around_phase(name, "start")
        moved = [0]
        trace = [(time.monotonic(), 0)]

        async def sample() -> None:
            while True:
                await asyncio.sleep(_TRACE_SECONDS)
                trace.append((time.monotonic(), moved[0]))

        start = time.perf_counter()
        deadline = start + _PHASE_SECONDS
        sampler = asyncio.create_task(sample())
        try:
            await asyncio.gather(*(stream(deadline, moved) for _ in range(_STREAMS)))
        finally:
            sampler.cancel()
        elapsed = time.perf_counter() - start
        trace.append((time.monotonic(), moved[0]))
        if around_phase:
            await around_phase(name, "end")
        return int(moved[0] * _BITS_PER_BYTE / elapsed), elapsed, tuple(trace)

    async def _download(self, http: httpx.AsyncClient, deadline: float, moved: list[int]) -> None:
        """Adds the bytes received to `moved[0]`, shared by all streams."""
        while time.perf_counter() < deadline:
            async with http.stream("GET", "/__down", params={"bytes": _DOWNLOAD_CHUNK_BYTES}) as response:
                _check(response)
                async for chunk in response.aiter_bytes(_READ_SIZE):
                    moved[0] += len(chunk)
                    if time.perf_counter() >= deadline:
                        return

    async def _upload(self, http: httpx.AsyncClient, deadline: float, moved: list[int]) -> None:
        payload = bytes(_UPLOAD_CHUNK_BYTES)
        while time.perf_counter() < deadline:
            response = await http.post("/__up", content=payload)
            _check(response)
            moved[0] += len(payload)
