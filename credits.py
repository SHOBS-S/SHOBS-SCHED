"""SHOBS-SCHED credits: the copyright line, how to cite SCHED, and every catalog, service, design and library it
uses, with the acknowledgement each source asks for (the Credits and sources page; same layout as SHOBS-P's)."""
from __future__ import annotations

COPYRIGHT = "© 2026 Shiloh Hill Observatory"
LICENSE_LINE = ("SHOBS-SCHED is free software under the GNU General Public License, version 3 or later; it comes "
                "with NO WARRANTY. See the LICENSE file.")
# The status-bar line (as SHOBS-P's): copyright, what it is built on, its data, and where the full list is.
STATUS_LINE = ("© Shiloh Hill Observatory    Data: NASA Exoplanet Archive, AAVSO VSX, CDS (VizieR, SIMBAD), "
               "Open-Meteo    Night plot after TAPIR    Full list: Credits and sources")

# A DOI (Zenodo) or ASCL ID once SCHED has one; the Credits page shows "How to cite" only when one is set.
CITATION_DOI = ""
ASCL_ID = ""


def citation_text(version: str) -> str:
    """How to cite SHOBS-SCHED, or "" while it has no DOI / ASCL entry."""
    if not (CITATION_DOI or ASCL_ID):
        return ""
    where = ", ".join(x for x in (f"doi:{CITATION_DOI}" if CITATION_DOI else "",
                                  f"ascl:{ASCL_ID}" if ASCL_ID else "") if x)
    return (f"Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler, "
            f"version {version}, {where}")


# (section, [(name, used for, citation / acknowledgement, web address)])
CREDITS = [
    ("Catalogs and services", [
        ("NASA Exoplanet Archive", "Transit targets: period, mid-transit time (the most precise published "
         "ephemeris), duration, depth and host star; the transit catalog behind Find suggested transits; a star's "
         "known planets for no-transit windows (TAP service).",
         "\"This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute "
         "of Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet "
         "Exploration Program.\" Akeson, R. L. et al. 2013, PASP 125, 989.",
         "https://exoplanetarchive.ipac.caltech.edu"),
        ("AAVSO VSX", "Variable-star targets: type, period, epoch and range; the stars offered by Find suggested "
         "variables.",
         "Watson, C. L., Henden, A. A. & Price, A. 2006, Society for Astronomical Sciences Annual Symposium 25, 47.",
         "https://vsx.aavso.org"),
        ("VizieR (CDS)", "Access to VSX (catalog B/vsx) when VSX itself does not answer, and for the variable "
         "search.",
         "\"This research has made use of the VizieR catalogue access tool, CDS, Strasbourg Astronomical "
         "Observatory, France (DOI : 10.26093/cds/vizier).\" Ochsenbein, F. et al. 2000, A&AS 143, 23.",
         "https://vizier.cds.unistra.fr"),
        ("SIMBAD (CDS)", "Positions and V magnitudes of stars added by name (campaigns and other targets).",
         "\"This research has made use of the SIMBAD database, CDS, Strasbourg Astronomical Observatory, France.\" "
         "Wenger, M. et al. 2000, A&AS 143, 9.", "https://simbad.cds.unistra.fr"),
        ("Open-Meteo", "Hourly cloud cover, temperature, dew point and wind for each planned night (the weather "
         "strip and the dew onset).",
         "Weather data by Open-Meteo.com, licensed under CC BY 4.0. Zippenfenig, P. 2023, Open-Meteo.com Weather "
         "API, Zenodo, doi:10.5281/zenodo.7970649.", "https://open-meteo.com"),
    ]),
    ("Design and code", [
        ("TAPIR", "The design of the night plot (airmass and elevation, twilight bands with their times, the Moon "
         "line by phase, the transit band).",
         "Jensen, E. 2013, \"Tapir: A web interface for transit/eclipse observability\", Astrophysics Source Code "
         "Library, ascl:1306.007 (GPL-3).", "https://github.com/elnjensen/Tapir"),
        ("SHOBS-P", "SCHED's sky arithmetic (Sun, Moon, twilight, altitude, meridian, the most precise ephemeris) "
         "started from SHOBS-P's transit planner; SCHED reads SHOBS-P's settings when SHOBS-P is installed.",
         "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-P: Shiloh Hill Observatory Photometry, Zenodo, "
         "doi:10.5281/zenodo.23417064.", "https://github.com/SHOBS-S/SHOBS-P"),
    ]),
    ("Libraries", [
        ("Python and Tk", "The program and its windows.", "Python Software Foundation.", "https://www.python.org"),
        ("NumPy", "Array math throughout.", "Harris, C. R. et al. 2020, Nature 585, 357.", "https://numpy.org"),
        ("Matplotlib", "The night plot on the Schedule page and on the printed plan card.",
         "Hunter, J. D. 2007, Computing in Science & Engineering 9, 90.", "https://matplotlib.org"),
        ("tzdata (IANA Time Zone Database)", "The site's time zone and its daylight-saving changes on every "
         "computer.", "The IANA Time Zone Database.", "https://www.iana.org/time-zones"),
    ]),
    ("Methods", [
        ("Sun and Moon positions", "Twilight, darkness, Moon rise and set, Moon distance.",
         "The Astronomical Almanac's low-precision formulas (Sun ~0.01°, Moon ~0.3°).", ""),
        ("Moonlit sky brightness", "The Moon penalty for suggested transits and the exposure estimate.",
         "Krisciunas, K. & Schaefer, B. E. 1991, PASP 103, 1033.", ""),
        ("Precession", "J2000 positions to the equator of date for meridian times and the ASIAIR list.",
         "Lieske, J. H. et al. 1977, A&A 58, 1 (IAU 1976 precession).", ""),
        ("Transit timing uncertainty", "The predicted mid-transit uncertainty from the ephemeris errors, and the "
         "choice of the most precise published ephemeris.", "As SHOBS-P (propagated period and epoch errors).", ""),
    ]),
]


def credits_text() -> str:
    """The whole list as plain text (the page's text box and Save credits as text)."""
    lines = []
    for section, items in CREDITS:
        lines.append(section.upper())
        lines.append("")
        for name, use, cite, url in items:
            lines.append(name)
            lines.append(f"  Used for: {use}")
            lines.append(f"  {cite}")
            if url:
                lines.append(f"  {url}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
