# Screenshots (SHOBS-SCHED manual, 1.3.0 edition)

Retaken for 1.2.0 on 31 Oct 2026 (maximized window, resized to 2200 px wide). `02-create-shortcuts.png` is kept from 1.1.0: the Windows setup did not change.

## 1.3.0: what must be reshot

House rule: never reuse an image when its page or dialog changed; reuse is fine when nothing on it changed. Every
shot of the **main window** shows the sidebar's version (**Version 1.2.0**, now **Version 1.3.0**), so all of them
are reshot on 1.3.0. On top of that, these pages and dialogs changed in 1.3.0:

| File | RESHOOT? | Why | Set up before taking it |
|---|---|---|---|
| `img/01-window-parts.png` | **RESHOOT** | Sidebar version 1.3.0. | As in the table below. |
| `img/02-first-start.png` | **RESHOOT** | Sidebar version; Mount and limits now has **Moon distance at least** (By Moon phase / Fixed / Off, Fixed °, By phase crescent/quarter/gibbous/full) and its gray hint. | Fresh settings folder, no SHOBS-P; Mount and limits fully visible. |
| `img/03-setup-saved.png` | **RESHOOT** | Sidebar version; the new Moon distance rows in Mount and limits. | Example Observatory filled in, Moon distance left at **By Moon phase** (20/35/50/70), Save → "✔ Saved." |
| `img/03-targets-two.png` | **RESHOOT** | Sidebar version. | WASP-12 b and DY Peg ticked; "✔ Added DY Peg (VSX)." |
| `img/03-night-tab.png` | **RESHOOT** | Sidebar version (plans may also differ now that the Moon distance default applies). | Build schedule with the two targets; "✔ Built … nights". |
| `img/04-setup-page.png` | **RESHOOT** | Sidebar version; Mount and limits has the new **Moon distance at least** block (radio row, Fixed, the 2 × 2 By phase boxes, the hint "While the Moon is up, targets stay at least this far from it…"). | Whole Setup page, two columns, example values, darks on hand `60, 120, 180`; Moon distance **By Moon phase** with the defaults. |
| `img/04-fits-summary.png` | **RESHOOT** | Sidebar version; the Mount and limits box beside it is taller. | Read from a FITS frame… with the example frame (no ROTATOR → ⚠). |
| `img/05-targets-page.png` | **RESHOOT** | Sidebar version; suggested transits found with a Priority now show it in Source (*suggested · NEA · ExoClock high*, *suggested · ExoFOP · SG1 1*). | Run **Find suggested transits…** with ExoClock **Alert** + **High** ticked and ExoFOP **1–2**, so several Source cells carry priorities (ideally one *suggested · ExoFOP · SG1 …* TOI row); keep HAT-P-32 b ticked and selected, the HD 219134 campaign and the variables as before. |
| `img/05-edit-dialog.png` | **RESHOOT** | The **Moon at least** hint now reads "° away (empty = Setup's default)" (was "empty = no limit"). | Edit HD 219134 (campaign), planets b and c ticked, as before. |
| `img/05-find-transits.png` | **RESHOOT** | New **Priority** rows (the gray "Priority (optional): …" line, **ExoClock priority** ticks Alert/High/Medium/Low, **ExoFOP SG1 priority** drop-down with "(1 = highest; TESS planet candidates)"), and the text now ends "… and Setup's Moon distance (by phase, 20–70°)". | Open the window with ExoClock **Alert** and **High** ticked and ExoFOP **1–2** chosen. |
| `img/05-find-variables.png` | reuse | The window did not change in 1.3.0 (only its "no new variables" result text, not shown). | — |
| `img/05-remove-dialog.png` | reuse | Unchanged. | — |
| `img/06-night-tab.png` | **RESHOOT** | Sidebar version; Didn't fit reasons can now name the Moon limit. | As in the table below; if possible include a Didn't fit line "Moon too close (… at best; default for a … Moon …°)". |
| `img/06-night-tab-lower.png` | **RESHOOT** | Sidebar version. | As in the table below. |
| `img/07-obsplan-page.png` | **RESHOOT** | Sidebar version. | As in the table below. |
| `img/07-plan-card-print.png` | reuse | The printed card (in the browser) shows no version and its layout did not change. | — |
| `img/08-busy-bar.png` | **RESHOOT** | The busy bar is now **green** (#4caf50) on the navy status strip (house rule; was blue), and the sidebar version shows in the crop. | Start **Find suggested variables…** and capture the lower left while the green bar runs. |
| `img/08-busy-bar-find.png` | **RESHOOT** | Green busy bar (and sidebar version if in the crop). | **Find suggested transits…** in its second pass ("…: the best with their most precise ephemeris… N of M"). |
| `img/09-credits-page.png` | **RESHOOT** | Header now "SHOBS-SCHED 1.3.0"; new **How to cite SHOBS-SCHED** section with the citation (Zenodo, doi:10.5281/zenodo.23674791) and **Copy**; new ExoClock and ExoFOP-TESS entries in the list. | Credits and sources page, scrolled so How to cite and the start of CATALOGS AND SERVICES show; ideally after **Copy** ("✔ Citation copied to the clipboard."). |
| `img/02-create-shortcuts.png` | reuse | The Windows setup console did not change. | — |

Optional new shot (not yet referenced by a chapter): the **Blocks set by hand** question with its new text "…
Remove them too? (Cancel keeps them in the schedule, with a note that the target is not planned.)".

## Every shot

Every image the manual refers to, with the window and state it should show. Take them on Windows, with the
example site (Example Observatory, latitude 40.0, longitude −100.0, America/Chicago, a 0.25 m telescope). Never use
the real observatory's coordinates or name. Save as PNG in `docs/manual/img/` with exactly these names.

| File | Chapter | What it should show |
|---|---|---|
| `img/01-window-parts.png` | 1 | The whole main window on the Targets or Schedule page, with the sidebar ("Shiloh Hill / Observatory / Photometry / Scheduler", "(SHOBS-SCHED)", the tagline, STEPS 1–4, Credits and sources, Version 1.3.0), the white page area with its title and subtitle, and the status strip at the bottom (status line and credits line). Ideally labeled with arrows: sidebar, page title, page, status strip, busy-bar position. |
| `img/02-create-shortcuts.png` | 2 | The black console window of Create shortcuts.bat at the end of a first run: "Looking for a Python…", the "usable:" line, "Setting up SHOBS-SCHED's own Python environment…", the package check, and "Done. Start SHOBS-SCHED from the Desktop icon or the Start menu." |
| `img/02-first-start.png` | 2 | SHOBS-SCHED on first start with no SHOBS-P installed: the Setup page, empty fields, the top line "Enter your site and equipment once (or read the camera from a FITS frame)…", the guessed time zone with its hint. |
| `img/03-setup-saved.png` | 3 | The Setup page filled in for Example Observatory (40.0, −100.0, America/Chicago, 250 mm aperture), with "✔ Saved." beside the green Save button, and "set here" tags beside the typed fields. |
| `img/03-targets-two.png` | 3 | The Targets page with WASP-12 b in the Transits box and DY Peg in the Variables box, both ticked ☑, their event columns filled; the result line "✔ Added DY Peg (VSX)." beside Add. |
| `img/03-night-tab.png` | 3 | The Schedule page after Build schedule: a night tab selected, the night's line (dark, Moon, weather), the night plot with a green transit block and a navy variable block, and the nightly table with both rows; "✔ Built … nights" in the message line. |
| `img/04-setup-page.png` | 4 | The whole Setup page in its two-column layout: Mount and limits (with Moon distance at least: By Moon phase, Fixed, the four By phase boxes and the hint) and Weather on the left with Site; Telescope and camera, Horizon profile (a few az/alt lines) and Suggestions and darks (with darks on hand) on the right; the source line at the top; Save at the bottom. |
| `img/04-fits-summary.png` | 4 | The Telescope and camera box right after Read from a FITS frame…: the "⚠ Read the header of … Press Save to keep these values." summary (⚠ because the example frame has no ROTATOR) with its "Changed:" list (old → new), and "from FITS" tags beside the filled fields. |
| `img/05-targets-page.png` | 5 | The Targets page with several targets in all three boxes (a campaign with "This week" progress, transits with Night/Ingress/Mid/Egress, variables with "min …"/"any time"), a mix of ticked and unticked (gray) rows, one row selected with its details line, the Nights row, and the buttons at the bottom. |
| `img/05-edit-dialog.png` | 5 | The Edit dialog for a campaign on a planet-host star (a made-up or public example such as HD 219134): Target and Observing fields (Moon at least: "° away (empty = Setup's default)"), the "No data while a planet transits" part with the Archive result line, the ticked transiting planets, a grayed non-transiting planet, the window list, and Notes. |
| `img/05-find-variables.png` | 5 | The Find suggested variables window: Types ticks (first four ticked), Include uncertain types, Period (days/hours), Brightness, Amplitude at least, Show at most, the Nights line, Search and Cancel. |
| `img/06-night-tab.png` | 6 | One night tab in detail: the night's line, a ⚠ dew line, the weather strip (cloud bars, temperature and dew-point lines, spread numbers, the dashed dew-onset line), the night plot with twilight shading, the Moon's dashed gold line, a transit with its green band and lighter baselines, a ▼ flip marker, label boxes; the nightly table with one row selected (highlighted on the plot); the Lock / Remove / Move to night… / Edit times… / Status row; and the Flats and darks, Didn't fit (with a Plan this instead button) and Free time boxes. |
| `img/07-obsplan-page.png` | 7 | The OBSPlan page: the night ticks, Print plan card with Include night plots ticked, the position-angle line, the ASIAIR export folder line, and one night of the on-screen card (date heading with Export to ASIAIR, Export to NINA and Copy ASIAIR list; the facts line; the table; Flats/Darks/Free time lists), with an "✔ ASIAIR: saved …" result line and its Open folder / Open in Notepad / Show file contents buttons. |
| `img/07-plan-card-print.png` | 7 | The printed plan card open in a web browser: "Photometry plan, <dates>" title, the site line (Example Observatory, 40.000° N, 100.000° W), one night's facts and weather line, its night plot (print style), and the start of the nightly table. |
| `img/08-busy-bar.png` | 8 | A close-up of the lower left of the main window during a long job: the green busy bar on the navy status strip with its words beside it (Find suggested variables; a build on a fast PC is over before the bar can be caught). |
| `img/09-credits-page.png` | 9 | The Credits and sources page: "SHOBS-SCHED 1.3.0 © 2026 Shiloh Hill Observatory", the license line, How to cite SHOBS-SCHED (the citation with doi:10.5281/zenodo.23674791) and Copy, the list (CATALOGS AND SERVICES first, with ExoClock and ExoFOP-TESS), Save credits as text… and Back. |
| `img/05-find-transits.png` | 5 | The Find suggested transits window: its text naming Setup's "Suggestions and darks" box, the values and Setup's Moon distance, Show at most, the Nights line, the Priority rows (ExoClock priority ticks, ExoFOP SG1 priority), Search and Cancel. |
| `img/05-remove-dialog.png` | 5 | The app's own Remove targets window: "Remove DY Peg from the list? …" with Remove and Cancel. |
| `img/06-night-tab-lower.png` | 6 | A second night: a block with "stop by … at the latest", a transit whose Meridian reads "(flip)", its "⚠ Meridian flip during the transit" caution, the tab marked ⚠. |
| `img/08-busy-bar-find.png` | 8 | The green busy bar during Find suggested transits, showing the stage and its count ("…: the best with their most precise ephemeris… 10 of 37"). |
