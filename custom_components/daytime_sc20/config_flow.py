"""Config flow for the daytime SC20.

The device has no authentication of any kind, so all that is needed is an address. The
flow still connects before accepting it, because a reachable HTTP server is not proof that
the WebSocket works.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SC20Client, SC20ConnectionError, SC20Error, async_check_connection
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


async def _async_probe(hass: Any, host: str) -> tuple[str, str]:
    """Connect and read enough to identify the device.

    Returns the unique id (the master's BSSID) and a display name. Raises
    `SC20ConnectionError` if the device is not reachable or is not an SC20.
    """
    session = async_get_clientsession(hass)

    if not await async_check_connection(host, session):
        raise SC20ConnectionError(f"no SC20 web server answered at {host}")

    client = SC20Client(host, session)
    try:
        await client.connect()
        state = await client.async_refresh()
    except SC20Error as err:
        raise SC20ConnectionError(str(err)) from err
    finally:
        await client.disconnect()

    if state.device_info is None:
        raise SC20ConnectionError(f"{host} did not identify itself as an SC20")

    unique_id = state.mesh.master if state.mesh and state.mesh.master else host
    name = state.device_info.aquarium_name or state.device_info.name or "daytime SC20"
    return unique_id, name


class SC20ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a controller."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                unique_id, name = await _async_probe(self.hass, host)
            except SC20ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(title=name, data={CONF_HOST: host})

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> SC20OptionsFlow:
        return SC20OptionsFlow()


class SC20OptionsFlow(OptionsFlow):
    """Let the user trade responsiveness against load on the controller."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
