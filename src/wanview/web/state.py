"""JSON views of the monitor state and history for the dashboard. Rates in bit/s,
times ISO 8601 UTC; ratings ("excellent" … "poor") are computed here."""

from datetime import UTC, datetime, timedelta

from wanview.app.data_log import DataLog
from wanview.app.monitor import Monitor
from wanview.domain.line import Cell
from wanview.domain.samples import ErrorRate, NormalSync, StatusSample
from wanview.domain.quality import Quality, crc_quality, rsrp_quality, rsrq_quality, snr_quality, sync_quality, worst
from wanview.domain.traffic import HybridTrafficRate

_KBIT = 1000
_ONLINE = "online"
_RUNNING = "running"
# Event kinds derived from the status log (the other kinds are logged as events).
_NR_BAND = "nr_band"
_LTE_BAND = "lte_band"
HISTORY_RANGES = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}


def _iso(time: datetime | None) -> str | None:
    return time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if time else None


def _rating(quality: Quality | None) -> str | None:
    return quality.name.lower() if quality is not None else None


def _state_rating(value: str | None, up: str) -> str | None:
    if not value:
        return None
    return _rating(Quality.EXCELLENT if value == up else Quality.POOR)


def _cell(cell: Cell, band: str | None) -> dict:
    # Strong signal with heavy interference is still poor: the worse rating counts.
    quality = worst(rsrp_quality(cell.rsrp) if cell.rsrp is not None else None,
                    rsrq_quality(cell.rsrq) if cell.rsrq is not None else None)
    return {"band": band, "pci": cell.physical_cell_id, "rsrp": cell.rsrp, "rsrq": cell.rsrq, "rating": _rating(quality)}


