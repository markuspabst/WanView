"""Engineer-page readings: DSL line details and the external 5G receiver."""

from dataclasses import dataclass

from routerlink.speedport.engineer_page import EngineerPage, integer, number

DSL_PAGE = "dsl_hidden_status.stm"
RECEIVER_PAGE = "hybrid_receiver.stm"

# Row indices of `upstream_status` / `downstream_status`, matching the page's `dsl_title` array.
_ACTUAL_RATE = 0
_ATTAINABLE_RATE = 1
_SNR_MARGIN = 2
_SIGNAL_LEVEL = 3
_ATTENUATION = 4
_CRC_ERRORS = 8
_HEC_ERRORS = 9
_FEC_ERRORS = 10

# The receiver page uses "-" for "no value".
_NO_VALUE = "-"


@dataclass(frozen=True)
class DSLDirection:
    actual_rate_kbps: int | None
    attainable_rate_kbps: int | None
    snr_margin_db: float | None
    signal_level_db: float | None
    attenuation_db: float | None
    crc_errors: int | None
    hec_errors: int | None
    fec_errors: int | None

    @classmethod
    def from_page(cls, page: EngineerPage, array: str) -> "DSLDirection":
        def row(index: int) -> str | None:
            return page.element(array, index)

        return cls(
            actual_rate_kbps=integer(row(_ACTUAL_RATE)),
            attainable_rate_kbps=integer(row(_ATTAINABLE_RATE)),
            snr_margin_db=number(row(_SNR_MARGIN)),
            signal_level_db=number(row(_SIGNAL_LEVEL)),
            attenuation_db=number(row(_ATTENUATION)),
            crc_errors=integer(row(_CRC_ERRORS)),
            hec_errors=integer(row(_HEC_ERRORS)),
            fec_errors=integer(row(_FEC_ERRORS)),
        )


@dataclass(frozen=True)
class DSLDetails:
    """From `dsl_hidden_status.stm`."""

    operating_mode: str | None
    """e.g. "VDSL2 Annex B"."""
    state: str | None
    """e.g. "0x801 (showtime)"."""
    power_mode: str | None
    upstream: DSLDirection
    downstream: DSLDirection

    @classmethod
    def from_page(cls, page: EngineerPage) -> "DSLDetails":
        return cls(
            operating_mode=page.string("dsl_opmode"),
            state=page.string("dsl_state"),
            power_mode=page.string("dsl_mode"),
            upstream=DSLDirection.from_page(page, "upstream_status"),
            downstream=DSLDirection.from_page(page, "downstream_status"),
        )


@dataclass(frozen=True)
class Cell:
    physical_cell_id: str | None
    rsrp: float | None
    rsrq: float | None


@dataclass(frozen=True)
class ReceiverStatus:
    """From `hybrid_receiver.stm`. SIM, IMEI, IMSI and APN are deliberately not parsed."""

    status: str | None
    """e.g. "running"."""
    uptime_seconds: int | None
    nr: Cell
    lte: Cell

    @classmethod
    def from_page(cls, page: EngineerPage) -> "ReceiverStatus":
        def cell(suffix: str) -> Cell:
            return Cell(
                physical_cell_id=_value(page.string(f"Receiver_Physical_{suffix}")),
                rsrp=number(page.string(f"Receiver_RSRP_{suffix}")),
                rsrq=number(page.string(f"Receiver_RSRQ_{suffix}")),
            )

        return cls(
            status=_value(page.string("Receiver_Status")),
            uptime_seconds=integer(page.string("Receiver_UpTime")),
            nr=cell("5G"),
            lte=cell("LTE"),
        )


def _value(raw: str | None) -> str | None:
    if not raw or raw == _NO_VALUE:
        return None
    return raw
