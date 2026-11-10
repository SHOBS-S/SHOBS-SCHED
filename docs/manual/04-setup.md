# 4. Setup

The Setup page holds your site and equipment. You fill it in once; after that you come back only when something
changes (a new camera, a new horizon survey). Every plan, every time and every exposure estimate depends on it, so
it is worth getting right.

New in 1.4.0: the **Photometry noise (exposure by S/N)** box. It holds what SHOBS-SCHED needs to work out how
precisely you can measure a star (the S/N, chapter 5). You can leave it empty: SHOBS-SCHED then uses rough
estimates, and says so.

The page subtitle says it plainly: *Your site and equipment, entered once. Every time SHOBS-SCHED shows is on the
site's clock (its time zone below), whatever this computer's clock says.*

This chapter covers:

- the line at the top (where the values came from) and the source tags,
- the Site box, including the time zone,
- the Telescope and camera box, including **Read from a FITS frame…**,
- the Mount and limits box (meridian flips, minimum altitude, airmass, darkness, and the default Moon distance),
- the Horizon profile box,
- the Weather box (forecast source and dew),
- the Suggestions and darks box (the filter for the Find buttons, and the darks you already have),
- the Photometry noise box (a SHOBS-FLUX instrument profile, or your own noise values, for the S/N),
- saving, and leaving the page with unsaved changes,
- the planner settings that are not on the page.

![The Setup page: Site, Mount and limits (with Moon distance at least: By Moon phase, Fixed, Off and the four phase values), Weather and the Photometry noise box on the left; Telescope and camera, Horizon profile, Suggestions and darks (darks on hand 60, 120, 180) and the New versions box on the right; Save at the bottom](img/04-setup-page.png)

The boxes sit in two columns when the window is wide enough: **Site**, **Mount and limits**, **Weather** and
**Photometry noise (exposure by S/N)** on the left; **Telescope and camera**, **Horizon profile**, **Suggestions
and darks** and **New versions** on the right. In a narrow window
they sit in one column, in the order this chapter follows. The page scrolls when it does not fit.

## Where the values came from

The line at the top of the page says where the values on the page came from:

- With nothing filled in yet: *Enter your site and equipment once (or read the camera from a FITS frame). SHOBS-P,
  if you add it later, can share these.*
- When values came from SHOBS-P or a FITS frame, a **✔** line says so, for example *✔ Site and telescope read from
  SHOBS-P (settings of 2026-10-20). Change them there, or override here. Camera values read from a FITS frame. Tags:
  from SHOBS-P, from FITS, set here.*
- When SHOBS-P's *Tonight's transits* finder gave SHOBS-SCHED its first limits, the line adds: *The minimum altitude,
  darkness and the “Suggestions and darks” filter started from SHOBS-P's Tonight's-transits finder (once; later
  changes there do not move them).*

Beside many fields, a small gray **tag** says where that one value came from:

| Tag | Meaning |
|---|---|
| *from SHOBS-P* | Read from SHOBS-P's settings. It follows SHOBS-P: change it in SHOBS-P and SHOBS-SCHED picks up the new value next time it starts. |
| *from FITS* | Read from a FITS frame with **Read from a FITS frame…**. SHOBS-P's value no longer replaces it. |
| *set here* | You typed it on this page. It is yours, and SHOBS-P never overwrites it. |
| (no tag) | The default, or nothing entered yet. |

The **Photometry noise** box has tags of its own: *from FLUX* (measured by SHOBS-FLUX), *set here* (typed by you)
and *estimate* (a rough value SHOBS-SCHED uses because nothing better is known). See "Photometry noise" below.

The tag changes as soon as you type: a value you change shows *set here* at once. If you type a field back to exactly
SHOBS-P's value and save, it follows SHOBS-P again.

> **Note:** The minimum altitude, the darkness and the suggested-transits filter (the V range, depth and baseline in
> the **Suggestions and darks** box) are taken from SHOBS-P's finder only
> **once**, and only while SHOBS-SCHED still has its own default there. After that, changing them in SHOBS-P does
> not move them here. This stops a later change in one program from quietly changing your plans in the other.

## Site

