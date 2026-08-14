"""Effect parameters for the daytime SC20, as number entities.

Every value here belongs to a settings record that has to be resent whole, so each setter
replaces one field of the record the coordinator last read. Ranges follow the vendor app's
own sliders, which is the closest thing to a documented limit this device has.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import SC20Client, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity


@dataclass(frozen=True, kw_only=True)
class SC20NumberDescription(NumberEntityDescription):
    """Ties a number to the record field it edits."""

    value: Callable[[SC20State], float | None]
    set_value: Callable[[SC20Client, SC20State, int], Coroutine[Any, Any, None]]


_Setter = Callable[[SC20Client, SC20State, int], Coroutine[Any, Any, None]]


def _moon_setter(field: str) -> _Setter:
    async def setter(client: SC20Client, state: SC20State, value: int) -> None:
        if state.moon is not None:
            await client.async_set_moon(dataclasses.replace(state.moon, **{field: value}))

    return setter


def _cloud_setter(field: str) -> _Setter:
    async def setter(client: SC20Client, state: SC20State, value: int) -> None:
        if state.cloud is not None:
            await client.async_set_cloud(dataclasses.replace(state.cloud, **{field: value}))

    return setter


def _acclimate_setter(field: str) -> _Setter:
    async def setter(client: SC20Client, state: SC20State, value: int) -> None:
        if state.acclimate is not None:
            await client.async_set_acclimate(
                dataclasses.replace(state.acclimate, **{field: value})
            )

    return setter


NUMBERS: tuple[SC20NumberDescription, ...] = (
    # --- moonlight ---
    SC20NumberDescription(
        key="moonlight_min",
        translation_key="moonlight_min",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.moon.minimum if s.moon else None,
        set_value=_moon_setter("minimum"),
    ),
    SC20NumberDescription(
        key="moonlight_max",
        translation_key="moonlight_max",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.moon.maximum if s.moon else None,
        set_value=_moon_setter("maximum"),
    ),
    SC20NumberDescription(
        key="cloud_max_per_day",
        translation_key="cloud_max_per_day",
        native_min_value=0,
        native_max_value=1500,
        native_step=10,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.max_per_day if s.cloud else None,
        set_value=_cloud_setter("max_per_day"),
    ),
    SC20NumberDescription(
        key="cloud_probability",
        translation_key="cloud_probability",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.probability if s.cloud else None,
        set_value=_cloud_setter("probability"),
    ),
    SC20NumberDescription(
        key="cloud_min_intensity",
        translation_key="cloud_min_intensity",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.min_intensity if s.cloud else None,
        set_value=_cloud_setter("min_intensity"),
    ),
    SC20NumberDescription(
        key="cloud_max_intensity",
        translation_key="cloud_max_intensity",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.max_intensity if s.cloud else None,
        set_value=_cloud_setter("max_intensity"),
    ),
    SC20NumberDescription(
        key="cloud_min_duration",
        translation_key="cloud_min_duration",
        native_min_value=0,
        native_max_value=30,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.min_duration_minutes if s.cloud else None,
        set_value=_cloud_setter("min_duration_minutes"),
    ),
    SC20NumberDescription(
        key="cloud_max_duration",
        translation_key="cloud_max_duration",
        native_min_value=0,
        native_max_value=30,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.cloud.max_duration_minutes if s.cloud else None,
        set_value=_cloud_setter("max_duration_minutes"),
    ),
    # --- acclimatisation ---
    SC20NumberDescription(
        key="acclimatisation_duration",
        translation_key="acclimatisation_duration",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.acclimate.duration_days if s.acclimate else None,
        set_value=_acclimate_setter("duration_days"),
    ),
    SC20NumberDescription(
        key="acclimatisation_reduction",
        translation_key="acclimatisation_reduction",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.acclimate.intensity_reduction if s.acclimate else None,
        set_value=_acclimate_setter("intensity_reduction"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SC20Number(coordinator, description) for description in NUMBERS)


class SC20Number(SC20Entity, NumberEntity):
    """One field of one effect record."""

    entity_description: SC20NumberDescription
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: SC20Coordinator, description: SC20NumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.coordinator.client.state)

    @property
    def available(self) -> bool:
        """Unavailable until the record has been read.

        Writing before then would resend a record built from defaults, silently discarding
        whatever the user had configured in the vendor app.
        """
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        client = self.coordinator.client
        await self.coordinator.async_write_then_refresh(
            lambda: self.entity_description.set_value(client, client.state, int(value))
        )
