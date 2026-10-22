"""SHOBS-SCHED exports: the nightly table (shared by the app and the plan card), the ASIAIR coordinate list, and the
printable plan card (HTML) with its plain-text twin.

Every time shown is on the SITE's clock (site.tz), never the computer's. Nights and blocks may be passed as model
objects or as their dicts (as saved in the schedule)."""
from __future__ import annotations

import csv
import html
import io
import math
import unicodedata
from datetime import date

import sky
from models import MODE_NAMES, STATUS_LABELS, Block, NightPlan, Site, Target

TABLE_COLUMNS = ("Time", "Target", "RA / Dec (J2000)", "SHOBS-P mode", "Alt", "Meridian", "Exposure", "Frames",
                 "Notes")
TABLE_KEYS = ("time", "target", "radec", "mode", "alt", "meridian", "exposure", "frames", "notes")


# ---- small helpers --------------------------------------------------------------------------------------------------

def _night(n) -> NightPlan:
    return n if isinstance(n, NightPlan) else NightPlan.from_dict(n)


def _block(b) -> Block:
    return b if isinstance(b, Block) else Block.from_dict(b)


def _target(t) -> Target | None:
    if t is None or isinstance(t, Target):
        return t
    return Target.from_dict(t) if isinstance(t, dict) else None


def _jd_ok(j) -> bool:
    """A usable JD (a real date after 1858); 0, None, NaN or a stray number is not."""
    try:
        return math.isfinite(float(j)) and float(j) > 2400000.0
    except (TypeError, ValueError):
        return False


def _hhmm(j, tz: str) -> str:
    return sky.hhmm(float(j), tz) if _jd_ok(j) else "—"


def _sort_key(b: Block) -> float:
    return float(b.start) if _jd_ok(b.start) else math.inf


def _blocks(night: NightPlan) -> list[Block]:
    return sorted((_block(b) for b in (night.blocks or [])), key=_sort_key)


def _date_label(iso: str, site: Site, year: bool = False) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    text = d.strftime(site.date_format or "%a %d %b")
    return f"{text} {d.year}" if year else text


def fmt_exposure(s: float | None) -> str:
    """'60 s', '0.75 s', '1.5 s'; '—' for None."""
    if s is None:
        return "—"
    return f"{float(s):g} s"


def fmt_frames(frames: int | None, in_transit: int | None = None) -> str:
    """'~240 (~135 in transit)' or '~240'; '—' for None."""
    if frames is None:
        return "—"
    text = f"~{int(round(frames))}"
    if in_transit is not None:
        text += f" (~{int(round(in_transit))} in transit)"
    return text


def _coords(block: Block, targets_by_id: dict | None) -> tuple[float | None, float | None]:
    t = _target((targets_by_id or {}).get(block.target_id))
    if t is not None and t.ra is not None and t.dec is not None:
        return float(t.ra), float(t.dec)
    ra, dec = getattr(block, "ra", None), getattr(block, "dec", None)     # if Block ever carries them
    return (ra, dec) if (ra is not None and dec is not None) else (None, None)


def _alt_range(b: Block) -> str:
    lo = b.alt_min if b.alt_min is not None else min((a for a in (b.alt_start, b.alt_end) if a is not None),
                                                     default=None)
    hi = b.alt_max if b.alt_max is not None else max((a for a in (b.alt_start, b.alt_end) if a is not None),
                                                     default=None)
    if lo is None or hi is None:
        return "—"
    lo, hi = int(round(lo)), int(round(hi))
    return f"{lo}°" if lo == hi else f"{lo}–{hi}°"


def _alt_best(b: Block) -> str:
    """'62° (X 1.13)': the highest altitude in the block and its airmass."""
    alt = b.alt_max
    x = b.airmass_best
    if alt is None and x:
        alt = math.degrees(math.asin(min(1.0, 1.0 / float(x))))
    if alt is None:
        return "—"
    if x is None:
        x = sky.airmass_from_alt(alt)
    return f"{int(round(alt))}° (X {float(x):.2f})" if x == x else f"{int(round(alt))}°"


# ---- the nightly table ------------------------------------------------------------------------------------------------

