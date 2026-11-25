"""Coarse ratings for dashboard indicators.

Thresholds follow common LTE/5G field-test bands and VDSL practice; they are
rules of thumb, not standards. The CRC thresholds are judgement.
"""

from enum import IntEnum


class Quality(IntEnum):
    POOR = 0
    FAIR = 1
    GOOD = 2
    EXCELLENT = 3


_RSRP_FLOORS = (-80.0, -90.0, -100.0)
_RSRQ_FLOORS = (-10.0, -15.0, -20.0)
# Below about 6 dB VDSL connections tend to resync.
_SNR_FLOORS = (12.0, 10.0, 6.0)
# Share of the normal sync rate. A line normally resyncs at the same rate; less means
# a degraded line (judgement, like the CRC limits).
_SYNC_FLOORS = (0.99, 0.95, 0.85)
# Upper limits per hour: occasional CRC errors are normal, hundreds per hour are not.
_CRC_GOOD_CEILING = 60.0
_CRC_FAIR_CEILING = 600.0


def _by_floors(value: float, floors: tuple[float, float, float]) -> Quality:
    excellent, good, fair = floors
    if value >= excellent:
        return Quality.EXCELLENT
    if value >= good:
        return Quality.GOOD
    if value >= fair:
        return Quality.FAIR
    return Quality.POOR


def rsrp_quality(rsrp_dbm: float) -> Quality:
    return _by_floors(rsrp_dbm, _RSRP_FLOORS)


def rsrq_quality(rsrq_db: float) -> Quality:
    """Interference and load, independent of signal strength."""
    return _by_floors(rsrq_db, _RSRQ_FLOORS)


def snr_quality(snr_margin_db: float) -> Quality:
    return _by_floors(snr_margin_db, _SNR_FLOORS)


def crc_quality(errors_per_hour: float) -> Quality:
    """Uncorrectable DSL errors per hour."""
    if errors_per_hour <= 0:
        return Quality.EXCELLENT
    if errors_per_hour <= _CRC_GOOD_CEILING:
        return Quality.GOOD
    if errors_per_hour <= _CRC_FAIR_CEILING:
        return Quality.FAIR
    return Quality.POOR


def sync_quality(current_bps: int, normal_bps: int) -> Quality:
    """Current DSL sync rate against the line's normal rate (see `samples.NormalSync`)."""
    return _by_floors(current_bps / normal_bps, _SYNC_FLOORS)


def worst(*qualities: Quality | None) -> Quality | None:
    present = [quality for quality in qualities if quality is not None]
    return min(present) if present else None
