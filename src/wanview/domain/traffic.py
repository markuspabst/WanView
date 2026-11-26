"""LAN byte counters, rates, the hybrid (DSL + 5G) split and the bonding page.

    internet ──DSL──▶ ┌──────────┐ ──LAN2..4──▶ wired clients
    5G receiver ─LAN1▶│ Speedport │ ──Wi-Fi────▶ (no counters)
                      └──────────┘

5G is measured on the receiver port (router RX = 5G download). DSL has no usable
byte counter, so it is estimated as wired client traffic minus 5G: too low while
Wi-Fi clients transfer, too high during local transfers between wired devices.
"""

from dataclasses import dataclass
from enum import StrEnum

from wanview.speedport.engineer_page import EngineerPage, integer, number

LAN_PAGE = "interfaces_hidden_lan.stm"
BONDING_PAGE = "hybrid_bonding.stm"
# Smart 4 port for external modems ("Link"/LAN1).
RECEIVER_PORT = 1

# Counter columns per port: packets, bytes, errors, dropped.
_BYTES_COLUMN = 1
# LAN2–4 byte counters are 32-bit and wrap (≈ every 6 min at 100 Mbit/s); LAN1 is 64-bit.
_COUNTER_MODULUS = 1 << 32
_BITS_PER_BYTE = 8
_LEG_UP = "Up"
# Without router readings a speed test trace has one point per this many seconds.
_TRACE_WINDOW_SECONDS = 1.0
# 5G counts as joined once it carries this share of a speed test's rate.
_ONSET_SHARE = 0.1


@dataclass(frozen=True)
class PortCounters:
    number: int
    """1-based, as labeled on the router ("LAN1")."""
    link_speed_mbps: int | None
    tx_bytes: int | None
    """Bytes sent by the router to the device (device download)."""
    rx_bytes: int | None
    """Bytes received by the router from the device (device upload)."""


@dataclass(frozen=True)
class LANCounters:
    """From `interfaces_hidden_lan.stm`. Wi-Fi is not included, and the WAN byte
    counter is stuck at 2^32-1 on this firmware: these are the only usable counters."""

    ports: tuple[PortCounters, ...]

    @classmethod
    def from_page(cls, page: EngineerPage) -> "LANCounters":
        count = integer(page.string("max_interface")) or 0
        return cls(tuple(
            PortCounters(
                number=index + 1,
                link_speed_mbps=integer(page.element("inte_face_speed", index)),
                tx_bytes=integer(page.element("inte_face_tx", index, _BYTES_COLUMN)),
                rx_bytes=integer(page.element("inte_face_rx", index, _BYTES_COLUMN)),
            )
            for index in range(count)
        ))

    def port(self, number: int) -> PortCounters | None:
        return next((port for port in self.ports if port.number == number), None)


def bits_per_second(before: int | None, after: int | None, seconds: float) -> int | None:
    """Rate between two byte-counter readings. A counter that went backwards is one
    32-bit wrap; backwards beyond 32 bits is a reset (router reboot) and gives None."""
    if before is None or after is None or seconds <= 0:
        return None
    if after < before and before >= _COUNTER_MODULUS:
        return None
    delta = after - before if after >= before else _COUNTER_MODULUS - before + after
    return int(delta * _BITS_PER_BYTE / seconds)


@dataclass(frozen=True)
class PortRate:
    number: int
    down_bps: int | None
    """Device download (router TX)."""
    up_bps: int | None
    """Device upload (router RX)."""


@dataclass(frozen=True)
class LANTrafficRate:
    ports: tuple[PortRate, ...]

    @classmethod
    def between(cls, old: LANCounters, new: LANCounters, seconds: float) -> "LANTrafficRate | None":
        if seconds <= 0:
            return None
        rates = []
        for port in new.ports:
            previous = old.port(port.number)
            rates.append(PortRate(
                number=port.number,
                down_bps=bits_per_second(previous and previous.tx_bytes, port.tx_bytes, seconds),
                up_bps=bits_per_second(previous and previous.rx_bytes, port.rx_bytes, seconds),
            ))
        return cls(tuple(rates))


@dataclass(frozen=True)
class HybridTrafficRate:
    mobile_down_bps: int | None
    mobile_up_bps: int | None
    client_ports: tuple[PortRate, ...]
    """Client ports only (receiver port excluded)."""

    @classmethod
    def from_lan(cls, lan: LANTrafficRate, receiver_port: int = RECEIVER_PORT) -> "HybridTrafficRate":
        receiver = next((port for port in lan.ports if port.number == receiver_port), None)
        return cls(
            # Seen from the router: what it receives from the 5G receiver is 5G download.
            mobile_down_bps=receiver.up_bps if receiver else None,
            mobile_up_bps=receiver.down_bps if receiver else None,
            client_ports=tuple(port for port in lan.ports if port.number != receiver_port),
        )

    @property
    def client_down_bps(self) -> int:
        return sum(port.down_bps or 0 for port in self.client_ports)

    @property
    def client_up_bps(self) -> int:
        return sum(port.up_bps or 0 for port in self.client_ports)

    @property
    def dsl_down_estimate_bps(self) -> int | None:
        if self.mobile_down_bps is None:
            return None
        return max(0, self.client_down_bps - self.mobile_down_bps)

    @property
    def dsl_up_estimate_bps(self) -> int | None:
        if self.mobile_up_bps is None:
            return None
        return max(0, self.client_up_bps - self.mobile_up_bps)


