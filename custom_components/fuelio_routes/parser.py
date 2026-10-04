"""Parsers for the files Fuelio's Trip Log uploads to Google Drive.

One trip is stored as up to four files sharing the trip's start time (epoch
milliseconds) in their name: ``route-<id>.data`` (zip with a CSV of GPS fixes),
``route-<id>.csv`` (the same CSV, not zipped), ``route-<id>.gpx`` and
``route-<id>.route`` (Google encoded polyline). Any subset may exist.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
import io
from itertools import pairwise
import math
import re
from typing import Any
import zipfile

from defusedxml import ElementTree

# Bump when parsing changes in a way that affects stored trip summaries.
PARSER_VERSION = 1

EXT_DATA = "data"
EXT_CSV = "csv"
EXT_GPX = "gpx"
EXT_ROUTE = "route"

FILE_NAME_RE = re.compile(r"^route-(\d{10,16})\.(data|csv|gpx|route)$")

MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_GPX_NAME_PREFIX = "Fuelio:"
_TZ_STEP_MS = 15 * 60 * 1000
_MAX_TZ_OFFSET_MS = 14 * 60 * 60 * 1000
_EARTH_RADIUS_M = 6371008.8

# (latitude, longitude, epoch milliseconds or None, speed in m/s or None)
type Point = tuple[float, float, int | None, float | None]


class ParseError(Exception):
    """Raised when a trip file cannot be parsed."""


@dataclass(slots=True)
class Trip:
    """A single recorded drive."""

    trip_id: str
    points: list[Point]
    source: str
    name: str | None = None
    start_name: str | None = None
    end_name: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def has_timestamps(self) -> bool:
        """Return True if the points carry their own timestamps."""
        return bool(self.points) and self.points[0][2] is not None

    def summary(self) -> dict[str, Any]:
        """Return the JSON-serializable description of the trip without points."""
        start = int(self.trip_id)
        end: int | None = None
        if self.has_timestamps:
            start = self.points[0][2] or start
            end = self.points[-1][2]
        speeds = [p[3] for p in self.points if p[3] is not None]
        return {
            "id": self.trip_id,
            "start": start,
            "end": end,
            "distance": round(track_length(self.points), 1),
            "max_speed": round(max(speeds), 2) if speeds else None,
            "name": self.name,
            "start_name": self.start_name,
            "end_name": self.end_name,
            "point_count": len(self.points),
            "has_timestamps": self.has_timestamps,
            "source": self.source,
        }


def parse_file_name(name: str) -> tuple[str, str] | None:
    """Split a Fuelio route file name into (trip id, extension)."""
    if match := FILE_NAME_RE.match(name):
        return match.group(1), match.group(2)
    return None


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two coordinates in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def track_length(points: list[Point]) -> float:
    """Return the length of a track in meters."""
    return sum(haversine(a[0], a[1], b[0], b[1]) for a, b in pairwise(points))


def _valid_coordinate(lat: float, lon: float) -> bool:
    return (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )


def parse_data(content: bytes) -> list[Point]:
    """Parse a ``.data`` file: a zip holding one CSV of GPS fixes."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            if not members:
                raise ParseError("empty archive")
            member = next(
                (i for i in members if i.filename.lower().endswith(".csv")), members[0]
            )
            if member.file_size > MAX_UNCOMPRESSED_BYTES:
                raise ParseError("archive member too large")
            return parse_csv(archive.read(member))
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as err:
        raise ParseError(f"invalid archive: {err}") from err