def state(monitor: Monitor) -> dict:
    readings = monitor.readings
    status = readings.status
    has_receiver = status is not None and status.has_receiver
    result: dict = {
        "router": {
            "model": monitor.model.name if monitor.model else None,
            "supported": monitor.is_supported if monitor.model else None,
            "serial": monitor.serial,
            "new": monitor.is_new_router,
            "ignored": monitor.is_ignored,
            "hasPassword": monitor.has_password,
            "canRestart": monitor.can_restart_router,
            "url": monitor.settings.router_url,
        },
        "paused": monitor.paused,
        "routerBackTime": _iso(monitor.router_back_time),
        "settings": {"statusInterval": monitor.settings.status_interval,
                     "detailsInterval": monitor.settings.details_interval,
                     "speedtestInterval": monitor.settings.speedtest_interval},
        "speedtest": _speedtest(monitor),
        "routers": [{"serial": router.serial_number, "name": router.name, "added": router.added,
                     "choice": router.choice.value, "connected": router.serial_number == monitor.serial}
                    for router in monitor.routers],
        "statusTime": _iso(readings.status_time),
        "detailsTime": _iso(readings.details_time),
        "errors": [error for error in (readings.status_error, readings.details_error) if error],
        "hasReceiver": has_receiver,
        "status": None, "dsl": None, "bonding": None, "receiver": None, "traffic": None,
    }
    if status is not None:
        normal = monitor.normal_sync

        def sync_rating(current: int | None, normal_bps: int | None) -> Quality | None:
            return sync_quality(current, normal_bps) if current is not None and normal_bps else None

        result["status"] = {
            "online": status.online_status, "onlineRating": _state_rating(status.online_status, _ONLINE),
            "onlineSince": status.online_since, "firmware": status.firmware_version,
            "dslLink": status.dsl_link_status, "dslLinkRating": _state_rating(status.dsl_link_status, _ONLINE),
            "syncDown": status.dsl_downstream, "syncUp": status.dsl_upstream,
            "syncNormalDown": normal.down_bps, "syncNormalUp": normal.up_bps,
            "syncRating": _rating(worst(sync_rating(status.dsl_downstream, normal.down_bps),
                                        sync_rating(status.dsl_upstream, normal.up_bps))),
            "internetDown": status.internet_download, "internetUp": status.internet_upload,
            "nrSignal": status.nr_signal, "nrBand": status.nr_band,
            "lteSignal": status.lte_signal, "lteBand": status.lte_band,
            "signalRating": _rating(rsrp_quality(status.nr_signal if status.nr_signal is not None else status.lte_signal))
            if (status.nr_signal is not None or status.lte_signal is not None) and has_receiver else None,
        }
    if readings.dsl is not None:
        down, up = readings.dsl.downstream, readings.dsl.upstream
        crc = readings.crc_rate
        crc_rating = worst(*(crc_quality(value) for value in (crc.down_per_hour, crc.up_per_hour) if value is not None)) if crc else None
        margins = [value for value in (down.snr_margin_db, up.snr_margin_db) if value is not None]
        result["dsl"] = {
            "mode": readings.dsl.operating_mode, "state": readings.dsl.state,
            "attainableDown": down.attainable_rate_kbps and down.attainable_rate_kbps * _KBIT,
            "attainableUp": up.attainable_rate_kbps and up.attainable_rate_kbps * _KBIT,
            "snrDown": down.snr_margin_db, "snrUp": up.snr_margin_db,
            "snrRating": _rating(snr_quality(min(margins))) if margins else None,
            "attenuationDown": down.attenuation_db, "attenuationUp": up.attenuation_db,
            "crcDown": down.crc_errors, "crcUp": up.crc_errors,
            "crcPerHourDown": crc.down_per_hour if crc else None, "crcPerHourUp": crc.up_per_hour if crc else None,
            "crcRating": _rating(crc_rating),
            "fecPerHourDown": readings.fec_rate.down_per_hour if readings.fec_rate else None,
            "fecPerHourUp": readings.fec_rate.up_per_hour if readings.fec_rate else None,
            "fecDown": down.fec_errors, "fecUp": up.fec_errors,
        }
    if has_receiver:
        bonding, overflow = readings.bonding, readings.overflow
        result["bonding"] = {
            "dslLegUp": bonding.dsl_leg_up if bonding else None,
            "mobileLegUp": bonding.mobile_leg_up if bonding else None,
            "legsRating": _rating(Quality.EXCELLENT if bonding.dsl_leg_up and bonding.mobile_leg_up else Quality.POOR)
            if bonding else None,
            "limitDown": bonding.dsl_down_overflow_kbps * _KBIT if bonding and bonding.dsl_down_overflow_kbps
            else status.internet_download if status else None,
            "limitUp": bonding.dsl_up_overflow_kbps * _KBIT if bonding and bonding.dsl_up_overflow_kbps
            else status.internet_upload if status else None,
            "overflowDown": overflow.mobile_down_bps if overflow else None,
            "overflowUp": overflow.mobile_up_bps if overflow else None,
            "overflowInterval": overflow.interval_seconds if overflow else None,
        }
        receiver = readings.receiver
        if receiver is not None:
            result["receiver"] = {
                "state": receiver.status, "stateRating": _state_rating(receiver.status, _RUNNING),
                "nr": _cell(receiver.nr, status.nr_band if status else None),
                "lte": _cell(receiver.lte, status.lte_band if status else None),
            }
    if readings.traffic is not None:
        ports = readings.traffic.ports
        traffic: dict = {}
        if has_receiver:
            hybrid = HybridTrafficRate.from_lan(readings.traffic)
            ports = hybrid.client_ports
            traffic.update(mobileDown=hybrid.mobile_down_bps, mobileUp=hybrid.mobile_up_bps,
                           dslEstimateDown=hybrid.dsl_down_estimate_bps, dslEstimateUp=hybrid.dsl_up_estimate_bps)
        traffic["ports"] = [{"number": port.number, "down": port.down_bps, "up": port.up_bps} for port in ports]
        result["traffic"] = traffic
    elif readings.traffic_error:
        result["traffic"] = {"error": readings.traffic_error}
    return result


def _speedtest(monitor: Monitor) -> dict:
    last = monitor.last_speedtest
    return {
        "running": monitor.speedtest_running,
        "error": monitor.speedtest_error,
        "last": None if last is None else {
            "time": _iso(last.time), "latencyMs": last.latency_ms, "down": last.down_bps, "up": last.up_bps,
            "mobileDown": last.mobile_down_bps, "mobileUp": last.mobile_up_bps,
            "dslDown": last.dsl_down_bps, "dslUp": last.dsl_up_bps,
            "hostLink": last.host_link.value if last.host_link else None,
        },
    }


