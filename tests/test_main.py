import pytest

from wanview.__main__ import listen_host


def test_listens_on_all_interfaces_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST", raising=False)
    assert listen_host() == "0.0.0.0"


def test_host_limits_listening_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "127.0.0.1")
    assert listen_host() == "127.0.0.1"
