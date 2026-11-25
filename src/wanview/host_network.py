"""The link type of this host's own network connection.

Linux:
    /proc/net/route                       default route (destination 00000000) -> interface
    /sys/class/net/<iface>/device         present for physical network cards only
    /sys/class/net/<iface>/wireless       present for Wi-Fi cards (also phy80211)

Inside Docker's default bridge network the container only sees a virtual interface
(no `device`): the result is then unknown. With `network_mode: host` the host's own
interfaces are visible.

macOS (development):
    route -n get default                  "interface: en0"
    networksetup -listallhardwareports    "Hardware Port: Wi-Fi" / "Device: en0" blocks
"""

import re
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

_ROUTE_TABLE = Path("proc/net/route")
_NET_CLASS = Path("sys/class/net")
_DEFAULT_DESTINATION = "00000000"
_WIFI_MARKERS = ("wireless", "phy80211")
_MACOS = "darwin"
_MACOS_WIFI_PORT = "Wi-Fi"
_COMMAND_TIMEOUT_SECONDS = 5
_ROUTE_INTERFACE = re.compile(r"^\s*interface:\s*(\S+)", re.MULTILINE)
_PORT_BLOCK = re.compile(r"Hardware Port:\s*(.+)\nDevice:\s*(\S+)")


class InterfaceKind(StrEnum):
    WIRED = "wired"
    WIFI = "wifi"


def default_interface_kind() -> InterfaceKind | None:
    """Kind of the interface carrying the default route; None when unknown or virtual."""
    if sys.platform == _MACOS:
        return _macos_interface_kind()
    return linux_interface_kind(Path("/"))


def linux_interface_kind(root: Path) -> InterfaceKind | None:
    """From /proc and /sys below `root`."""
    interface = _default_interface(root)
    if interface is None:
        return None
    device_dir = root / _NET_CLASS / interface
    if any((device_dir / marker).exists() for marker in _WIFI_MARKERS):
        return InterfaceKind.WIFI
    if (device_dir / "device").exists():
        return InterfaceKind.WIRED
    return None


def _default_interface(root: Path) -> str | None:
    try:
        lines = (root / _ROUTE_TABLE).read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 1 and fields[1] == _DEFAULT_DESTINATION:
            return fields[0]
    return None


def _macos_interface_kind() -> InterfaceKind | None:
    try:
        route = _run(["/sbin/route", "-n", "get", "default"])
        ports = _run(["/usr/sbin/networksetup", "-listallhardwareports"])
    except (OSError, subprocess.SubprocessError):
        return None
    return macos_interface_kind(route, ports)


def macos_interface_kind(route_output: str, ports_output: str) -> InterfaceKind | None:
    """From `route -n get default` and `networksetup -listallhardwareports` output.
    Interfaces not listed as hardware ports (VPN tunnels, bridges) are unknown."""
    match = _ROUTE_INTERFACE.search(route_output)
    if match is None:
        return None
    for port, device in _PORT_BLOCK.findall(ports_output):
        if device == match.group(1):
            return InterfaceKind.WIFI if port.strip() == _MACOS_WIFI_PORT else InterfaceKind.WIRED
    return None


def _run(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=_COMMAND_TIMEOUT_SECONDS).stdout
