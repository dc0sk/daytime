"""Translation between SC20 wire frames and the models in `models.py`.

This module is the only place that knows how the wire differs from sane units. Every quirk
below was recovered by reverse engineering and is documented in docs/protocol/; if one of
them leaks into entity code, a setting will be silently corrupted the first time a user
changes it.

The quirks, in one place:

* `CLOUD` durations are seconds on the wire, minutes everywhere else.
* `CLOUD` intensities are inverted *and* swapped: the wire carries the remaining light
  level, the model carries cloud strength, so `wire.min` pairs with `model.max`.
* `USRDTA.power` arrives as a string.
* Booleans are 0/1 integers.
* `CLOCK.month` is 1-based, matching `datetime`. The vendor's JavaScript adds one because JS
  months are 0-based; Python must not copy that.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .const import (
    CHANNEL_COUNT,
    DAY_MINUTES,
    FROM_USER,
    MAX_PERCENT,
    MAX_SETPOINTS,
    MIN_PERCENT,
    MODE_DAYCYCLE,
    MODE_MANUAL,
    TITLE_ACCLIMATE,
    TITLE_CLOUD,
    TITLE_DAYCYCLE_MODE,
    TITLE_DSCRPTN,
    TITLE_DYCL,
    TITLE_MANUAL_MODE,
    TITLE_MOON,
    TITLE_PAUSE_ACCLIMATION,
    TITLE_PREVIEW_CURVE,
    TITLE_SET_VALUES,
    TITLE_START_FOTA,
    TO_ALL_LIGHTS,
    TO_MASTER,
)
from .exceptions import SC20ProtocolError, SC20ValidationError
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

# --- small helpers -----------------------------------------------------------------------


def _flag(value: Any) -> bool:
    """Read a wire boolean. They are 0/1 integers, but strings turn up on some fields."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def _int(frame: dict[str, Any], key: str, default: int = 0) -> int:
    """Read an integer that may have been sent as a string (`power` is, for one)."""
    value = frame.get(key, default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _clamp_percent(value: Any) -> int:
    """Coerce to an in-range percentage.

    Applied to values coming *from* the device, which is trusted to be self-consistent but
    not to be in range. Values going *to* the device are validated and rejected instead.
    """
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return MIN_PERCENT
    return max(MIN_PERCENT, min(MAX_PERCENT, number))


# --- frame classification ----------------------------------------------------------------


def is_echo(frame: dict[str, Any]) -> bool:
    """True if this frame is another client's command, not device state.

    The device broadcasts every frame to every connected WebSocket client, so anything
    stamped `from:"USER"` originated at a client — possibly at us. Acting on these would
    make the integration respond to its own writes and to whatever the phone app is doing.
    """
    return frame.get("from") == FROM_USER


def iter_frames(payload: Any) -> list[dict[str, Any]]:
    """Normalise a decoded message into a list of frames.

    The device sends a bare object most of the time, but dumps its whole state as two JSON
    arrays right after connect. Code that assumes an object crashes on that burst.
    """
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise SC20ProtocolError(
        f"expected an object or array of objects, got {type(payload).__name__}"
    )


# --- outbound: requests ------------------------------------------------------------------


def build_request(title: str, *, to: str = TO_MASTER) -> dict[str, Any]:
    """A read request. These are the only frames the device answers."""
    return {"title": title, "to": to, "from": FROM_USER}


def build_set_mode(manual: bool) -> dict[str, Any]:
    """Switch between manual override and the scheduled programme.

    The two modes are mutually exclusive. Setting channel values only takes effect in manual
    mode, so a caller must send this first.
    """
    return {
        "title": TITLE_MANUAL_MODE if manual else TITLE_DAYCYCLE_MODE,
        "to": TO_ALL_LIGHTS,
        "from": FROM_USER,
    }


def build_set_values(values: ChannelValues) -> dict[str, Any]:
    """Set the live channel levels. Only has an effect while in manual mode."""
    return {
        "title": TITLE_SET_VALUES,
        "to": TO_ALL_LIGHTS,
        "currentValues": list(values.values),
        "from": FROM_USER,
    }


def build_set_daycycle(daycycle: Daycycle) -> dict[str, Any]:
    """Overwrite the lighting programme.

    Validated hard, because this write is unacknowledged, replaces the user's real schedule,
    and cannot be undone on the device.
    """
    validate_daycycle(daycycle)
    return {
        "title": TITLE_DYCL,
        "to": TO_ALL_LIGHTS,
        "configuration": [[p.minute, *p.values] for p in daycycle.setpoints],
        "from": FROM_USER,
    }


def validate_daycycle(daycycle: Daycycle) -> None:
    """Reject anything the device or the vendor app would not accept.

    Raises `SC20ValidationError` rather than letting a malformed schedule reach the lamp.
    """
    points = daycycle.setpoints
    if not points:
        raise SC20ValidationError("a daycycle needs at least one setpoint")
    if len(points) > MAX_SETPOINTS:
        raise SC20ValidationError(
            f"a daycycle may hold at most {MAX_SETPOINTS} setpoints, got {len(points)}"
        )
    minutes = [p.minute for p in points]
    if minutes != sorted(minutes):
        raise SC20ValidationError(f"setpoints must be sorted by time, got {minutes}")
    if len(set(minutes)) != len(minutes):
        raise SC20ValidationError(f"setpoints must have distinct times, got {minutes}")
    if minutes[0] != 0:
        raise SC20ValidationError(f"the first setpoint must be at minute 0, got {minutes[0]}")
    if minutes[-1] != DAY_MINUTES:
        raise SC20ValidationError(
            f"the last setpoint must be at minute {DAY_MINUTES}, got {minutes[-1]}"
        )
    # Setpoint.__post_init__ already range-checked each channel value.


def build_set_description(description: DaycycleDescription) -> dict[str, Any]:
    return {
        "title": TITLE_DSCRPTN,
        "to": TO_ALL_LIGHTS,
        "description": encode_description(description),
        "from": FROM_USER,
    }


def build_set_moon(moon: Moon) -> dict[str, Any]:
    return {
        "title": TITLE_MOON,
        "to": TO_ALL_LIGHTS,
        "maxmoonlight": moon.maximum,
        "minmoonlight": moon.minimum,
        "moonlightActive": int(moon.active),
        "moonlightCycle": int(moon.cycle),
        "color": moon.color,
        "moonStart": moon.start,
        "moonEnd": moon.end,
        "from": FROM_USER,
    }


def build_set_cloud(cloud: Cloud) -> dict[str, Any]:
    """Encode cloud settings, applying both wire quirks.

    Durations become seconds. Intensities become the *remaining light level* — inverted, and
    with min and max exchanged, because inverting a range reverses its ends.
    """
    return {
        "title": TITLE_CLOUD,
        "to": TO_ALL_LIGHTS,
        "cloudActive": int(cloud.active),
        "maxAmount": cloud.max_per_day,
        "probability": cloud.probability,
        "minIntensity": MAX_PERCENT - cloud.max_intensity,
        "maxIntensity": MAX_PERCENT - cloud.min_intensity,
        "minDuration": cloud.min_duration_minutes * 60,
        "maxDuration": cloud.max_duration_minutes * 60,
        "mode": cloud.mode,
        "from": FROM_USER,
    }


def build_set_acclimate(acclimate: Acclimate) -> dict[str, Any]:
    return {
        "title": TITLE_ACCLIMATE,
        "to": TO_ALL_LIGHTS,
        "duration": acclimate.duration_days,
        "intensityReduction": acclimate.intensity_reduction,
        "currentAcclDay": acclimate.current_day,
        "acclActive": int(acclimate.active),
        "pause": int(acclimate.paused),
        "from": FROM_USER,
    }


def build_pause_acclimation(paused: bool) -> dict[str, Any]:
    return {
        "title": TITLE_PAUSE_ACCLIMATION,
        "to": TO_ALL_LIGHTS,
        "pause": int(paused),
        "from": FROM_USER,
    }


def build_set_clock(moment: datetime, mode: str = MODE_DAYCYCLE) -> dict[str, Any]:
    """Set the device clock.

    `month` is 1-based on the wire, which is what `datetime.month` already is. The vendor's
    JavaScript adds one only because JS `Date` months are 0-based.
    """
    return {
        "title": "CLOCK",
        "to": TO_ALL_LIGHTS,
        "year": moment.year,
        "month": moment.month,
        "day": moment.day,
        "hour": moment.hour,
        "min": moment.minute,
        "sec": moment.second,
        "mode": mode,
        "from": FROM_USER,
    }


def build_start_firmware_update() -> dict[str, Any]:
    """Tell the controller to update itself.

    It then downloads from data.daytime.de and reboots — so it needs working internet
    access, and it will be off the network for a few minutes. There is no way to cancel
    once this is sent, and a failed write leaves the lamp needing a manual reflash.

    Confirmed from the vendor app, which sends exactly this and nothing else.
    """
    return {"title": TITLE_START_FOTA, "to": TO_ALL_LIGHTS, "from": FROM_USER}


def build_preview_curve(speed_factor: int, start: int, end: int) -> dict[str, Any]:
    """Fast-forward replay of the programmed curve. Stopped by sending DAYCL_MODE."""
    return {
        "title": TITLE_PREVIEW_CURVE,
        "to": TO_ALL_LIGHTS,
        "speedFactor": speed_factor,
        "startTime": start,
        "endTime": end,
        "from": FROM_USER,
    }


# --- inbound: parsers --------------------------------------------------------------------


def parse_channel_values(frame: dict[str, Any]) -> ChannelValues:
    """Parse a CCV frame.

    The device may report more channels than the SC20 has; the vendor UI ignores the extras
    and so do we.
    """
    raw = frame.get("currentValues")
    if not isinstance(raw, list) or len(raw) < CHANNEL_COUNT:
        raise SC20ProtocolError(f"CCV has no usable currentValues: {raw!r}")
    return ChannelValues(tuple(_clamp_percent(v) for v in raw[:CHANNEL_COUNT]))


def parse_daycycle(frame: dict[str, Any]) -> Daycycle:
    """Parse a DYCL frame.

    Rows past the first that sit at minute 0 are padding: the vendor UI drops them, and this
    unit was observed not to send any. Rows are also truncated to the SC20's channel count.
    """
    raw = frame.get("configuration")
    if not isinstance(raw, list):
        raise SC20ProtocolError(f"DYCL has no configuration: {raw!r}")

    points: list[Setpoint] = []
    for index, row in enumerate(raw):
        if not isinstance(row, list) or len(row) < CHANNEL_COUNT + 1:
            raise SC20ProtocolError(f"DYCL row {index} is malformed: {row!r}")
        minute = int(row[0])
        if index > 0 and minute == 0:
            continue  # padding row
        values = tuple(_clamp_percent(v) for v in row[1 : CHANNEL_COUNT + 1])
        points.append(Setpoint(minute=min(minute, DAY_MINUTES), values=values))
    return Daycycle(setpoints=tuple(points))


def encode_description(description: DaycycleDescription) -> str:
    """Serialise DSCRPTN. The vendor parser is order-sensitive, so key order is fixed."""
    return ";".join(
        (
            f"confId:{description.conf_id}",
            f"expMode:{str(description.expert_mode).lower()}",
            f"start:{description.start}",
            f"end:{description.end}",
            f"sunrise:{description.sunrise}",
            f"sunset:{description.sunset}",
            f"intensity:{description.intensity}",
            f"individual:{str(description.individual).lower()}",
            "intensities:" + ",".join(str(v) for v in description.intensities),
        )
    )


def parse_description(frame: dict[str, Any]) -> DaycycleDescription:
    """Parse DSCRPTN. Unknown or missing keys fall back to the dataclass defaults."""
    text = frame.get("description")
    if not isinstance(text, str):
        raise SC20ProtocolError(f"DSCRPTN has no description string: {text!r}")

    fields: dict[str, str] = {}
    for part in text.split(";"):
        key, separator, value = part.partition(":")
        if separator:
            fields[key.strip()] = value.strip()

    def as_int(key: str, default: int) -> int:
        try:
            return int(fields[key])
        except (KeyError, ValueError):
            return default

    intensities: tuple[int, ...] = (MAX_PERCENT,) * CHANNEL_COUNT
    if "intensities" in fields:
        parsed = []
        for chunk in fields["intensities"].split(","):
            try:
                parsed.append(_clamp_percent(int(chunk)))
            except ValueError:
                continue
        if parsed:
            intensities = tuple(parsed)

    return DaycycleDescription(
        conf_id=as_int("confId", -1),
        expert_mode=fields.get("expMode", "false").lower() == "true",
        start=as_int("start", 0),
        end=as_int("end", DAY_MINUTES),
        sunrise=as_int("sunrise", 0),
        sunset=as_int("sunset", 0),
        intensity=as_int("intensity", MAX_PERCENT),
        individual=fields.get("individual", "false").lower() == "true",
        intensities=intensities,
    )


def parse_moon(frame: dict[str, Any]) -> Moon:
    return Moon(
        active=_flag(frame.get("moonlightActive")),
        cycle=_flag(frame.get("moonlightCycle")),
        minimum=_clamp_percent(frame.get("minmoonlight", 0)),
        maximum=_clamp_percent(frame.get("maxmoonlight", 0)),
        color=str(frame.get("color", "")),
        start=_int(frame, "moonStart"),
        end=_int(frame, "moonEnd"),
    )


def parse_cloud(frame: dict[str, Any]) -> Cloud:
    """Parse a CLOUD frame, undoing both wire quirks.

    Mirrors `build_set_cloud`: seconds become minutes, and the remaining-light levels become
    cloud strengths, which inverts them and exchanges min with max.
    """
    return Cloud(
        active=_flag(frame.get("cloudActive")),
        max_per_day=_int(frame, "maxAmount"),
        probability=_clamp_percent(frame.get("probability", 0)),
        min_intensity=MAX_PERCENT - _clamp_percent(frame.get("maxIntensity", MAX_PERCENT)),
        max_intensity=MAX_PERCENT - _clamp_percent(frame.get("minIntensity", MAX_PERCENT)),
        min_duration_minutes=_int(frame, "minDuration") // 60,
        max_duration_minutes=_int(frame, "maxDuration") // 60,
        mode=_int(frame, "mode"),
    )


def parse_acclimate(frame: dict[str, Any]) -> Acclimate:
    return Acclimate(
        active=_flag(frame.get("acclActive")),
        paused=_flag(frame.get("pause")),
        duration_days=_int(frame, "duration"),
        intensity_reduction=_clamp_percent(frame.get("intensityReduction", 0)),
        current_day=_int(frame, "currentAcclDay"),
    )


def parse_clock(frame: dict[str, Any]) -> Clock:
    """Parse a CLOCK frame. `month` is 1-based on the wire, as `datetime` wants it."""
    try:
        timestamp = datetime(
            year=_int(frame, "year", 1970),
            month=_int(frame, "month", 1),
            day=_int(frame, "day", 1),
            hour=_int(frame, "hour"),
            minute=_int(frame, "min"),
            second=_int(frame, "sec"),
        )
    except ValueError as err:
        raise SC20ProtocolError(f"CLOCK carries an impossible date: {err}") from err

    mode = frame.get("mode")
    return Clock(
        timestamp=timestamp,
        mode=mode if mode in (MODE_MANUAL, MODE_DAYCYCLE) else MODE_DAYCYCLE,
    )


def parse_device_info(frame: dict[str, Any]) -> DeviceInfo:
    def as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def revision(key: str) -> tuple[int, int]:
        raw = frame.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            return (as_int(raw[0]), as_int(raw[1]))
        return (0, 0)

    mode = frame.get("mode")
    return DeviceInfo(
        name=str(frame.get("name", "")),
        aquarium_name=str(frame.get("aqName", "")),
        mode=mode if mode in (MODE_MANUAL, MODE_DAYCYCLE) else MODE_DAYCYCLE,
        host=str(frame.get("host", "")),
        tank_config=str(frame.get("tankconfig", "")),
        language=str(frame.get("language", "")),
        timezone=_int(frame, "timezone"),
        dst=_flag(frame.get("dst")),
        revision=revision("revision"),
        latest_revision=revision("latestAvailableRevision"),
        firmware_available=_flag(frame.get("firmwareAvailable")),
        live_time=_int(frame, "liveTime"),
        power=str(frame.get("power", "")),
        mesh_enabled=_flag(frame.get("meshing")),
        group_id=_int(frame, "groupID"),
    )


def parse_mesh_network(frame: dict[str, Any]) -> MeshNetwork:
    raw = frame.get("clientList")
    if not isinstance(raw, list):
        raise SC20ProtocolError(f"MESH_NETWORK has no clientList: {raw!r}")
    return MeshNetwork(clients=tuple(str(client) for client in raw))


# --- /serverLog scraping -----------------------------------------------------------------

_SERVER_LOG_FIELDS: dict[str, re.Pattern[str]] = {
    "uptime_minutes": re.compile(r"Server Uptime\s*:\s*(\d+)"),
    "free_heap": re.compile(r"Server Heap\s*:\s*(\d+)"),
    "operating_hours": re.compile(r"Operating Hours\s*=\s*(\d+)"),
    "firmware_version": re.compile(r"Firmware Version:\s*(\d+)"),
    "webapp_version": re.compile(r"Webapp Version:\s*(\d+)"),
}


def parse_server_log(html: str) -> ServerLog:
    """Scrape the `/serverLog` page.

    Uptime, heap and operating hours appear nowhere in the WebSocket protocol; this page is
    the only source, so it is scraped rather than parsed. Missing fields stay None instead
    of failing the whole read.
    """
    plain = re.sub(r"<[^>]+>", "\n", html)
    found: dict[str, int] = {}
    for name, pattern in _SERVER_LOG_FIELDS.items():
        match = pattern.search(plain)
        if match:
            found[name] = int(match.group(1))
    return ServerLog(**found)


def parse_scen_file(text: str) -> Daycycle:
    """Parse a vendor `.scen` file.

    The format is a bare JSON array of `[minute, ch...]` rows — exactly a DYCL
    `configuration` value, which is how the format was decoded in the first place.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise SC20ProtocolError(f"not a valid .scen file: {err}") from err
    return parse_daycycle({"configuration": data})


def encode_scen_file(daycycle: Daycycle) -> str:
    """Render a daycycle as a `.scen` file the vendor app can import.

    Written without whitespace so the output is byte-identical to the vendor's own files.
    """
    rows = [[p.minute, *p.values] for p in daycycle.setpoints]
    return json.dumps(rows, separators=(",", ":"))
