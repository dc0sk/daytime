"""Time-of-day settings for the daytime SC20.

The moonlight window is a pair of clock times, so it belongs here rather than as a count of
minutes. The device stores them as minutes since midnight; that conversion is the only
thing this module does beyond the usual read/write.

Note the window may legitimately run backwards — this hardware is normally configured with
a start of 22:00 and an end of 06:00. Nothing here tries to "correct" that.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import SC20Client, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity


def _to_time(minute: int) -> time:
    return time(hour=minute // 60, minute=minute % 60)


def _to_minute(value: time) -> int:
    """Seconds are dropped: the device only stores whole minutes."""
    return value.hour * 60 + value.minute


@dataclass(frozen=True, kw_only=True)
class SC20TimeDescription(TimeEntityDescription):
    """Ties a time entity to the record field it edits."""

    value: Callable[[SC20State], int | None]
    set_value: Callable[[SC20Client, SC20State, int], Coroutine[Any, Any, None]]


_Setter = Callable[[SC20Client, SC20State, int], Coroutine[Any, Any, None]]


def _moon_setter(field: str) -> _Setter:
    async def setter(client: SC20Client, state: SC20State, minute: int) -> None:
        if state.moon is not None:
            await client.async_set_moon(dataclasses.replace(state.moon, **{field: minute}))

    return setter


TIMES: tuple[SC20TimeDescription, ...] = (
    SC20TimeDescription(
        key="moonlight_start",
        translation_key="moonlight_start",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.moon.start if s.moon else None,
        set_value=_moon_setter("start"),
    ),
    SC20TimeDescription(
        key="moonlight_end",
        translation_key="moonlight_end",
        icon="mdi:weather-sunset-up",
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.moon.end if s.moon else None,
        set_value=_moon_setter("end"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SC20Time(coordinator, description) for description in TIMES)


class SC20Time(SC20Entity, TimeEntity):
    """One time-of-day field of one settings record."""

    entity_description: SC20TimeDescription

    def __init__(self, coordinator: SC20Coordinator, description: SC20TimeDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _minute(self) -> int | None:
        return self.entity_description.value(self.coordinator.client.state)

    @property
    def native_value(self) -> time | None:
        minute = self._minute
        return None if minute is None else _to_time(minute)

    @property
    def available(self) -> bool:
        """Unavailable until the record has been read.

        Writing before then would resend a record built from defaults, silently discarding
        whatever the user had configured in the vendor app.
        """
        return super().available and self._minute is not None

    async def async_set_value(self, value: time) -> None:
        client = self.coordinator.client
        minute = _to_minute(value)
        await self.coordinator.async_write_then_refresh(
            lambda: self.entity_description.set_value(client, client.state, minute)
        )
