"""Tests for the Fuelio file parsers."""

from __future__ import annotations

import io
import zipfile

import pytest

from custom_components.fuelio_routes.parser import (
    ParseError,
    Trip,
    build_trip,
    correct_gpx_times,
    decode_polyline,
    haversine,
    parse_csv,
    parse_data,
    parse_file_name,
    parse_gpx,
    parse_route,
)

from .conftest import (
    TRACK,
    TRIP_A,
    encode_polyline,
    make_csv,
    make_data,
    make_gpx,
    make_route,
)

TWO_HOURS = 7200000


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("route-1790867355823.gpx", ("1790867355823", "gpx")),
        ("route-1790867355823.data", ("1790867355823", "data")),
        ("route-1790867355823.route", ("1790867355823", "route")),
        ("route-1790867355823.csv", ("1790867355823", "csv")),
        ("route-1790867355823.txt", None),
        ("route-abc.gpx", None),
        ("../route-1790867355823.gpx", None),
        ("vehicle-1-sync.csv", None),
    ],
)
def test_parse_file_name(name: str, expected: tuple[str, str] | None) -> None:
    """Only Fuelio route files are recognized."""
    assert parse_file_name(name) == expected


def test_haversine() -> None:
    """One degree of latitude is about 111 km."""
    assert haversine(47.0, 19.0, 48.0, 19.0) == pytest.approx(111195, rel=1e-3)
    assert haversine(47.0, 19.0, 47.0, 19.0) == 0


def test_parse_data() -> None:
    """The zipped CSV yields timestamped points with speed."""
    points = parse_data(make_data(TRIP_A))
    assert len(points) == len(TRACK)
    assert points[0] == (47.0, 19.0, TRIP_A, 5.0)
    assert points[-1][2] == TRIP_A + 6000


def test_parse_data_skips_bad_rows_and_sorts() -> None:
    """Malformed rows are ignored and fixes are ordered by time."""
    text = "\n".join(
        [
            "2000,47.1,19.1,0,3.5,10,1",
            "garbage",
            "x,47.0,19.0",
            "3000,95.0,19.0",
            "1000,47.0,19.0",
            "4000,47.2,19.2,1,fast,10,1",
            "5000,47.3,19.3,1,-4,10,1",
            "6000,47.4,19.4,1,nan,10,1",
        ]
    )
    points = parse_data(_zip("r.csv", text))
    assert [p[2] for p in points] == [1000, 2000, 4000, 5000, 6000]
    assert [p[3] for p in points] == [None, 3.5, None, None, None]


@pytest.mark.parametrize(
    "content",
    [
        b"definitely not a zip",
        _zip("r.csv", "no,valid,rows\n"),
        _zip("folder/", ""),
    ],
)
def test_parse_data_invalid(content: bytes) -> None:
    """Unusable archives raise ParseError."""
    with pytest.raises(ParseError):
        parse_data(content)


def test_parse_data_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized archive members are refused before being read."""
    monkeypatch.setattr(
        "custom_components.fuelio_routes.parser.MAX_UNCOMPRESSED_BYTES", 10
    )
    with pytest.raises(ParseError, match="too large"):
        parse_data(make_data(TRIP_A))


def test_parse_gpx() -> None:
    """Name, waypoints and timestamped points are extracted."""
    name, waypoints, points = parse_gpx(make_gpx(TRIP_A))
    assert name == "Evening drive"
    assert waypoints == ["Start street", "End street"]
    assert points[0] == (47.0, 19.0, TRIP_A, None)
    assert len(points) == len(TRACK)


def test_parse_gpx_without_times_or_names() -> None:
    """Missing times make the whole track untimed; a bare prefix is no name."""
    name, waypoints, points = parse_gpx(
        make_gpx(TRIP_A, name="Fuelio:", waypoints=(), with_time=False)
    )
    assert name is None
    assert waypoints == []
    assert all(p[2] is None for p in points)


def test_parse_gpx_tolerates_bad_points() -> None:
    """Points with unusable coordinates or times are dropped or untimed."""
    content = b"""<gpx><trk><name>Plain</name><trkseg>
      <trkpt lat="47.0" lon="19.0"><time>2026-10-01T10:00:00Z</time></trkpt>
      <trkpt lat="abc" lon="19.0"/>
      <trkpt lon="19.0"/>
      <trkpt lat="91" lon="19.0"/>
      <trkpt lat="47.1" lon="19.1"><time>yesterday</time></trkpt>
      <trkpt lat="47.2" lon="19.2"><time>2026-10-01T10:00:04</time></trkpt>
    </trkseg></trk></gpx>"""
    name, _, points = parse_gpx(content)
    assert name == "Plain"
    assert points == [
        (47.0, 19.0, None, None),
        (47.1, 19.1, None, None),
        (47.2, 19.2, None, None),
    ]


def test_parse_gpx_invalid() -> None:
    """Broken XML raises ParseError."""
    with pytest.raises(ParseError):
        parse_gpx(b"<gpx><trk>")


def test_decode_polyline_known_value() -> None:
    """Decode the example from Google's documentation."""
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_polyline_round_trip() -> None:
    """The test encoder and the decoder agree."""
    assert decode_polyline(encode_polyline(TRACK)) == TRACK


