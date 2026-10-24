# SHOBS-SCHED — Shiloh Hill Observatory Scheduler

SHOBS-SCHED plans each night's observing — **transits**, **variable stars** and **campaigns** (stars or fields you
visit on many nights) — for one night or a rolling seven nights, and exports each night for ASIAIR or NINA (a target
file to import) plus a printable plan card.

It works on its own. If [SHOBS-P](https://github.com/SHOBS-S/SHOBS-P) is installed on the same computer,
SHOBS-SCHED reads its site and equipment settings so you type them once (it never changes SHOBS-P's files).

## What it does

1. **Setup** — your site (latitude, longitude, elevation, time zone), optics and camera, mount (German equatorial
   with meridian flips, or alt-az), minimum altitude, maximum airmass, darkness, the horizon profile from a site
   survey, and the weather source.
2. **Targets** — three boxes, each with its own scroll bar: **Campaigns** (long-running stars and fields with
   priorities, weekly goals and rules such as "no data while a planet transits"), **Transits** (ephemerides from
   the NASA Exoplanet Archive, the most precise published one for the coming weeks, with the predicted mid-transit
   uncertainty) and **Variables** (light elements from the AAVSO VSX, or VizieR's copy of it); drag the bar between
   Transits and Variables to share the room. Each row shows its period and its next event in the planned nights
   (a transit's night, ingress, mid and egress; a variable's timed minimum or maximum, or "any time"), soonest
   first. **Find suggested transits…** and **Find suggested variables…** (eclipsing minima, RR Lyrae maxima, short
   pulsators with a whole cycle in the dark, and long-period fillers from VSX, by type, period, brightness and
   amplitude; up to 25 of each by default) add other good targets unticked into the same boxes; the **Plan** tick
   (first column) decides what is built, and **Keep** stops a suggestion being replaced by the next search. Add any
   target by name.
3. **Schedule** — builds each night: fixed-time targets first (transits with baseline, timed minima/maxima), then
   campaigns and any-time variables by priority at their best airmass, with twilight, the Moon, your horizon,
   meridian flips (start 10–15 min before the meridian and let the mount flip), overheads and the dew forecast.
   Each night shows a night plot (every block's airmass through the night over the twilight shading, with the
   Moon, transits and meridian flips, and the hourly weather above it; click a block or a table row to pick it
   out), the nightly table, flats and darks to shoot, what didn't fit and why (with *Plan this instead*), and free
   time. Mark each block Done, Partial or Clouded out (the blank entry clears a mark); **Re-review** rolls the week forward and puts missed targets back in the pool.
4. **OBSPlan** — the plan card itself, on screen: print it (with or without the night plots), or send one night at a
   time to **ASIAIR** (a CSV for its Plan import, saved to Downloads or a folder that syncs to your tablet/phone) or
   to **NINA** (a target list for the Sequencer's *Import targets*).

All times are shown on your **site's clock**, whatever time zone the computer is set to, and labelled
"Local time (UTC−4)" (on a daylight-saving night "Local time (UTC−4 → UTC−5)").

Setup can also **read your camera from a FITS frame** (camera, gain, e⁻/ADU, pixel size, focal length, binning,
camera angle), and every Setup value is tagged with where it came from: *from SHOBS-P*, *from FITS* or *set here*.

## Install (Windows)

1. Unzip anywhere (for example `C:\Astro\shobs_sched`).
2. Double-click **Create shortcuts.bat**. It finds a Python 3.11 or newer with Tkinter (python.org, Anaconda, …),
   gives SHOBS-SCHED its own environment in `%LOCALAPPDATA%\SHOBS-SCHED`, installs `numpy`, `tzdata` and
   `matplotlib`, and puts a SHOBS-SCHED icon on the Desktop and in the Start menu.
3. **Run self-test.bat** checks the install; `run_windows.bat` starts the app with a console window beside it.

Upgrading: after unzipping a new version over the old one, double-click **Create shortcuts.bat** again (it installs
any new packages, e.g. matplotlib for the night plot).

Settings live in your home folder: `.shobs_sched_settings.json`, `.shobs_sched_targets.json` and the
`.shobs_sched` folder (saved schedule, cache, log).

## Data sources

NASA Exoplanet Archive (ephemerides), AAVSO VSX and VizieR B/vsx (variable stars), SIMBAD (names and positions),
Open-Meteo (weather). Sky brightness from the Moon: Krisciunas & Schaefer (1991, PASP 103, 1033). Sun and Moon
positions from the Astronomical Almanac's low-precision formulas.

## Credits

**Credits and sources** (the sidebar button) lists every catalog, service, design and library with the
acknowledgement each asks for, and can save the list as text.

The Schedule tab's night plot follows the design of the airmass plot in **TAPIR**
(`plot_airmass.cgi`) by Eric Jensen, Swarthmore College — GPL-3; Jensen, E. 2013, Astrophysics Source Code
Library, [ascl:1306.007](https://ascl.net/1306.007); <https://github.com/elnjensen/Tapir>: airmass and elevation
axes, twilight shading with the times across the top, the nautical-twilight ± 1.5 h time span, and the Moon's line
growing with its phase. SCHED's plot is its own code (matplotlib); no TAPIR code is copied.

## License

Copyright © 2026 Shiloh Hill Observatory.

SHOBS-SCHED is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY. See the
[LICENSE](LICENSE) file for details.
