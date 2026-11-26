"""Polls the router, logs every poll and holds the latest readings for the web UI.

- The router is identified first (no login): model from `Status.json`, serial
  number for the password. Only a supported, set-up router is polled with login.
- `Status.json` is polled every `status_interval` without login.
- Engineer pages (DSL details, 5G receiver, bonding, LAN counters) need the device
  password; they are polled every `details_interval` with one login + logout per
  poll, because the router allows one session and would lock out the browser.
- LAN traffic is polled every `TRAFFIC_INTERVAL` only while a dashboard is open
  (it calls `keep_traffic_alive`); the session is held for that time.
- While paused or while the router restarts, nothing is polled.
- Logs and interval settings belong to the identified router (its serial number); a
  router swapped at the same address is identified again before any login.

Thread-safety: use from one asyncio event loop only.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx

from wanview.app.data_log import DataLog
from wanview.app.store import Choice, KnownRouter, Settings, Store
from wanview.domain.line import DSL_PAGE, RECEIVER_PAGE, DSLDetails, ReceiverStatus
from wanview.domain.samples import (
    ErrorRate,
    EventKind,
    EventSample,
    LineSample,
    NormalSync,
    OverflowSample,
    SpeedTestSample,
    SpeedTraceSample,
    StatusSample,
    HostLink,
    details_events,
    link_from_router_ports,
    status_events,
)
from wanview.domain.status import LineStatus, RouterModel
from wanview.domain.traffic import (
    BONDING_PAGE,
    LAN_PAGE,
    BondingStatus,
    HybridTrafficRate,
    Direction,
    LANCounters,
    LANTrafficRate,
    phase_trace,
)
from wanview.speedport.client import REBOOT_SECONDS, SpeedportClient
from wanview.speedport.errors import SpeedportError, connection_problem
from wanview.host_network import InterfaceKind, default_interface_kind
from wanview.speedtest import RateLimited, SpeedTester

log = logging.getLogger(__name__)

TRAFFIC_INTERVAL = 5
_STATUS_SOURCE = "Status"
# Live traffic stops when no dashboard asked for it for this long.
_TRAFFIC_KEEPALIVE_SECONDS = 15
# An overflow or CRC reference older than this many details intervals is not used.
_REFERENCE_INTERVALS = 3
_NETWORK_ERRORS = (httpx.HTTPError, SpeedportError)
# The normal sync rate is the median of this window, recomputed hourly from the log.
_NORMAL_SYNC_WINDOW = timedelta(days=7)
_NORMAL_SYNC_REFRESH_SECONDS = 3600
# The schedule checks this often whether an automatic speed test is due.
_SPEEDTEST_CHECK_SECONDS = 60
_SECONDS_PER_MINUTE = 60
# The last result shown after a restart comes from the log of this window.
_SPEEDTEST_HISTORY = timedelta(days=7)
_BITS_PER_MEGABIT = 1_000_000
_COUNTER_POLL_PAUSE_SECONDS = 0.2
# Last logged value per event kind is looked up this far back after a restart.
_EVENT_MEMORY = timedelta(days=30)
_HOST_LINKS = {InterfaceKind.WIRED: HostLink.WIRED, InterfaceKind.WIFI: HostLink.WIFI}


@dataclass
class Readings:
    status: LineStatus | None = None
    dsl: DSLDetails | None = None
    receiver: ReceiverStatus | None = None
    bonding: BondingStatus | None = None
    overflow: OverflowSample | None = None
    crc_rate: ErrorRate | None = None
    fec_rate: ErrorRate | None = None
    traffic: LANTrafficRate | None = None
    status_time: datetime | None = None
    details_time: datetime | None = None
    status_error: str | None = None
    details_error: str | None = None
    traffic_error: str | None = None


class Monitor:
    def __init__(self, store: Store, initial_password: str | None,
                 client_factory: Callable[[str, str | None], SpeedportClient] = SpeedportClient,
                 speed_tester: SpeedTester | None = None,
                 host_interface_kind: Callable[[], InterfaceKind | None] = default_interface_kind) -> None:
        """`client_factory(base_url, password)` creates router clients; it, `speed_tester` and
        `host_interface_kind` are replaceable in tests."""
        self._make_client = client_factory
        self._host_interface_kind = host_interface_kind
        self._speed_tester = speed_tester or SpeedTester()
        self._store = store
        # Logs of the identified router; nothing is logged before identification.
        self.data_log: DataLog | None = None
        self._initial_password = initial_password
        self.settings: Settings = store.settings()
        self.routers: list[KnownRouter] = store.routers()
        self.readings = Readings()
        self.model: RouterModel | None = None
        self.serial: str | None = None
        self.paused = False
        self.router_back_time: datetime | None = None
        self.normal_sync = NormalSync(None, None)
        self._normal_sync_due = 0.0
        self.last_speedtest: SpeedTestSample | None = None
        # Rates over time during the last speed test: shows when 5G joined.
        self.last_speedtrace: list[SpeedTraceSample] = []
        self.speedtest_error: str | None = None
        # Scheduled tests count from the last attempt, so failures are not retried every minute.
        self._last_speedtest_attempt: datetime | None = None
        self._speedtest_blocked_until: datetime | None = None
        self._speedtest_task: asyncio.Task[None] | None = None
        self._schedule_task: asyncio.Task[None] | None = None
        self._client = client_factory(self.settings.router_url, None)
        self._poll_task: asyncio.Task[None] | None = None
        self._traffic_task: asyncio.Task[None] | None = None
        self._traffic_requested_at = 0.0
        self._last_counters: tuple[LANCounters, float] | None = None
        self._last_line: LineSample | None = None
        # Last value per event kind of the identified router, so only changes are logged.
        self._event_values: dict[EventKind, str] = {}

    # Lifecycle

    def start(self) -> None:
        self._restart()
        self._schedule_task = asyncio.get_running_loop().create_task(self._speedtest_schedule())

    async def stop(self) -> None:
        for task in (self._poll_task, self._traffic_task, self._speedtest_task, self._schedule_task):
            if task:
                task.cancel()
        await self._client.logout()
        await self._client.close()

    # Router identity

    @property
    def current_router(self) -> KnownRouter | None:
        return next((router for router in self.routers if router.serial_number == self.serial), None)

    @property
    def is_supported(self) -> bool:
        return self.model is not None and self.model.supported

    @property
    def is_new_router(self) -> bool:
        return self.is_supported and self.serial is not None and self.current_router is None

    @property
    def is_ignored(self) -> bool:
        router = self.current_router
        return self.is_supported and router is not None and router.choice == Choice.IGNORED

    @property
    def has_password(self) -> bool:
        """True only for a set-up router with a stored password."""
        return self._client.has_password

    @property
    def can_restart_router(self) -> bool:
        return self.has_password and self.is_supported and self.router_back_time is None

    def set_choice(self, serial: str, choice: Choice) -> None:
        """Sets up or ignores a router; adds the current router if new."""
        index = next((i for i, router in enumerate(self.routers) if router.serial_number == serial), None)
        if index is not None:
            self.routers[index] = replace(self.routers[index], choice=choice)
        elif serial == self.serial and self.model is not None:
            self.routers.append(Store.new_router(serial, self.model.name, choice))
        else:
            return
        self._store.save_routers(self.routers)
        log.info("Router %s: %s", serial[-4:], choice.value)
        if serial == self.serial:
            if choice == Choice.IGNORED:
                self._clear_readings()
            self._restart()

    def remove_router(self, serial: str) -> None:
        """Forgets a router and its password; it is offered for setup again when detected."""
        self._store.delete_password(serial)
        self.routers = [router for router in self.routers if router.serial_number != serial]
        self._store.save_routers(self.routers)
        log.info("Router %s removed", serial[-4:])
        if serial == self.serial:
            self._clear_readings()
            self._restart()

    def save_password(self, password: str) -> None:
        """Stores the password for the current router and sets it up."""
        if self.serial is None:
            return
        self._store.save_password(self.serial, password)
        self.readings.details_error = None
        self.set_choice(self.serial, Choice.SET_UP)

    def delete_password(self) -> None:
        if self.serial is None:
            return
        self._store.delete_password(self.serial)
        self._clear_details()
        self._restart()

    def update_settings(self, settings: Settings) -> None:
        """The intervals belong to the identified router; the address is global."""
        self._store.save_settings(settings, self.serial)
        if settings.router_url != self.settings.router_url:
            # Unknown until identified at the new address.
            self._forget_router()
        self.settings = settings
        self._restart()

    def _adopt_initial_password(self) -> None:
        """The password from the environment belongs to the first router identified
        while none is known yet (the user configured it for the router at this address)."""
        if self._initial_password and not self.routers and self.serial and self.model:
            self._store.save_password(self.serial, self._initial_password)
            self.routers.append(Store.new_router(self.serial, self.model.name, Choice.SET_UP))
            self._store.save_routers(self.routers)
            log.info("Initial password assigned to router %s", self.serial[-4:])

    def _new_client(self) -> None:
        """Loads the password of the current router, only if it is set up."""
        router = self.current_router
        password = None
        if router is not None and router.choice == Choice.SET_UP and self.serial is not None:
            password = self._store.password(self.serial)
        old = self._client
        self._client = self._make_client(self.settings.router_url, password)
        asyncio.get_running_loop().create_task(_close_quietly(old))

    # Pause and router restart

    def pause(self) -> None:
        if self.paused:
            return
        log.info("Monitoring paused")
        self.paused = True
        self._cancel_polling()
        self._stop_traffic()
        # A speed test may hold the router login for the DSL / 5G split.
        if self._speedtest_task is not None:
            self._speedtest_task.cancel()
        self._logout_soon()

    def resume(self) -> None:
        if not self.paused:
            return
        log.info("Monitoring resumed")
        self.paused = False
        self._restart()

    def restart_router(self) -> None:
        """Restarts the router; polling resumes once it is back, unless paused."""
        if not self.can_restart_router:
            return
        log.info("Router restart requested")
        self.router_back_time = datetime.now(UTC) + timedelta(seconds=REBOOT_SECONDS)
        self.readings.status_error = None
        self.readings.details_error = None
        self._cancel_polling()
        self._stop_traffic()
        asyncio.get_running_loop().create_task(self._perform_router_restart())

    async def _perform_router_restart(self) -> None:
        try:
            await self._client.reboot()
            await asyncio.sleep(REBOOT_SECONDS)
        except _NETWORK_ERRORS as error:
            problem = connection_problem(error, self.settings.router_url)
            log.error("Router restart failed: %s", problem)
            self.readings.details_error = f"Router restart failed: {problem}"
        self.router_back_time = None
        self._restart()

    # Speed test

    @property
    def speedtest_running(self) -> bool:
        return self._speedtest_task is not None

    def start_speedtest(self) -> bool:
        """False if a test is running, monitoring is paused, the router restarts or no router
        is identified (results are stored with the router)."""
        if self.speedtest_running or self.paused or self.router_back_time is not None or self.data_log is None:
            return False
        log.info("Speed test started")
        self._last_speedtest_attempt = datetime.now(UTC)
        self._speedtest_task = asyncio.get_running_loop().create_task(self._run_speedtest())
        return True

    async def _speedtest_schedule(self) -> None:
        while True:
            await asyncio.sleep(_SPEEDTEST_CHECK_SECONDS)
            if self._speedtest_due(datetime.now(UTC)):
                self.start_speedtest()

    def _speedtest_due(self, now: datetime) -> bool:
        interval = self.settings.speedtest_interval
        if not interval:
            return False
        if self._speedtest_blocked_until is not None and now < self._speedtest_blocked_until:
            return False
        last = self._last_speedtest_attempt
        return last is None or now - last >= timedelta(minutes=interval)

    async def _run_speedtest(self) -> None:
        # LAN counters per phase: DSL / 5G split (with receiver) and whether the host is
        # wired or on Wi-Fi. Needs the router login.
        read_counters = self._may_use_login()
        has_receiver = self.readings.status is not None and self.readings.status.has_receiver
        readings: dict[tuple[str, str], LANCounters] = {}
        # Every reading per phase, stamped with the middle of its request, for the rate over time.
        series: dict[str, list[tuple[float, LANCounters]]] = {}
        pollers: dict[str, asyncio.Task[None]] = {}

        async def read(phase: str) -> LANCounters:
            before = time.monotonic()
            counters = LANCounters.from_page(await self._client.fetch_engineer_page(LAN_PAGE))
            series[phase].append(((before + time.monotonic()) / 2, counters))
            return counters

        async def poll(phase: str) -> None:
            # As often as the router answers (about once a second), with a pause so a fast
            # answer does not turn into a busy loop.
            while True:
                await read(phase)
                await asyncio.sleep(_COUNTER_POLL_PAUSE_SECONDS)

        async def around_phase(phase: str, edge: str) -> None:
            if not read_counters:
                return
            if edge == "start":
                series[phase] = []
                readings[(phase, edge)] = await read(phase)
                pollers[phase] = asyncio.get_running_loop().create_task(poll(phase))
                return
            pollers[phase].cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pollers[phase]
            readings[(phase, edge)] = await read(phase)

        def trace(test_time: datetime, phase: str, byte_trace: tuple[tuple[float, int], ...],
                  direction: Direction) -> list[SpeedTraceSample]:
            if not byte_trace:
                return []
            points = phase_trace(byte_trace, series.get(phase, []) if has_receiver else [], direction)
            return [SpeedTraceSample(test_time, direction, point) for point in points]

        def phase_rate(phase: str, seconds: float) -> LANTrafficRate | None:
            """Port rates during a phase. The readings enclose the phase, so they hold all its
            bytes; dividing by the time between them instead of the phase's duration would
            understate the rates by the seconds each reading takes."""
            if (phase, "start") not in readings or (phase, "end") not in readings:
                return None
            return LANTrafficRate.between(readings[(phase, "start")], readings[(phase, "end")], seconds)

        def mobile_bps(rate: LANTrafficRate | None, direction: str, total_bps: int) -> int | None:
            if rate is None or not has_receiver:
                return None
            hybrid = HybridTrafficRate.from_lan(rate)
            mobile = hybrid.mobile_down_bps if direction == "down" else hybrid.mobile_up_bps
            # Other traffic on the receiver port during the test can push it above the total.
            return None if mobile is None else min(mobile, total_bps)

        def wired_bps(rate: LANTrafficRate | None, direction: str) -> int | None:
            if rate is None:
                return None
            ports = HybridTrafficRate.from_lan(rate).client_ports if has_receiver else rate.ports
            return sum((port.down_bps if direction == "down" else port.up_bps) or 0 for port in ports)

        try:
            measurement = await self._speed_tester.run(around_phase)
            down_rate = phase_rate("download", measurement.down_seconds)
            up_rate = phase_rate("upload", measurement.up_seconds)
            # The host's own interface is reliable; the router's port counters cannot see Wi-Fi
            # behind a mesh access point on a LAN port.
            kind = self._host_interface_kind()
            link = _HOST_LINKS[kind] if kind else link_from_router_ports(
                measurement.down_bps, measurement.up_bps, wired_bps(down_rate, "down"), wired_bps(up_rate, "up"))
            sample = SpeedTestSample(datetime.now(UTC), measurement.latency_ms, measurement.down_bps,
                                     measurement.up_bps, mobile_bps(down_rate, "down", measurement.down_bps),
                                     mobile_bps(up_rate, "up", measurement.up_bps), link)
            self.last_speedtest = sample
            self.last_speedtrace = (trace(sample.time, "download", measurement.down_trace, Direction.DOWN)
                                    + trace(sample.time, "upload", measurement.up_trace, Direction.UP))
            self.speedtest_error = None
            if self.data_log is not None:
                self.data_log.append_speedtest(sample)
                self.data_log.append_speedtrace(self.last_speedtrace)
            log.info("Speed test: %.0f / %.0f Mbit/s, %.0f ms, host %s", sample.down_bps / _BITS_PER_MEGABIT,
                     sample.up_bps / _BITS_PER_MEGABIT, sample.latency_ms, link.value if link else "unknown")
        except RateLimited as error:
            # Cloudflare's limit for automated tests: wait as long as it asks.
            log.warning("Speed test refused: %s", error)
            self._speedtest_blocked_until = datetime.now(UTC) + timedelta(seconds=error.retry_after_seconds)
            self.speedtest_error = f"Speed test refused: {error}."
        except _NETWORK_ERRORS as error:
            log.error("Speed test failed: %s", error)
            self.speedtest_error = f"Speed test failed: {error}"
        finally:
            for poller in pollers.values():
                poller.cancel()
            self._speedtest_task = None
            if read_counters and self._traffic_task is None:
                await self._client.logout()

    # Live traffic

    def keep_traffic_alive(self) -> None:
        """Called by every dashboard poll; traffic stops shortly after the last one."""
        self._traffic_requested_at = time.monotonic()
        if self._traffic_task is None and self._may_use_login() and not self.paused and self.router_back_time is None:
            log.info("Live traffic started")
            self._traffic_task = asyncio.get_running_loop().create_task(self._traffic_loop())

    async def _traffic_loop(self) -> None:
        try:
            while time.monotonic() - self._traffic_requested_at < _TRAFFIC_KEEPALIVE_SECONDS:
                try:
                    counters = LANCounters.from_page(await self._client.fetch_engineer_page(LAN_PAGE))
                    now = time.monotonic()
                    if self._last_counters is not None:
                        old, then = self._last_counters
                        self.readings.traffic = LANTrafficRate.between(old, counters, now - then)
                    self._last_counters = (counters, now)
                    self.readings.traffic_error = None
                except _NETWORK_ERRORS as error:
                    problem = connection_problem(error, self.settings.router_url)
                    log.error("LAN traffic poll failed: %s", problem)
                    self.readings.traffic_error = problem
                await asyncio.sleep(TRAFFIC_INTERVAL)
        finally:
            log.info("Live traffic stopped")
            self._traffic_task = None
            self._last_counters = None
            self.readings.traffic = None
            await self._client.logout()

    def _stop_traffic(self) -> None:
        if self._traffic_task is not None:
            self._traffic_task.cancel()

    # Polling

    def _may_use_login(self) -> bool:
        return self.is_supported and not self.is_ignored and self.has_password

    def _restart(self) -> None:
        self.model = None
        self._cancel_polling()
        if self.paused or self.router_back_time is not None:
            return
        self._poll_task = asyncio.get_running_loop().create_task(self._poll_loop())

    def _cancel_polling(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        next_details = 0.0
        while True:
            was_identified = self.model is not None
            if not was_identified:
                await self._identify()
            if self.is_supported and not self.is_ignored:
                # Identification already read Status.json.
                if was_identified:
                    await self._refresh_status()
                if time.monotonic() >= self._normal_sync_due:
                    self._refresh_normal_sync()
                if self.has_password and time.monotonic() >= next_details:
                    await self._refresh_details()
                    next_details = time.monotonic() + self.settings.details_interval
            await asyncio.sleep(self.settings.status_interval)

    async def _identify(self) -> None:
        """Model and serial number are set before any login, so the password of
        another router is never sent."""
        try:
            status_vars = await self._client.fetch_public(_STATUS_SOURCE)
            status = LineStatus.from_vars(status_vars)
        except _NETWORK_ERRORS:
            status = None
        try:
            if status is not None and status.title:
                model = RouterModel.from_status_title(status.title)
            else:
                # Not a Speedport Smart; if unreachable, this request raises the network error.
                model = RouterModel.from_html(await self._client.fetch_start_page())
        except _NETWORK_ERRORS as error:
            self._status_failed("Router identification failed", error)
            return

        self.model = model
        self.readings.status_error = None
        log.info("Identified router: %s", model.name)
        if not model.supported:
            return
        self._identify_device(status)
        if status is not None and not self.is_ignored:
            self._apply_status(status)

    def _identify_device(self, status: LineStatus | None) -> None:
        serial = status.serial_number if status else None
        if serial is None:
            self.serial = None
            self.readings.details_error = "Cannot identify the router: it reports no serial number. Line details are not read."
            log.warning("Router reports no serial number")
            self._new_client()
            return
        if self.serial != serial or self.data_log is None:
            self._open_router_data(serial)
        self._adopt_initial_password()
        self._new_client()

    def _open_router_data(self, serial: str) -> None:
        """Switches logs, settings and everything derived from the log to this router."""
        moved = self._store.adopt_legacy_logs(serial)
        if moved:
            log.info("Moved %d log files from before per-router storage to router %s", moved, serial[-4:])
        self.serial = serial
        self.data_log = DataLog(self._store.router_logs_folder(serial))
        self.settings = replace(self._store.settings(serial), router_url=self.settings.router_url)
        self.readings.overflow = None
        self._last_line = None
        self.normal_sync = NormalSync(None, None)
        self._normal_sync_due = 0.0
        now = datetime.now(UTC)
        logged_tests = self.data_log.speedtest_samples(now - _SPEEDTEST_HISTORY)
        self.last_speedtest = logged_tests[-1] if logged_tests else None
        self.last_speedtrace = [] if self.last_speedtest is None else [
            point for point in self.data_log.speedtrace_samples(self.last_speedtest.time)
            if point.test_time == self.last_speedtest.time]
        self._last_speedtest_attempt = self.last_speedtest.time if self.last_speedtest else None
        self._event_values = {event.kind: event.value for event in self.data_log.event_samples(now - _EVENT_MEMORY)}

    def _forget_router(self) -> None:
        """No router is known until the next identification; nothing is logged or sent meanwhile."""
        self._stop_traffic()
        self.model = None
        self.serial = None
        self.data_log = None
        self.readings = Readings()
        self._last_line = None
        self.last_speedtest = None
        self.last_speedtrace = []

    async def _refresh_status(self) -> None:
        try:
            status = LineStatus.from_vars(await self._client.fetch_public(_STATUS_SOURCE))
        except _NETWORK_ERRORS as error:
            self._status_failed("Status poll failed", error)
            return
        if status.serial_number != self.serial:
            # Another router at this address: its password and logs are not this one's.
            log.warning("Router at %s changed; identifying it again", self.settings.router_url)
            self._forget_router()
            self._new_client()
            return
        self._apply_status(status)

    def _status_failed(self, action: str, error: Exception) -> None:
        """Warns once per distinct problem; the same failure repeats every status interval."""
        problem = connection_problem(error, self.settings.router_url)
        if problem != self.readings.status_error:
            log.warning("%s: %s", action, problem)
        self.readings.status_error = problem

    def _apply_status(self, status: LineStatus) -> None:
        now = datetime.now(UTC)
        self.readings.status = status
        self.readings.status_time = now
        self.readings.status_error = None
        if self.data_log is None:
            return
        self.data_log.append_status(StatusSample.from_status(now, status))
        self._log_changes(now, status_events(status))

    def _log_changes(self, now: datetime, values: dict[EventKind, str | None]) -> None:
        if self.data_log is None:
            return
        for kind, value in values.items():
            if not value or self._event_values.get(kind) == value:
                continue
            log.info("%s: %s", kind.value, value)
            self.data_log.append_event(EventSample(now, kind, value))
            self._event_values[kind] = value

    def _refresh_normal_sync(self) -> None:
        since = datetime.now(UTC) - _NORMAL_SYNC_WINDOW
        self.normal_sync = NormalSync.from_samples(self.data_log.status_samples(since) if self.data_log else [])
        self._normal_sync_due = time.monotonic() + _NORMAL_SYNC_REFRESH_SECONDS

    async def _refresh_details(self) -> None:
        has_receiver = self.readings.status is not None and self.readings.status.has_receiver
        try:
            dsl = DSLDetails.from_page(await self._client.fetch_engineer_page(DSL_PAGE))
            receiver = None
            if has_receiver:
                receiver = ReceiverStatus.from_page(await self._client.fetch_engineer_page(RECEIVER_PAGE))
            now = datetime.now(UTC)
            sample = LineSample.from_details(now, dsl, receiver)
            reference = self._line_reference(now)
            self.readings.crc_rate = ErrorRate.crc(reference, sample) if reference else None
            self.readings.fec_rate = ErrorRate.fec(reference, sample) if reference else None
            self._last_line = sample
            self.readings.dsl, self.readings.receiver = dsl, receiver
            self.readings.details_time = now
            self.readings.details_error = None
            if self.data_log is not None:
                self.data_log.append_line(sample)
            self._log_changes(now, details_events(dsl, receiver))
            if has_receiver:
                await self._refresh_overflow(now)
        except _NETWORK_ERRORS as error:
            problem = connection_problem(error, self.settings.router_url)
            log.error("Engineer page poll failed: %s", problem)
            self.readings.details_error = problem
        finally:
            # The dashboard's live traffic may still be using the session.
            if self._traffic_task is None:
                await self._client.logout()

    async def _refresh_overflow(self, now: datetime) -> None:
        bonding = BondingStatus.from_page(await self._client.fetch_engineer_page(BONDING_PAGE))
        counters = LANCounters.from_page(await self._client.fetch_engineer_page(LAN_PAGE))
        sample = OverflowSample.measure(now, bonding, counters, self._overflow_reference(now))
        self.readings.bonding = bonding
        self.readings.overflow = sample
        if self.data_log is not None:
            self.data_log.append_overflow(sample)

    def _oldest_reference(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self.settings.details_interval * _REFERENCE_INTERVALS)

    def _overflow_reference(self, now: datetime) -> OverflowSample | None:
        """Previous sample from memory, else (after a restart) from the log of the same router."""
        oldest = self._oldest_reference(now)
        candidate = self.readings.overflow
        if candidate is None and self.data_log is not None:
            logged = self.data_log.overflow_samples(oldest)
            candidate = logged[-1] if logged else None
        return candidate if candidate is not None and oldest <= candidate.time < now else None

    def _line_reference(self, now: datetime) -> LineSample | None:
        oldest = self._oldest_reference(now)
        candidate = self._last_line
        if candidate is None and self.data_log is not None:
            logged = self.data_log.line_samples(oldest)
            candidate = logged[-1] if logged else None
        return candidate if candidate is not None and oldest <= candidate.time < now else None

    def _clear_details(self) -> None:
        readings = self.readings
        readings.dsl = readings.receiver = readings.bonding = readings.overflow = None
        readings.crc_rate = readings.fec_rate = readings.details_time = None
        self._last_line = None

    def _clear_readings(self) -> None:
        self._stop_traffic()
        self._clear_details()
        self.readings.status = None

    def _logout_soon(self) -> None:
        if self._traffic_task is None:
            asyncio.get_running_loop().create_task(self._client.logout())



async def _close_quietly(client: SpeedportClient) -> None:
    await client.logout()
    await client.close()
