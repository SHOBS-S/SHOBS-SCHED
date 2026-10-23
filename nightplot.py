"""SHOBS-SCHED night plot: the Schedule tab's graph of one night, and the same graph as a PNG for the plan card.

The design follows TAPIR's airmass plot (plot_airmass.cgi in Eric Jensen's Transit and Ephemeris Prediction
tool: Jensen, E. 2013, "Tapir: A web interface for transit/eclipse observability", Astrophysics Source Code Library
ascl:1306.007, https://github.com/elnjensen/Tapir, GPL-3): airmass downward on the left, elevation on the right,
the Sun's altitude as shaded twilight bands with the sunset / twilight / sunrise times across the top, the time span
from nautical dusk − 1.5 h to nautical dawn + 1.5 h in whole hours, the Moon as a dashed line whose width grows
with its phase (1.5 · (1 + illuminated fraction)), and a transit's ingress-to-egress shaded. SCHED adds what a
night plan needs: every planned target's whole-night path faint and its block bold in its SHOBS-P mode color with
the airmass at the block's start and end, a label box per block (name, local start–end, Moon distance), ▼ where a
meridian flip falls inside a block, the transit's baselines, no-transit windows that changed the plan (hatched),
and the hourly weather (cloud, temperature, dew point, spread, dew risk) in a strip above on the same time axis.

Data it is drawn from (the same objects the Schedule tab uses):
  night          models.NightPlan (or its dict): date, blocks (Block dicts), didnt_fit, moon_illum
  site           models.Site (lat, lon, tz, max_airmass, mount, temp_unit, length_unit, date_format, name)
  targets_by_id  {target id: models.Target (or its dict)} for the positions and the no-transit rules; a block whose
                 target is missing is drawn from the block's own ra/dec
  weather        the night's weather dict from weather.py (NightPlan.weather: hours = [{jd, cloud, temp_c,
                 dew_c, spread_c, wind_kmh}], dew_onset, source, note); None or {} = no forecast
  options        the app's options dict (dew_spread_c = the dew-risk spread)

matplotlib is optional at import time: available() says whether it loads; png_bytes() returns None and the
Schedule tab shows a ⚠ line without it. All times are on the SITE's clock (sky.to_local with site.tz)."""
from __future__ import annotations

import io
import math
from datetime import date, timedelta

import numpy as np

import events
import sky
from models import Block, NightPlan, Target

# ---- matplotlib (optional) ------------------------------------------------------------------------------------------
_MPL: dict | None = None
_MPL_TRIED = False


def _load(force: bool = False) -> dict | None:
    """The matplotlib pieces this module uses, or None when matplotlib does not import (tried once; force retries)."""
    global _MPL, _MPL_TRIED
    if _MPL_TRIED and not force:
        return _MPL
    _MPL_TRIED = True
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.colors import ListedColormap
        from matplotlib.figure import Figure
        from matplotlib.ticker import FixedLocator, NullLocator
        _MPL = {"Figure": Figure, "Agg": FigureCanvasAgg, "ListedColormap": ListedColormap,
                "FixedLocator": FixedLocator, "NullLocator": NullLocator}
    except Exception:
        _MPL = None
    return _MPL


def available(force: bool = False) -> bool:
    return _load(force) is not None


MISSING_TEXT = "⚠ Night plot needs matplotlib — run Create shortcuts.bat again"

# ---- look -----------------------------------------------------------------------------------------------------------
BRIGHT = {"V": "#8ec5f2", "T": "#8fe3a8", "D": "#e09bd9"}       # bold block paths on the dark sky (John's mockup)
MODE_FILL = {"V": "#2a5a80", "T": "#357a50", "D": "#7a3576"}    # label boxes (style.MODE_COLORS "light")
LOCK = "#ffd54f"                                               # label box edge: locked or set by you

SCREEN = {
    "sky": ("#cfe3f5", "#9bb8d8", "#5f7fae", "#34477a", "#141d3d"),   # day, civil, nautical, astronomical, night
    "text": "#1c2b33", "on_sky": "#ffffff", "path": "#d9e2ec", "under": "#0b1020", "moon": "#e8c547",
    "limit": "#ff8a65", "transit": "#7CFC9A", "transit_text": "#b9ffcb", "blackout": "#ff7043",
    "blackout_text": "#ffab91", "grid": "#ffffff", "mode": BRIGHT, "wx_sky_alpha": 0.18, "flip": "#ffffff",
}
PRINT = {
    "sky": ("#ffffff", "#eef3f8", "#dde7f1", "#cbd8e6", "#b9c9da"),
    "text": "#1c2b33", "on_sky": "#1c2b33", "path": "#7d8c9a", "under": "#ffffff", "moon": "#b8860b",
    "limit": "#d84315", "transit": "#2e7d32", "transit_text": "#1b5e20", "blackout": "#d84315",
    "blackout_text": "#bf360c", "grid": "#5d6d76", "mode": MODE_FILL, "wx_sky_alpha": 0.35, "flip": "#1c2b33",
}
CLOUD = "#90a4ae"
TEMP = "#e53935"
DEW = "#1e88e5"
AIRMASS_TOP = 1.0
AIRMASS_BOTTOM = 2.4

TWILIGHT = (("sunset", "Sunset"), ("civil_dusk", "Civil"), ("nautical_dusk", "Naut."), ("astro_dusk", "Astro"),
            ("astro_dawn", "Astro"), ("nautical_dawn", "Naut."), ("civil_dawn", "Civil"), ("sunrise", "Sunrise"))
