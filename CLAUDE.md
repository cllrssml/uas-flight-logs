# uas_flight_logs — workflow notes

DJI Mavic flight log ingestion → EarthRanger GPS tracks + Flight Folio events.
Tested on Mavic 3T + RC Pro controller and Mavic 2 Pro + DJI Go v4 mobile app.
Published at `github.com/cllrssml/uas-flight-logs`.

Custom package: `uas-tasks` at `/home/sam/Ecoscope_Projects/uas-tasks/`.
GitHub source: https://github.com/cllrssml/uas-flight-logs

---

## Task chain

`set_workflow_details` → `set_er_connection` → `set_dji_api_key` →
`set_input_folder` →
`set_event_type_name` (blank = tracking-only mode: GPS tracks only, no Flight Folio events) →
`set_aircraft_identity` → `set_decimation_rate` →
`ingest_flights` (custom; full per-file loop; dual idempotency on obs+events; returns `IngestResult`) →
`extract_track_gdf` → `extract_results_df` →
`create_polyline_layer` (`color_column: "track_color"`; `legend: ~`) →
`draw_ecomap` → `persist_text` → `create_map_widget_single_view` →
`draw_table` → `persist_text` → `create_table_widget_single_view` →
`count_ingested` → widget_ingested →
`count_skipped` → widget_skipped →
`count_failed` → widget_failed →
`format_total_flight_time` → widget_flight_time →
`format_total_distance` → widget_distance →
`count_aircraft` → widget_aircraft →
`gather_dashboard` (`time_range: ~`).

## Dashboard layout

8 widgets — `widget_id` order matches `gather_dashboard` widgets list:

| widget_id | Widget | x | w | y | h |
|---|---|---|---|---|---|
| 0 | Ingested | 0 | 3 | 0 | 3 |
| 1 | Skipped | 3 | 2 | 0 | 3 |
| 2 | Failed | 5 | 2 | 0 | 3 |
| 3 | Flight Time | 7 | 3 | 0 | 3 |
| 4 | Distance | 0 | 5 | 3 | 3 |
| 5 | Aircraft | 5 | 5 | 3 | 3 |
| 6 | Map | 0 | 10 | 6 | 14 |
| 7 | Table | 0 | 10 | 20 | 10 |

Row 1 (y=0, h=3): `3+2+2+3 = 10`. Row 2 (y=3, h=3): `5+5 = 10`.
Map full-width (y=6, h=14). Table full-width (y=20, h=10).

## Key implementation notes

- **`total_distance_m` is computed from GPS frames (haversine sum)** — do NOT use
  `log_details["totalDistance"]` (units vary: km for RC Pro v13+, metres for older
  firmware e.g. Mavic 2 Pro).
- **DJI `.txt` format** works with RC Pro controller AND DJI Go v4/DJI Fly mobile
  apps — not RC Pro-specific.
- **Subject-source lower bound** — always use `datetime(2000, 1, 1, tzinfo=timezone.utc)`.
  Never use `takeoff_dt` (ER silently excludes observations outside assignment range).
- **`post_source()` provider** — use a named provider, not the default. Create with
  `client.post_sourceproviders(provider_key=..., display_name=...)` first.
- **Blank `set_event_type_name`** → tracking-only mode (GPS tracks, no events).
  All event-related steps should have appropriate `skipif` conditions.

## Post-compile patch

```bash
cp -r /home/sam/Ecoscope_Projects/uas-tasks ecoscope-workflows-*-workflow/uas-tasks
sed -i 's|path = "/home/sam/Ecoscope_Projects/uas-tasks"|path = "./uas-tasks"|' \
  ecoscope-workflows-*-workflow/pixi.toml
cd ecoscope-workflows-*-workflow && pixi install && cd ..
```

## GitHub

Repo: `github.com/cllrssml/uas-flight-logs`. `uas-tasks` bundled inside compiled dir.
Community post: community.earthranger.com/c/ecoscope/16 (June 2026) — first community
Platform SDK drone workflow.

## GPS location extraction — 3-step fallback (2026-06-30)

**Symptom:** Many Flight Folio events posted to ER with no GPS location.
**Root cause:** Takeoff frame OSD lat/lon is 0.0 when GPS hasn't locked yet at the exact moment of liftoff. Old code jumped straight to home coordinates, which can also be 0 or carry firmware large-number garbage (~45836623).
**Fix:** 3-step fallback in `ingest_flights`:
1. Takeoff frame OSD
2. First valid OSD in any airborne frame between takeoff and landing (GPS locks a few seconds post-liftoff — this catches most failures)
3. Home coordinates from any frame

All candidates validated to WGS-84 range (`abs(lat) <= 90, abs(lon) <= 180`). `_valid_wgs84()` helper defined inline in `ingest_flights`.

