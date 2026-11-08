# 6. Schedule

The Schedule page turns your ticked targets into a plan for each night. It also keeps the record of what happened:
after each night you mark each block, and SHOBS-SCHED rolls the week forward around it.

Its subtitle reads: *One tab per night, on the site's clock. Lock a block to keep it through rebuilds; mark what
happened (Done, Partial, Clouded out) and Re-review to roll the week forward.*

This chapter covers:

- building a schedule, and how the planner fills a night,
- the night tabs and the night plot,
- the nightly table and its notes,
- locking, removing, moving and re-timing blocks,
- flats and darks, what didn't fit (and **Plan this instead**), free time,
- the weather and dew,
- marking **Done**, **Partial** and **Clouded out**, and **Re-review schedule**,
- weekly goals for campaigns,
- the out-of-date and past-night warnings.

## Build a schedule

1. At the top of the page, choose **Nights** (1 to 7) and the **first night**. The first night is tonight by
   default; pick tonight or one of the next 13 nights from the list, or type any date (`2026-11-03`, `3 Nov`). These are the
   same nights as the Targets page's **Nights** row.
2. Press **Build schedule**.
3. The busy bar at the lower left of the window shows each step: *Getting the forecast from Open-Meteo…*, then
   *Building 7 nights…*, then *Working out twilight and Moon times…*. **Build schedule** and **Re-review
   schedule** are grayed out until it finishes.
4. When it is done, the busy bar goes, and the line under the controls says what was built, for example *✔ Built 7
   nights: 11 blocks; 2 targets didn't fit (see each night).* That line is the only result: nothing extra appears
   beside the button.

If the forecast could not be fetched, the plan is still built, and the line says so with a ⚠: *⚠ Built 7 nights: 11
blocks. No forecast (Open-Meteo did not answer (timed out)), so it was built without weather.* When an older saved
forecast was used instead, the line says how old it is.

The schedule is saved after every change, so it is there the next time you start SHOBS-SCHED.

Before it builds, SHOBS-SCHED checks for blocks you locked or set by hand (and have not marked yet), from the first
night on, whose target is no longer ticked or no longer in your list. If there are any, a window titled **Blocks set
by hand** asks first, for example: *BL Cam has 2 blocks you locked or set by hand on Tue 03 Nov and Wed 04 Nov.
Their targets are not ticked to plan. Remove them too? (Cancel keeps them in the schedule, with a note that the
target is not planned.)* The middle sentence says *… no longer in your list.* for removed targets, and *Their targets
are not ticked to plan, or no longer in your list.* for a mix. **Remove** takes them out; **Cancel** keeps them, and
each kept block gets the note *⚠ This target is not ticked to plan* (or *⚠ This target is no longer in your list*).
If you keep them, SHOBS-SCHED does not ask about the same blocks again until you restart it. **Re-review schedule**
asks the same question (from tonight on), and so does the Targets page when you untick or remove such a target
(chapter 5), but not while a build is running.

Building again keeps your work: every block you **locked**, set the times of, or **marked** (Done, Partial, Clouded
out) stays on its night with its times and its mark, and the rest of the night is rebuilt around it. A build that
starts later than the saved schedule moves the nights before it into the history, marks included.

> **Tip:** Building needs your site (latitude, longitude, time zone) and at least one ticked target. Without a site
> the page says *✖ Enter your site on the Setup page first (latitude, longitude, time zone); then build.* Without a
> ticked target it says *⚠ No targets are ticked to plan (☑ on the Targets page).*

### How a night is filled

The planner fills each night in a fixed order. Knowing it helps you read the plan:

1. **Your blocks first.** Locked blocks, blocks whose times you set, and blocks already marked stay exactly as they
   are. If one is no longer possible (the target is below your limits, outside the darkness, inside a no-transit
   window, or overlapping another of your blocks), it stays and gets a ⚠. One whose target you unticked or removed
   stays too, with an information note (see the Build question above).