STATUS_MARK = {"done": " ✔", "clouded": " ✖", "partial": " ½"}


# ---- plain helpers (no matplotlib) ----------------------------------------------------------------------------------

def _night(night) -> NightPlan:
    return NightPlan.from_dict(night) if isinstance(night, dict) else night


def _target(t) -> Target | None:
    if t is None:
        return None
    return Target.from_dict(t) if isinstance(t, dict) else t


def sun_level(alt) -> np.ndarray:
    """0 day, 1 civil, 2 nautical, 3 astronomical twilight, 4 night, from the Sun's altitude (degrees)."""
    a = np.asarray(alt, dtype=float)
    return np.select([a > sky.SUN_RISE_ALT, a > sky.CIVIL, a > sky.NAUTICAL, a > sky.ASTRONOMICAL], [0, 1, 2, 3], 4)


def twilight_times(d: date, site) -> dict:
    """sunset, civil/nautical/astro dusk and dawn, sunrise (JD UTC or None) from sky.night_window."""
    out = {}
    for key, lvl in (("sun", sky.SUN_RISE_ALT), ("civil", sky.CIVIL), ("nautical", sky.NAUTICAL),
                     ("astro", sky.ASTRONOMICAL)):
        w = sky.night_window(d, site, lvl)
        a, b = w if w else (None, None)
        if key == "sun":
            out["sunset"], out["sunrise"] = a, b
        else:
            out[f"{key}_dusk"], out[f"{key}_dawn"] = a, b
    j0, j1 = sky.night_span(d, site)
    for k in list(out):                     # the noon-to-noon span's own ends are not real events (polar night)
        if out[k] is not None and (abs(out[k] - j0) < 1e-9 or abs(out[k] - j1) < 1e-9):
            out[k] = None
    return out


def _floor_hour(jd: float, tz: str) -> float:
    t = sky.to_local(jd + 0.5 / 86400.0, tz)          # (a JD of 18:00 may read 17:59:59.99999)
    return sky.jd_from_datetime(t.replace(minute=0, second=0, microsecond=0))


def _ceil_hour(jd: float, tz: str) -> float:
    f = _floor_hour(jd, tz)
    return f if jd - f < 1e-6 else _floor_hour(jd + 1.0 / 24.0, tz)


def x_span(d: date, site, blocks=(), times: dict | None = None) -> tuple[float, float]:
    """The plot's time span (JD UTC), the TAPIR rule: nautical dusk − 1.5 h to nautical dawn + 1.5 h, out to whole
    hours of the site clock. Without a nautical night: civil, then sunset/sunrise, then 18:00–07:00. Widened (in
    whole hours) to show every block."""
    tz = site.tz
    times = times if times is not None else twilight_times(d, site)
    a = b = None
    for k0, k1 in (("nautical_dusk", "nautical_dawn"), ("civil_dusk", "civil_dawn"), ("sunset", "sunrise")):
        if times.get(k0) and times.get(k1) and times[k1] > times[k0]:
            a, b = times[k0], times[k1]
            break
    if a is None:
        w = sky.night_window(d, site, sky.NAUTICAL)          # polar night: the whole span is nautically dark
        if w:
            a, b = w
    if a is None:
        a, b = sky.local_jd(d, 19, 30, tz), sky.local_jd(d + timedelta(days=1), 5, 30, tz)
    a, b = a - 1.5 / 24.0, b + 1.5 / 24.0
    for blk in blocks:
        a, b = min(a, blk.start), max(b, blk.end)
    return _floor_hour(a, tz), _ceil_hour(b, tz)


def hour_ticks(j0: float, j1: float, tz: str, every: int = 1) -> list[float]:
    """JDs of the whole site-clock hours in [j0, j1] (every 1 or 2 h: even hours for 2)."""
    out = []
    j = _ceil_hour(j0, tz)
    while j <= j1 + 1e-9:
        if every == 1 or sky.to_local(j, tz).hour % every == 0:
            out.append(j)
        j += 1.0 / 24.0
    return out


def plan_blackouts(night, targets_by_id: dict, j0: float | None = None, j1: float | None = None) -> list[dict]:
    """The no-transit windows that changed tonight's plan, each {target_id, name, start, end, reason, why}:
    a target in Didn't fit because it was "blacked out" (all its windows tonight), a block that touches a window
    (the planner trimmed or moved it against the window: an edge within 3 min of it) or overlaps one (a locked block
    inside a window). Windows that did not shape the plan are left to the Notes column."""
    night = _night(night)
    if j0 is None or j1 is None:
        bl = night.blocks_obj()
        j0 = min([b.start for b in bl] + [night.dusk or 1e12]) - 0.5
        j1 = max([b.end for b in bl] + [night.dawn or 0]) + 0.5
    out = []
    touch = 3.0 / 1440.0
    by_target: dict[str, list[Block]] = {}
    for b in night.blocks_obj():
        by_target.setdefault(b.target_id, []).append(b)
    blacked = {d.get("target_id") for d in night.didnt_fit if "blacked out" in str(d.get("reason", "")).lower()}
    for tid in list(by_target) + sorted(t for t in blacked if t and t not in by_target):
        t = _target(targets_by_id.get(tid))
        if t is None or not any(r.get("kind") == "no_transit" for r in (t.rules or []) if isinstance(r, dict)):
            continue
        try:
            bos = events.blackouts(t, j0, j1)
        except Exception:
            continue
        for bo in bos:
            why = ""
            if tid in blacked:
                why = "didn't fit"
            for b in by_target.get(tid, []):
                if b.start < bo["end"] - touch and bo["start"] + touch < b.end:
                    why = "inside the block"
                elif abs(b.end - bo["start"]) <= touch or abs(b.start - bo["end"]) <= touch:
                    why = "trimmed or moved"
            if why:
                out.append({"target_id": tid, "name": t.name, "start": bo["start"], "end": bo["end"],
                            "reason": bo["reason"], "why": why})
    return out


