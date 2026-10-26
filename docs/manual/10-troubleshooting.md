# 10. Troubleshooting

This chapter covers:

- the self-test and its log,
- the program's log,
- the night plot that says Matplotlib is missing,
- when the online services do not answer,
- the cache,
- typical problems and their fixes,
- starting afresh,
- how to report a problem.

## The self-test

The self-test checks SHOBS-SCHED with the same Python the shortcuts use. It changes nothing of yours: it works in a
temporary folder and deletes it at the end.

1. Open the SHOBS-SCHED program folder.
2. Double-click **Run self-test.bat**. A console window opens.
3. Wait for it to finish (usually under a minute).
4. Read the last result line. It should be `ALL PASSED`.

The sections:

1. **Environment**: your Python, NumPy, Matplotlib (with its Tk part, for the night plot), Tk, the time zones, and the
   time-zone guess.
2. **Sky and events**: dark times, transit times, a no-transit window and a meridian time, against a known week of
   hand-made plans (each within a few minutes).
3. **Planner**: those five nights built again (every target placed, no overlaps), and a seven-night build.
4. **Weather and export**: a recorded forecast, the nightly table, the ASIAIR list and the plan card.
5. **Window**: the app opened on a temporary folder with the network switched off: every page, a build, and the
   ASIAIR list copied. Without a screen it says `SKIP`.
6. **Live services (information only)**: whether the NASA Exoplanet Archive, SIMBAD, VizieR, AAVSO VSX and Open-Meteo
   answer. `OK` or `NOTE`; never a failure.

Each check is `PASS` or `FAIL`. If the end says `FAILED: N check(s): …`, the lines marked `FAIL` say which.

**The log** goes to `selftest_log.txt` in the program folder and a copy to `SHOBS-SCHED_selftest_log.txt` in your
Downloads folder. Attach it to any problem report.

## The program's log

SHOBS-SCHED started from its icon has no console window. It writes what it did, and every error in full, to
`sched.log` in the `.shobs_sched` folder in your user folder (`C:\Users\<you>\.shobs_sched\sched.log`), each line
with the date and time. Lookups, builds, saved files and fallbacks to saved copies are all there. When the log grows
past 2 MB it is renamed `sched.log.1` and a new one starts.

If something goes wrong inside the window, the status strip says *✖ Something went wrong: … Details are in
…\sched.log.* Open the log, and look at the last lines.

The folder name starts with a dot. Type `%USERPROFILE%\.shobs_sched` into the Explorer address bar to open it.

## "Night plot needs matplotlib"

If a night tab shows *⚠ Night plot needs matplotlib — run Create shortcuts.bat again* where the plot should be,
Matplotlib is not installed in SHOBS-SCHED's environment (for example after updating from a version that did not
need it).

1. Close SHOBS-SCHED.
2. Double-click **Create shortcuts.bat**. It installs any missing package.
3. Start SHOBS-SCHED again.

If the message instead says *matplotlib is installed but its Tk backend does not load (…)*, Matplotlib cannot draw in
a window with this Python. Run **Create shortcuts.bat** again; if the message stays, reinstall Python from
python.org with **tcl/tk and IDLE** ticked.

Everything else works without the plot, and **Print plan card** simply prints without plots.

## When the online services do not answer

SHOBS-SCHED asks five services, each with its own fallback:

| Service | Used for | When it does not answer |
|---|---|---|
| NASA Exoplanet Archive | Planets, ephemerides, the transit list, a star's planets | The saved copy is used (with a ⚠ saying how old). A planet never looked up before cannot be added until it answers. |
| AAVSO VSX | Variable stars | VizieR's copy of VSX is asked instead; if that fails too, the saved copy. |
| VizieR (CDS) | VSX's copy; the variable-star search | The search reports the failure; try later. |
| SIMBAD (CDS) | Positions of other stars | The saved copy, if any. |
| Open-Meteo | The forecast | The last saved forecast (with its age); else the plan is built without weather. |

Every failure is said in plain words, with the reason: *HTTP 503*, *timed out*, *no network connection*, or *the
Archive's firewall (Cloudflare) blocked the query*. The plan itself never needs the network: once your targets are in
the list, you can build offline.

## The cache

Saved answers from the services are kept in `.shobs_sched\cache` in your user folder, one small file per lookup.

| What | Fetched again after |
|---|---|
| Planet ephemerides and the transit list | 7 days |
| VSX and SIMBAD answers | 30 days |
| The variable-star search | 1 day |
| The forecast | 3 hours |

An old copy is still used, with a ⚠, when a service does not answer. You can delete the cache folder at any time
(with SHOBS-SCHED closed): everything is fetched again when needed. Your targets keep the values they were added
with; the cache only speeds up and backs up the lookups.

The latest printed card, `plan_card.html`, is kept in the same folder.

## Typical problems

| Problem | Likely cause | Fix |
|---|---|---|
| All times are off by some hours. | The time zone on the Setup page is wrong (or still the guess from a computer in another zone). | Pick your site's zone and **Save**. |
| Times are right, but the target is in the wrong part of the sky. | Longitude has the wrong sign. | West longitudes are negative (`-100.0`). |
| A transit you expected says *none in the next 7 nights*. | Not all of it is in the dark and above your limits on those nights. | Look at more nights, lower **Minimum altitude** if your sky allows, or check the horizon profile. |
| A target never gets a block. | Read its line under **Didn't fit, and why** on the night's tab. | The reason says what to change (limits, Moon distance, its own window, priority). |
| *✖ No dark time tonight* in summer. | The Sun never reaches −18°. | Choose **Nautical (Sun −12°)** in Setup. |
| Exposures look much too short or too long. | Missing or wrong telescope or camera values, or the linear limit. | Check Setup; or type your own exposure in the target's Edit dialog. |
| The window does not open from the icon. | Python or a package problem. | Start **run_windows.bat** and read the console. |
| Setup changes "did not stick". | They were saved, but the schedule was built before. | Press **Build schedule** (the ⚠ line says so). |
| SHOBS-P's site does not appear. | The fields were typed in SHOBS-SCHED (tagged *set here*), which win. | Type SHOBS-P's value back into the field and **Save**: it follows SHOBS-P again. |

## Starting afresh

**The Python environment.** If the install gets into a muddle, delete the `SHOBS-SCHED` folder in `%LOCALAPPDATA%`
and run **Create shortcuts.bat** again. Your settings, targets and schedule are not in that folder, so they are kept.

**Your data.** To start with an empty Setup, target list or schedule, close SHOBS-SCHED and rename (or delete) the
file in your user folder: `.shobs_sched_settings.json`, `.shobs_sched_targets.json`, or `.shobs_sched\schedule.json`.
Renaming keeps a copy in case you want it back.

## Reporting a problem

Include:

- what you did, what you expected, and what happened, with a screenshot;
- `SHOBS-SCHED_selftest_log.txt` from your Downloads folder (run the self-test first);
- the last lines of `.shobs_sched\sched.log`;
- the version, from the bottom of the sidebar.

---

Previous: [9. Credits and citing](09-credits-and-citing.md) · Next: [11. Glossary](11-glossary.md)
