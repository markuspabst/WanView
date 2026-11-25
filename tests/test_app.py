import asyncio
from dataclasses import replace
import os
import socket
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakeRouter, FakeTester
import httpx

from wanview.app.data_log import DataLog
from wanview.app.monitor import Monitor
from wanview.app.store import Choice, Settings, Store
from wanview.domain.samples import EventKind, HostLink, StatusSample
from wanview.host_network import InterfaceKind
from wanview.speedtest import RateLimited
from wanview.domain.status import LineStatus
from wanview.speedport.varlist import VarList

DEFAULTS = Settings(router_url="https://speedport.ip", status_interval=3600, details_interval=3600)


def router_log(tmp_path: Path, serial: str = "SN-A") -> DataLog:
    return DataLog(tmp_path / "routers" / serial / "logs")


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def started(tmp_path: Path, router: FakeRouter, initial_password: str | None = None,
                  store: Store | None = None, tester: FakeTester | None = None,
                  host_kind: InterfaceKind | None = None) -> Monitor:
    store = store or Store(tmp_path, DEFAULTS)
    monitor = Monitor(store, initial_password, client_factory=router.client,
                      speed_tester=tester or FakeTester(), host_interface_kind=lambda: host_kind)
    monitor.start()
    await settle()
    return monitor


async def test_unknown_router_gets_setup_prompt_and_no_login(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router)
    assert monitor.is_new_router
    assert not monitor.has_password
    assert router.engineer_reads == []
    assert monitor.readings.status is not None
    await monitor.stop()


async def test_initial_password_goes_to_first_router(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router, initial_password="secret")
    assert not monitor.is_new_router
    assert monitor.has_password
    assert router.engineer_reads[0] == ("secret", "dsl_hidden_status.stm")
    assert Store(tmp_path, DEFAULTS).password("SN-A") == "secret"
    await monitor.stop()


async def test_password_is_not_sent_to_another_router(tmp_path: Path) -> None:
    store = Store(tmp_path, DEFAULTS)
    store.save_password("SN-A", "secret")
    store.save_routers([Store.new_router("SN-A", "Speedport Smart 4", Choice.SET_UP)])
    router = FakeRouter(serial="SN-B")
    monitor = await started(tmp_path, router, initial_password="secret", store=store)
    assert monitor.is_new_router
    assert not monitor.has_password
    assert router.engineer_reads == []
    await monitor.stop()


async def test_ignored_router_is_not_monitored(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router)
    monitor.set_choice("SN-A", Choice.IGNORED)
    await settle()
    assert monitor.is_ignored
    assert monitor.readings.status is None
    assert router_log(tmp_path).status_samples(datetime.now(UTC) - timedelta(minutes=1)) != []  # before ignoring
    await monitor.stop()


async def test_saving_password_sets_up_router_and_logs_in(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router)
    monitor.save_password("secret")
    await settle()
    assert monitor.current_router is not None and monitor.current_router.choice == Choice.SET_UP
    assert ("secret", "dsl_hidden_status.stm") in router.engineer_reads
    await monitor.stop()


async def test_pause_stops_polling(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router, initial_password="secret")
    monitor.pause()
    reads = len(router.engineer_reads)
    monitor.save_password("other")  # would restart polling if not paused
    await settle()
    assert len(router.engineer_reads) == reads
    await monitor.stop()


def test_secrets_file_is_private(tmp_path: Path) -> None:
    store = Store(tmp_path, DEFAULTS)
    store.save_password("SN-A", "secret")
    assert stat.S_IMODE(os.stat(tmp_path / "secrets.json").st_mode) == 0o600
    store.delete_password("SN-A")
    assert store.password("SN-A") is None


def test_unreadable_dashboard_access_does_not_open_the_dashboard(tmp_path: Path) -> None:
    (tmp_path / "dashboard.json").write_text("{broken")
    with pytest.raises(ValueError, match="delete it"):
        Store(tmp_path, DEFAULTS).dashboard_access()


