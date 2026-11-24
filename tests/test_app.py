import asyncio
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakeRouter, FakeTester
import httpx

from routerlink.app.data_log import DataLog
from routerlink.app.monitor import Monitor
from routerlink.app.store import Choice, Settings, Store
from routerlink.domain.samples import HostLink, StatusSample
from routerlink.host_network import InterfaceKind
from routerlink.domain.status import LineStatus
from routerlink.speedport.varlist import VarList

DEFAULTS = Settings(router_url="https://speedport.ip", status_interval=3600, details_interval=3600)


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def started(tmp_path: Path, router: FakeRouter, initial_password: str | None = None,
                  store: Store | None = None, tester: FakeTester | None = None,
                  host_kind: InterfaceKind | None = None) -> Monitor:
    store = store or Store(tmp_path, DEFAULTS)
    monitor = Monitor(store, DataLog(tmp_path / "logs"), initial_password, client_factory=router.client,
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
    assert DataLog(tmp_path / "logs").status_samples(datetime.now(UTC) - timedelta(minutes=1)) != []  # before ignoring
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
    logged = DataLog(tmp_path / "logs").speedtest_samples(sample.time - timedelta(seconds=1))
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
