"""Service tests.

`set_daycycle` and `load_scenario` replace the user's real lighting programme on a device
that neither acknowledges writes nor offers an undo, so most of what is checked here is
that a bad call is refused before anything is sent, and that a good one saves the old
programme first.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.const import BACKUP_STORAGE_KEY, DOMAIN

from .test_entities import FakeClient, _state

SCENARIO_DIR = Path(__file__).parent.parent / "docs" / "protocol" / "scenarios"

VALID_SETPOINTS = [
    [0, 0, 0, 0],
    [420, 0, 0, 0],
    [480, 80, 80, 80],
    [1200, 80, 80, 80],
    [1320, 0, 0, 0],
    [1440, 0, 0, 0],
]


class RecordingClient(FakeClient):
    """Adds the daycycle write path the entity tests do not need."""

    async def async_set_daycycle(self, daycycle, description=None):
        self.calls.append(("set_daycycle", daycycle))
        self.state.daycycle = daycycle
        return daycycle


@pytest.fixture
async def setup_entry(hass: HomeAssistant):
    async def _setup() -> tuple[MockConfigEntry, RecordingClient]:
        client = RecordingClient(_state())
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


async def test_set_daycycle_writes_the_programme(hass: HomeAssistant, setup_entry) -> None:
    entry, client = await setup_entry()

    await hass.services.async_call(
        DOMAIN,
        "set_daycycle",
        {"config_entry_id": entry.entry_id, "setpoints": VALID_SETPOINTS},
        blocking=True,
    )

    written = next(v for name, v in client.calls if name == "set_daycycle")
    assert [[p.minute, *p.values] for p in written.setpoints] == VALID_SETPOINTS


async def test_set_daycycle_backs_up_the_old_programme_first(
    hass: HomeAssistant, setup_entry, hass_storage
) -> None:
    """There is no undo on the device, so the previous schedule must be recoverable."""
    entry, client = await setup_entry()
    before = [[p.minute, *p.values] for p in client.state.daycycle.setpoints]

    await hass.services.async_call(
        DOMAIN,
        "set_daycycle",
        {"config_entry_id": entry.entry_id, "setpoints": VALID_SETPOINTS},
        blocking=True,
    )
    await hass.async_block_till_done()

    saved = hass_storage[BACKUP_STORAGE_KEY]["data"]
    history = saved["192.168.1.34"]
    assert history[-1]["setpoints"] == before
    assert before != VALID_SETPOINTS, "the test would be vacuous if these matched"


@pytest.mark.parametrize(
    ("setpoints", "reason"),
    [
        ([[10, 0, 0, 0], [1440, 0, 0, 0]], "must start at minute 0"),
        ([[0, 0, 0, 0], [1400, 0, 0, 0]], "must end at minute 1440"),
        ([[0, 0, 0, 0], [900, 1, 1, 1], [480, 1, 1, 1], [1440, 0, 0, 0]], "must be sorted"),
        ([[0, 0, 0, 0], [480, 101, 0, 0], [1440, 0, 0, 0]], "value out of range"),
        ([[0, 0, 0, 0], [480, -1, 0, 0], [1440, 0, 0, 0]], "negative value"),
    ],
)
async def test_set_daycycle_refuses_a_bad_programme(
    hass: HomeAssistant, setup_entry, setpoints: list[list[int]], reason: str
) -> None:
    """Nothing may reach the device: a bad write would destroy a working schedule."""
    entry, client = await setup_entry()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_daycycle",
            {"config_entry_id": entry.entry_id, "setpoints": setpoints},
            blocking=True,
        )

    assert not any(name == "set_daycycle" for name, _ in client.calls), reason


async def test_set_daycycle_refuses_too_many_setpoints(
    hass: HomeAssistant, setup_entry
) -> None:
    entry, client = await setup_entry()
    setpoints = [[0, 0, 0, 0]]
    setpoints += [[m, 50, 50, 50] for m in range(10, 310, 10)]
    setpoints += [[1440, 0, 0, 0]]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_daycycle",
            {"config_entry_id": entry.entry_id, "setpoints": setpoints},
            blocking=True,
        )
    assert not any(name == "set_daycycle" for name, _ in client.calls)


async def test_get_daycycle_returns_both_representations(
    hass: HomeAssistant, setup_entry
) -> None:
    """Setpoints for automations, and .scen text for saving or sharing."""
    entry, client = await setup_entry()
    expected = [[p.minute, *p.values] for p in client.state.daycycle.setpoints]

    response = await hass.services.async_call(
        DOMAIN,
        "get_daycycle",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert response["setpoints"] == expected
    assert response["scen"] == (
        "[[0,0,0,0],[360,0,0,0],[480,90,90,90],[1200,90,90,90],[1320,0,0,0],[1440,0,0,0]]"
    )


async def test_save_scenario_writes_a_vendor_compatible_file(
    hass: HomeAssistant, setup_entry, tmp_path: Path
) -> None:
    entry, _ = await setup_entry()
    target = tmp_path / "backup.scen"

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN,
            "save_scenario",
            {"config_entry_id": entry.entry_id, "filename": str(target)},
            blocking=True,
        )

    # Must be loadable straight back, and shaped like the vendor's own files.
    from custom_components.daytime_sc20.api import protocol

    written = target.read_text()
    assert protocol.parse_scen_file(written) is not None
    assert written.startswith("[[0,")


async def test_load_scenario_writes_a_vendor_file_to_the_device(
    hass: HomeAssistant, setup_entry
) -> None:
    entry, client = await setup_entry()
    scenario = SCENARIO_DIR / "freshwater-2-daycycle.scen"

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await hass.services.async_call(
            DOMAIN,
            "load_scenario",
            {"config_entry_id": entry.entry_id, "filename": str(scenario)},
            blocking=True,
        )

    written = next(v for name, v in client.calls if name == "set_daycycle")
    assert [[p.minute, *p.values] for p in written.setpoints] == [
        [0, 0, 0, 0],
        [420, 0, 0, 0],
        [430, 0, 0, 80],
        [480, 100, 100, 100],
        [1080, 100, 100, 100],
        [1140, 0, 0, 80],
        [1150, 0, 0, 0],
        [1440, 0, 0, 0],
    ]


async def test_load_scenario_refuses_a_path_outside_the_allowlist(
    hass: HomeAssistant, setup_entry
) -> None:
    """Otherwise a service call could read any file the HA process can reach."""
    entry, client = await setup_entry()

    with (
        patch.object(hass.config, "is_allowed_path", return_value=False),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            DOMAIN,
            "load_scenario",
            {"config_entry_id": entry.entry_id, "filename": "/etc/shadow"},
            blocking=True,
        )
    assert not any(name == "set_daycycle" for name, _ in client.calls)


async def test_load_scenario_refuses_a_file_that_is_not_a_scenario(
    hass: HomeAssistant, setup_entry, tmp_path: Path
) -> None:
    entry, client = await setup_entry()
    rubbish = tmp_path / "notes.txt"
    rubbish.write_text("this is not a scenario")

    with (
        patch.object(hass.config, "is_allowed_path", return_value=True),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            DOMAIN,
            "load_scenario",
            {"config_entry_id": entry.entry_id, "filename": str(rubbish)},
            blocking=True,
        )
    assert not any(name == "set_daycycle" for name, _ in client.calls)


async def test_services_reject_an_unknown_config_entry(
    hass: HomeAssistant, setup_entry
) -> None:
    await setup_entry()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_daycycle",
            {"config_entry_id": "does-not-exist", "setpoints": VALID_SETPOINTS},
            blocking=True,
        )
