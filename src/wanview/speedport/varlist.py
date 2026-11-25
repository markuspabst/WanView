"""Speedport JSON payload: a flat array of `{"vartype", "varid", "varvalue"}`
entries. `varvalue` is a string, or a nested entry list for templates."""

import json
from dataclasses import dataclass

from wanview.speedport.errors import MalformedResponse

# The UI escapes commas inside values (`decodeValue` in `jsonvariables.js`).
_ESCAPED_COMMA = "&#044;"


@dataclass(frozen=True)
class Entry:
    type: str
    id: str
    value: "str | VarList"


@dataclass(frozen=True)
class VarList:
    entries: tuple[Entry, ...]

    @classmethod
    def from_json(cls, data: bytes | str) -> "VarList":
        try:
            parsed = json.loads(data)
        except ValueError as error:
            raise MalformedResponse(f"invalid JSON ({error})") from error
        return cls._parse(parsed)

    @classmethod
    def _parse(cls, parsed: object) -> "VarList":
        if not isinstance(parsed, list):
            raise MalformedResponse("expected a variable list")
        entries = []
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("varid"), str):
                continue
            raw = item.get("varvalue")
            if isinstance(raw, list):
                value: str | VarList = cls._parse(raw)
            elif isinstance(raw, str):
                value = raw.replace(_ESCAPED_COMMA, ",")
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                value = str(raw)
            else:
                value = ""
            entries.append(Entry(type=str(item.get("vartype", "")), id=item["varid"], value=value))
        return cls(tuple(entries))

    def string(self, var_id: str) -> str | None:
        """First string value for `var_id`, like `getVar` in the web UI."""
        for entry in self.entries:
            if entry.id == var_id and isinstance(entry.value, str):
                return entry.value
        return None

    def lists(self, var_id: str) -> list["VarList"]:
        """All nested lists with `var_id` (repeated template rows)."""
        return [entry.value for entry in self.entries if entry.id == var_id and isinstance(entry.value, VarList)]
