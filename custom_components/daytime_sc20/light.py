"""Light entities for the daytime SC20.

The controller drives three independent white / blue / red channels. That is not an RGB
colour space — the white channel is a separate emitter, not a mix — so each channel is its
own dimmable light, plus a master that scales all three together while preserving their
ratio.

Two things about this device shape the behaviour here:

* Setting a level only takes effect in manual mode. Turning a light on therefore switches
  the controller out of its scheduled programme, which is a real, visible side effect. The
  mode select entity is how you go back, and `mode` is exposed as an attribute on every
  light so the state is never ambiguous.
* In scheduled mode the reported level is the *actual* output, including cloud and
  moonlight modulation, so it drifts on its own. That is the truth about the tank, not a
  bug — but it means these entities report a brightness the user did not set.
"""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import CHANNEL_NAMES, MAX_PERCENT, ChannelValues
from .coordinator import SC20Coordinator
from .entity import SC20Entity

#: Home Assistant brightness is 0-255; the device speaks percent.
_HA_MAX = 255


def _to_percent(brightness: int) -> int:
    return max(0, min(MAX_PERCENT, round(brightness * MAX_PERCENT / _HA_MAX)))


def _to_ha(percent: int) -> int:
    return max(0, min(_HA_MAX, round(percent * _HA_MAX / MAX_PERCENT)))


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[LightEntity] = [SC20MasterLight(coordinator)]
    entities.extend(SC20ChannelLight(coordinator, index) for index in range(len(CHANNEL_NAMES)))
    async_add_entities(entities)


class SC20LightBase(SC20Entity, LightEntity):
    """Shared behaviour: percent-to-brightness, mode attribute, manual-mode writes."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar = {ColorMode.BRIGHTNESS}

    @property
    def _values(self) -> ChannelValues | None:
        return self.coordinator.client.state.values

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface the mode, because turning a light on silently leaves the schedule."""
        state = self.coordinator.client.state
        return {
            "mode": "manual" if state.is_manual else "daycycle",
            "following_schedule": not state.is_manual,
        }

    async def _async_write(self, values: ChannelValues) -> None:
        """Apply new channel levels, entering manual mode if needed."""
        await self.coordinator.async_write_then_refresh(
            lambda: self.coordinator.client.async_set_values(values)
        )


class SC20MasterLight(SC20LightBase):
    """All three channels at once, keeping their relative levels.

    Brightness tracks the brightest channel: at white 100 / blue 50 / red 0, master
    brightness is 100 %, and halving it gives 50 / 25 / 0.
    """

    _attr_name = None  # takes the device name
    _attr_icon = "mdi:fishbowl"

    def __init__(self, coordinator: SC20Coordinator) -> None:
        super().__init__(coordinator, "master")

    @property
    def is_on(self) -> bool:
        values = self._values
        return values is not None and not values.is_off

    @property
    def brightness(self) -> int | None:
        values = self._values
        return None if values is None else _to_ha(values.brightest)

    async def async_turn_on(self, **kwargs: Any) -> None:
        values = self._values or ChannelValues.uniform(0)
        target = _to_percent(kwargs[ATTR_BRIGHTNESS]) if ATTR_BRIGHTNESS in kwargs else None

        if target is None:
            # No brightness asked for: restore full output if currently dark, otherwise
            # leave the mix alone and just make sure we are on.
            target = MAX_PERCENT if values.is_off else values.brightest

        await self._async_write(values.scaled_to(target))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Take every channel to zero.

        This leaves the controller in manual mode: the lamp stays dark rather than
        resuming the programme at the next setpoint. Use the mode select to hand control
        back to the schedule.
        """
        await self._async_write(ChannelValues.uniform(0))


class SC20ChannelLight(SC20LightBase):
    """One colour channel on its own."""

    def __init__(self, coordinator: SC20Coordinator, index: int) -> None:
        super().__init__(coordinator, f"channel_{index}")
        self._index = index
        self._attr_name = CHANNEL_NAMES[index]
        self._attr_translation_key = f"channel_{CHANNEL_NAMES[index].lower()}"

    @property
    def _level(self) -> int | None:
        values = self._values
        return None if values is None else values.values[self._index]

    @property
    def is_on(self) -> bool:
        level = self._level
        return level is not None and level > 0

    @property
    def brightness(self) -> int | None:
        level = self._level
        return None if level is None else _to_ha(level)

    async def _async_set_level(self, percent: int) -> None:
        values = self._values or ChannelValues.uniform(0)
        updated = list(values.values)
        updated[self._index] = percent
        await self._async_write(ChannelValues(tuple(updated)))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            await self._async_set_level(_to_percent(kwargs[ATTR_BRIGHTNESS]))
            return
        # No brightness given: full output, unless it is already on.
        current = self._level or 0
        await self._async_set_level(current if current > 0 else MAX_PERCENT)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_level(0)
