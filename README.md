# UAS Flight Logs → EarthRanger

An [Ecoscope Desktop](https://ecoscope.io) workflow that ingests DJI flight logs (`.txt` files exported from the DJI RC Pro controller) into EarthRanger — creating GPS tracks as Subject observations and a structured **UAS Flight Folio** event per flight.

Built for conservation organisations using DJI drones alongside EarthRanger for ranger coordination and wildlife monitoring.

> **Tested on:** DJI Mavic 3T + RC Pro controller.
> Any DJI drone that pairs with the RC Pro and exports `.txt` logs should work in principle.
> Community testing on other drone/controller combinations is very welcome — please open an issue or post in the [EarthRanger Community Forum](https://community.earthranger.com/c/ecoscope/16) with your results.

---

## What it does

For each `.txt` log file in a folder you specify:

1. Decrypts and parses the DJI RC Pro log (requires a DJI developer API key)
2. Gets or creates an EarthRanger **Subject** and **Source** for the aircraft (keyed on serial number)
3. Checks whether this flight has already been ingested — safe to re-run against the same folder
4. Posts **GPS track observations** to EarthRanger at 1 Hz (configurable)
5. Posts a **UAS Flight Folio event** with flight metadata (times, battery, altitude, speed, distance)
6. Saves a KML file per flight to the results folder

The dashboard shows a live satellite map of all flight tracks, an ingestion status table per file, and summary stats (flights ingested / skipped / failed / total time flown / total distance / aircraft count).

---

## Compatibility

| Component | Requirement |
|---|---|
| Controller | **DJI RC Pro** — the `.txt` log format is specific to this controller. Other DJI controllers (RC-N1, Smart Controller) export `.dat` files and are not supported. |
| Drone | Any DJI drone compatible with the RC Pro (Mavic 3 series, Mini 4 Pro, Air 3, Avata 2, etc.) |
| Platform | Ecoscope Desktop (Windows, macOS) |
| EarthRanger | Any hosted EarthRanger instance |

---

## Requirements

- [Ecoscope Desktop](https://ecoscope.io/desktop)
- An EarthRanger instance configured with:
  - A **Subject Type** for your aircraft (e.g. `aircraft`)
  - A **Subject Subtype** (e.g. `drone_quadcopter`)
  - A **Source Type** for GPS tracks (e.g. `tracking-device`)
  - A **UAS Flight Folio** event type — see [Event Type Setup](#event-type-setup) below
- A [DJI developer API key](https://developer.dji.com/) (required to decrypt RC Pro v13+ logs)
- DJI RC Pro flight logs exported as `.txt` files via USB

---

## Installation

1. In Ecoscope Desktop, go to **Workflow Templates → + Add Template**
2. Paste this repository URL and click **Add**
3. Desktop installs the workflow automatically

---

## Getting a DJI API Key

DJI RC Pro logs (firmware v13+) are encrypted. The workflow uses your DJI developer **App Key** (also called SDK Key) to fetch the decryption keychain from DJI's servers the first time each log is processed. Subsequent runs use a locally cached keychain.

1. Go to [developer.dji.com](https://developer.dji.com/) and sign in (or create a free account using your DJI username)
2. Click **Apps** in the top navigation → **Create App**
3. Fill in any app name and description — the values do not matter for log decryption
4. Select **Mobile SDK** as the SDK type
5. Once created, open the app detail page and copy the **SDK Key**

Paste this key into the **DJI API Key** field when configuring the workflow.

> Each organisation should register their own free DJI developer account and use their own key.

---

## Configuration

| Field | Default | Description |
|---|---|---|
| **Data Source** | — | Your EarthRanger connection configured in Desktop |
| **DJI API Key** | — | Your DJI developer API key |
| **Flight Logs Folder** | — | Path to the folder containing `.txt` log files |
| **Flight Folio Event Type** | `uas_flight_folio` | Slug of the UAS Flight Folio event type in ER. Leave blank for tracking-only mode (GPS tracks only, no Flight Folio events) |
| **Aircraft Registration** | *(blank)* | Legal registration number (e.g. `ZT-001407`). Leave blank if unregistered |
| **Subject Type** | `aircraft` | ER subject type slug for this aircraft |
| **Subject Subtype** | `drone_quadcopter` | ER subject subtype slug |
| **Source Type** | `djirc` | ER source type slug for GPS tracks |
| **Track Decimation Rate** | `1` Hz | GPS fixes per second to post to ER (1–10). Higher = more detail, slower upload |

Adjust **Subject Type**, **Subject Subtype**, and **Source Type** to match whatever slugs exist in your ER instance. The values shown above are suggestions — use whatever your ER admin has already created.

---

## Event Type Setup

Create a **UAS Flight Folio** event type in your ER instance before running the workflow. Use the **Form Builder** at `https://<your-er-instance>/admin/form-builder/`.

Set:
- **Display name:** UAS Flight Folio (or any label you prefer)
- **Value (slug):** `uas_flight_folio` — must match the **Flight Folio Event Type** field in the workflow config
- Mark all fields as **not required** — the workflow auto-fills the technical sections; the operational section is completed manually after a flight

### Field reference

#### Section 1 — Flight Identity *(auto-filled)*
| Display Name | API Key | Type |
|---|---|---|
| Aircraft Serial | `aircraft_serial` | Free Text |
| Aircraft Registration | `aircraft_registration` | Free Text |
| Flight Key | `flight_key` | Free Text |

#### Section 2 — Timing *(auto-filled)*
| Display Name | API Key | Type |
|---|---|---|
| Flight Date | `flight_date` | Date |
| Start Time (UTC) | `start_time_utc` | Date/Time |
| End Time (UTC) | `end_time_utc` | Date/Time |
| Flight Duration (min) | `flight_time_min` | Number |

#### Section 3 — Battery *(auto-filled)*
| Display Name | API Key | Type |
|---|---|---|
| Battery % at Takeoff | `battery_pct_takeoff` | Number (0–100) |
| Battery % at Landing | `battery_pct_landing` | Number (0–100) |
| Battery Serial | `battery_serial` | Free Text |

#### Section 4 — Performance Envelope *(auto-filled)*
| Display Name | API Key | Type |
|---|---|---|
| Max Altitude AGL (m) | `max_alt_agl_m` | Number |
| Max Speed (m/s) | `max_speed_ms` | Number |
| Max Distance from Home (m) | `max_dist_m` | Number |
| Total Distance (m) | `total_distance_m` | Number |

#### Section 5 — Operational *(fill in manually after each flight)*
| Display Name | API Key | Type | Notes |
|---|---|---|---|
| Journey From | `journey_from` | Free Text | Departure location |
| Journey To | `journey_to` | Free Text | Destination location |
| Nature of Flight | `nature_of_flight` | Dropdown or Free Text | See note below |
| Remote Pilot | `remote_pilot` | Free Text | |
| UA Observer | `ua_observer` | Free Text | |
| Defects | `defects` | Long Text | |

> **Note on `nature_of_flight`:** In the reference implementation this is a dropdown linked to a custom choice list (`R / V / E / B / D-VLOS`). If you want a dropdown, create a choice list named `nature_of_flight` in your ER Admin first, then reference it here. Alternatively, use a plain **Free Text** field — the workflow will populate it as empty regardless.

#### Section 6 — Technical *(auto-filled)*
| Display Name | API Key | Type |
|---|---|---|
| Home Point Latitude | `home_lat` | Number |
| Home Point Longitude | `home_lon` | Number |
| Firmware Version | `firmware` | Free Text |

> **Note on Firmware Version:** This field is populated from the DJI Fly **app version** recorded in the log, not the drone's firmware version. The DJI log format does not expose drone firmware directly.

### JSON schema (for API-based setup)

The Form Builder does not have a JSON import button. If your ER administrator prefers to create the event type via the EarthRanger REST API rather than the Form Builder UI, the full EFE v2 schema is provided in [`uas_flight_folio_schema.json`](uas_flight_folio_schema.json) at the root of this repository.

---

## Dashboard

| Widget | Description |
|---|---|
| **Ingested** | Flights posted to ER this run |
| **Skipped** | Flights already in ER (duplicate check passed) |
| **Failed** | Files that could not be parsed or posted |
| **Time Flown** | Total airborne time across ingested flights |
| **Distance** | Total GPS track distance across ingested flights |
| **Aircraft** | Number of unique aircraft serials in this batch |
| **Flight Tracks** | Satellite map — each flight coloured distinctly |
| **Ingestion Status** | Per-file table with metrics and error messages |

---

## Notes

**Idempotency** — Before posting each flight, the workflow checks whether a UAS Flight Folio event already exists at the same takeoff time (±10 seconds). If found, the flight is skipped. Re-running against the same folder is safe.

**Multiple aircraft** — The workflow handles mixed folders. Each aircraft gets its own ER Subject and Source, keyed on serial number.

**Source provider** — The workflow automatically creates a `DJI RC Pro` source provider in ER on first run.

**Tracking-only mode** — Leave the **Flight Folio Event Type** field blank to post GPS tracks only, without creating Flight Folio events. Useful for organisations that want drone tracks visible in EarthRanger without setting up the full Flight Folio event type. Idempotency still works — re-running against the same folder is safe.

**Operational fields** — Section 5 (journey, pilot, observer, defects) requires manual completion in EarthRanger after ingestion. These fields cannot be auto-filled from the log.

**KML files** — Full-resolution KML tracks are saved alongside the HTML results for each run.

---

## Known Limitations

- **Non-RC-Pro controllers:** `.dat` logs from older DJI controllers are not supported.
- **Corrupt timestamps:** A small number of RC Pro logs have epoch (1970) timestamps on every frame. The workflow falls back to the filename date/time in this case — the date will be correct but the time may reflect local device time rather than UTC.
- **Firmware version:** Populated from the DJI Fly app version in the log, not the drone's actual firmware.

---

## Troubleshooting

**"Event type not found"** — The `uas_flight_folio` slug does not exist in your ER instance. Create the event type following [Event Type Setup](#event-type-setup) above, or update the **Flight Folio Event Type** field to match your existing slug.

**Flights showing as Failed** — Check the Ingestion Status table for the error message. Common causes: wrong DJI API key, corrupted log file, or a log with no valid GPS frames.

**Observations not visible in ER** — Check that the Subject-Source assignment in ER Admin covers the date range of your flights. The workflow sets the lower bound to 2000-01-01.

**Slow runs / timeout errors** — If DJI's decryption API is unreachable, each file will fail after a 120-second timeout and be recorded as Failed. Check your internet connection and try again.

**macOS: "Got unexpected extra argument" error** — Ecoscope Desktop on macOS does not yet quote the config JSON correctly when passing it to the shell. If your flight logs folder path contains a space (e.g. `DJI Logs`), the shell splits the argument and the workflow fails before it starts. **Fix:** rename the folder to remove the space (e.g. `DJI_Logs` or `DJILogs`) and re-enter the path.

---

## Community

This workflow is the first community-contributed Ecoscope Platform SDK workflow for drone operations. If you are using it with a different DJI drone or controller combination, your results are valuable — please share them on the [EarthRanger Community Forum](https://community.earthranger.com/c/ecoscope/16) or open a GitHub issue.

---

## Acknowledgements

- GPS log parsing via [dji-log-parser](https://github.com/lvauvillier/dji-log-parser) (bundled binaries, MIT licence)
- Built with the [Ecoscope Platform SDK](https://ecoscope.io/en/stable/platform-sdk/)

---

*Built by [Samuel Cilliers](https://github.com/cllrssml) — Care for Wild Rhino Sanctuary*
