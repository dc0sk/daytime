"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daytime_sc20.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

PROBE = "custom_components.daytime_sc20.config_flow._async_probe"


async def test_user_flow_creates_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(PROBE, AsyncMock(return_value=("AA:BB:CC:DD:EE:FF", "Reef tank"))),
        patch("custom_components.daytime_sc20.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.34"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Reef tank"
    assert result["data"] == {CONF_HOST: "192.168.1.34"}
    assert result["result"].unique_id == "AA:BB:CC:DD:EE:FF"


async def test_user_flow_reports_an_unreachable_device(hass: HomeAssistant) -> None:
    from custom_components.daytime_sc20.api import SC20ConnectionError

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(side_effect=SC20ConnectionError("nope"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.99"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    # The form must stay usable so the user can correct the address.
    with (
        patch(PROBE, AsyncMock(return_value=("AA:BB:CC:DD:EE:FF", "Reef tank"))),
        patch("custom_components.daytime_sc20.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.34"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_reports_an_unexpected_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(side_effect=RuntimeError("boom"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.99"}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_the_same_controller_cannot_be_added_twice(hass: HomeAssistant) -> None:
    """Re-adding should update the address rather than create a duplicate device."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.34"}, unique_id="AA:BB:CC:DD:EE:FF"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, AsyncMock(return_value=("AA:BB:CC:DD:EE:FF", "Reef tank"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.50"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.50"


async def test_host_is_trimmed(hass: HomeAssistant) -> None:
    """A pasted address often carries whitespace."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    probe = AsyncMock(return_value=("AA:BB:CC:DD:EE:FF", "Reef tank"))
    with (
        patch(PROBE, probe),
        patch("custom_components.daytime_sc20.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "  192.168.1.34  "}
        )

    assert probe.call_args.args[1] == "192.168.1.34"
    assert result["data"] == {CONF_HOST: "192.168.1.34"}


async def test_options_flow_sets_the_scan_interval(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.34"}, unique_id="AA:BB:CC:DD:EE:FF"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 15


@pytest.mark.parametrize("bad_value", [0, 4, 301, 10000])
async def test_options_flow_rejects_an_out_of_range_interval(
    hass: HomeAssistant, bad_value: int
) -> None:
    """Too short would hammer a device with 27 KB of heap; too long is not an update."""
    import voluptuous as vol

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.34"}, unique_id="AA:BB:CC:DD:EE:FF"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: bad_value}
        )


async def test_default_scan_interval_is_offered(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.34"}, unique_id="AA:BB:CC:DD:EE:FF"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == CONF_SCAN_INTERVAL)
    assert key.default() == DEFAULT_SCAN_INTERVAL
