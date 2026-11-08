# 9. Credits and citing

SHOBS-SCHED stands on other people's work: catalogs and online services, a published plot design, Python libraries
and published methods. When you publish results from nights planned with it, please credit the sources your work
used.

## The Credits and sources page

1. Click **Credits and sources** near the bottom of the sidebar. It is not a step: **Back** returns you to the page
   you came from.
2. At the top: **SHOBS-SCHED 1.3.0 © 2026 Shiloh Hill Observatory**, the license line, and **How to cite
   SHOBS-SCHED** with the citation and a **Copy** button (see "How to cite SHOBS-SCHED" below).
3. Below that, the full list, in four parts:
   - **CATALOGS AND SERVICES**: the NASA Exoplanet Archive, AAVSO VSX, VizieR (CDS), SIMBAD (CDS), ExoClock,
     ExoFOP-TESS and Open-Meteo. The VizieR and SIMBAD entries also name the mirrors SHOBS-SCHED asks when CDS does
     not answer: VizieR's at the CfA and at IDIA (Cape Town), and SIMBAD's at IDIA. ExoClock and ExoFOP-TESS are new
     in 1.3.0: they give the optional priorities of **Find suggested transits…** (chapter 5).
   - **DESIGN AND CODE**: TAPIR (the night plot's design) and SHOBS-P (where SHOBS-SCHED's sky arithmetic started).
   - **LIBRARIES**: Python and Tk, NumPy, Matplotlib, and tzdata (the IANA Time Zone Database).
   - **METHODS**: the Sun and Moon positions (the Astronomical Almanac's low-precision formulas), the moonlit sky
     brightness (Krisciunas & Schaefer 1991), precession (IAU 1976), and the transit-timing uncertainty.

   For each source the page says what SHOBS-SCHED uses it for and gives the reference. Sentences in quotes are the
   acknowledgements each source asks for, in its own words.
4. To keep a copy, click **Save credits as text…**. It offers `SHOBS-SCHED_credits.txt` in your Downloads folder.

![The Credits and sources page: the copyright and license line, How to cite SHOBS-SCHED with its Copy button, the list of catalogs, design, libraries and methods, and the Save credits as text… and Back buttons](img/09-credits-page.png)

The saved text starts with the same citation (*How to cite SHOBS-SCHED:*) before the list.

The second line of the status strip is a short version of the same credits, always on screen (in 1.3.0 it names
ExoClock and ExoFOP too).

## The night plot and TAPIR

The Schedule page's night plot follows the design of the airmass plot in **TAPIR** (`plot_airmass.cgi`) by Eric
Jensen, Swarthmore College: Jensen, E. 2013, *Tapir: A web interface for transit/eclipse observability*,
Astrophysics Source Code Library, [ascl:1306.007](https://ascl.net/1306.007);
<https://github.com/elnjensen/Tapir> (GPL-3). The ideas taken from it: airmass and elevation axes, twilight
shading with the times across the top, the nautical-twilight ± 1.5 h time span, and the Moon's line growing with its
phase. SHOBS-SCHED's plot is its own code (Matplotlib); no TAPIR code is copied.

## What to include in a paper or article

A plan is not a result, so most work needs only the sources of the *data* you report, credited through SHOBS-P or
your reduction program. When the planning itself matters to your work (for example a transit timing from a planet
SHOBS-SCHED suggested, or an ephemeris it chose), include:

- the **NASA Exoplanet Archive** for planet ephemerides and the transit catalog;
- **AAVSO VSX** (and **VizieR**) for variable-star elements and suggestions;
- **SIMBAD** for positions of stars added by name;
- **ExoClock** when you used its priorities, or a planet came from its list: Kokori, A. et al. 2021, Experimental
  Astronomy 53, 547 (ExoClock I); Kokori, A. et al. 2022, ApJS 258, 40 (ExoClock II); Kokori, A. et al. 2023, ApJS
  265, 4 (ExoClock III);
- **ExoFOP-TESS** when you used its SG1 priorities, or observed a TESS candidate (TOI) from its table, with the
  acknowledgement it asks for: *"This research has made use of the Exoplanet Follow-up Observation Program (ExoFOP;
  DOI: 10.26134/ExoFOP5) website, which is operated by the California Institute of Technology, under contract with
  the National Aeronautics and Space Administration under the Exoplanet Exploration Program."*

A mirror serves the same CDS data, so a lookup answered by the CfA or IDIA mirror is credited to VizieR or SIMBAD as
usual.

When in doubt, include the saved credits text in an appendix or data note.

## How to cite SHOBS-SCHED

SHOBS-SCHED is archived on Zenodo, with the DOI **10.5281/zenodo.23674791** (<https://doi.org/10.5281/zenodo.23674791>).
This is the *concept* DOI: it stands for all versions of SHOBS-SCHED and always leads to the newest one, so it stays
right however often the program is updated. Cite it with the version you used.

The **How to cite SHOBS-SCHED** section at the top of the Credits and sources page gives the citation, ready to
paste; press **Copy** to put it on the clipboard (*✔ Citation copied to the clipboard.*). For 1.3.0 it reads:

*Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler, version
1.3.0, Zenodo, doi:10.5281/zenodo.23674791*

The version in it is always the one you are running (it is also at the bottom of the sidebar).

The program folder also has a **CITATION.cff** file, the standard citation file that GitHub and reference managers
read. It holds the same details: title, version, authors (John William Armbrust and Shiloh Hill Observatory), the
license, the code's home (<https://github.com/SHOBS-S/shobs-sched>) and the DOI.

For a reference list in BibTeX, for example:

```bibtex
@software{shobs_sched,
  author    = {Armbrust, John William and {Shiloh Hill Observatory}},
  title     = {{SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler}},
  version   = {1.3.0},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23674791},
  url       = {https://doi.org/10.5281/zenodo.23674791}
}
```

SHOBS-SCHED has no ASCL entry yet.

SHOBS-P, the sister program, is archived on Zenodo: doi:10.5281/zenodo.23417063.

## License

SHOBS-SCHED is free software under the GNU General Public License, version 3 or later. You may use it, share it and
change it under that license's terms. It comes with no warranty. The full text is in the **LICENSE** file in the
program folder.

## How SHOBS-SCHED was made

SHOBS-SCHED was designed at Shiloh Hill Observatory and checked against the observatory's own hand-made plans (the
self-test still compares it with one of those weeks). Most of the code was written with the help of an AI assistant
(Anthropic's Claude), working to the observatory's specifications.

Bug reports, questions and ideas are welcome (see [10. Troubleshooting](10-troubleshooting.md)).

---

Previous: [8. The busy bar and messages](08-busy-bar-and-messages.md) · Next: [10. Troubleshooting](10-troubleshooting.md)