| Field | What to type |
|---|---|
| **Name** | Your observatory's name. It is printed on the plan card. Optional. |
| **Latitude** | Degrees, north positive, south negative (e.g. `40.0`). Required. |
| **Longitude** | Degrees, **east positive, west negative** (e.g. `-100.0` for 100° W). Required. |
| **Elevation** | Height above sea level, in meters or feet (see **Lengths**). Optional; it fine-tunes the sky noise estimate. |
| **Time zone** | Your site's time zone, by its standard name (`America/Chicago`, `Europe/London`, `Australia/Sydney`). Required. |
| **Temperatures** | **°C** or **°F**, for the weather and the dew setting. Display only. |
| **Lengths** | **m** or **ft**, for the elevation (and wind in km/h or mph). Display only. |

### The time zone

Pick your site's zone from the drop-down list, or type its name. Under the box a line says what every time will read
as, for example *Times show as Local time (UTC−5) now.* When SHOBS-SCHED had to guess the zone from the computer's
clock (first start, nothing saved yet), the line adds *guessed from this computer's clock: check it and Save.* A
name the program does not know gives *Not a zone name: pick one from the list.*

Why this matters: every time in SHOBS-SCHED (dark times, ingress, meridian, block starts) is shown on the site's
clock in this zone, with the daylight-saving changes of that zone. The computer's own time zone is never used. A
remote observatory in another zone, or a laptop that travels, still gets the times a clock at the telescope shows.

## Telescope and camera

| Field | What it is | Why SHOBS-SCHED needs it |
|---|---|---|
| **Telescope** | Its name. | Shown for your reference. |
| **Aperture** | Mirror or lens diameter, mm. | Light collected: the exposure estimate. |
| **Focal length** | mm. | With the pixel size, the pixel scale. |
| **Pixel size** | µm, unbinned. | Pixel scale. |
| **Binning** | 1 to 4. The pixel scale appears beside it (e.g. *1.24″ per pixel*). | Pixel scale. |
| **Camera** | The camera's name (e.g. its model). | Also sets the field of view written to the ASIAIR and NINA lists, for the common ZWO sensors. |
| **Sensor** | **Color (one-shot)** or **Mono**. | A color camera's green pixels see less light than an unfiltered mono pixel. |
| **Gain setting** | The gain you shoot at (as in the FITS GAIN keyword). | Your reference, next to the e⁻ per ADU that goes with it. |
| **e⁻ per ADU** | Electrons per count at that gain. | Turns the predicted electrons into ADU for the peak estimate. |
| **Full well** | Electrons. | Used for e⁻ per ADU when that is empty. |
| **Linear limit** | Raw ADU a star's peak should stay under (32000 if empty). | The exposure estimate keeps peaks under it. |

Two ticks under these say whether a value is from the spec sheet rather than measured: **Full well is from the spec
sheet** and **Linear limit is from the spec sheet**. A ticked one shows **⚠ estimate** beside it.

Why the linear limit matters for photometry: above a certain level, a camera's pixels stop responding in proportion
to the light, long before they saturate. A star whose peak goes past that level is measured too faint. SHOBS-SCHED
picks each target's exposure so that the predicted peak stays under about 80 % of your linear limit (both **Longest
safe** and **Best precision** do, chapter 5), and warns you when your own exposure may go over. A target whose
predicted peak is over the linear limit gets the verdict **Saturates**. Your own measured value (from a linearity test in SHOBS-P, for example) is better than
the spec sheet's.

Three more fields:

- **Per-frame overhead (download/save, all targets)**: seconds added to every target's exposure for the camera's
  download and save time (*s; empty = 3 s*). About 3 s on an ASIAIR. The planner uses it to count frames. A target's
  own **Seconds between frames** (in its Edit dialog, chapter 5) is the whole frame-to-frame interval and wins for
  that target.
- **Usual camera angle (rotator)**: the angle your camera usually sits at, in degrees (*empty = 0*). It is written to
  the ASIAIR and NINA lists as the position angle, so the plan frames the field the way your flats were taken
  (chapter 7).
- **Defocus widens stars ×**: how much wider a star gets when you defocus (1.5 by default). It applies to targets
  whose notes contain the word "defocus" (chapter 5), so their exposure estimate allows for the spread-out light.

### Read from a FITS frame

If you have a frame taken with this camera and telescope, let SHOBS-SCHED read the values from it:

