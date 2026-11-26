from wanview.domain.line import DSLDetails, DSLDirection, ReceiverStatus, Cell
from wanview.domain.traffic import (
    RECEIVER_PORT,
    BondingStatus,
    Direction,
    HybridTrafficRate,
    LANCounters,
    LANTrafficRate,
    PortCounters,
    PortRate,
    mobile_onset,
    phase_trace,
)
from wanview.speedport.engineer_page import EngineerPage


def test_dsl_details_parse_hidden_status_page() -> None:
    details = DSLDetails.from_page(EngineerPage("""
    var dsl_opmode = "VDSL2 Annex B";
    var dsl_state = " 0x801 (showtime)";
    var dsl_mode = "L0";
    upstream_status[0] = " 32000 Kbps";
    downstream_status[0] = " 90000 Kbps";
    upstream_status[1] = " 33219 Kbps";
    downstream_status[1] = " 99367 Kbps";
    upstream_status[2] = " 7.0 dB";
    downstream_status[2] = " 8.2 dB";
    upstream_status[3] = " 0.0 dB";
    downstream_status[3] = " 19.7 dB";
    upstream_status[4] = " 0.0 dB";
    downstream_status[4] = " 19.7 dB";
    upstream_status[8] = "0";
    downstream_status[8] = "12";
    upstream_status[9] = "0";
    downstream_status[9] = "0";
    upstream_status[10] = "19990";
    downstream_status[10] = "14302101";
    """))
    assert details.operating_mode == "VDSL2 Annex B"
    assert details.state == "0x801 (showtime)"
    assert details.power_mode == "L0"
    assert details.downstream == DSLDirection(90000, 99367, 8.2, 19.7, 19.7, 12, 0, 14_302_101)
    assert details.upstream.snr_margin_db == 7.0
    assert details.upstream.fec_errors == 19990
    # The page reports 0.0 dB upstream where it has no measurement.
    assert details.upstream.attenuation_db is None
    assert details.upstream.signal_level_db is None


def test_receiver_parses_values_and_placeholders() -> None:
    receiver = ReceiverStatus.from_page(EngineerPage("""
        var Receiver_Status = "-";
        var Receiver_RSRP_LTE = "-";
    Receiver_Status = "running";
    Receiver_UpTime = "1538986";
    Receiver_Physical_5G = "873";
    Receiver_RSRP_5G = "-103.0";
    Receiver_RSRQ_5G = "-11.0";
    Receiver_Physical_LTE = "-";
    """))
    assert receiver.status == "running"
    assert receiver.uptime_seconds == 1_538_986
    assert receiver.nr == Cell("873", -103, -11)
    assert receiver.lte == Cell(None, None, None)


LAN_PAGE = EngineerPage("""
var max_interface = 2;
inte_face_speed[0] = 1000;
inte_face_tx[0][0] = 10;
inte_face_tx[0][1] = 4294967000;
inte_face_rx[0][1] = 100;
inte_face_speed[1] = 1000;
inte_face_tx[1][1] = 1924008923;
inte_face_rx[1][1] = 3973273581;
""")


def test_receiver_zero_means_no_measurement() -> None:
    receiver = ReceiverStatus.from_page(EngineerPage('Receiver_RSRP_5G = "0.0";\nReceiver_RSRQ_5G = "0.0";'))
    assert receiver.nr == Cell(None, None, None)


def test_counters_parse_bytes_column() -> None:
    counters = LANCounters.from_page(LAN_PAGE)
    assert len(counters.ports) == 2
    assert counters.ports[0] == PortCounters(1, 1000, 4_294_967_000, 100)
    assert counters.ports[1].rx_bytes == 3_973_273_581


def test_rate_handles_32_bit_wrap() -> None:
    new = LANCounters.from_page(EngineerPage("""
    var max_interface = 2;
    inte_face_tx[0][1] = 704;
    inte_face_rx[0][1] = 1100;
    inte_face_tx[1][1] = 1924009923;
    inte_face_rx[1][1] = 3973273581;
    """))
    rate = LANTrafficRate.between(LANCounters.from_page(LAN_PAGE), new, 2)
    assert rate is not None
    # Port 1 TX wrapped: 4294967000 -> 704 is 1000 bytes.
    assert rate.ports[0] == PortRate(1, 4000, 4000)
    assert rate.ports[1] == PortRate(2, 4000, 0)


