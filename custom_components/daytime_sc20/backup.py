"""Snapshotting the lighting programme before it is overwritten.

The controller has no undo and does not acknowledge writes, so once a schedule is replaced
the old one is gone unless it was saved here first. Both the services and the options flow
go through this.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import SC20Client, protocol
from .const import BACKUP_STORAGE_KEY, BACKUP_STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

#: This is a safety net, not an archive.
MAX_BACKUPS_PER_HOST = 10


async def async_backup_daycycle(hass: HomeAssistant, client: SC20Client) -> bool:
    """Save the schedule that is about to be replaced. Returns whether anything was saved.

    Best-effort: failing to write a backup should not stop someone changing their own
    lighting, but it is logged loudly enough to notice.
    """
    current = client.state.daycycle
    if current is None:
        _LOGGER.warning(
            "no daycycle has been read from %s yet, so nothing could be backed up",
            client.host,
        )
        return False

    store: Store[dict[str, Any]] = Store(hass, BACKUP_STORAGE_VERSION, BACKUP_STORAGE_KEY)
    try:
        saved = await store.async_load() or {}
        history = saved.setdefault(client.host, [])
        history.append(
            {
                "saved_at": dt_util.utcnow().isoformat(),
                "setpoints": [[p.minute, *p.values] for p in current.setpoints],
                "description": (
                    protocol.encode_description(client.state.description)
                    if client.state.description
                    else None
                ),
            }
        )
        saved[client.host] = history[-MAX_BACKUPS_PER_HOST:]
        await store.async_save(saved)
    except OSError as err:
        _LOGGER.error("could not back up the daycycle before overwriting it: %s", err)
        return False

    _LOGGER.info("backed up the current daycycle of %s before overwriting it", client.host)
    return True
