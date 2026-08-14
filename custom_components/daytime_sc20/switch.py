"""Effect switches for the daytime SC20: moonlight, clouds and acclimatisation.

Each effect is a whole settings record on the wire — there is no "just toggle it" command,
so flipping a switch resends the record with one field changed. That means the switch is
only safe to touch once the current settings have been read, which the coordinator
guarantees before entities are added.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import SC20Client, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity


@dataclass(frozen=True, kw_only=True)
class SC20SwitchDescription(SwitchEntityDescription):
    """Ties a switch to the record it lives in."""

    is_on: Callable[[SC20State], bool | None]
    set_state: Callable[[SC20Client, SC20State, bool], Coroutine[Any, Any, None]]


async def _set_moon(client: SC20Client, state: SC20State, value: bool) -> None:
    if state.moon is None:
        return
    await client.async_set_moon(dataclasses.replace(state.moon, active=value))


async def _set_cloud(client: SC20Client, state: SC20State, value: bool) -> None:
    if state.cloud is None:
        return
    await client.async_set_cloud(dataclasses.replace(state.cloud, active=value))


async def _set_acclimate(client: SC20Client, state: SC20State, value: bool) -> None:
    if state.acclimate is None:
        return
    await client.async_set_acclimate(dataclasses.replace(state.acclimate, active=value))


async def _set_acclimate_pause(client: SC20Client, state: SC20State, value: bool) -> None:
    """Pause has its own command as well as a field in the record.

    The dedicated command is what the vendor app sends, so send that; the record is then
    updated so the entity reflects it without waiting for a refresh.
    """
    await client.async_pause_acclimation(value)
    if state.acclimate is not None:
        client.state.acclimate = dataclasses.replace(state.acclimate, paused=value)


SWITCHES: tuple[SC20SwitchDescription, ...] = (
    SC20SwitchDescription(
        key="moonlight",
        translation_key="moonlight",
        icon="mdi:weather-night",
        is_on=lambda state: state.moon.active if state.moon else None,
        set_state=_set_moon,
    ),
    SC20SwitchDescription(
        key="cloud_simulation",
        translation_key="cloud_simulation",
        icon="mdi:weather-partly-cloudy",
        is_on=lambda state: state.cloud.active if state.cloud else None,
        set_state=_set_cloud,
    ),
    SC20SwitchDescription(
        key="acclimatisation",
        translation_key="acclimatisation",
        icon="mdi:chart-timeline-variant",
        is_on=lambda state: state.acclimate.active if state.acclimate else None,
        set_state=_set_acclimate,
    ),
    SC20SwitchDescription(
        key="acclimatisation_pause",
        translation_key="acclimatisation_pause",
        icon="mdi:pause-circle-outline",
        entity_registry_enabled_default=False,
        is_on=lambda state: state.acclimate.paused if state.acclimate else None,
        set_state=_set_acclimate_pause,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SC20Switch(coordinator, description) for description in SWITCHES)


class SC20Switch(SC20Entity, SwitchEntity):
    """One effect on or off."""

    entity_description: SC20SwitchDescription

    def __init__(
        self, coordinator: SC20Coordinator, description: SC20SwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.coordinator.client.state)

    @property
    def available(self) -> bool:
        """Unavailable until the record this switch lives in has been read.

        Toggling before then would send a record built from defaults and quietly overwrite
        the user's settings.
        """
        return super().available and self.is_on is not None

    async def _async_set(self, value: bool) -> None:
        client = self.coordinator.client
        await self.coordinator.async_write_then_refresh(
            lambda: self.entity_description.set_state(client, client.state, value)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
