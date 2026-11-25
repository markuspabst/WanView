"""LAN byte counters, rates, the hybrid (DSL + 5G) split and the bonding page.

    internet ──DSL──▶ ┌──────────┐ ──LAN2..4──▶ wired clients
    5G receiver ─LAN1▶│ Speedport │ ──Wi-Fi────▶ (no counters)
                      └──────────┘

5G is measured on the receiver port (router RX = 5G download). DSL has no usable
byte counter, so it is estimated as wired client traffic minus 5G: too low while
Wi-Fi clients transfer, too high during local transfers between wired devices.
"""

from dataclasses import dataclass

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
