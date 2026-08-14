"""Entity tests, driven through a fully set-up config entry.

These go through the real platform setup so the wiring is exercised, not just the classes.
The client is replaced with a fake that records what was asked of it — the protocol layer
already has its own tests, and what matters here is the order and shape of the calls the
entities make.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.api import (
    Acclimate,
    ChannelValues,
    Clock,
    Cloud,
    Daycycle,
    DaycycleDescription,
    DeviceInfo,
    MeshNetwork,
    Moon,
    SC20State,
    ServerLog,
    Setpoint,
)
from custom_components.daytime_sc20.const import DOMAIN


def _state(mode: str = "DAYCL_MODE", values: tuple[int, ...] = (90, 90, 90)) -> SC20State:
    state = SC20State()
    state.device_info = DeviceInfo(
        name="SC20_0000000",
        aquarium_name="",
        mode=mode,
        host="sc20",
        tank_config="DAYTIME",
        language="DE",
        timezone=60,
        dst=True,
        revision=(23, 15),
        latest_revision=(23, 15),
        firmware_available=False,
        live_time=951240,
        power="17",
        mesh_enabled=True,
        group_id=0,
    )
    state.clock = Clock(timestamp=datetime(2026, 8, 14, 10, 30), mode=mode)
    state.values = ChannelValues(values)
    state.daycycle = Daycycle(
        (
            Setpoint(0, (0, 0, 0)),
            Setpoint(360, (0, 0, 0)),
            Setpoint(480, (90, 90, 90)),
            Setpoint(1200, (90, 90, 90)),
            Setpoint(1320, (0, 0, 0)),
            Setpoint(1440, (0, 0, 0)),
        )
    )
    state.description = DaycycleDescription()
    state.moon = Moon(
        active=False, cycle=True, minimum=2, maximum=30, color="b", start=1320, end=360
    )
    state.cloud = Cloud(
        active=True,
        max_per_day=150,
        probability=65,
        min_intensity=0,
        max_intensity=40,
        min_duration_minutes=10,
        max_duration_minutes=25,
    )
    state.acclimate = Acclimate(
        active=False, paused=False, duration_days=30, intensity_reduction=50, current_day=0
    )
    state.mesh = MeshNetwork(clients=("AA:BB:CC:DD:EE:FF",))
    state.server_log = ServerLog(uptime_minutes=6601, free_heap=26848, operating_hours=951240)
    return state


class FakeClient:
    """Records calls instead of talking to a device."""

    def __init__(self, state: SC20State) -> None:
        self.host = "192.168.1.34"
        self.state = state
        self.connected = True
        self.calls: list[tuple[str, Any]] = []
        self._on_update = None

    def set_update_callback(self, callback) -> None:
        self._on_update = callback

    async def connect(self) -> None:
        self.calls.append(("connect", None))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect", None))

    async def async_refresh(self) -> SC20State:
        return self.state

    async def async_get_values(self) -> ChannelValues:
        assert self.state.values is not None
        return self.state.values

    async def async_get_daycycle(self) -> Daycycle:
        assert self.state.daycycle is not None
        return self.state.daycycle

    async def async_set_mode(self, manual: bool) -> str:
        self.calls.append(("set_mode", manual))
        mode = "MAN_MODE" if manual else "DAYCL_MODE"
        assert self.state.clock is not None
        self.state.clock = Clock(self.state.clock.timestamp, mode=mode)
        return mode

    async def async_set_values(self, values: ChannelValues) -> ChannelValues:
        # Mirrors the real client: entering manual mode is part of setting values.
        if not self.state.is_manual:
            await self.async_set_mode(manual=True)
        self.calls.append(("set_values", values))
        self.state.values = values
        return values

    async def async_set_moon(self, moon: Moon) -> None:
        self.calls.append(("set_moon", moon))
        self.state.moon = moon

    async def async_set_cloud(self, cloud: Cloud) -> None:
        self.calls.append(("set_cloud", cloud))
        self.state.cloud = cloud

    async def async_set_acclimate(self, acclimate: Acclimate) -> None:
        self.calls.append(("set_acclimate", acclimate))
        self.state.acclimate = acclimate

    async def async_pause_acclimation(self, paused: bool) -> None:
        self.calls.append(("pause_acclimation", paused))


@pytest.fixture
async def setup_entry(hass: HomeAssistant):
    """Set up the integration with a fake client, and hand both back."""

    async def _setup(state: SC20State | None = None) -> tuple[MockConfigEntry, FakeClient]:
        client = FakeClient(state or _state())
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


# --- lights ------------------------------------------------------------------------------


async def test_master_and_channel_lights_are_created(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    assert hass.states.get("light.reef_tank") is not None
    for channel in ("white", "blue", "red"):
        assert hass.states.get(f"light.reef_tank_{channel}") is not None, channel


async def test_master_brightness_tracks_the_brightest_channel(
    hass: HomeAssistant, setup_entry
) -> None:
    await setup_entry(_state(values=(100, 50, 0)))
    state = hass.states.get("light.reef_tank")
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 255


async def test_turning_the_master_on_enters_manual_mode_first(
    hass: HomeAssistant, setup_entry
) -> None:
    """Levels are ignored while the schedule is running, so the order matters."""
    _, client = await setup_entry()

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.reef_tank", "brightness": 128},
        blocking=True,
    )

    names = [name for name, _ in client.calls]
    assert "set_mode" in names
    assert "set_values" in names
    assert names.index("set_mode") < names.index("set_values")
    assert client.calls[names.index("set_mode")][1] is True


async def test_master_brightness_preserves_the_channel_mix(
    hass: HomeAssistant, setup_entry
) -> None:
    """Dimming must not change the colour of the light."""
    _, client = await setup_entry(_state(values=(100, 50, 0)))

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.reef_tank", "brightness": 128},  # ~50 %
        blocking=True,
    )

    values = next(v for name, v in client.calls if name == "set_values")
    assert values.values == (50, 25, 0)


async def test_turning_the_master_off_zeroes_every_channel(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry()

    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: "light.reef_tank"}, blocking=True
    )

    values = next(v for name, v in client.calls if name == "set_values")
    assert values.values == (0, 0, 0)
    assert hass.states.get("light.reef_tank").state == STATE_OFF


async def test_a_single_channel_can_be_set_without_disturbing_the_others(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry(_state(values=(90, 90, 90)))

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.reef_tank_blue", "brightness": 51},  # ~20 %
        blocking=True,
    )

    values = next(v for name, v in client.calls if name == "set_values")
    assert values.values == (90, 20, 90)


async def test_turning_a_channel_off_leaves_the_others_alone(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry(_state(values=(90, 80, 70)))

    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: "light.reef_tank_red"}, blocking=True
    )

    values = next(v for name, v in client.calls if name == "set_values")
    assert values.values == (90, 80, 0)


async def test_lights_expose_the_mode_so_the_side_effect_is_visible(
    hass: HomeAssistant, setup_entry
) -> None:
    """Turning a light on leaves the schedule; the user must be able to see that."""
    await setup_entry()
    attributes = hass.states.get("light.reef_tank").attributes
    assert attributes["mode"] == "daycycle"
    assert attributes["following_schedule"] is True


async def test_master_off_when_every_channel_is_zero(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry(_state(values=(0, 0, 0)))
    assert hass.states.get("light.reef_tank").state == STATE_OFF


# --- mode select -------------------------------------------------------------------------


async def test_mode_select_reflects_the_device(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry(_state(mode="MAN_MODE"))
    assert hass.states.get("select.reef_tank_mode").state == "manual"


async def test_mode_select_returns_control_to_the_schedule(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry(_state(mode="MAN_MODE"))

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.reef_tank_mode", "option": "daycycle"},
        blocking=True,
    )

    assert ("set_mode", False) in client.calls
    assert hass.states.get("select.reef_tank_mode").state == "daycycle"


# --- switches ----------------------------------------------------------------------------


async def test_effect_switches_reflect_the_device(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    assert hass.states.get("switch.reef_tank_moonlight").state == STATE_OFF
    assert hass.states.get("switch.reef_tank_cloud_simulation").state == STATE_ON
    assert hass.states.get("switch.reef_tank_acclimatisation").state == STATE_OFF


async def test_toggling_an_effect_resends_the_whole_record_unchanged(
    hass: HomeAssistant, setup_entry
) -> None:
    """There is no toggle command — the record goes back whole, so nothing else may move."""
    _, client = await setup_entry()
    before = client.state.moon

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: "switch.reef_tank_moonlight"}, blocking=True
    )

    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.active is True
    # Every other field must be exactly what the device already held.
    assert sent.minimum == before.minimum
    assert sent.maximum == before.maximum
    assert sent.color == before.color
    assert sent.start == before.start
    assert sent.end == before.end
    assert sent.cycle == before.cycle


async def test_cloud_switch_preserves_the_settings(hass: HomeAssistant, setup_entry) -> None:
    _, client = await setup_entry()
    before = client.state.cloud

    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.reef_tank_cloud_simulation"},
        blocking=True,
    )

    sent = next(v for name, v in client.calls if name == "set_cloud")
    assert sent.active is False
    assert sent.max_per_day == before.max_per_day
    assert sent.probability == before.probability
    assert sent.min_duration_minutes == before.min_duration_minutes
    assert sent.max_duration_minutes == before.max_duration_minutes


# --- numbers -----------------------------------------------------------------------------


async def test_effect_numbers_reflect_the_device(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    assert float(hass.states.get("number.reef_tank_moonlight_maximum").state) == 30
    assert float(hass.states.get("number.reef_tank_cloud_probability").state) == 65
    # Wire held minDuration 600 s; the entity must show minutes.
    assert float(hass.states.get("number.reef_tank_cloud_minimum_duration").state) == 10


async def test_setting_a_number_changes_only_that_field(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry()
    before = client.state.cloud

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.reef_tank_cloud_probability", "value": 25},
        blocking=True,
    )

    sent = next(v for name, v in client.calls if name == "set_cloud")
    assert sent.probability == 25
    assert sent.max_per_day == before.max_per_day
    assert sent.min_intensity == before.min_intensity
    assert sent.active == before.active


async def test_a_number_is_unavailable_until_its_record_has_been_read(
    hass: HomeAssistant, setup_entry
) -> None:
    """Writing from defaults would silently discard the user's real settings."""
    state = _state()
    state.cloud = None
    await setup_entry(state)
    assert hass.states.get("number.reef_tank_cloud_probability").state == "unavailable"


# --- sensors -----------------------------------------------------------------------------


async def test_diagnostic_sensors(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    assert hass.states.get("sensor.reef_tank_mode").state == "daycycle"
    assert float(hass.states.get("sensor.reef_tank_output_level").state) == 90


async def test_output_level_sensor_breaks_out_the_channels(
    hass: HomeAssistant, setup_entry
) -> None:
    await setup_entry(_state(values=(90, 80, 70)))
    attributes = hass.states.get("sensor.reef_tank_output_level").attributes
    assert attributes["white"] == 90
    assert attributes["blue"] == 80
    assert attributes["red"] == 70


async def test_unloading_disconnects_the_client(hass: HomeAssistant, setup_entry) -> None:
    entry, client = await setup_entry()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert ("disconnect", None) in client.calls
