"""The daytime SC20 aquarium LED controller integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SC20Client, SC20ConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import SC20Coordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SC20ConfigEntry = ConfigEntry[SC20Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SC20ConfigEntry) -> bool:
    """Set up one controller."""
    host = entry.data[CONF_HOST]
    session = async_get_clientsession(hass)
    client = SC20Client(host, session)

    try:
        await client.connect()
    except SC20ConnectionError as err:
        raise ConfigEntryNotReady(f"cannot reach the SC20 at {host}: {err}") from err

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = SC20Coordinator(hass, entry, client, scan_interval)

    try:
        # The device dumps its whole state on connect, but do not rely on that having
        # landed already — ask for it, so setup fails loudly if the device is unresponsive.
        await client.async_refresh()
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.disconnect()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SC20ConfigEntry) -> bool:
    """Tear one controller down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.disconnect()
    return unloaded


async def _async_reload_on_options_change(hass: HomeAssistant, entry: SC20ConfigEntry) -> None:
    """The poll interval is baked into the coordinator, so a change needs a reload."""
    await hass.config_entries.async_reload(entry.entry_id)
