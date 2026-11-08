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
2. Double-click **Run self-test.bat** (on a Mac, **Run self-test.command**; on Linux, type
   `bash start_linux.sh selftest` in a terminal in that folder). A console window opens.
3. Wait for it to finish (usually under a minute).
4. Read the last result line. It should be `ALL PASSED`.

The sections:

- **1. Environment**: your Python, NumPy, Matplotlib (with its Tk part, for the night plot), Tk, the time zones, and the
  time-zone guess.
- **2. Sky and events**: dark times, transit times, a no-transit window and a meridian time, against a known week of
  hand-made plans (each within a few minutes).
- **3. Planner**: those five nights built again (every target placed, no overlaps), and a seven-night build.
- **4. Weather and export**: a recorded forecast, the nightly table, the ASIAIR list and the plan card.
- **4b. Exposure by S/N** (new in 1.4.0): the shared `shobs_profile.py` file is the version SHOBS-SCHED was built
  with; with no SHOBS-FLUX profile the numbers are a rough estimate, and the cold start predicts the same peak as
  1.3.0; the example SHOBS-FLUX profile is found; rough numbers keep a transit on Longest safe; TOI-2046 b by S/N
  gives *Detectable?*; a saturating exposure says *Saturates?*; the Moon on a measured sky; Longest safe on rough
  numbers is 1.3.0's rule; a zero point typed in Setup gives Best precision and one peak model; the flip notes by
  type; SHOBS-P's DOI.
- **5. Window**: the app opened on a temporary folder with the network switched off: every page, a build, and the
  ASIAIR list copied. Without a screen it says `SKIP`.
- **6. Live services (information only)**: whether the NASA Exoplanet Archive, SIMBAD, VizieR (VSX copy), SIMBAD
  mirror (IDIA), VizieR mirror (IDIA), AAVSO VSX and Open-Meteo answer. Each is `OK` or `NOTE`; never a failure.
  A `NOTE` says `did not answer (…); SCHED falls back to its saved copies`. Then the two Priority lists of **Find
  suggested transits…**: `OK    ExoClock planet list answered: … planets` and `OK    ExoFOP TOI table answered:
  … TOIs (not FP/FA)`, or a `NOTE` ending `Find suggested transits searches without it`. (The ExoFOP table is large;
  this check can take a minute.)

Each check is `PASS` or `FAIL`. If the end says `FAILED: N check(s): …`, the lines marked `FAIL` say which.

**The log** goes to `selftest_log.txt` in the program folder and a copy to `SHOBS-SCHED_selftest_log.txt` in your
Downloads folder. Attach it to any problem report. On a Mac, **Run self-test.command** says where both are when it
finishes. If it says *The self-test did not pass or could not run*, leave the window open and copy the message above
it; when no Python could be set up, the test never ran and there is no log, so that message is all there is.

## The program's log

SHOBS-SCHED started from its icon has no console window. It writes what it did, and every error in full, to
`sched.log` in the `.shobs_sched` folder in your user folder (`C:\Users\<you>\.shobs_sched\sched.log`), each line
with the date and time. Lookups, builds, saved files and fallbacks to saved copies are all there. When the log grows
past 2 MB it is renamed `sched.log.1` and a new one starts.

If something goes wrong inside the window, the status strip says *✖ Something went wrong: … Details are in
…\sched.log.* Open the log, and look at the last lines.

The folder name starts with a dot. On Windows, type `%USERPROFILE%\.shobs_sched` into the Explorer address bar to
open it. On Linux or a Mac it is `~/.shobs_sched`.

**On a Mac, when SHOBS-SCHED does not start.** If **Start SHOBS-SCHED.command** fails, its Terminal window stays
open and says *SHOBS-SCHED did not start. Leave this window open and copy the message above.* It then names the two
logs: *The package install log is ~/Library/Application Support/SHOBS-SCHED/pip_log.txt; the program's own log is
~/.shobs_sched/sched.log.* Send both with your report. (In Finder, **Go → Go to Folder…** and paste the path.)

## "Night plot needs matplotlib"

If a night tab shows *⚠ Night plot needs matplotlib — …* where the plot should be, Matplotlib is not installed in
SHOBS-SCHED's environment (for example after updating from a version that did not need it). The message names the
fix for your system:

| System | The message ends | What to do |
|---|---|---|
| Windows | *— run Create shortcuts.bat again* | Close SHOBS-SCHED, double-click **Create shortcuts.bat** (it installs any missing package), and start SHOBS-SCHED again. |
| Mac | *— start SHOBS-SCHED again with Start SHOBS-SCHED.command (it installs what is missing)* | Close SHOBS-SCHED and double-click **Start SHOBS-SCHED.command**. |
| Linux | *— start SHOBS-SCHED again with bash start_linux.sh (it installs what is missing)* | Close SHOBS-SCHED and type `bash start_linux.sh` in a terminal in the program folder. |

