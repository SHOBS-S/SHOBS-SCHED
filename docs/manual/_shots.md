# Screenshots (SHOBS-SCHED manual, 1.4.0 edition)

Retaken for 1.2.0 on 31 Oct 2026 and for 1.3.0 (maximized window, resized to 2200 px wide). `02-create-shortcuts.png`
is kept from 1.1.0: the Windows setup did not change.

## 1.4.0: what must be reshot

House rule: never reuse an image when its page or dialog changed; reuse is fine when nothing on it changed. Every
shot of the **main window** shows the sidebar's version (**Version 1.3.0**, now **Version 1.4.0**), so all of them
are reshot on 1.4.0. On top of that, these pages and dialogs changed in 1.4.0 (see `_changes_140.md`).

**For every 1.4.0 shot:** the example site as below, and **no SHOBS-FLUX profile** (no `~/.shobs` folder, or start
SHOBS-SCHED with `SHOBS_DATA_HOME` set to an empty folder), so the verdicts are the rough ones with a "?" that most
users will see first. Never use the real observatory's SHOBS-FLUX profile (its telescope and name). Setup's
**Photometry noise** box stays empty.

### RESHOOT

| File | Why | Set up before taking it |
|---|---|---|
| `img/01-window-parts.png` | Sidebar version 1.4.0; the Targets page has the Verdict column. | Targets page as for `05-targets-page.png` (or the Schedule page), whole window. |
| `img/02-first-start.png` | Sidebar version; the new **Photometry noise (exposure by S/N)** box at the bottom of the left column. | Fresh settings folder, no SHOBS-P, no SHOBS-FLUX profiles. The box shows **Automatic**, *No SHOBS-FLUX profile matches the telescope and camera above …*, empty fields tagged *estimate* (no values yet: no telescope). |
| `img/03-setup-saved.png` | Sidebar version; the Photometry noise box (its *estimate 19.76*-style tags now show values). | Example Observatory filled in as in 1.3.0 (40.0, −100.0, America/Chicago, 250 mm), Photometry noise left empty, Save → "✔ Saved." |
| `img/03-targets-two.png` | Sidebar version; the **Verdict** column (e.g. *Detectable?*). | WASP-12 b and DY Peg ticked; "✔ Added DY Peg (VSX)." |
| `img/03-night-tab.png` | Sidebar version; Notes lead with the verdict; DY Peg's flip note now reads *… Pre-flip frames carry no weight in the night mean.*; the transit's Exposure *(est.)* (Longest safe without FLUX). | Build schedule with the two targets; "✔ Built … nights"; ideally the transit row selected so its verdict note shows in full. |
| `img/04-setup-page.png` | Sidebar version; the new Photometry noise box (bottom of the left column). | Whole Setup page, two columns, example values, darks on hand `60, 120, 180`, Moon distance **By Moon phase**; Photometry noise empty (Automatic, the "No SHOBS-FLUX profile matches" line, seven *estimate …* tags). |
| `img/04-fits-summary.png` | Sidebar version; the left column is taller (new box), so the page around the summary moved. | Read from a FITS frame… with the example frame (no ROTATOR → ⚠), as in 1.3.0. |
| `img/05-targets-page.png` | Sidebar version; **Verdict** column in all three boxes; Campaigns' new **Type** and **Period** columns; the details line now names the exposure mode and the verdict. | As in 1.3.0 (ExoClock **Alert** + **High**, ExoFOP **1–2**, HAT-P-32 b ticked and selected, the HD 219134 campaign, the variables), **Hide out of reach** unticked so a mix of verdicts shows (*Detectable?*, *Marginal?*, *Out of reach?*). If possible add one campaign that came from a VSX variable (move a variable to Campaigns in Edit) so Type and Period show values beside HD 219134's *—*. |
| `img/05-edit-dialog.png` | New **Exposure** section between Target and Observing (the Observing part lost its Exposure row). | Edit HD 219134 (campaign), planets b and c ticked, as before. The dialog scrolls: show the top (Target, Exposure, Observing) and as much of No data while a planet transits as fits. |
| `img/05-find-transits.png` | New **Hide out of reach** tick with its gray words under the Priority rows. | Open the window with ExoClock **Alert** and **High** ticked, ExoFOP **1–2**, and **Hide out of reach** ticked. |
| `img/06-night-tab.png` | Sidebar version; Notes lead with the verdict; Exposure shows *(S/N)* or *(est.)*; flip notes by type. | As in 1.3.0. Before building, set one target (e.g. HAT-P-32 b) to **Best precision** in Edit so the table shows a *… (S/N)* exposure beside the *(est.)* ones. Select the transit row so its notes show in full under the table. |
| `img/06-night-tab-lower.png` | Sidebar version; Notes lead with the verdict; the transit's flip notes. | As in 1.3.0 (WASP-12 b with *(flip)* and *⚠ Meridian flip during the transit*), the transit selected. |
| `img/07-obsplan-page.png` | Sidebar version; the Exposure cell has the verdict word under it (green / yellow tint). | As in 1.3.0, after the 06 build (so one exposure reads *(S/N)*). |
| `img/07-plan-card-print.png` | The printed card's Exposure column now has the verdict in bold under the exposure (and *(S/N)*). | Print plan card with Include night plots, as in 1.3.0; capture so the nightly table's Exposure column is readable. |
| `img/08-busy-bar.png` | Sidebar version in the crop. | Start **Find suggested variables…** and capture the lower left while the green bar runs. |
| `img/08-busy-bar-find.png` | Sidebar version in the crop. | **Find suggested transits…** in its second pass ("…: the best with their most precise ephemeris… N of M"). |
| `img/09-credits-page.png` | Header now "SHOBS-SCHED 1.4.0"; new **APASS DR9 (via VizieR)** entry; SHOBS-FLUX, Scintillation and Sun-like colours entries; SHOBS-P's concept DOI. | Credits and sources page scrolled so How to cite and CATALOGS AND SERVICES (down to APASS DR9) show; ideally after **Copy**. |

