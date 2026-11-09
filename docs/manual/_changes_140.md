# What changed in 1.4.0 (for the manual writer)

The chapters were NOT edited for 1.4.0, except chapter 9's SHOBS-P DOI (now the concept DOI
**10.5281/zenodo.23417063**). This list says, for each user-visible change, which chapter and which screenshots it
touches. House rule: never reuse an image when its page or dialog changed. Every shot of the **main window** shows
the sidebar's **Version 1.4.0**, so every main-window shot is a reshoot anyway.

## The changes

| # | Change (as the user sees it) | Chapters | Screenshots |
|---|---|---|---|
| 1 | **Edit… → new Exposure section** (it replaces the old single "Exposure" row of Observing): radio buttons **Best precision** (default for transits only once the zero point is measured by SHOBS-FLUX or typed in Setup) / **Longest safe** (default for variables and campaigns, and for transits while the numbers are rough — the grey line then ends *Longest safe until a zero point is measured or typed in Setup (then Best precision is the default; your own choice above always wins).*; 1.3.0's choice while no SHOBS-FLUX profile or typed value is in use (the peak shown is the noise model's, as in the readout), the noise model's peak once a profile or a typed value is in use — so it never picks an exposure the verdict calls Saturates; My exposure's ⚠/✖ peak warnings follow the same rule) / **My exposure** [box] s; your own choice always wins: a click on any button, even the one already chosen, pins that way (untouched, the target follows the default). The grey hint reads *… (the default for transits once a zero point is measured by SHOBS-FLUX or typed in Setup). Longest safe: the longest standard exposure whose predicted peak stays under the aim (1.3.0's rule while no SHOBS-FLUX profile or typed value is in use). My exposure: yours.*; a Group change on an untouched choice shows the new group's default. An Out of reach reason keeps its *(needs 5)* at the end, with a grey hint; **Band** drop-down (only bands with a zero point: the profile's measured bands, else Setup's main band: *Green channel (TG)* on a color camera, *Clear band (CV)* on a mono one) and its source tag (*from FLUX*, *set here*, *estimate (no FLUX profile)*, *estimate (FLUX has no measured zero point yet)*); the magnitude line (*V 11.55 → your green channel (TG) (taken as V)*, or for TR/TB/B/R/I/Sloan *… (R ≈ V − 0.39 for a Sun-like star; SCHED knows only V, …)*); the readout: *Exposure 60 s (Best precision) · predicted peak ~40 % of the linear limit (12,830 ADU)*, *Precision 0.89 ppt per 10-minute bin (~10 frames a bin; the target star alone)*, the coloured **noise budget bar** with *Noise budget: photon 60 % · sky 0 % · scintillation 39 % · read + dark 1 %*, *Transit 1.47 % deep, 2.4 h long: 14 ten-minute bins in transit* (variables: *Range 0.56 mag (VSX; 40 % in flux), against one 10-minute bin*), the **S/N and verdict word** (green *Detectable*, yellow *Marginal*, plain *Out of reach*, plain *Saturates* whenever the predicted peak is over the linear limit — then the Exposure line turns yellow; from rough numbers the same word with a “?”: *Detectable?*, *Marginal?*, *Out of reach?*, *Saturates?*) with its reason (*peak ~1070 % of your linear limit at 60 s (shorten it or defocus)*), and the grey line *Worked out at airmass … (its highest from your site) under a dark sky; …* (plus *Rough estimate (no measured zero point): the verdict word carries a “?”.*); the **precision vs exposure plot** (log exposure axis, *over linear* shaded yellow, *saturated* pink, the chosen exposure circled in green; a click picks the nearest standard exposure as My exposure); the **Comps** line and **Check comps** button (asks APASS at VizieR, dialog's green busy bar lower left; result ✔ / ⚠ / ✖ under it). Typing an exposure selects My exposure; My exposure empty → *✖ Not saved: My exposure is empty: …* | 5 (Edit dialog, table of fields, "Why an own exposure matters"), 1 (the "Exposures must stay linear" bullet), 11 (glossary: S/N, verdict, zero point, scintillation, noise budget, ppt, Best precision, Longest safe) | **RESHOOT** `img/05-edit-dialog.png`; suggest a **new** shot of a transit's Exposure section (e.g. `img/05-edit-exposure.png`) showing the readout, bar, verdict, plot and comp line |
| 2 | **Setup → new box "Photometry noise (exposure by S/N)"**: **Instrument profile** drop-down (*Automatic (matches the telescope and camera)*, *None (use the values below)*, then each SHOBS-FLUX system profile id with its telescope and camera), the status line (*✔ SHOBS-FLUX profile … (matches the telescope and camera above; revision …)*, or *No SHOBS-FLUX profile matches …*, or *⚠ The chosen profile … is not in … any more*), and seven fields with tags: Zero point, Extinction, Dark-sky brightness, Seeing (FWHM, in focus), Read noise, Dark current, Scintillation C_Y. A value the profile measured is shown greyed with *from FLUX* and wins; a typed one says *set here*; an empty one *estimate 21.03* (the value used). With no gain setting in Setup and several FLUX camera profiles (gains) fitting, Automatic takes the one whose e⁻/ADU is Setup's; if it still cannot tell: *⚠ Several SHOBS-FLUX camera profiles fit (…): choose the profile above …*. A profile from a newer FLUX: *⚠ A newer SHOBS-FLUX wrote N profile(s) here (…): update SHOBS-SCHED …*. The hint under the box ends “… gets a “?” (Detectable?, Marginal?, Out of reach?, Saturates?), and transits stay on Longest safe until a zero point is measured or typed here.” A typed zero point counts as known: no “?”, and transits default to Best precision. The grey hint below. Wrong values: *✖ Not saved: Scintillation C_Y must be between 0.1 and 10.* | 4 (new section; the "Advanced options" table could list snr_profile, snr_zp … snr_cy and suggest_hide_out_of_reach) | **RESHOOT** `img/04-setup-page.png` (the new box sits at the bottom of the left column), `img/02-first-start.png`, `img/03-setup-saved.png` (whole-page shots) |
| 3 | **Targets page**: a **Verdict** column in all three boxes (*Detectable*, *Marginal*, *Out of reach*, *Saturates*, the same with “?” from rough numbers, or *—* when there is nothing to judge: no V, or a campaign with no VSX range); the **Campaigns** box has new **Type** and **Period** columns (the star's VSX type and period, kept when a variable is moved to Campaigns; *—* otherwise). The details line now says *exposure: Best precision (by S/N)* / *exposure: Longest safe* / *exposure 60 s (yours)* and, when there is one, *Detectable? (at its best airmass, dark sky): S/N ~63 (rough estimate, no measured zero point): …* (a suggestion: *(on its night)*). | 5 (the three boxes' column lists, the details line) | **RESHOOT** `img/05-targets-page.png`, `img/03-targets-two.png`, `img/01-window-parts.png` |
| 4 | **Find suggested transits**: new tick **Hide out of reach** with the grey words *(transits whose S/N would be under 5 with your telescope; each suggestion's Verdict shows on the Targets page)*; remembered. The result line adds *N out of reach left out (Hide out of reach).* and, for guesses it did not hide, *N rough estimates kept (“Out of reach?”: no measured zero point, so not hidden).* Each suggestion's verdict is for its own night (airmass and Moon). | 5 (Find suggested transits) | **RESHOOT** `img/05-find-transits.png` |
| 5 | **Schedule**: the nightly table's Exposure reads **"120 s (S/N)"** for an exposure chosen by S/N (no *(est.)* then; an S/N exposure that cannot keep under the aim shows *(est.)* instead); every block's **Notes lead with the verdict** (*Detectable (S/N 18): a 1.00 % dip against 1.7 ppt per 10-min bin, 9 bins in transit* (0.01 ÷ (0.0017 ÷ √9) ≈ 18); with no measured zero point *Detectable? (S/N ~63, rough estimate): …*; too faint *Out of reach (S/N 3.1, needs 5): …* or *Out of reach? (S/N ~3.1, needs 5, rough estimate): …*; over the linear limit *Saturates: peak ~140 % of your linear limit at 120 s (shorten it or defocus)*). Without a FLUX profile transits are on Longest safe, so their Exposure reads *… (est.)*. | 6 (the table's Exposure and Notes rows; examples in "Reading a night") | **RESHOOT** `img/03-night-tab.png`, `img/06-night-tab.png`, `img/06-night-tab-lower.png` |
| 6 | **Flip notes by type** (item 3): a variable's or campaign's block that starts just before its meridian now says *Start before the meridian and let it flip (~22:40). Pre-flip frames carry no weight in the night mean.*; a transit's *Start before the meridian and let it flip (~22:40). Pre-flip frames are kept; SHOBS-P fits the flip step.* A transit's baseline note after such a flip reads *Baseline N min before ingress (after the flip; the kept pre-flip frames add more), …* | 3 (line 82), 4 (line 177), 6 (lines 102 and 206): replace *"…; drop the pre-flip frames"* | Any shot whose table shows a flip note (`img/03-night-tab.png`, `img/06-night-tab.png`) — already reshoots above |
| 7 | **OBSPlan / plan card**: the Exposure cell shows the exposure ("120 s (S/N)", no *est.* for S/N exposures) with the **verdict word under it** (on screen: green / yellow tint for Detectable / Marginal; on paper: bold); the text card says *exposure 120 s (S/N) · Detectable · frames …*. | 7 (the nightly table description, the *est.* sentence) | **RESHOOT** `img/07-obsplan-page.png`, `img/07-plan-card-print.png` (the printed card's Exposure column changed) |
| 8 | **Credits and sources**: SHOBS-P now cited with its concept DOI *doi:10.5281/zenodo.23417063* (was …064, the old v2.2.16 record); new entries **APASS DR9 (via VizieR)**, **SHOBS-FLUX**, **Scintillation** (Young 1967; Osborn et al. 2015) and **A Sun-like star's colours** (Willmer 2018); the Moonlit sky entry mentions the S/N. Header "SHOBS-SCHED 1.4.0". | 9 (DOI fixed in the text already; the list of sources) | **RESHOOT** `img/09-credits-page.png` |
| 9 | **Self-test**: new section *4b. Exposure by S/N* (shared shobs_profile.py version, no profile → rough estimate, cold start = 1.3.0's peak, the example SHOBS-FLUX profile found, rough numbers keep a transit on Longest safe, a typed zero point gives Best precision and one peak model, TOI-2046 b by S/N → *Detectable?*, a saturating exposure → *Saturates?*, the Moon on a measured sky, cold start: Longest safe = 1.3.0's rule, with a profile Longest safe never says Saturates, flip notes, SHOBS-P DOI). | 2 (what the self-test checks), 10 (troubleshooting, if it lists sections) | — |
| 10 | **Busy bar**: Check comps in the Edit window uses the dialog's green busy bar (*Asking APASS (VizieR) for comparison stars…*). | 8 (list of jobs that show the bar) | optional |

## Facts the chapters may quote

* Verdict thresholds: S/N ≥ 10 Detectable, 5–10 Marginal, under 5 Out of reach; Saturates whenever the predicted
  peak is over the linear limit. From a rough estimate (no zero point measured by SHOBS-FLUX or typed in Setup) the
  same word with “?”; the same rule decides a transit's default (Best precision once the zero point is known).
* Default exposure for a transit: Best precision only once the zero point is measured (SHOBS-FLUX) or typed in Setup;
  Longest safe until then. Variables and campaigns: Longest safe. An explicit choice in Edit always wins.
* The Moon: its light is added to the measured sky (not the dark-sky penalty subtracted again).
* S/N of a transit = depth ÷ (precision per 10-minute bin ÷ √(bins in transit)); bins in transit = duration ÷ 10 min.
  A variable's: its VSX range (as a flux fraction, 1 − 10^(−0.4 Δmag)) ÷ the precision of one 10-minute bin.
* The precision is the target star alone (comparison stars add a little more); aperture radius 1.5 × FWHM.
* Where the numbers come from: a SHOBS-FLUX profile (from FLUX) → Setup's Photometry noise box (set here) → rough
  estimates. Cold-start values: zero point from 1.3.0's exposure model, extinction 0.20 (V), dark sky 21.5 mag/″²,
  seeing 3″, read noise 2 e⁻, dark current 0.01 e⁻/px/s, C_Y 1.5, overhead 3 s.
* SCHED never writes SHOBS-FLUX's profiles (folder `~/.shobs`) and works fully without them.

## After the manual writer's review

Changed after the writer's review of 8 Nov 2026 (builder's second round, on top of the first review fixes above).
Exact new wording in *italics*.

* **One peak model (item 3).** Longest safe, My exposure's ⚠/✖ peak warnings and the *Saturates* check use the noise
  model's predicted peak whenever a SHOBS-FLUX profile is in use (even one whose zero point is only FLUX's own
  estimate) or any value is typed in Setup's Photometry noise box. Only with no profile and nothing typed (SCHED's
  bare cold start) does Longest safe still choose the exposure exactly as 1.3.0 did — and even then it never hands on
  an exposure the readout would call *Saturates?* when a shorter standard exposure stays under the aim. Longest safe
  can read *Saturates* only when even the shortest standard exposure (0.75 s) is over the linear limit (then: defocus).
  Example (the 14in RC example profile, V 9.0, airmass 1.3): 1.3.0's rule took 10 s (*Saturates?*, peak ~107 %);
  Longest safe now takes 5 s.
* **Longest safe note with a profile or typed values** (TOI-2046 b, the example profile, airmass 1.3): *Longest safe: peak ~62 % of your 32k linear limit (~19,706
  ADU)* (defocused: *Longest safe defocused (stars ~1.5× wider): …*); none fits: *⚠ even 0.75 s may saturate
  (predicted peak ~… ADU, limit 32,000); defocus*.
* **One peak number on the cold start (finding B).** With no profile the peak in Longest safe's note (and in My
  exposure's note and the Schedule's cautions) is now the same number as the Edit readout's *predicted peak ~… %
  of the linear limit (… ADU)* (WASP-12 b, 250 mm f/6.4: both read the readout's value). The exposure chosen is
  unchanged (1.3.0's).
* **Edit hint (Longest safe):** *Longest safe: the longest standard exposure whose predicted peak stays under the aim
  (1.3.0's rule while no SHOBS-FLUX profile or typed value is in use). My exposure: yours.*
* **Plural (finding A):** *Precision … per 10-minute bin (~1 frame a bin; the target star alone)* for one frame a bin
  (*~10 frames a bin* otherwise).
* **One bracket (finding E):** a rough Saturates reason now ends *(shorten it or defocus; rough estimate)* (was
  *(shorten it or defocus) (rough estimate)*); e.g. the Targets details line *Saturates? (at its best airmass, dark
  sky): peak ~616 % of your linear limit at 600 s (shorten it or defocus; rough estimate)*, Edit's reason the same.
  A reason that is just *(defocus)* reads *(defocus; rough estimate)*. The Schedule note is unchanged: *Saturates?
  (rough estimate): peak ~… (shorten it or defocus)*.
* **S/N example (finding D):** *Detectable (S/N 18): a 1.00 % dip against 1.7 ppt per 10-min bin, 9 bins in transit*
  (0.01 ÷ (0.0017 ÷ √9) ≈ 18; the old 1.1 ppt would give 27).
* **Status strip credits line (finding F):** APASS added at the end of the data list (it fits the pattern and the line
  wraps if needed): *© Shiloh Hill Observatory    Data: NASA Exoplanet Archive, AAVSO VSX, CDS (VizieR, SIMBAD),
  Open-Meteo, ExoClock, ExoFOP, APASS    Night plot after TAPIR    Full list: Credits and sources*.
* **Suggestions (finding C):** no visible change; each suggestion's verdict is worked out in the default mode (Longest
  safe on rough numbers, Best precision once the zero point is measured or typed), and it is now labelled so
  internally (the docstrings said Best precision).
* **Self-test 4b:** *cold start: Longest safe is 1.3.0's rule* and *with a profile, Longest safe (60 s) never says
  Saturates* replace *Longest safe on rough numbers is 1.3.0's rule*.
* Unchanged from the first review round and confirmed: typed zero point = known (no “?”, Best precision default, no
  *Longest safe until …* line); a click on any radio button pins that mode; transit flip note *Start before the
  meridian and let it flip (~22:40). Pre-flip frames are kept; SHOBS-P fits the flip step.*; Out of reach keeps
  *needs 5* (Edit reason *… (needs 5; rough estimate, no measured zero point)*, Targets *S/N 3.1 (needs 5): …*,
  Schedule *Out of reach (S/N 3.1, needs 5): …*).

## After the Fable review of 8f834b8..dd898f1

* **My exposure that saturates always warns (Fable S1).** On the bare cold start 1.3.0's notes warned only from 1.5×
  the linear limit, so a My exposure between 1× and 1.5× read *Saturates?* with no ⚠ (and no caution in the
  Schedule). Now any own exposure over the limit gets the noise model's note: *⚠ Your exposure: predicted peak ~41k,
  over your 32k linear limit — shorten 180 s or defocus* (✖ from twice the limit). Below the limit the 1.3.0 wording
  stays.
* Manual ch 4 and 5: "exactly 1.3.0" reworded — on the cold start Longest safe *chooses* as 1.3.0 did, the peak shown is
  the noise model's, and it never hands on a Saturates exposure.

## After John's first look (8 Nov 2026)
- Edit → Exposure plot: the "over linear" and "saturated" shading now starts where the curve's own raw peak (bias included,
  like the limits) reaches each limit (`snr.exposure_at_peak`). It used peak/t at the shortest exposure, which put the
  whole bias into the slope: TOI-6130 b (502 ADU bias) showed "saturated" from ~83 s against a true ~546 s. Test:
  tests/test_review140d.py.
- Setup → Save now refreshes the Photometry noise box: its *estimate …* tags show the values in use for the telescope and
  camera just saved (they said a bare *estimate* until the page was reloaded). Test: tests/test_review140d.py.

## After the reshoot evening (8 Nov 2026)

- **Check comps gets round a VizieR TAP outage.** On 8 Nov the main VizieR site (CDS) was down (a security issue);
  its TAP service timed out on every query. The variable search got through on its ASU fallback, but the comp check
  (II/336/apass9 by TAP only) could never answer. It now falls back to VizieR ASU at CDS, then the CfA and IDIA
  mirrors, as the VSX lookups do (`targets._comps_asu`; `parse_asu_tsv` takes a key column and skips the units line
  of a numeric key). Same cache, same result line.
- **An expected lookup failure is one log line.** A job that fails with `LookupError_` / `NotFound` / `FetchError`
  (a service did not answer, nothing saved) no longer writes a traceback to sched.log; anything else still does.
- **The comp result line wraps** (460 px, like the other lines), so a long *✖ Comps not checked: …* no longer widens
  the Edit dialog and clips its right side.
- **No verdict says why in its own words:** *no amplitude to judge (VSX gives no range)* for a VSX star without a
  range, *no amplitude to judge (not a VSX variable)* for any other star (e.g. HD 219134 as a campaign; was
  *(VSX range)* for both). Manual ch 5 updated.
- Tests: tests/test_review140e.py (ASU fallback, mirrors in turn, all down, TAP first, units line, the two
  wordings, expected failures); test_review140's offline check now takes the ASU route offline too.
