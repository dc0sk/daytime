"""Protocol constants for the daytime SC20 controller.

Everything here was recovered by reverse engineering; see docs/protocol/ for the evidence
behind each value.
"""

from __future__ import annotations

from typing import Final

# --- Transport ---------------------------------------------------------------------------

#: The device's WebSocket refuses the handshake without this subprotocol.
SUBPROTOCOL: Final = "arduino"

#: The web UI paces its sends this far apart. The device is an ESP8266 with ~27 KB of free
#: heap, so bursts are a real risk; keep this.
SEND_INTERVAL: Final = 0.04

#: Heartbeat period and the silence after which the connection is considered dead. Both
#: match the vendor web UI (3 s, 3 missed beats).
HEARTBEAT_INTERVAL: Final = 3.0
HEARTBEAT_TIMEOUT: Final = 9.0

#: How long to wait for the response to a GET_*/REQ_* request.
REQUEST_TIMEOUT: Final = 5.0

# --- Envelope ----------------------------------------------------------------------------

#: Stamped on every outbound frame. The device broadcasts frames to all connected clients,
#: so inbound frames carrying this value are another client's command echoed back, never
#: device state, and must be dropped.
FROM_USER: Final = "USER"

#: Address for reads: the master node answers on behalf of the mesh.
TO_MASTER: Final = "MASTER"

#: Address for writes: every lamp in the mesh. Single-lamp installations included.
TO_ALL_LIGHTS: Final = "ALL-LIGHTS"

# --- Message titles ----------------------------------------------------------------------

# Requests, paired with the response title each one produces.
REQUEST_RESPONSE: Final[dict[str, str]] = {
    "GET_USRDTA": "USRDTA",
    "GET_CLOCK": "CLOCK",
    "REQ_CCV": "CCV",
    "GET_DYCL": "DYCL",
    "GET_DSCRPTN": "DSCRPTN",
    "GET_MOON": "MOON",
    "GET_CLOUD": "CLOUD",
    "GET_ACCL": "ACCLIMATE",
    "GET_NET_ST": "NET_ST",
    "GET_NET_AP": "NET_AP",
    "GET_MESH_NETWORK": "MESH_NETWORK",
    # Unlike every other read, this one is addressed to ALL-LIGHTS rather than MASTER.
    "GET_CCMODE": "CCMODE",
}

#: Reads that are not addressed to the master node.
REQUEST_ADDRESS: Final[dict[str, str]] = {"GET_CCMODE": "ALL-LIGHTS"}

TITLE_USRDTA: Final = "USRDTA"
TITLE_CLOCK: Final = "CLOCK"
TITLE_CCV: Final = "CCV"
TITLE_DYCL: Final = "DYCL"
TITLE_DSCRPTN: Final = "DSCRPTN"
TITLE_MOON: Final = "MOON"
TITLE_CLOUD: Final = "CLOUD"
TITLE_ACCLIMATE: Final = "ACCLIMATE"
TITLE_MESH_NETWORK: Final = "MESH_NETWORK"
TITLE_CCMODE: Final = "CCMODE"

#: Inbound CCV arrives under any of these titles; they all carry the same payload.
CCV_TITLES: Final = frozenset({"CCV", "CCV-SL", "CCV-SW", "CCV-Hari"})

#: Outbound value-set title. "SL" is the slider variant the web UI uses for continuous
#: changes; "SW" is its on/off switch variant. Confirmed working on hardware: CCV-SL.
TITLE_SET_VALUES: Final = "CCV-SL"

TITLE_MANUAL_MODE: Final = "MAN_MODE"
TITLE_DAYCYCLE_MODE: Final = "DAYCL_MODE"
TITLE_PAUSE_ACCLIMATION: Final = "PAUSE_ACCLIMATION"
TITLE_PREVIEW_CURVE: Final = "PREV-CRV"
TITLE_PREVIEW_POINT: Final = "PREV-PNT"

#: Starts the controller's own over-the-air updater, which then fetches from
#: data.daytime.de and reboots. Confirmed from the vendor app: `startUpdate()` sends exactly
#: `{title:"START_FOTA", to:"ALL-LIGHTS"}`.
TITLE_START_FOTA: Final = "START_FOTA"

#: The two modes are mutually exclusive; a lamp is always in exactly one.
MODE_MANUAL: Final = TITLE_MANUAL_MODE
MODE_DAYCYCLE: Final = TITLE_DAYCYCLE_MODE

# --- Channel model -----------------------------------------------------------------------

#: The SC20 has exactly three channels, in this wire order. Confirmed on hardware.
CHANNEL_NAMES: Final = ("White", "Blue", "Red")
CHANNEL_COUNT: Final = len(CHANNEL_NAMES)

#: The colours the vendor UI uses for each channel, kept so HA can present them alike.
CHANNEL_COLORS: Final = ("#b9e2fa", "#2fa8e0", "#e40131")

#: Channel values are integer percent. The SC20 applies no gamma and no scaling, so the
#: wire value is the UI value.
MIN_PERCENT: Final = 0
MAX_PERCENT: Final = 100

# --- Daycycle ----------------------------------------------------------------------------

#: Minutes in a day. The schedule is anchored by a row at 0 and a row at 1440.
DAY_MINUTES: Final = 1440

#: The web UI refuses to save more than this many setpoints ("Max Light Points: 30!!").
#: The firmware limit is unknown, so this is enforced as if it were the device's.
MAX_SETPOINTS: Final = 30

# --- HTTP --------------------------------------------------------------------------------

PATH_CONNECTION_CHECK: Final = "/connectioncheck"
PATH_SERVER_LOG: Final = "/serverLog"

#: Requesting either of these wipes the device with a plain GET. Listed so that no code
#: here ever builds a URL that could reach them by accident.
FORBIDDEN_PATHS: Final = frozenset({"/formateeprom", "/formatspiffs", "/update"})
