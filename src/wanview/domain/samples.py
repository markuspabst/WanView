"""Logged polls as CSV rows. Columns and formats are fixed: existing logs must stay readable.
Times are ISO 8601 UTC with seconds, e.g. `2026-11-23T18:53:20Z`."""

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from wanview.domain.line import DSLDetails, ReceiverStatus
from wanview.domain.status import LineStatus
from wanview.domain.traffic import RECEIVER_PORT, BondingStatus, Direction, LANCounters, TracePoint, bits_per_second

_SEPARATOR = ","
_REPLACEMENT = " "
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_YES = "1"
_NO = "0"
_SECONDS_PER_HOUR = 3600.0


def _time(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_TIME_FORMAT)


def _parse_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _field(value: int | float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        # No quoting: separators and line breaks in values are replaced.
        return value.replace(_SEPARATOR, _REPLACEMENT).replace("\n", _REPLACEMENT).replace("\r", _REPLACEMENT)
    return repr(float(value)) if isinstance(value, float) else str(value)


def _row(values: list[int | float | str | None]) -> str:
    return _SEPARATOR.join(_field(value) for value in values)


def _fields(row: str, count: int) -> list[str] | None:
    fields = row.split(_SEPARATOR)
    return fields if len(fields) == count else None


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class StatusSample:
    time: datetime
    online_status: str
    dsl_downstream: int | None
    dsl_upstream: int | None
    internet_download: int | None
    internet_upload: int | None
    nr_signal: float | None
    nr_band: str | None
    lte_signal: float | None
    lte_band: str | None

    CSV_HEADER = "time,online,dsl_down_bps,dsl_up_bps,inet_down_bps,inet_up_bps,nr_dbm,nr_band,lte_dbm,lte_band"
    _FIELD_COUNT = 10

    @classmethod
    def from_status(cls, time: datetime, status: LineStatus) -> "StatusSample":
        return cls(time, status.online_status, status.dsl_downstream, status.dsl_upstream,
                   status.internet_download, status.internet_upload, status.nr_signal, status.nr_band,
                   status.lte_signal, status.lte_band)

    @property
    def csv_row(self) -> str:
        return _row([_time(self.time), self.online_status, self.dsl_downstream, self.dsl_upstream,
                     self.internet_download, self.internet_upload, self.nr_signal, self.nr_band or "",
                     self.lte_signal, self.lte_band or ""])

    @classmethod
    def from_csv(cls, row: str) -> "StatusSample | None":
        fields = _fields(row, cls._FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        if fields is None or time is None:
            return None
        return cls(time, fields[1], _int(fields[2]), _int(fields[3]), _int(fields[4]), _int(fields[5]),
                   _float(fields[6]), fields[7] or None, _float(fields[8]), fields[9] or None)


# Fewer samples (about 50 min at 30 s) give no reliable normal rate.
MIN_SAMPLES_FOR_NORMAL_SYNC = 100


@dataclass(frozen=True)
class NormalSync:
    """The line's normal sync rate per direction: the median of logged readings. A DSL
    line resyncs at the same rate, so short drops do not move it. Sync 0 (link
    down) is an outage, not a rate, and is left out. None without enough readings."""

    down_bps: int | None
    up_bps: int | None

    @classmethod
    def from_samples(cls, samples: list[StatusSample]) -> "NormalSync":
        def median(values: list[int]) -> int | None:
            return statistics.median_low(values) if len(values) >= MIN_SAMPLES_FOR_NORMAL_SYNC else None

        return cls(median([s.dsl_downstream for s in samples if s.dsl_downstream]),
                   median([s.dsl_upstream for s in samples if s.dsl_upstream]))


@dataclass(frozen=True)
class LineSample:
    time: datetime
    snr_margin_down: float | None
    snr_margin_up: float | None
    attenuation_down: float | None
    attenuation_up: float | None
    attainable_down_kbps: int | None
    attainable_up_kbps: int | None
    crc_down: int | None
    crc_up: int | None
    fec_down: int | None
    fec_up: int | None
    nr_rsrp: float | None
    nr_rsrq: float | None
    lte_rsrp: float | None
    lte_rsrq: float | None

    CSV_HEADER = ("time,snr_down_db,snr_up_db,att_down_db,att_up_db,attain_down_kbps,attain_up_kbps,crc_down,crc_up,"
                  "fec_down,fec_up,nr_rsrp_dbm,nr_rsrq_db,lte_rsrp_dbm,lte_rsrq_db")
    _FIELD_COUNT = 15

    @classmethod
    def from_details(cls, time: datetime, dsl: DSLDetails, receiver: ReceiverStatus | None) -> "LineSample":
        down, up = dsl.downstream, dsl.upstream
        return cls(time, down.snr_margin_db, up.snr_margin_db, down.attenuation_db, up.attenuation_db,
                   down.attainable_rate_kbps, up.attainable_rate_kbps, down.crc_errors, up.crc_errors,
                   down.fec_errors, up.fec_errors,
                   receiver.nr.rsrp if receiver else None, receiver.nr.rsrq if receiver else None,
                   receiver.lte.rsrp if receiver else None, receiver.lte.rsrq if receiver else None)

    @property
    def csv_row(self) -> str:
        return _row([_time(self.time), self.snr_margin_down, self.snr_margin_up, self.attenuation_down,
                     self.attenuation_up, self.attainable_down_kbps, self.attainable_up_kbps, self.crc_down,
                     self.crc_up, self.fec_down, self.fec_up, self.nr_rsrp, self.nr_rsrq, self.lte_rsrp,
                     self.lte_rsrq])

    @classmethod
    def from_csv(cls, row: str) -> "LineSample | None":
        fields = _fields(row, cls._FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        if fields is None or time is None:
            return None
        return cls(time, _float(fields[1]), _float(fields[2]), _float(fields[3]), _float(fields[4]),
                   _int(fields[5]), _int(fields[6]), _int(fields[7]), _int(fields[8]), _int(fields[9]),
                   _int(fields[10]), _float(fields[11]), _float(fields[12]), _float(fields[13]),
                   _float(fields[14]))


@dataclass(frozen=True)
class ErrorRate:
    """Increase of a DSL error counter between two line samples, per hour; None per
    direction without both readings or when the counter went backwards (resync)."""

    down_per_hour: float | None
    up_per_hour: float | None

    @classmethod
    def crc(cls, previous: LineSample, current: LineSample) -> "ErrorRate | None":
        """Uncorrectable errors."""
        return cls._between(previous, current, (previous.crc_down, current.crc_down), (previous.crc_up, current.crc_up))

    @classmethod
    def fec(cls, previous: LineSample, current: LineSample) -> "ErrorRate | None":
        """Corrected errors: normal in operation, not rated."""
        return cls._between(previous, current, (previous.fec_down, current.fec_down), (previous.fec_up, current.fec_up))

    @classmethod
    def _between(cls, previous: LineSample, current: LineSample, down: tuple[int | None, int | None],
                 up: tuple[int | None, int | None]) -> "ErrorRate | None":
        seconds = (current.time - previous.time).total_seconds()
        if seconds <= 0:
            return None

        def per_hour(counts: tuple[int | None, int | None]) -> float | None:
            before, after = counts
            if before is None or after is None or after < before:
                return None
            return (after - before) * _SECONDS_PER_HOUR / seconds

        return cls(per_hour(down), per_hour(up))


@dataclass(frozen=True)
class OverflowSample:
    """One details poll of the DSL overflow to 5G. Stores the receiver port's
    cumulative byte counters, so the average 5G rate since the previous sample can
    be computed, also against the last logged sample after a restart."""

    time: datetime
    interval_seconds: float | None
    dsl_leg_up: bool
    mobile_leg_up: bool
    dsl_down_limit_kbps: int | None
    dsl_up_limit_kbps: int | None
    mobile_rx_bytes: int | None
    """Cumulative bytes the router received from the receiver (5G download)."""
    mobile_tx_bytes: int | None
    mobile_down_bps: int | None
    mobile_up_bps: int | None

    CSV_HEADER = ("time,interval_s,dsl_leg_up,mobile_leg_up,dsl_down_limit_kbps,dsl_up_limit_kbps,mobile_rx_bytes,"
                  "mobile_tx_bytes,mobile_down_avg_bps,mobile_up_avg_bps")
    _FIELD_COUNT = 10

    @classmethod
    def measure(cls, time: datetime, bonding: BondingStatus, counters: LANCounters,
                previous: "OverflowSample | None", receiver_port: int = RECEIVER_PORT) -> "OverflowSample":
        port = counters.port(receiver_port)
        rx = port.rx_bytes if port else None
        tx = port.tx_bytes if port else None
        interval = (time - previous.time).total_seconds() if previous else None
        return cls(
            time, interval, bonding.dsl_leg_up, bonding.mobile_leg_up, bonding.dsl_down_overflow_kbps,
            bonding.dsl_up_overflow_kbps, rx, tx,
            bits_per_second(previous.mobile_rx_bytes, rx, interval) if previous and interval else None,
            bits_per_second(previous.mobile_tx_bytes, tx, interval) if previous and interval else None,
        )

    @property
    def csv_row(self) -> str:
        interval = repr(float(round(self.interval_seconds))) if self.interval_seconds is not None else ""
        return _row([_time(self.time), interval, _YES if self.dsl_leg_up else _NO,
                     _YES if self.mobile_leg_up else _NO, self.dsl_down_limit_kbps, self.dsl_up_limit_kbps,
                     self.mobile_rx_bytes, self.mobile_tx_bytes, self.mobile_down_bps, self.mobile_up_bps])

    @classmethod
    def from_csv(cls, row: str) -> "OverflowSample | None":
        fields = _fields(row, cls._FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        if fields is None or time is None:
            return None
        return cls(time, _float(fields[1]), fields[2] == _YES, fields[3] == _YES, _int(fields[4]), _int(fields[5]),
                   _int(fields[6]), _int(fields[7]), _int(fields[8]), _int(fields[9]))


class HostLink(StrEnum):
    """How the host running the speed test reaches the router.

    WIRED / WIFI come from the host's own network interface (reliable). ROUTER_WIFI /
    LAN_PORT come from the router's port counters: traffic via a LAN port can still be
    Wi-Fi behind a mesh access point or other device on that port.
    """

    WIRED = "wired"
    WIFI = "wifi"
    ROUTER_WIFI = "router-wifi"
    LAN_PORT = "lan-port"

    @property
    def limits_result(self) -> bool:
        """The result measures Wi-Fi rather than the line."""
        return self in (HostLink.WIFI, HostLink.ROUTER_WIFI)


# Wired client ports must carry at least this share of the measured test traffic for
# the traffic to count as entering via a LAN port. Other wired traffic only adds to
# it, so a lower share means the test traffic took another path: the router's Wi-Fi.
_LAN_PORT_SHARE = 0.5


def link_from_router_ports(down_bps: int, up_bps: int, lan_down_bps: int | None,
                           lan_up_bps: int | None) -> HostLink | None:
    """None when the LAN port counters are unknown (no router login)."""
    if lan_down_bps is None or lan_up_bps is None:
        return None
    via_lan = lan_down_bps >= down_bps * _LAN_PORT_SHARE or lan_up_bps >= up_bps * _LAN_PORT_SHARE
    return HostLink.LAN_PORT if via_lan else HostLink.ROUTER_WIFI


@dataclass(frozen=True)
class SpeedTestSample:
    """One internet speed test. The 5G share comes from the receiver port's counters
    during each phase (other traffic in that time counts too); DSL is the rest.
    `host_link` tells whether the result may be limited by Wi-Fi."""

    time: datetime
    latency_ms: float
    down_bps: int
    up_bps: int
    mobile_down_bps: int | None
    mobile_up_bps: int | None
    host_link: HostLink | None = None

    CSV_HEADER = "time,latency_ms,down_bps,up_bps,mobile_down_bps,mobile_up_bps,host_link"
    _FIELD_COUNT = 7
    # Files written before `host_link` existed.
    _LEGACY_FIELD_COUNT = 6

    @property
    def dsl_down_bps(self) -> int | None:
        return None if self.mobile_down_bps is None else max(0, self.down_bps - self.mobile_down_bps)

    @property
    def dsl_up_bps(self) -> int | None:
        return None if self.mobile_up_bps is None else max(0, self.up_bps - self.mobile_up_bps)

    @property
    def csv_row(self) -> str:
        return _row([_time(self.time), round(self.latency_ms, 1), self.down_bps, self.up_bps,
                     self.mobile_down_bps, self.mobile_up_bps, self.host_link.value if self.host_link else ""])

    @classmethod
    def from_csv(cls, row: str) -> "SpeedTestSample | None":
        fields = _fields(row, cls._FIELD_COUNT) or _fields(row, cls._LEGACY_FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        latency, down, up = (_float(fields[1]), _int(fields[2]), _int(fields[3])) if fields else (None, None, None)
        if fields is None or time is None or latency is None or down is None or up is None:
            return None
        link = fields[6] if len(fields) == cls._FIELD_COUNT else ""
        return cls(time, latency, down, up, _int(fields[4]), _int(fields[5]),
                   HostLink(link) if link in HostLink._value2member_map_ else None)


@dataclass(frozen=True)
class SpeedTraceSample:
    """One window of a speed test phase (see `traffic.phase_trace`); `test_time` is the
    time of the speed test it belongs to."""

    test_time: datetime
    direction: Direction
    point: TracePoint

    CSV_HEADER = "test_time,direction,offset_s,total_bps,mobile_bps"
    _FIELD_COUNT = 5

    @property
    def time(self) -> datetime:
        return self.test_time

    @property
    def csv_row(self) -> str:
        return _row([_time(self.test_time), self.direction.value, self.point.offset_seconds,
                     self.point.total_bps, self.point.mobile_bps])

    @classmethod
    def from_csv(cls, row: str) -> "SpeedTraceSample | None":
        fields = _fields(row, cls._FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        offset, total = (_float(fields[2]), _int(fields[3])) if fields else (None, None)
        if (fields is None or time is None or fields[1] not in Direction._value2member_map_
                or offset is None or total is None):
            return None
        return cls(time, Direction(fields[1]), TracePoint(offset, total, _int(fields[4])))


class EventKind(StrEnum):
    """Values that are not measurements but whose changes matter: reconnects, firmware
    updates, DSL retrains, receiver restarts, cell handovers."""

    ONLINE_SINCE = "online_since"
    FIRMWARE = "firmware"
    DSL_MODE = "dsl_mode"
    DSL_STATE = "dsl_state"
    RECEIVER_STATE = "receiver_state"
    NR_CELL = "nr_cell"
    LTE_CELL = "lte_cell"


@dataclass(frozen=True)
class EventSample:
    """A changed value. The first value seen
    is logged too, so every later change has its predecessor in the log."""

    time: datetime
    kind: EventKind
    value: str

    CSV_HEADER = "time,kind,value"
    _FIELD_COUNT = 3

    @property
    def csv_row(self) -> str:
        return _row([_time(self.time), self.kind.value, self.value])

    @classmethod
    def from_csv(cls, row: str) -> "EventSample | None":
        fields = _fields(row, cls._FIELD_COUNT)
        time = _parse_time(fields[0]) if fields else None
        if fields is None or time is None or fields[1] not in EventKind._value2member_map_ or not fields[2]:
            return None
        return cls(time, EventKind(fields[1]), fields[2])


def status_events(status: LineStatus) -> dict[EventKind, str | None]:
    return {EventKind.ONLINE_SINCE: status.online_since, EventKind.FIRMWARE: status.firmware_version}


def details_events(dsl: DSLDetails, receiver: ReceiverStatus | None) -> dict[EventKind, str | None]:
    values = {EventKind.DSL_MODE: dsl.operating_mode, EventKind.DSL_STATE: dsl.state}
    if receiver is not None:
        values.update({EventKind.RECEIVER_STATE: receiver.status, EventKind.NR_CELL: receiver.nr.physical_cell_id,
                       EventKind.LTE_CELL: receiver.lte.physical_cell_id})
    return values
