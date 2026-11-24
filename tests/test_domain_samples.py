from datetime import UTC, datetime, timedelta

import pytest

from routerlink.domain.line import DSLDetails
from routerlink.domain.quality import Quality, crc_quality, rsrp_quality, rsrq_quality, snr_quality, sync_quality
from routerlink.domain.samples import (
    ErrorRate,
    HostLink,
    LineSample,
    NormalSync,
    OverflowSample,
    SpeedTestSample,
    StatusSample,
    link_from_router_ports,
)
from routerlink.domain.status import LineStatus
from routerlink.domain.traffic import BondingStatus, LANCounters
from routerlink.speedport.engineer_page import EngineerPage
from routerlink.speedport.varlist import VarList

TIME = datetime.fromtimestamp(1_795_460_000, UTC)
BONDING = BondingStatus(True, False, 84716, 30121)


def test_status_sample_matches_linkbar_csv() -> None:
    status = LineStatus.from_vars(VarList.from_json("""
    [{"varid":"onlinestatus","varvalue":"online"},{"varid":"dsl_downstream","varvalue":"90000000"},
     {"varid":"ex5g_signal_5g","varvalue":"-102.0"},{"varid":"ex5g_freq_5g","varvalue":"NR2100"}]
    """))
    sample = StatusSample.from_status(TIME, status)
    assert sample.csv_row == "2026-11-23T18:53:20Z,online,90000000,,,,-102.0,NR2100,,"
    assert StatusSample.from_csv(sample.csv_row) == sample


def test_separators_in_values_are_replaced() -> None:
    status = LineStatus.from_vars(VarList.from_json(
        '[{"varid":"onlinestatus","varvalue":"on,line\\nnow"},{"varid":"ex5g_freq_5g","varvalue":"NR2100,n78"}]'))
    assert StatusSample.from_status(TIME, status).csv_row == "2026-11-23T18:53:20Z,on line now,,,,,,NR2100 n78,,"


def test_line_sample_round_trip_and_garbage() -> None:
    dsl = DSLDetails.from_page(EngineerPage('downstream_status[2] = " 8.2 dB";\nupstream_status[2] = " 7.0 dB";\n'
                                            'downstream_status[8] = "12";'))
    sample = LineSample.from_details(TIME, dsl, None)
    assert sample.csv_row.startswith("2026-11-23T18:53:20Z,8.2,7.0,")
    assert LineSample.from_csv(sample.csv_row) == sample
    assert LineSample.from_csv("garbage") is None
    assert StatusSample.from_csv(StatusSample.CSV_HEADER) is None


def counters(rx: int, tx: int) -> LANCounters:
    return LANCounters.from_page(EngineerPage(f"var max_interface = 1;\ninte_face_rx[0][1] = {rx};\ninte_face_tx[0][1] = {tx};"))


def test_first_overflow_sample_matches_linkbar_csv() -> None:
    sample = OverflowSample.measure(TIME, BONDING, counters(473_000_000_000, 27_000_000_000), None)
    assert sample.interval_seconds is None
    assert sample.mobile_down_bps is None
    assert sample.csv_row == "2026-11-23T18:53:20Z,,1,0,84716,30121,473000000000,27000000000,,"
    assert OverflowSample.from_csv(sample.csv_row) == sample


def test_overflow_rate_against_logged_previous_sample() -> None:
    first = OverflowSample.measure(TIME, BONDING, counters(473_000_000_000, 27_000_000_000), None)
    logged = OverflowSample.from_csv(first.csv_row)
    second = OverflowSample.measure(TIME + timedelta(seconds=300), BONDING,
                                    counters(482_000_000_000, 27_037_500_000), logged)
    assert second.interval_seconds == 300
    assert second.mobile_down_bps == 240_000_000
    assert second.mobile_up_bps == 1_000_000
    assert second.csv_row.split(",")[1] == "300.0"
    assert OverflowSample.from_csv(second.csv_row) == second


def test_overflow_counter_reset_gives_no_rate() -> None:
    first = OverflowSample.measure(TIME, BONDING, counters(473_000_000_000, 27_000_000_000), None)
    second = OverflowSample.measure(TIME + timedelta(seconds=300), BONDING, counters(5_000, 1_000), first)
    assert second.mobile_down_bps is None
    assert second.mobile_rx_bytes == 5_000


def line(seconds: int, down: int, up: int) -> LineSample:
    dsl = DSLDetails.from_page(EngineerPage(f'downstream_status[8] = "{down}";\nupstream_status[8] = "{up}";'))
    return LineSample.from_details(TIME + timedelta(seconds=seconds), dsl, None)


def test_crc_increase_per_hour() -> None:
    assert ErrorRate.crc(line(0, 100, 5), line(300, 110, 5)) == ErrorRate(120, 0)


def test_fec_increase_per_hour() -> None:
    def fec(seconds: int, down: int) -> LineSample:
        dsl = DSLDetails.from_page(EngineerPage(f'downstream_status[10] = "{down}";\nupstream_status[10] = "0";'))
        return LineSample.from_details(TIME + timedelta(seconds=seconds), dsl, None)

    assert ErrorRate.fec(fec(0, 1_000), fec(3600, 3_500)) == ErrorRate(2_500, 0)


