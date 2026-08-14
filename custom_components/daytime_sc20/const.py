"""Constants for the daytime SC20 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "daytime_sc20"

MANUFACTURER: Final = "daytime / aquaLEDs.de"
MODEL: Final = "Smart Control SC20"

CONF_SCAN_INTERVAL: Final = "scan_interval"

#: How often to re-read the live channel values. The vendor web app polls every 2.5 s to
#: animate its sliders; Home Assistant does not need that, and the device is an ESP8266
#: with very little heap, so the default is much gentler.
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

#: How often to re-read everything else. Configuration changes rarely, and any change made
#: from the vendor app arrives on the broadcast anyway.
FULL_REFRESH_INTERVAL: Final = 300

# Services
SERVICE_SET_DAYCYCLE: Final = "set_daycycle"
SERVICE_GET_DAYCYCLE: Final = "get_daycycle"
SERVICE_LOAD_SCENARIO: Final = "load_scenario"
SERVICE_SAVE_SCENARIO: Final = "save_scenario"
SERVICE_PREVIEW_CURVE: Final = "preview_curve"
SERVICE_SET_CLOCK: Final = "set_clock"

ATTR_SETPOINTS: Final = "setpoints"
ATTR_FILENAME: Final = "filename"
ATTR_SPEED_FACTOR: Final = "speed_factor"
ATTR_START_TIME: Final = "start_time"
ATTR_END_TIME: Final = "end_time"

#: Highest minute-of-day a time-of-day setting accepts. The schedule itself is anchored at
#: 1440, but the moonlight window is a clock time, so it stops one minute short.
DAY_MINUTES_MAX: Final = 1439

#: Where `set_daycycle` and `load_scenario` stash the schedule they are about to replace.
#: The device has no undo and does not acknowledge writes.
BACKUP_STORAGE_KEY: Final = f"{DOMAIN}_daycycle_backup"
BACKUP_STORAGE_VERSION: Final = 1