The self-test's Environment section says the same (*matplotlib does not load (…); run Create shortcuts.bat again*,
or the Mac or Linux start file).

If the message instead says *matplotlib is installed but its Tk backend does not load (…)*, Matplotlib cannot draw in
a window with this Python. Do the step above first; if the message stays:

- **Windows:** reinstall Python from python.org with **tcl/tk and IDLE** ticked.
- **Mac:** install Python 3.13 from python.org (it includes Tk).
- **Linux:** install your Python's Tkinter package (e.g. `python3-tk`).

If the message reads *⚠ Night plot needs matplotlib (nightplot.py did not load): install SHOBS-SCHED again*, a
program file is missing or damaged: unzip SHOBS-SCHED again.

Everything else works without the plot, and **Print plan card** simply prints without plots.

## When the online services do not answer

SHOBS-SCHED asks five services, each with its own fallback. VizieR and SIMBAD each have mirrors that serve the same
data, and SHOBS-SCHED asks them in turn:

| Service | Used for | When it does not answer |
|---|---|---|
| NASA Exoplanet Archive | Planets, ephemerides, the transit list, a star's planets | The saved copy is used (with a ⚠ saying how old). A planet never looked up before cannot be added until it answers. |
| AAVSO VSX | Variable stars | VizieR's copy of VSX is asked instead; if that fails too, the saved copy. |
| VizieR (CDS) | VSX's copy; the variable-star search | VizieR's other service at CDS is asked, then its mirrors at the CfA and at IDIA (Cape Town); if none answers, the saved copy, if any. |
| SIMBAD (CDS) | Positions of other stars | Its mirror at IDIA (Cape Town) is asked; if that fails too, the saved copy. With neither, a planet host star's position is taken from the NASA Exoplanet Archive. |
| Open-Meteo | The forecast | The last saved forecast (with its age); else the plan is built without weather. |

Every failure is said in plain words, with the reason, for example *service unavailable, HTTP 503*, *no answer within
30 s*, *no network connection (name lookup failed)*, or *the service's firewall (Cloudflare) blocked the query*. When
SIMBAD and its mirror both fail, both reasons are given: *…; its mirror: …*.

SHOBS-SCHED tells a service that does not answer from one that does not know the name:

- *SIMBAD does not know HD 12345. Check the spelling, or type the coordinates by hand.* SIMBAD answered: check the
  name.
- *SIMBAD did not answer (…) and there is no saved copy for HD 12345. Try again later.* SIMBAD (and its mirror) could
  not be reached, or reported a problem on its side: the name may be fine.

The plan itself never needs the network: once your targets are in the list, you can build offline.

### ExoClock and ExoFOP (Find suggested transits' Priority lists)

These two are asked only when you choose a **Priority** in **Find suggested transits…** (chapter 5), and each list
is saved for a week.

- **The server does not answer, or the list needs a login now.** The search goes on without that list and says so
  with a ⚠ (*⚠ ExoFOP did not answer (…) — searched without its priorities.*). With a saved copy, that copy is used
  (*…; using the saved copy from 3 days ago*). Nothing for you to fix: try again later. ExoFOP's table is large
  (about 4 MB) and its server can be slow; SHOBS-SCHED waits up to two minutes for it.
