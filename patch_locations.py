#!/usr/bin/env python3
"""
patch_locations.py — Backfill GPS locations on UAS Flight Folio events that
were posted to EarthRanger without a location (event_location is null).

Root cause: at liftoff the drone's GPS is not always locked yet, so the
takeoff frame has lat/lon = 0.0. The original code fell back to home
coordinates, which can also be zero or carry firmware-specific garbage values.
Fix: scan forward through airborne frames for the first valid OSD position.

This script:
  1. Fetches all Flight Folio events from ER and filters to those without location
  2. Maps each flight_key to its source .txt filename via the backfill CSV
  3. Re-runs dji-log on each file to extract a GPS position using the 3-step fallback
  4. Patches the ER event location

Usage (run from inside the compiled workflow dir):
    cd ecoscope-workflows-uas-flight-logs-workflow
    export ER_PASSWORD="..."
    pixi run python ../patch_locations.py \\
        --er-server https://<your-er-instance>.pamdas.org \\
        --er-user <your-er-username> \\
        --api-key <DJI_API_KEY> \\
        --logs-folder "C:\\\\Users\\\\apute\\\\Documents\\\\DJILogs" \\
        --csv "/mnt/c/Users/apute/Downloads/DJItoERLogsPrep - first_csv_by_cc.csv" \\
        [--execute]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from pathlib import Path

EVENT_TYPE_SLUG = "uas_flight_folio"


def _valid_wgs84(lat: float, lon: float) -> bool:
    return lat != 0.0 and lon != 0.0 and abs(lat) <= 90.0 and abs(lon) <= 180.0


def _extract_location(filepath: Path, binary: str, api_key: str) -> tuple[float, float] | None:
    """Run dji-log on a file and return the first valid GPS position using
    the 3-step fallback: takeoff OSD → first airborne OSD → home coords."""
    try:
        result = subprocess.run(
            [binary, str(filepath), "--api-key", api_key],
            capture_output=True, encoding="utf-8", timeout=120, check=True,
        )
        data = json.loads(result.stdout)
    except Exception as e:
        return None

    frames = data.get("frames", [])
    if not frames:
        return None

    # Find takeoff and landing indices
    takeoff_idx = next(
        (i for i, f in enumerate(frames)
         if not f["osd"]["isOnGround"] and f["osd"]["isMotorOn"]),
        0,
    )
    landing_idx = len(frames) - 1 - next(
        (i for i, f in enumerate(reversed(frames))
         if not f["osd"]["isOnGround"] and f["osd"]["isMotorOn"]),
        0,
    )

    # Step 1: takeoff frame OSD
    lat = frames[takeoff_idx]["osd"]["latitude"]
    lon = frames[takeoff_idx]["osd"]["longitude"]
    if _valid_wgs84(lat, lon):
        return (lat, lon)

    # Step 2: first valid OSD in any airborne frame
    for f in frames[takeoff_idx:landing_idx + 1]:
        lat = f["osd"]["latitude"]
        lon = f["osd"]["longitude"]
        if _valid_wgs84(lat, lon):
            return (lat, lon)

    # Step 3: home coordinates
    for f in frames:
        hlat = f["home"]["latitude"]
        hlon = f["home"]["longitude"]
        if _valid_wgs84(hlat, hlon):
            return (hlat, hlon)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--er-server",   required=True)
    parser.add_argument("--er-user",     required=True)
    parser.add_argument("--api-key",     required=True, help="DJI developer API key")
    parser.add_argument("--logs-folder", required=True, help="Path to folder containing .txt log files")
    parser.add_argument("--csv",         required=True, help="Path to first_csv_by_cc.csv (flight_key → filename mapping)")
    parser.add_argument("--execute",     action="store_true", help="Write to ER (default: dry run)")
    args = parser.parse_args()

    dry_run = not args.execute
    print(f"\n{'*** DRY RUN ***' if dry_run else '*** LIVE RUN — writing to EarthRanger ***'}\n")

    er_password = os.environ.get("ER_PASSWORD")
    if not er_password:
        import getpass
        er_password = getpass.getpass(f"ER password for {args.er_user}: ")

    # Build flight_key → filename map from CSV
    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        fk_to_file: dict[str, str] = {
            r["flight_key"]: r["file"]
            for r in csv.DictReader(f)
            if r.get("flight_key") and r.get("file")
        }
    print(f"CSV: {len(fk_to_file)} flight_key → filename mappings loaded.")

    logs_folder = Path(args.logs_folder)
    if not logs_folder.exists():
        raise SystemExit(f"Logs folder not found: {logs_folder}")

    # Locate dji-log binary
    from uas_tasks._binary import get_binary_path
    binary = get_binary_path()
    print(f"Binary: {binary}")

    # Connect to ER
    print("\nConnecting to EarthRanger...")
    from ecoscope.io.earthranger import EarthRangerIO
    client = EarthRangerIO(
        server=args.er_server, username=args.er_user, password=er_password,
        tcp_limit=5, sub_page_size=4000,
    )
    print("  Connected.")

    # Resolve event type UUID
    et_df = client.get_event_types()
    et_match = et_df[et_df["value"] == EVENT_TYPE_SLUG].drop_duplicates("id")
    if et_match.empty:
        raise SystemExit(f"Event type '{EVENT_TYPE_SLUG}' not found.")
    event_type_uuid = str(et_match.iloc[0]["id"])

    # Fetch all events
    print("\nFetching all UAS Flight Folio events...")
    events_gdf = client.get_events(
        event_type=[event_type_uuid],
        since="2020-01-01T00:00:00Z",
        include_details=True,
        drop_null_geometry=False,
        force_point_geometry=True,
    )
    print(f"  Retrieved {len(events_gdf)} events.")

    # Filter to events with no location
    no_loc = events_gdf[events_gdf.geometry.is_empty | events_gdf.geometry.isna()]
    print(f"  Events with no location: {len(no_loc)}\n")

    if no_loc.empty:
        print("Nothing to patch.")
        return

    n_patched = n_no_file = n_no_gps = n_failed = 0

    for event_id, row in no_loc.iterrows():
        ed = row.get("event_details", {})
        if isinstance(ed, str):
            try:
                ed = json.loads(ed)
            except Exception:
                ed = {}
        flight_key = ed.get("flight_key", "") if isinstance(ed, dict) else ""

        filename = fk_to_file.get(flight_key)
        if not filename:
            print(f"  NO_FILE:  {flight_key} (not in CSV)")
            n_no_file += 1
            continue

        filepath = logs_folder / filename
        if not filepath.exists():
            print(f"  NO_FILE:  {flight_key} → {filename} (not in logs folder)")
            n_no_file += 1
            continue

        loc = _extract_location(filepath, binary, args.api_key)
        if loc is None:
            print(f"  NO_GPS:   {flight_key} → {filename}")
            n_no_gps += 1
            continue

        lat, lon = loc
        action = "would_patch" if dry_run else "patching"
        print(f"  {action}: {flight_key}  → ({lat:.6f}, {lon:.6f})")

        if not dry_run:
            try:
                client.patch_event(str(event_id), {
                    "location": {"latitude": lat, "longitude": lon}
                })
                n_patched += 1
            except Exception as exc:
                print(f"    FAILED: {exc}")
                n_failed += 1
        else:
            n_patched += 1

    print()
    print("=" * 60)
    print(f"  {'Would patch' if dry_run else 'Patched'}:  {n_patched}")
    print(f"  No GPS found:       {n_no_gps}")
    print(f"  File not found:     {n_no_file}")
    print(f"  Failed:             {n_failed}")
    if dry_run:
        print("\n  Dry run — add --execute to apply.")
    print("=" * 60)


if __name__ == "__main__":
    main()
