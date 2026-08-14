"""Shared entity base for the daytime SC20."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import SC20Coordinator


class SC20Entity(CoordinatorEntity[SC20Coordinator]):
    """Common wiring: device registry entry, availability, naming."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SC20Coordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"

        info = coordinator.client.state.device_info
        self._attr_device_info = HADeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=entry.title,
            sw_version=info.firmware_version if info else None,
            configuration_url=f"http://{coordinator.client.host}/",
        )

    @property
    def available(self) -> bool:
        """Entities go unavailable when the socket drops, not just when a poll fails.

        The client reconnects on its own, so this recovers without user action.
        """
        return super().available and self.coordinator.client.connected
