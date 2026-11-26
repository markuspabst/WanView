"""CSV history, one file per UTC day and kind:

    2026-11-23-status.csv   every status poll
    2026-11-23-line.csv     every line-details poll
    2026-11-23-overflow.csv every line-details poll with a 5G receiver
    2026-11-23-speedtest.csv every speed test
    2026-11-23-speedtrace.csv rates over time during each speed test (5G joining)
    2026-11-23-events.csv   changed values: reconnects, firmware, DSL mode/state, receiver state, cells

Files are not pruned (about 300 KB per day). Not thread-safe: use from the event loop.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypeVar

from wanview.domain.samples import (
    EventSample,
    LineSample,
    OverflowSample,
    SpeedTestSample,
    SpeedTraceSample,
    StatusSample,
)

_STATUS = "status"
_LINE = "line"
_OVERFLOW = "overflow"
_SPEEDTEST = "speedtest"
_EVENTS = "events"
_SPEEDTRACE = "speedtrace"


class _Sample(Protocol):
    time: datetime

    @property
    def csv_row(self) -> str: ...


SampleT = TypeVar("SampleT", StatusSample, LineSample, OverflowSample, SpeedTestSample, EventSample, SpeedTraceSample)


class DataLog:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def append_status(self, sample: StatusSample) -> None:
        self._append(sample, StatusSample.CSV_HEADER, _STATUS)

    def append_line(self, sample: LineSample) -> None:
        self._append(sample, LineSample.CSV_HEADER, _LINE)

    def append_overflow(self, sample: OverflowSample) -> None:
        self._append(sample, OverflowSample.CSV_HEADER, _OVERFLOW)

    def append_speedtest(self, sample: SpeedTestSample) -> None:
        self._append(sample, SpeedTestSample.CSV_HEADER, _SPEEDTEST)

    def append_speedtrace(self, samples: list[SpeedTraceSample]) -> None:
        for sample in samples:
            self._append(sample, SpeedTraceSample.CSV_HEADER, _SPEEDTRACE)

    def speedtrace_samples(self, since: datetime) -> list[SpeedTraceSample]:
        return self._read(_SPEEDTRACE, since, SpeedTraceSample.from_csv)

    def append_event(self, sample: EventSample) -> None:
        self._append(sample, EventSample.CSV_HEADER, _EVENTS)

    def event_samples(self, since: datetime) -> list[EventSample]:
        return self._read(_EVENTS, since, EventSample.from_csv)

    def speedtest_samples(self, since: datetime) -> list[SpeedTestSample]:
        return self._read(_SPEEDTEST, since, SpeedTestSample.from_csv)

    def status_samples(self, since: datetime) -> list[StatusSample]:
        return self._read(_STATUS, since, StatusSample.from_csv)

    def line_samples(self, since: datetime) -> list[LineSample]:
        return self._read(_LINE, since, LineSample.from_csv)

    def overflow_samples(self, since: datetime) -> list[OverflowSample]:
        return self._read(_OVERFLOW, since, OverflowSample.from_csv)

    def _append(self, sample: _Sample, header: str, kind: str) -> None:
        path = self._path(kind, sample.time)
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as file:
            if is_new:
                file.write(header + "\n")
            file.write(sample.csv_row + "\n")

    def _read(self, kind: str, since: datetime, parse: Callable[[str], SampleT | None]) -> list[SampleT]:
        """Rows from `since`'s UTC day through today; missing days are normal gaps."""
        samples: list[SampleT] = []
        day = since.astimezone(UTC).date()
        today = datetime.now(UTC).date()
        while day <= today:
            path = self._path(kind, datetime(day.year, day.month, day.day, tzinfo=UTC))
            if path.exists():
                for row in path.read_text(encoding="utf-8").splitlines()[1:]:
                    sample = parse(row)
                    if sample is not None and sample.time >= since:
                        samples.append(sample)
            day += timedelta(days=1)
        return samples

    def _path(self, kind: str, time: datetime) -> Path:
        return self.folder / f"{time.astimezone(UTC):%Y-%m-%d}-{kind}.csv"