def _f(c, unit):
    return None if c is None else (c * 9 / 5 + 32 if unit == "F" else c)


def _wind(kmh, length_unit):
    if kmh is None:
        return None, ""
    return (kmh / 1.609344, "mph") if length_unit == "ft" else (kmh, "km/h")


# ---- the plot -------------------------------------------------------------------------------------------------------

class NightPlot:
    """One night drawn on a matplotlib Figure (made here, or given: the Tk canvas's own figure).

    After draw(): groups = {target id: [artists]} (what a row click highlights), blocks = [(index in night.blocks,
    Block)], and layout() re-places the size-dependent pieces (axes margins in inches, hour ticks every 1 or 2 h,
    staggered labels) — call it after a resize. set_highlight(target id or None) changes only artist properties
    (the original look of every artist is kept, so clearing restores it exactly)."""

    GRID_MIN = 2.0                      # minutes between the path samples

    def __init__(self, night, site, targets_by_id: dict, weather: dict | None, options: dict | None,
                 print_style: bool = False, figure=None, info: dict | None = None):
        mpl = _load()
        if mpl is None:
            raise RuntimeError("matplotlib is not installed")
        self.mpl = mpl
        self.night = _night(night)
        self.site = site
        self.tz = site.tz
        self.by_id = {k: _target(v) for k, v in (targets_by_id or {}).items()}
        self.weather = weather if isinstance(weather, dict) else {}
        self.options = options or {}
        self.print_style = print_style
        self.look = PRINT if print_style else SCREEN
        self.fig = figure if figure is not None else mpl["Figure"](figsize=(10, 5.6), dpi=150)
        self.d = date.fromisoformat(self.night.date)
        self.info = info
        self.groups: dict[str, list] = {}
        self.orig: dict[int, dict] = {}
        self.roles: dict[int, str] = {}
        self.selected: str | None = None
        self.blocks: list[tuple[int, Block]] = []
        self.paths: dict[str, tuple[np.ndarray, np.ndarray]] = {}   # target id → (airmass, altitude) on self.jd
        self.labels: list = []           # (text artist, mid jd, n chars) block label boxes, staggered in layout()
        self.edge_texts: list = []       # (start text, end text, block) airmass numbers
        self.twilight_texts: list = []   # (text, jd)
        self.spread_texts: list = []
        self.ax = self.ax2 = self.axw = self.axt = None

    # ---- coordinates ----
    def x(self, jd):
        """JD → plot x (hours since the span start)."""
        return (np.asarray(jd, dtype=float) - self.j0) * 24.0

    def jd_of(self, x: float) -> float:
        return self.j0 + float(x) / 24.0

    def hhmm(self, jd) -> str:
        return sky.hhmm(jd, self.tz)

    # ---- registry for the highlight ----
    def _reg(self, tid: str, artist, role: str):
        """Remember an artist of target tid with its original look. role: path (faint whole-night line), line,
        span (translucent fill), text, label (text in a box)."""
        self.groups.setdefault(tid, []).append(artist)
        self.roles[id(artist)] = role
        o = {"alpha": artist.get_alpha(), "zorder": artist.get_zorder()}
        if hasattr(artist, "get_linewidth") and role != "text" and role != "label":
            o["lw"] = artist.get_linewidth()
        if hasattr(artist, "get_markersize"):
            o["ms"] = artist.get_markersize()
        if role in ("text", "label"):
            o["weight"] = artist.get_fontweight()
            p = artist.get_bbox_patch()
            if p is not None:
                o["bbox_alpha"] = p.get_alpha()
                o["bbox_lw"] = p.get_linewidth()
        self.orig[id(artist)] = o
        return artist

    def look_of(self, artist) -> dict:
        """The artist's current highlight-relevant properties (tests compare these with the originals)."""
        role = self.roles[id(artist)]
        o = {"alpha": artist.get_alpha(), "zorder": artist.get_zorder()}
        if "lw" in self.orig[id(artist)]:
            o["lw"] = artist.get_linewidth()
        if "ms" in self.orig[id(artist)]:
            o["ms"] = artist.get_markersize()
        if role in ("text", "label"):
            o["weight"] = artist.get_fontweight()
            p = artist.get_bbox_patch()
            if p is not None:
                o["bbox_alpha"] = p.get_alpha()
                o["bbox_lw"] = p.get_linewidth()
        return o

    def _restore(self, a):
        o = self.orig[id(a)]
        a.set_alpha(o["alpha"])
        a.set_zorder(o["zorder"])
        if "lw" in o:
            a.set_linewidth(o["lw"])
        if "ms" in o:
            a.set_markersize(o["ms"])
        if "weight" in o:
            a.set_fontweight(o["weight"])
        if "bbox_alpha" in o:
            p = a.get_bbox_patch()
            p.set_alpha(o["bbox_alpha"])
            p.set_linewidth(o["bbox_lw"])

    def set_highlight(self, tid: str | None) -> bool:
        """Highlight target tid (thicker, opaque, on top) and fade every other target; None = all normal.
        Moon, twilight and weather are never touched. True when something changed."""
        if tid is not None and tid not in self.groups:
            tid = None
        if tid == self.selected:
            return False
        self.selected = tid
        for gid, arts in self.groups.items():
            for a in arts:
                self._restore(a)
                if tid is None:
                    continue
                o, role = self.orig[id(a)], self.roles[id(a)]
                base = 1.0 if o["alpha"] is None else o["alpha"]
                if gid == tid:
                    a.set_zorder(o["zorder"] + 10)
                    a.set_alpha(min(1.0, base * 2.2) if role == "span" else 1.0)
                    if "lw" in o and role != "span":
                        a.set_linewidth(o["lw"] * 1.6)
                    if "ms" in o:
                        a.set_markersize(o["ms"] * 1.3)
                    if role == "label":
                        a.get_bbox_patch().set_alpha(1.0)
                        a.get_bbox_patch().set_linewidth(o["bbox_lw"] * 2.5)
                else:
                    if role == "span":
                        a.set_alpha(base * 0.3)
                    else:
                        a.set_alpha(min(base, 0.06 if role == "path" else 0.15))
                    if role == "label":
                        a.get_bbox_patch().set_alpha(0.15)
        self._selection_note()
        return True

    def _selection_note(self):
        if getattr(self, "sel_text", None) is None:
            return
        if self.selected is None:
            self.sel_text.set_text("")
            return
        bl = [b for _i, b in self.blocks if b.target_id == self.selected]
        if bl:
            b = bl[0]
            self.sel_text.set_text(f"Selected: {b.name} ({b.mode})  {self.hhmm(b.start)}–{self.hhmm(b.end)}")

    # ---- drawing ----
    def draw(self):
        fig, look, site = self.fig, self.look, self.site
        fig.clear()
        fig.set_facecolor("#ffffff")
        night = self.night
        self.blocks = sorted(enumerate(night.blocks_obj()), key=lambda p: p[1].start)
        if self.info:                       # app.night_info(): the same night_window times, already computed
            self.times = {k: self.info.get(k) for k, _w in TWILIGHT}
        else:
            self.times = twilight_times(self.d, site)
        self.j0, self.j1 = x_span(self.d, site, [b for _i, b in self.blocks], self.times)
        n = int(round((self.j1 - self.j0) * 1440 / self.GRID_MIN)) + 1
        self.jd = np.linspace(self.j0, self.j1, n)
        self.xs = self.x(self.jd)
        hours = [h for h in (self.weather.get("hours") or []) if isinstance(h, dict) and h.get("jd")]
        self.hours = [h for h in hours if self.j0 - 1 / 48 <= h["jd"] <= self.j1 + 1 / 48]
        self.has_wx = bool(self.hours)
        self.axw = fig.add_axes([0.1, 0.8, 0.8, 0.1])
        self.ax = fig.add_axes([0.1, 0.1, 0.8, 0.6], sharex=self.axw)
        self.bottom = max(AIRMASS_BOTTOM, math.ceil((float(site.max_airmass or 2.0) + 0.15) * 5) / 5)
        self._sky(self.ax, 1.0)
        self._twilight_labels()
        self._limits()
        self._moon()
        for i, b in self.blocks:
            self._block(i, b)
        for bo in plan_blackouts(night, self.by_id, self.j0, self.j1):
            self._blackout(bo)
        self._weather()
        self._title()
        self.layout()
        return self

    def _sky(self, ax, alpha):
        """Twilight shading as ONE mesh (no seams between the bands): the band edges at the twilight times."""
        sun = sky.sun_alt_at(self.site, self.jd)
        lv = sun_level(sun)
        edges = [self.j0]
        cols = [int(lv[0])]
        events_jd = [v for v in self.times.values() if v and self.j0 < v < self.j1]
        for k in range(1, len(lv)):
            if lv[k] != lv[k - 1]:
                t = 0.5 * (self.jd[k] + self.jd[k - 1])
                near = [e for e in events_jd if abs(e - t) <= 3 * self.GRID_MIN / 1440]
                edges.append(min(near, key=lambda e: abs(e - t)) if near else t)
                cols.append(int(lv[k]))
        edges.append(self.j1)
        cmap = self.mpl["ListedColormap"](list(self.look["sky"]))
        mesh = ax.pcolormesh(self.x(edges), [0, 1], np.array([cols], dtype=float), cmap=cmap, vmin=-0.5, vmax=4.5,
                             shading="flat", antialiased=False, edgecolors="none", linewidth=0,
                             transform=ax.get_xaxis_transform(), zorder=0, alpha=alpha, rasterized=True)
        self.sky_edges = edges
        self.sky_levels = cols
        return mesh

    def _twilight_labels(self):
        c = self.look["on_sky"]
        self.twilight_texts = []
        for key, word in TWILIGHT:
            j = self.times.get(key)
            if not j or not (self.j0 < j < self.j1):
                continue
            dusk = key in ("sunset", "civil_dusk", "nautical_dusk", "astro_dusk")
            # sunset / sunrise labels sit on the pale civil band: dark text there on screen
            col = self.look["text"] if key in ("sunset", "sunrise") else c
            t = self.ax.text(float(self.x(j)), 0.995, f" {word}\n {self.hhmm(j)}" if dusk else f"{word} \n{self.hhmm(j)} ",
                             transform=self.ax.get_xaxis_transform(), va="top", ha="left" if dusk else "right",
                             fontsize=7.5, color=col, alpha=0.9, zorder=3, linespacing=1.1)
            self.twilight_texts.append((t, j))

    def _limits(self):
        ax, look = self.ax, self.look
        mx = float(self.site.max_airmass or 2.0)
        ax.axhline(mx, color=look["limit"], lw=1.0, ls=(0, (4, 3)), zorder=2)
        ax.text(0.1, mx, f" max airmass {mx:g}", color=look["limit"], fontsize=7.5, va="bottom", zorder=2)
        ax.set_ylim(self.bottom, AIRMASS_TOP)
        ticks = np.round(np.arange(AIRMASS_TOP, self.bottom + 1e-6, 0.2), 2)
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:.1f}" for t in ticks])
        ax.set_ylabel("Airmass", color=look["text"])
        ax.grid(axis="y", color=look["grid"], alpha=0.12 if not self.print_style else 0.25, lw=0.6)
        ax.set_xlim(0, float(self.x(self.j1)))
        self.ax2 = ax.twinx()
        self.ax2.set_ylim(ax.get_ylim())
        self.ax2.set_yticks(ticks)
        self.ax2.set_yticklabels([f"{sky.alt_for_airmass(t):.0f}°" for t in ticks])
        self.ax2.set_ylabel("Elevation", color=look["text"])
        self.ax.set_xlabel(sky.night_local_label(self.d, self.tz), color=look["text"])

    def _moon(self):
        alt = np.asarray(sky.moon_alt_at(self.site, self.jd), dtype=float)
        self.moon_alt = alt
        xm = np.where(alt > 0, sky.airmass_from_alt(np.clip(alt, 0.1, 90)), np.nan)
        self.moon_x = xm
        illum = self.night.moon_illum
        if illum is None:
            illum = float(sky.moon_radec(0.5 * (self.j0 + self.j1))[2])
        self.illum = float(illum)
        if np.isfinite(xm).any():
            self.moon_line = self.ax.plot(self.xs, xm, color=self.look["moon"], lw=1.5 * (1 + self.illum),
                                          ls=(0, (2, 2.5)), alpha=0.9, zorder=4)[0]
        else:
            self.moon_line = None

    def _coords(self, b: Block):
        t = self.by_id.get(b.target_id)
        if t is not None and t.ra is not None and t.dec is not None:
            return t.ra, t.dec
        return b.ra, b.dec

    def _block(self, i: int, b: Block):
        ax, look = self.ax, self.look
        tid = b.target_id or f"#{i}"
        mode = b.mode if b.mode in "VTD" else "V"
        col = look["mode"][mode]
        ra, dec = self._coords(b)
        x0, x1 = float(self.x(b.start)), float(self.x(b.end))
        self._reg(tid, ax.axvspan(x0, x1, color=col, alpha=0.13, lw=0, zorder=1), "span")
        if mode == "T" and b.t1 and b.t4:
            tl = look["transit"]
            for a, z in ((b.start, b.t1), (b.t4, b.end)):
                if z > a:
                    self._reg(tid, ax.axvspan(float(self.x(a)), float(self.x(z)), color=tl, alpha=0.07, lw=0,
                                              zorder=1), "span")
            self._reg(tid, ax.axvspan(float(self.x(b.t1)), float(self.x(b.t4)), color=tl, alpha=0.2, lw=0,
                                      zorder=1), "span")
            for t in (b.t1, b.t4):
                self._reg(tid, ax.axvline(float(self.x(t)), color=tl, lw=1.1, zorder=2), "line")
            self._reg(tid, ax.text(float(self.x(b.t1)), 0.25, f" Transit {self.hhmm(b.t1)}–{self.hhmm(b.t4)}",
                                   transform=ax.get_xaxis_transform(), color=look["transit_text"], fontsize=8,
                                   va="center", zorder=5), "text")
        if ra is None or dec is None:
            return
        alt = np.asarray(sky.target_alt(ra, dec, self.site, self.jd), dtype=float)
        xa = np.where(alt > 0, sky.airmass_from_alt(np.clip(alt, 0.01, 90)), np.nan)
        self.paths[tid] = (xa, alt)
        self._reg(tid, ax.plot(self.xs, xa, color=look["path"], lw=0.9, alpha=0.55, zorder=3)[0], "path")
        # the block itself: bold, the exact block ends included
        inb = (self.jd > b.start) & (self.jd < b.end)
        jj = np.concatenate([[b.start], self.jd[inb], [b.end]])
        xb = np.asarray(sky.target_airmass(ra, dec, self.site, jj), dtype=float)
        xx = self.x(jj)
        self._reg(tid, ax.plot(xx, xb, color=look["under"], lw=5.0, solid_capstyle="round", zorder=6)[0], "line")
        self._reg(tid, ax.plot(xx, xb, color=col, lw=2.8, solid_capstyle="round", zorder=7)[0], "line")
        # airmass at the start and the end (inside the block, under the line)
        tc = look["on_sky"]
        st = en = None
        if np.isfinite(xb[0]):
            st = self._reg(tid, ax.text(xx[0], xb[0] + 0.03, f"{xb[0]:.2f}", fontsize=7.5, color=tc, ha="left",
                                        va="top", zorder=8), "text")
        if np.isfinite(xb[-1]):
            en = self._reg(tid, ax.text(xx[-1], xb[-1] + 0.03, f"{xb[-1]:.2f}", fontsize=7.5, color=tc,
                                        ha="right", va="top", zorder=8), "text")
        self.edge_texts.append((st, en, b))
        # ▼ only when the meridian is inside the block
        mer = b.meridian
        if mer is None or not (b.start - 0.6 <= mer <= b.end + 0.6):
            mer = sky.target_meridian(ra, dec, self.site, 0.5 * (b.start + b.end))
        b.meridian = mer
        if b.start <= mer <= b.end:
            xm = float(sky.target_airmass(ra, dec, self.site, mer))
            if np.isfinite(xm):
                self._reg(tid, ax.plot([float(self.x(mer))], [xm], marker="v", ms=8, color=look["flip"],
                                       mec=MODE_FILL[mode], ls="none", zorder=9, clip_on=False)[0], "line")
                word = "flip" if (self.site.mount or "GEM") == "GEM" else "meridian"
                if xm - AIRMASS_TOP > 0.09:                # room above the mark
                    at = dict(x=float(self.x(mer)), y=xm - 0.03, ha="center", va="bottom")
                else:                                      # at the top edge: under the line
                    at = dict(x=float(self.x(mer)), y=xm + 0.11, ha="center", va="top")
                self._reg(tid, ax.text(at.pop("x"), at.pop("y"), f"{word} {self.hhmm(mer)}", fontsize=7.5,
                                       color=tc, zorder=9, **at), "text")
        # the label box
        mid = 0.5 * (b.start + b.end)
        sep = b.moon_sep if b.moon_sep is not None else float(sky.target_moon_sep(ra, dec, mid))
        lines = [b.name + STATUS_MARK.get(b.status, ""), f"{self.hhmm(b.start)}–{self.hhmm(b.end)}",
                 f"Moon {sep:.0f}°"]
        locked = b.locked or b.set_by_user
        lab = ax.text(float(self.x(mid)), self.bottom - 0.23, "\n".join(lines), ha="center", va="center",
                      fontsize=8.5, color="#ffffff", fontweight="bold", zorder=10, linespacing=1.15,
                      bbox=dict(boxstyle="round,pad=0.3", fc=MODE_FILL[mode], ec=LOCK if locked else "#ffffff",
                                lw=1.6 if locked else 0.6, alpha=0.9))
        self._reg(tid, lab, "label")
        self.labels.append((lab, mid, max(len(s) for s in lines), tid, i))

    def _blackout(self, bo: dict):
        ax, look = self.ax, self.look
        a, z = float(self.x(max(bo["start"], self.j0))), float(self.x(min(bo["end"], self.j1)))
        if z <= a:
            return
        tid = bo["target_id"]
        p = ax.axvspan(a, z, facecolor="none", edgecolor=look["blackout"], hatch="///", lw=0, alpha=0.6, zorder=2)
        self._reg(tid, p, "span")
        reason = str(bo.get("reason", "")).replace(" transits", " transit")
        self._reg(tid, ax.text(a, self.bottom - 0.06, f" no {bo['name']} ({reason})", color=look["blackout_text"],
                               fontsize=7.5, va="bottom", zorder=5, clip_on=True), "text")

    def _weather(self):
        axw, look = self.axw, self.look
        self._sky(axw, look["wx_sky_alpha"])
        axw.set_ylim(0, 100)
        axw.tick_params(labelbottom=False, labelsize=7)
        self.axt = None
        self.spread_texts = []
        tu = (self.site.temp_unit or "C").upper()
        if not self.has_wx:
            note = (self.weather or {}).get("note") or ""
            axw.text(0.5, 0.5, "No forecast for this night" + (f" ({note})" if note else ""),
                     transform=axw.transAxes, ha="center", va="center", fontsize=8, color=look["text"])
            axw.set_yticks([])
            return
        wt = np.array([float(self.x(h["jd"])) for h in self.hours])
        cloud = np.array([float(h.get("cloud") or 0.0) for h in self.hours])
        axw.bar(wt, cloud, width=0.9, color=CLOUD, alpha=0.85, zorder=2)
        axw.set_yticks([0, 50, 100])
        axw.set_ylabel("Cloud %", fontsize=7)
        tmp = np.array([np.nan if h.get("temp_c") is None else _f(float(h["temp_c"]), tu) for h in self.hours])
        dew = np.array([np.nan if h.get("dew_c") is None else _f(float(h["dew_c"]), tu) for h in self.hours])
        axt = self.axt = axw.twinx()
        axt.tick_params(labelsize=7)
        axt.set_ylabel(f"°{tu}", fontsize=7)
        if np.isfinite(tmp).any():
            axt.plot(wt, tmp, color=TEMP, lw=1.6, zorder=3)
            axt.plot(wt, dew, color=DEW, lw=1.6, zorder=3)
            ok = np.isfinite(tmp) & np.isfinite(dew)
            axt.fill_between(wt, dew, tmp, where=ok, color="#ffcc80", alpha=0.25, lw=0, interpolate=True)
            lim = float(self.options.get("dew_spread_c", 2.5))
            spread_c = np.array([(h.get("spread_c") if h.get("spread_c") is not None else
                                  (h["temp_c"] - h["dew_c"] if h.get("temp_c") is not None and
                                   h.get("dew_c") is not None else np.nan)) for h in self.hours], dtype=float)
            risk = ok & (spread_c < lim)
            if risk.any():
                axt.fill_between(wt, dew, tmp, where=risk, color=DEW, alpha=0.4, lw=0, interpolate=True)
            hi = float(np.nanmax(tmp))
            lo = float(np.nanmin(np.concatenate([dew[np.isfinite(dew)], tmp[np.isfinite(tmp)]])))
            span = max(4.0, hi - lo)
            axt.set_ylim(lo - 0.15 * span, hi + 0.45 * span)
            for x, s in zip(wt, spread_c):
                if np.isfinite(s) and 0 <= x <= float(self.x(self.j1)):
                    v = s * 1.8 if tu == "F" else s
                    t = axt.text(x, hi + 0.2 * span, f"{round(v) + 0:.0f}", fontsize=7, ha="center",
                                 color="#c62828" if s < lim else "#6d4c41", zorder=4)
                    self.spread_texts.append(t)
        onset = (self.weather or {}).get("dew_onset")
        if onset and self.j0 <= onset <= self.j1:
            axw.axvline(float(self.x(onset)), color="#8a5a00", lw=1.2, ls=(0, (4, 3)), zorder=4)
            axw.text(float(self.x(onset)), 96, f" ⚠ dew from {self.hhmm(onset)}", color="#8a5a00", fontsize=7.5,
                     va="top", ha="left", zorder=5, fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.15", fc="#fff3c4", ec="#8a5a00", lw=0.5))

    def _title(self):
        fig, look, site = self.fig, self.look, self.site
        fmt = site.date_format or "%a %d %b"
        when = f"{self.d.strftime(fmt)} {self.d.year}"
        bits = [when] + ([site.name] if site.name else []) + [f"Moon {self.illum * 100:.0f} %  (dashed gold; "
                                                               "thicker = fuller)"]
        self.title = fig.text(0.01, 0.99, "  ·  ".join(bits), fontsize=11, fontweight="bold", color=look["text"],
                              ha="left", va="top")
        self.sel_text = fig.text(0.99, 0.99, "", fontsize=9, color=look["text"], ha="right", va="top")
        src = (self.weather or {}).get("source") or "forecast"
        tu = (self.site.temp_unit or "C").upper()
        lim = float(self.options.get("dew_spread_c", 2.5))
        lim_t = f"{lim * 1.8:.1f} °F" if tu == "F" else f"{lim:g} °C"
        wx_txt = (f"Weather ({src}): grey bars = cloud %;  red = temperature, blue = dew point;  numbers = spread "
                  f"°{tu} (blue fill = dew risk, spread < {lim_t})") if self.has_wx else "Weather"
        self.wx_title = fig.text(0.01, 0.9, wx_txt, fontsize=7.5, color="#37474f", ha="left", va="bottom")

    # ---- size-dependent placement ----
    def layout(self):
        """Place the axes in inches (so text never squeezes the plot), choose 1- or 2-hour ticks, and stagger the
        labels that would overlap at this width. Cheap: call it after every resize."""
        fig = self.fig
        W, H = fig.get_size_inches()
        if W < 1 or H < 1:
            return
        L, R, B = 0.62, 0.62, 0.48
        top = 0.28
        wx_h = min(1.5, max(0.75, 0.2 * H)) if self.has_wx else 0.32
        gap_title = 0.22
        gap = 0.12
        main_h = max(0.5, H - top - gap_title - wx_h - gap - B)
        fx = lambda v: v / W
        fy = lambda v: v / H
        aw = fx(W - L - R)
        self.axw.set_position([fx(L), fy(B + main_h + gap), aw, fy(wx_h)])
        self.ax.set_position([fx(L), fy(B), aw, fy(main_h)])
        if self.ax2 is not None:
            self.ax2.set_position(self.ax.get_position())
        if self.axt is not None:
            self.axt.set_position(self.axw.get_position())
        self.title.set_position((fx(0.12), 1 - fy(0.06)))
        self.sel_text.set_position((1 - fx(0.12), 1 - fy(0.08)))
        self.wx_title.set_position((fx(L), fy(B + main_h + gap + wx_h + 0.04)))
        # hour ticks
        dpi = fig.dpi
        hours = (self.j1 - self.j0) * 24
        pph = (W - L - R) * dpi / max(1.0, hours)          # pixels per hour
        every = 1 if pph >= 7.5 * dpi / 72 * 5 else 2
        ticks = hour_ticks(self.j0, self.j1, self.tz, every)
        self.ax.xaxis.set_major_locator(self.mpl["FixedLocator"](list(self.x(ticks))))
        self.ax.set_xticklabels([self.hhmm(j) for j in ticks])
        self.ax.set_xlim(0, float(self.x(self.j1)))
        self.every = every
        hpj = 24.0                                         # x units per day
        char = 0.6 * 8.5 / 72 * dpi                         # px per bold label character (estimate)
        ax_px = main_h * dpi                               # main plot height in pixels
        per_px = (self.bottom - AIRMASS_TOP) / max(1.0, ax_px)
        # block labels: lowest lane where they do not overlap (a box is 3 lines of 8.5 pt plus its padding)
        lanes: list[float] = []
        box = (3 * 8.5 * 1.15 + 2 * 0.3 * 8.5 + 4) / 72 * dpi
        lane_y = [self.bottom - per_px * (box / 2 + 6 + k * (box + 5)) for k in range(3)]
        for lab, mid, nch, _tid, _i in sorted(self.labels, key=lambda p: p[1]):
            half = (nch * char / 2 + 6) / pph               # hours
            x = (mid - self.j0) * hpj
            k = 0
            while k < len(lanes) and lanes[k] > x - half:
                k += 1
            if k == len(lanes):
                lanes.append(-1e9)
            k = min(k, len(lane_y) - 1)
            lanes[k] = x + half
            lab.set_position((x, lane_y[k]))
        # start / end numbers: both only when the block is wide enough
        need = 2 * 4 * 0.6 * 7.5 / 72 * dpi + 6
        for st, en, b in self.edge_texts:
            wide = (b.end - b.start) * 24 * pph >= need
            if en is not None:
                en.set_visible(wide or st is None)
        # twilight labels: alternate two rows where they crowd
        tw = 7 * 0.6 * 7.5 / 72 * dpi / pph                # hours a label takes
        last = {}
        for t, j in self.twilight_texts:
            x = (j - self.j0) * 24
            side = t.get_ha()
            row = 0
            if side in last and abs(x - last[side][0]) < tw and last[side][1] == 0:
                row = 1
            last[side] = (x, row)
            t.set_y(0.995 if row == 0 else 0.995 - 2.3 * 7.5 * 1.1 / 72 * dpi / max(1.0, ax_px))
        # spread numbers: every hour, or every other hour when narrow
        step = 1 if pph >= 22 * dpi / 100 else 2
        for k, t in enumerate(self.spread_texts):
            t.set_visible(k % step == 0)

    # ---- mouse ----
    def block_at(self, x: float) -> int | None:
        """Index (in night.blocks) of the block at plot x, or None."""
        j = self.jd_of(x)
        for i, b in self.blocks:
            if b.start <= j <= b.end:
                return i
        return None

    def target_of_block(self, i: int | None) -> str | None:
        for k, b in self.blocks:
            if k == i:
                return b.target_id or f"#{k}"
        return None

    def hover_text(self, x: float, y: float | None = None, in_main: bool = True) -> str:
        """What is under the mouse at plot x (and airmass y on the main plot): site time, the target or Moon line
        nearest the pointer (airmass and elevation), the block at that time, the weather of that hour."""
        if x is None or not (0 <= x <= float(self.x(self.j1))):
            return ""
        j = self.jd_of(x)
        k = int(np.clip(round((j - self.j0) * 1440 / self.GRID_MIN), 0, len(self.jd) - 1))
        bits = [self.hhmm(j)]
        if in_main and y is not None:
            best = None
            for tid, (xa, alt) in self.paths.items():
                v = xa[k]
                if np.isfinite(v) and abs(v - y) < 0.08 and (best is None or abs(v - y) < best[0]):
                    name = next((b.name for _i, b in self.blocks if (b.target_id or f"#{_i}") == tid), tid)
                    best = (abs(v - y), f"{name}: X {v:.2f}, {alt[k]:.0f}°")
            mv = self.moon_x[k]
            if np.isfinite(mv) and abs(mv - y) < 0.08 and (best is None or abs(mv - y) < best[0]):
                best = (abs(mv - y), f"Moon: X {mv:.2f}, {self.moon_alt[k]:.0f}°")
            if best:
                bits.append(best[1])
        i = self.block_at(x)
        if i is not None:
            b = dict(self.blocks)[i]
            bits.append(f"block {b.name} {self.hhmm(b.start)}–{self.hhmm(b.end)}")
        if self.hours:
            h = min(self.hours, key=lambda r: abs(r["jd"] - j))
            if abs(h["jd"] - j) <= 0.75 / 24:
                tu = (self.site.temp_unit or "C").upper()
                w = []
                if h.get("cloud") is not None:
                    w.append(f"cloud {float(h['cloud']):.0f} %")
                if h.get("temp_c") is not None:
                    w.append(f"{round(_f(float(h['temp_c']), tu)) + 0:.0f} °{tu}")
                if h.get("dew_c") is not None:
                    w.append(f"dew point {round(_f(float(h['dew_c']), tu)) + 0:.0f} °{tu}")
                sp = h.get("spread_c")
                if sp is None and h.get("temp_c") is not None and h.get("dew_c") is not None:
                    sp = h["temp_c"] - h["dew_c"]
                if sp is not None:
                    w.append(f"spread {round(sp * 1.8 if tu == 'F' else sp) + 0:.0f} °{tu}")
                wv, wu = _wind(h.get("wind_kmh"), self.site.length_unit or "m")
                if wv is not None:
                    w.append(f"wind {wv:.0f} {wu}")
                if w:
                    bits.append(", ".join(w))
        return "   ·   ".join(bits)


def tk_canvas(master):
    """A FigureCanvasTkAgg (white, empty figure) for the Schedule tab, or None when matplotlib (or its Tk backend)
    does not load."""
    mpl = _load()
    if mpl is None:
        return None
    try:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    except Exception:
        return None
    fig = mpl["Figure"](figsize=(10, 6), dpi=100, facecolor="#ffffff")
    return FigureCanvasTkAgg(fig, master=master)


def png_bytes(night, site, targets_by_id, weather, options, print_style=True) -> bytes | None:
    """The night plot as a PNG (landscape, 10 in wide at 150 dpi, for the printed plan card). print_style=True: a
    white background and colors that read on paper; False: the screen look. None when matplotlib is missing or the
    night cannot be drawn (no date, site incomplete). Data types: see the module docstring."""
    mpl = _load()
    if mpl is None:
        return None
    try:
        n = _night(night)
        if not n.date or not site.complete():
            return None
        fig = mpl["Figure"](figsize=(10, 5.6), dpi=150)
        mpl["Agg"](fig)
        NightPlot(n, site, targets_by_id, weather, options, print_style=print_style, figure=fig).draw()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, facecolor="#ffffff")
        return buf.getvalue()
    except Exception:
        import traceback
        try:
            import settings
            settings.log("nightplot.png_bytes: " + traceback.format_exc(limit=3))
        except Exception:
            pass
        return None
