"""Typed representations of the SC20's state.

These hold values in the units a caller would expect (minutes, percent, days). Everything
needed to get them on and off the wire lives in `protocol.py` — including the places where
the wire disagrees with those units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise

from .const import CHANNEL_COUNT, DAY_MINUTES, MAX_PERCENT, MIN_PERCENT, MODE_DAYCYCLE
from .exceptions import SC20ValidationError


def _check_percent(value: int, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SC20ValidationError(f"{what} must be an integer, got {value!r}")
    if not MIN_PERCENT <= value <= MAX_PERCENT:
        raise SC20ValidationError(f"{what} must be {MIN_PERCENT}-{MAX_PERCENT}, got {value}")
    return value


def _check_minute(value: int, what: str, *, limit: int = DAY_MINUTES) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SC20ValidationError(f"{what} must be an integer, got {value!r}")
    if not 0 <= value <= limit:
        raise SC20ValidationError(f"{what} must be 0-{limit}, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class ChannelValues:
    """Per-channel intensity in percent, in wire order: White, Blue, Red.

    When read from the device this is the *actual* live output — clouds, moonlight and
    acclimatisation have already been applied to it, so in scheduled mode it drifts.
    """

    values: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.values) != CHANNEL_COUNT:
            raise SC20ValidationError(
                f"expected {CHANNEL_COUNT} channel values, got {len(self.values)}"
            )
        for index, value in enumerate(self.values):
            _check_percent(value, f"channel {index}")

    @classmethod
    def uniform(cls, value: int) -> ChannelValues:
        """All channels at the same level."""
        return cls(tuple([value] * CHANNEL_COUNT))

    @property
    def is_off(self) -> bool:
        return not any(self.values)

    @property
    def brightest(self) -> int:
        """The highest channel, which is what a single master brightness should track."""
        return max(self.values)

    def scaled_to(self, brightness: int) -> ChannelValues:
        """Rescale so the brightest channel becomes `brightness`, keeping channel ratios.

        Used by the master light: changing overall brightness should not change colour. If
        every channel is currently off there is no ratio to preserve, so all channels go to
        `brightness` together.
        """
        _check_percent(brightness, "brightness")
        top = self.brightest
        if top == 0:
            return ChannelValues.uniform(brightness)
        return ChannelValues(tuple(round(v * brightness / top) for v in self.values))


@dataclass(frozen=True, slots=True)
class Setpoint:
    """One point of the daycycle: a time of day and the level of each channel there."""

    minute: int
    values: tuple[int, ...]

    def __post_init__(self) -> None:
        _check_minute(self.minute, "setpoint minute")
        if len(self.values) != CHANNEL_COUNT:
            raise SC20ValidationError(
                f"setpoint at minute {self.minute} has {len(self.values)} channels,"
                f" expected {CHANNEL_COUNT}"
            )
        for index, value in enumerate(self.values):
            _check_percent(value, f"setpoint at minute {self.minute}, channel {index}")


@dataclass(frozen=True, slots=True)
class Daycycle:
    """The lighting programme: a sorted list of setpoints spanning minute 0 to 1440."""

    setpoints: tuple[Setpoint, ...]

    def level_at(self, minute: int) -> ChannelValues:
        """The programmed level at a time of day, interpolating linearly between setpoints.

        Linear interpolation matches the vendor UI's graph and its own preset generator, but
        the firmware's actual curve was never confirmed — treat this as an estimate of what
        the lamp is doing, not as ground truth. The live value from `REQ_CCV` is
        authoritative, though it also includes effect modulation.
        """
        _check_minute(minute, "minute")
        points = self.setpoints
        if not points:
            return ChannelValues.uniform(0)
        if minute <= points[0].minute:
            return ChannelValues(points[0].values)
        for earlier, later in pairwise(points):
            if minute > later.minute:
                continue
            span = later.minute - earlier.minute
            if span == 0:
                return ChannelValues(later.values)
            ratio = (minute - earlier.minute) / span
            return ChannelValues(
                tuple(
                    round(a + (b - a) * ratio)
                    for a, b in zip(earlier.values, later.values, strict=True)
                )
            )
        return ChannelValues(points[-1].values)


@dataclass(frozen=True, slots=True)
class DaycycleDescription:
    """The `DSCRPTN` sidecar: metadata about how the daycycle was authored.

    The device stores this opaquely and never acts on it — only `DYCL` is executed. It is
    modelled so that saving a schedule can leave the vendor app's own view coherent.
    """

    conf_id: int = -1
    expert_mode: bool = False
    start: int = 0
    end: int = DAY_MINUTES
    sunrise: int = 0
    sunset: int = 0
    intensity: int = MAX_PERCENT
    individual: bool = False
    intensities: tuple[int, ...] = field(default=(MAX_PERCENT,) * CHANNEL_COUNT)

    @property
    def levels(self) -> tuple[int, ...]:
        """The daytime level of each channel.

        `intensities` only applies when `individual` is set; otherwise the single
        `intensity` governs every channel. A device was observed holding a stale
        `intensities:85,85,85` alongside `intensity:90` while actually running 90, so
        reading the wrong one yields a schedule the lamp is not on.
        """
        if not self.individual:
            return (self.intensity,) * CHANNEL_COUNT
        values = tuple(self.intensities[:CHANNEL_COUNT])
        # Pad if the device sent fewer values than this product has channels.
        return values + (self.intensity,) * (CHANNEL_COUNT - len(values))

    def to_daycycle(self) -> Daycycle:
        """Build the trapezoid schedule these settings describe.

        This is the vendor app's "easy mode": dark until `start`, ramping up over `sunrise`
        minutes, holding, then ramping down over `sunset` minutes to be dark again at
        `end`, with anchor rows at minute 0 and 1440.

        Verified against hardware — feeding a device its own `DSCRPTN` through this
        reproduces the `DYCL` it is actually running, setpoint for setpoint.

        Raises `SC20ValidationError` when the ramps do not fit between `start` and `end`,
        which would otherwise emit setpoints out of order and write a corrupt schedule.
        """
        if not 0 <= self.start < self.end <= DAY_MINUTES:
            raise SC20ValidationError(
                "the lighting day must start before it ends and fit within one day, got"
                f" start={self.start}, end={self.end}"
            )
        full_from = self.start + self.sunrise
        full_until = self.end - self.sunset
        if full_from > full_until:
            raise SC20ValidationError(
                f"sunrise ({self.sunrise} min) and sunset ({self.sunset} min) together are"
                f" longer than the {self.end - self.start} min between start and end"
            )

        levels = self.levels
        dark = (0,) * CHANNEL_COUNT
        points = [Setpoint(0, dark)]
        # Skip any point that would repeat the previous minute: a start of 0, or ramps
        # that meet so the flat middle collapses to a single peak.
        for minute, values in (
            (self.start, dark),
            (full_from, levels),
            (full_until, levels),
            (self.end, dark),
            (DAY_MINUTES, dark),
        ):
            if minute > points[-1].minute:
                points.append(Setpoint(minute, values))
        return Daycycle(setpoints=tuple(points))


@dataclass(frozen=True, slots=True)
class Moon:
    """Moonlight simulation.

    `start` may be greater than `end`: the window wraps past midnight, which is the normal
    configuration (e.g. 22:00 to 06:00).
    """

    active: bool
    cycle: bool
    minimum: int
    maximum: int
    color: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Cloud:
    """Cloud simulation, in the units a person would use.

    On the wire the durations are seconds and the intensities are inverted and swapped;
    `protocol.py` is the only place that knows about that.
    """

    active: bool
    max_per_day: int
    probability: int
    #: Cloud strength as a percentage: 0 means no dimming, 100 means fully dark.
    min_intensity: int
    max_intensity: int
    min_duration_minutes: int
    max_duration_minutes: int
    #: Multi-lamp transition style. 0/1/2 is believed to be
    #: synchronous/delayed/individual, inferred from the UI's string order only.
    mode: int = 0


@dataclass(frozen=True, slots=True)
class Acclimate:
    """Acclimatisation ramp for a newly set-up tank."""

    active: bool
    paused: bool
    duration_days: int
    intensity_reduction: int
    current_day: int


@dataclass(frozen=True, slots=True)
class Clock:
    """The device's own clock, and the primary source of the current mode.

    The mode rides along on every CLOCK frame, which arrives in the connect burst and
    immediately after any mode change — so it is read from here rather than from the
    separate `GET_CCMODE` request, which reports the same value one round trip later.
    """

    timestamp: datetime
    mode: str = MODE_DAYCYCLE

    @property
    def is_manual(self) -> bool:
        return self.mode != MODE_DAYCYCLE


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity and configuration from `USRDTA`."""

    name: str
    aquarium_name: str
    mode: str
    host: str
    tank_config: str
    language: str
    #: Minutes east of UTC.
    timezone: int
    dst: bool
    #: `[webserver, website]`, displayed by the vendor UI as `0X.Y`.
    revision: tuple[int, int]
    latest_revision: tuple[int, int]
    firmware_available: bool
    #: Total operating minutes, the same counter `/serverLog` reports.
    live_time: int
    #: Which LED colour strings are attached, as a vendor code. Arrives as a string.
    power: str
    mesh_enabled: bool
    group_id: int

    @staticmethod
    def format_revision(value: int) -> str:
        """Render a revision number the way the vendor UI does: 23 becomes "02.3"."""
        return f"0{value // 10}.{value % 10}"

    @property
    def firmware_version(self) -> str:
        return self.format_revision(self.revision[0])

    @property
    def webapp_version(self) -> str:
        return self.format_revision(self.revision[1])


@dataclass(frozen=True, slots=True)
class MeshNetwork:
    """The lamps the master knows about, by BSSID. The last entry is the master itself."""

    clients: tuple[str, ...]

    @property
    def master(self) -> str | None:
        return self.clients[-1] if self.clients else None


@dataclass(frozen=True, slots=True)
class ServerLog:
    """Counters scraped from the device's `/serverLog` page.

    Not available over the WebSocket at all — this HTML page is the only source.
    """

    uptime_minutes: int | None = None
    free_heap: int | None = None
    operating_hours: int | None = None
    firmware_version: int | None = None
    webapp_version: int | None = None
