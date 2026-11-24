from datetime import UTC, datetime, timedelta
from pathlib import Path

from fakes import FakeRouter, FakeTester
from fastapi.testclient import TestClient

from routerlink.app.data_log import DataLog
from routerlink.app.monitor import Monitor
from routerlink.app.store import Settings, Store
from routerlink.domain.samples import OverflowSample
from routerlink.web.server import create_app

DEFAULTS = Settings(router_url="https://speedport.ip", status_interval=3600, details_interval=3600)


def client(tmp_path: Path, router: FakeRouter, dashboard_password: str | None = None) -> TestClient:
    data_log = DataLog(tmp_path / "logs")
    monitor = Monitor(Store(tmp_path, DEFAULTS), data_log, None, client_factory=router.client,
                      speed_tester=FakeTester())
    return TestClient(create_app(monitor, data_log, dashboard_password))


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


def test_dashboard_password_is_enforced(tmp_path: Path) -> None:
    with client(tmp_path, FakeRouter(), dashboard_password="letmein") as api:
        assert api.get("/api/state").status_code == 401
        assert api.get("/", auth=("any", "wrong")).status_code == 401
        assert api.get("/api/state", auth=("any", "letmein")).status_code == 200


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