def _measured(value: float | None) -> float | None:
    """Older log rows may hold the receiver's 0.0 placeholder ("no measurement")."""
    return None if value == 0 else value


def _sync(rate: int | None, online: bool) -> int | None:
    """Older log rows may hold the router's sync 0 glitch while online."""
    return None if rate == 0 and online else rate


def _bps(kbps: int | None) -> int | None:
    return kbps * _KBIT if kbps is not None else None


def history(data_log: DataLog, range_name: str, normal_sync: NormalSync) -> dict:
    since = datetime.now(UTC) - HISTORY_RANGES[range_name]
    lines = data_log.line_samples(since)
    pairs = list(zip(lines, lines[1:]))
    crc_rates = [(current, ErrorRate.crc(previous, current)) for previous, current in pairs]
    fec_rates = [(current, ErrorRate.fec(previous, current)) for previous, current in pairs]
    statuses = data_log.status_samples(since)
    events = [[_iso(e.time), e.kind.value, e.value] for e in data_log.event_samples(since)] + _band_changes(statuses)
    return {
        "since": _iso(since),
        "now": _iso(datetime.now(UTC)),
        "normalSync": [normal_sync.down_bps, normal_sync.up_bps],
        # [time, sync down, sync up, 5G dBm, LTE dBm, online, internet rate down, up]
        "status": [[_iso(s.time), _sync(s.dsl_downstream, s.online_status == _ONLINE),
                    _sync(s.dsl_upstream, s.online_status == _ONLINE), s.nr_signal, s.lte_signal, s.online_status == _ONLINE,
                    s.internet_download, s.internet_upload]
                   for s in statuses],
        # [time, SNR down, SNR up, attainable down, attainable up (bit/s), 5G RSRQ, LTE RSRQ,
        #  attenuation down, up (dB)]
        "line": [[_iso(s.time), s.snr_margin_down, s.snr_margin_up, _bps(s.attainable_down_kbps),
                  _bps(s.attainable_up_kbps), _measured(s.nr_rsrq), _measured(s.lte_rsrq),
                  s.attenuation_down, s.attenuation_up] for s in lines],
        # [time, CRC errors per hour down, up] between consecutive line polls
        "crc": _rates(crc_rates),
        # [time, corrected (FEC) errors per hour down, up]
        "fec": _rates(fec_rates),
        # [time, 5G down, 5G up, DSL leg up, 5G leg up, DSL limit down, DSL limit up] (bit/s)
        "overflow": [[_iso(s.time), s.mobile_down_bps, s.mobile_up_bps, s.dsl_leg_up, s.mobile_leg_up,
                      s.dsl_down_limit_kbps and s.dsl_down_limit_kbps * _KBIT,
                      s.dsl_up_limit_kbps and s.dsl_up_limit_kbps * _KBIT]
                     for s in data_log.overflow_samples(since)],
        # [time, down, up, latency ms, host link ("wired", "wifi" or None), 5G part down, up]
        "speedtest": [[_iso(s.time), s.down_bps, s.up_bps, s.latency_ms, s.host_link.value if s.host_link else None,
                       s.mobile_down_bps, s.mobile_up_bps]
                      for s in data_log.speedtest_samples(since)],
        # [time, kind, new value] in time order: logged events and 5G/LTE band changes
        "events": sorted(events),
    }


def _rates(rates: list) -> list:
    return [[_iso(current.time), rate.down_per_hour if rate else None, rate.up_per_hour if rate else None]
            for current, rate in rates]


def _band_changes(statuses: list[StatusSample]) -> list[list]:
    """Bands are logged with every status poll; only their changes are events."""
    changes = []
    previous: dict[str, str | None] = {}
    for sample in statuses:
        for kind, band in ((_NR_BAND, sample.nr_band), (_LTE_BAND, sample.lte_band)):
            if band and kind in previous and band != previous[kind]:
                changes.append([_iso(sample.time), kind, band])
            if band:
                previous[kind] = band
    return changes
