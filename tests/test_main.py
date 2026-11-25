import os
from pathlib import Path

import pytest

from wanview.__main__ import ensure_writable, listen_host, warn_removed_variables


def test_listens_on_all_interfaces_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST", raising=False)
    assert listen_host() == "0.0.0.0"


def test_host_limits_listening_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "127.0.0.1")
    assert listen_host() == "127.0.0.1"


def test_removed_dashboard_password_variable_is_warned(monkeypatch: pytest.MonkeyPatch,
                                                       caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("DASHBOARD_PASSWORD", "letmein")
    warn_removed_variables()
    assert "DASHBOARD_PASSWORD is no longer used" in caplog.text
    assert "letmein" not in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_unwritable_data_folder_exits_with_a_hint(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(SystemExit):
            ensure_writable(tmp_path)
    finally:
        tmp_path.chmod(0o700)
    assert f"Cannot write to {tmp_path}" in caplog.text
    assert "user:" in caplog.text