def table_rows(night, site: Site, targets_by_id: dict | None = None) -> list[dict]:
    """The nightly table in the plan-doc format, one dict per block in time order. Display keys (TABLE_KEYS, in the
    order of TABLE_COLUMNS): time "20:15–00:30", target, radec "23:13:17 +57:10:06" (from targets_by_id, else the
    block's own ra/dec if it has them, else "—"), mode "Transits", alt "52–72°" (low–high), meridian "22:44", exposure "60 s", frames
    "~240 (~135 in transit)", notes (notes then cautions, joined with "; "). Extra keys for the app: alt_x
    "62° (X 1.13)" (at best), start/end (JD), mode_code (V/T/D), kind, status, status_label, locked, set_by_user,
    fixed, cautions (list), exposure_is_estimate, target_id."""
    n = _night(night)
    tz = site.tz
    rows = []
    for b in _blocks(n):
        ra, dec = _coords(b, targets_by_id)
        radec = f"{sky.fmt_ra(ra, 0)} {sky.fmt_dec(dec)}" if ra is not None else "—"
        notes = [str(x) for x in (b.notes or []) if str(x).strip()]
        cautions = [str(x) for x in (b.cautions or []) if str(x).strip()]
        rows.append({
            "time": f"{_hhmm(b.start, tz)}–{_hhmm(b.end, tz)}",
            "target": b.name,
            "radec": radec,
            "mode": MODE_NAMES.get(b.mode, b.mode),
            "alt": _alt_range(b),
            "meridian": _hhmm(b.meridian, tz),
            "exposure": fmt_exposure(b.exposure_s),
            "frames": fmt_frames(b.frames, b.frames_in_transit if b.kind == "transit" else None),
            "notes": "; ".join(notes + cautions),
            "alt_x": _alt_best(b),
            "start": b.start, "end": b.end, "mode_code": b.mode, "kind": b.kind, "status": b.status,
            "status_label": STATUS_LABELS.get(b.status, b.status), "locked": b.locked,
            "set_by_user": b.set_by_user, "fixed": b.fixed, "cautions": cautions,
            "exposure_is_estimate": b.exposure_is_estimate, "target_id": b.target_id,
        })
    return rows


# ---- ASIAIR coordinate list ------------------------------------------------------------------------------------------
#
# What ASIAIR's Plan mode imports: the text Telescopius puts on the clipboard with "Copy CSV" (Telescope Simulator /
# mosaic framing), pasted with Plan → Add → Import. It carries positions only (no exposures, no times). That text, as
# quoted verbatim in three independent places (2021–2025):
#     Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column
#     Pane 1, 21hr 46' 08", 58º 29' 44", 0.00, 109.20, 74.40, 20%, 1, 1
#     Center, 05hr 05' 30", -07º 03' 20", 189.30, 86.40, 86.40, 20%, -, -
#   * https://astroguide.starlust.de/html/TargetandMosaicPlanningusingTele.html  (ASIAIR guide: "Copy CSV" in
#     Telescopius, then the import symbol in ASIAIR's Plan mode; the pasted text above)
#   * https://forum.telescopius.com/t/telescopius-csv-format-problem/1753  (2025, ASIAIR Plus; same header and
#     row shape; the "ยบ" seen there is the º sign mangled by a text editor, not part of the format)
#   * https://www.cloudynights.com/topic/848027-mosaic-using-asiair-plus-problem-with-telescopius-import/  (2022:
#     "Copy CSV" in Telescopius, then in ASIAIR Plan mode Add → Import and paste)
# Format details copied from those samples: comma + space separated, fields unquoted; RA "HHhr MM' SS"" (two-digit
# hours, the letters "hr"); Dec "DDº MM' SS"" with the masculine-ordinal sign º (U+00BA, as Telescopius writes it),
# "-" for south and no sign for north; position angle and pane sizes with two decimals; "-" for Row/Column of a
# single (non-mosaic) frame, as Telescopius writes for its "Center" row. We put the target's name in the Pane column
# (ASCII, no commas or quotes). NOT confirmed by any source: that ASIAIR keeps that name as the plan's target name
# (all published samples say "Pane N"/"Center"); if an import is refused, asiair_csv(..., pane_names=False) writes
# "Pane 1", "Pane 2", ... in time order instead (the comment line is never written: ASIAIR wants the bare table).
#
# Telescopius's OBSERVING-LIST export is a different file, used by Sequence Generator Pro, not documented for ASIAIR
# (a 2021 Cloudy Nights thread reports ASIAIR refusing it). Verbatim from a downloaded list
# (https://forum.sequencegeneratorpro.com/t/import-data-from-telescopius/12109, 2020):
#     Catalogue Entry,Familiar Name,Alternative Entries,Type,Constellation,Right Ascension,Declination,Magnitude,
#     Size,Surface Brightness,Rises over 35º,Transit Time,Sets below 35º,Maximum Altitude      (one line)
#     NGC 4150,,"CGCG 158-37, ...",Galaxy,Coma Berenices,"12h 10' 33""","30º 24' 07""",11.60,2.3' x 1.6',...
# asiair_csv(..., style="observing_list") writes that layout too (for SGP or a future ASIAIR), unused columns empty.

