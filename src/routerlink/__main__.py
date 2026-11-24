"""Entry point: `python -m routerlink`. Configuration from environment variables
(see README and .env.example); `*_FILE` variants read the value from a file,
e.g. a Docker secret."""

import logging
import os
from pathlib import Path

import uvicorn

from routerlink.app.data_log import DataLog
from routerlink.app.monitor import Monitor
from routerlink.app.store import SPEEDTEST_OFF, Settings, Store
from routerlink.speedport.client import DEFAULT_BASE_URL
from routerlink.web.server import create_app

_DEFAULT_DATA_DIR = "/data"
_DEFAULT_PORT = 8080
_DEFAULT_STATUS_INTERVAL = 30
_DEFAULT_DETAILS_INTERVAL = 300


def _secret(name: str) -> str | None:
    """Value of `NAME`, or the contents of the file named by `NAME_FILE`."""
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip() or None
    return os.environ.get(name) or None


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Request logs would show every dashboard poll.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data_dir = Path(os.environ.get("DATA_DIR", _DEFAULT_DATA_DIR))
    defaults = Settings(
        router_url=os.environ.get("ROUTER_URL", DEFAULT_BASE_URL),
        status_interval=int(os.environ.get("STATUS_INTERVAL", _DEFAULT_STATUS_INTERVAL)),
        details_interval=int(os.environ.get("DETAILS_INTERVAL", _DEFAULT_DETAILS_INTERVAL)),
        speedtest_interval=int(os.environ.get("SPEEDTEST_INTERVAL", SPEEDTEST_OFF)),
    )
    data_log = DataLog(data_dir / "logs")
    monitor = Monitor(Store(data_dir, defaults), data_log, initial_password=_secret("ROUTER_PASSWORD"))
    app = create_app(monitor, data_log, dashboard_password=_secret("DASHBOARD_PASSWORD"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", _DEFAULT_PORT)), log_config=None)


if __name__ == "__main__":
    main()
