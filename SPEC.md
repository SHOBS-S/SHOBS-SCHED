# SHOBS-SCHED v1 — build spec (for the build workers)

SHOBS-SCHED plans each night's targets for the three SHOBS-P modes (Variables = V, Transits = T, Discovery = D),
for one night or a rolling 7 nights, and exports the plan for ASIAIR (coordinate CSV + printable plan card).
Owner: John (amateur observatory, 14" RC f/8, ZWO ASI2600MC color camera, EQ8-R German equatorial mount (GEM) with
meridian flips, ASIAIR control). Sibling app: SHOBS-P (photometry; repo SHOBS-S/SHOBS-P, read-only reference at
/home/claude/shobs-p). SCHED is a separate app in this repo: /home/claude/shobs-sched.

## Ground rules (all workers)

* Python 3.11+ standard library + **numpy** + **tzdata** + **matplotlib** (the night plot, added in round 2) only
  (no astropy, no requests).
  Network = `urllib.request` with a User-Agent "SHOBS-SCHED/1.0" and timeouts; never block the UI thread.
* Run tests with **`/usr/bin/python3.12`** (it has numpy, tzdata, tkinter). The default `python3` has no Tk.
  GUI tests: `xvfb-run -a /usr/bin/python3.12 ...`. Tests use `unittest` (no pytest), in `tests/test_<module>.py`,
  runnable as `cd /home/claude/shobs-sched && /usr/bin/python3.12 -m unittest tests.test_<module> -v`.
  Tests must not depend on the network: put recorded replies in `tests/fixtures/` and monkeypatch the fetch
  function. (Live calls may be tried manually: NASA Exoplanet Archive TAP, SIMBAD, VizieR TAP and Open-Meteo are
  reachable from this workspace; the AAVSO VSX API answers 403 here, so VizieR's copy of VSX (B/vsx) is the
  fallback. The Archive's Cloudflare blocks some ADQL shapes (an `in (...)` list combined with `is not null`
  was blocked; one-planet `pl_name = '...'` queries work): keep queries simple and test them live once.)
* Point every file at a temp folder in tests: set env `SHOBS_SCHED_HOME` (and `SHOBS_P_SETTINGS`) before importing
  `settings`. Never touch the real home folder in tests.
* **Times:** JD (UTC) everywhere inside; the SITE's IANA time zone (`site.tz`) for anything shown to the user, via
  `sky.to_local`, `sky.hhmm`, `sky.local_jd`. Never the computer's time zone (no naive `datetime.now()` for site
  times, no `.astimezone()` without a zone).
* **Non-localized:** any latitude (southern hemisphere: meridian is to the NORTH, flips the same by hour angle),
  any time zone, daylight-saving nights (23 h/25 h), high latitude with no astronomical darkness (say so; plan with
  what darkness there is if the user allows a brighter sun_alt, else "no dark time tonight"), units °C/°F and m/ft as
  display settings. English UI.
* **Stand-alone first:** SCHED works fully with no SHOBS-P installed. SHOBS-P's settings are only read
  (`settings.merge_shobs_p`), never written.
* **House Style** (all SHOBS apps): blue frame (sidebar), results/info in the WHITE centre; page title at the top
  of every page; mode colors navy = Variables, green = Transits, plum = Discovery (blocks on the night timeline take
  the color of the mode they will be reduced in); every final action button (Build, Add, Copy, Print, Save…) is
  GREEN and dims briefly when pressed; drop-downs for several choices, checkboxes for on/off, radio buttons for 2–3
  always-visible choices; every status = color AND icon/word: ✖ red warning, ⚠ yellow caution, ✔ normal (blue text
  on white); background jobs over ~5 s show a progress bar when the count is known, else a spinner, placed in the
  white centre next to the job, with a brief "✔ Done" (or ✖ + reason) at the end.
* Plain, specific wording in messages ("Archive did not answer (HTTP 503); using the copy from 3 days ago").
  No version numbers in user messages. Log noteworthy events with `settings.log(...)`.
* Do NOT commit; the lead commits. Do not edit files owned by another worker (list below); if you need a change in
  a shared file (models.py, sky.py, events.py, settings.py), make the smallest additive change, and say so in your
  final report.