@pytest.mark.parametrize("encoded", ["_p~iF~ps|", "_p~iF\x01", "_p~iF"])
def test_decode_polyline_invalid(encoded: str) -> None:
    """Truncated or corrupt polylines raise ParseError."""
    with pytest.raises(ParseError):
        decode_polyline(encoded)


def test_parse_route() -> None:
    """A route file yields untimed points."""
    assert parse_route(make_route() + b"\n") == [
        (lat, lon, None, None) for lat, lon in TRACK
    ]
    with pytest.raises(ParseError):
        parse_route(b"")


def test_correct_gpx_times() -> None:
    """A whole-timezone offset is removed, anything else is left alone."""
    shifted = [(47.0, 19.0, TRIP_A + TWO_HOURS + 7000, None)]
    assert correct_gpx_times(str(TRIP_A), shifted)[0][2] == TRIP_A + 7000
    correct = [(47.0, 19.0, TRIP_A + 7000, None)]
    assert correct_gpx_times(str(TRIP_A), correct) == correct
    far_off = [(47.0, 19.0, TRIP_A + 20 * 3600000, None)]
    assert correct_gpx_times(str(TRIP_A), far_off) == far_off
    untimed = [(47.0, 19.0, None, None)]
    assert correct_gpx_times(str(TRIP_A), untimed) == untimed
    assert correct_gpx_times(str(TRIP_A), []) == []


def test_build_trip_prefers_data_and_takes_names_from_gpx() -> None:
    """With all three files the CSV provides points and the GPX the names."""
    trip = build_trip(
        str(TRIP_A),
        {
            "data": make_data(TRIP_A + 5000),
            "gpx": make_gpx(TRIP_A + 5000 + TWO_HOURS),
            "route": make_route(),
        },
    )
    summary = trip.summary()
    assert trip.source == "data"
    assert summary["name"] == "Evening drive"
    assert summary["start_name"] == "Start street"
    assert summary["end_name"] == "End street"
    assert summary["start"] == TRIP_A + 5000
    assert summary["end"] == TRIP_A + 11000
    assert summary["max_speed"] == 8.0
    assert summary["has_timestamps"] is True
    assert summary["point_count"] == len(TRACK)
    assert summary["distance"] == pytest.approx(557.6, abs=1)


def test_parse_csv() -> None:
    """A plain CSV file holds the same rows as the one inside a .data file."""
    points = parse_csv(make_csv(TRIP_A))
    assert points == parse_data(make_data(TRIP_A))
    with pytest.raises(ParseError, match="no GPS fixes"):
        parse_csv(b"")


def test_build_trip_csv_only_and_priority() -> None:
    """A CSV-only trip is complete; with a .data file the CSV is not needed."""
    trip = build_trip(str(TRIP_A), {"csv": make_csv(TRIP_A + 5000)})
    assert trip.source == "csv"
    assert trip.summary()["start"] == TRIP_A + 5000
    assert trip.summary()["max_speed"] == 8.0

    trip = build_trip(
        str(TRIP_A), {"csv": make_csv(TRIP_A + 1000), "data": make_data(TRIP_A + 5000)}
    )
    assert trip.source == "data"

    trip = build_trip(
        str(TRIP_A),
        {
            "data": b"broken",
            "csv": make_csv(TRIP_A + 5000),
            "gpx": make_gpx(TRIP_A + 7200000),
        },
    )
    assert trip.source == "csv"
    assert trip.summary()["name"] == "Evening drive"
    assert len(trip.errors) == 1

    trip = build_trip(str(TRIP_A), {"csv": b"", "route": make_route()})
    assert trip.source == "route"


def test_build_trip_gpx_only_fixes_timezone() -> None:
    """GPX-only trips get their mislabelled local times converted to UTC."""
    trip = build_trip(str(TRIP_A), {"gpx": make_gpx(TRIP_A + 5000 + TWO_HOURS)})
    summary = trip.summary()
    assert trip.source == "gpx"
    assert summary["start"] == TRIP_A + 5000
    assert summary["max_speed"] is None


def test_build_trip_route_only() -> None:
    """A polyline-only trip starts at the time in its file name."""
    summary = build_trip(str(TRIP_A), {"route": make_route()}).summary()
    assert summary["source"] == "route"
    assert summary["start"] == TRIP_A
    assert summary["end"] is None
    assert summary["has_timestamps"] is False
    assert summary["name"] is None


def test_build_trip_falls_back_and_reports_errors() -> None:
    """Broken richer files are reported and the next source is used."""
    trip = build_trip(
        str(TRIP_A), {"data": b"broken", "gpx": b"<gpx", "route": make_route()}
    )
    assert trip.source == "route"
    assert len(trip.errors) == 2

    trip = build_trip(
        str(TRIP_A),
        {"gpx": make_gpx(TRIP_A, track=[], waypoints=("Only",)), "route": make_route()},
    )
    assert trip.source == "route"
    assert trip.summary()["start_name"] == "Only"
    assert trip.summary()["end_name"] is None


def test_build_trip_nothing_usable() -> None:
    """A trip without any readable file raises ParseError."""
    with pytest.raises(ParseError, match="no usable files"):
        build_trip(str(TRIP_A), {})
    with pytest.raises(ParseError, match="route"):
        build_trip(str(TRIP_A), {"route": b"\x01"})


def test_empty_trip_has_no_timestamps() -> None:
    """The timestamp check copes with a trip without points."""
    assert Trip(trip_id=str(TRIP_A), points=[], source="route").has_timestamps is False