2. **Fixed-time targets next.** Transits (from the baseline before ingress to the baseline after egress) and timed
   variable events (a minimum or maximum ± its half-width). The transit itself must be in the planning darkness and
   above your limits, and, while the Moon is up, no closer to it than the target's **Moon at least** or Setup's
   default **Moon distance at least** (chapter 4); the baselines may run into nautical twilight, and may be shortened
   to fit the night. When two
   fixed-time targets collide, their baselines are trimmed first, down to 20 minutes a side (*⚠ Baseline trimmed*).
   If they still collide, one wins: higher priority first, then the rarer chance (a transit not seen again for a
   month beats one that comes back in three nights), then the better signal-to-noise.
3. **Flexible targets last.** Campaigns and any-time variables, High priority first, then Normal, then Low. A
   campaign behind its weekly goal moves up one level. Each gets at most one block a night, of its **Block length**
   (or less when there is less room, but never under its **Shortest useful block**), placed at the best part of the
   night for it: on an equatorial mount wholly before its meridian when a full block fits there, otherwise starting
   about 12 minutes before its meridian (see *Flexible blocks and the meridian flip*, below); on an alt-az mount
   centred on the meridian. Its own rules apply: **Own minimum altitude**, **Moon at least** (or, when that is empty,
   Setup's default **Moon distance at least**, by the Moon's phase that night), **Only between**, and its no-transit
   windows.
4. **Overheads.** A few minutes between blocks for slewing and plate solving, autofocus at the start of the night,
   and a refocus after a defocused target.
5. **Weather.** With the dew setting on, flexible blocks end when dew is expected (Setup, chapter 4).

### Flexible blocks and the meridian flip

This applies to a German equatorial mount (**Mount** in Setup). An alt-az mount never flips.

When a target crosses the meridian, an equatorial mount must turn to the other side of the pier. The star then lands
on a different part of the sensor, the field is turned 180°, and the guiding and plate solve start again. For a
campaign or an any-time variable you want one long, even series, so a flip in the **middle** of a run is the worst
place for it: it leaves a step in the light curve. The planner places flexible blocks so that this does not happen:

1. **No flip, if a full block fits.** When a whole block of the target's **Block length** fits in the dark before
   the meridian, the block runs wholly before it, ending inside your **Tracks past the meridian** margin (Setup). There
   is no flip at all, and no frames to drop. The note says, for example, *Ends 5 min past its meridian, inside your
   tracking margin; stop by 23:47 at the latest*: stop the run by then, or the mount flips.
2. **Otherwise, flip at the start.** When a full block does not fit before the meridian, the block starts about 12
   minutes before the meridian. The mount flips while you are there at the start, and the rest of the run is on one
   side of the pier. The note says *Start before the meridian and let it flip (~22:40). Pre-flip frames carry no
   weight in the night mean.* (A transit's note differs; see "Notes you will see" below.)
3. **A flip in the middle only as a last resort.** The planner counts a mid-run flip as a heavy cost (as bad as
   starting 4 hours from the best time), so it only places one there when nothing better fits. The block then gets
   *⚠ Meridian flip in the middle of the run (~01:10): a pier-side step in the series*.

**No flip, but not at another target's cost.** Running a block wholly before the meridian puts it earlier in the
night than a flip-at-start block. Sometimes that takes the room another target needed. When targets didn't fit, the
planner tries each no-flip block at flip-at-start instead, and keeps the change only when every target that fitted
still fits and at least one more does. Every block moved this way then says *Flips at the start (not run wholly
before its meridian) so another target fits tonight*. Blocks you locked or set the times of are never changed this
way. Only a room that holds the **whole** block counts as "fits before the meridian": a shorter no-flip run never wins
over a full flip-at-start block.

Fixed-time blocks (transits, timed events) cannot move: their flip falls where it falls, with a note (and a ⚠ when it
is inside the transit).

## The night tabs

Each night has a tab named by its date, for example **Tue 20 Oct**. A **⚠** in front of the name means the night
needs your attention: a block still planned (not yet marked) has a caution you should act on, or the night has a
night-wide ✖ (no dark time). A target that didn't fit, a cloudy forecast, a night-wide ⚠ line above the plot (such as
*⚠ Dew expected from 04:05*; a block that the dew reaches still has its own caution), and the
cautions *⚠ Baseline trimmed*, *⚠ Ephemeris uncertain*, *⚠ This target is no longer in your list* and *⚠ This target
is not ticked to plan* do not put a ⚠ on the tab; you still see them on the night itself. A night that has passed says **(past)**.

