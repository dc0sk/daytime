"""Firmware update status for the daytime SC20.

The controller checks `data.daytime.de` for itself and reports the result in `USRDTA`:
`revision` is what it runs, `latestAvailableRevision` is what it found, and
`firmwareAvailable` is its own verdict. This surfaces that — Home Assistant makes no
request to the vendor.

Both halves are tracked because the device updates them independently: `revision[0]` is the
controller firmware (its web server), `revision[1]` is the web app in SPIFFS. Either can be
behind on its own.

Installing is supported, and it is genuinely risky. `START_FOTA` tells the controller to
download from data.daytime.de, flash itself and reboot; it is off the network for several
minutes, there is no cancel, and a failed write leaves the aquarium unlit until someone
reflashes by hand through `http://<host>/update`. The vendor's own updater warns not to use
or power-cycle the controller during it.

So the button is guarded: it refuses unless the device is connected and actually reports
something newer than what it runs. Nothing here will flash a device that is already current,
and nothing will flash on a hunch — the version comparison comes from the controller's own
upstream check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SC20ConfigEntry
from .api import DeviceInfo, SC20Error, SC20State
from .coordinator import SC20Coordinator
from .entity import SC20Entity

#: Where the vendor publishes the combined image, and what the device's own updater fetches.
RELEASE_URL = "http://data.daytime.de/update"

_LOGGER = logging.getLogger(__name__)


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

    #: PROGRESS is required for Home Assistant to surface `in_progress` at all — without
    #: it the busy flag is silently ignored and the entity looks idle mid-flash.
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_release_url = RELEASE_URL
    #: The controller decides what to fetch; a specific version cannot be requested.
    _attr_auto_update = False

    def __init__(
        self, coordinator: SC20Coordinator, description: SC20UpdateDescription
    ) -> None:
        super().__init__(coordinator, f"update_{description.key}")
        self.entity_description = description
        self._installing = False

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
            "Installing reboots the controller and takes several minutes, during which the "
            "aquarium light is not under control. Do not power-cycle it. There is no "
            "cancel, and a failed update needs a manual reflash at "
            f"http://{self.coordinator.client.host}/update."
        )

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Trigger the controller's own updater.

        `version` is ignored: the device fetches whatever the vendor currently publishes and
        offers no way to ask for a particular build. `backup` is ignored too — there is
        nowhere to put a firmware backup, and the device cannot produce one.
        """
        client = self.coordinator.client
        info = self._info

        if not client.connected or info is None:
            raise HomeAssistantError(
                f"Not connected to {client.host}, so an update cannot be started safely."
            )

        # Refuse to flash a device that is already current. Rewriting identical firmware
        # buys nothing and carries the full risk of a failed write.
        if self.installed_version == self.latest_version:
            raise HomeAssistantError(
                f"{client.host} already runs {self.installed_version}; there is nothing to "
                "install."
            )

        previous = info.revision
        _LOGGER.warning(
            "installing firmware on %s: %s -> %s. It will reboot and be unreachable for "
            "several minutes. Do not power-cycle it.",
            client.host,
            self.installed_version,
            self.latest_version,
        )

        self._installing = True
        self.async_write_ha_state()
        try:
            await client.async_start_firmware_update()
            updated = await client.async_wait_for_update(previous)
        except SC20Error as err:
            raise HomeAssistantError(
                f"Could not start the update on {client.host}: {err}. Check the controller "
                "directly before retrying."
            ) from err
        finally:
            self._installing = False
            self.async_write_ha_state()

        if not updated:
            # Deliberately not phrased as a failure: the device may still be flashing, or
            # may have come back on a revision we did not manage to re-read.
            raise HomeAssistantError(
                f"{client.host} did not report a new version in time. It may still be "
                f"updating — give it a few minutes, then check http://{client.host}/update. "
                "Do not power-cycle it."
            )

        await self.coordinator.async_request_refresh()

    @property
    def in_progress(self) -> bool:
        """True while an install this entity started is running.

        The controller reports no progress percentage — the vendor app just waits for it to
        come back — so this is a plain busy flag. `update_percentage` stays None, which
        renders as an indeterminate progress bar rather than an invented number.
        """
        return self._installing

    @property
    def update_percentage(self) -> int | None:
        return None