ASIAIR_HEADER = ("Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, "
                 "Column")
OBSERVING_LIST_HEADER = ["Catalogue Entry", "Familiar Name", "Alternative Entries", "Type", "Constellation",
                         "Right Ascension", "Declination", "Magnitude", "Size", "Surface Brightness",
                         "Rises over 35º", "Transit Time", "Sets below 35º", "Maximum Altitude"]
DEG = "º"   # U+00BA, the sign Telescopius writes (not ° U+00B0)

# Sensor size in pixels for common cameras, for the pane size (ASIAIR frames with its own camera; this is cosmetic).
SENSOR_PX = {"2600": (6248, 4176), "533": (3008, 3008), "294": (4144, 2822), "183": (5496, 3672),
             "1600": (4656, 3520), "6200": (9576, 6388), "071": (4944, 3284)}
DEFAULT_FOV_ARCMIN = (30.0, 20.0)


def _sexa(value: float, sec_round: int = 1) -> tuple[int, int, int]:
    """Absolute value → (units, minutes, whole seconds) with carries."""
    v = abs(float(value))
    total = int(round(v * 3600.0 / sec_round)) * sec_round
    return total // 3600, (total // 60) % 60, total % 60


def ra_telescopius(ra_deg: float, hours_mark: str = "hr") -> str:
    """'23hr 13' 17"' (mosaic CSV) or with hours_mark='h' '23h 13' 17"' (observing list)."""
    h, m, s = _sexa((float(ra_deg) % 360.0) / 15.0)
    return f"{h % 24:02d}{hours_mark} {m:02d}' {s:02d}\""


def dec_telescopius(dec_deg: float) -> str:
    """'57º 10' 06"' / '-07º 03' 20"' (north unsigned, as Telescopius writes it)."""
    d, m, s = _sexa(dec_deg)
    sign = "-" if float(dec_deg) < 0 and (d or m or s) else ""
    return f"{sign}{d:02d}{DEG} {m:02d}' {s:02d}\""


def ascii_name(name: str) -> str:
    """Plain ASCII, no commas or quotes (the mosaic CSV is unquoted)."""
    t = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    for ch in ",\"'\t\r\n;":
        t = t.replace(ch, " ")
    return " ".join(t.split()) or "Target"


def fov_arcmin(optics) -> tuple[float, float]:
    """Camera field of view (width, height) in arcminutes from the optics, if the camera's sensor is known."""
    try:
        scale = optics.pixel_scale() if optics is not None else None
        cam = str(getattr(optics, "camera", "") or "")
        for key, (w, h) in SENSOR_PX.items():
            if key in cam and scale:
                b = max(1, int(getattr(optics, "binning", 1) or 1))
                return w / b * scale / 60.0, h / b * scale / 60.0
    except Exception:
        pass
    return DEFAULT_FOV_ARCMIN


def asiair_targets(nights, targets_by_id, site: Site | None = None) -> tuple[list[dict], list[str]]:
    """Targets to export, in the order their first block starts (each once): [{name, ra, dec, vmag, kind}], and the
    names skipped for want of coordinates."""
    blocks = []
    for n in nights or []:
        blocks.extend(_blocks(_night(n)))
    blocks.sort(key=_sort_key)
    seen, out, skipped = set(), [], []
    for b in blocks:
        key = b.target_id or b.name
        if key in seen:
            continue
        seen.add(key)
        ra, dec = _coords(b, targets_by_id)
        if ra is None:
            skipped.append(b.name)
            continue
        t = _target((targets_by_id or {}).get(b.target_id))
        out.append({"name": b.name, "ra": ra, "dec": dec, "vmag": t.vmag if t else None, "kind": b.kind})
    return out, skipped