A tab is built the first time you open it. At the top is the night's line: the date, the dark hours and which darkness
(*Dark 19:42–05:58 (astronomical)*), the Moon (below) and the weather summary (*Clear until 02:00, then 60–80 %
cloud; dew from 04:05*). Under it come any night-wide warnings, such as *⚠ Dew expected from
04:05*, or *✖ No dark time tonight: the Sun gets no lower than −15.2° (planning darkness −18.0°); nautical darkness
(Sun below −12°) is possible if you allow it in Setup*.

The Moon is given in the order the dark night meets it: whether it is up or down at dusk, then what it does before
dawn. For example *Moon 62 % (down at dusk, rises 23:10)*, *Moon 48 % (up at dusk, sets 01:35)*, or, when it does not
rise or set in the dark, *Moon 95 % (up all night)* or *Moon 3 % (down all night)*.

![A night tab (Sat 31 Oct): the night's line and a ⚠ dew line, the weather strip above the night plot, the HAT-P-32 b transit selected and drawn bright, DY Peg and the HD 219134 campaign, the nightly table with its (S/N) and (est.) exposures and its notes (each led by the verdict), and the Flats and darks, Didn't fit (with Plan this instead) and Free time boxes](img/06-night-tab.png)

## The night plot

The night plot shows the whole night at a glance. Its design follows the airmass plot of TAPIR by Eric Jensen
(chapter 9).

- **Across:** the time on the site's clock, from 1.5 hours before nautical dusk to 1.5 hours after nautical dawn,
  with the sunset, twilight and sunrise times along the top. The sky is shaded by twilight: lighter at dusk and
  dawn, darkest in astronomical night.
- **Down:** airmass on the left (1.0 at the top), elevation (altitude) on the right. The red dashed line is your
  maximum airmass (*max airmass 2*).
- **Title:** the date, your site's name and the Moon's phase; at the right, the selected block (*Selected: HAT-P-6 b
  (Transit) 19:05–00:36*).
- **Each planned target:** its path through the whole night is drawn faint; its block is drawn bold in its type's
  color (green Transit, navy Variable, plum Campaign), with the airmass at the block's start and end. A label box
  gives its name, start and end, and its distance from the Moon.
- **Transits:** the transit itself (ingress to egress) is a green band with its times (*Transit 20:05–23:36*), its
  baselines a fainter green band.
- **Meridian:** a **▼** marks a meridian flip inside a block (*flip 21:27*); a **|** marks a meridian crossing that
  needs no flip (*meridian 21:05*).
- **The Moon:** a dashed gold line, thicker when fuller.
- **No-transit windows:** hatched, but only where one changed the plan.
- **Locked blocks** and blocks you set the times of have a gold box edge.
- **Weather strip** (above the plot, on the same time axis): gray bars for cloud %, a red line for temperature and a
  blue line for the dew point, the spread (temperature minus dew point) as numbers along the top, a blue fill where
  dew is likely (spread under your Setup threshold), and a dashed line with *⚠ dew from 04:05* at the dew onset. With
  no forecast, the strip says *No forecast for this night*.

