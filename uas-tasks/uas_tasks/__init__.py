"""
uas_tasks — DJI flight log ingestion into EarthRanger via Ecoscope Platform.

Tasks prefixed set_ expose typed parameters to the Desktop config form.
ingest_flights is the main processing task; it runs the full per-file loop
internally and returns an IngestResult bundle. Accessor and stat tasks
downstream pull individual components from that bundle.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
from pydantic import Field
from pydantic.json_schema import WithJsonSchema
from shapely.geometry import LineString
from wt_registry import register

# ---------------------------------------------------------------------------
# Per-flight track colour palette (RGBA uint8) — cycles if >10 flights
# ---------------------------------------------------------------------------

_TRACK_PALETTE = [
    [228,  26,  28, 255],  # red
    [ 55, 126, 184, 255],  # blue
    [ 77, 175,  74, 255],  # green
    [152,  78, 163, 255],  # purple
    [255, 127,   0, 255],  # orange
    [ 23, 190, 207, 255],  # cyan
    [247, 129, 191, 255],  # pink
    [188, 189,  34, 255],  # yellow-green
    [140,  86,  75, 255],  # brown
    [ 31, 119, 180, 255],  # dark blue
]

# ---------------------------------------------------------------------------
# Type aliases (follow hex-tasks conventions)
# ---------------------------------------------------------------------------

_GDF = Annotated[Any, WithJsonSchema({"type": "ecoscope.platform.annotations.DataFrame"})]

# ---------------------------------------------------------------------------
# Expected columns in the per-file status DataFrame
# ---------------------------------------------------------------------------

_RESULTS_COLUMNS = [
    "file",
    "aircraft_serial",
    "takeoff_utc",
    "flight_time_min",
    "battery_pct_takeoff",
    "battery_pct_landing",
    "battery_serial",
    "max_alt_agl_m",
    "max_speed_ms",
    "max_dist_m",
    "total_distance_m",
    "firmware",
    "status",
    "error",
]

# ---------------------------------------------------------------------------
# Result bundle — returned by ingest_flights, consumed by accessor tasks
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Holds all outputs of the ingestion loop. Not serialised by the SDK."""

    track_gdf: gpd.GeoDataFrame  # LineString per flight, for the map
    results_df: pd.DataFrame     # one row per .txt file, for the status table
    kml_paths: list              # absolute paths to persisted KML files
    n_ingested: int = 0
    n_skipped: int = 0
    n_failed: int = 0
    total_flight_seconds: float = 0.0
    total_distance_m: float = 0.0
    n_aircraft: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_results_dir(root_path: str) -> Path:
    """Convert a file:// URL (or plain path string) to an absolute local Path."""
    if root_path.startswith("file://"):
        url_path = urlparse(root_path).path
        # Windows: /C:/Users/... → C:/Users/...
        if url_path.startswith("/") and len(url_path) > 2 and url_path[2] == ":":
            url_path = url_path[1:]
        return Path(url_path)
    return Path(root_path)


def _empty_track_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "aircraft_serial": pd.Series(dtype=str),
            "takeoff_utc": pd.Series(dtype=str),
            "track_color": pd.Series(dtype=object),
        },
        geometry=gpd.GeoSeries([], crs="EPSG:4326"),
    )


