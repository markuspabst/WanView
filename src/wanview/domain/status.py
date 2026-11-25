"""Values from `/data/Status.json` (no login) and router model detection."""

import re
from dataclasses import dataclass

from wanview.speedport.varlist import VarList

_ENABLED = "1"
_ONLINE = "online"
# Reported by the receiver when there is no measurement.
_NO_SIGNAL = 0.0

SUPPORTED_MODEL_NAMES = ("Speedport Smart 4",)
_SMART4_PREFIX = "Speedport Smart 4"
_TITLE_SEPARATOR = " - "
_UNKNOWN_PRODUCT = "unknown device"
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class LineStatus:
    """Rates in bit/s; signal levels in dBm as reported by the external 5G receiver."""

    online_status: str
    dsl_link_status: str
    dsl_downstream: int | None
    dsl_upstream: int | None
    internet_download: int | None
    internet_upload: int | None
    online_since: str | None
    """Router local time, as shown in the web UI."""
    firmware_version: str | None
    serial_number: str | None
    """Identifies the device; a device password is only valid for one router."""
    uses_mobile: bool
    uses_bonding: bool
    nr_signal: float | None
    nr_band: str | None
    lte_signal: float | None
    lte_band: str | None
    receiver_model_name: str | None
    """e.g. "5G-EmpfaengerSE"; None when no receiver is reported."""
    title: str | None

    @classmethod
    def from_vars(cls, vars: VarList) -> "LineStatus":
        link = vars.string("dsl_link_status") or ""
        return cls(
            online_status=vars.string("onlinestatus") or "",
            dsl_link_status=link,
            dsl_downstream=_sync_rate(vars.string("dsl_downstream"), link),
            dsl_upstream=_sync_rate(vars.string("dsl_upstream"), link),
            internet_download=_int(vars.string("inet_download")),
            internet_upload=_int(vars.string("inet_upload")),
            online_since=_non_empty(vars.string("inet_uptime")),
            firmware_version=_non_empty(vars.string("firmware_version")),
            serial_number=_non_empty(vars.string("serial_number")),
            uses_mobile=vars.string("use_lte") == _ENABLED,
            uses_bonding=vars.string("use_bonding") == _ENABLED,
            nr_signal=_signal(vars.string("ex5g_signal_5g")),
            nr_band=_non_empty(vars.string("ex5g_freq_5g")),
            lte_signal=_signal(vars.string("ex5g_signal_lte")),
            lte_band=_non_empty(vars.string("ex5g_freq_lte")),
            receiver_model_name=_non_empty(vars.string("ex5g_model_name")),
            title=_non_empty(vars.string("title")),
        )

    @property
    def has_receiver(self) -> bool:
        """Hybrid enabled and a receiver reported; `uses_mobile` alone stays on while unplugged."""
        return self.uses_mobile and self.receiver_model_name is not None


@dataclass(frozen=True)
class RouterModel:
    """Router identified at the configured address. Detection order: the `title` of
    `Status.json` (Speedport Smart family, no login), else the HTML `<title>` of `/`."""

    name: str
    supported: bool

    @classmethod
    def from_status_title(cls, title: str) -> "RouterModel":
        return cls._for_product(title.split(_TITLE_SEPARATOR, 1)[0].strip())

    @classmethod
    def from_html(cls, html: str) -> "RouterModel":
        match = _HTML_TITLE.search(html)
        title = match.group(1).strip() if match else ""
        return cls._for_product(title.split(_TITLE_SEPARATOR, 1)[0].strip())

    @classmethod
    def _for_product(cls, product: str) -> "RouterModel":
        if product.startswith(_SMART4_PREFIX):
            # Typ A/B and Plus share the web UI and engineer pages.
            return cls(name=_SMART4_PREFIX, supported=True)
        return cls(name=product or _UNKNOWN_PRODUCT, supported=False)


def _sync_rate(raw: str | None, link_status: str) -> int | None:
    """The router occasionally reports sync 0 while the link is online; such a
    contradictory reading is no value. With the link down, 0 is real and kept."""
    rate = _int(raw)
    if rate == 0 and link_status == _ONLINE:
        return None
    return rate


def _signal(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return None if value is None or value == _NO_SIGNAL else value


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _non_empty(value: str | None) -> str | None:
    return value or None
