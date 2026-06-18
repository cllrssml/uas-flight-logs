# UAS Flight Logs → EarthRanger

An [Ecoscope Desktop](https://ecoscope.io) workflow that ingests DJI Mavic 3E flight logs (`.txt` files exported from the RC Pro controller) into EarthRanger — creating GPS tracks as Subject observations and a structured **UAS Flight Folio** event per flight.

Built for conservation organisations using DJI drones alongside EarthRanger for ranger coordination and wildlife monitoring.

---

## What it does

For each `.txt` log file in a folder you specify:

1. Decrypts and parses the DJI RC Pro log (requires a DJI developer API key)
2. Gets or creates an EarthRanger **Subject** and **Source** for the aircraft (keyed on serial number)
3. Checks whether this flight has already been ingested (idempotent — safe to re-run)
4. Posts **GPS track observations** to EarthRanger at 1 Hz (configurable)
5. Posts a **UAS Flight Folio event** with flight metadata (times, battery, altitude, speed, distance)
6. Saves a KML file per flight to the results folder

The dashboard shows a live satellite map of all flight tracks, ingestion status per file, and summary stats (flights ingested / skipped / failed / total time flown).

---

## Requirements

- [Ecoscope Desktop](https://ecoscope.io/desktop) (Windows or macOS)
- An EarthRanger instance with:
  - A **Subject Type** for your aircraft (e.g. `aircraft`)
  - A **Subject Subtype** (e.g. `drone_quadcopter`)
  - A **Source Type** for GPS tracks from the RC Pro (e.g. `tracking-device`)
  - A **UAS Flight Folio** event type — see [Event Type Setup](#event-type-setup) below
- A [DJI developer API key](https://developer.dji.com/) (required to decrypt RC Pro v13+ logs)
- DJI Mavic 3E / RC Pro flight logs exported via USB as `.txt` files

---

## Installation

1. In Ecoscope Desktop, go to **Workflow Templates → + Add Template**
2. Paste this repository URL and click **Add**
3. Desktop will install the workflow automatically

---

## Getting a DJI API Key

DJI RC Pro logs (firmware v13+) are encrypted. The workflow uses your DJI developer **App Key** (also called SDK Key) to fetch the decryption keychain from DJI's servers the first time each log is processed.

1. Go to [developer.dji.com](https://developer.dji.com/) and sign in (or create a free account using your DJI username)
2. Click **Apps** in the top navigation → **Create App**
3. Fill in any app name and description — the values do not matter for log decryption
4. Select **Mobile SDK** as the SDK type
5. Once created, open your app and go to the **App Detail** page
6. Copy the **SDK Key** — this is your DJI API Key

Paste this key into the **DJI API Key** field when configuring the workflow. The key is only sent to DJI's servers on the first decrypt of each log file; subsequent runs use a locally cached keychain and do not require internet access to DJI.

> **Note:** The key is tied to your DJI developer account. Each organisation running this workflow should register their own free account and use their own key.

---

## Configuration

| Field | Description |
|---|---|
| **Data Source** | Your EarthRanger connection configured in Desktop |
| **DJI API Key** | Your DJI developer API key for decrypting RC Pro logs |
| **Flight Logs Folder** | Path to a folder containing `.txt` log files exported from the RC Pro |
| **Flight Folio Event Type** | Slug of the UAS Flight Folio event type in ER (default: `uas_flight_folio`) |
| **Aircraft Registration** | Legal registration number (e.g. `ZT-001407`). Leave blank if unregistered |
| **Subject Type** | ER subject type slug for this aircraft (e.g. `aircraft`) |
| **Subject Subtype** | ER subject subtype slug (e.g. `drone_quadcopter`) |
| **Source Type** | ER source type slug for GPS tracks (e.g. `tracking-device`) |
| **Track Decimation Rate** | Observations per second to post to ER (default: 1 Hz). Higher = more detail, slower upload |

---

## Event Type Setup

Create a **UAS Flight Folio** event type in ER Admin → Event Types using EFE v2. Set the slug (Value) to `uas_flight_folio` and add the following fields. All fields should be **not required** (the workflow auto-fills them).

**Section 1 — Flight Identity**

| Display Name | API Key | Type |
|---|---|---|
| Aircraft Serial | `aircraft_serial` | Free Text |
| Aircraft Registration | `aircraft_registration` | Free Text |
| Flight Key | `flight_key` | Free Text |

**Section 2 — Timing**

| Display Name | API Key | Type |
|---|---|---|
| Flight Date | `flight_date` | Date |
| Start Time (UTC) | `start_time_utc` | Free Text |
| End Time (UTC) | `end_time_utc` | Free Text |
| Flight Duration (min) | `flight_time_min` | Number |

**Section 3 — Battery**

| Display Name | API Key | Type |
|---|---|---|
| Battery % at Takeoff | `battery_pct_takeoff` | Number (0–100) |
| Battery % at Landing | `battery_pct_landing` | Number (0–100) |
| Battery Serial | `battery_serial` | Free Text |

**Section 4 — Performance Envelope**

| Display Name | API Key | Type |
|---|---|---|
| Max Altitude AGL (m) | `max_alt_agl_m` | Number |
| Max Speed (m/s) | `max_speed_ms` | Number |
| Max Distance from Home (m) | `max_dist_m` | Number |
| Total Distance (m) | `total_distance_m` | Number |

**Section 5 — Operational**

| Display Name | API Key | Type |
|---|---|---|
| Journey From | `journey_from` | Free Text |
| Journey To | `journey_to` | Free Text |
| Nature of Flight | `nature_of_flight` | List (R / V / E / B / D-VLOS) |
| Remote Pilot | `remote_pilot` | Free Text |
| UA Observer | `ua_observer` | Free Text |
| Defects | `defects` | Scrolling Text |

**Section 6 — Technical**

| Display Name | API Key | Type |
|---|---|---|
| Home Point Latitude | `home_lat` | Number |
| Home Point Longitude | `home_lon` | Number |
| Firmware Version | `firmware` | Free Text |

---

## Notes

**Idempotency** — The workflow checks EarthRanger before posting each flight. If a UAS Flight Folio event with the same flight key (`{serial}_{takeoff_utc}`) already exists, the flight is skipped. Re-running against the same folder is safe.

**Source provider** — The workflow automatically creates a `DJI RC Pro` source provider in ER on first run. New aircraft sources are registered under this provider.

**KML files** — Full-resolution KML tracks are saved alongside the HTML results for each run.

**Logs from multiple aircraft** — The workflow handles mixed folders. Each aircraft gets its own ER Subject and Source, keyed on serial number.

---

## Troubleshooting

**"Event type not found"** — The `uas_flight_folio` event type does not exist in your ER instance. Create it following the [Event Type Setup](#event-type-setup) section above.

**"dji-log binary not found"** — The bundled parser binary was not found. This should not happen with a standard Desktop install — please open an issue.

**Flights showing as Failed** — Check the Ingestion Status table in the dashboard for the error message. Common causes: corrupted log file, wrong DJI API key (v13+ RC Pro logs are encrypted), or a log file with no valid GPS frames.

**Observations not visible in ER** — Check that the Subject-Source assignment in ER Admin covers the date range of your flights. The workflow sets the assignment start to 2000-01-01, but if you had an earlier install the range may need to be updated manually.

---

## Acknowledgements

- GPS log parsing via [dji-log-parser](https://github.com/lvauvillier/dji-log-parser) (bundled binaries, MIT licence)
- Built with the [Ecoscope Platform SDK](https://ecoscope.io/en/stable/platform-sdk/)

---

*Built by [Samuel Cilliers](https://github.com/cllrssml) — Care for Wild Rhino Sanctuary*
