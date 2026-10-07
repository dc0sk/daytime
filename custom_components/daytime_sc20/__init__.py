"""The daytime SC20 aquarium LED controller integration.

Copyright 2026 Simon Keimer

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file
except in compliance with the License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the
License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. See the License for the specific language governing permissions
and limitations under the License.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SC20Client, SC20ConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import SC20Coordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.UPDATE,
]

type SC20ConfigEntry = ConfigEntry[SC20Coordinator]

#: Entities this integration used to create and no longer does, as (entity domain, key).
#: Moonlight start and end were `number` entities holding a minute-of-day before they became
#: proper `time` entities. A unique id is scoped per entity domain, so the `time` versions
#: did not take over the old rows — they were simply abandoned.
_SUPERSEDED_ENTITIES: tuple[tuple[str, str], ...] = (
    (Platform.NUMBER, "moonlight_start"),
    (Platform.NUMBER, "moonlight_end"),
)


def _async_remove_superseded_entities(hass: HomeAssistant, entry: SC20ConfigEntry) -> None:
    """Delete registry rows for entities this integration stopped providing.

    Home Assistant keeps a registry row forever once it has seen an entity, and shows it as
    `unavailable` if nothing provides it any more. Nothing cleans that up on its own, so an
    install that predates a rename is left with permanently dead entities: on a device page
    they look like the feature is broken, and they are easy to put on a dashboard by
    mistake. Removing them here is safe — anything that is still provided gets its row back
    as the platforms set up moments later.
    """
    registry = er.async_get(hass)
    base = entry.unique_id or entry.entry_id
    for domain, key in _SUPERSEDED_ENTITIES:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{base}_{key}")
        if entity_id is not None:
            _LOGGER.debug("removing superseded entity %s", entity_id)
            registry.async_remove(entity_id)


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

    _async_remove_superseded_entities(hass, entry)
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
