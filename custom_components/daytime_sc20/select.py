"""Selects for the daytime SC20: operating mode and moonlight colour.

The mode select is the explicit way between the stored daycycle and manual control — the
lights only ever switch *into* manual, as a side effect of being told to change.

Moonlight colour is a select rather than three switches on purpose. On the wire it is a
string of channel letters, and the device emits nothing at all if it is empty; the vendor
app and this integration's options flow both guard against that. Three independent
switches would let someone turn the last one off and leave moonlight silently dark, so the
choice is modelled as the seven non-empty combinations instead. There is then no invalid
state to defend against.
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar, Final

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import MODE_DAYCYCLE, MODE_MANUAL
from .const import MOON_COLORS
from .coordinator import SC20Coordinator
from .entity import SC20Entity

OPTION_DAYCYCLE = "daycycle"
OPTION_MANUAL = "manual"

_TO_OPTION = {MODE_DAYCYCLE: OPTION_DAYCYCLE, MODE_MANUAL: OPTION_MANUAL}

#: Every non-empty combination of the three channels, each in MOON_COLORS order so that one
#: colour set has exactly one spelling.
MOON_COLOR_OPTIONS: Final = ("r", "b", "w", "rb", "rw", "bw", "rbw")


def _canonical_color(raw: str | None) -> str | None:
    """Normalise a device colour string to one of MOON_COLOR_OPTIONS.

    The device is not fussy about order or case, so "wb" and "BW" are the same set as "bw".
    Returning None for an empty or unrecognised string keeps the entity `unknown` rather
    than inventing a selection, and stops Home Assistant warning about an option that is
    not in the list.
    """
    letters = "".join(c for c in MOON_COLORS if c in (raw or "").lower())
    return letters or None


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([SC20ModeSelect(coordinator), SC20MoonColorSelect(coordinator)])


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


class SC20MoonColorSelect(SC20Entity, SelectEntity):
    """Which channels carry the moonlight."""

    _attr_translation_key = "moonlight_color"
    _attr_icon = "mdi:palette"
    _attr_options: ClassVar = list(MOON_COLOR_OPTIONS)

    def __init__(self, coordinator: SC20Coordinator) -> None:
        super().__init__(coordinator, "moonlight_color")

    @property
    def available(self) -> bool:
        """Unavailable until the moonlight record has been read.

        Selecting before then would send a record built from defaults and quietly overwrite
        the stored settings — the same reasoning as the effect switches.
        """
        return super().available and self.coordinator.client.state.moon is not None

    @property
    def current_option(self) -> str | None:
        moon = self.coordinator.client.state.moon
        return _canonical_color(moon.color) if moon else None

    async def async_select_option(self, option: str) -> None:
        """Rewrite the moonlight record with a new colour set, leaving everything else."""
        moon = self.coordinator.client.state.moon
        if moon is None:
            return
        client = self.coordinator.client
        await self.coordinator.async_write_then_refresh(
            lambda: client.async_set_moon(dataclasses.replace(moon, color=option))
        )
