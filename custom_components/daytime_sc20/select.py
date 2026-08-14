"""Mode select for the daytime SC20.

The controller is always in exactly one of two modes: following its stored daycycle, or
under manual control. This entity is the explicit way to move between them — the lights
only ever switch *into* manual, as a side effect of being told to change.
"""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import MODE_DAYCYCLE, MODE_MANUAL
from .coordinator import SC20Coordinator
from .entity import SC20Entity

OPTION_DAYCYCLE = "daycycle"
OPTION_MANUAL = "manual"

_TO_OPTION = {MODE_DAYCYCLE: OPTION_DAYCYCLE, MODE_MANUAL: OPTION_MANUAL}


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SC20ModeSelect(entry.runtime_data)])


class SC20ModeSelect(SC20Entity, SelectEntity):
    """Switch between the stored programme and manual control."""

    _attr_translation_key = "mode"
    _attr_icon = "mdi:theme-light-dark"
    _attr_options: ClassVar = [OPTION_DAYCYCLE, OPTION_MANUAL]

    def __init__(self, coordinator: SC20Coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str:
        return _TO_OPTION.get(self.coordinator.client.state.mode, OPTION_DAYCYCLE)

    async def async_select_option(self, option: str) -> None:
        """Change mode.

        Selecting `daycycle` hands control back to the stored programme; the lamp jumps to
        whatever the schedule calls for right now. Selecting `manual` freezes the current
        levels until something sets new ones.
        """
        await self.coordinator.async_write_then_refresh(
            lambda: self.coordinator.client.async_set_mode(manual=option == OPTION_MANUAL)
        )