**Patch script for existing events:** `patch_locations.py` at repo root. Requires the original `.txt` log files and the DJI API key. Run from inside the compiled workflow dir.

## Future timestamp guard (2026-06-30)

**Symptom:** `DJIFlightRecord_2025-08-08_[10-59-50].txt` ingested as year 2050 in ER.
**Root cause:** Garbage GPS timestamps can land in any year, not just pre-2015. Old guard only checked `year < 2015`.
**Fix:** Added upper bound `_MAX_YEAR = datetime.now(timezone.utc).year + 1`. Now catches both past and future garbage.
**ER patch:** The 2050 event was fixed via `patch_provisional_and_2050.py` (one-time script, not committed — CFW-specific). Event ID `4a25df43-0a57-4cc6-a832-c41084636222`, corrected to `1581F5FJD22B800B_20250808T105950Z`. GPS observations still carry 2050 timestamps — cosmetic only.

## DJI API key — correct app type is Open API (2026-06-30)

Per dji-log-parser docs: when creating a DJI developer app, select **Open API** (not Mobile SDK). There is also an activation email step before the key is usable. README and Desktop form tooltip corrected.

## set_operational_defaults task — SHIPPED v4.0.0 (2026-06-30)

`set_operational_defaults` wired into `spec.yaml` between `set_decimation_rate` and `ingest_flights`.
- Single form field: `nature_of_flight` dropdown — blank (default, for mixed batches) or one of the 5 ER values
- `remote_pilot_name` removed: Desktop has no live ER dropdown; single value can't handle multi-pilot batches. Deferred to Web catalogue release.
- Dead helpers `_parse_er_users()` / `_match_pilot_name()` removed from `__init__.py`

**ER `nature_of_flight` enum values (from ER Admin → Event Schema):**

| Value | Display | Order |
|---|---|---|
| `r_vlos` | R-VLOS | 10 |
| `vlos` | VLOS | 20 |
| `e_vlos` | E-VLOS | 30 |
| `b_vlos` | B-VLOS | 40 |
| `d_vlos` | D-VLOS | 50 |

Blank (default) = leave unset; user fills per-flight manually in ER. Use for mixed batches.
Do NOT use `bvlos` — it is not a valid ER value.

**Roadmap — `remote_pilot` on Web:** use `rjsf-overrides` + `EarthRangerEnumResolver` once ER user resolver type is confirmed with EcoScope team (Jake Wall / Charles Stern). Override is silently ignored on Desktop, activates automatically on Web.

**`remote_pilot` ER Admin field:** already changed from free-text → user dropdown (2026-06-30).

## CFW data reconciliation — COMPLETE (2026-06-30)

All 1182 events fully reconciled:
- 22 provisional Mhlonishwa flights verified against paper folios
  - Row 10 (2026-05-10 17:18 UTC): corrected pilot → Sam
  - Row 13 (2026-06-01 11:17 UTC): corrected pilot → Siboniso
  - All 22: `journey_from`/`journey_to` = CFW, PROVISIONAL defects cleared
- 2050 ghost event datetime corrected to 2025-08-08
- 4 "ghost" flights (in ER, not on paper folio) confirmed real — kept as-is
- Scripts used: `patch_provisional_and_2050.py` (untracked), `patch_locations.py` (committed, pending run with logs)

## Security audit (2026-06-30)

Actual aircraft registration `ZT-001407` was in README and Desktop form tooltip — replaced with placeholder `ZT-000001`. CFW ER URL (`careforwildrhino.pamdas.org`) and username (`SamC`) were in `patch_locations.py` docstring — genericized. The one-time scripts (`backfill_operational.py`, `patch_provisional_and_2050.py`, `config-retry74.json`) contain CFW-specific identifiers (aircraft serials, ER URL) — keep untracked, do not commit to public repo.

## Windows encoding fix (2026-06-28) — FIXED in current code

**Root cause:** `subprocess.run(..., text=True)` on Windows uses cp1252 by default. The dji-log
binary outputs UTF-8 JSON. Bytes 0x8d / 0x9d (valid UTF-8, undefined in cp1252) cause the
subprocess reader thread to crash with `UnicodeDecodeError`, leaving `result.stdout = None`.
The main thread then hits `json.loads(None)` → `TypeError: the JSON object must be str...NoneType`.

**Fix:** `encoding="utf-8"` instead of `text=True` in the `subprocess.run` call. Pushed in commit
`fba7952`. Confirmed: 74/1182 files failed on the first bulk import; re-running via WSL CLI
recovered 73/74 (1 genuine loss: `DJIFlightRecord_2026-06-07_[22-28-21].txt` — no aircraft serial).
