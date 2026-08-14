"""Services for the daytime SC20.

The schedule lives here rather than in an entity because a lighting programme is a list of
timed setpoints — there is no entity shape that fits it.

Anything that overwrites the schedule snapshots the current one to HA storage first. The
device does not acknowledge writes and has no undo, so a bad `set_daycycle` would otherwise
destroy a configuration the user may have spent real time on.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import (
    CHANNEL_COUNT,
    DAY_MINUTES,
    Daycycle,
    SC20Error,
    SC20ValidationError,
    Setpoint,
    protocol,
)
from .backup import async_backup_daycycle
from .const import (
    ATTR_END_TIME,
    ATTR_FILENAME,
    ATTR_SETPOINTS,
    ATTR_SPEED_FACTOR,
    ATTR_START_TIME,
    DOMAIN,
    SERVICE_GET_DAYCYCLE,
    SERVICE_LOAD_SCENARIO,
    SERVICE_PREVIEW_CURVE,
    SERVICE_SAVE_SCENARIO,
    SERVICE_SET_CLOCK,
    SERVICE_SET_DAYCYCLE,
)
from .coordinator import SC20Coordinator

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

_ENTRY_SCHEMA = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}

SET_DAYCYCLE_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Required(ATTR_SETPOINTS): vol.All(
            cv.ensure_list,
            [vol.All(cv.ensure_list, [vol.Coerce(int)], vol.Length(min=CHANNEL_COUNT + 1))],
            vol.Length(min=1),
        ),
    }
)

LOAD_SCENARIO_SCHEMA = vol.Schema({**_ENTRY_SCHEMA, vol.Required(ATTR_FILENAME): cv.string})
SAVE_SCENARIO_SCHEMA = vol.Schema({**_ENTRY_SCHEMA, vol.Required(ATTR_FILENAME): cv.string})
GET_DAYCYCLE_SCHEMA = vol.Schema(_ENTRY_SCHEMA)
SET_CLOCK_SCHEMA = vol.Schema(_ENTRY_SCHEMA)

PREVIEW_CURVE_SCHEMA = vol.Schema(
    {
        **_ENTRY_SCHEMA,
        vol.Optional(ATTR_SPEED_FACTOR, default=100): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000)
        ),
        vol.Optional(ATTR_START_TIME, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=DAY_MINUTES)
        ),
        vol.Optional(ATTR_END_TIME, default=DAY_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=DAY_MINUTES)
        ),
    }
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> SC20Coordinator:
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry: ConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(f"no daytime SC20 config entry with id {entry_id}")
    if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
        raise ServiceValidationError(f"the SC20 config entry {entry_id} is not loaded")
    return entry.runtime_data


def _daycycle_from_rows(rows: list[list[int]]) -> Daycycle:
    """Build a validated Daycycle from raw service data."""
    try:
        setpoints = tuple(
            Setpoint(minute=row[0], values=tuple(row[1 : CHANNEL_COUNT + 1])) for row in rows
        )
        daycycle = Daycycle(setpoints=setpoints)
        protocol.validate_daycycle(daycycle)
    except SC20ValidationError as err:
        raise ServiceValidationError(str(err)) from err
    return daycycle


def _rows_from_daycycle(daycycle: Daycycle) -> list[list[int]]:
    return [[p.minute, *p.values] for p in daycycle.setpoints]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services. Safe to call for every config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_DAYCYCLE):
        return

    async def set_daycycle(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        daycycle = _daycycle_from_rows(call.data[ATTR_SETPOINTS])
        await async_backup_daycycle(hass, coordinator.client)
        try:
            await coordinator.async_write_then_refresh(
                lambda: coordinator.client.async_set_daycycle(daycycle)
            )
        except SC20Error as err:
            raise HomeAssistantError(f"could not write the daycycle: {err}") from err

    async def get_daycycle(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        try:
            daycycle = await coordinator.client.async_get_daycycle()
        except SC20Error as err:
            raise HomeAssistantError(f"could not read the daycycle: {err}") from err
        return {
            "setpoints": _rows_from_daycycle(daycycle),
            "scen": protocol.encode_scen_file(daycycle),
        }

    async def load_scenario(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        filename = call.data[ATTR_FILENAME]
        if not hass.config.is_allowed_path(filename):
            raise ServiceValidationError(
                f"{filename} is outside the directories Home Assistant may read"
            )

        def read() -> str:
            with open(filename, encoding="utf-8") as handle:
                return handle.read()

        try:
            text = await hass.async_add_executor_job(read)
        except OSError as err:
            raise ServiceValidationError(f"could not read {filename}: {err}") from err

        try:
            daycycle = protocol.parse_scen_file(text)
            protocol.validate_daycycle(daycycle)
        except SC20Error as err:
            raise ServiceValidationError(f"{filename} is not a usable scenario: {err}") from err

        await async_backup_daycycle(hass, coordinator.client)
        try:
            await coordinator.async_write_then_refresh(
                lambda: coordinator.client.async_set_daycycle(daycycle)
            )
        except SC20Error as err:
            raise HomeAssistantError(f"could not write the daycycle: {err}") from err

    async def save_scenario(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        filename = call.data[ATTR_FILENAME]
        if not hass.config.is_allowed_path(filename):
            raise ServiceValidationError(
                f"{filename} is outside the directories Home Assistant may write"
            )
        try:
            daycycle = await coordinator.client.async_get_daycycle()
        except SC20Error as err:
            raise HomeAssistantError(f"could not read the daycycle: {err}") from err

        text = protocol.encode_scen_file(daycycle)

        def write() -> None:
            with open(filename, "w", encoding="utf-8") as handle:
                handle.write(text)

        try:
            await hass.async_add_executor_job(write)
        except OSError as err:
            raise HomeAssistantError(f"could not write {filename}: {err}") from err

    async def preview_curve(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        try:
            await coordinator.client.async_preview_curve(
                call.data[ATTR_SPEED_FACTOR],
                call.data[ATTR_START_TIME],
                call.data[ATTR_END_TIME],
            )
        except SC20Error as err:
            raise HomeAssistantError(f"could not start the preview: {err}") from err

    async def set_clock(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        # The device holds local wall-clock time, not UTC.
        now = dt_util.now().replace(tzinfo=None)
        try:
            await coordinator.client.async_set_clock(now)
        except SC20Error as err:
            raise HomeAssistantError(f"could not set the clock: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_SET_DAYCYCLE, set_daycycle, schema=SET_DAYCYCLE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DAYCYCLE,
        get_daycycle,
        schema=GET_DAYCYCLE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOAD_SCENARIO, load_scenario, schema=LOAD_SCENARIO_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SCENARIO, save_scenario, schema=SAVE_SCENARIO_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PREVIEW_CURVE, preview_curve, schema=PREVIEW_CURVE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SET_CLOCK, set_clock, schema=SET_CLOCK_SCHEMA)