def parse_csv(content: bytes) -> list[Point]:
    """Parse a ``.csv`` file, or the CSV inside a ``.data`` file.

    Columns: epoch ms, latitude, longitude, meters since the previous fix,
    speed in m/s, bearing in degrees, and one undocumented value.
    """
    text = content.decode("utf-8", errors="replace")
    points: list[Point] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        try:
            timestamp = int(float(row[0]))
            lat = float(row[1])
            lon = float(row[2])
        except ValueError:
            continue
        if not _valid_coordinate(lat, lon):
            continue
        speed: float | None = None
        if len(row) > 4:
            try:
                speed = float(row[4])
            except ValueError:
                speed = None
            else:
                if not math.isfinite(speed) or speed < 0:
                    speed = None
        points.append((lat, lon, timestamp, speed))
    if not points:
        raise ParseError("no GPS fixes")
    points.sort(key=lambda p: p[2] or 0)
    return points


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: Any, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_gpx_time(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def parse_gpx(content: bytes) -> tuple[str | None, list[str], list[Point]]:
    """Parse a ``.gpx`` file into (trip name, waypoint names, track points)."""
    try:
        root = ElementTree.fromstring(content)
    except Exception as err:
        raise ParseError(f"invalid GPX: {err}") from err

    name: str | None = None
    waypoints: list[str] = []
    points: list[Point] = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag in ("metadata", "trk") and name is None:
            name = _child_text(element, "name")
        elif tag == "wpt":
            if wpt_name := _child_text(element, "name"):
                waypoints.append(wpt_name)
        elif tag == "trkpt":
            try:
                lat = float(element.attrib["lat"])
                lon = float(element.attrib["lon"])
            except (KeyError, ValueError):
                continue
            if not _valid_coordinate(lat, lon):
                continue
            points.append(
                (lat, lon, _parse_gpx_time(_child_text(element, "time")), None)
            )

    if any(p[2] is None for p in points):
        points = [(p[0], p[1], None, None) for p in points]
    if name and name.startswith(_GPX_NAME_PREFIX):
        name = name[len(_GPX_NAME_PREFIX) :].strip() or None
    return name, waypoints, points


def correct_gpx_times(trip_id: str, points: list[Point]) -> list[Point]:
    """Undo Fuelio writing local wall-clock time into GPX while labelling it UTC.

    The trip id is the true start time, so the difference to the first GPX
    point, rounded to a whole timezone step, is the offset that was baked in.
    """
    if not points or points[0][2] is None:
        return points
    offset = round((points[0][2] - int(trip_id)) / _TZ_STEP_MS) * _TZ_STEP_MS
    if offset == 0 or abs(offset) > _MAX_TZ_OFFSET_MS:
        return points
    return [(p[0], p[1], (p[2] or 0) - offset, p[3]) for p in points]


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a Google encoded polyline."""
    factor = 10**precision
    coordinates: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = 0
            result = 0
            while True:
                if index >= length:
                    raise ParseError("truncated polyline")
                value = ord(encoded[index]) - 63
                index += 1
                if value < 0 or value > 63:
                    raise ParseError("invalid polyline character")
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coordinates.append((lat / factor, lon / factor))
    return coordinates


def parse_route(content: bytes) -> list[Point]:
    """Parse a ``.route`` file: a bare encoded polyline without timestamps."""
    encoded = content.decode("ascii", errors="replace").strip()
    points: list[Point] = [
        (lat, lon, None, None)
        for lat, lon in decode_polyline(encoded)
        if _valid_coordinate(lat, lon)
    ]
    if not points:
        raise ParseError("empty polyline")
    return points


def build_trip(trip_id: str, files: dict[str, bytes]) -> Trip:
    """Combine the available files of one trip, preferring the richest source.

    Points come from ``.data``, else ``.csv``, else ``.gpx``, else ``.route``. The trip and
    place names only exist in the GPX file and are used whenever it is present.
    """
    errors: list[str] = []
    points: list[Point] = []
    source = ""
    name: str | None = None
    waypoints: list[str] = []

    if EXT_DATA in files:
        try:
            points = parse_data(files[EXT_DATA])
            source = EXT_DATA
        except ParseError as err:
            errors.append(f"{EXT_DATA}: {err}")

    if not points and EXT_CSV in files:
        try:
            points = parse_csv(files[EXT_CSV])
            source = EXT_CSV
        except ParseError as err:
            errors.append(f"{EXT_CSV}: {err}")

    if EXT_GPX in files:
        try:
            name, waypoints, gpx_points = parse_gpx(files[EXT_GPX])
        except ParseError as err:
            errors.append(f"{EXT_GPX}: {err}")
        else:
            if not points and gpx_points:
                points = correct_gpx_times(trip_id, gpx_points)
                source = EXT_GPX

    if not points and EXT_ROUTE in files:
        try:
            points = parse_route(files[EXT_ROUTE])
            source = EXT_ROUTE
        except ParseError as err:
            errors.append(f"{EXT_ROUTE}: {err}")

    if not points:
        raise ParseError("; ".join(errors) or "no usable files")
    return Trip(
        trip_id=trip_id,
        points=points,
        source=source,
        name=name,
        start_name=waypoints[0] if waypoints else None,
        end_name=waypoints[-1] if len(waypoints) > 1 else None,
        errors=errors,
    )
