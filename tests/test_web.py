from datetime import UTC, datetime, timedelta
import os
import stat
from pathlib import Path

from fakes import FakeRouter, FakeTester
from fastapi.testclient import TestClient

from wanview.app.access import DashboardAccess
from wanview.app.data_log import DataLog
from wanview.app.monitor import Monitor
from wanview.app.store import Settings, Store
from wanview.domain.line import DSLDetails
from wanview.domain.samples import EventKind, EventSample, LineSample, OverflowSample, SpeedTestSample, StatusSample
from wanview.domain.status import LineStatus
from wanview.speedport.engineer_page import EngineerPage
from wanview.speedport.varlist import VarList
from wanview.web.server import create_app

DEFAULTS = Settings(router_url="https://speedport.ip", status_interval=3600, details_interval=3600)


def client(tmp_path: Path, router: FakeRouter) -> TestClient:
    data_log = DataLog(tmp_path / "logs")
    store = Store(tmp_path, DEFAULTS)
    monitor = Monitor(store, data_log, None, client_factory=router.client, speed_tester=FakeTester())
    return TestClient(create_app(monitor, data_log, DashboardAccess(store)))


def test_saving_password_sets_up_router_and_reads_details(tmp_path: Path) -> None:
    router = FakeRouter()
    with client(tmp_path, router) as api:
        assert api.get("/api/state").json()["router"]["new"]
        response = api.put("/api/password", json={"password": "secret"})
        assert response.status_code == 204
        state = api.get("/api/state").json()
        assert state["router"]["hasPassword"]
        assert ("secret", "dsl_hidden_status.stm") in router.engineer_reads


