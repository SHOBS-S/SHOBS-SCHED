# 11. Glossary

Terms used in SHOBS-SCHED and in this manual, in alphabetical order. Words in **bold** inside an entry have their own
entry.

**Airmass** — How much atmosphere a star's light passes through: 1 at the zenith, about 1.15 at 60° altitude, 2 at
30°. Stars dim, redden and twinkle more with airmass, and the change is fastest low down. The night plot's left axis
is airmass; Setup's **Maximum airmass** is a limit.

**Alt-az mount** — A mount that moves in altitude and azimuth. It never makes a **meridian flip**, but cannot track
smoothly through the zenith (see **zenith keyhole**). Choose **Alt-az** under **Mount** in Setup.

**Any-time variable** — A variable star with no single moment worth catching: a pulsator whose whole cycle is useful,
or a slow long-period star. Planned as a **flexible block** at its best airmass.

**Baseline** — In transit work, the steady out-of-transit light before **ingress** and after **egress**. The fit
needs it to know the star's normal brightness and its slow trends. SHOBS-SCHED plans 60 minutes a side by default,
trimmed to no less than 20 when two targets share a night.

**Blackout** — See **no-transit window**.

**Block** — A stretch of the night given to one target. A **fixed** block's time is set by the sky (a transit, a
timed minimum or maximum); a **flexible** block is placed where the target is best.

**BJD_TDB** — Barycentric Julian Date in Barycentric Dynamical Time: a time stamp corrected to the center of mass of
the Solar System, the standard for transit times. A transit's **Tc** is given in it.

**Campaign** — A star or field you come back to on many nights, with an optional weekly **goal** and rules of its
own. One of the three target types (plum).

**Composite ephemeris** — The NASA Exoplanet Archive's planet-list ephemeris for a planet. It can be years old;
SHOBS-SCHED replaces it with the **most precise ephemeris** for planets you plan.

**Darkness** — When the Sun is low enough to plan in: astronomical darkness (Sun below −18°) or nautical (below
−12°). Chosen under **Darkness to plan in** in Setup.

**Dew point, spread** — The dew point is the temperature at which moisture condenses. The spread is the air
temperature minus the dew point; when it falls under Setup's threshold (2.5 °C by default), dew on the optics is
likely.

**Depth** — How much a star dims during a transit, in percent (or as a fraction): 1 % means the star loses one
hundredth of its light. Deeper transits are easier to detect.

**Duration (T14)** — The length of a transit from **ingress** to **egress**.

**Eclipsing binary** — Two stars orbiting each other in our line of sight, so each eclipses the other. Their
**minima** happen at predictable times; timing them shows whether the period is changing. VSX types EA, EB, EW.

**Egress** — The end of a transit, when the planet has left the star's disk.

**Ephemeris** — A planet's (or a variable star's) **period** and a reference time (**Tc** or epoch), from which every
future event is predicted. Its uncertainty grows with every cycle away from the reference time.

**ExoClock** — A project (www.exoclock.space) that keeps the transit times of known planets up to date for ESA's
Ariel space mission, with help from amateur and professional observers. It ranks its planets **Alert**, **High**,
**Medium** or **Low** by how badly new timings are needed. **Find suggested transits…** can search only the levels
you tick (its **Priority** rows).

**ExoFOP (ExoFOP-TESS)** — The Exoplanet Follow-up Observing Program website for TESS, run by NASA's Exoplanet
Science Institute at Caltech. Its **TOI** table lists the TESS planet candidates with their ephemerides and the
follow-up groups' priorities (see **SG1 priority**).

**Exposure estimate** — SHOBS-SCHED's first guess of a good exposure for a star, from its V magnitude, your aperture,
camera and the seeing: the longest standard exposure that keeps the star's peak under about 80 % of your **linear
limit**. Marked *(est.)* in the Schedule page's table and *est.* on the plan card.

**Fixed-time target** — A target whose block is set by the sky: a transit, or a timed minimum or maximum. Planned
before the flexible ones.

**Flats** — Frames of an evenly lit field, used to correct dust shadows and vignetting. They must be taken at the same
camera angle (and focus) as the lights.

**Flexible block** — A block placed where its target is best (near its meridian), of the target's **Block length**.
Campaigns and **any-time variables** get flexible blocks. On an equatorial mount it runs wholly before the meridian
when a full block fits there, otherwise it starts just before the meridian (see **meridian flip**).

**Goal** — A campaign's wanted number of **Done** nights a week (Monday to Sunday). A campaign behind its goal is
planned one priority level higher.

**Ingress** — The start of a transit, when the planet starts to cross the star's disk.

**J2000** — The standard reference frame for star positions (RA and Dec), fixed at the start of the year 2000.

**Linear limit** — The raw brightness (in ADU) above which your camera's response is no longer proportional to the
light. A star whose brightest pixel goes past it is measured too faint. Set it in Setup; the exposure estimate keeps
star peaks under it.

**Meridian** — The north–south line through the zenith. A star is highest (lowest **airmass**) when it crosses the
meridian.

**Meridian flip** — On a German equatorial mount, the turn to the other side of the pier when a target crosses the
**meridian**. It costs a few minutes and moves the star on the sensor, so a flip in the middle of a run leaves a step
in the light curve. For a **flexible block** SHOBS-SCHED avoids the flip when it can: the block runs wholly before the
meridian, ending inside your **tracking margin**. When a full block does not fit there, or when running it there
would leave another target without a block that night, the block starts about 12 minutes before the meridian, so the
flip happens at the start. A flip in the middle of a flexible run is a last resort, with a ⚠.