- **✖ … answered, but its list is not in the form SCHED knows (a format change or a SCHED bug; please report it).**
  The site changed its list, or SHOBS-SCHED has a bug. This is never hidden, because a quietly wrong list would give
  wrong suggestions. Please report it (below), with the log. Until it is fixed, untick that list's ExoClock levels
  (or set ExoFOP SG1 to **Don't use**) and the search runs without it.

## The cache

Saved answers from the services are kept in `.shobs_sched\cache` in your user folder, one small file per lookup.

| What | Fetched again after |
|---|---|
| Planet ephemerides and the transit list | 7 days |
| The ExoClock and ExoFOP Priority lists | 7 days |
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
| *✖ No dark time tonight: the Sun gets no lower than …* in summer. | The Sun never reaches −18°. | If the line goes on *nautical darkness (Sun below −12°) is possible if you allow it in Setup*, choose **Nautical (Sun −12°)** in Setup. |
| Exposures look much too short or too long. | Missing or wrong telescope or camera values, or the linear limit. | Check Setup; or choose **My exposure** in the target's Edit dialog and type your own. |
| Every verdict ends in "?" (*Detectable?*, *Out of reach?*). | No measured zero point: no SHOBS-FLUX profile was found, or it has not measured one yet. | That is expected without SHOBS-FLUX. Check Setup's **Photometry noise** line; choose your profile there if Automatic does not find it. Typing a zero point you trust also removes the "?". |
| Transits are on **Longest safe**, not **Best precision**. | Best precision is a transit's default only once the zero point is measured by SHOBS-FLUX or typed in Setup's Photometry noise box. | Type a zero point in Setup, or choose **Best precision** for the target in its Edit dialog. |
| Setup says *⚠ Several SHOBS-FLUX camera profiles fit …* | SHOBS-FLUX has a camera profile for more than one gain, and Setup has no **Gain setting**. | Choose the profile in **Instrument profile**, or type the **Gain setting**, and **Save**. |
| Setup says *⚠ A newer SHOBS-FLUX wrote … profiles here* | SHOBS-FLUX is newer than this SHOBS-SCHED. | Install the newest SHOBS-SCHED. |
| A target is **Out of reach** but you have measured it before. | A rough zero point, or a wrong seeing or sky value, can make the S/N too low; or the transit's depth or the star's VSX range is wrong. | Check the Edit dialog's readout and Setup's Photometry noise values. The verdict is advice; you can still plan the target. |
| The window does not open from the icon. | Python or a package problem. | Start **run_windows.bat** and read the console. |
| Setup changes "did not stick". | They were saved, but the schedule was built before. | Press **Build schedule** (the ⚠ line says so). |
| **Find suggested transits…** finds few planets, or none. | The filters in Setup's **Suggestions and darks** box (host V range, depth, baseline, Moon-brightened sky) are tight for your nights, or **Hide out of reach** is ticked. The Find suggested transits pop-up shows the values it uses. | Widen them in Setup and **Save**, or untick **Hide out of reach**, then Find again. |
| **Darks to shoot** lists exposures you already have. | **Darks you already have** in Setup's **Suggestions and darks** box is empty or missing them. | Type them there (for example `60, 120, 180`) and **Save**, then **Build schedule**. |
| On untick, **Remove**, **Build schedule** or **Re-review schedule**, a question *Blocks set by hand* comes up. | A target that is no longer planned still has blocks you locked or set by hand; a rebuild keeps those. | **Remove** takes the blocks out too; **Cancel** keeps them in the schedule, with a note (*⚠ This target is not ticked to plan* or *⚠ This target is no longer in your list*) that does not mark the night's tab. |
| A target often says *Moon too close* in **Didn't fit**, even with the Moon well away from where you would worry. | Setup's **Moon distance at least** (by phase) is stricter than you need, or the target's own **Moon at least** is. The brackets name the limit used. | Lower the by-phase values (or choose **Fixed** or **Off**) in Setup, or give the target its own **Moon at least**, which wins. |
| **Find suggested transits…** finds fewer planets than in 1.2.0. | 1.3.0 also keeps suggestions clear of the Moon by Setup's **Moon distance at least**; or a Priority is chosen, so only listed planets are searched. | Check the window's text (it names the Moon distance) and the Priority rows. |
| Every equatorial block suddenly flips at its start. | **Tracks past the meridian** was saved as 0 by an older version (an empty field once saved 0). | Type your mount's real value (5 by default) and **Save**. Since 1.3.0 an empty field keeps the saved value. |
| SHOBS-P's site does not appear. | The fields were typed in SHOBS-SCHED (tagged *set here*), which win. | Type SHOBS-P's value back into the field and **Save**: it follows SHOBS-P again. |

## Starting afresh

**The Python environment.** If the install gets into a muddle, delete the `SHOBS-SCHED` folder in `%LOCALAPPDATA%`
and run **Create shortcuts.bat** again. On a Mac, delete `~/Library/Application Support/SHOBS-SCHED`; on Linux,
`~/.local/share/SHOBS-SCHED`; then start SHOBS-SCHED with its start file, which sets it up again. Your settings,
targets and schedule are not in that folder, so they are kept.

**Your data.** To start with an empty Setup, target list or schedule, close SHOBS-SCHED and rename (or delete) the
file in your user folder: `.shobs_sched_settings.json`, `.shobs_sched_targets.json`, or `.shobs_sched\schedule.json`.
Renaming keeps a copy in case you want it back.

## Reporting a problem

Include:

- what you did, what you expected, and what happened, with a screenshot;
- your computer's system (Windows, Linux or macOS, and its version);
- `SHOBS-SCHED_selftest_log.txt` from your Downloads folder (run the self-test first);
- the last lines of `.shobs_sched\sched.log`;
- the version, from the bottom of the sidebar.

---

Previous: [9. Credits and citing](09-credits-and-citing.md) · Next: [11. Glossary](11-glossary.md)
