"""Standalone client for the daytime SC20 aquarium LED controller.

Free of Home Assistant imports on purpose: the protocol was reverse engineered (see
docs/protocol/) and is worth being able to test and reuse on its own.
"""

from __future__ import annotations

from .client import SC20Client, SC20State, async_check_connection
from .const import (
    CHANNEL_COLORS,
    CHANNEL_COUNT,
    CHANNEL_NAMES,
    DAY_MINUTES,
    MAX_PERCENT,
    MAX_SETPOINTS,
    MIN_PERCENT,
    MODE_DAYCYCLE,
    MODE_MANUAL,
)
from .exceptions import (
    SC20ConnectionError,
    SC20Error,
    SC20ProtocolError,
    SC20Timeout,
    SC20ValidationError,
)
from .models import (
    Acclimate,
    ChannelValues,
    Clock,
    Cloud,
    Daycycle,
    DaycycleDescription,
    DeviceInfo,
    MeshNetwork,
    Moon,
    ServerLog,
    Setpoint,
)

__all__ = [
    "CHANNEL_COLORS",
    "CHANNEL_COUNT",
    "CHANNEL_NAMES",
    "DAY_MINUTES",
    "MAX_PERCENT",
    "MAX_SETPOINTS",
    "MIN_PERCENT",
    "MODE_DAYCYCLE",
    "MODE_MANUAL",
    "Acclimate",
    "ChannelValues",
    "Clock",
    "Cloud",
    "Daycycle",
    "DaycycleDescription",
    "DeviceInfo",
    "MeshNetwork",
    "Moon",
    "SC20Client",
    "SC20ConnectionError",
    "SC20Error",
    "SC20ProtocolError",
    "SC20State",
    "SC20Timeout",
    "SC20ValidationError",
    "ServerLog",
    "Setpoint",
    "async_check_connection",
]