1. Press **Read from a FITS frame…** at the bottom of the box.
2. Choose a FITS file (`.fits`, `.fit` or `.fts`). Only its header is read, so even a large frame is quick.
3. A summary appears under the button: which values changed (old → new), which were already the same, and which the
   frame does not have. It starts with **✔**, or **⚠** when some keywords were missing.
4. Press **Save** to keep the values.

| Setup field | FITS keyword |
|---|---|
| **Camera** | INSTRUME |
| **Sensor** | BAYERPAT (a pattern such as RGGB means color; NONE or MONO means mono; no keyword leaves it as it is) |
| **Gain setting** | GAIN |
| **e⁻ per ADU** | EGAIN |
| **Pixel size** | XPIXSZ (divided by XBINNING, since XPIXSZ is the binned pixel) |
| **Binning** | XBINNING |
| **Focal length** | FOCALLEN |
| **Usual camera angle** | ROTATOR (taken modulo 180°: 210° and 30° frame the field the same way, on the other pier side) |

The TELESCOP keyword is not used: ASIAIR writes the mount driver's name there, not the telescope's.

Values read from a frame are tagged *from FITS* and win over SHOBS-P's. Type over one and it becomes *set here*.

![The Setup page right after Read from a FITS frame…: the summary "⚠ Read the header of example_frame2.fits (ZWO ASI533MC Pro). Press Save to keep these values.", the Changed: list (Gain setting 100 → 120, e⁻ per ADU 0.25 → 0.75 e⁻/ADU, Focal length 1600 → 1580 mm), the values already the same, the keyword not in this frame (ROTATOR, hence ⚠), and the *from FITS* tags](img/04-fits-summary.png)

## Mount and limits

| Field | What it is |
|---|---|
| **Mount** | **Equatorial (flips)** for a German equatorial mount that makes meridian flips, or **Alt-az**. |
| **Tracks past the meridian** | How many minutes your equatorial mount can track past the meridian before it must flip (5 by default). |
| **Minimum altitude** | Degrees. No block is planned below it. 30° by default. |
| **Maximum airmass** | No block is planned at a higher airmass. 2.0 by default (about 30° altitude). |
| **Darkness to plan in** | **Astronomical (Sun −18°)** or **Nautical (Sun −12°)**. |
| **Moon distance at least** | **By Moon phase** (the default), **Fixed** or **Off**: how close the Moon may come to any target that has no Moon limit of its own (see "The Moon distance" below). |
| **Fixed** | Degrees, used when **Fixed** is chosen. 30° by default. |
| **By phase** | Four small boxes, **crescent**, **quarter**, **gibbous** and **full**, used when **By Moon phase** is chosen. 20°, 35°, 50° and 70° by default. |

### Meridian flips

On an equatorial mount, a target crossing the meridian makes the mount flip to the other side of the pier. A flip
costs a few minutes (slew, plate solve, guiding restarts), turns the field on the sensor by 180°, and moves the star to
other pixels, which is unwelcome in the middle of a transit.

SHOBS-SCHED plans around this. Blocks at a fixed time (transits, eclipse minima) and flexible blocks (campaigns
and any-time variables) are placed a little differently.

For every block:

- **It avoids starting a block within an hour after its target's meridian.** A start there would make the mount flip
  at once, while the run is under way. When nothing else fits, the block gets *⚠ Starts 20 min after its meridian:
  the mount may flip at once (start before 22:40 if you can)*.
- **A block that ends just past the meridian** but inside your **Tracks past the meridian** margin needs no flip at
  all. The note says, for example, *Ends 3 min past its meridian, inside your tracking margin; stop by 03:38 at the
  latest*.
- **A block that must cross the meridian flips at the start.** It starts about 12 minutes *before* the meridian, so
  the mount flips right at the start, while you are there, and the rest of the run is on one side of the pier. The
  note says *Start before the meridian and let it flip (~22:40). Pre-flip frames carry no weight in the night
  mean.* for a variable or campaign, and *Start before the meridian and let it flip (~22:40). Pre-flip frames are
  kept; SHOBS-P fits the flip step.* for a transit.
- **The flip costs time.** The frame count of a block with a flip allows for a few minutes of lost time.

For a **fixed-time block**, a flip in the middle is noted (*Meridian flip at ~01:10; drop the frames during the
flip*), and a flip during a transit gets a caution: *⚠ Meridian flip during the transit (23:05)*.

