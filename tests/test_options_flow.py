"""Tests for the configuration frontend.

The options flow writes to the lamp, not to Home Assistant, so what matters here is that a
page sends exactly what the user asked for and nothing else — and that a page which cannot
build a valid schedule refuses rather than writing a broken one.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.api import Daycycle, DaycycleDescription
from custom_components.daytime_sc20.const import BACKUP_STORAGE_KEY, CONF_SCAN_INTERVAL, DOMAIN

from .test_entities import FakeClient, _state


class ConfigClient(FakeClient):
    """Adds the daycycle write path the entity tests do not need."""

    async def async_set_daycycle(self, daycycle, description=None):
        self.calls.append(("set_daycycle", daycycle))
        self.calls.append(("set_description", description))
        self.state.daycycle = daycycle
        if description is not None:
            self.state.description = description
        return daycycle


@pytest.fixture
async def entry(hass: HomeAssistant):
    client = ConfigClient(_state())
    # Give it the description the real device was observed holding.
    client.state.description = DaycycleDescription(
        conf_id=-1,
        expert_mode=False,
        start=360,
        end=1320,
        sunrise=120,
        sunset=120,
        intensity=90,
        individual=False,
        intensities=(85, 85, 85),
    )
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.34"},
        unique_id="AA:BB:CC:DD:EE:FF",
        title="Reef tank",
    )
    config_entry.add_to_hass(hass)
    with patch("custom_components.daytime_sc20.SC20Client", return_value=client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry, client


async def _open(hass: HomeAssistant, entry_id: str, step: str) -> dict:
    """Open the menu and pick one page."""
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_menu_lists_the_vendor_pages(hass: HomeAssistant, entry) -> None:
    config_entry, _ = entry
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "daycycle",
        "moonlight",
        "clouds",
        "acclimatisation",
        "connection",
    ]


# --- daycycle ----------------------------------------------------------------------------


async def test_daycycle_form_is_prefilled_from_the_device(hass: HomeAssistant, entry) -> None:
    """The page must open showing what the lamp is actually running."""
    config_entry, _ = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")
    assert result["type"] is FlowResultType.FORM

    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["start"] == "06:00:00"
    assert defaults["end"] == "22:00:00"
    assert defaults["sunrise"] == 120
    assert defaults["sunset"] == 120
    assert defaults["brightness"] == 90


async def test_daycycle_prefill_ignores_stale_per_colour_values(
    hass: HomeAssistant, entry
) -> None:
    """With `individual` off the device runs `intensity`, not `intensities`.

    The real device was holding intensity:90 alongside a stale intensities:85,85,85 and
    running 90. Showing 85 would misreport the tank.
    """
    config_entry, _ = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["level_white"] == 90
    assert defaults["level_blue"] == 90
    assert defaults["level_red"] == 90


async def test_saving_the_daycycle_writes_the_expected_curve(
    hass: HomeAssistant, entry
) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "start": "07:00:00",
            "end": "21:00:00",
            "sunrise": 60,
            "sunset": 90,
            "brightness": 80,
            "individual": False,
            "level_white": 80,
            "level_blue": 80,
            "level_red": 80,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    written = next(v for name, v in client.calls if name == "set_daycycle")
    assert [[p.minute, *p.values] for p in written.setpoints] == [
        [0, 0, 0, 0],
        [420, 0, 0, 0],  # 07:00 dark
        [480, 80, 80, 80],  # +60 min sunrise
        [1170, 80, 80, 80],  # 21:00 - 90 min sunset
        [1260, 0, 0, 0],  # 21:00 dark
        [1440, 0, 0, 0],
    ]


async def test_saving_the_daycycle_sends_the_matching_description(
    hass: HomeAssistant, entry
) -> None:
    """The sidecar must describe the curve actually written, or the vendor app lies."""
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "start": "07:00:00",
            "end": "21:00:00",
            "sunrise": 60,
            "sunset": 90,
            "brightness": 80,
            "individual": False,
            "level_white": 80,
            "level_blue": 80,
            "level_red": 80,
        },
    )
    described = next(v for name, v in client.calls if name == "set_description")
    assert described.start == 420
    assert described.end == 1260
    assert described.sunrise == 60
    assert described.sunset == 90
    assert described.intensity == 80
    # Whatever was there before, what got written is a plain sunrise/sunset shape.
    assert described.expert_mode is False


async def test_per_colour_brightness_is_honoured(hass: HomeAssistant, entry) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "start": "06:00:00",
            "end": "22:00:00",
            "sunrise": 60,
            "sunset": 60,
            "brightness": 90,
            "individual": True,
            "level_white": 70,
            "level_blue": 100,
            "level_red": 40,
        },
    )
    written = next(v for name, v in client.calls if name == "set_daycycle")
    peak = next(p for p in written.setpoints if p.minute == 420)
    assert peak.values == (70, 100, 40)


async def test_saving_the_daycycle_backs_up_the_old_one(
    hass: HomeAssistant, entry, hass_storage
) -> None:
    """Same guarantee the service gives — the controller has no undo."""
    config_entry, client = entry
    before = [[p.minute, *p.values] for p in client.state.daycycle.setpoints]

    result = await _open(hass, config_entry.entry_id, "daycycle")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "start": "07:00:00",
            "end": "21:00:00",
            "sunrise": 60,
            "sunset": 90,
            "brightness": 80,
            "individual": False,
            "level_white": 80,
            "level_blue": 80,
            "level_red": 80,
        },
    )
    await hass.async_block_till_done()

    history = hass_storage[BACKUP_STORAGE_KEY]["data"]["192.168.1.34"]
    assert history[-1]["setpoints"] == before


@pytest.mark.parametrize(
    ("form", "why"),
    [
        (
            {"start": "22:00:00", "end": "06:00:00", "sunrise": 0, "sunset": 0},
            "day ends before it starts",
        ),
        (
            {"start": "08:00:00", "end": "10:00:00", "sunrise": 90, "sunset": 90},
            "ramps are longer than the day",
        ),
    ],
)
async def test_an_impossible_day_is_refused_before_anything_is_sent(
    hass: HomeAssistant, entry, form: dict, why: str
) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "daycycle")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **form,
            "brightness": 80,
            "individual": False,
            "level_white": 80,
            "level_blue": 80,
            "level_red": 80,
        },
    )

    assert result["type"] is FlowResultType.FORM, why
    assert result["errors"] == {"base": "invalid_daycycle"}
    assert not any(name == "set_daycycle" for name, _ in client.calls), why


# --- moonlight ---------------------------------------------------------------------------


async def test_moonlight_form_is_prefilled_and_saves(hass: HomeAssistant, entry) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "moonlight")

    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["min_level"] == 2
    assert defaults["max_level"] == 30
    assert defaults["color"] == ["b"]
    # The device's window wraps past midnight; the form must show it as-is.
    assert defaults["start"] == "22:00:00"
    assert defaults["end"] == "06:00:00"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "active": True,
            "lunar_cycle": True,
            "min_level": 5,
            "max_level": 40,
            "color": ["b", "w"],
            "start": "21:30:00",
            "end": "05:30:00",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.active is True
    assert sent.minimum == 5
    assert sent.maximum == 40
    assert sent.color == "bw"
    assert sent.start == 1290
    assert sent.end == 330


async def test_moonlight_keeps_a_colour_when_none_is_chosen(hass: HomeAssistant, entry) -> None:
    """An empty colour string would leave moonlight enabled but emitting nothing."""
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "moonlight")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "active": True,
            "lunar_cycle": True,
            "min_level": 5,
            "max_level": 40,
            "color": [],
            "start": "22:00:00",
            "end": "06:00:00",
        },
    )
    sent = next(v for name, v in client.calls if name == "set_moon")
    assert sent.color == "b", "should fall back to what the device already had"


# --- clouds ------------------------------------------------------------------------------


async def test_cloud_form_shows_human_units_and_saves(hass: HomeAssistant, entry) -> None:
    """The form works in minutes and cloud strength; the wire quirks stay below."""
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "clouds")

    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["min_duration"] == 10
    assert defaults["max_duration"] == 25
    assert defaults["min_level"] == 0
    assert defaults["max_level"] == 40
    assert defaults["probability"] == 65

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "active": True,
            "max_per_day": 200,
            "min_duration": 5,
            "max_duration": 20,
            "min_level": 10,
            "max_level": 50,
            "probability": 70,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    sent = next(v for name, v in client.calls if name == "set_cloud")
    assert sent.min_duration_minutes == 5
    assert sent.max_duration_minutes == 20
    assert sent.min_intensity == 10
    assert sent.max_intensity == 50


async def test_cloud_form_rejects_a_reversed_range(hass: HomeAssistant, entry) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "clouds")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "active": True,
            "max_per_day": 200,
            "min_duration": 5,
            "max_duration": 20,
            "min_level": 60,
            "max_level": 20,
            "probability": 70,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"min_level": "min_above_max"}
    assert not any(name == "set_cloud" for name, _ in client.calls)


async def test_cloud_form_rejects_a_reversed_duration(hass: HomeAssistant, entry) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "clouds")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "active": True,
            "max_per_day": 200,
            "min_duration": 25,
            "max_duration": 5,
            "min_level": 10,
            "max_level": 50,
            "probability": 70,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"min_duration": "min_above_max"}
    assert not any(name == "set_cloud" for name, _ in client.calls)


# --- acclimatisation ---------------------------------------------------------------------


async def test_acclimatisation_preserves_the_current_day(hass: HomeAssistant, entry) -> None:
    """Resetting the day counter would silently restart a weeks-long ramp."""
    config_entry, client = entry
    client.state.acclimate = type(client.state.acclimate)(
        active=True, paused=False, duration_days=30, intensity_reduction=50, current_day=11
    )

    result = await _open(hass, config_entry.entry_id, "acclimatisation")
    assert result["description_placeholders"]["current_day"] == "11"

    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"active": True, "paused": False, "duration_days": 45, "reduction": 40},
    )
    sent = next(v for name, v in client.calls if name == "set_acclimate")
    assert sent.duration_days == 45
    assert sent.intensity_reduction == 40
    assert sent.current_day == 11


# --- connection --------------------------------------------------------------------------


async def test_connection_page_stores_in_home_assistant_not_on_the_lamp(
    hass: HomeAssistant, entry
) -> None:
    config_entry, client = entry
    result = await _open(hass, config_entry.entry_id, "connection")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_SCAN_INTERVAL] == 15
    assert not any(name.startswith("set_") for name, _ in client.calls)


# --- the curve generator itself ----------------------------------------------------------


def test_easy_mode_reproduces_a_real_devices_schedule() -> None:
    """The strongest check available: a matched input/output pair from real hardware.

    A device was captured holding both this DSCRPTN and this DYCL. If the generator is
    right, one produces the other exactly.
    """
    described = DaycycleDescription(
        conf_id=-1,
        expert_mode=False,
        start=360,
        end=1320,
        sunrise=120,
        sunset=120,
        intensity=90,
        individual=False,
        intensities=(85, 85, 85),
    )
    generated = described.to_daycycle()
    assert [[p.minute, *p.values] for p in generated.setpoints] == [
        [0, 0, 0, 0],
        [360, 0, 0, 0],
        [480, 90, 90, 90],
        [1200, 90, 90, 90],
        [1320, 0, 0, 0],
        [1440, 0, 0, 0],
    ]


def test_easy_mode_collapses_ramps_that_meet() -> None:
    """Sunrise running straight into sunset gives a single peak, not a duplicate minute."""
    described = DaycycleDescription(start=600, end=720, sunrise=60, sunset=60, intensity=100)
    generated = described.to_daycycle()
    minutes = [p.minute for p in generated.setpoints]
    assert minutes == [0, 600, 660, 720, 1440]
    assert len(set(minutes)) == len(minutes)


def test_easy_mode_handles_a_day_starting_at_midnight() -> None:
    """A start of 0 must not emit two rows at minute 0."""
    described = DaycycleDescription(start=0, end=1200, sunrise=60, sunset=60, intensity=50)
    generated = described.to_daycycle()
    minutes = [p.minute for p in generated.setpoints]
    assert minutes[0] == 0
    assert len(set(minutes)) == len(minutes)


def test_generated_schedules_always_pass_validation() -> None:
    """Whatever the form produces must be writable, or the page is a trap."""
    from custom_components.daytime_sc20.api import protocol

    for start, end, sunrise, sunset in (
        (0, 1440, 0, 0),
        (360, 1320, 120, 120),
        (600, 720, 60, 60),
        (1, 1439, 1, 1),
        (0, 60, 30, 30),
    ):
        described = DaycycleDescription(
            start=start, end=end, sunrise=sunrise, sunset=sunset, intensity=75
        )
        protocol.validate_daycycle(described.to_daycycle())


def test_levels_ignores_intensities_unless_individual_is_set() -> None:
    assert DaycycleDescription(intensity=90, intensities=(85, 85, 85)).levels == (90, 90, 90)
    assert DaycycleDescription(
        intensity=90, individual=True, intensities=(10, 20, 30)
    ).levels == (10, 20, 30)


def test_daycycle_is_a_daycycle() -> None:
    assert isinstance(DaycycleDescription().to_daycycle(), Daycycle)


@pytest.mark.parametrize("bad_value", [0, 4, 301, 10000])
async def test_connection_page_rejects_an_out_of_range_interval(
    hass: HomeAssistant, entry, bad_value: int
) -> None:
    """Too short would hammer a device with 27 KB of heap; too long is not an update."""
    import voluptuous as vol

    config_entry, _ = entry
    result = await _open(hass, config_entry.entry_id, "connection")
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: bad_value}
        )


async def test_connection_page_offers_the_current_interval(hass: HomeAssistant, entry) -> None:
    config_entry, _ = entry
    hass.config_entries.async_update_entry(config_entry, options={CONF_SCAN_INTERVAL: 45})
    result = await _open(hass, config_entry.entry_id, "connection")
    key = next(k for k in result["data_schema"].schema if str(k) == CONF_SCAN_INTERVAL)
    assert key.default() == 45