@dataclass(frozen=True)
class BondingStatus:
    """From `hybrid_bonding.stm`: traffic fills DSL up to the overflow bandwidths, only
    the excess goes over 5G. Addresses, session ID and magic number are not parsed."""

    dsl_leg_up: bool
    mobile_leg_up: bool
    dsl_down_overflow_kbps: int | None
    dsl_up_overflow_kbps: int | None

    @classmethod
    def from_page(cls, page: EngineerPage) -> "BondingStatus":
        return cls(
            dsl_leg_up=page.string("DSL_Status") == _LEG_UP,
            mobile_leg_up=page.string("LTE_Status") == _LEG_UP,
            dsl_down_overflow_kbps=_kbps(page.string("DSL_DBW")),
            dsl_up_overflow_kbps=_kbps(page.string("DSL_UBW")),
        )


def _kbps(raw: str | None) -> int | None:
    value = number(raw)
    return int(value) if value is not None else None


class Direction(StrEnum):
    DOWN = "down"
    UP = "up"


@dataclass(frozen=True)
class TracePoint:
    """Rates in one window of a speed test phase."""

    offset_seconds: float
    """Middle of the window, from the phase start."""
    total_bps: int
    mobile_bps: int | None
    """5G part from the receiver port; None without router readings."""


def _bytes_at(byte_trace: tuple[tuple[float, int], ...], moment: float) -> float:
    """Bytes moved by `moment`, interpolated between the trace's samples."""
    if moment <= byte_trace[0][0]:
        return byte_trace[0][1]
    for (t0, b0), (t1, b1) in zip(byte_trace, byte_trace[1:]):
        if moment <= t1:
            return b0 if t1 == t0 else b0 + (b1 - b0) * (moment - t0) / (t1 - t0)
    return byte_trace[-1][1]


def _mobile_bytes(before: LANCounters, after: LANCounters, direction: Direction,
                  receiver_port: int) -> int | None:
    old, new = before.port(receiver_port), after.port(receiver_port)
    if old is None or new is None:
        return None
    # Seen from the router: 5G download arrives on the receiver port (RX).
    first, last = (old.rx_bytes, new.rx_bytes) if direction == Direction.DOWN else (old.tx_bytes, new.tx_bytes)
    if first is None or last is None or last < first:
        return None
    return last - first


def phase_trace(byte_trace: tuple[tuple[float, int], ...], readings: list[tuple[float, LANCounters]],
                direction: Direction, receiver_port: int = RECEIVER_PORT) -> tuple[TracePoint, ...]:
    """Rates over one speed test phase, so the moment 5G joins shows as a jump.

    `byte_trace`: (monotonic time, bytes moved so far) from the phase start to its end.
    `readings`: router counters, each stamped with the middle of its request. The
    windows run between consecutive readings, clipped to the phase; 5G bytes in a
    window's part outside the phase are counted to the phase (the test's traffic
    ends with it). Without readings the windows are _TRACE_WINDOW_SECONDS long.
    """
    start, end = byte_trace[0][0], byte_trace[-1][0]
    if len(readings) >= 2:
        windows = [(max(t0, start), min(t1, end), _mobile_bytes(c0, c1, direction, receiver_port))
                   for (t0, c0), (t1, c1) in zip(readings, readings[1:])]
    else:
        edges = [start]
        while edges[-1] + _TRACE_WINDOW_SECONDS < end:
            edges.append(edges[-1] + _TRACE_WINDOW_SECONDS)
        edges.append(end)
        windows = [(w0, w1, None) for w0, w1 in zip(edges, edges[1:])]

    points = []
    for w0, w1, mobile_bytes in windows:
        seconds = w1 - w0
        if seconds <= 0:
            continue
        total = round((_bytes_at(byte_trace, w1) - _bytes_at(byte_trace, w0)) * _BITS_PER_BYTE / seconds)
        mobile = None if mobile_bytes is None else min(round(mobile_bytes * _BITS_PER_BYTE / seconds), total)
        points.append(TracePoint(round((w0 + w1) / 2 - start, 3), total, mobile))
    return tuple(points)


def mobile_onset(points: tuple[TracePoint, ...]) -> float | None:
    """Offset of the first window in which 5G carries a noticeable share; None if never."""
    return next((p.offset_seconds for p in points
                 if p.mobile_bps is not None and p.total_bps > 0 and p.mobile_bps >= _ONSET_SHARE * p.total_bps),
                None)