def asiair_csv(nights, targets_by_id, site: Site | None = None, *, style: str = "mosaic", optics=None,
               pane_names: bool = True) -> str:
    """Paste-ready coordinate list for ASIAIR's Plan import (see the comment above for the format and its sources).
    One row per target in time order (a target once even with two blocks), across the given nights. No trailing
    newline. style="observing_list" writes Telescopius's observing-list CSV instead.
    The text contains º (U+00BA): when it is saved to a file, write UTF-8 WITHOUT a byte-order mark (a BOM would
    become part of the first header name); on the clipboard it is plain Unicode text."""
    rows, _skipped = asiair_targets(nights, targets_by_id, site)
    if style == "observing_list":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(OBSERVING_LIST_HEADER)
        types = {"transit": "Star (transit)", "variable": "Variable star", "campaign": "Field"}
        for r in rows:
            mag = f"{r['vmag']:.2f}" if r["vmag"] is not None else ""
            w.writerow([ascii_name(r["name"]), "", "", types.get(r["kind"], "Star"), "",
                        ra_telescopius(r["ra"], "h"), dec_telescopius(r["dec"]), mag, "", "", "", "", "", ""])
        return buf.getvalue().rstrip("\n")
    fw, fh = fov_arcmin(optics)
    lines = [ASIAIR_HEADER]
    for k, r in enumerate(rows, 1):
        name = ascii_name(r["name"]) if pane_names else f"Pane {k}"
        lines.append(f"{name}, {ra_telescopius(r['ra'])}, {dec_telescopius(r['dec'])}, 0.00, {fw:.2f}, {fh:.2f}, "
                     f"0%, -, -")
    return "\n".join(lines)


# ---- plan card ------------------------------------------------------------------------------------------------------

def _darkness_word(site: Site) -> str:
    a = float(site.sun_alt if site.sun_alt is not None else -18.0)
    return {-18.0: "astronomical", -12.0: "nautical", -6.0: "civil"}.get(a, f"Sun below {a:g}°")


def _night_facts(n: NightPlan, site: Site, weather_by_date: dict | None, settings_dict: dict | None) -> dict:
    """Header lines of one night, as plain strings (escaped later for HTML)."""
    tz = site.tz
    if _jd_ok(n.dusk) and _jd_ok(n.dawn):
        dark = f"Dark {_hhmm(n.dusk, tz)}–{_hhmm(n.dawn, tz)} ({_darkness_word(site)})"
    else:
        dark = "✖ No dark time tonight"
    moon = "Moon —"
    if n.moon_illum is not None:
        moon = f"Moon {int(round(100 * float(n.moon_illum)))} %"
        ev = []
        if _jd_ok(n.moonrise):
            ev.append((n.moonrise, f"rises {_hhmm(n.moonrise, tz)}"))
        if _jd_ok(n.moonset):
            ev.append((n.moonset, f"sets {_hhmm(n.moonset, tz)}"))
        if ev:
            moon += ", " + ", ".join(t for _j, t in sorted(ev))
    w = (weather_by_date or {}).get(n.date) or n.weather
    wline = ""
    if isinstance(w, dict):
        import weather as _w
        wline = str(w.get("summary") or "").split("; dew from")[0]
        hours = [h for h in (w.get("hours") or []) if h.get("temp_c") is not None]
        if hours and w.get("ok", True):
            unit = site.temp_unit or "C"
            lo = min(h["temp_c"] for h in hours)
            hi = max(h["temp_c"] for h in hours)
            winds = [h["wind_kmh"] for h in hours if h.get("wind_kmh") is not None]
            wline += f". High {_w.fmt_temp(hi, unit)}, low {_w.fmt_temp(lo, unit)}"
            if winds:
                wline += f", wind up to {_w.fmt_wind(max(winds), site.length_unit or 'm')}"
        if _jd_ok(w.get("dew_onset")):
            wline += f". ⚠ Dew from {_hhmm(w['dew_onset'], tz)}"
        if w.get("note") and w.get("ok", True):
            wline += f" ({w['note']})"
        if wline:
            wline = f"Weather ({w.get('source') or 'forecast'}): {wline}"
    return {"date": _date_label(n.date, site, year=True), "dark": dark, "moon": moon, "weather": wline}


def _didnt_fit_lines(n: NightPlan, site: Site) -> list[str]:
    out = []
    for d in n.didnt_fit or []:
        d = d if isinstance(d, dict) else {}
        line = f"{d.get('name', '?')}: {d.get('reason', '')}".rstrip(": ")
        nc = d.get("next_chance")
        if nc and "next chance" not in line:
            line += f" (next chance {_date_label(nc, site)})"
        out.append(line)
    return out


