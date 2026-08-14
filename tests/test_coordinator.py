"""Coordinator tests.

The bug these exist for: the device answers our 3-second heartbeat with a MESH_NETWORK
frame, which the client publishes as a state change. Publishing through
`async_set_updated_data` resets the coordinator's refresh timer — its docstring says so —
so a push arriving more often than the poll interval means the poll never fires at all.

The visible symptom was a light stuck at its last known brightness: the schedule dimmed the
tank to 0 % at 22:00 and Home Assistant still showed 90 %, because `REQ_CCV` had not been
sent since the connection was established.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.daytime_sc20.api import ChannelValues, SC20State
from custom_components.daytime_sc20.const import DEFAULT_SCAN_INTERVAL, DOMAIN

from .test_entities import FakeClient, _state


class HeartbeatingClient(FakeClient):
    """A client that pushes state as often as the real device answers the heartbeat."""

    def __init__(self, state: SC20State) -> None:
        super().__init__(state)
        self.get_values_calls = 0
        #: What a fresh read would return — i.e. what the tank is really doing.
        self.live_values = ChannelValues((90, 90, 90))

    async def async_get_values(self) -> ChannelValues:
        self.get_values_calls += 1
        self.state.values = self.live_values
        return self.live_values

    def push(self) -> None:
        """Stand in for a MESH_NETWORK heartbeat reply arriving from the device."""
        if self._on_update is not None:
            self._on_update(self.state)


@pytest.fixture
async def setup_entry(hass: HomeAssistant):
    async def _setup() -> tuple[MockConfigEntry, HeartbeatingClient]:
        client = HeartbeatingClient(_state())
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.168.1.34"},
            unique_id="AA:BB:CC:DD:EE:FF",
            title="Reef tank",
        )
        entry.add_to_hass(hass)
        with patch("custom_components.daytime_sc20.SC20Client", return_value=client):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry, client

    return _setup


async def test_heartbeat_pushes_do_not_stop_the_poll(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    """A push every 3 s must not starve a poll every 30 s.

    This is the regression: with pushes resetting the refresh timer, `async_get_values` was
    never called after setup, and the reported brightness froze.
    """
    _, client = await setup_entry()
    before = client.get_values_calls

    # Three minutes of heartbeat replies, at the real device's 3-second cadence.
    for _ in range(60):
        freezer.tick(timedelta(seconds=3))
        client.push()
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    polls = client.get_values_calls - before
    # Three minutes at a 30-second interval is about six polls; anything above zero proves
    # the timer is no longer being starved, and the lower bound catches a partial fix.
    assert polls >= 4, f"only {polls} polls in 180 s at a {DEFAULT_SCAN_INTERVAL} s interval"


async def test_reported_brightness_follows_the_device(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    """The reported symptom, end to end: the tank dims, Home Assistant must follow."""
    _, client = await setup_entry()
    assert hass.states.get("light.reef_tank").attributes["brightness"] == 230  # 90 %

    # The schedule takes the tank to dark, as it does at 22:00.
    client.live_values = ChannelValues((0, 0, 0))

    for _ in range(30):
        freezer.tick(timedelta(seconds=3))
        client.push()
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert hass.states.get("light.reef_tank").state == "off"


async def test_pushes_still_reach_entities_promptly(hass: HomeAssistant, setup_entry) -> None:
    """Fixing the timer must not cost us the push path.

    A change made from the vendor app arrives on the broadcast, and should show up without
    waiting for the next poll.
    """
    _, client = await setup_entry()

    client.state.values = ChannelValues((10, 10, 10))
    client.push()
    await hass.async_block_till_done()

    assert hass.states.get("light.reef_tank").attributes["brightness"] == 26  # 10 %
