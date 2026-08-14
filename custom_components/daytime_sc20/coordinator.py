"""Update coordinator for the daytime SC20.

The device is a mix of push and poll:

* It dumps its whole configuration unprompted on connect, and broadcasts any change made by
  another client (the phone app, say) to us as well. Those arrive via the client's callback
  and are pushed straight to entities.
* The live channel values are not pushed. They have to be polled, and they move on their own
  whenever cloud simulation is running, so the poll interval decides how closely Home
  Assistant tracks the tank.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SC20Client, SC20ConnectionError, SC20Error, SC20State
from .const import DOMAIN, FULL_REFRESH_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SC20Coordinator(DataUpdateCoordinator[SC20State]):
    """Keeps one controller's state fresh."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SC20Client,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._polls_since_full_refresh = 0
        self._polls_per_full_refresh = max(1, FULL_REFRESH_INTERVAL // max(scan_interval, 1))
        client.set_update_callback(self._handle_pushed_state)

    def _handle_pushed_state(self, state: SC20State) -> None:
        """Publish state the device sent us without being asked.

        Called from the client's reader task, so it must not block or await.
        """
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> SC20State:
        """Poll the live values, and everything else once in a while."""
        if not self.client.connected:
            # The client reconnects on its own; report the failure so entities go
            # unavailable, and let the next poll find it back up.
            raise UpdateFailed(f"not connected to {self.client.host}")

        try:
            await self.client.async_get_values()

            self._polls_since_full_refresh += 1
            if self._polls_since_full_refresh >= self._polls_per_full_refresh:
                self._polls_since_full_refresh = 0
                await self.client.async_refresh()
        except SC20ConnectionError as err:
            raise UpdateFailed(f"connection to {self.client.host} lost: {err}") from err
        except SC20Error as err:
            raise UpdateFailed(f"could not read from {self.client.host}: {err}") from err

        return self.client.state

    async def async_write_then_refresh(self, action) -> None:
        """Run a write and republish state.

        Writes are never acknowledged by the device, so the client re-reads what it wrote.
        This wrapper exists so entities do not each have to remember to publish afterwards.
        """
        await action()
        self.async_set_updated_data(self.client.state)