async def test_unresolvable_router_gets_an_explained_warning_once(tmp_path: Path,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    failure = httpx.ConnectError("[Errno -2] Name or service not known")
    failure.__cause__ = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    monitor = await started(tmp_path, FakeRouter(unreachable=failure))
    await monitor._identify()
    await monitor.stop()
    assert monitor.readings.status_error.startswith("Cannot resolve speedport.ip")
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1 and "Cannot resolve speedport.ip" in warnings[0].getMessage()


async def test_changed_values_are_logged_as_events_once(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router, initial_password="secret")
    await monitor._refresh_status()
    router.online_since, router.dsl_state = "2026-11-25 03:00:00", "0x302 (training)"
    await monitor._refresh_status()
    await monitor._refresh_details()
    await monitor.stop()

    events = [(e.kind, e.value) for e in router_log(tmp_path).event_samples(datetime.now(UTC) - timedelta(hours=1))]
    assert events == [
        (EventKind.ONLINE_SINCE, "2026-11-24 10:00:00"),
        (EventKind.DSL_MODE, "VDSL2 Annex B"),
        (EventKind.DSL_STATE, "0x801 (showtime)"),
        (EventKind.ONLINE_SINCE, "2026-11-25 03:00:00"),
        (EventKind.DSL_STATE, "0x302 (training)"),
    ]


async def test_events_are_not_repeated_after_restart(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(), initial_password="secret")
    await monitor.stop()
    monitor = await started(tmp_path, FakeRouter(), store=Store(tmp_path, DEFAULTS))
    await monitor.stop()
    events = router_log(tmp_path).event_samples(datetime.now(UTC) - timedelta(hours=1))
    assert len(events) == 3


async def test_each_router_has_its_own_logs_and_settings(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router, initial_password="secret")
    monitor.update_settings(replace(monitor.settings, status_interval=60, speedtest_interval=120))
    await settle()

    router.serial = "SN-B"
    await monitor._refresh_status()        # the router at this address was swapped
    assert monitor.serial is None and not monitor.has_password
    await monitor._identify()
    await monitor.stop()

    assert monitor.serial == "SN-B"
    assert monitor.settings.status_interval == DEFAULTS.status_interval      # SN-A's settings stay with SN-A
    assert Store(tmp_path, DEFAULTS).settings("SN-A").speedtest_interval == 120
    since = datetime.now(UTC) - timedelta(hours=1)
    assert router_log(tmp_path, "SN-A").status_samples(since) != []
    assert len(router_log(tmp_path, "SN-B").status_samples(since)) == 1
    # The new router's events start fresh instead of continuing SN-A's values.
    assert [e.kind for e in router_log(tmp_path, "SN-B").event_samples(since)] == [EventKind.ONLINE_SINCE]


async def test_swapped_router_never_gets_the_old_password(tmp_path: Path) -> None:
    router = FakeRouter()
    monitor = await started(tmp_path, router, initial_password="secret")
    router.serial = "SN-B"
    router.engineer_reads.clear()
    await monitor._refresh_status()
    await monitor._refresh_details()
    await monitor.stop()
    assert all(password is None for password, _ in router.engineer_reads)


async def test_logs_from_before_per_router_storage_move_to_the_first_router(tmp_path: Path) -> None:
    status = LineStatus.from_vars(VarList.from_json('[{"varid":"onlinestatus","varvalue":"online"}]'))
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
    DataLog(tmp_path / "logs").append_status(StatusSample.from_status(old, status))
    monitor = await started(tmp_path, FakeRouter())
    await monitor.stop()
    assert router_log(tmp_path).status_samples(old)[0].time == old
    assert not (tmp_path / "logs").exists()


def test_unsafe_serial_cannot_escape_the_data_folder(tmp_path: Path) -> None:
    folder = Store(tmp_path, DEFAULTS).router_folder("../../etc")
    assert folder.parent == tmp_path / "routers"


async def test_speed_test_needs_an_identified_router(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(unreachable=httpx.ConnectError("offline")))
    assert not monitor.start_speedtest()
    await monitor.stop()


def test_settings_override_defaults_and_survive_corruption(tmp_path: Path) -> None:
    store = Store(tmp_path, DEFAULTS)
    store.save_settings(Settings("https://192.168.2.1", 60, 600))
    assert store.settings().router_url == "https://192.168.2.1"
    (tmp_path / "settings.json").write_text("{broken")
    assert store.settings() == DEFAULTS


def test_data_log_reads_back_across_days(tmp_path: Path) -> None:
    data_log = DataLog(tmp_path)
    status = LineStatus.from_vars(VarList.from_json('[{"varid":"onlinestatus","varvalue":"online"}]'))
    now = datetime.now(UTC).replace(microsecond=0)
    for time in (now - timedelta(days=1, hours=1), now - timedelta(hours=1), now - timedelta(days=9)):
        data_log.append_status(StatusSample.from_status(time, status))
    samples = data_log.status_samples(now - timedelta(days=2))
    assert [sample.time for sample in samples] == [now - timedelta(days=1, hours=1), now - timedelta(hours=1)]
    first_file = sorted(tmp_path.glob("*-status.csv"))[0]
    assert first_file.read_text().splitlines()[0] == StatusSample.CSV_HEADER


@pytest.fixture(autouse=True)
def quiet_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")


async def test_speed_test_logs_result_with_5g_split(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(receiver=True), initial_password="secret")
    assert monitor.start_speedtest()
    assert not monitor.start_speedtest()  # one at a time
    await settle()
    sample = monitor.last_speedtest
    assert sample is not None and sample.down_bps == 100_000_000
    # 10 MB on the receiver port between the phase readings.
    assert sample.mobile_down_bps is not None and sample.mobile_down_bps > 0
    # The CSV keeps whole seconds.
    logged = router_log(tmp_path).speedtest_samples(sample.time - timedelta(seconds=1))
    assert [(s.down_bps, s.up_bps) for s in logged] == [(100_000_000, 40_000_000)]
    await monitor.stop()


async def test_speed_test_without_password_has_no_split(tmp_path: Path) -> None:
    router = FakeRouter(receiver=True)
    monitor = await started(tmp_path, router)
    monitor.start_speedtest()
    await settle()
    assert monitor.last_speedtest is not None and monitor.last_speedtest.mobile_down_bps is None
    assert router.engineer_reads == []
    await monitor.stop()


async def test_speed_test_failure_is_reported(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(), tester=FakeTester(fail=httpx.ConnectError("offline")))
    monitor.start_speedtest()
    await settle()
    assert monitor.speedtest_error is not None and "offline" in monitor.speedtest_error
    assert not monitor.speedtest_running
    await monitor.stop()


async def test_no_speed_test_while_paused(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter())
    monitor.pause()
    assert not monitor.start_speedtest()
    await monitor.stop()


@pytest.mark.parametrize(("receiver", "password", "host_kind", "expected"), [
    (False, "secret", None, HostLink.LAN_PORT),              # the only port is a LAN client and carries the traffic
    (True, "secret", None, HostLink.ROUTER_WIFI),            # the only port is the 5G receiver: no LAN client moved data
    (False, None, None, None),                               # no router login, host unknown
    (False, "secret", InterfaceKind.WIFI, HostLink.WIFI),    # the host's own interface wins (e.g. mesh Wi-Fi on a LAN port)
    (False, None, InterfaceKind.WIRED, HostLink.WIRED),      # host check needs no router login
])
async def test_speed_test_tells_wired_from_wifi(tmp_path: Path, receiver: bool, password: str | None,
                                                host_kind: InterfaceKind | None, expected: HostLink | None) -> None:
    monitor = await started(tmp_path, FakeRouter(receiver=receiver), initial_password=password, host_kind=host_kind)
    monitor.start_speedtest()
    await settle()
    assert monitor.last_speedtest is not None and monitor.last_speedtest.host_link == expected
    await monitor.stop()


async def test_failed_scheduled_test_is_not_retried_every_minute(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(), tester=FakeTester(fail=httpx.ConnectError("offline")))
    monitor.update_settings(Settings(DEFAULTS.router_url, 3600, 3600, speedtest_interval=60))
    now = datetime.now(UTC)
    assert monitor._speedtest_due(now)
    monitor.start_speedtest()
    await settle()
    assert monitor.speedtest_error is not None
    assert not monitor._speedtest_due(now + timedelta(minutes=1))
    assert monitor._speedtest_due(now + timedelta(minutes=61))
    await monitor.stop()


async def test_rate_limit_postpones_scheduled_tests(tmp_path: Path) -> None:
    monitor = await started(tmp_path, FakeRouter(), tester=FakeTester(fail=RateLimited(retry_after_seconds=7200)))
    monitor.update_settings(Settings(DEFAULTS.router_url, 3600, 3600, speedtest_interval=30))
    now = datetime.now(UTC)
    monitor.start_speedtest()
    await settle()
    assert monitor.speedtest_error is not None and "Cloudflare" in monitor.speedtest_error
    assert not monitor._speedtest_due(now + timedelta(minutes=61))
    assert monitor._speedtest_due(now + timedelta(minutes=121))
    await monitor.stop()