**Minimum, maximum** — The faintest or brightest point in a variable star's cycle. An eclipsing binary's minimum or an
RR Lyrae star's maximum can be timed to minutes.

**Mirror** — A copy of a service on another server. When SIMBAD or VizieR at CDS does not answer, SHOBS-SCHED asks a
mirror (VizieR at the CfA, SIMBAD and VizieR at IDIA in Cape Town).

**Most precise ephemeris** — Of all the ephemerides published for a planet, the one with the smallest predicted
**Tc** uncertainty for the coming weeks. SHOBS-SCHED uses it for planets you add, plan or keep.

**Moon distance (Moon at least)** — The closest the Moon may come to a target while the Moon is up, in degrees. A
target's own **Moon at least** (Edit dialog) wins; otherwise Setup's **Moon distance at least** applies, which by
default goes by the **Moon phase** that night (crescent 20°, quarter 35°, gibbous 50°, full 70°), or is one fixed
value, or off.

**Moon phase** (for the Moon distance) — How much of the Moon is lit at the middle of the night: under 30 % is a
crescent, under 70 % a quarter, under 95 % gibbous, and 95 % or more full. Waxing and waning count the same.

**Moon penalty** — How much the Moon brightens the sky at a target, in magnitudes, from the Moon's phase, height and
distance (Krisciunas & Schaefer 1991). A brighter sky means more noise. Suggestions are limited by Setup's
**Moon-brightened sky up to** (3 magnitudes by default).

**No-transit window** (blackout) — A time when a campaign or variable must not be observed, because one of its star's
planets is transiting. Set in the target's Edit dialog.

**O − C** — Observed minus calculated: how much an observed event (a transit, a minimum) differs from the
**ephemeris**' prediction. A trend in O − C shows a changing period; new timings of poorly known planets are valuable
for this reason.

**Period** — The time between successive transits of a planet, or one cycle of a variable star.

**Pixel scale** — Arcseconds of sky per camera pixel: 206.265 × pixel size (µm) × binning ÷ focal length (mm).

**Plan card** — The printed plan: for each night, the dark hours, Moon, weather, night plot, nightly table, flats,
darks and free time. Made on the OBSPlan page.

**Position angle (camera angle)** — The angle of the camera on the sky. Written to the ASIAIR and NINA lists from
Setup's **Usual camera angle**.

**Priority** — **High**, **Normal** or **Low**. Decides the order flexible targets are placed in, and which of two
colliding fixed-time targets wins. Not to be confused with the **Priority** rows of **Find suggested transits…**,
which are **ExoClock** and **ExoFOP** priorities: which planets those projects want timed.

**SG1 priority** — The priority (1 = highest … 5) that TESS follow-up Sub Group 1 (seeing-limited ground-based
photometry) gives a **TOI** in **ExoFOP**'s table. SHOBS-SCHED reads its SG1B column. In **Find suggested
transits…**, **ExoFOP SG1 priority 1–2** searches the TOIs of priority 1 or 2.

**Saturation** — A pixel at its maximum value; its light is lost. Saturation happens above the **linear limit**.

**Signal-to-noise (S/N)** — How clearly a signal stands out from the noise. For a suggested transit, roughly its
depth × √(minutes in transit) ÷ the noise per minute for that star and sky.

**Site's clock** — The clock in your site's time zone, with its daylight-saving changes. Every time in SHOBS-SCHED is
on it, labeled for example *Local time (UTC−5)*.

**Tc (T0, mid-transit time)** — The moment the planet is in the middle of its transit. The ephemeris' reference time
is one Tc; **Tc ±** is the predicted uncertainty of a future one.

**TOI (TESS Object of Interest)** — A TESS planet candidate, named like **TOI-1234.01**. Many are not yet in the
NASA Exoplanet Archive's planet list; SHOBS-SCHED then takes their ephemeris, depth and duration from **ExoFOP**, and
estimates V from the TESS magnitude (V ≈ TESS mag + 0.5). Those that turned out false positives or false alarms are
left out.

**Tracking margin** — How long your equatorial mount can track past the **meridian** before it must flip: Setup's
**Tracks past the meridian** (5 minutes by default). A block that ends inside it needs no flip; its note says when to
stop at the latest (*stop by 23:47 at the latest*).

**Transit** — A planet passing in front of its star, dimming it by the transit's **depth** for its **duration**. One
of the three target types (green).

**Twilight** — Civil (Sun 0° to −6°), nautical (−6° to −12°) and astronomical (−12° to −18°). The night plot shades
each band.

**V magnitude** — A star's brightness in the visual (V) band. Smaller is brighter; 5 magnitudes is a factor of 100.

**Variable** — A star whose brightness changes, from AAVSO VSX. One of the three target types (navy).

**VSX** — The AAVSO International Variable Star Index: names, types, periods and epochs of variable stars.

**Zenith keyhole** — On an **alt-az mount**, the region near the zenith that the mount cannot track through smoothly.
SHOBS-SCHED warns when a target passes above 85°.

---

Previous: [10. Troubleshooting](10-troubleshooting.md) · Back to the [table of contents](README.md)