def _items(head: str, items) -> list[str]:
    """Sentences under a heading, without repeating it: under "Darks", "Darks to shoot: 40 s" reads
    "To shoot: 40 s" and "Darks: all on hand (…)" reads "All on hand (…)"."""
    out = []
    for x in items or []:
        s = str(x).strip()
        if s.lower().startswith(head.lower()) and s[len(head):len(head) + 1] in (":", " "):
            s = s[len(head):].lstrip(": ").strip()
            s = s[:1].upper() + s[1:]
        if s:
            out.append(s)
    return out


def _free_lines(n: NightPlan, site: Site) -> list[str]:
    return [f"Free {_hhmm(g[0], site.tz)}–{_hhmm(g[1], site.tz)}" for g in (n.free or []) if len(g) >= 2]


def _title(nights: list[NightPlan], site: Site) -> str:
    if not nights:
        return "Observing plan"
    a, b = nights[0].date, nights[-1].date
    return (f"Observing plan, {_date_label(a, site, True)}" if a == b else
            f"Observing plan, {_date_label(a, site)} to {_date_label(b, site, True)}")


def _site_line(site: Site, settings_dict: dict | None) -> str:
    parts = []
    if site.name:
        parts.append(site.name)
    if site.lat is not None and site.lon is not None:
        parts.append(f"{abs(site.lat):.3f}° {'N' if site.lat >= 0 else 'S'}, "
                     f"{abs(site.lon):.3f}° {'E' if site.lon >= 0 else 'W'}")
    code = (settings_dict or {}).get("observer_code")
    if code:
        parts.append(f"observer {code}")
    return " · ".join(parts)


def _footer(site: Site) -> str:
    text = f"SHOBS-SCHED · site clock ({site.tz or 'UTC'})"
    if not sky.zone_ok(site.tz):
        text += " ⚠ unknown time zone, times in UTC"
    return text


_CSS = """
@page { size: auto; margin: 12mm; }
* { box-sizing: border-box; }
html, body { background: #fff; color: #000; }
body { font-family: "Segoe UI", Arial, Helvetica, sans-serif; font-size: 11pt; margin: 0 auto; max-width: 270mm;
       padding: 8mm; line-height: 1.3; }
h1 { font-size: 18pt; margin: 0 0 2pt; }
.site { font-size: 10pt; margin-bottom: 8pt; }
.night { break-inside: avoid; page-break-inside: avoid; border-top: 2pt solid #000; padding-top: 6pt;
         margin-top: 12pt; }
.night + .night { break-before: auto; }
h2 { font-size: 16pt; margin: 0 0 3pt; }
.facts { font-size: 12pt; margin: 0 0 2pt; }
.facts b { font-size: 14pt; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0; }
th, td { border: 0.75pt solid #000; padding: 3pt 4pt; vertical-align: top; text-align: left; }
th { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.02em; }
td.time { font-size: 16pt; font-weight: 700; white-space: nowrap; }
td.target { font-size: 13pt; font-weight: 700; }
td.num, td.mer { white-space: nowrap; }
td.mer { font-size: 13pt; font-weight: 700; }
td.notes { font-size: 10pt; }
.mode { display: inline-block; border: 1pt solid #000; padding: 0 3pt; font-size: 9pt; font-weight: 700; }
.status { font-size: 9pt; font-weight: 700; }
.list { margin: 3pt 0; font-size: 11pt; }
.list h3 { font-size: 11pt; margin: 4pt 0 1pt; }
.list ul { margin: 0; padding-left: 16pt; }
.empty { font-style: italic; }
footer { margin-top: 14pt; border-top: 0.75pt solid #000; padding-top: 3pt; font-size: 9pt; }
@media print { body { padding: 0; max-width: none; } }
"""


