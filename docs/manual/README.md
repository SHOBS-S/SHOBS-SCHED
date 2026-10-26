# SHOBS-SCHED User's Manual (version 1.1.0)

**Shiloh Hill Observatory Photometry Scheduler (SHOBS-SCHED)**
*Plans each night's photometry: transits, variable stars and campaigns.*

> First edition, October 2026. Written for SHOBS-SCHED 1.1.0 and checked against the program; the screenshots
> are real captures from 1.1.0 on Windows, using a made-up example site. Each chapter is its own file in this
> folder; this page is the table of contents. A PDF of the whole manual is built with
> `python tools/build_manual_pdf.py`.

## Part 1: Getting started

1. **[What SHOBS-SCHED is](01-what-it-is.md)**: a photometry scheduler (and what it is not), the four pages, how it
   works with SHOBS-P, the site's clock.
2. **[Installing and starting](02-installing.md)**: Python, **Create shortcuts.bat**, the self-test,
   **run_windows.bat**, Linux and the Mac, where your settings, targets and schedule are kept, updating.
3. **[Your first night, step by step](03-first-night.md)**: Setup, a transit and a variable star, Build schedule,
   the plan card, and the night's list for ASIAIR.

## Part 2: The four pages

4. **[Setup](04-setup.md)**: site and time zone, telescope and camera, reading a FITS frame, mount and meridian
   flips, limits and darkness, the horizon profile, weather and dew, the source tags (*from SHOBS-P*, *from FITS*,
   *set here*), and the planner settings you do not see.
5. **[Targets](05-targets.md)**: the Campaigns, Transits and Variables boxes, the **Plan** tick, **Priority**,
   **Keep**, adding by name, the **Edit** dialog (known planets and no-transit windows), **Find suggested
   transits…** and **Find suggested variables…**, the next-event columns, ephemeris precision.
6. **[Schedule](06-schedule.md)**: building, the night tabs, the night plot, the nightly table, locking and moving
   blocks, flats and darks, what didn't fit and **Plan this instead**, free time, weather and dew, marking **Done**,
   **Partial** and **Clouded out**, **Re-review**, weekly goals, the out-of-date warning.
7. **[OBSPlan: the plan card, ASIAIR and NINA](07-obsplan.md)**: printing (with or without the night plots),
   **Export to ASIAIR**, **Copy ASIAIR list**, **Export to NINA**, the camera angle.

## Part 3: Reference

8. **[The busy bar and messages](08-busy-bar-and-messages.md)**: the busy bar in the status strip, the ✔ / ⚠ / ✖
   result lines, the status strip, common messages.
9. **[Credits and citing](09-credits-and-citing.md)**: the Credits and sources page, the license.
10. **[Troubleshooting](10-troubleshooting.md)**: the self-test log, the program log, the missing-matplotlib
    message, network failures, the cache, starting afresh.
11. **[Glossary](11-glossary.md)**: airmass, baseline, ephemeris, linear limit, meridian flip, O − C, and the other
    photometry words this manual uses.

---
*Screenshots taken from SHOBS-SCHED 1.1.0 on Windows.*