## Shared foundation (written by the lead; read these first)

* `models.py` — dataclasses Site, Optics, Planet, VarElements, Rule, Target, Block, NightPlan, Schedule (JSON
  round-trip via to_dict/from_dict; unknown keys ignored).
* `sky.py` — time (site zone), Sun/Moon, `target_alt/az/airmass/meridian/hour_angle/moon_sep` (J2000 positions,
  precessed), `night_info(d, site)` (dusk/dawn at site.sun_alt + civil/nautical/astro, sunset/sunrise,
  moonrise/set, moon_illum, dark_ok), `night_window`, `up_mask` / `limit_alt` (min_alt, surveyed horizon by azimuth,
  max airmass), `moon_sky_brightness` / `moon_sky_penalty` (Krisciunas & Schaefer 1991), `fmt_ra/fmt_dec`,
  `parse_ra/parse_dec`. Checked against the plan doc: dark times ±1 min, meridian times ±1 min.
* `events.py` — `transit_events(planet, ra, dec, jd0, jd1)`, `variable_events(elements, ra, dec, jd0, jd1)`,
  `blackouts(target, jd0, jd1)` ("no_transit" campaign rules), `clock_window(rule, night_date, tz)`.
* `settings.py` — file locations, `load_settings/save_settings` (dict: site, optics, options, observer_code,
  set_here, shobs_p_read), `DEFAULT_OPTIONS` (all planner/suggest/weather/exposure knobs), `merge_shobs_p`,
  `guess_tz`, `all_zones`, `load_targets/save_targets`, `load_schedule/save_schedule`, `cache_dir()`, `log()`.
* `tests/fixtures/golden_oct19_24.json` — the golden test: John's Oct 19–24 plan (site, targets, ephemerides,
  expected dark times, transit times, blackout, and the plan's blocks per night).

## Workers and the files each OWNS

| Worker | Files |
|---|---|
| A — targets | `targets.py`, `tests/test_targets.py`, `tests/fixtures/targets_*` |
| B — planner | `planner.py`, `tests/test_planner.py`, `tests/test_golden.py`, `tests/test_nonlocal.py` |
| C — weather + export | `weather.py`, `export.py`, `tests/test_weather.py`, `tests/test_export.py`, `tests/fixtures/weather_*` |
| D — app | `sched_app.py`, `style.py`, `tests/test_app.py` |

## A — targets.py (lookups, cache, Suggested transits, next-event text)

API (keep these names/signatures; add helpers freely):
* `class LookupError_(Exception)` with a user-facing message; `class NeedsMode(Exception)` (name resolved but not a
  known planet or VSX variable: carries ra, dec, vmag, main_id so the UI asks which mode from a drop-down).
* `resolve_simbad(name) -> dict` {main_id, ra, dec, vmag, otype} (SIMBAD; TAP or sim-id script output).
* `lookup_planet(name) -> Target` — NASA Exoplanet Archive composite table (pscomppars) for coordinates, V, depth
  ((Rp/R*)² when pl_ratror is known, else pl_trandep/100), duration (hours → days); then the **most precise
  ephemeris** at the coming weeks (port `most_precise_ephemeris` from /home/claude/shobs-p/transit_core.py
  ~L1818–1856: of all rows in table `ps` for that planet with period, t0 and both errors, the one with the smallest
  predicted Tc sigma at `when_jd`; keep the composite one unless a row beats it by >5 %). Fill Planet.reference
  (strip HTML; the Archive double-escapes names, unescape twice) and Planet.source ("NEA" or "NEA (most precise)").
  Name variants like SHOBS-P's `_name_variants` (port it): "wasp-12b", "TrES-3b", "toi 2046 b".
  A star name with several transiting planets → raise a `PlanetChoice`-like exception listing them.