def plan_card_html(nights, site: Site, settings_dict: dict | None = None, weather_by_date: dict | None = None,
                   targets_by_id: dict | None = None) -> str:
    """A printable, self-contained HTML page (no scripts, no external files): black on white, big times. Per night:
    date, darkness, Moon, weather + dew onset, the nightly table, flats, darks, didn't fit, free gaps. Footer
    'SHOBS-SCHED · site clock (<zone>)'. Pass targets_by_id to fill the RA / Dec column."""
    e = lambda s: html.escape(str(s), quote=True)
    ns = [_night(n) for n in (nights or [])]
    title = _title(ns, site)
    out = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{e(title)}</title><style>{_CSS}</style></head><body>",
           f"<h1>{e(title)}</h1>", f'<div class="site">{e(_site_line(site, settings_dict))}</div>']
    if not ns:
        out.append('<p class="empty">No nights selected.</p>')
    for n in ns:
        f = _night_facts(n, site, weather_by_date, settings_dict)
        out.append('<section class="night">')
        out.append(f"<h2>{e(f['date'])}</h2>")
        out.append(f'<p class="facts"><b>{e(f["dark"])}</b> · {e(f["moon"])}</p>')
        if f["weather"]:
            out.append(f'<p class="facts">{e(f["weather"])}</p>')
        rows = table_rows(n, site, targets_by_id)
        if rows:
            out.append("<table><thead><tr>" + "".join(f"<th>{e(c)}</th>" for c in TABLE_COLUMNS)
                       + "</tr></thead><tbody>")
            for r in rows:
                status = "" if r["status"] == "planned" else f' <span class="status">[{e(r["status_label"])}]</span>'
                lock = " (locked)" if r["locked"] else (" (set by you)" if r["set_by_user"] else "")
                exp = e(r["exposure"]) + (" est." if r["exposure_is_estimate"] and r["exposure"] != "—" else "")
                out.append(
                    "<tr>"
                    f'<td class="time">{e(r["time"])}</td>'
                    f'<td class="target">{e(r["target"])}{e(lock)}{status}</td>'
                    f'<td class="num">{e(r["radec"])}</td>'
                    f'<td><span class="mode">{e(r["mode_code"])}</span> {e(r["mode"])}</td>'
                    f'<td class="num">{e(r["alt"])}<br>{e(r["alt_x"])}</td>'
                    f'<td class="mer">{e(r["meridian"])}</td>'
                    f'<td class="num">{exp}</td>'
                    f'<td class="num">{e(r["frames"])}</td>'
                    f'<td class="notes">{e(r["notes"])}</td>'
                    "</tr>")
            out.append("</tbody></table>")
        else:
            out.append('<p class="empty">No blocks planned.</p>')
        for head, items in (("Flats", n.flats), ("Darks", n.darks),
                            ("Didn't fit, and why", _didnt_fit_lines(n, site)), ("Free time", _free_lines(n, site))):
            items = _items(head, items)
            if items:
                out.append(f'<div class="list"><h3>{e(head)}</h3><ul>'
                           + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul></div>")
        out.append("</section>")
    out.append(f"<footer>{e(_footer(site))}</footer></body></html>")
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def plan_card_text(nights, site: Site, settings_dict: dict | None = None, weather_by_date: dict | None = None,
                   targets_by_id: dict | None = None, width: int = 100) -> str:
    """The plan card as plain text (preview pane, copy): same content as plan_card_html."""
    ns = [_night(n) for n in (nights or [])]
    lines = [_title(ns, site)]
    sl = _site_line(site, settings_dict)
    if sl:
        lines.append(sl)
    if not ns:
        lines += ["", "No nights selected."]
    for n in ns:
        f = _night_facts(n, site, weather_by_date, settings_dict)
        lines += ["", "=" * min(width, 60), f["date"], f"{f['dark']} · {f['moon']}"]
        if f["weather"]:
            lines += _wrap(f["weather"], width)
        rows = table_rows(n, site, targets_by_id)
        if not rows:
            lines.append("No blocks planned.")
        for r in rows:
            extra = "" if r["status"] == "planned" else f"  [{r['status_label']}]"
            lock = "  (locked)" if r["locked"] else ("  (set by you)" if r["set_by_user"] else "")
            lines.append("")
            lines.append(f"{r['time']:<13} {r['target']}{lock}{extra}")
            est = " est." if r["exposure_is_estimate"] and r["exposure"] != "—" else ""
            lines.append(f"{'':13} {r['mode']} · {r['radec']} · alt {r['alt']}, best {r['alt_x']} · "
                         f"meridian {r['meridian']}")
            lines.append(f"{'':13} exposure {r['exposure']}{est} · frames {r['frames']}")
            if r["notes"]:
                for part in _wrap(r["notes"], width - 14):
                    lines.append(f"{'':13} {part}")
        for head, items in (("Flats", n.flats), ("Darks", n.darks),
                            ("Didn't fit, and why", _didnt_fit_lines(n, site)), ("Free time", _free_lines(n, site))):
            items = _items(head, items)
            if items:
                lines += ["", f"{head}:"] + [f"  - {x}" for x in items]
    lines += ["", _footer(site)]
    return "\n".join(lines)
