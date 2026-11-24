"""Values embedded as JavaScript assignments in the engineer menu pages
(`/engineer/html/*.stm`, login required). The router renders e.g.:

    var Receiver_RSRP_5G = "-";        <- placeholder declaration
    Receiver_RSRP_5G = "-103.0";       <- actual value, later in the page
    downstream_status[2] = " 8.2 dB";
    inte_face_tx[1][1] = 3253603667;   <- unquoted number, two indices

Later assignments win, matching what the browser would evaluate.
"""

import re

_ASSIGNMENT = re.compile(
    r'^\s*(?:var\s+)?([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)\s*=\s*(?:"([^"]*)"|(-?\d+(?:\.\d+)?))\s*;?\s*$',
    re.MULTILINE,
)
_INDEX = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class EngineerPage:
    def __init__(self, html: str) -> None:
        self._scalars: dict[str, str] = {}
        self._arrays: dict[str, dict[tuple[int, ...], str]] = {}
        for match in _ASSIGNMENT.finditer(html):
            name, index_text, quoted, number = match.groups()
            value = (quoted if quoted is not None else number or "").strip()
            indices = tuple(int(index) for index in _INDEX.findall(index_text))
            if indices:
                self._arrays.setdefault(name, {})[indices] = value
            else:
                self._scalars[name] = value

    def string(self, name: str) -> str | None:
        return self._scalars.get(name)

    def element(self, name: str, *indices: int) -> str | None:
        return self._arrays.get(name, {}).get(indices)


def number(value: str | None) -> float | None:
    """Leading number of a value such as `" 8.2 dB"` or `"90000 Kbps"`; None for `"-"` or empty."""
    if value is None:
        return None
    match = _NUMBER.search(value)
    return float(match.group(0)) if match else None


def integer(value: str | None) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None
