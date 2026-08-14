"""Tests for the WebSocket client, against a stand-in for the real device.

The fake server reproduces the behaviours that actually caught us out on hardware: the
unprompted array-wrapped state dump on connect, the broadcast of client frames back to
every client, and the total absence of write acknowledgements.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

from custom_components.daytime_sc20.api import (
    ChannelValues,
    Cloud,
    SC20Client,
    SC20ConnectionError,
    SC20Timeout,
    async_check_connection,
)
from custom_components.daytime_sc20.api.const import REQUEST_RESPONSE


class FakeSC20:
    """A stand-in SC20 web server."""

    def __init__(self, frames: dict[str, dict[str, Any]], server_log: str) -> None:
        self.frames = frames
        self.server_log = server_log
        #: Everything a client sent us, for assertions about what went on the wire.
        self.received: list[dict[str, Any]] = []
        #: Turn off the connect burst to test clients that must poll for themselves.
        self.send_burst_on_connect = True
        #: Titles to simply not answer, for timeout tests.
        self.ignore: set[str] = set()
        self.app = web.Application()
        self.app.router.add_get("/ws", self._ws_handler)
        self.app.router.add_get("/serverLog", self._server_log)
        self.app.router.add_get("/connectioncheck", self._connection_check)

    async def _connection_check(self, request: web.Request) -> web.Response:
        return web.Response(text='angular.callbacks._0({"id":1})')

    async def _server_log(self, request: web.Request) -> web.Response:
        return web.Response(text=self.server_log, content_type="text/html")

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("arduino",))
        await ws.prepare(request)

        if self.send_burst_on_connect:
            # Exactly what the device does: two arrays, not individual objects.
            first = ["USRDTA", "CCV", "CLOCK", "MOON", "CLOUD", "ACCLIMATE"]
            second = ["DSCRPTN", "DYCL", "NET_ST", "NET_AP", "MESH_NETWORK"]
            for group in (first, second):
                payload = [self.frames[t] for t in group if t in self.frames]
                await ws.send_str(json.dumps(payload))

        async for message in ws:
            if message.type is not web.WSMsgType.TEXT:
                continue
            frame = json.loads(message.data)
            self.received.append(frame)
            title = frame.get("title", "")

            # The real device broadcasts every client frame back to every client.
            await ws.send_str(json.dumps(frame))

            if title == "INJECT_FOREIGN_CCV":
                # Stand in for the phone app sending a command: same broadcast, different
                # originator, still stamped from:"USER".
                await ws.send_str(
                    json.dumps({"title": "CCV-SL", "from": "USER", "currentValues": [1, 2, 3]})
                )
                continue

            if title in self.ignore:
                continue

            response_title = REQUEST_RESPONSE.get(title)
            if response_title and response_title in self.frames:
                await ws.send_str(json.dumps(self.frames[response_title]))
            elif title in ("MAN_MODE", "DAYCL_MODE"):
                # Writes are unacknowledged; the mode is only visible on the next read.
                clock = dict(self.frames["CLOCK"])
                clock["mode"] = title
                self.frames["CLOCK"] = clock
            elif title in ("CCV-SL", "CCV-SW"):
                ccv = dict(self.frames["CCV"])
                ccv["currentValues"] = frame["currentValues"]
                self.frames["CCV"] = ccv
            elif title == "DYCL":
                dycl = dict(self.frames["DYCL"])
                dycl["configuration"] = frame["configuration"]
                self.frames["DYCL"] = dycl
        return ws


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """These tests talk to a loopback server, which pytest-socket blocks by default.

    Home Assistant's test plugin disables sockets to catch accidental real network calls;
    the fake device here is genuinely local, so it is opted back in explicitly.
    """
    return socket_enabled


@pytest.fixture
async def fake_device(aiohttp_server, device_frames, server_log_html):
    device = FakeSC20(device_frames, server_log_html)
    server = await aiohttp_server(device.app)
    device.host = f"{server.host}:{server.port}"
    return device


@pytest.fixture
async def client(fake_device, aiohttp_client):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client(fake_device.host, session)
        await client.connect()
        yield client
        await client.disconnect()


async def test_connect_burst_populates_everything(client, fake_device) -> None:
    """The device volunteers its whole state; no requests should be needed."""
    for _ in range(50):
        if client.state.device_info is not None and client.state.daycycle is not None:
            break
        await asyncio.sleep(0.02)

    state = client.state
    assert state.device_info is not None
    assert state.clock is not None
    assert state.values is not None
    assert state.daycycle is not None
    assert state.description is not None
    assert state.moon is not None
    assert state.cloud is not None
    assert state.acclimate is not None
    assert state.mesh is not None
    # Not one request was sent to get all that.
    assert fake_device.received == []


async def test_another_clients_broadcast_command_is_not_treated_as_state(
    client, fake_device
) -> None:
    """The phone app's commands reach us too, and must not be mistaken for device state.

    The injected frame carries values that differ from what the device reports, so the two
    outcomes are distinguishable: if the echo filter were removed this would end up at
    (1, 2, 3) instead of the device's real values.
    """
    await asyncio.sleep(0.1)
    before = client.state.values
    assert before is not None and before != ChannelValues((1, 2, 3))

    # Ask the fake device to emit a frame as though some other client had sent it.
    await client._send({"title": "INJECT_FOREIGN_CCV"})
    await asyncio.sleep(0.2)

    assert client.state.values == before


async def test_echo_of_another_clients_command_is_not_state() -> None:
    """A frame stamped from:"USER" must never reach the state, whoever sent it."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        client.state.values = ChannelValues((90, 90, 90))
        # Directly exercise the receive path with a foreign client's command.
        client._on_message(
            json.dumps({"title": "CCV-SL", "from": "USER", "currentValues": [1, 2, 3]})
        )
        assert client.state.values == ChannelValues((90, 90, 90))