Point at the plot to read the values under the pointer on the line under it: the time, the target or Moon line
nearest the pointer (airmass and altitude), the block at that time, and that hour's weather. **Click a block** to
select its row in the table; the selected target is drawn bright and thick and every other one fades. Click beside
the blocks to clear it. A legend under the plot repeats these meanings.

The plot grows with the window: a taller window gives a taller plot.

> **Note:** If Matplotlib is missing, the plot's place shows *⚠ Night plot needs matplotlib — run Create
> shortcuts.bat again* on Windows (on a Mac and on Linux it names that system's start file instead). Everything else
> on the page works. See chapter 10.

## The nightly table

One row per block, in time order, tinted by the target's type:

| Column | What it shows |
|---|---|
| **Time** | Start–end on the site's clock. |
| **Target** | The name, with *· locked*, *· set by you*, or the mark (*· Done*, *· Partial*, *· Clouded out*). |
| **RA / Dec (J2000)** | The target's position. |
| **Alt** | The highest altitude in the block and its airmass, e.g. *62° (X 1.13)*. |
| **Meridian** | When the target crosses the meridian; *(flip)* when the mount flips during the block, e.g. *22:44 (flip)* (the flip's time is in the notes). |
| **Exposure** | The exposure, e.g. *40 s*. *120 s (S/N)* means it was chosen by S/N (**Best precision**, chapter 5). *40 s (est.)* means SHOBS-SCHED estimated it (**Longest safe**: the 1.3.0 way on rough numbers, the noise model's peak once a zero point is measured or typed), and also marks a Best precision exposure that could not keep the peak under the aim (the star is too bright even at the shortest exposure). Your own exposure has no mark. The OBSPlan page and the plan card write *est.* and put the verdict under the exposure (chapter 7). |
| **Frames** | About how many frames fit (*~240*); for a transit also how many fall in the transit (*~240 (~135 in transit)*). |
| **Notes** | The verdict first (new in 1.4.0), then what to know about this block (below), then its cautions. |

Click a row to select it (and pick it out on the plot). The line under the table shows its notes and cautions in
full, with each caution on its own ⚠ line.

### Notes you will see

- **The verdict** (first, on every block that has one): the verdict word with its S/N, then what it rests on, worked
  out for this night's airmass and Moon (chapter 5):
  - *Detectable (S/N 18): a 1.00 % dip against 1.7 ppt per 10-min bin, 9 bins in transit*
  - *Marginal (S/N 7.4): a 0.05 mag range against 6.1 ppt per 10-min bin* (a variable)
  - *Detectable? (S/N ~63, rough estimate): …*: no measured zero point yet, so it is a guess (the "~" says the same);
  - *Out of reach (S/N 3.1, needs 5): …*, or *Out of reach? (S/N ~3.1, needs 5, rough estimate): …*;
  - *Saturates: peak ~140 % of your linear limit at 120 s (shorten it or defocus)* (with *(rough estimate)* after the
    word when the numbers are rough).

  Without a SHOBS-FLUX profile, transits are on **Longest safe**, so their Exposure reads *… (est.)* even though
  their notes give the S/N.
- **Transits**: *Ingress 21:32, mid 22:44, egress 23:56 (±1 min)* and *Baseline 60 min before ingress, 60 min after
  egress*. After a flip at the start the baseline note reads *Baseline 48 min before ingress (after the flip; the
  kept pre-flip frames add more), 60 min after egress*.
- **Timed variables**: *Minimum at 01:14* or *Maximum at 22:05*.
- **Any-time variables**: *About 1.3 cycles (P ≈ 1.75 h)*, how much of the light curve you will cover.
- **Campaigns with a goal**: *Goal: 3 nights a week*, and when it is behind, *Behind its weekly goal (0 of 3 so
  far): moved up one priority level*.
- **No-transit windows**: *No HD 219134 after 04:28 (planet c transit)*.
- **Flips** (equatorial mount; see *Flexible blocks and the meridian flip*): *Ends 3 min past its meridian, inside
  your tracking margin; stop by 03:38 at the latest* (or *Ends at its meridian; stop by 03:40 at the latest*), *Meridian
  flip at ~01:10; drop the frames during the flip*, and *Flips at the start (not run wholly before its meridian) so
  another target fits tonight*. The flip-at-start note depends on the target's type (new in 1.4.0):
  - a variable or campaign: *Start before the meridian and let it flip (~22:40). Pre-flip frames carry no weight in
    the night mean.* One night's mean brightness does not need them.
  - a transit: *Start before the meridian and let it flip (~22:40). Pre-flip frames are kept; SHOBS-P fits the flip
    step.*
    Keep them: SHOBS-P fits the small step the flip leaves in the light curve, and the extra baseline helps.
- **Room for the next block**: *Ends 12 min early so DY Peg can start before its meridian*.
- **Clouds**: *Cloud ≥ 70 % forecast for most of this block*. A plain note, not a caution.
- **Your own frame interval**: *Frames every 45 s (your seconds between frames)*.
- **Defocused targets**: *Refocus after this block*.
- Your own notes from the target's Edit dialog.

### Cautions (⚠)

| Caution | What it means |
|---|---|
| *⚠ Ephemeris uncertain: mid-time ± 25 min* | The transit may come early or late. Worth observing; consider extra baseline. |
| *⚠ Baseline trimmed (next to WASP-12 b)* | It shares the night with another fixed-time target; its baseline is shorter. |
| *⚠ Meridian flip during the transit (23:05)* | The flip falls between ingress and egress. Expect a gap and a step in the light curve. |
| *⚠ Meridian flip in the middle of the run (~01:10): a pier-side step in the series* | A flexible block that could not avoid a mid-run flip. Expect a step in the light curve at the flip. |
| *⚠ Starts 20 min after its meridian: the mount may flip at once (start before 22:40 if you can)* | Start before the meridian time if you can. |
| *⚠ Counterweight up beyond HA ±6 h (04:10)* | Equatorial mount: the target is more than 6 hours from the meridian during the block, so the mount runs counterweight-up. Check your mount allows it. |
| *⚠ Zenith keyhole: 87° at 23:40* | Alt-az mount: the target passes too near the zenith to track smoothly. |
| *⚠ estimate: peak ~45k, near your 32k limit — check a frame* | Your exposure may go past the linear limit. Take a test frame. |
| *⚠ estimate: even 0.75 s may saturate…; defocus* | A star too bright for the shortest standard exposure. Defocus, and write "defocus" in its notes. |
| *⚠ Dew expected from 04:05* | Dew is forecast during the block (*…: the block ends there* when the dew setting cut it short). |
| *⚠ Below your limits 03:10–03:40* | One of your own (locked) blocks now runs below your limits. |
| *⚠ Overlaps your block …* / *⚠ Inside a blackout: …* | One of your own blocks clashes with another or with a no-transit window. |
| *⚠ This target is no longer in your list* | A block you locked or set by hand, kept when its target was removed. Remove it if you no longer want it. |
| *⚠ This target is not ticked to plan* | A block you locked or set by hand, kept when its target was unticked (it is still in your list). Remove it, or tick the target again. |

*⚠ Baseline trimmed*, *⚠ Ephemeris uncertain*, *⚠ This target is no longer in your list* and *⚠ This target is not
ticked to plan* are for your information: they do not put a ⚠ on the night's tab. The others do, while the block is
still planned.

## Change a block

Select a row, then use the buttons under the table. A result line under the buttons says what happened.

- **Lock** keeps the block's times through every rebuild (*✔ HAT-P-23 b locked: it keeps its times when you
  rebuild.*). The button reads **Unlock** for a locked block or one you set the times of.
- **Remove** takes the block off this night (*it comes back when you rebuild unless you untick it*).
- **Move to night…** moves a flexible block to another night of the schedule. It keeps its place in the sky, so it
  starts about 4 minutes earlier on the clock for each night later (later for an earlier night); the planner checks
  it is still up. The block is kept there (the table says *set by you*), and that night is rebuilt around it. A
  transit or a timed event cannot move: its time is set by the sky, so the button is grayed for those (should you get
  there anyway, the line says *✖ WASP-12 b happens at a time set by the sky (a transit or a timed event); it cannot
  move to another night.*).
- **Edit times…** asks for a new start and end (`HH:MM` on the site's clock). The block is marked *set by you*, keeps
  those times through rebuilds, and the night is rebuilt around it. The times must be between sunset and sunrise, and
  must not overlap a block that stays put (fixed, locked, set by you, or marked).

When the change leaves a problem, the result line says so with a ⚠, for example *⚠ BL Cam set by you: 23:10–01:00.
Below your limits 00:40–01:00*.

While a build or rebuild runs, these buttons wait: *⚠ Wait for the build to finish.*

## Flats and darks, didn't fit, free time

Under the table are three boxes.

![Another night (Tue 03 Nov, its tab marked ⚠): AB And with "stop by 21:04 at the latest", the WASP-12 b transit selected with its meridian time marked (flip) and the caution "⚠ Meridian flip during the transit (04:18)"; the Lock, Remove, Move to night… (grayed for a transit), Edit times… and Status row; and the Flats and darks, Didn't fit and Free time boxes](img/06-night-tab-lower.png)

**Flats and darks to shoot.** For example *Flats: one set at the night's camera angle; a meridian flip's 180° turn
needs no new flats*, and *Darks to shoot: 0.75 s, 45 s, 60 s*. A defocused target adds *flats at the defocus position
too*. List the darks you already have under **Darks you already have** in Setup's **Suggestions and darks** box
(chapter 4), for example `60, 120, 180`. *Darks to shoot* then lists only the exposures your library does not cover,
and when it covers them all the line says *Darks: all on hand (45 s, 60 s)*.

Why one set of flats is enough after a flip: a flip turns the camera 180° with the telescope, so the dust and
vignetting stay where they were relative to the sensor. A rotator angle change is different, which is why the plan
keeps the camera angle fixed.

**Didn't fit, and why.** Every ticked target that got no block, with the reason and its next chance, for example:

- *WASP-12 b: collides with HAT-P-23 b; next chance Fri 23 Oct*
- *BL Cam: below your limits all night (highest 24°; limit 30° there); next chance Mon 26 Oct*
- *DY Peg: no free stretch of 45 min left tonight (longest 19 min); next chance Wed 04 Nov*
- *KELT-1 b: no transit tonight*
- *BL Cam: Moon too close (38° at best; default for a gibbous Moon 50°)*, or for a transit *Moon too close during
  the transit 22:10–00:45 (31°; your limit 40°)*. The words in brackets say which limit was used: *your limit 40°* is
  the target's own **Moon at least**; *default for a gibbous Moon 50°* (crescent, quarter, gibbous or full) is Setup's
  **Moon distance at least** by phase; *Setup's default 30°* is its **Fixed** value (chapter 4).

When another block took the room (a collision, or no free stretch left), a green **Plan this instead** button appears
under the line. It rebuilds the night with that target first; the one that gave way then appears here instead. It
only ever plans a ticked target. The result line says *✔ Rebuilt with WASP-12 b first.*, or *✖ Still didn't fit: …* when it cannot be done.

**Free time.** Gaps of 15 minutes or more in the dark with nothing planned, for example *Free 00:36–01:02 (26 min)*. A good place
for an extra any-time variable, or for flats and darks.

## Weather and dew

The forecast comes from Open-Meteo for your site each time you build or re-review. It is shown:

- in the night's line (the summary),
- in the weather strip over the night plot,
- in the blocks' cautions (dew) and notes (cloud),
- on the plan card.

Why dew matters for photometry: a dewing corrector or mirror dims the stars gradually, which looks like a slow
trend in your light curve, or ruins a transit's egress. With **End dew-sensitive runs at the predicted dew onset**
ticked in Setup, flexible blocks end at the dew onset (*⚠ Dew expected from 04:05: the block ends there*). Fixed-time
blocks are not cut: a transit happens when it happens, so it gets a ⚠ instead, and you decide.

Cloudy hours are not removed from the plan. A block that is mostly under 70 % cloud or more gets the note *Cloud ≥
70 % forecast for most of this block*. It is a plain note, not a ⚠, and it does not put a ⚠ on the night's tab: the
plan stays, because forecasts change, and you decide on the night.

## After the night: mark what happened

1. Open the night's tab.
2. Select a block.
3. Choose its **Status**: **Done**, **Partial** or **Clouded out**. The empty entry at the top of the list clears a
   mark (*✔ BL Cam: mark cleared (planned, not marked).*).

Marked blocks stay where they are on rebuilds, and the table shows the mark. Done and Clouded out rows are grayed.

What each mark means for the planner:

| Mark | Counts toward a campaign's weekly goal? | Target back in the pool for later nights? |
|---|---|---|
| **Done** | Yes | No need |
| **Partial** | No (counts as missed) | Yes |
| **Clouded out** | No (counts as missed) | Yes |

## Re-review: roll the week forward

**Re-review schedule** moves the schedule on to tonight:

1. Nights before tonight go into the history, with their marks.
2. The schedule keeps its length (seven nights stays seven nights), so new nights are added at the end.
3. Everything that is not locked, set by you or marked is rebuilt, with a new forecast. Targets clouded out or only
   partly observed are back in the pool and get planned again where they fit.

If locked blocks on past nights were never marked, SHOBS-SCHED asks first: they would go to the history as Planned,
not Done. Mark them first if you observed them. Like **Build schedule**, it also asks about blocks you locked or set
by hand whose targets are unticked or removed (see "Build a schedule" above).

The busy bar says *Re-reviewing 7 nights…* while it works, and the line under the controls reports the result as for a build.

## Weekly goals for campaigns

A campaign with a **Goal** (nights a week, in its Edit dialog) is paced through the week, Monday to Sunday:

- **Done** nights count, and so do nights already planned for it earlier in this schedule. Partial and Clouded out
  nights do not.
- A campaign is **behind** when it cannot reach its goal without tonight, or is behind an even pace through the week.
  A goal of 1 is behind from Monday until it has a night; a goal of 2 is behind from Monday until it has one night,
  and from Thursday until it has two.
- A campaign that is behind is placed one priority level higher. If there is no room at all, it may shorten other
  flexible blocks (toward their shortest useful length) to get its night.

The Targets page's **This week** column shows the progress, for example *2 of 3 this week*.

## Warnings about the schedule itself

- **Setup or targets changed.** After you save a change in Setup, or change a ticked target (tick, untick, edit,
  priority), the schedule was built from the old values. The page says *⚠ Setup or targets changed since this
  schedule was built: press Build schedule (or Re-review) to rebuild it with the changes.* The OBSPlan page shows a
  similar warning, and still lets you print and export. Unticked suggestions coming and going do not count.
- **The schedule starts before tonight.** *⚠ The schedule starts on Mon 19 Oct, before tonight. Mark what happened
  and press Re-review schedule to roll it forward.* Past nights stay visible as a record, with a ⚠ line above the
  plot: *⚠ Mon 19 Oct has passed: this tab is the record of that night (mark what happened, then Re-review
  schedule).*
- **Every night has passed.** *⚠ Last built for Thu 22 Oct (past): press Build schedule.*

---

Previous: [5. Targets](05-targets.md) · Next: [7. OBSPlan](07-obsplan.md)