def _parse_dt(dt_str: str) -> datetime:
    """Parse an ISO 8601 UTC string (with Z or +00:00 suffix) to a timezone-aware datetime."""
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def _datetime_from_filename(filepath: Path) -> datetime | None:
    """Extract datetime from DJI filename: DJIFlightRecord_YYYY-MM-DD_[HH-MM-SS].txt.
    The RC Pro records local device time; we store it as UTC (caller notes the caveat).
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})_\[(\d{2})-(\d{2})-(\d{2})\]", filepath.stem)
    if not m:
        return None
    try:
        y, mo, d = (int(x) for x in m.group(1).split("-"))
        h, mi, s = int(m.group(2)), int(m.group(3)), int(m.group(4))
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except Exception:
        return None


def _in_flight_window(dt_str: str, window_start: datetime, window_end: datetime) -> bool:
    """Return True if dt_str parses to a datetime within the flight window (±margin already applied)."""
    try:
        return window_start <= _parse_dt(dt_str) <= window_end
    except Exception:
        return False


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2.0 * R * atan2(sqrt(a), sqrt(1.0 - a))


def _write_kml(
    kml_text: str,
    aircraft_serial: str,
    takeoff_dt: datetime,
    results_dir: Path,
    kml_paths: list,
) -> None:
    """Persist a KML string to results_dir and append its path to kml_paths."""
    fname = f"{aircraft_serial}_{takeoff_dt.strftime('%Y%m%dT%H%M%SZ')}.kml"
    kml_path = results_dir / fname
    kml_path.write_text(kml_text, encoding="utf-8")
    kml_paths.append(str(kml_path))


# ---------------------------------------------------------------------------
# Form-field tasks — each exposes one config section to the Desktop form
# ---------------------------------------------------------------------------


@register()
def set_dji_api_key(
    dji_api_key: Annotated[
        str,
        Field(
            title="DJI API Key",
            description=(
                "Your DJI developer API key (also called 'App Key' or 'SDK Key'). "
                "Required to decrypt DJI .txt flight logs using firmware v13+. "
                "Find it at developer.dji.com → your apps → app detail page → 'SDK key'. "
                "The key is used once per file to fetch the decryption keychain from DJI; "
                "the keychain is cached, so DJI connectivity is only needed on first decrypt."
            ),
        ),
    ],
) -> str:
    return dji_api_key


@register()
def set_input_folder(
    folder_path: Annotated[
        str,
        Field(
            title="Flight Logs Folder",
            description=(
                "Full path to the folder containing DJI .txt flight records exported via USB from your DJI controller. "
                "All .txt files in this folder will be processed. "
                "On Windows use a path like C:\\Users\\you\\Documents\\FlightLogs. "
                "On the Desktop app the folder must be accessible from the machine running the workflow."
            ),
        ),
    ],
) -> str:
    return folder_path


@register()
def set_event_type_name(
    event_type_name: Annotated[
        str,
        Field(
            title="Flight Folio Event Type",
            description=(
                "The EarthRanger event type slug for UAS Flight Folio records. "
                "This event type must already exist in your ER instance before running the workflow. "
                "The slug is the lowercase-underscore identifier shown in the ER admin panel "
                "(Admin → Event Types → slug column), not the display name. "
                "Example: 'uas_flight_folio'."
            ),
            default="uas_flight_folio",
        ),
    ] = "uas_flight_folio",
) -> str:
    return event_type_name


@register()
def set_aircraft_identity(
    registration: Annotated[
        str,
        Field(
            title="Aircraft Registration",
            description=(
                "Legal registration number as it appears on the airframe (e.g. ZT-001407). "
                "Stored on the EarthRanger Subject as additional metadata. "
                "Leave blank if the aircraft is not registered."
            ),
            default="",
        ),
    ] = "",
    subject_type: Annotated[
        str,
        Field(
            title="Subject Type",
            description=(
                "EarthRanger subject type slug for this aircraft. "
                "Find it in ER under Admin → Subject Types. "
                "Must already exist in your ER instance. Example: 'aircraft'."
            ),
            default="aircraft",
        ),
    ] = "aircraft",
    subject_subtype: Annotated[
        str,
        Field(
            title="Subject Subtype",
            description=(
                "EarthRanger subject subtype slug for this aircraft. "
                "Find it in ER under Admin → Subject Types → (your type) → Subtypes. "
                "Must already exist in your ER instance. Example: 'drone_quadcopter'."
            ),
            default="uas",
        ),
    ] = "uas",
    source_type: Annotated[
        str,
        Field(
            title="Source Type",
            description=(
                "EarthRanger source type slug for GPS tracks from your DJI aircraft. "
                "Find it in ER under Admin → Source Types. "
                "Must already exist in your ER instance. Example: 'tracking-device'."
            ),
            default="djirc",
        ),
    ] = "djirc",
) -> dict:
    """Bundle aircraft identity config into a single dict for ingest_flights."""
    return {
        "registration": registration,
        "subject_type": subject_type,
        "subject_subtype": subject_subtype,
        "source_type": source_type,
    }


@register()
def set_decimation_rate(
    rate_hz: Annotated[
        int,
        Field(
            title="Track Decimation Rate (Hz)",
            description=(
                "GPS fixes per second to post to EarthRanger. "
                "DJI logs at ~10 Hz (~18 000 fixes per 30-min flight). "
                "1 Hz (default) gives ~1 800 fixes — smooth tracks in ER with a 10× data reduction. "
                "Increase only if you need sub-second track fidelity. "
                "Performance envelope stats (max altitude, speed, distance) are always "
                "computed on full-resolution data before decimation."
            ),
            default=1,
            ge=1,
            le=10,
        ),
    ] = 1,
) -> int:
    return rate_hz


# ---------------------------------------------------------------------------
# Main ingestion task
# ---------------------------------------------------------------------------


@register()
def ingest_flights(
    client: Any,
    dji_api_key: str,
    input_folder: str,
    event_type_name: str,
    aircraft_identity: Any,
    decimation_rate: int,
    root_path: str,
) -> Any:
    """
    Iterate DJI .txt flight records in input_folder, decrypt each, post track
    observations and a UAS Flight Folio event to EarthRanger, persist one KML
    per flight, and return an IngestResult bundle for the dashboard tasks.

    Per-file exceptions are caught and recorded as 'failed' rows so that one
    corrupt or undecryptable file does not abort the rest of the batch.
    """
    from ecoscope.platform.connections import EarthRangerConnection
    from uas_tasks._binary import get_binary_path

    if isinstance(client, str):
        client = EarthRangerConnection.client_from_named_connection(client)

    results_dir = _resolve_results_dir(root_path)
    results_dir.mkdir(parents=True, exist_ok=True)

    folder = Path(input_folder)
    if not folder.exists():
        row = {col: None for col in _RESULTS_COLUMNS}
        row.update({"file": str(folder), "status": "failed",
                     "error": f"Folder not found: {folder}"})
        return IngestResult(
            track_gdf=_empty_track_gdf(),
            results_df=pd.DataFrame([row], columns=_RESULTS_COLUMNS),
            kml_paths=[],
            n_failed=1,
        )

    binary = get_binary_path()

    # Look up event type UUID once — each org's ER has a different UUID for their
    # copy of the event type, so we resolve it from the slug at runtime.
    event_types_df = client.get_event_types()
    et_match = event_types_df[event_types_df["value"] == event_type_name].drop_duplicates("id")
    if et_match.empty:
        raise ValueError(
            f"Event type '{event_type_name}' not found in EarthRanger. "
            "Create it in ER Admin → Event Types using the EFE v2 template in the "
            f"repository README, with slug set to '{event_type_name}'."
        )
    event_type_uuid = str(et_match.iloc[0]["id"])

    txt_files = sorted(folder.glob("*.txt"))

    all_track_rows: list[dict] = []
    rows: list[dict] = []
    kml_paths: list[str] = []
    n_ingested = n_skipped = n_failed = 0
    total_flight_seconds = 0.0
    total_distance_m = 0.0

    # Ensure the DJI source provider exists — idempotent (ER returns error if duplicate, we ignore it).
    _DJI_PROVIDER_KEY = "dji_rc_pro"
    try:
        client.post_sourceproviders(provider_key=_DJI_PROVIDER_KEY, display_name="DJI RC Pro")
    except Exception:
        pass

    # Cache subject/source IDs per aircraft_serial to avoid redundant ER lookups
    # across multiple log files from the same aircraft.
    _subject_cache: dict[str, str] = {}
    _source_cache: dict[str, str] = {}

    for filepath in txt_files:
        row: dict = {col: None for col in _RESULTS_COLUMNS}
        row["file"] = filepath.name

        try:
            # ------------------------------------------------------------------
            # Step 2: decrypt + parse via dji-log (JSON to stdout, KML to tmp file)
            # ------------------------------------------------------------------
            with tempfile.TemporaryDirectory() as tmpdir:
                kml_tmp = Path(tmpdir) / "flight.kml"
                result = subprocess.run(
                    [binary, str(filepath),
                     "--api-key", dji_api_key,
                     "--kml", str(kml_tmp)],
                    capture_output=True, text=True, check=True,
                    timeout=120,
                )
                flight_data = json.loads(result.stdout)
                kml_text = kml_tmp.read_text(encoding="utf-8") if kml_tmp.exists() else ""

            # ------------------------------------------------------------------
            # Step 3: extract fields from parsed JSON
            # ------------------------------------------------------------------
            log_details = flight_data["details"]
            frames = flight_data["frames"]

            if not frames:
                raise ValueError("No frames decoded from log file.")

            aircraft_serial = log_details["aircraftSn"]
            if not aircraft_serial:
                raise ValueError("Aircraft serial number missing from log file.")

            # Battery serial is in the recover record of the first frame
            battery_serial = frames[0]["recover"]["batterySn"]

            # Firmware version is not recorded in DJI logs; use app version as proxy
            firmware = log_details.get("appVersion", "")

            # Takeoff = first frame where the drone is airborne with motors on
            takeoff_idx = next(
                (i for i, f in enumerate(frames)
                 if not f["osd"]["isOnGround"] and f["osd"]["isMotorOn"]),
                0,
            )
            # Landing = last such frame (search reversed list)
            landing_idx = len(frames) - 1 - next(
                (i for i, f in enumerate(reversed(frames))
                 if not f["osd"]["isOnGround"] and f["osd"]["isMotorOn"]),
                0,
            )

            takeoff_dt = _parse_dt(frames[takeoff_idx]["custom"]["dateTime"])
            landing_dt = _parse_dt(frames[landing_idx]["custom"]["dateTime"])

            # Guard: epoch/pre-GPS-lock timestamps on the takeoff frame itself.
            # DJI drones didn't exist before 2015; any earlier year is garbage.
            _MIN_YEAR = 2015
            if takeoff_dt.year < _MIN_YEAR:
                # Scan forward through airborne frames for a sane timestamp.
                for f in frames[takeoff_idx:]:
                    try:
                        dt = _parse_dt(f["custom"]["dateTime"])
                        if dt.year >= _MIN_YEAR and not f["osd"]["isOnGround"]:
                            takeoff_dt = dt
                            break
                    except Exception:
                        continue
            if takeoff_dt.year < _MIN_YEAR:
                # All frame timestamps are corrupt — fall back to the filename date/time.
                # The RC Pro uses local device time; we store it as-is (labelled UTC).
                fallback = _datetime_from_filename(filepath)
                if fallback is not None:
                    takeoff_dt = fallback
            if landing_dt.year < _MIN_YEAR:
                landing_dt = takeoff_dt

            # Flight window used to reject frames with garbage timestamps (near-zero
            # GPS coordinates that passed the != 0.0 filter but are pre-GPS-lock noise).
            _win_margin = timedelta(minutes=5)
            _win_start = takeoff_dt - _win_margin
            _win_end = landing_dt + _win_margin

            battery_pct_takeoff = int(frames[takeoff_idx]["battery"]["chargeLevel"])
            battery_pct_landing = int(frames[landing_idx]["battery"]["chargeLevel"])

            # Reference point for max_dist_m and event location.
            # Use the drone's OSD position at the takeoff frame (actual GPS, always in
            # valid decimal degrees). Fall back to home.* only if OSD is zero, and
            # validate that home.* is within WGS-84 range (some firmware versions store
            # home coordinates in a non-standard unit that looks like ~45836623).
            ref_lat = frames[takeoff_idx]["osd"]["latitude"]
            ref_lon = frames[takeoff_idx]["osd"]["longitude"]
            if ref_lat == 0.0 or ref_lon == 0.0:
                for f in frames:
                    hlat = f["home"]["latitude"]
                    hlon = f["home"]["longitude"]
                    if hlat != 0.0 and hlon != 0.0 and abs(hlat) <= 90.0 and abs(hlon) <= 180.0:
                        ref_lat, ref_lon = hlat, hlon
                        break

            # ------------------------------------------------------------------
            # Step 4: performance envelope (parser computes most values for us)
            # ------------------------------------------------------------------
            flight_time_s = float(log_details["totalTime"])
            max_alt_agl_m = float(log_details["maxHeight"])
            max_speed_ms = float(log_details["maxHorizontalSpeed"])

            # max_dist_m: max great-circle distance from the takeoff reference point.
            # Flight window filter rejects pre-GPS-lock frames with garbage timestamps.
            if ref_lat != 0.0 and ref_lon != 0.0:
                valid_pts = [
                    (f["osd"]["latitude"], f["osd"]["longitude"])
                    for f in frames
                    if f["osd"]["latitude"] != 0.0 and f["osd"]["longitude"] != 0.0
                    and _in_flight_window(f["custom"]["dateTime"], _win_start, _win_end)
                ]
                max_dist_m = max(
                    (_haversine_m(ref_lat, ref_lon, lat, lon) for lat, lon in valid_pts),
                    default=0.0,
                )
            else:
                max_dist_m = 0.0

            # ------------------------------------------------------------------
            # Step 5: decimate track to decimation_rate Hz
            # ------------------------------------------------------------------
            native_hz = len(frames) / max(flight_time_s, 1.0)
            step = max(1, round(native_hz / decimation_rate))
            frames_dec = frames[::step]

            # ------------------------------------------------------------------
            # Step 6: build LineString geometry for the map
            # ------------------------------------------------------------------
            airborne_dec = [
                f for f in frames_dec
                if f["osd"]["latitude"] != 0.0 and f["osd"]["longitude"] != 0.0
                and not f["osd"]["isOnGround"]
            ]
            if len(airborne_dec) >= 2:
                line = LineString(
                    [(f["osd"]["longitude"], f["osd"]["latitude"]) for f in airborne_dec]
                )
                all_track_rows.append({
                    "geometry": line,
                    "aircraft_serial": aircraft_serial,
                    "takeoff_utc": takeoff_dt.isoformat(),
                    "track_color": _TRACK_PALETTE[len(all_track_rows) % len(_TRACK_PALETTE)],
                })

            # ------------------------------------------------------------------
            # Step 7: get-or-create Subject (keyed on aircraft_serial)
            # ------------------------------------------------------------------
            if aircraft_serial not in _subject_cache:
                subjects_df = client.get_subjects(name=aircraft_serial, include_inactive=True)
                if subjects_df.empty:
                    new_sub = client.post_subject(
                        subject_name=aircraft_serial,
                        subject_type=aircraft_identity["subject_type"],
                        subject_subtype=aircraft_identity["subject_subtype"],
                        additional={"registration": aircraft_identity["registration"]},
                    )
                    _subject_cache[aircraft_serial] = str(new_sub.iloc[0]["id"])
                else:
                    _subject_cache[aircraft_serial] = str(subjects_df.iloc[0]["id"])
            subject_id = _subject_cache[aircraft_serial]

            # ------------------------------------------------------------------
            # Step 8: get-or-create Source (keyed on aircraft_serial as manufacturer_id)
            # ------------------------------------------------------------------
            if aircraft_serial not in _source_cache:
                sources_df = client.get_sources(manufacturer_id=aircraft_serial)
                if sources_df.empty:
                    new_src = client.post_source(
                        source_type=aircraft_identity["source_type"],
                        manufacturer_id=aircraft_serial,
                        model_name=log_details.get("aircraftName", "DJI Aircraft"),
                        provider=_DJI_PROVIDER_KEY,
                    )
                    source_id = str(new_src.iloc[0]["id"])
                    # Link this subject to the new source with an open-ended range
                    client.post_subjectsource(
                        subject_id=subject_id,
                        source_id=source_id,
                        lower_bound_assigned_range=datetime(2000, 1, 1, tzinfo=timezone.utc),
                        upper_bound_assigned_range=datetime(2099, 1, 1, tzinfo=timezone.utc),
                    )
                else:
                    source_id = str(sources_df.iloc[0]["id"])
                _source_cache[aircraft_serial] = source_id
            source_id = _source_cache[aircraft_serial]

            # ------------------------------------------------------------------
            # Step 9: idempotency check — query ER event ledger by event time.
            # We use a ±10s window around takeoff_dt rather than matching a
            # flight_key in event_details, because ER does not reliably return
            # event_details fields in list queries even with include_details=True.
            # Real flights are never within 10 s of each other.
            # ------------------------------------------------------------------
            flight_key = f"{aircraft_serial}_{takeoff_dt.strftime('%Y%m%dT%H%M%SZ')}"
            candidates = client.get_events(
                event_type=[event_type_uuid],
                since=(takeoff_dt - timedelta(seconds=10)).isoformat(),
                until=(takeoff_dt + timedelta(seconds=10)).isoformat(),
            )

            is_duplicate = False
            if not candidates.empty:
                for _, ev in candidates.iterrows():
                    try:
                        ev_time = _parse_dt(str(ev.get("time") or ""))
                        if abs((ev_time - takeoff_dt).total_seconds()) <= 10:
                            is_duplicate = True
                            break
                    except Exception:
                        continue

            if is_duplicate:
                # Already in ER — persist KML but skip all ER writes
                if kml_text:
                    _write_kml(kml_text, aircraft_serial, takeoff_dt, results_dir, kml_paths)
                row.update({
                    "aircraft_serial":     aircraft_serial,
                    "takeoff_utc":         takeoff_dt.isoformat(),
                    "flight_time_min":     round(flight_time_s / 60, 1),
                    "battery_pct_takeoff": battery_pct_takeoff,
                    "battery_pct_landing": battery_pct_landing,
                    "battery_serial":      battery_serial,
                    "max_alt_agl_m":       round(max_alt_agl_m, 1),
                    "max_speed_ms":        round(max_speed_ms, 1),
                    "max_dist_m":          round(max_dist_m, 1),
                    "total_distance_m":    round(float(log_details.get("totalDistance", 0.0)) * 1000, 1),
                    "firmware":            firmware,
                    "status":              "skipped",
                })
                n_skipped += 1

            else:
                # ------------------------------------------------------------------
                # Step 10: post decimated track observations to EarthRanger
                # ------------------------------------------------------------------
                airborne_all = [
                    f for f in frames_dec
                    if f["osd"]["latitude"] != 0.0 and f["osd"]["longitude"] != 0.0
                    and not f["osd"]["isOnGround"]
                    and _in_flight_window(f["custom"]["dateTime"], _win_start, _win_end)
                ]
                if airborne_all:
                    obs_gdf = gpd.GeoDataFrame(
                        {
                            "source": [source_id] * len(airborne_all),
                            "recorded_at": [f["custom"]["dateTime"] for f in airborne_all],
                            "device_status_properties": [
                                {"altitude": f["osd"]["height"]} for f in airborne_all
                            ],
                        },
                        geometry=gpd.points_from_xy(
                            [f["osd"]["longitude"] for f in airborne_all],
                            [f["osd"]["latitude"] for f in airborne_all],
                        ),
                        crs="EPSG:4326",
                    )
                    client.post_observations(obs_gdf)

                # ------------------------------------------------------------------
                # Step 11: post UAS Flight Folio event
                # ------------------------------------------------------------------
                event_location = (
                    {"latitude": ref_lat, "longitude": ref_lon}
                    if ref_lat != 0.0 else None
                )
                total_dist_m = round(float(log_details.get("totalDistance", 0.0)) * 1000, 1)
                client.post_event({
                    "event_type": event_type_name,
                    "time": takeoff_dt.isoformat(),
                    "location": event_location,
                    "event_details": {
                        "flight_key":            flight_key,
                        "aircraft_serial":       aircraft_serial,
                        "aircraft_registration": aircraft_identity["registration"],
                        "flight_date":           takeoff_dt.date().isoformat(),
                        "start_time_utc":        takeoff_dt.isoformat(),
                        "end_time_utc":          landing_dt.isoformat(),
                        "flight_time_min":       round(flight_time_s / 60, 1),
                        "battery_pct_takeoff":   battery_pct_takeoff,
                        "battery_pct_landing":   battery_pct_landing,
                        "battery_serial":        battery_serial,
                        "max_alt_agl_m":         round(max_alt_agl_m, 1),
                        "max_speed_ms":          round(max_speed_ms, 1),
                        "max_dist_m":            round(max_dist_m, 1),
                        "total_distance_m":      total_dist_m,
                        "home_lat":              ref_lat,
                        "home_lon":              ref_lon,
                        "firmware":              firmware,
                    },
                })

                # ------------------------------------------------------------------
                # Step 12: persist KML (full-resolution, from parser output)
                # ------------------------------------------------------------------
                if kml_text:
                    _write_kml(kml_text, aircraft_serial, takeoff_dt, results_dir, kml_paths)

                row.update({
                    "aircraft_serial":     aircraft_serial,
                    "takeoff_utc":         takeoff_dt.isoformat(),
                    "flight_time_min":     round(flight_time_s / 60, 1),
                    "battery_pct_takeoff": battery_pct_takeoff,
                    "battery_pct_landing": battery_pct_landing,
                    "battery_serial":      battery_serial,
                    "max_alt_agl_m":       round(max_alt_agl_m, 1),
                    "max_speed_ms":        round(max_speed_ms, 1),
                    "max_dist_m":          round(max_dist_m, 1),
                    "total_distance_m":    total_dist_m,
                    "firmware":            firmware,
                    "status":              "ingested",
                })
                n_ingested += 1
                total_flight_seconds += flight_time_s
                total_distance_m += float(log_details.get("totalDistance", 0.0)) * 1000

        except Exception as exc:
            row["status"] = "failed"
            row["error"] = f"{type(exc).__name__}: {exc}"
            n_failed += 1

        rows.append(row)

    # Build combined track GeoDataFrame
    if all_track_rows:
        track_gdf = gpd.GeoDataFrame(all_track_rows, geometry="geometry", crs="EPSG:4326")
    else:
        track_gdf = _empty_track_gdf()

    results_df = (
        pd.DataFrame(rows, columns=_RESULTS_COLUMNS)
        if rows
        else pd.DataFrame(columns=_RESULTS_COLUMNS)
    )

    ingested_mask = results_df["status"] == "ingested" if not results_df.empty else pd.Series([], dtype=bool)
    n_aircraft = int(results_df.loc[ingested_mask, "aircraft_serial"].nunique()) if not results_df.empty else 0

    return IngestResult(
        track_gdf=track_gdf,
        results_df=results_df,
        kml_paths=kml_paths,
        n_ingested=n_ingested,
        n_skipped=n_skipped,
        n_failed=n_failed,
        total_flight_seconds=total_flight_seconds,
        total_distance_m=total_distance_m,
        n_aircraft=n_aircraft,
    )


# ---------------------------------------------------------------------------
# Accessor tasks — pull individual components from the IngestResult bundle
# ---------------------------------------------------------------------------


@register()
def extract_track_gdf(ingest_result: Any) -> _GDF:
    """Return the combined flight-tracks GeoDataFrame (LineString per flight)."""
    return ingest_result.track_gdf


@register()
def extract_results_df(ingest_result: Any) -> _GDF:
    """Return the per-file status DataFrame (one row per .txt file processed)."""
    return ingest_result.results_df


@register()
def extract_kml_paths(ingest_result: Any) -> list:
    """Return the list of absolute paths to persisted KML files."""
    return ingest_result.kml_paths


# ---------------------------------------------------------------------------
# Stat tasks — scalar values for dashboard score-card widgets
# ---------------------------------------------------------------------------


@register()
def count_ingested(ingest_result: Any) -> int:
    """Number of flights successfully written to EarthRanger this run."""
    return ingest_result.n_ingested


@register()
def count_skipped(ingest_result: Any) -> int:
    """Number of flights skipped (duplicate — already present in EarthRanger)."""
    return ingest_result.n_skipped


@register()
def count_failed(ingest_result: Any) -> int:
    """Number of files that failed (decrypt error, parse error, ER write error, etc.)."""
    return ingest_result.n_failed


@register()
def format_total_flight_time(ingest_result: Any) -> str:
    """Total flight time across all ingested flights, formatted as H:MM."""
    total_s = ingest_result.total_flight_seconds
    if total_s <= 0:
        return "0:00"
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    return f"{h}:{m:02d}"


@register()
def format_total_distance(ingest_result: Any) -> str:
    """Total GPS distance flown across all ingested flights, formatted as X.X km."""
    km = ingest_result.total_distance_m / 1000.0
    return f"{km:.1f} km" if km > 0 else "0.0 km"


@register()
def count_aircraft(ingest_result: Any) -> int:
    """Number of unique aircraft serial numbers in this ingestion batch."""
    return ingest_result.n_aircraft