async def test_setting_values_enters_manual_mode_first(client, fake_device) -> None:
    """Levels are ignored in scheduled mode, so the mode switch is not optional."""
    await asyncio.sleep(0.1)
    assert not client.state.is_manual

    await client.async_set_values(ChannelValues((50, 40, 30)))

    titles = [f["title"] for f in fake_device.received]
    assert "MAN_MODE" in titles
    assert "CCV-SL" in titles
    assert titles.index("MAN_MODE") < titles.index("CCV-SL")


async def test_setting_values_when_already_manual_skips_the_mode_frame(
    client, fake_device
) -> None:
    await asyncio.sleep(0.1)
    await client.async_set_mode(manual=True)
    fake_device.received.clear()

    await client.async_set_values(ChannelValues((50, 40, 30)))

    titles = [f["title"] for f in fake_device.received]
    assert "MAN_MODE" not in titles


async def test_mode_round_trip(client) -> None:
    await asyncio.sleep(0.1)
    assert await client.async_set_mode(manual=True) == "MAN_MODE"
    assert client.state.is_manual is True
    assert await client.async_set_mode(manual=False) == "DAYCL_MODE"
    assert client.state.is_manual is False


async def test_writes_are_confirmed_by_reading_back(client, fake_device) -> None:
    """The device never acknowledges, so a setter must re-read to know what happened."""
    await asyncio.sleep(0.1)
    fake_device.received.clear()

    await client.async_set_values(ChannelValues((10, 20, 30)))

    titles = [f["title"] for f in fake_device.received]
    assert "REQ_CCV" in titles, "the setter must confirm by reading back"


async def test_refresh_reads_everything_including_the_server_log(client) -> None:
    state = await client.async_refresh()
    assert state.server_log is not None
    assert state.server_log.free_heap == 26848
    assert state.server_log.uptime_minutes == 6601


async def test_request_timeout_is_raised_not_hung(fake_device) -> None:
    import aiohttp

    fake_device.send_burst_on_connect = False
    fake_device.ignore = {"REQ_CCV"}

    async with aiohttp.ClientSession() as session:
        client = SC20Client(fake_device.host, session)
        await client.connect()
        try:
            with pytest.raises(SC20Timeout):
                await client._request("REQ_CCV", timeout=0.3)
        finally:
            await client.disconnect()


