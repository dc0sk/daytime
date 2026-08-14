"""Async WebSocket client for the daytime SC20 controller.

Deliberately free of Home Assistant imports so it can be tested on its own and extracted to
a package later.

Three properties of the device shape this design:

* It never acknowledges writes. Setters therefore re-read their own packet and return what
  the device actually holds, rather than assuming the write landed.
* It broadcasts every frame to every connected client, including frames *sent by* other
  clients. Those are filtered out; acting on them would make the integration react to the
  phone app's traffic and to its own writes.
* It dumps its entire state, unprompted, as two JSON arrays right after connect. Connecting
  is therefore enough to populate state; the explicit read sweep is a fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp

from . import protocol
from .const import (
    CCV_TITLES,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    MODE_DAYCYCLE,
    MODE_MANUAL,
    PATH_SERVER_LOG,
    REQUEST_ADDRESS,
    REQUEST_RESPONSE,
    REQUEST_TIMEOUT,
    SEND_INTERVAL,
    SUBPROTOCOL,
    TITLE_ACCLIMATE,
    TITLE_CCMODE,
    TITLE_CLOCK,
    TITLE_CLOUD,
    TITLE_DSCRPTN,
    TITLE_DYCL,
    TITLE_MESH_NETWORK,
    TITLE_MOON,
    TITLE_USRDTA,
    TO_MASTER,
)
from .exceptions import SC20ConnectionError, SC20ProtocolError, SC20Timeout
from .models import (
    Acclimate,
    ChannelValues,
    Clock,
    Cloud,
    Daycycle,
    DaycycleDescription,
    DeviceInfo,
    MeshNetwork,
    Moon,
    ServerLog,
)

_LOGGER = logging.getLogger(__name__)

#: Backoff bounds for reconnection, in seconds.
_RECONNECT_MIN = 2.0
_RECONNECT_MAX = 60.0


class SC20State:
    """The last known value of everything the device reports."""

    def __init__(self) -> None:
        self.device_info: DeviceInfo | None = None
        self.clock: Clock | None = None
        self.values: ChannelValues | None = None
        self.daycycle: Daycycle | None = None
        self.description: DaycycleDescription | None = None
        self.moon: Moon | None = None
        self.cloud: Cloud | None = None
        self.acclimate: Acclimate | None = None
        self.mesh: MeshNetwork | None = None
        self.server_log: ServerLog | None = None

    @property
    def mode(self) -> str:
        """The current mode.

        `CLOCK.mode` arrives in the connect burst and updates immediately after a mode
        change, so it is the primary source. `GET_CCMODE` reports the same value and is
        read too, but only as a cross-check.
        """
        if self.clock is not None:
            return self.clock.mode
        if self.device_info is not None:
            return self.device_info.mode
        return MODE_DAYCYCLE

    @property
    def is_manual(self) -> bool:
        return self.mode == MODE_MANUAL


class SC20Client:
    """A persistent connection to one SC20 controller."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        on_update: Callable[[SC20State], None] | None = None,
    ) -> None:
        self.host = host
        self.state = SC20State()
        self._session = session
        self._on_update = on_update
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._send_lock = asyncio.Lock()
        self._last_frame: float = 0.0
        self._closing = False
        self._connected = asyncio.Event()

    # --- lifecycle -----------------------------------------------------------------------

    def set_update_callback(self, callback: Callable[[SC20State], None] | None) -> None:
        """Register who to tell when state changes.

        Separate from the constructor because the usual consumer is an update coordinator,
        which needs the client to exist before it can be built.
        """
        self._on_update = callback

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Open the connection and start the background tasks.

        Returns once the socket is up. The device's unprompted state dump usually lands a
        moment later, so callers that need state should await `async_refresh()`.
        """
        self._closing = False
        await self._open()
        self._tasks = [
            asyncio.create_task(self._run_reader(), name=f"sc20-reader-{self.host}"),
            asyncio.create_task(self._run_heartbeat(), name=f"sc20-heartbeat-{self.host}"),
        ]

    async def disconnect(self) -> None:
        """Close the connection and stop the background tasks."""
        self._closing = True
        self._connected.clear()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        # Nobody is going to answer the outstanding reads now.
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(SC20ConnectionError("client disconnected"))
        self._waiters.clear()

    async def _open(self) -> None:
        url = f"http://{self.host}/ws"
        try:
            self._ws = await self._session.ws_connect(url, protocols=(SUBPROTOCOL,))
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise SC20ConnectionError(f"cannot connect to {self.host}: {err}") from err
        self._last_frame = asyncio.get_running_loop().time()
        self._connected.set()
        _LOGGER.debug("connected to %s", self.host)

    async def __aenter__(self) -> SC20Client:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # --- background tasks ----------------------------------------------------------------

    async def _run_reader(self) -> None:
        """Read frames, reconnecting with backoff whenever the socket drops."""
        delay = _RECONNECT_MIN
        while not self._closing:
            try:
                if self._ws is None or self._ws.closed:
                    await self._open()
                delay = _RECONNECT_MIN
                assert self._ws is not None
                async for message in self._ws:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        self._on_message(message.data)
                    elif message.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
            except asyncio.CancelledError:
                raise
            except (SC20ConnectionError, aiohttp.ClientError, OSError) as err:
                _LOGGER.debug("connection to %s failed: %s", self.host, err)
            except Exception:
                _LOGGER.exception("unexpected error reading from %s", self.host)

            if self._closing:
                return
            self._connected.clear()
            self._ws = None
            _LOGGER.debug("reconnecting to %s in %.0fs", self.host, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    async def _run_heartbeat(self) -> None:
        """Keep the link alive and notice when it has gone quiet.

        Mirrors the vendor UI: a `GET_MESH_NETWORK` every 3 s, and the link declared dead
        after three missed beats. Dropping the socket makes the reader reconnect.
        """
        while not self._closing:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if not self.connected:
                continue
            try:
                await self._send(protocol.build_request("GET_MESH_NETWORK"))
            except SC20ConnectionError:
                continue
            silence = asyncio.get_running_loop().time() - self._last_frame
            if silence > HEARTBEAT_TIMEOUT:
                _LOGGER.debug("%s silent for %.0fs, dropping the socket", self.host, silence)
                if self._ws is not None:
                    await self._ws.close()

    # --- inbound -------------------------------------------------------------------------

    def _on_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.debug("ignoring non-JSON frame from %s: %.120s", self.host, raw)
            return

        try:
            frames = protocol.iter_frames(payload)
        except SC20ProtocolError as err:
            _LOGGER.debug("ignoring unusable frame from %s: %s", self.host, err)
            return

        changed = False
        for frame in frames:
            # Frames stamped from:"USER" are another client's command coming back off the
            # broadcast, not device state. That includes our own writes.
            if protocol.is_echo(frame):
                continue
            self._last_frame = asyncio.get_running_loop().time()
            if self._apply(frame):
                changed = True
            self._resolve_waiters(frame)

        if changed and self._on_update is not None:
            self._on_update(self.state)

    def _store(self, attribute: str, value: Any) -> bool:
        """Store a parsed value, reporting whether it actually differs from what we had.

        The models are frozen dataclasses, so equality is by value. This matters because
        the device answers our 3-second heartbeat with an identical MESH_NETWORK frame
        forever; treating each one as a change would rewrite every entity's state twenty
        times a minute and fill the recorder with nothing.
        """
        if getattr(self.state, attribute) == value:
            return False
        setattr(self.state, attribute, value)
        return True

    def _apply(self, frame: dict[str, Any]) -> bool:
        """Fold one frame into the state. Returns whether anything actually changed."""
        title = frame.get("title")
        if not isinstance(title, str):
            return False

        try:
            if title in CCV_TITLES:
                return self._store("values", protocol.parse_channel_values(frame))
            elif title == TITLE_CLOCK:
                # The clock ticks every read, so comparing it whole would always differ.
                # Only the mode it carries is worth waking entities for.
                clock = protocol.parse_clock(frame)
                previous = self.state.clock
                self.state.clock = clock
                return previous is None or previous.mode != clock.mode
            elif title == TITLE_USRDTA:
                return self._store("device_info", protocol.parse_device_info(frame))
            elif title == TITLE_DYCL:
                return self._store("daycycle", protocol.parse_daycycle(frame))
            elif title == TITLE_DSCRPTN:
                return self._store("description", protocol.parse_description(frame))
            elif title == TITLE_MOON:
                return self._store("moon", protocol.parse_moon(frame))
            elif title == TITLE_CLOUD:
                return self._store("cloud", protocol.parse_cloud(frame))
            elif title == TITLE_ACCLIMATE:
                return self._store("acclimate", protocol.parse_acclimate(frame))
            elif title == TITLE_MESH_NETWORK:
                return self._store("mesh", protocol.parse_mesh_network(frame))
            elif title == TITLE_CCMODE:
                mode = frame.get("mode")
                if mode not in (MODE_MANUAL, MODE_DAYCYCLE):
                    return False
                return self._store_mode(mode)
            elif title in (MODE_MANUAL, MODE_DAYCYCLE):
                # A bare mode frame, sent when some other client changes the mode.
                return self._store_mode(title)
            else:
                # The firmware has many more titles than this integration models.
                return False
        except SC20ProtocolError as err:
            _LOGGER.debug("could not parse %s from %s: %s", title, self.host, err)
            return False

    def _store_mode(self, mode: str) -> bool:
        """Record a mode change that arrived on its own, without a full CLOCK frame.

        Mode is read from the clock, so it is folded in there. If no clock has arrived yet
        there is no timestamp to attach it to, and the next CLOCK frame will carry the mode
        anyway — so this drops it rather than inventing a time.

        Returns whether the mode actually changed, so a repeated report does not wake every
        entity for nothing.
        """
        clock = self.state.clock
        if clock is None or clock.mode == mode:
            return False
        self.state.clock = Clock(clock.timestamp, mode=mode)
        return True

    def _resolve_waiters(self, frame: dict[str, Any]) -> None:
        title = frame.get("title")
        # A CCV reply may arrive under any of the CCV variants; a REQ_CCV waiter accepts all.
        keys = {title} | ({"CCV"} if title in CCV_TITLES else set())
        for key in keys:
            for waiter in self._waiters.pop(key, []):
                if not waiter.done():
                    waiter.set_result(frame)

    # --- outbound ------------------------------------------------------------------------

    async def _send(self, frame: dict[str, Any]) -> None:
        """Send one frame, paced.

        The lock plus the trailing sleep keeps sends at least `SEND_INTERVAL` apart the way
        the vendor UI does; the device is an ESP8266 with very little heap and bursts are a
        genuine risk.
        """
        async with self._send_lock:
            if self._ws is None or self._ws.closed:
                raise SC20ConnectionError(f"not connected to {self.host}")
            try:
                await self._ws.send_str(json.dumps(frame))
            except (aiohttp.ClientError, ConnectionResetError) as err:
                raise SC20ConnectionError(f"send to {self.host} failed: {err}") from err
            await asyncio.sleep(SEND_INTERVAL)

    async def _request(self, title: str, *, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
        """Send a read request and wait for the frame it produces."""
        expected = REQUEST_RESPONSE.get(title)
        if expected is None:
            raise ValueError(f"{title} is not a request with a known response")

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters.setdefault(expected, []).append(waiter)
        try:
            await self._send(
                protocol.build_request(title, to=REQUEST_ADDRESS.get(title, TO_MASTER))
            )
            return await asyncio.wait_for(waiter, timeout)
        except TimeoutError as err:
            raise SC20Timeout(f"{self.host} did not answer {title} within {timeout}s") from err
        finally:
            remaining = [w for w in self._waiters.get(expected, []) if w is not waiter]
            if remaining:
                self._waiters[expected] = remaining
            else:
                self._waiters.pop(expected, None)

    # --- reads ---------------------------------------------------------------------------

    async def async_refresh(self) -> SC20State:
        """Re-read everything.

        Requests are issued one at a time rather than gathered: the send queue serialises
        them anyway, and the device is happier for it.
        """
        for title in REQUEST_RESPONSE:
            try:
                await self._request(title)
            except SC20Timeout:
                _LOGGER.debug("%s did not answer %s", self.host, title)
            except SC20ConnectionError:
                break
        self.state.server_log = await self.async_fetch_server_log()
        if self._on_update is not None:
            self._on_update(self.state)
        return self.state

    async def async_get_values(self) -> ChannelValues:
        """Read the live channel levels.

        This is the *actual* output, with cloud, moonlight and acclimatisation already
        applied — in scheduled mode with clouds active it changes continuously.
        """
        frame = await self._request("REQ_CCV")
        values = protocol.parse_channel_values(frame)
        self.state.values = values
        return values

    async def async_get_daycycle(self) -> Daycycle:
        frame = await self._request("GET_DYCL")
        daycycle = protocol.parse_daycycle(frame)
        self.state.daycycle = daycycle
        return daycycle

    async def async_fetch_server_log(self) -> ServerLog | None:
        """Scrape `/serverLog` for uptime, heap and operating hours.

        These appear nowhere in the WebSocket protocol. A failure here is not fatal — the
        rest of the integration works without them.
        """
        url = f"http://{self.host}{PATH_SERVER_LOG}"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response.raise_for_status()
                return protocol.parse_server_log(await response.text())
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("could not read %s: %s", url, err)
            return None

    # --- writes --------------------------------------------------------------------------
    #
    # The device never acknowledges a write, so each of these re-reads the packet it wrote
    # and returns what the device actually holds.

    async def async_set_mode(self, manual: bool) -> str:
        """Switch between manual override and the scheduled programme."""
        await self._send(protocol.build_set_mode(manual))
        try:
            self.state.clock = protocol.parse_clock(await self._request("GET_CLOCK"))
        except (SC20Timeout, SC20ProtocolError):
            _LOGGER.debug("%s did not confirm the mode change", self.host)
        return self.state.mode

    async def async_set_values(self, values: ChannelValues) -> ChannelValues:
        """Set the live channel levels, entering manual mode first if needed.

        Setting values has no effect in scheduled mode, so the mode switch is not optional.
        It is skipped when already manual to avoid a redundant frame.
        """
        if not self.state.is_manual:
            await self.async_set_mode(manual=True)
        await self._send(protocol.build_set_values(values))
        try:
            return await self.async_get_values()
        except (SC20Timeout, SC20ProtocolError):
            # The write almost certainly landed; the confirmation read did not.
            self.state.values = values
            return values

    async def async_set_daycycle(
        self, daycycle: Daycycle, description: DaycycleDescription | None = None
    ) -> Daycycle:
        """Overwrite the lighting programme.

        Validated before sending. This replaces the user's real schedule and there is no
        undo on the device, so callers should snapshot the current one first.
        """
        await self._send(protocol.build_set_daycycle(daycycle))
        if description is not None:
            await self._send(protocol.build_set_description(description))
        try:
            return await self.async_get_daycycle()
        except (SC20Timeout, SC20ProtocolError):
            self.state.daycycle = daycycle
            return daycycle

    async def async_set_moon(self, moon: Moon) -> None:
        await self._send(protocol.build_set_moon(moon))
        self.state.moon = moon

    async def async_set_cloud(self, cloud: Cloud) -> None:
        await self._send(protocol.build_set_cloud(cloud))
        self.state.cloud = cloud

    async def async_set_acclimate(self, acclimate: Acclimate) -> None:
        await self._send(protocol.build_set_acclimate(acclimate))
        self.state.acclimate = acclimate

    async def async_pause_acclimation(self, paused: bool) -> None:
        await self._send(protocol.build_pause_acclimation(paused))

    async def async_set_clock(self, moment: datetime) -> None:
        """Push a wall-clock time to the device, preserving its current mode."""
        await self._send(protocol.build_set_clock(moment, mode=self.state.mode))

    async def async_start_firmware_update(self) -> None:
        """Tell the controller to update itself, and return immediately.

        The device then downloads from data.daytime.de, flashes, and reboots — so it drops
        off the network for a few minutes. Use `async_wait_for_update` to follow it back.

        There is no cancel. A failed flash leaves the lamp needing a manual reflash through
        its own web page, and the aquarium unlit until then.
        """
        _LOGGER.warning(
            "starting a firmware update on %s — it will reboot and be unreachable for "
            "several minutes; do not power-cycle it",
            self.host,
        )
        await self._send(protocol.build_start_firmware_update())

    async def async_wait_for_update(
        self, previous: tuple[int, int], *, timeout: float = 420.0, poll: float = 10.0
    ) -> bool:
        """Follow the device through a reboot, returning whether its revision changed.

        `previous` is the revision pair from before the update started. The reader task
        reconnects on its own, so this just waits for the socket to come back and then
        re-reads USRDTA until the revision moves.

        Returns False on timeout rather than raising: a timeout means "we stopped
        watching", not "the update failed", and the difference matters when the thing on
        the other end is someone's aquarium light.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Give it a moment to actually go away, so a stale pre-reboot read is not mistaken
        # for a finished update.
        await asyncio.sleep(poll)

        while loop.time() < deadline:
            if self.connected:
                try:
                    frame = await self._request("GET_USRDTA", timeout=poll)
                    info = protocol.parse_device_info(frame)
                except (SC20Timeout, SC20ConnectionError, SC20ProtocolError):
                    pass
                else:
                    self.state.device_info = info
                    if info.revision != previous:
                        _LOGGER.info(
                            "%s came back on revision %s (was %s)",
                            self.host,
                            info.revision,
                            previous,
                        )
                        return True
            await asyncio.sleep(poll)

        _LOGGER.warning(
            "%s did not report a new revision within %.0fs; check it directly",
            self.host,
            timeout,
        )
        return False

    async def async_preview_curve(self, speed_factor: int, start: int, end: int) -> None:
        """Fast-forward the programmed curve as a demo. Ends with `async_set_mode(False)`."""
        await self._send(protocol.build_preview_curve(speed_factor, start, end))


async def async_check_connection(host: str, session: aiohttp.ClientSession) -> bool:
    """Cheap reachability probe, the same one the vendor web app uses before connecting."""
    url = f"http://{host}/connectioncheck"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            return response.status == 200
    except (TimeoutError, aiohttp.ClientError, OSError):
        return False