For a **flexible block**, the planner tries to avoid the flip altogether:

1. **No flip, when a whole block fits before the meridian.** If a full-length block fits in the dark and ends no
   later than your **Tracks past the meridian** margin after the meridian, the block goes there. There is no flip,
   no settling and no lost frames.
2. **Otherwise, the flip at the start**, as above.
3. **A flip in the middle of the run is the last choice.** It puts a pier-side step into a campaign's series, so the
   planner counts it as costly (about as bad as starting four hours away from the best time) and picks it only
   when the other places are worse still. Such a block gets *⚠ Meridian flip in the
   middle of the run (~01:10): a pier-side step in the series*.
4. **Never at another target's cost.** If running a block wholly before its meridian leaves another target off the
   night, the planner moves that block back to flip-at-start, as long as everything that fitted still fits and more
   targets fit. The block's note then says *Flips at the start (not run wholly before its meridian) so another
   target fits tonight*.

Set **Tracks past the meridian** to what your mount really does (in ASIAIR this is its meridian-flip setting). A
generous margin lets more blocks finish without a flip. If you clear the field and save, SHOBS-SCHED keeps the value
you had saved before (or the 5-minute default); it never turns an empty field into 0, which would make every block
flip at once.

On an **Alt-az** mount there are no flips. Instead, a target that passes higher than 85° gets *⚠ Zenith keyhole*
with the time: an alt-az mount cannot track smoothly through the zenith.

Southern sites work the same way: flips follow the target's hour angle, wherever the meridian is.

### Limits

A target is "up" when it is above **Minimum altitude**, below **Maximum airmass**, and above your horizon profile
(below). All three apply at once.

Why limits matter for photometry: low stars are seen through more atmosphere. They are fainter, redder and twinkle
more, and the extinction changes quickly as they sink. A transit observed from 25° altitude down to 15° has a sloping
baseline that is hard to fit. Keep the minimum altitude where your data is still good.

### Darkness

**Astronomical (Sun −18°)** is the default: the planning darkness runs from astronomical dusk to dawn. On short
summer nights, or at high latitudes where the Sun never gets 18° below the horizon, choose **Nautical (Sun −12°)**
to plan in what darkness there is. The night tab says when there is no dark time at your chosen level.

Transit baselines may run into nautical twilight even when you plan in astronomical darkness: the transit itself
(ingress to egress) must be in the dark, but the out-of-transit stretches can start a little earlier and end a little
later.

If SHOBS-P's finder used another darkness (for example Sun −15°), that value appears in the drop-down too.

### The Moon distance

A bright Moon near your target brightens the sky around it, and the brighter the Moon, the farther that glow
reaches. **Moon distance at least** keeps every target at least this far from the Moon, so you don't have to set a
Moon limit on each target by hand.

- **By Moon phase** (the default) picks the distance from how much of the Moon is lit at the middle of the night:

  | The Moon's lit fraction | Phase | Default distance |
  |---|---|---|
  | under 30 % | crescent | 20° |
  | 30 % to under 70 % | quarter | 35° |
  | 70 % to under 95 % | gibbous | 50° |
  | 95 % or more | full | 70° |

  Waxing and waning count the same. Type your own values in the four **By phase** boxes.
- **Fixed** uses the one distance in **Fixed** every night, whatever the phase.
- **Off** sets no default; only the targets with their own Moon limit have one.

Coming from 1.2.0? The first time 1.3.0 (or later) opens your settings it says so once in the status line: *New in 1.3.0: a
site-wide Moon distance (by Moon phase) now applies to every target without its own “Moon at least”. Setup → Mount
and limits; Off works as before.* Choose **Off** if you want 1.2.0's plans back exactly.