async def test_cloud_settings_survive_the_wire(client, fake_device) -> None:
    """End to end through the encode/decode pair, including both quirks."""
    await asyncio.sleep(0.1)
    cloud = Cloud(
        active=True,
        max_per_day=200,
        probability=50,
        min_intensity=10,
        max_intensity=45,
        min_duration_minutes=5,
        max_duration_minutes=20,
    )
    await client.async_set_cloud(cloud)

    sent = next(f for f in fake_device.received if f["title"] == "CLOUD")
    assert sent["minDuration"] == 300
    assert sent["maxDuration"] == 1200
    assert sent["minIntensity"] == 55
    assert sent["maxIntensity"] == 90


async def test_connection_check_probe(fake_device) -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        assert await async_check_connection(fake_device.host, session) is True
        assert await async_check_connection("127.0.0.1:1", session) is False


async def test_sending_while_disconnected_raises() -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        with pytest.raises(SC20ConnectionError):
            await client.async_set_mode(manual=True)


async def test_connect_to_a_dead_host_raises() -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        with pytest.raises(SC20ConnectionError):
            await client.connect()


async def test_update_callback_fires_on_pushed_state(fake_device) -> None:
    import aiohttp

    seen: list[int] = []
    async with aiohttp.ClientSession() as session:
        client = SC20Client(fake_device.host, session)
        client.set_update_callback(lambda state: seen.append(1))
        await client.connect()
        await asyncio.sleep(0.2)
        await client.disconnect()

    assert seen, "the connect burst should have notified the listener"


# --- change detection --------------------------------------------------------------------


async def test_an_identical_frame_is_not_reported_as_a_change() -> None:
    """The heartbeat returns the same MESH_NETWORK forever; that is not news.

    Reporting it would rewrite every entity's state twenty times a minute and fill the
    recorder with nothing.
    """
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        updates: list[int] = []
        client.set_update_callback(lambda state: updates.append(1))

        frame = json.dumps(
            {"title": "MESH_NETWORK", "from": "AA:BB:CC:DD:EE:FF", "clientList": ["AA:BB"]}
        )
        client._on_message(frame)
        assert len(updates) == 1, "the first one is genuinely new"

        for _ in range(10):
            client._on_message(frame)
        assert len(updates) == 1, "repeats must not notify"


async def test_a_changed_frame_is_reported() -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        updates: list[int] = []
        client.set_update_callback(lambda state: updates.append(1))

        client._on_message(
            json.dumps({"title": "CCV", "from": "AA:BB", "currentValues": [90, 90, 90]})
        )
        client._on_message(
            json.dumps({"title": "CCV", "from": "AA:BB", "currentValues": [80, 80, 80]})
        )
        assert len(updates) == 2
        assert client.state.values == ChannelValues((80, 80, 80))


async def test_a_ticking_clock_alone_is_not_a_change() -> None:
    """CLOCK differs on every read because it carries seconds. Only its mode matters."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = SC20Client("127.0.0.1:1", session)
        updates: list[int] = []
        client.set_update_callback(lambda state: updates.append(1))

        def clock(second: int, mode: str = "DAYCL_MODE") -> str:
            return json.dumps(
                {
                    "title": "CLOCK",
                    "from": "AA:BB",
                    "year": 2026,
                    "month": 8,
                    "day": 14,
                    "hour": 10,
                    "min": 30,
                    "sec": second,
                    "mode": mode,
                }
            )

        client._on_message(clock(1))
        first = len(updates)
        for second in range(2, 12):
            client._on_message(clock(second))
        assert len(updates) == first, "a ticking clock must not wake entities"

        # ...but the mode riding along on it must.
        client._on_message(clock(12, mode="MAN_MODE"))
        assert len(updates) == first + 1
        assert client.state.is_manual is True
        # The timestamp is still kept current even when it does not notify.
        assert client.state.clock.timestamp.second == 12
