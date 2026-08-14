"""Tests for the wire encoding.

The point of this file is the asymmetric assertions. A round-trip test (`parse(build(x))
== x`) would pass even if a conversion were deleted from *both* directions, which is
exactly how a unit bug survives a test suite. So every conversion is pinned to the literal
value that must appear on the wire, and separately to what must come back out.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from custom_components.daytime_sc20.api import (
    ChannelValues,
    Cloud,
    Daycycle,
    DaycycleDescription,
    SC20ProtocolError,
    SC20ValidationError,
    Setpoint,
    protocol,
)

SCENARIO_DIR = Path(__file__).parent.parent / "docs" / "protocol" / "scenarios"


# --- CLOUD: seconds, inverted, swapped ---------------------------------------------------


def test_cloud_durations_are_seconds_on_the_wire() -> None:
    """Minutes in the model, seconds on the wire. Pinned to the literal."""
    cloud = Cloud(
        active=True,
        max_per_day=150,
        probability=65,
        min_intensity=0,
        max_intensity=40,
        min_duration_minutes=10,
        max_duration_minutes=25,
    )
    frame = protocol.build_set_cloud(cloud)
    assert frame["minDuration"] == 600
    assert frame["maxDuration"] == 1500


def test_cloud_intensities_are_inverted_and_swapped_on_the_wire() -> None:
    """The wire carries remaining light, the model carries cloud strength.

    Inverting a range reverses its ends, so the model's *max* becomes the wire's *min*.
    Both halves are asserted: a test that only checked inversion would miss the swap.
    """
    cloud = Cloud(
        active=True,
        max_per_day=0,
        probability=0,
        min_intensity=10,
        max_intensity=40,
        min_duration_minutes=0,
        max_duration_minutes=0,
    )
    frame = protocol.build_set_cloud(cloud)
    assert frame["minIntensity"] == 60, "wire min must be 100 - model max"
    assert frame["maxIntensity"] == 90, "wire max must be 100 - model min"


def test_cloud_parses_the_real_device_frame() -> None:
    """The frame this unit actually returned, decoded into human units.

    The expected values are not a guess: a screenshot of the vendor app taken while the
    device held exactly this configuration shows 150 clouds per day, 10-25 min duration,
    0-40 % strength and 65 % probability. See docs/protocol/README.md.
    """
    cloud = protocol.parse_cloud(
        {
            "probability": 65,
            "maxAmount": 150,
            "minIntensity": 60,
            "maxIntensity": 100,
            "minDuration": 600,
            "maxDuration": 1500,
            "cloudActive": 1,
            "mode": 0,
        }
    )
    assert cloud.active is True
    assert cloud.min_duration_minutes == 10
    assert cloud.max_duration_minutes == 25
    # wire min 60 / max 100 remaining light -> strength 0 to 40
    assert cloud.min_intensity == 0
    assert cloud.max_intensity == 40


def test_cloud_round_trip_is_stable() -> None:
    """Belt and braces on top of the literal assertions above."""
    cloud = Cloud(
        active=True,
        max_per_day=150,
        probability=65,
        min_intensity=5,
        max_intensity=40,
        min_duration_minutes=10,
        max_duration_minutes=25,
        mode=1,
    )
    assert protocol.parse_cloud(protocol.build_set_cloud(cloud)) == cloud


# --- CLOCK: 1-based month ----------------------------------------------------------------


def test_clock_month_is_one_based_and_not_offset() -> None:
    """The vendor's JavaScript adds one because JS months are 0-based. Python must not.

    August must go on the wire as 8, not 9.
    """
    frame = protocol.build_set_clock(datetime(2026, 8, 14, 10, 30, 26))
    assert frame["month"] == 8
    assert frame["year"] == 2026
    assert frame["day"] == 14
    assert frame["hour"] == 10
    assert frame["min"] == 30
    assert frame["sec"] == 26


def test_clock_parses_the_real_device_frame() -> None:
    clock = protocol.parse_clock(
        {
            "year": 2026,
            "month": 8,
            "day": 14,
            "hour": 10,
            "min": 30,
            "sec": 26,
            "mode": "DAYCL_MODE",
        }
    )
    assert clock.timestamp == datetime(2026, 8, 14, 10, 30, 26)
    assert clock.mode == "DAYCL_MODE"
    assert clock.is_manual is False


def test_clock_rejects_an_impossible_date() -> None:
    with pytest.raises(SC20ProtocolError):
        protocol.parse_clock({"year": 2026, "month": 13, "day": 40})


# --- echoes and framing ------------------------------------------------------------------


def test_frames_from_user_are_echoes() -> None:
    """The device broadcasts client frames back; acting on them would be a feedback loop."""
    assert protocol.is_echo({"title": "CCV", "from": "USER"}) is True
    assert protocol.is_echo({"title": "CCV", "from": "AA:BB:CC:DD:EE:FF"}) is False


def test_iter_frames_accepts_both_shapes() -> None:
    """The device sends a bare object normally, but arrays in its connect burst."""
    assert protocol.iter_frames({"title": "CCV"}) == [{"title": "CCV"}]
    assert protocol.iter_frames([{"title": "CCV"}, {"title": "CLOCK"}]) == [
        {"title": "CCV"},
        {"title": "CLOCK"},
    ]
    with pytest.raises(SC20ProtocolError):
        protocol.iter_frames("not a frame")


# --- daycycle validation -----------------------------------------------------------------


def _rows_to_daycycle(rows: list[list[int]]) -> Daycycle:
    return Daycycle(tuple(Setpoint(minute=r[0], values=tuple(r[1:])) for r in rows))


def test_daycycle_accepts_a_valid_programme() -> None:
    protocol.validate_daycycle(
        _rows_to_daycycle([[0, 0, 0, 0], [480, 90, 90, 90], [1440, 0, 0, 0]])
    )


def test_daycycle_rejects_more_than_thirty_setpoints() -> None:
    rows = [[0, 0, 0, 0]]
    rows += [[minute, 50, 50, 50] for minute in range(10, 310, 10)]
    rows += [[1440, 0, 0, 0]]
    assert len(rows) == 32
    with pytest.raises(SC20ValidationError, match="at most 30"):
        protocol.validate_daycycle(_rows_to_daycycle(rows))


def test_daycycle_rejects_unsorted_setpoints() -> None:
    with pytest.raises(SC20ValidationError, match="sorted"):
        protocol.validate_daycycle(
            _rows_to_daycycle(
                [[0, 0, 0, 0], [900, 50, 50, 50], [480, 90, 90, 90], [1440, 0, 0, 0]]
            )
        )


def test_daycycle_rejects_duplicate_times() -> None:
    with pytest.raises(SC20ValidationError, match="distinct"):
        protocol.validate_daycycle(
            _rows_to_daycycle(
                [[0, 0, 0, 0], [480, 50, 50, 50], [480, 90, 90, 90], [1440, 0, 0, 0]]
            )
        )


def test_daycycle_requires_both_anchors() -> None:
    with pytest.raises(SC20ValidationError, match="first setpoint"):
        protocol.validate_daycycle(_rows_to_daycycle([[10, 0, 0, 0], [1440, 0, 0, 0]]))
    with pytest.raises(SC20ValidationError, match="last setpoint"):
        protocol.validate_daycycle(_rows_to_daycycle([[0, 0, 0, 0], [1400, 0, 0, 0]]))


def test_setpoint_rejects_out_of_range_values() -> None:
    with pytest.raises(SC20ValidationError):
        Setpoint(minute=0, values=(101, 0, 0))
    with pytest.raises(SC20ValidationError):
        Setpoint(minute=0, values=(-1, 0, 0))
    with pytest.raises(SC20ValidationError):
        Setpoint(minute=1441, values=(0, 0, 0))


def test_setpoint_rejects_the_wrong_channel_count() -> None:
    with pytest.raises(SC20ValidationError, match="expected 3"):
        Setpoint(minute=0, values=(0, 0))


def test_build_set_daycycle_validates_before_sending() -> None:
    """A bad schedule must never reach the device: writes are unacknowledged and final."""
    with pytest.raises(SC20ValidationError):
        protocol.build_set_daycycle(_rows_to_daycycle([[0, 0, 0, 0], [1400, 0, 0, 0]]))


def test_daycycle_drops_padding_rows_on_receive() -> None:
    """Rows at minute 0 after the first are padding, not setpoints."""
    daycycle = protocol.parse_daycycle(
        {
            "configuration": [
                [0, 0, 0, 0],
                [480, 90, 90, 90],
                [1440, 0, 0, 0],
                [0, 0, 0, 0],
                [0, 0, 0, 0],
            ]
        }
    )
    assert [p.minute for p in daycycle.setpoints] == [0, 480, 1440]


def test_daycycle_truncates_extra_channels() -> None:
    """Other products in this family have six channels; the SC20 has three."""
    daycycle = protocol.parse_daycycle(
        {"configuration": [[0, 1, 2, 3, 4, 5, 6], [1440, 1, 2, 3, 4, 5, 6]]}
    )
    assert daycycle.setpoints[0].values == (1, 2, 3)


# --- interpolation -----------------------------------------------------------------------


def test_level_at_interpolates_linearly() -> None:
    daycycle = _rows_to_daycycle([[0, 0, 0, 0], [100, 100, 50, 0], [1440, 0, 0, 0]])
    assert daycycle.level_at(0).values == (0, 0, 0)
    assert daycycle.level_at(50).values == (50, 25, 0)
    assert daycycle.level_at(100).values == (100, 50, 0)


def test_level_at_matches_the_real_schedule_at_midday() -> None:
    """The schedule this unit holds, checked against what the device reported live."""
    daycycle = _rows_to_daycycle(
        [
            [0, 0, 0, 0],
            [360, 0, 0, 0],
            [480, 90, 90, 90],
            [1200, 90, 90, 90],
            [1320, 0, 0, 0],
            [1440, 0, 0, 0],
        ]
    )
    assert daycycle.level_at(10 * 60 + 30).values == (90, 90, 90)
    assert daycycle.level_at(420).values == (45, 45, 45)  # halfway up the sunrise ramp


# --- .scen files -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "freshwater-1-daycycle.scen",
        "freshwater-2-daycycle.scen",
        "freshwater-3-daycycle.scen",
    ],
)
def test_vendor_scenario_files_round_trip_byte_identically(filename: str) -> None:
    """Parsing and re-encoding a vendor file must reproduce it exactly.

    These are the files the format was decoded from, so this is the closest thing to a
    conformance test the format has.
    """
    original = (SCENARIO_DIR / filename).read_text().strip()
    daycycle = protocol.parse_scen_file(original)
    assert protocol.encode_scen_file(daycycle) == original


def test_scen_file_parses_into_expected_setpoints() -> None:
    daycycle = protocol.parse_scen_file(
        "[[0,0,0,0],[420,0,0,0],[480,100,100,100],[1080,100,100,100],[1140,0,0,0],[1440,0,0,0]]"
    )
    assert len(daycycle.setpoints) == 6
    assert daycycle.setpoints[2] == Setpoint(minute=480, values=(100, 100, 100))


def test_scen_file_rejects_rubbish() -> None:
    with pytest.raises(SC20ProtocolError):
        protocol.parse_scen_file("this is not JSON")


# --- DSCRPTN -----------------------------------------------------------------------------


def test_description_round_trips_through_the_vendor_string_format() -> None:
    original = (
        "confId:-1;expMode:false;start:360;end:1320;sunrise:120;sunset:120;"
        "intensity:90;individual:false;intensities:85,85,85"
    )
    parsed = protocol.parse_description({"description": original})
    assert parsed == DaycycleDescription(
        conf_id=-1,
        expert_mode=False,
        start=360,
        end=1320,
        sunrise=120,
        sunset=120,
        intensity=90,
        individual=False,
        intensities=(85, 85, 85),
    )
    assert protocol.encode_description(parsed) == original


def test_description_key_order_is_fixed() -> None:
    """The vendor parser walks the string positionally, so order is part of the format."""
    encoded = protocol.encode_description(DaycycleDescription())
    keys = [part.split(":")[0] for part in encoded.split(";")]
    assert keys == [
        "confId",
        "expMode",
        "start",
        "end",
        "sunrise",
        "sunset",
        "intensity",
        "individual",
        "intensities",
    ]


# --- misc parsing ------------------------------------------------------------------------


def test_power_arrives_as_a_string() -> None:
    """Observed on hardware: USRDTA.power is "17", not 17."""
    info = protocol.parse_device_info({"name": "SC20_1", "power": "17", "revision": [23, 15]})
    assert info.power == "17"


def test_revision_is_formatted_the_way_the_vendor_app_shows_it() -> None:
    info = protocol.parse_device_info({"revision": [23, 15]})
    assert info.firmware_version == "02.3"
    assert info.webapp_version == "01.5"


def test_channel_values_ignores_extra_channels() -> None:
    values = protocol.parse_channel_values({"currentValues": [90, 80, 70, 60, 50]})
    assert values.values == (90, 80, 70)


def test_channel_values_rejects_too_few() -> None:
    with pytest.raises(SC20ProtocolError):
        protocol.parse_channel_values({"currentValues": [90, 80]})


def test_moon_window_may_wrap_past_midnight() -> None:
    """This unit holds 22:00 to 06:00, so start > end is normal."""
    moon = protocol.parse_moon(
        {
            "maxmoonlight": 30,
            "minmoonlight": 2,
            "moonlightActive": 0,
            "moonlightCycle": 1,
            "color": "b",
            "moonStart": 1320,
            "moonEnd": 360,
        }
    )
    assert moon.start == 1320
    assert moon.end == 360
    assert moon.active is False
    assert moon.cycle is True


def test_server_log_scrapes_the_real_page() -> None:
    html = (
        "<body> <h1> Server Log </h1><br>Server Uptime  :   6601 minutes<br>"
        "Server Heap  :   26848<br><br>Operating Hours = 951240<br><br>"
        "Firmware Version: 23<br>Webapp Version: 23</body>"
    )
    log = protocol.parse_server_log(html)
    assert log.uptime_minutes == 6601
    assert log.free_heap == 26848
    assert log.operating_hours == 951240
    assert log.firmware_version == 23


def test_server_log_tolerates_a_missing_field() -> None:
    log = protocol.parse_server_log("<body>Server Heap  :   100</body>")
    assert log.free_heap == 100
    assert log.uptime_minutes is None


# --- master brightness scaling -----------------------------------------------------------


def test_scaling_preserves_the_channel_mix() -> None:
    """Changing overall brightness must not change the colour."""
    assert ChannelValues((100, 50, 0)).scaled_to(50).values == (50, 25, 0)
    assert ChannelValues((80, 40, 20)).scaled_to(100).values == (100, 50, 25)


def test_scaling_from_off_lights_every_channel() -> None:
    """With everything at zero there is no ratio to preserve."""
    assert ChannelValues((0, 0, 0)).scaled_to(60).values == (60, 60, 60)


def test_build_set_values_puts_percentages_on_the_wire() -> None:
    frame = protocol.build_set_values(ChannelValues((90, 80, 70)))
    assert frame["title"] == "CCV-SL"
    assert frame["currentValues"] == [90, 80, 70]
    assert frame["to"] == "ALL-LIGHTS"
    assert frame["from"] == "USER"


def test_mode_frames() -> None:
    assert protocol.build_set_mode(manual=True)["title"] == "MAN_MODE"
    assert protocol.build_set_mode(manual=False)["title"] == "DAYCL_MODE"


def test_every_outbound_frame_is_stamped_from_user() -> None:
    """Without this the device would not route the frame, and our own echo filter breaks."""
    frames = [
        protocol.build_request("GET_CLOCK"),
        protocol.build_set_mode(manual=True),
        protocol.build_set_values(ChannelValues((1, 2, 3))),
        protocol.build_set_clock(datetime(2026, 1, 1)),
        protocol.build_pause_acclimation(True),
        protocol.build_preview_curve(100, 0, 1440),
    ]
    for frame in frames:
        assert frame["from"] == "USER", frame["title"]
        assert json.dumps(frame)  # must be serialisable