The gray line under the boxes sums it up: *While the Moon is up, targets stay at least this far from it (by phase:
the Moon's lit fraction at the middle of the night). A target's own “Moon at least” (Edit) wins; Find suggested
transits uses it too.*

Some details:

- **Only while the Moon is up.** Once the Moon has set (or before it rises), the distance does not matter.
- **A target's own limit wins.** A target with its own **Moon at least** in its Edit dialog (chapter 5) uses that,
  even when it is smaller than the default. Leave a target's field empty to use this default.
- **It applies to every block**, fixed-time ones (transits, timed minima) as well as flexible ones.
- **Find suggested transits…** skips a transit if the Moon comes closer than this at any time during it, while the
  Moon is up (chapter 5).
- **When the Moon spoils a target**, the reason says which limit was used, for example *Moon too close (38° at best;
  default for a gibbous Moon 50°)*, *Moon too close (38° at best; Setup's default 30°)* with **Fixed**, or *Moon too
  close (38° at best; your limit 40°)* for a target's own limit (chapter 6).
- **The limits:** each value is 0 to 180 degrees. An empty box goes back to its default when you save. A wrong
  value gives, for example, *Moon distance for a gibbous Moon must be between 0 and 180 degrees.*

How this differs from **Moon-brightened sky up to** (in **Suggestions and darks**, below): that one is about how
much the Moon brightens the sky over the whole transit, and is used only by the two Finds. **Moon distance at least**
is a plain distance, used by the planner for every block and by **Find suggested transits…**.

## Horizon profile

If trees or buildings block part of your sky, give SHOBS-SCHED your horizon:

- Type one `azimuth altitude` pair per line, in degrees, for example `120 25` (at azimuth 120°, the horizon is at 25°
  altitude). Commas or semicolons between the numbers work too, and anything after a `#` on a line is ignored.
- Or press **Import CSV…** and choose a CSV or text file with azimuth and altitude columns. Lines that are not two
  numbers are skipped. The message says how many points were read; press **Save** to keep them.
- Leave the box empty for a flat horizon. The minimum altitude always applies on top.

Azimuths run from 0 to 360 (north 0, east 90); altitudes from −5 to 90. Under **Azimuths measured from**, choose
**true north**, or **magnetic north** if your survey was made with a compass; then type your **Declination** (the
magnetic declination, east positive) so the profile is turned to true north.

## Weather

| Field | What it does |
|---|---|
| **Forecast source** | Where the forecast comes from. In 1.4.1 the only source is **Open-Meteo** (free, no account). |
| **Dew when temp − dew point under** | The dew threshold: dew is expected from the first moment in the dark when the air temperature is less than this many degrees above the dew point. 2.5 °C by default. |
| **End dew-sensitive runs at the predicted dew onset** | When ticked (the default), flexible blocks (campaigns and any-time variables) end when dew is expected. Fixed-time blocks are not cut; they get a ⚠ instead. |

The forecast is fetched each time you build a schedule (chapter 6). It covers up to 16 days ahead, and is fetched
again when the saved one is more than three hours old.

## Suggestions and darks

This box holds the filter that **Find suggested transits…** and **Find suggested variables…** use (chapter 5), and
the exposures you already have darks for.

| Field | Unit | Default | What it does |
|---|---|---|---|
| **Host star brighter than V** | mag | 13 | The faint end of the V range. **Find suggested transits…** keeps only planets whose host star is brighter than this. |
| **… and fainter than V** | mag | 9 | The bright end of the V range: the host star must be fainter than this. |
| **Transit depth at least** | % | 0.5 | The shallowest transit **Find suggested transits…** offers. |
| **Baseline each side at least** | min | 30 | The baseline before ingress and after egress that a suggested transit must have, with the star above your limits. |
| **Moon-brightened sky up to** | mag | 3 | How much the Moon may brighten the sky. Both Finds skip targets whose sky the Moon brightens by more than this. |
| **Darks you already have** | s | (none) | The exposures your dark library covers, for example `60, 120, 180`. |

Two gray lines in the box say the same in short: *Find suggested transits keeps only full transits (with this
baseline) of hosts in this V range; both Finds skip targets whose sky the Moon brightens by more than this.* and
*Exposures your dark library covers (at your usual gain and temperature); “Darks to shoot” lists only the others.*

Some details:

- **The V range.** Remember that a brighter star has the smaller V. The bright limit (**Host star brighter than V**)
  is the bigger number. If the two are the wrong way round, Save says *The V range is upside down: brighter than V 9
  and fainter than V 13 leaves nothing (the bright limit is the bigger number).*
- **The V range and variables.** **Find suggested variables…** starts its own **Brightness** range from this V
  range. Once you have searched, that window remembers its own range.
- **The depth** is typed in percent (`0.5` for 0.5 %). The settings file keeps it as a fraction (0.005).
- **An empty field** goes back to its default when you save.
- **The limits:** V from −2 to 25, depth 0 to 100 %, baseline 0 to 600 minutes, Moon-brightened sky 0 to 15 mag.
- **Darks you already have.** Separate the exposures with commas, semicolons or spaces; a trailing `s` is allowed
  (`60s`). Each must be more than 0 and at most 7200 seconds. A value that is not a number gives *Darks you already
  have: “abc” is not an exposure in seconds (e.g. 60, 120, 180).* The night's **Darks to shoot** line (chapter 6)
  leaves these exposures out. When every exposure of the night is on hand, it says *Darks: all on hand (…)* instead.
- **The tags.** The V range, depth and baseline show a source tag like the other fields (*from SHOBS-P* when
  SHOBS-P's finder seeded them, see the note above). The Moon limit and the darks have no tag.

Why the Moon limit matters for photometry: moonlight raises the sky background, and the sky's noise rises with it.
A 1 % transit on a V 12 star that is easy on a dark night can be lost in the noise when the Moon is near and bright.

## Photometry noise (exposure by S/N)

New in 1.4.0. SHOBS-SCHED can now work out, for each target, how precisely your telescope and camera can measure it,
and whether its transit or its light changes will show: the **S/N** and a **verdict** word (**Detectable**,
**Marginal**, **Out of reach** or **Saturates**; chapter 5). For that it needs a few facts about your system that the
other boxes do not hold, such as how many electrons a star of a given magnitude gives each second. This box is where
they come from.

![The Photometry noise (exposure by S/N) box: the Instrument profile drop-down on Automatic, the line "No SHOBS-FLUX profile matches the telescope and camera above (SCHED works without one): …", the seven fields, empty, each with its estimate tag (estimate 19.76, estimate 0.2, estimate 21.5 …), and the gray hint](img/04-photometry-noise.png)

**You do not have to fill it in.** With the box empty, SHOBS-SCHED uses rough estimates for everything, the same
model 1.3.0 used for its exposures. You still get an S/N and a verdict, but the verdict word then carries a **?**
(*Detectable?*, *Marginal?*, *Out of reach?*, *Saturates?*) to say it is a guess, not a measurement.

### Where the numbers come from

Each number is taken from the first of these that has it:

1. **A SHOBS-FLUX instrument profile** (tag *from FLUX*). SHOBS-FLUX is the SHOBS program that measures your
   telescope and camera, on the bench and from your nights under the sky. It keeps what it measured in profiles in the
   `.shobs` folder in your user folder. SHOBS-SCHED only reads them: it never writes them, and it works fully without
   them. A value from the profile is shown grayed, and you cannot type over it: what was measured wins.
2. **What you type in this box** (tag *set here*).
3. **A rough estimate** (tag *estimate*, with the value used, for example *estimate 21.5*). This is either the
   profile's own first guess, or SHOBS-SCHED's.

### Instrument profile

The **Instrument profile** drop-down chooses the SHOBS-FLUX profile to read:

- **Automatic (matches the telescope and camera)**, the default: the profile whose camera is the one in Setup's
  **Camera** field (at your **Gain setting** and **Binning**, when the profile names them), whose telescope has the
  name in Setup's **Telescope** field (when you typed one), and whose focal length is within 3 % of Setup's. (ASIAIR's
  plate-solved focal length moves a few millimeters from night to night, so the match is not exact.) When several
  match, the nearest focal length wins.
- **None (use the values below)**: no profile; your typed values, or rough estimates.
- Each SHOBS-FLUX profile on this computer, by its name, with its telescope and camera in brackets. Choose one when
  Automatic does not find yours.

The line under the drop-down says what is used:

- *✔ SHOBS-FLUX profile … (matches the telescope and camera above; revision 3, 2026-11-06). Its values are shown greyed
  below (from FLUX); empty fields use rough estimates. SCHED only reads it.* (With a profile you chose, it says
  *chosen here* instead of *matches the telescope and camera above*.)
- *No SHOBS-FLUX profile matches the telescope and camera above (SCHED works without one): the values below, or rough
  estimates, are used.*
- *No SHOBS-FLUX profile (you chose none): the values below, or rough estimates, are used.*
- *⚠ The chosen profile … is not in … any more: choose another, or Automatic. Until then the values below, or rough
  estimates, are used.* The profile you chose was deleted or renamed in SHOBS-FLUX.

Two more ⚠ lines can appear while the drop-down is on **Automatic**:

- **Several profiles fit.** SHOBS-FLUX keeps one camera profile per gain. If Setup has no **Gain setting**,
  Automatic picks the profile whose e⁻ per ADU is closest to Setup's **e⁻ per ADU**. If even that cannot tell them
  apart, the line says *⚠ Several SHOBS-FLUX camera profiles fit (…) and Setup has no gain setting to tell them
  apart: choose the profile above (or type the gain setting under Telescope and camera). Until then the values below,
  or rough estimates, are used.* Do either, and **Save**.
- **A newer SHOBS-FLUX.** If a newer SHOBS-FLUX wrote profiles that this SHOBS-SCHED cannot read yet, the line says
  *⚠ A newer SHOBS-FLUX wrote 2 profiles here (…): update SHOBS-SCHED to read them. Until then the values below, or
  rough estimates, are used.* Install the newest SHOBS-SCHED (chapter 2).

### The fields

| Field | Unit | What it is | Estimate when empty |
|---|---|---|---|
| **Zero point** | mag for 1 e⁻/s (main band) | The magnitude of a star that gives one electron a second in your camera, through your telescope, with the star overhead (airmass 1). A bigger number means a more sensitive system. It is the most important number here. | Worked out from your aperture and camera, as 1.3.0's exposure estimate did. |
| **Extinction** | mag per airmass | How much the air dims a star for each extra airmass. | 0.20 (for V) |
| **Dark-sky brightness** | mag/arcsec² | Your sky with no Moon. A bigger number is a darker sky. | 21.5 |
| **Seeing (FWHM, in focus)** | arcsec | How wide a star is in focus. The photometry aperture is 1.5 × this wide in radius. | 3 (or the planner's `fwhm_arcsec`) |
| **Read noise** | e⁻ | The camera's read noise at your gain. | 2 |
| **Dark current** | e⁻/px/s | The camera's dark current at its working temperature. | 0.01 |
| **Scintillation C_Y** | (Young; 1.5 typical) | How strongly your site twinkles. 1.5 is a typical site. | 1.5 |

The "main band" is the band SHOBS-SCHED measures in when nothing else is said: the **green channel (TG)** of a color
camera, or the **clear band (CV)** of a mono camera. The gain, the linear limit and the per-frame overhead come from
the profile when it has them, otherwise from the **Telescope and camera** box (they have no field here).

The gray hint under the box sums it up: *SHOBS-FLUX measures these from your nights (it never needs SCHED, and SCHED
only reads its profiles). Without a profile, type what you know; empty fields use rough estimates, and a verdict from
an estimated zero point gets a “?” (Detectable?, Marginal?, Out of reach?, Saturates?), and transits stay on Longest
safe until a zero point is measured or typed here.*

Some details:

- **Only a measured zero point removes the "?".** The verdict is a guess while the zero point is. Typing your own
  zero point here (*set here*) counts as known, so its verdicts lose the "?" too.
- **Transits and Best precision.** A transit's exposure is chosen for the best precision only when the zero point is
  known: measured by a SHOBS-FLUX profile, or typed here. Until then transits stay on **Longest safe**.
  You can always choose **Best precision** (or pin **Longest safe**) for a target yourself (chapter 5).
- **One peak model.** Whenever a SHOBS-FLUX profile is in use (even one whose zero point is only FLUX's estimate)
  or any value is typed here, every predicted peak (Longest safe's choice, the warnings on your own exposure, and
  **Saturates**) comes from the same noise model, with the profile's seeing and linear limit. With no profile and
  nothing typed, Longest safe chooses the exposure as 1.3.0 did (the peak shown is still the noise model's), and never
  one the verdict calls Saturates; the warnings on your own exposure follow the noise model's peak too.
- **Empty fields.** Leave a field empty for its estimate; the tag shows the value used (*estimate 0.2*).
- **The limits.** Zero point 10 to 30, extinction 0 to 1.5, dark sky 15 to 23, seeing 0.3 to 20, read noise 0 to 50,
  dark current 0 to 10, C_Y 0.1 to 10. A wrong value gives, for example, *✖ Not saved: Scintillation C_Y must be
  between 0.1 and 10.*
- **A value from FLUX is not saved here.** When the profile has a value, your own typed value for that field stays
  saved underneath, unused, and comes back if you choose **None**.

Why this matters for photometry: with these numbers SHOBS-SCHED can tell you, before the night, whether a transit
is deep enough to see with your telescope, and which exposure gives the best light curve. How it works it out is in
chapter 5, "How SHOBS-SCHED works out the S/N".

## Saving

Press the green **Save** button at the bottom. It says **✔ Saved.**, and the status strip says **✔ Setup saved.**

If a value is wrong, nothing is saved and the line says why, for example *✖ Not saved: Longitude must be between
−180 and 180 (degrees, east positive, west negative).* When several values are wrong, it shows the first and says how
many more there are.

**Leaving the page with unsaved changes.** If you go to another page (or close the window) with valid changes, they
are saved for you, and the status strip says *✔ Setup saved: you left the page with changes, so they were kept.* If
the changes cannot be saved, SHOBS-SCHED asks whether to drop them.

**Changing Setup after a schedule was built.** The schedule was worked out with the old values. The Schedule and
OBSPlan pages then show a ⚠ line asking you to build again (chapter 6).

## New versions

New in 1.4.1. One tick box, **Check for new versions at start** (on by default): SHOBS-SCHED asks GitHub once a
day whether a newer version is out and says so on the status strip (chapter 2, "Updating to a new version"). It
never downloads anything. The tick is saved as soon as you change it (no **Save** needed); unticking clears a note
already on the strip.

## Planner settings that are not on the page

A few settings have sensible defaults and no field on the Setup page. They are kept in
`.shobs_sched_settings.json`, in the `options` section, and you rarely need to change them. Close SHOBS-SCHED before
editing the file by hand.

| Setting | Default | What it does |
|---|---|---|
| `baseline_min` | 60 | Minutes of baseline wanted before ingress and after egress of a planned transit. |
| `baseline_trim_min` | 20 | The shortest baseline a side when a transit has to share the night with another fixed target. |
| `overhead_slew_min` | 3 | Minutes between blocks for slew, settle and plate solve. |
| `overhead_focus_min` | 5 | Minutes for autofocus at the start of the night, and after a defocused block. |
| `flip_lead_min` | 12 | How long before the meridian a block starts so the mount flips at the start. |
| `flip_settle_min` | 4 | Minutes lost to a flip. |
| `min_gap_report_min` | 15 | Free gaps shorter than this are not listed. |
| `tc_sigma_warn_min` | 10 | A transit whose predicted mid-time uncertainty is larger than this many minutes gets a ⚠. |
| `fwhm_arcsec` | 3.0 | Typical seeing (star width), for the exposure estimate. |
| `target_peak_frac` | 0.8 | The exposure estimate aims the star's peak at this fraction of the linear limit. |

The suggestion filter (`suggest_vmin`, `suggest_vmax`, `suggest_min_depth`, `suggest_baseline_min`,
`suggest_moon_penalty_max`) and `darks_on_hand` are kept in the same `options` section, but you set them in the
**Suggestions and darks** box (above). So are the Moon distance (`moon_sep_mode`, `moon_sep_fixed`,
`moon_sep_crescent`, `moon_sep_quarter`, `moon_sep_gibbous`, `moon_sep_full`), set in **Mount and limits**, and the
Priority choices and the **Hide out of reach** tick of **Find suggested transits…** (`suggest_exoclock_levels`,
`suggest_exofop_max`, `suggest_hide_out_of_reach`), set in its window (chapter 5). The **Photometry noise** box keeps
its values there too: `snr_profile` (`auto`, `none` or a profile's name), `snr_zp`, `snr_k`, `snr_sky`, `snr_fwhm`,
`snr_read_noise`, `snr_dark` and `snr_cy`.

`fwhm_arcsec` and `target_peak_frac` (in the table above) are used by the S/N too: the first is the seeing when the
Photometry noise box has none, the second the "aim" that **Best precision** and **Longest safe** keep the peak under.

---

Previous: [3. Your first night, step by step](03-first-night.md) · Next: [5. Targets](05-targets.md)
