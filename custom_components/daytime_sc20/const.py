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

# --- Options-flow form keys ---------------------------------------------------------------
#
# Shared across the configuration pages, which mirror the vendor web app's own screens.

CONF_ACTIVE: Final = "active"
CONF_START: Final = "start"
CONF_END: Final = "end"
CONF_SUNRISE: Final = "sunrise"
CONF_SUNSET: Final = "sunset"
CONF_BRIGHTNESS: Final = "brightness"
CONF_INDIVIDUAL: Final = "individual"
CONF_CHANNEL_LEVELS: Final = ("level_white", "level_blue", "level_red")

CONF_LUNAR_CYCLE: Final = "lunar_cycle"
CONF_COLOR: Final = "color"
CONF_MIN_LEVEL: Final = "min_level"
CONF_MAX_LEVEL: Final = "max_level"

CONF_MAX_PER_DAY: Final = "max_per_day"
CONF_PROBABILITY: Final = "probability"
CONF_MIN_DURATION: Final = "min_duration"
CONF_MAX_DURATION: Final = "max_duration"

CONF_PAUSED: Final = "paused"
CONF_DURATION_DAYS: Final = "duration_days"
CONF_REDUCTION: Final = "reduction"

#: The letters the device accepts in the moonlight colour string, in the order the vendor
#: app builds them.
MOON_COLORS: Final = ("r", "b", "w")

# Form limits, taken from the vendor app's own sliders — the nearest thing to a documented
# range this device has.
MAX_CLOUDS_PER_DAY: Final = 1500
MAX_CLOUD_DURATION: Final = 30
MAX_ACCLIMATE_DAYS: Final = 100