### NEW shots

| File | Chapter | Set up before taking it |
|---|---|---|
| `img/04-photometry-noise.png` | 4 | Close-up of Setup's **Photometry noise (exposure by S/N)** box after `03-setup-saved` (example values, no profile): **Automatic**, the "No SHOBS-FLUX profile matches …" line, the seven empty fields with their *estimate …* tags (zero point *estimate 19.76* or similar), the gray hint. |
| `img/05-edit-exposure.png` | 5 | Edit **WASP-12 b** (transit) on the example site, no profile, nothing chosen (so **Longest safe**). Crop to the Exposure section: the three buttons, the gray hint, Band *Green channel (TG)* with *estimate (no FLUX profile)*, the magnitude line, the readout (Exposure … (Longest safe) · predicted peak …; Precision … per 10-minute bin), the noise budget bar and words, the transit line, *S/N …  Detectable?* in green with its reason, the gray line (airmass, *Rough estimate …*, *Longest safe until SHOBS-FLUX measures this system …*), the precision vs exposure plot with the green circle, and the Comps line after **Check comps** (*✔ Checked with APASS.* under it). |

Optional (not referenced by a chapter): the Edit dialog's own green busy bar during **Check comps** (*Asking APASS
(VizieR) for comparison stars…*); a variable's Exposure section (*Range … mag (VSX; … % in flux), against one
10-minute bin*); a *Saturates?* readout (a bright star at **My exposure** 60 s, the Exposure line yellow, the plot's
yellow and pink shading).

### REUSE (nothing on them changed in 1.4.0)

| File | Why it can be reused |
|---|---|
| `img/02-create-shortcuts.png` | The Windows setup console did not change. |
| `img/05-find-variables.png` | The Find suggested variables window did not change in 1.4.0 (no sidebar in the shot). |
| `img/05-remove-dialog.png` | The Remove targets window did not change (no sidebar in the shot). |

## Every shot

Every image the manual refers to, with the window and state it should show. Take them on Windows, with the
example site (Example Observatory, latitude 40.0, longitude −100.0, America/Chicago, a 0.25 m telescope). Never use
the real observatory's coordinates or name. Save as PNG in `docs/manual/img/` with exactly these names.

