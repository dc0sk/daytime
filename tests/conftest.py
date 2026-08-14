"""Shared fixtures.

The device frames used here are the ones a real SC20 returned, taken from the capture in
docs/protocol/capture/ — so the client is exercised against recorded hardware output rather
than against schemas someone inferred.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

CAPTURE_DIR = Path(__file__).parent.parent / "docs" / "protocol" / "capture"


def _load_recorded_frames() -> dict[str, dict[str, Any]]:
    """The latest frame of each title from the newest capture."""
    captures = sorted(CAPTURE_DIR.glob("capture-*.jsonl"))
    if not captures:
        return {}
    frames: dict[str, dict[str, Any]] = {}
    for line in captures[-1].read_text().splitlines():
        if not line.strip():
            continue
        frame = json.loads(line)
        if frame.get("_sent") or frame.get("_echo"):
            continue
        title = frame.get("title")
        if isinstance(title, str):
            frames[title] = {k: v for k, v in frame.items() if not k.startswith(("ts", "_"))}
    return frames


#: Fallback used when no capture is committed, so the suite still runs on a fresh clone.
_FALLBACK_FRAMES: dict[str, dict[str, Any]] = {
    "USRDTA": {
        "title": "USRDTA",
        "from": "AA:BB:CC:DD:EE:FF",
        "name": "SC20_0000000",
        "aqName": "",
        "mode": "DAYCL_MODE",
        "version": 4,
        "language": "DE",
        "timezone": 60,
        "dst": 1,
        "tankconfig": "DAYTIME",
        "power": "17",
        "netmode": "ST",
        "host": "sc20",
        "groupID": 0,
        "meshing": 1,
        "revision": [23, 15],
        "latestAvailableRevision": [23, 15],
        "firmwareAvailable": 0,
        "liveTime": 951240,
        "to": "USER",
    },
    "CLOCK": {
        "title": "CLOCK",
        "from": "AA:BB:CC:DD:EE:FF",
        "year": 2026,
        "month": 8,
        "day": 14,
        "hour": 10,
        "min": 30,
        "sec": 26,
        "mode": "DAYCL_MODE",
        "to": "USER",
    },
    "CCV": {"title": "CCV", "from": "AA:BB:CC:DD:EE:FF", "currentValues": [90, 90, 90]},
    "DYCL": {
        "title": "DYCL",
        "from": "AA:BB:CC:DD:EE:FF",
        "configuration": [
            [0, 0, 0, 0],
            [360, 0, 0, 0],
            [480, 90, 90, 90],
            [1200, 90, 90, 90],
            [1320, 0, 0, 0],
            [1440, 0, 0, 0],
        ],
    },
    "DSCRPTN": {
        "title": "DSCRPTN",
        "from": "AA:BB:CC:DD:EE:FF",
        "description": (
            "confId:-1;expMode:false;start:360;end:1320;sunrise:120;sunset:120;"
            "intensity:90;individual:false;intensities:85,85,85"
        ),
    },
    "MOON": {
        "title": "MOON",
        "from": "AA:BB:CC:DD:EE:FF",
        "maxmoonlight": 30,
        "minmoonlight": 2,
        "moonlightActive": 0,
        "moonlightCycle": 1,
        "color": "b",
        "moonStart": 1320,
        "moonEnd": 360,
    },
    "CLOUD": {
        "title": "CLOUD",
        "from": "AA:BB:CC:DD:EE:FF",
        "probability": 65,
        "maxAmount": 150,
        "minIntensity": 60,
        "maxIntensity": 100,
        "minDuration": 600,
        "maxDuration": 1500,
        "cloudActive": 1,
        "mode": 0,
    },
    "ACCLIMATE": {
        "title": "ACCLIMATE",
        "from": "AA:BB:CC:DD:EE:FF",
        "duration": 30,
        "intensityReduction": 50,
        "currentAcclDay": 0,
        "acclActive": 0,
        "pause": 0,
    },
    "NET_ST": {"title": "NET_ST", "from": "AA:BB:CC:DD:EE:FF", "dhcp": 1},
    "NET_AP": {"title": "NET_AP", "from": "AA:BB:CC:DD:EE:FF", "apSSID": "Smart Control SC20"},
    "MESH_NETWORK": {
        "title": "MESH_NETWORK",
        "from": "AA:BB:CC:DD:EE:FF",
        "clientList": ["AA:BB:CC:DD:EE:FF"],
    },
    "CCMODE": {"title": "CCMODE", "from": "AA:BB:CC:DD:EE:FF", "mode": "DAYCL_MODE"},
}


@pytest.fixture
def device_frames() -> dict[str, dict[str, Any]]:
    """One frame per title, preferring real recorded output over the fallback."""
    recorded = _load_recorded_frames()
    frames = dict(_FALLBACK_FRAMES)
    frames.update({title: frame for title, frame in recorded.items() if title in frames})
    return frames


@pytest.fixture
def server_log_html() -> str:
    return (
        "<body> <h1> Server Log </h1><br>Server Uptime  :   6601 minutes<br>"
        "Server Heap  :   26848<br><br>----- Test Date @ Daytime: <br>17.12.2025,13:2<br>"
        "Operating Hours = 951240<br><br>Firmware Version: 23<br>Webapp Version: 23</body>"
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Home Assistant only loads custom_components in tests when asked to."""
    return enable_custom_integrations
