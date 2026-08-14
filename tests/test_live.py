"""Live tests against real hardware. Skipped unless SC20_HOST is set.

    SC20_HOST=192.168.1.34 .venv/bin/python -m pytest tests/test_live.py -v

These set up the whole integration against an actual controller and read what it reports.
Everything here is READ-ONLY: no mode change, no channel write, no schedule write. A device
running someone's aquarium is not a test fixture, and a failing assertion must never leave
the tank in a different state than it started in.

Keep this file that way. If a write ever needs testing on hardware, use
`tools/sc20_probe.py --allow-writes`, which confirms each command and restores afterwards.
"""

from __future__ import annotations

import os

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.api import CHANNEL_NAMES
from custom_components.daytime_sc20.const import DOMAIN

LIVE_HOST = os.environ.get("SC20_HOST")

pytestmark = pytest.mark.skipif(
    not LIVE_HOST, reason="set SC20_HOST to run against real hardware"
)


@pytest.fixture(autouse=True)
def _allow_the_device(socket_enabled):
    """Let outbound sockets reach the one controller under test, and nothing else.

    Home Assistant's test plugin pins sockets to loopback so a stray real network call
    cannot hide in a test run. It applies that in its own fixture, so the allowance has to
    be re-applied here — and reverted afterwards, so the rest of the suite stays sealed.
    """
    import pytest_socket

    pytest_socket.socket_allow_hosts([LIVE_HOST, "127.0.0.1", "::1"], allow_unix_socket=True)
    yield
    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1"], allow_unix_socket=True)


@pytest.fixture
async def live_entry(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: LIVE_HOST}, unique_id="live", title="SC20"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_against_real_hardware(hass: HomeAssistant, live_entry) -> None:
    """The whole integration comes up and every platform produces entities."""
    state = live_entry.runtime_data.client.state
    assert state.device_info is not None
    assert state.clock is not None
    assert state.values is not None
    assert state.daycycle is not None
    assert state.moon is not None
    assert state.cloud is not None
    assert state.acclimate is not None

    print(f"\n  device      {state.device_info.name}")
    print(f"  firmware    {state.device_info.firmware_version}")
    print(f"  webapp      {state.device_info.webapp_version}")
    print(f"  mode        {state.mode}")
    print(f"  values      {state.values.values}")
    print(f"  setpoints   {[[p.minute, *p.values] for p in state.daycycle.setpoints]}")


async def test_all_expected_entities_exist(hass: HomeAssistant, live_entry) -> None:
    expected = [
        "light.sc20",
        *[f"light.sc20_{name.lower()}" for name in CHANNEL_NAMES],
        "select.sc20_mode",
        "switch.sc20_moonlight",
        "switch.sc20_cloud_simulation",
        "switch.sc20_acclimatisation",
        "sensor.sc20_mode",
        "sensor.sc20_output_level",
        "number.sc20_moonlight_maximum",
        "number.sc20_cloud_probability",
    ]
    missing = [entity_id for entity_id in expected if hass.states.get(entity_id) is None]
    assert not missing, f"missing entities: {missing}"


async def test_reported_values_are_plausible(hass: HomeAssistant, live_entry) -> None:
    """Sanity-check the decoded values against what the hardware could possibly mean."""
    state = live_entry.runtime_data.client.state

    assert len(state.values.values) == len(CHANNEL_NAMES)
    assert all(0 <= v <= 100 for v in state.values.values), state.values

    # A schedule must span the whole day, in order.
    minutes = [p.minute for p in state.daycycle.setpoints]
    assert minutes[0] == 0
    assert minutes[-1] == 1440
    assert minutes == sorted(minutes)
    assert len(minutes) <= 30

    # Cloud durations decoded from seconds should be sane minute counts, not thousands.
    assert 0 <= state.cloud.min_duration_minutes <= 120, state.cloud
    assert 0 <= state.cloud.max_duration_minutes <= 120, state.cloud
    assert state.cloud.min_duration_minutes <= state.cloud.max_duration_minutes

    # Cloud strengths are percentages after the inversion.
    assert 0 <= state.cloud.min_intensity <= 100
    assert 0 <= state.cloud.max_intensity <= 100

    # The device holds the local wall-clock time of wherever it is installed, and its
    # schedule runs off that — so drift means the lighting day is shifted. Compare against
    # this machine's local time, not Home Assistant's: the test harness configures hass to
    # US/Pacific regardless of where the hardware actually is.
    from datetime import datetime

    drift = abs((datetime.now() - state.clock.timestamp).total_seconds())
    assert drift < 3600, (
        f"device clock reads {state.clock.timestamp}, this machine reads {datetime.now()}"
        f" — off by {drift / 60:.0f} minutes"
    )


async def test_mode_is_not_changed_by_setting_up(hass: HomeAssistant, live_entry) -> None:
    """Setting up the integration must not disturb a running aquarium."""
    state = live_entry.runtime_data.client.state
    assert state.mode in ("DAYCL_MODE", "MAN_MODE")
    # Re-read straight from the device rather than trusting cached state.
    fresh = await live_entry.runtime_data.client.async_refresh()
    assert fresh.mode == state.mode


async def test_server_log_scraping_works_on_real_hardware(
    hass: HomeAssistant, live_entry
) -> None:
    log = live_entry.runtime_data.client.state.server_log
    assert log is not None
    assert log.free_heap and log.free_heap > 0
    assert log.uptime_minutes is not None
    print(f"\n  heap {log.free_heap} B, up {log.uptime_minutes} min, {log.operating_hours} h")


async def test_config_frontend_prefills_from_real_hardware(
    hass: HomeAssistant, live_entry
) -> None:
    """Open every configuration page against the device and check it reflects reality.

    Read-only: each page is opened and its defaults inspected, never submitted.
    """
    from custom_components.daytime_sc20.api import protocol

    state = live_entry.runtime_data.client.state

    result = await hass.config_entries.options.async_init(live_entry.entry_id)
    assert result["type"].value == "menu"

    for step in ("daycycle", "moonlight", "clouds", "acclimatisation", "connection"):
        opened = await hass.config_entries.options.async_init(live_entry.entry_id)
        page = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": step}
        )
        assert page["type"].value == "form", f"{step} did not open: {page}"
        defaults = {str(k): k.default() for k in page["data_schema"].schema}
        print(f"\n  {step}: {defaults}")

    # The daycycle page must describe the schedule the lamp is actually running: feeding
    # its own description back through the generator has to reproduce its own DYCL.
    assert state.description is not None
    if not state.description.expert_mode:
        regenerated = state.description.to_daycycle()
        assert [[p.minute, *p.values] for p in regenerated.setpoints] == [
            [p.minute, *p.values] for p in state.daycycle.setpoints
        ], "the easy-mode form would not round-trip this device's schedule"
        protocol.validate_daycycle(regenerated)