| File | Chapter | What it should show |
|---|---|---|
| `img/01-window-parts.png` | 1 | The whole main window on the Targets or Schedule page, with the sidebar ("Shiloh Hill / Observatory / Photometry / Scheduler", "(SHOBS-SCHED)", the tagline, STEPS 1–4, Credits and sources, Version 1.4.0), the white page area with its title and subtitle, and the status strip at the bottom (status line and credits line). Ideally labeled with arrows: sidebar, page title, page, status strip, busy-bar position. |
| `img/02-create-shortcuts.png` | 2 | The black console window of Create shortcuts.bat at the end of a first run: "Looking for a Python…", the "usable:" line, "Setting up SHOBS-SCHED's own Python environment…", the package check, and "Done. Start SHOBS-SCHED from the Desktop icon or the Start menu." |
| `img/02-first-start.png` | 2 | SHOBS-SCHED on first start with no SHOBS-P installed: the Setup page, empty fields, the top line "Enter your site and equipment once (or read the camera from a FITS frame)…", the guessed time zone with its hint, and the Photometry noise box at the bottom of the left column. |
| `img/03-setup-saved.png` | 3 | The Setup page filled in for Example Observatory (40.0, −100.0, America/Chicago, 250 mm aperture), with "✔ Saved." beside the green Save button, and "set here" tags beside the typed fields. |
| `img/03-targets-two.png` | 3 | The Targets page with WASP-12 b in the Transits box and DY Peg in the Variables box, both ticked ☑, their event columns and Verdict filled; the result line "✔ Added DY Peg (VSX)." beside Add. |
| `img/03-night-tab.png` | 3 | The Schedule page after Build schedule: a night tab selected, the night's line (dark, Moon, weather), the night plot with a green transit block and a navy variable block, and the nightly table with both rows (their Notes led by the verdict); "✔ Built … nights" in the message line. |
| `img/04-setup-page.png` | 4 | The whole Setup page in its two-column layout: Mount and limits (with Moon distance at least: By Moon phase, Fixed, the four By phase boxes and the hint) and Weather on the left with Site; Telescope and camera, Horizon profile (a few az/alt lines) and Suggestions and darks (with darks on hand) on the right; Photometry noise (exposure by S/N) at the bottom of the left column; the source line at the top; Save at the bottom. |
| `img/04-photometry-noise.png` | 4 | The Photometry noise (exposure by S/N) box alone: Instrument profile **Automatic**, the status line, the seven fields with their tags, the gray hint. |
| `img/04-fits-summary.png` | 4 | The Telescope and camera box right after Read from a FITS frame…: the "⚠ Read the header of … Press Save to keep these values." summary (⚠ because the example frame has no ROTATOR) with its "Changed:" list (old → new), and "from FITS" tags beside the filled fields. |
| `img/05-targets-page.png` | 5 | The Targets page with several targets in all three boxes (a campaign with "This week" progress, transits with Night/Ingress/Mid/Egress, variables with "min …"/"any time"), a mix of ticked and unticked (gray) rows, the Verdict column (and the campaigns' Type and Period), one row selected with its details line (exposure mode and verdict), the Nights row, and the buttons at the bottom. |
| `img/05-edit-dialog.png` | 5 | The Edit dialog for a campaign on a planet-host star (a made-up or public example such as HD 219134): Target, the Exposure section, and Observing fields (Moon at least: "° away (empty = Setup's default)"), the "No data while a planet transits" part with the Archive result line, the ticked transiting planets, a grayed non-transiting planet, the window list, and Notes. |
| `img/05-edit-exposure.png` | 5 | The Edit dialog's Exposure section for a transit (WASP-12 b): buttons, Band and tag, magnitude line, readout, noise budget bar, transit line, S/N and verdict, gray line, precision vs exposure plot, Comps line and Check comps. |
| `img/05-find-variables.png` | 5 | The Find suggested variables window: Types ticks (first four ticked), Include uncertain types, Period (days/hours), Brightness, Amplitude at least, Show at most, the Nights line, Search and Cancel. |
| `img/06-night-tab.png` | 6 | One night tab in detail: the night's line, a ⚠ dew line, the weather strip (cloud bars, temperature and dew-point lines, spread numbers, the dashed dew-onset line), the night plot with twilight shading, the Moon's dashed gold line, a transit with its green band and lighter baselines, a ▼ flip marker, label boxes; the nightly table (Exposure with *(S/N)* / *(est.)*, Notes led by the verdict) with one row selected (highlighted on the plot); the Lock / Remove / Move to night… / Edit times… / Status row; and the Flats and darks, Didn't fit (with a Plan this instead button) and Free time boxes. |
| `img/07-obsplan-page.png` | 7 | The OBSPlan page: the night ticks, Print plan card with Include night plots ticked, the position-angle line, the ASIAIR export folder line, and one night of the on-screen card (date heading with Export to ASIAIR, Export to NINA and Copy ASIAIR list; the facts line; the table, with the verdict under each exposure; Flats/Darks/Free time lists), with an "✔ ASIAIR: saved …" result line and its Open folder / Open in Notepad / Show file contents buttons. |
| `img/07-plan-card-print.png` | 7 | The printed plan card open in a web browser: "Photometry plan, <dates>" title, the site line (Example Observatory, 40.000° N, 100.000° W), one night's facts and weather line, its night plot (print style), and the start of the nightly table (the verdict in bold under each exposure). |
| `img/08-busy-bar.png` | 8 | A close-up of the lower left of the main window during a long job: the green busy bar on the navy status strip with its words beside it (Find suggested variables; a build on a fast PC is over before the bar can be caught). |
| `img/09-credits-page.png` | 9 | The Credits and sources page: "SHOBS-SCHED 1.4.0 © 2026 Shiloh Hill Observatory", the license line, How to cite SHOBS-SCHED (the citation with doi:10.5281/zenodo.23674791) and Copy, the list (CATALOGS AND SERVICES first, with ExoClock, ExoFOP-TESS and APASS DR9), Save credits as text… and Back. |
| `img/05-find-transits.png` | 5 | The Find suggested transits window: its text naming Setup's "Suggestions and darks" box, the values and Setup's Moon distance, Show at most, the Nights line, the Priority rows (ExoClock priority ticks, ExoFOP SG1 priority), Hide out of reach with its gray words, Search and Cancel. |
| `img/05-remove-dialog.png` | 5 | The app's own Remove targets window: "Remove DY Peg from the list? …" with Remove and Cancel. |
| `img/06-night-tab-lower.png` | 6 | A second night: a block with "stop by … at the latest", a transit whose Meridian reads "(flip)", its "⚠ Meridian flip during the transit" caution, the Notes led by the verdict, the tab marked ⚠. |
| `img/08-busy-bar-find.png` | 8 | The green busy bar during Find suggested transits, showing the stage and its count ("…: the best with their most precise ephemeris… 10 of 37"). |