def test_actions_run_on_the_event_loop(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        api.get("/api/state")
        for path in ("/api/pause", "/api/resume"):
            assert api.post(path).status_code == 204
        assert api.put("/api/settings", json={"routerUrl": "https://192.168.2.1", "statusInterval": 30,
                                              "detailsInterval": 300}).status_code == 204


def test_settings_reject_bad_router_address(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        for url in ("ftp://speedport.ip", "https://speedport.ip/admin", "speedport.ip"):
            body = {"routerUrl": url, "statusInterval": 30, "detailsInterval": 300}
            assert api.put("/api/settings", json=body).status_code == 422
        body = {"routerUrl": "https://speedport.ip", "statusInterval": 5, "detailsInterval": 300}
        assert api.put("/api/settings", json=body).status_code == 422


def test_dashboard_asks_for_access_choice_on_first_open(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        assert api.get("/api/state").json()["dashboardAccess"] == "unset"
        assert api.put("/api/dashboard-access", json={"password": None}).status_code == 204
        assert api.get("/api/state").json()["dashboardAccess"] == "open"


def test_dashboard_password_set_on_first_open_is_enforced(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        assert api.put("/api/dashboard-access", json={"password": "letmein"}).status_code == 204
        assert api.get("/api/state").status_code == 401
        assert api.get("/", auth=("any", "wrong")).status_code == 401
        state = api.get("/api/state", auth=("any", "letmein")).json()
        assert state["dashboardAccess"] == "password"


def test_dashboard_password_survives_restart_and_is_stored_hashed(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        api.put("/api/dashboard-access", json={"password": "letmein"})
    stored = tmp_path / "dashboard.json"
    assert "letmein" not in stored.read_text()
    assert stat.S_IMODE(os.stat(stored).st_mode) == 0o600
    with client(tmp_path, FakeRouter()) as api:
        assert api.get("/api/state").status_code == 401
        assert api.get("/api/state", auth=("any", "letmein")).status_code == 200


def test_dashboard_password_change_needs_current_login(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        api.put("/api/dashboard-access", json={"password": "old"})
        assert api.put("/api/dashboard-access", json={"password": "new"}).status_code == 401
        assert api.put("/api/dashboard-access", json={"password": "new"}, auth=("any", "old")).status_code == 204
        assert api.get("/api/state", auth=("any", "old")).status_code == 401
        assert api.put("/api/dashboard-access", json={"password": None}, auth=("any", "new")).status_code == 204
        assert api.get("/api/state").status_code == 200


def test_dashboard_password_must_not_be_empty(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        assert api.put("/api/dashboard-access", json={"password": ""}).status_code == 422
        assert api.get("/api/state").json()["dashboardAccess"] == "unset"


def test_history_includes_all_logged_values(tmp_path: Path) -> None:
    data_log = DataLog(tmp_path / "logs")
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    for minutes, band, fec in ((0, "n78", 1000), (30, "n28", 1600)):
        time = start + timedelta(minutes=minutes)
        status = LineStatus.from_vars(VarList.from_json(
            '[{"varid":"onlinestatus","varvalue":"online"},{"varid":"inet_download","varvalue":"250000"},'
            f'{{"varid":"inet_upload","varvalue":"40000"}},{{"varid":"ex5g_freq_5g","varvalue":"{band}"}}]'))
        data_log.append_status(StatusSample.from_status(time, status))
        dsl = DSLDetails.from_page(EngineerPage(f'downstream_status[4] = " 12.5 dB";\ndownstream_status[10] = "{fec}";'))
        data_log.append_line(LineSample.from_details(time, dsl, None))
    data_log.append_speedtest(SpeedTestSample(start, 20.0, 300_000_000, 50_000_000, 80_000_000, 5_000_000))
    data_log.append_event(EventSample(start, EventKind.ONLINE_SINCE, "2026-11-24 10:00:00"))

    with client(tmp_path, FakeRouter()) as api:
        h = api.get("/api/history?range=day").json()
    assert h["status"][0][6:] == [250_000, 40_000]
    assert h["line"][0][7] == 12.5
    assert h["fec"][0][1] == 1200.0                # 600 corrected errors in 30 min
    assert h["speedtest"][0][5:] == [80_000_000, 5_000_000]
    events = [row[1:] for row in h["events"] if row[0] <= _iso(start + timedelta(minutes=30))]
    assert events == [["online_since", "2026-11-24 10:00:00"], ["nr_band", "n28"]]


def _iso(time: datetime) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_dashboard_files_are_revalidated_on_every_load(tmp_path: Path) -> None:
    # Without this, browsers keep an old app.js after an update (the tabs then did nothing).
    with client(tmp_path, FakeRouter()) as api:
        for path in ("/", "/static/app.js", "/static/style.css"):
            assert api.get(path).headers.get("cache-control") == "no-cache"


def test_speed_test_endpoint_and_interval_validation(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter()) as api:
        assert api.post("/api/speedtest").status_code == 202
        body = {"routerUrl": "https://speedport.ip", "statusInterval": 30, "detailsInterval": 300}
        assert api.put("/api/settings", json=body | {"speedtestInterval": 10}).status_code == 422
        assert api.put("/api/settings", json=body | {"speedtestInterval": 60}).status_code == 204
        assert api.get("/api/state").json()["settings"]["speedtestInterval"] == 60


def test_history_includes_bonding_state(tmp_path: Path) -> None:
    DataLog(tmp_path / "logs").append_overflow(OverflowSample(
        datetime.now(UTC) - timedelta(minutes=5), 300.0, True, False, 84716, 30121, 1, 1, 2_000_000, 100_000))
    with client(tmp_path, FakeRouter()) as api:
        row = api.get("/api/history?range=day").json()["overflow"][0]
        assert row[1:] == [2_000_000, 100_000, True, False, 84_716_000, 30_121_000]


def test_history_has_outages_line_details_and_crc_rate(tmp_path: Path) -> None:
    data_log = DataLog(tmp_path / "logs")
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    for minutes, crc, online in ((0, 100, "online"), (30, 110, "offline")):
        time = start + timedelta(minutes=minutes)
        dsl = DSLDetails.from_page(EngineerPage(f'downstream_status[1] = " 99367 Kbps";\ndownstream_status[8] = "{crc}";'))
        data_log.append_line(LineSample.from_details(time, dsl, None))
        status = LineStatus.from_vars(VarList.from_json(f'[{{"varid":"onlinestatus","varvalue":"{online}"}}]'))
        data_log.append_status(StatusSample.from_status(time, status))
    with client(tmp_path, FakeRouter()) as api:
        h = api.get("/api/history?range=month").json()
        # The two logged polls (the fake router's own status poll at startup comes after them).
        assert [row[5] for row in h["status"][:2]] == [True, False]
        assert h["line"][0][3] == 99_367_000
        assert h["crc"][0][1] == 20.0          # 10 errors in 30 min
        assert len(h["normalSync"]) == 2
