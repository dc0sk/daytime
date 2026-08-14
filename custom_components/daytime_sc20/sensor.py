"""Diagnostic sensors for the daytime SC20.

Uptime, free heap and operating hours are not in the WebSocket protocol at all — they come
from scraping the device's `/serverLog` page, which the client refreshes on each full
update.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import CHANNEL_NAMES, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity


@dataclass(frozen=True, kw_only=True)
class SC20SensorDescription(SensorEntityDescription):
    """A sensor and how to pull its value out of the state."""

    value: Callable[[SC20State], Any]
    attributes: Callable[[SC20State], dict[str, Any]] | None = None


def _mode(state: SC20State) -> str:
    return "manual" if state.is_manual else "daycycle"


def _programmed_level(state: SC20State) -> int | None:
    """What the schedule calls for right now.

    Deliberately distinct from the live values sensor: with clouds running, the two differ,
    and the gap between them is the effect actually doing something. Interpolation is
    linear, which matches the vendor app's graph but was never confirmed against the
    firmware — treat this as an estimate.
    """
    if state.daycycle is None or state.clock is None:
        return None
    minute = state.clock.timestamp.hour * 60 + state.clock.timestamp.minute
    return state.daycycle.level_at(minute).brightest


SENSORS: tuple[SC20SensorDescription, ...] = (
    SC20SensorDescription(
        key="mode",
        translation_key="mode_sensor",
        icon="mdi:theme-light-dark",
        value=_mode,
    ),
    SC20SensorDescription(
        key="output_level",
        translation_key="output_level",
        icon="mdi:brightness-percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.values.brightest if s.values else None,
        attributes=lambda s: (
            {
                name.lower(): value
                for name, value in zip(CHANNEL_NAMES, s.values.values, strict=True)
            }
            if s.values
            else {}
        ),
    ),
    SC20SensorDescription(
        key="programmed_level",
        translation_key="programmed_level",
        icon="mdi:chart-bell-curve-cumulative",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value=_programmed_level,
    ),
    SC20SensorDescription(
        key="acclimatisation_day",
        translation_key="acclimatisation_day",
        icon="mdi:calendar-today",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.acclimate.current_day if s.acclimate else None,
    ),
    SC20SensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.device_info.firmware_version if s.device_info else None,
        attributes=lambda s: (
            {
                "webapp_version": s.device_info.webapp_version,
                "update_available": s.device_info.firmware_available,
            }
            if s.device_info
            else {}
        ),
    ),
    SC20SensorDescription(
        key="free_heap",
        translation_key="free_heap",
        icon="mdi:memory",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.server_log.free_heap if s.server_log else None,
    ),
    SC20SensorDescription(
        key="uptime",
        translation_key="uptime",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.server_log.uptime_minutes if s.server_log else None,
    ),
    SC20SensorDescription(
        key="operating_hours",
        translation_key="operating_hours",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s.server_log.operating_hours if s.server_log else None,
    ),
    SC20SensorDescription(
        key="mesh_clients",
        translation_key="mesh_clients",
        icon="mdi:lan",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: len(s.mesh.clients) if s.mesh else None,
        attributes=lambda s: {"clients": list(s.mesh.clients)} if s.mesh else {},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SC20Sensor(coordinator, description) for description in SENSORS)


class SC20Sensor(SC20Entity, SensorEntity):
    """One read-only value."""

    entity_description: SC20SensorDescription

    def __init__(
        self, coordinator: SC20Coordinator, description: SC20SensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.client.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator.client.state)
