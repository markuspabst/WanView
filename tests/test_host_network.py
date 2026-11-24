from pathlib import Path

import pytest

from routerlink.host_network import InterfaceKind, linux_interface_kind, macos_interface_kind

ROUTES = """Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask
{iface}\t00000000\t014CA8C0\t0003\t0\t0\t0\t00000000
{iface}\t004CA8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF
"""


def fake_host(tmp_path: Path, iface: str, *markers: str) -> Path:
    (tmp_path / "proc/net").mkdir(parents=True)
    (tmp_path / "proc/net/route").write_text(ROUTES.format(iface=iface))
    device = tmp_path / "sys/class/net" / iface
    device.mkdir(parents=True)
    for marker in markers:
        (device / marker).mkdir()
    return tmp_path


def test_wifi_interface(tmp_path: Path) -> None:
    assert linux_interface_kind(fake_host(tmp_path, "wlan0", "device", "wireless")) == InterfaceKind.WIFI


def test_wired_interface(tmp_path: Path) -> None:
    assert linux_interface_kind(fake_host(tmp_path, "eth0", "device")) == InterfaceKind.WIRED


def test_virtual_interface_in_docker_bridge_is_unknown(tmp_path: Path) -> None:
    assert linux_interface_kind(fake_host(tmp_path, "eth0")) is None


def test_no_route_table_is_unknown(tmp_path: Path) -> None:
    assert linux_interface_kind(tmp_path) is None


PORTS = """
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: fc:00:00:00:00:01

Hardware Port: USB 10/100/1000 LAN
Device: en7
Ethernet Address: 00:00:00:00:00:02
"""


@pytest.mark.parametrize(("interface", "expected"), [
    ("en0", InterfaceKind.WIFI), ("en7", InterfaceKind.WIRED), ("utun3", None),
])
def test_macos_interface_kind(interface: str, expected: InterfaceKind | None) -> None:
    route = f"   route to: default\ndestination: default\n  interface: {interface}\n"
    assert macos_interface_kind(route, PORTS) == expected


def test_macos_without_default_route_is_unknown() -> None:
    assert macos_interface_kind("route: writing to routing socket: not in table\n", PORTS) is None