* `lookup_variable(name) -> Target` — AAVSO VSX API first
  (`https://www.aavso.org/vsx/index.php?view=api.object&ident=<name>&format=json`), fallback VizieR TAP
  `B/vsx/vsx` (columns Name, RAJ2000, DEJ2000, Type, max, min, Epoch, Period; Name is space-padded: compare
  stripped; query by exact name, then by a 1' cone if SIMBAD resolves the name). Elements → `VarElements`:
  pulsators (DSCT, HADS, SXPHE, RR*, CEP*, DCEP, BCEP, ROAP, GDOR, …) any_time=True, epoch_kind "max";
  eclipsing (EA, EB, EW, ELL…) any_time=False, epoch_kind "min" (event_halfwidth_min = default 60, or ~0.6×eclipse
  duration when VSX gives it); long-period/irregular (M, SR, L, …) any_time=True. vmag = max (brightest).
* `add_target(name) -> Target` — planet first (if it looks like a planet name or the Archive knows it), else VSX,
  else SIMBAD → `NeedsMode`. Sets kind/mode (planet → transit/T; variable → variable/V), source, added (ISO date).
* **Cache** (stand-alone, in `settings.cache_dir()`): one JSON per lookup key with the fetch time; a lookup that
  fails (no network, HTTP error, Cloudflare page) returns the cached answer with a note of its age, or raises
  `LookupError_` with the HTTP status / reason in plain words. Planet ephemerides refresh after 7 days,
  VSX/SIMBAD after 30. Document the cache format in a docstring (it must merge with SHOBS-P's future cache, #21).
* `load_transit_catalog(refresh=False) -> (list[dict], note)` — every transiting planet with period+t0+duration
  (port SHOBS-P's `load_transit_catalog`, cache file in cache_dir, weekly refresh, saved-copy fallback).
* `suggest_transits(site, optics, options, nights: list[date], exclude_names=set()) -> list[Target]` — other good
  transits in the given nights, in John's order (all thresholds from `options`): (1) **full** transit: ingress −
  `suggest_baseline_min` to egress + `suggest_baseline_min` all inside the planning darkness and above every limit
  (`sky.up_mask`: min altitude, horizon profile, max airmass); (2) host V in [suggest_vmin, suggest_vmax];
  (3) depth ≥ suggest_min_depth; (4) Moon: mean `sky.moon_sky_penalty` over the window ≤ suggest_moon_penalty_max;
  (5) rank by expected S/N (depth × √(points in transit) / noise, noise from V and the Moon-brightened sky — a simple
  documented proxy is fine) with a bonus for an uncertain/old ephemeris (predicted Tc sigma large: new timings add
  the most). Return Targets with suggested=True, enabled=False, kind "transit", mode "T", notes = "<date>: ingress
  HH:MM, egress HH:MM (site clock)"; at most suggest_max; each planet once (its best night). Catalog ephemerides
  only (no per-planet `ps` queries for the whole catalog); a ⚠ note when the predicted Tc sigma >
  tc_sigma_warn_min.
* `next_event_text(target, site, from_jd, days=30) -> str` — Targets-page column: transits "Tue 20 Oct 21:32–23:56
  (full)" (first visible FULL transit; else first partial with "(partial)"; else "none in 30 nights"); timed
  variables "min Thu 22 Oct 01:14"; any-time → "any time"; campaigns → "every night" or the next blackout.
* `tc_sigma_minutes(planet, when_jd) -> float | None`.
* `campaign_progress(target, schedule: Schedule, today: date) -> tuple[int, int]` — (nights Done/Partial in the
  current week Mon–Sun by schedule.history and the schedule's marked blocks, goal_per_week).

Tests: name variants; most-precise selection (fixture rows); VSX JSON parse + VizieR CSV fallback (fixtures);
cache hit / stale / offline fallback; suggest_transits on a small fixture catalog for the golden site/nights
(HAT-P-23 b on 23 Oct is full; a deep transit that only half fits is excluded); next_event_text strings in the
site zone (also for a site whose zone differs from the computer's — run with TZ=Asia/Tokyo in the env).

## B — planner.py (building the night)

API:
* `build_night(d: date, site, optics, targets: list[Target], options: dict, locked: list[Block] = (),
  weather: dict | None = None, history: list | None = None, prefer: set[str] = frozenset()) -> NightPlan`
* `build_schedule(start: date, n_nights: int, site, optics, targets, options, prior: Schedule | None = None,
  weather_by_date: dict | None = None, prefer_by_date: dict | None = None) -> Schedule` — keeps every locked /
  set-by-user block of `prior` on its night (times kept) and builds around it; keeps block statuses.
* `rereview(schedule, today: date, site, optics, targets, options, weather_by_date=None) -> Schedule` — moves nights
  before `today` into schedule.history (one record per block with its status/minutes), keeps 7-night rolling length
  (adds new nights at the end), and returns targets whose blocks were "clouded" or "partial" to the pool (the
  rebuild naturally re-plans them; a Clouded/Partial campaign night counts as missed for its weekly goal).
* `estimate_exposure(target, optics, options, airmass=1.2, moon_penalty=0.0) -> dict` {exposure_s, peak_adu,
  is_estimate, note}: cold-start estimate from V, aperture (assume 30 % central obstruction), QE, e/ADU, pixel scale
  and seeing FWHM (`options['fwhm_arcsec']`), Bayer green for a color camera; choose the longest of the standard
  exposures (0.75, 1, 2, 5, 10, 20, 30, 40, 60, 90, 120, 180, 300 s) whose predicted raw peak ≤
  target_peak_frac × linear_limit_adu; always labelled "estimate" unless target.exposure_s is set by the user (then
  it predicts the peak for that exposure and cautions ⚠ when over the linear limit). Sanity anchor (John's data):
  V 11.4, 60 s, 14" f/8, ASI2600MC gain 100 (0.243 e/ADU), good seeing → raw peak 10–17k ADU.
* Block notes text in the plan-doc style (see the golden fixture's plan doc): "Ingress 21:32, mid 22:44, egress
  23:56 (±1 min)", "Start before the meridian and let it flip (~22:40); drop the pre-flip frames",
  "About 1.3 cycles (P ≈ 1.75 h)", "stop by 03:38 at the latest", "No HD 219134 after 04:00 (planet c transit)".

Rules (Requirements doc, decisions of 20 Oct):
1. **Locked / set-by-user blocks** first, exactly as they are (warn ⚠ if now impossible, e.g. target below limits).
2. **Fixed-time targets** next: transits (window = t1 − `baseline_min` … t4 + `baseline_min`; the transit itself
   (t1…t4) must be inside the darkness and above limits; baseline may be shortened to fit the night or the
   limits — keep ≥ `baseline_trim_min` per side or drop with the reason; baselines may run into nautical twilight
   (Sun ≤ −12°): the plan doc's HAT-P-23 b starts 19:30 in twilight, ingress 20:31), then timed variable
   events (variable_events ± event_halfwidth). A transit whose meridian falls INSIDE t1…t4 on a GEM gets ⚠
   "meridian flip during the transit (HH:MM)".
   **Collisions** between fixed-time targets: first trim baselines toward `baseline_trim_min` each side; if both
   then fit, keep both with ⚠ "baseline trimmed". Otherwise the winner is: higher priority, then the rarer next
   chance (search forward up to 60 nights for that target's next feasible full event; a transit not visible again
   for 30 nights beats one back in 3), then better S/N (depth / noise by V). The loser goes to didnt_fit with reason
   and next chance ("collides with WASP-12 b; next chance 27 Oct"). `prefer` (target ids) wins collisions — that is
   how the UI's "Plan this instead" works.
3. **Flexible targets** (campaigns incl. Discovery fields, any-time variables) by priority (High → Normal → Low);
   a campaign behind its weekly goal (`history` + this schedule) is pushed up one level. Each is placed in free
   time at its best airmass (centered on its meridian where possible), length min(block_min, free time), at least
   min_block_min, never inside its blackouts (events.blackouts: "No HD 219134 after 04:00 (planet c transit)"),
   respecting its own rules (min_alt, min_moon_sep, window) and site limits. A flexible target is scheduled at most
   once per night.
4. **Flip rule (GEM, 20 Oct):** never START a block just after its target's meridian (the mount gets confused).
   If a block would start within ~60 min after the meridian, move its start to `flip_lead_min` (10–15) before the
   meridian and let the mount flip; note "Start before the meridian and let it flip (~HH:MM); drop the pre-flip
   frames"; the usable time loses `flip_settle_min`. Blocks starting more than about an hour past the meridian are
   left as they are. A block that starts before the meridian and runs past it flips at the meridian (note it).
   The mount tracks at most `site.past_meridian_min` past the meridian without flipping: a block ending within
   that margin after the meridian needs no flip ("ends 3 min past its meridian, inside your tracking margin; stop
   by 03:38 at the latest"). Alt-az mounts: no flips; ⚠ "zenith keyhole" when the target passes above 85°.
   Southern hemisphere: same hour-angle logic.
5. **Overheads** between blocks: `overhead_slew_min` (slew, settle, solve); `overhead_focus_min` once at the start
   of the night (and when a block's notes ask for a refocus, e.g. after a defocused campaign: keep it simple — the
   target's notes may contain "defocus"; then add a refocus overhead before the next in-focus block).
6. **Weather/dew** (optional `weather` dict from weather.py: {"hours":[{jd, cloud, temp_c, dew_c, spread_c,
   wind_kmh}], "dew_onset": jd|None, ...}): with `options['dew_sensitive_end']`, flexible blocks end at the dew onset
   (⚠ "dew expected from HH:MM"); a fixed block crossing it gets ⚠. Cloudy hours are NOT removed in v1 (the user
   decides); add a caution when cloud ≥ 70 % over most of a block.
7. **Didn't fit, and why** for every enabled target not placed (below limits all night, Moon too close, collision,
   no event tonight, no dark time…), with its next chance date when known. **Free gaps** ≥ `min_gap_report_min`.
8. **Flats:** one line per night ("Flats: one set at the night's camera angle; a meridian flip's 180° turn needs
   no new flats" — John's correction 18 Oct); a defocused campaign (notes contain "defocus") adds "flats at the
   defocus position too". **Darks to shoot:** exposures used tonight that are not in `options.get('darks_on_hand',
   [])`.
9. Each Block gets: alt_start/alt_end/alt_min/alt_max, airmass_start/best/end, meridian (JD of the crossing nearest
   the block), flip_in_block, moon_sep (minimum), exposure (estimate_exposure) and frames = minutes·60 /
   (exposure + cadence_overhead_s); transits also frames_in_transit and tc_sigma_min (⚠ when >
   tc_sigma_warn_min).

Tests: `tests/test_golden.py` — for each of the 5 nights in `tests/fixtures/golden_oct19_24.json`, enable only that
night's targets and build: dusk/dawn within 3 min; transit t1/tc/t4 within 3 min; HD 219134's block on 22 Oct ends
before the 04:28 blackout; every target listed for the night gets a block; fixed transit blocks cover t1−20 min …
t4+20 min; the HD 219134 blocks that the plan started before the meridian (Wed, Thu) carry the flip note; print a
side-by-side table (plan vs SCHED) for every block and assert flexible block starts within 30 min of the plan OR
document the reason in the test output (the plan doc was hand-made). `tests/test_nonlocal.py` — southern site
(Siding Spring −31.27, 149.07, Australia/Sydney) flips by hour angle and finds dark; a site in a zone different from
the computer (run the test with env TZ=Asia/Tokyo and site America/New_York: same answers as golden); the US DST
night 2026-11-01 (25 h, dawn still right); a high-latitude site (Tromsø 69.65, 18.96, June) with no astronomical
dark → no blocks and a clear reason; collisions (two transits overlapping: trim then priority, prefer swaps).

## C — weather.py and export.py

weather.py:
* `class WeatherSource` (name, `fetch(site, start: date, n_nights: int) -> dict[str, dict]` keyed by night ISO date);
  `OpenMeteo(WeatherSource)` = v1's only source (https://api.open-meteo.com/v1/forecast, hourly temperature_2m,
  dew_point_2m, relative_humidity_2m, cloud_cover, wind_speed_10m, timezone=GMT, forecast_days ≤ 16, units metric).
  `SOURCES = {"Open-Meteo": OpenMeteo}`; NWS plugs in later (leave a stub comment, no code).
* Night dict: {"source", "fetched" (ISO), "hours": [{"jd", "cloud", "temp_c", "dew_c", "spread_c", "rh",
  "wind_kmh"}] from sunset to sunrise (sky.night_info), "dew_onset": first jd in darkness where spread <
  options["dew_spread_c"] (None if never), "summary": "Clear until 02:00, then 60–80 % cloud; dew from 04:05"}.
* Cache the last answer per site in cache_dir (`weather_<lat>_<lon>.json`); offline → cached answer with its age,
  or a clear "no forecast (reason)". Forecasts older than 3 h are refetched.
* `fmt_temp(c, unit)`, `fmt_wind(kmh, unit_system)` helpers.
export.py:
* `table_rows(night: NightPlan, site) -> list[dict]` — the nightly table in the plan-doc format (columns Time,
  Target, RA / Dec (J2000), SHOBS-P mode, Alt, Meridian, Exposure, Frames, Notes). Alt = "52–72°" low–high over the
  block; the UI also shows airmass ("62° (X 1.13)" at best) — provide both fields. Exposure "60 s" or "0.75 s",
  frames "~240 (~135 in transit)", Notes joined. Used by the app AND the card.
* `asiair_csv(nights: list[NightPlan], targets_by_id, site) -> str` — paste-ready coordinate list ASIAIR's Plan
  can import (it imports coordinates only — no exposures or timing — via a Telescopius-style CSV copied to the
  clipboard). Research the exact Telescopius list-export column names with WebSearch/WebFetch (ASIAIR docs,
  Telescopius help, forums: e.g. "Pane, Familiar Name, Catalogue Entry, Type, Right Ascension, Declination,
  Magnitude, ..."), document the source in a comment, and write exactly that header; one row per block in time
  order (a target once even with two blocks), RA "23h 13m 17s"/Dec "+57° 10' 06"" in the format that list uses.
  Plain ASCII where the format needs it.
* `plan_card_html(nights, site, settings_dict, weather_by_date=None) -> str` — a printable, self-contained HTML
  page (A4/Letter, black on white, big readable times; per night: date, dark, Moon %, moonrise/set, weather line +
  dew onset, the nightly table, flats, darks, didn't fit, free gaps; footer "SHOBS-SCHED · site clock (<zone>)").
  The app writes it to cache_dir and opens it in the browser (the user prints from there).
* `plan_card_text(...)` — the same as plain text (for the preview pane and copy).

Tests: parse a recorded Open-Meteo reply (fixture; record one live for John's site and also a southern site);
dew onset; DST night hours; offline fallback; table_rows from a hand-made NightPlan (golden Oct 20 night shape);
CSV header/rows; HTML contains every block and escapes names (`<`, `&`).

## D — sched_app.py (the app) and style.py

Same shell as SHOBS-P (look at /home/claude/shobs-p/clearv_app.py ~L1100–1160 for its ttk styles and sidebar):
Tkinter + ttk, window title `settings.APP_TITLE`, blue sidebar ("Shiloh Hill Observatory Scheduler" + a numbered
STEPS list: 1 Setup, 2 Targets, 3 Schedule, 4 Export), page title at the top of the white centre, a status bar.
`style.py` holds colors and ttk style setup: frame blue (SHOBS-P sidebar #16324a family), mode colors (navy
Variables #16324a/#2a5a80, green Transits #1d4a2f/#357a50, plum Discovery #4a1d47/#7a3576 — use SHOBS-P's
MODE table values), GREEN action buttons (e.g. #2e7d32, hover #256628, pressed dim), ✖/⚠/✔ label styles.
High-DPI: no fixed pixel widths for text; size from content; use `tk scaling` sensibly; John runs Windows at
125–150 % on a 4K screen.

Pages:
1. **Setup** — site (lat/long, elevation, time zone drop-down from `settings.all_zones()` with the guess
   preselected, units °C/°F and m/ft radio buttons), optics and camera (aperture, focal length, pixel size, binning,
   camera name, color/mono, gain, e/ADU, full well, linear-limit ADU), mount (GEM / alt-az radio) and its
   past-meridian limit (minutes), minimum altitude, max airmass, darkness (drop-down: astronomical −18 / nautical −12),
   horizon profile (a text box of "az alt" pairs, one per line, + Import CSV…, + true/magnetic north radio and
   declination), weather source drop-down (Open-Meteo), dew spread threshold. A provenance line at the top:
   "✔ Site, optics and camera read from SHOBS-P (settings of <date>). Change them there, or override here." (an
   overridden field gets a small "set here" tag) — or stand-alone: "Enter your site and equipment once. SHOBS-P, if
   you add it later, can share these." (the ONLY mention of SHOBS-P). Values typed from a spec sheet (full well,
   linear limit) show ⚠ "estimate". Save (green). Validation messages in plain words.
2. **Targets** — one ttk.Treeview with four collapsible groups: Transits, Variables, Campaigns, Suggested. Columns:
   plan-this checkbox (☑/☐, click toggles), mode tag (V/T/D in the mode color), Name, V, RA/Dec, Priority
   (High/Normal/Low drop-down editor on click), Next useful event (`targets.next_event_text`), Source (✔ NEA, VSX…);
   Transits also depth, duration, ephemeris reference and Tc ± (⚠ > 10 min); Campaigns: rules summary, goal and
   progress ("2 of 3 this week"). "Add" box + Add (green): runs `targets.add_target` in a thread (spinner); on
   `NeedsMode` asks the mode from a drop-down dialog; on a multi-planet star asks which planet. Remove, Edit… (a
   dialog for exposure, block length, goal, rules incl. "no data while a planet transits" with planet lookup, min
   altitude, Moon distance, clock window, notes), "Find suggested transits" (green; thread + progress), tick a
   Suggested row to plan it or "Keep" to move it to Transits.
3. **Schedule** — nights spinbox/drop-down 1–7, start date (tonight by default, site clock), Build schedule
   (green; thread + progress; fetches weather via weather.SOURCES[options['weather_source']] first, tolerates
   failure). One ttk.Notebook tab per night ("Thu 22 Oct"). Each tab top to bottom: (1)+(2) the **night plot**
   (nightplot.py, matplotlib, round 2 — replaces the v1 weather-strip and block-bar canvases; design after TAPIR's
   plot_airmass.cgi, credited in README): airmass 1.0–2.4 (elevation on the right) against the site clock from
   nautical dusk − 1.5 h to nautical dawn + 1.5 h in whole hours, twilight shading with the times on top, each
   planned target's whole-night path faint and its block bold in the mode color with the airmass at start and end,
   a label box per block (name, start–end, Moon distance), ▼ at a flip inside a block, transit t1–t4 and baselines,
   the Moon dashed (width by phase), the max-airmass line, no-transit windows only where they changed the plan, and
   a weather strip on the same axis (cloud, temperature, dew point, spread, dew risk); hover shows the values, a row
   click highlights that target (and a block click selects its row); without matplotlib a ⚠ line instead;
   (3) the **nightly table**
   (Time, Target, RA/Dec, SHOBS-P mode, Alt — e.g. "62° (X 1.13)" —, Meridian, Exposure, Frames, Notes), rows tinted
   lightly by mode; per selected row: Lock, Remove, Move to night…, Edit times… (typed start/end; such blocks are
   tagged "set by you" and survive rebuilds), and a status drop-down Planned / Done / Partial / Clouded out;
   (4) below: Flats and darks to shoot, "Didn't fit, and why" (each with "Plan this instead" → rebuild that night
   with prefer={id}), and free gaps ("Free 02:10–03:00"). Re-review schedule (green) → `planner.rereview` then
   rebuild. Schedule saved with `settings.save_schedule` after every change.
4. **Export** — pick nights (checkboxes), Copy ASIAIR CSV (green; clipboard + "✔ Copied N targets"), Save CSV…,
   Print plan card (green; writes the HTML to cache_dir and opens it with `webbrowser`), and a preview (Text widget
   with `export.plan_card_text`).

Code against the APIs above (targets, planner, weather, export); they are being written in parallel. Until they
exist, use small local fakes in your tests only (never in sched_app.py). Make every page work with an empty target
list and no network. Tests (`tests/test_app.py`, under `xvfb-run -a /usr/bin/python3.12`): the app starts with a
clean temp home (stand-alone), with a fake SHOBS-P settings file (provenance line), switches through all pages,
builds a schedule from the golden fixture's targets with network calls monkeypatched off, draws the canvases
(no exceptions), toggles a checkbox, locks a block and rebuilds (lock survives), exports the CSV to the clipboard.
Also `python3.12 sched_app.py --selftest-ui` may be useful for the lead.
