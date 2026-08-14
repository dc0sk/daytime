"""Tests for the moonlight time entities and the firmware update entities."""

from __future__ import annotations

import dataclasses
from datetime import time
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.api import SC20State
from custom_components.daytime_sc20.const import DOMAIN

from .test_entities import FakeClient, _state


@pytest.fixture
async def setup_entry(hass: HomeAssistant):
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


# --- moonlight times ---------------------------------------------------------------------


async def test_moonlight_times_are_time_entities_not_numbers(
    hass: HomeAssistant, setup_entry
) -> None:
    """They are clock times, so they get a HH:MM field rather than a count of minutes."""
    await setup_entry()
    assert hass.states.get("time.reef_tank_moonlight_start") is not None
    assert hass.states.get("time.reef_tank_moonlight_end") is not None
    # The number versions must be gone, or two entities would edit the same field.
    assert hass.states.get("number.reef_tank_moonlight_start") is None
    assert hass.states.get("number.reef_tank_moonlight_end") is None


async def test_moonlight_times_show_the_devices_clock_times(
    hass: HomeAssistant, setup_entry
) -> None:
    """Minute 1320 is 22:00 and minute 360 is 06:00 — the real device's window."""
    await setup_entry()
    assert hass.states.get("time.reef_tank_moonlight_start").state == "22:00:00"
    assert hass.states.get("time.reef_tank_moonlight_end").state == "06:00:00"


async def test_setting_a_moonlight_time_converts_back_to_minutes(
    hass: HomeAssistant, setup_entry
) -> None:
    _, client = await setup_entry()

    await hass.services.async_call(
        "time",
        "set_value",
        {ATTR_ENTITY_ID: "time.reef_tank_moonlight_start", "time": "21:30:00"},
        blocking=True,
    )

    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.start == 1290


async def test_setting_a_moonlight_time_leaves_the_rest_alone(
    hass: HomeAssistant, setup_entry
) -> None:
    """The whole moon record is resent, so nothing else may move."""
    _, client = await setup_entry()
    before = client.state.moon

    await hass.services.async_call(
        "time",
        "set_value",
        {ATTR_ENTITY_ID: "time.reef_tank_moonlight_end", "time": "05:15:00"},
        blocking=True,
    )

    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.end == 315
    assert sent.start == before.start
    assert sent.minimum == before.minimum
    assert sent.maximum == before.maximum
    assert sent.color == before.color
    assert sent.active == before.active


async def test_a_window_that_wraps_past_midnight_is_left_alone(
    hass: HomeAssistant, setup_entry
) -> None:
    """Start after end is the normal configuration on this hardware, not an error."""
    _, client = await setup_entry()
    assert client.state.moon.start > client.state.moon.end

    await hass.services.async_call(
        "time",
        "set_value",
        {ATTR_ENTITY_ID: "time.reef_tank_moonlight_start", "time": "23:45:00"},
        blocking=True,
    )
    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.start == 1425
    assert sent.end == 360, "the end must not be 'corrected' to follow the start"


async def test_seconds_are_dropped(hass: HomeAssistant, setup_entry) -> None:
    """The device stores whole minutes; a stray 30 s must not round the minute."""
    _, client = await setup_entry()
    await hass.services.async_call(
        "time",
        "set_value",
        {ATTR_ENTITY_ID: "time.reef_tank_moonlight_start", "time": "21:30:45"},
        blocking=True,
    )
    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.start == 1290


async def test_moonlight_time_is_unavailable_before_the_record_is_read(
    hass: HomeAssistant, setup_entry
) -> None:
    state = _state()
    state.moon = None
    await setup_entry(state)
    assert hass.states.get("time.reef_tank_moonlight_start").state == "unavailable"


def test_time_conversion_round_trips() -> None:
    from custom_components.daytime_sc20.time import _to_minute, _to_time

    for minute in (0, 1, 359, 360, 720, 1319, 1320, 1439):
        assert _to_minute(_to_time(minute)) == minute
    assert _to_time(1320) == time(22, 0)
    assert _to_minute(time(6, 0)) == 360


# --- firmware update ---------------------------------------------------------------------


async def test_update_entities_exist(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    assert hass.states.get("update.reef_tank_firmware") is not None
    assert hass.states.get("update.reef_tank_web_app") is not None


async def test_up_to_date_when_installed_matches_latest(
    hass: HomeAssistant, setup_entry
) -> None:
    """The captured device runs [23, 15] and reports [23, 15] available."""
    await setup_entry()
    firmware = hass.states.get("update.reef_tank_firmware")
    assert firmware.state == STATE_OFF
    assert firmware.attributes["installed_version"] == "02.3"
    assert firmware.attributes["latest_version"] == "02.3"


async def test_update_available_when_the_device_reports_a_newer_revision(
    hass: HomeAssistant, setup_entry
) -> None:
    state = _state()
    state.device_info = dataclasses.replace(
        state.device_info, latest_revision=(24, 15), firmware_available=True
    )
    await setup_entry(state)

    firmware = hass.states.get("update.reef_tank_firmware")
    assert firmware.state == STATE_ON
    assert firmware.attributes["installed_version"] == "02.3"
    assert firmware.attributes["latest_version"] == "02.4"
    # The web app half is unchanged, so it must not also claim an update.
    assert hass.states.get("update.reef_tank_web_app").state == STATE_OFF


async def test_the_web_app_can_be_behind_on_its_own(hass: HomeAssistant, setup_entry) -> None:
    """The two halves update independently, which is why both are tracked."""
    state = _state()
    state.device_info = dataclasses.replace(state.device_info, latest_revision=(23, 16))
    await setup_entry(state)

    assert hass.states.get("update.reef_tank_firmware").state == STATE_OFF
    webapp = hass.states.get("update.reef_tank_web_app")
    assert webapp.state == STATE_ON
    assert webapp.attributes["installed_version"] == "01.5"
    assert webapp.attributes["latest_version"] == "01.6"


async def test_a_failed_upstream_check_does_not_show_a_phantom_downgrade(
    hass: HomeAssistant, setup_entry
) -> None:
    """With no internet the device reports [0, 0]; that is 'unknown', not 'version 00.0'."""
    state = _state()
    state.device_info = dataclasses.replace(state.device_info, latest_revision=(0, 0))
    await setup_entry(state)

    firmware = hass.states.get("update.reef_tank_firmware")
    assert firmware.attributes["latest_version"] == "02.3"
    assert firmware.state == STATE_OFF


async def test_update_entity_cannot_install(hass: HomeAssistant, setup_entry) -> None:
    """Flashing a live aquarium controller is not something an automation should trigger."""
    from homeassistant.components.update import UpdateEntityFeature

    await setup_entry()
    firmware = hass.states.get("update.reef_tank_firmware")
    assert not firmware.attributes["supported_features"] & UpdateEntityFeature.INSTALL


async def test_update_entity_points_at_the_devices_own_updater(
    hass: HomeAssistant, setup_entry
) -> None:
    await setup_entry()
    firmware = hass.states.get("update.reef_tank_firmware")
    assert firmware.attributes["update_via"] == "http://192.168.1.34/update"
    assert firmware.attributes["device_reports_update_available"] is False