def test_crc_counter_reset_gives_no_rate() -> None:
    assert ErrorRate.crc(line(0, 5_000, 5), line(300, 3, 6)) == ErrorRate(None, 12)


@pytest.mark.parametrize(("rsrp", "expected"), [(-75, Quality.EXCELLENT), (-80, Quality.EXCELLENT), (-85, Quality.GOOD),
                                                (-100, Quality.FAIR), (-100.5, Quality.POOR)])
def test_rsrp_bands(rsrp: float, expected: Quality) -> None:
    assert rsrp_quality(rsrp) == expected


@pytest.mark.parametrize(("rsrq", "expected"), [(-10, Quality.EXCELLENT), (-11, Quality.GOOD), (-19.5, Quality.FAIR),
                                                (-20.5, Quality.POOR)])
def test_rsrq_bands(rsrq: float, expected: Quality) -> None:
    assert rsrq_quality(rsrq) == expected


@pytest.mark.parametrize(("snr", "expected"), [(12, Quality.EXCELLENT), (10, Quality.GOOD), (8.2, Quality.FAIR),
                                               (5.9, Quality.POOR)])
def test_snr_bands(snr: float, expected: Quality) -> None:
    assert snr_quality(snr) == expected


@pytest.mark.parametrize(("per_hour", "expected"), [(0, Quality.EXCELLENT), (60, Quality.GOOD), (61, Quality.FAIR),
                                                    (601, Quality.POOR)])
def test_crc_bands(per_hour: float, expected: Quality) -> None:
    assert crc_quality(per_hour) == expected


@pytest.mark.parametrize(("current", "expected"), [(90_000_000, Quality.EXCELLENT), (89_200_000, Quality.EXCELLENT),
                                                   (86_000_000, Quality.GOOD), (80_000_000, Quality.FAIR),
                                                   (60_000_000, Quality.POOR)])
def test_sync_bands_against_normal(current: int, expected: Quality) -> None:
    assert sync_quality(current, 90_000_000) == expected


def status_sample(down: int | None, up: int | None) -> StatusSample:
    return StatusSample(TIME, "online", down, up, None, None, None, None, None, None)


def test_normal_sync_is_median_ignoring_gaps_and_short_drops() -> None:
    samples = [status_sample(90_000_000, 32_000_000)] * 95 + [status_sample(None, None)] * 20 \
        + [status_sample(40_000_000, 10_000_000)] * 10
    assert NormalSync.from_samples(samples) == NormalSync(90_000_000, 32_000_000)


def test_normal_sync_needs_enough_samples() -> None:
    assert NormalSync.from_samples([status_sample(90_000_000, 32_000_000)] * 99) == NormalSync(None, None)


def test_speed_test_sample_round_trip_and_split() -> None:
    sample = SpeedTestSample(TIME, 62.84, 112_000_000, 48_000_000, 27_000_000, None)
    assert sample.csv_row == "2026-11-23T18:53:20Z,62.8,112000000,48000000,27000000,,"
    assert sample.dsl_down_bps == 85_000_000
    assert sample.dsl_up_bps is None
    restored = SpeedTestSample.from_csv(sample.csv_row)
    assert restored is not None and restored.down_bps == 112_000_000 and restored.latency_ms == 62.8
    assert SpeedTestSample.from_csv(SpeedTestSample.CSV_HEADER) is None


def test_speed_test_host_link_round_trip_and_legacy_rows() -> None:
    sample = SpeedTestSample(TIME, 20.0, 40_000_000, 30_000_000, None, None, HostLink.WIFI)
    assert sample.csv_row.endswith(",wifi")
    assert SpeedTestSample.from_csv(sample.csv_row) == sample
    # Written before the host_link column existed.
    legacy = SpeedTestSample.from_csv("2026-11-23T18:53:20Z,62.8,112000000,48000000,27000000,")
    assert legacy is not None and legacy.host_link is None and legacy.mobile_down_bps == 27_000_000


@pytest.mark.parametrize(("lan_down", "lan_up", "expected"), [
    (41_000_000, 46_000_000, HostLink.LAN_PORT),     # test traffic through a LAN port
    (60_000_000, 1_000_000, HostLink.LAN_PORT),      # plus other LAN traffic: still LAN port
    (3_000_000, 600_000, HostLink.ROUTER_WIFI),      # test traffic not on any LAN port
    (None, 600_000, None),                           # no router login: unknown
])
def test_link_from_router_ports(lan_down: int | None, lan_up: int | None, expected: HostLink | None) -> None:
    assert link_from_router_ports(41_000_000, 47_000_000, lan_down, lan_up) == expected


def test_only_wifi_links_limit_the_result() -> None:
    assert [link for link in HostLink if link.limits_result] == [HostLink.WIFI, HostLink.ROUTER_WIFI]
