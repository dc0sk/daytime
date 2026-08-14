"""Firmware update status for the daytime SC20.

The controller checks `data.daytime.de` for itself and reports the result in `USRDTA`:
`revision` is what it runs, `latestAvailableRevision` is what it found, and
`firmwareAvailable` is its own verdict. This surfaces that — Home Assistant makes no
request to the vendor.

Both halves are tracked because the device updates them independently: `revision[0]` is the
controller firmware (its web server), `revision[1]` is the web app in SPIFFS. Either can be
behind on its own.

**Reporting only — this cannot install anything.** The firmware does expose an over-the-air
command, but flashing is not a safe thing to trigger from an automation: the vendor's own
updater warns not to use or power-cycle the controller mid-flash, and a failed write leaves
an aquarium without light until someone reflashes it by hand. Updating is done deliberately,
through the controller's own web interface at `http://<host>/update`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import DeviceInfo, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity

#: Where the vendor publishes the combined image, and what the device's own updater fetches.
RELEASE_URL = "http://data.daytime.de/update"


@dataclass(frozen=True, kw_only=True)
class SC20UpdateDescription(UpdateEntityDescription):
    """One updatable component of the controller."""

    #: Index into the `revision` / `latestAvailableRevision` pairs.
    index: int


UPDATES: tuple[SC20UpdateDescription, ...] = (
    SC20UpdateDescription(
        key="firmware",
        translation_key="firmware",
        index=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SC20UpdateDescription(
        key="webapp",
        translation_key="webapp",
        index=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SC20ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SC20Update(coordinator, description) for description in UPDATES)


class SC20Update(SC20Entity, UpdateEntity):
    """Reports whether one component of the controller is behind."""

    entity_description: SC20UpdateDescription

    #: No INSTALL: see the module docstring. An update entity without it still shows the
    #: versions and raises the "update available" state, which is what was wanted.
    _attr_supported_features = UpdateEntityFeature(0)
    _attr_release_url = RELEASE_URL

    def __init__(
        self, coordinator: SC20Coordinator, description: SC20UpdateDescription
    ) -> None:
        super().__init__(coordinator, f"update_{description.key}")
        self.entity_description = description

    @property
    def _state(self) -> SC20State:
        return self.coordinator.client.state

    @property
    def _info(self) -> DeviceInfo | None:
        return self._state.device_info

    def _revision(self, pair_name: str) -> str | None:
        info = self._info
        if info is None:
            return None
        pair = getattr(info, pair_name)
        index = self.entity_description.index
        if index >= len(pair):
            return None
        return DeviceInfo.format_revision(pair[index])

    @property
    def installed_version(self) -> str | None:
        return self._revision("revision")

    @property
    def latest_version(self) -> str | None:
        """What the controller found upstream.

        If it has not managed to check — no internet, or the vendor's server down — the
        pair reads all zeroes. Reporting "00.0" would show a permanent phantom downgrade,
        so fall back to the installed version, which renders as "up to date".
        """
        latest = self._revision("latest_revision")
        if latest is None or latest == "00.0":
            return self.installed_version
        return latest

    @property
    def available(self) -> bool:
        return super().available and self._info is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info
        if info is None:
            return {}
        return {
            # The controller's own verdict, kept alongside our version comparison so a
            # disagreement between the two is visible rather than hidden.
            "device_reports_update_available": info.firmware_available,
            "update_via": f"http://{self.coordinator.client.host}/update",
        }

    @property
    def release_summary(self) -> str | None:
        if self.installed_version == self.latest_version:
            return None
        return (
            "Update the controller through its own web interface at "
            f"http://{self.coordinator.client.host}/update — this integration reports "
            "versions but does not flash firmware. Do not power-cycle the controller "
            "during an update."
        )