def test_rate_rejects_non_positive_interval() -> None:
    counters = LANCounters.from_page(LAN_PAGE)
    assert LANTrafficRate.between(counters, counters, 0) is None


def single_port(rx: int, tx: int = 0) -> LANCounters:
    return LANCounters.from_page(EngineerPage(f"var max_interface = 1;\ninte_face_rx[0][1] = {rx};\ninte_face_tx[0][1] = {tx};"))


def test_delta_above_4_gb_on_64_bit_counter_is_not_truncated() -> None:
    # 9 GB in 300 s (≈ 240 Mbit/s) on the 64-bit LAN1 counter.
    rate = LANTrafficRate.between(single_port(473_000_000_000), single_port(482_000_000_000), 300)
    assert rate is not None and rate.ports[0].up_bps == 240_000_000


def test_reset_of_64_bit_counter_gives_no_rate() -> None:
    rate = LANTrafficRate.between(single_port(473_000_000_000), single_port(1_000), 300)
    assert rate is not None and rate.ports[0].up_bps is None


def test_hybrid_splits_measured_5g_and_estimated_dsl() -> None:
    # Port 1: 5G receiver (router RX 160 Mbit/s, TX 8). Port 2: client (router TX 223, RX 10).
    lan = LANTrafficRate((PortRate(1, 8_000_000, 160_000_000), PortRate(2, 223_000_000, 10_000_000)))
    hybrid = HybridTrafficRate.from_lan(lan)
    assert hybrid.mobile_down_bps == 160_000_000
    assert hybrid.mobile_up_bps == 8_000_000
    assert hybrid.client_down_bps == 223_000_000
    assert hybrid.dsl_down_estimate_bps == 63_000_000
    assert hybrid.dsl_up_estimate_bps == 2_000_000
    assert [port.number for port in hybrid.client_ports] == [2]


def test_dsl_estimate_never_negative() -> None:
    lan = LANTrafficRate((PortRate(1, 0, 50_000_000), PortRate(2, 10_000_000, 0)))
    assert HybridTrafficRate.from_lan(lan).dsl_down_estimate_bps == 0


def test_bonding_parses_legs_and_limits() -> None:
    bonding = BondingStatus.from_page(EngineerPage("""
    DSL_Status = "-";
    DSL_Status = "Up";
    LTE_Status = "Down";
    DSL_UBW = "30121";
    DSL_DBW = "84716";
    """))
    assert bonding == BondingStatus(True, False, 84716, 30121)


MB = 1_000_000


def receiver_counters(rx: int, tx: int = 0) -> LANCounters:
    return LANCounters((PortCounters(RECEIVER_PORT, None, tx, rx),))


def test_phase_trace_shows_when_5g_joins() -> None:
    # 100 Mbit/s for 8 s; 5G carries nothing for 2 s, then 40 Mbit/s.
    start = 1000.0
    byte_trace = tuple((start + s, int(s * 12.5 * MB)) for s in (0.0, 2.0, 4.0, 6.0, 8.0))
    readings = [(start - 0.5, receiver_counters(0)), (start + 2.0, receiver_counters(0)),
                (start + 4.0, receiver_counters(10 * MB)), (start + 6.0, receiver_counters(20 * MB)),
                (start + 8.5, receiver_counters(30 * MB))]
    points = phase_trace(byte_trace, readings, Direction.DOWN)
    assert [(p.offset_seconds, p.total_bps, p.mobile_bps) for p in points] == [
        (1.0, 100_000_000, 0), (3.0, 100_000_000, 40_000_000), (5.0, 100_000_000, 40_000_000),
        (7.0, 100_000_000, 40_000_000)]
    assert mobile_onset(points) == 3.0


def test_phase_trace_without_router_readings_has_total_only() -> None:
    byte_trace = tuple((s, int(s * 12.5 * MB)) for s in (0.0, 0.5, 1.0, 1.5, 2.0))
    points = phase_trace(byte_trace, [], Direction.DOWN)
    assert [(p.offset_seconds, p.total_bps, p.mobile_bps) for p in points] == [
        (0.5, 100_000_000, None), (1.5, 100_000_000, None)]
    assert mobile_onset(points) is None
