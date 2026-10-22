"""SHOBS-SCHED planner: builds each night's blocks (Requirements doc, decisions of 20 Oct).

One night (build_night), in this order:
  1. Locked / set-by-user blocks (and blocks already marked Done/Partial/Clouded), exactly as they are; ⚠ when
     one is no longer possible (target below the limits, outside the darkness, inside a blackout, overlapping).
  2. Fixed-time targets: transits (ingress − baseline … egress + baseline; the transit itself inside the planning
     darkness and above every limit; the baselines may run into nautical twilight and may be shortened to fit, down
     to `baseline_trim_min` a side) and timed variable events (event ± event_halfwidth).
     Collisions: first trim baselines toward `baseline_trim_min`; if both then fit, keep both (⚠ "baseline
     trimmed"); otherwise the winner is the `prefer`red target, then higher priority, then the rarer next chance
     (next feasible full event within 60 nights), then the better S/N (depth / noise by V).
  3. Flexible targets (campaigns incl. Discovery fields, any-time variables) by priority (a campaign behind its
     weekly goal moves up one level), each at most once, in free time near its ideal spot (below), never inside
     its blackouts, inside its own rules (min_alt, min_moon_sep, window) and the site limits.
  4. Flip rule (GEM): never START a block within an hour after its target's meridian; the start goes to
     `flip_lead_min` before the meridian ("Start before the meridian and let it flip (~HH:MM); drop the pre-flip
     frames"), and a flip costs `flip_settle_min` of usable time. A block ending inside the mount's
     `site.past_meridian_min` tracking margin needs no flip ("stop by HH:MM at the latest"). Alt-az mounts never
     flip (⚠ zenith keyhole above 85°). Southern sites: the same hour-angle logic.
  5. Overheads: `overhead_slew_min` between blocks, `overhead_focus_min` at the start of the night (flexible blocks
     start no earlier than dusk + focus) and again after a defocused block ("defocus" in the target's notes).
  6. Weather: flexible blocks end at the dew onset (`dew_sensitive_end`), ⚠ on fixed blocks crossing it, ⚠ when
     ≥ 70 % cloud covers most of a block (cloudy hours are not removed in v1).
  7. Didn't fit (with the reason and the next chance), free gaps, flats and darks lines.

Where a flexible block goes. Candidates are every whole-minute start in the free time (length min(block_min, room)).
The cost of a candidate is its distance (minutes) from the IDEAL start, plus LOST_MIN_WEIGHT per usable minute
it loses against the longest candidate (shorter room, or a flip's settle time), plus AIRMASS_WEIGHT per unit of
worse mean airmass; the cheapest wins. The ideal start on a GEM is `flip_lead_min` before the meridian: the flip
happens at the start while you are there, then the whole run is on one pier side with the best part of the night
for that target (this is also how John's hand plans place blocks). On an alt-az mount the block is centred on the
meridian. A target whose meridian is before dusk thus starts as early as it can, one whose meridian is after dawn
runs as late as it can.

Times: JD (UTC) inside; the site's zone (site.tz) for every clock time in a note, never the computer's."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np

import events
import sky
from models import PRIORITY_RANK, Block, NightPlan, Schedule, Target
from settings import DEFAULT_OPTIONS

MIN = 1.0 / 1440.0
SIDEREAL_DAY = 0.99726957
STANDARD_EXPOSURES = (0.75, 1, 2, 5, 10, 20, 30, 40, 60, 90, 120, 180, 300)
FLIP_AFTER_MIN = 60.0          # a block must not START within this long after its target's meridian (GEM)
FLIP_BEFORE_MIN = 5.0          # ... nor this close before it (no time to settle before the flip)
SEARCH_NIGHTS = 60             # how far ahead "next chance" looks
AIRMASS_WEIGHT = 300.0         # flexible placement: minutes of distance from the ideal start per unit mean airmass
LOST_MIN_WEIGHT = 1.5          # flexible placement: minutes of distance from the ideal start per usable minute lost
NUDGE_MAX_MIN = 20.0           # an earlier flexible block may end up to this much early so the next one can start
                               # before its meridian (instead of an hour after it)
EPS = 0.2 * MIN

# Cold-start exposure model (estimate_exposure). Calibrated so John's anchor (V 11.4, 60 s, 14" f/8,
# ASI2600MC gain 100 = 0.243 e/ADU, 3" seeing, airmass 1.2) predicts a raw green peak of ~15k ADU (data: 10–17k).
V0_PHOTONS = 8.8e5             # photons s⁻¹ cm⁻² from a V = 0 star across the V band (~1000 ph/s/cm²/Å × 880 Å)
OBSTRUCTION_AREA = 0.30        # fraction of the aperture area lost to the central obstruction (and spider)
OPTICS_THROUGHPUT = 0.80       # mirrors, corrector, window
MONO_BAND_FACTOR = 2.5         # an unfiltered mono camera collects about this much more than a Bayer green pixel
CLOUDY_PCT = 70.0


# ---- small helpers ---------------------------------------------------------------------------------------------

def _opts(options: dict | None) -> dict:
    o = dict(DEFAULT_OPTIONS)
    o.update(options or {})
    return o


def fmt_exposure(s: float | None) -> str:
    if s is None:
        return "—"
    return f"{float(s):g} s"


def _k(adu: float) -> str:
    """32000 → '32k'."""
    return f"{adu / 1000:.0f}k"


def _fmt_day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _is_fixed(t: Target) -> bool:
    if t.kind == "transit":
        return True
    if t.kind == "variable":
        el = t.elements_obj()
        return bool(el and not el.any_time and el.period and el.epoch)
    return False


def _defocused(t: Target | None) -> bool:
    return bool(t and "defocus" in (t.notes or "").lower())


def _short_planet(name: str) -> str:
    """'HD 219134 c' → 'planet c' (plan-doc style); other names unchanged."""
    parts = (name or "").split()
    if len(parts) >= 2 and len(parts[-1]) == 1 and parts[-1].isalpha():
        return f"planet {parts[-1]}"
    return name or "planet"


def _period_text(p_days: float) -> str:
    return f"P ≈ {p_days * 24:.2f} h" if p_days < 2.0 else f"P ≈ {p_days:.2f} d"


def _noise_proxy(v: float | None) -> float:
    """Relative noise per point by V (scintillation/systematics floor + photon noise): a documented proxy only."""
    v = 12.0 if v is None else float(v)
    return math.sqrt(0.5 ** 2 + 10 ** (0.4 * (v - 12.0)))


def _depth_of(t: Target) -> float:
    if t.kind == "transit":
        pl = t.planet_obj()
        return float(pl.depth or 0.005) if pl else 0.005
    el = t.elements_obj()
    if el and el.mag_max is not None and el.mag_min is not None:
        return abs(el.mag_min - el.mag_max) * 0.921
    return 0.05


def _snr(t: Target) -> float:
    return _depth_of(t) / _noise_proxy(t.vmag)


def _airmass(alt):
    a = np.asarray(alt, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = 1.0 / np.sin(np.radians(np.maximum(a, 1.0)))
    return x


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(i0, i1), ...] half-open index runs where mask is True."""
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist()))


# ---- exposure ----------------------------------------------------------------------------------------------------

def _e_per_adu(optics) -> tuple[float, bool]:
    if optics.e_per_adu:
        return float(optics.e_per_adu), True
    if optics.full_well_e:
        return float(optics.full_well_e) / 65535.0, False
    return 1.0, False


def predict_peak_adu(vmag, exposure_s, optics, options=None, airmass: float = 1.2,
                     moon_penalty: float = 0.0) -> float | None:
    """Predicted raw peak (ADU) of a star's brightest pixel (Bayer green for a color camera), sky included.
    Gaussian PSF of FWHM options['fwhm_arcsec'] integrated over the central pixel. None when V or the optics are
    missing."""
    if vmag is None or not optics or not optics.aperture_mm:
        return None
    scale = optics.pixel_scale()
    if not scale:
        return None
    o = _opts(options)
    fwhm = float(o.get("fwhm_arcsec") or 3.0)
    d_cm = float(optics.aperture_mm) / 10.0
    area = math.pi * (d_cm / 2.0) ** 2 * (1.0 - OBSTRUCTION_AREA)
    band = 1.0 if optics.color_camera else MONO_BAND_FACTOR
    trans = OPTICS_THROUGHPUT * 10 ** (-0.4 * sky.EXTINCTION_K * max(1.0, float(airmass or 1.2)))
    rate0 = V0_PHOTONS * area * trans * float(optics.qe or 0.8) * band          # e⁻/s for V = 0
    star_e = rate0 * 10 ** (-0.4 * float(vmag)) * float(exposure_s)
    sigma_px = fwhm / 2.3548 / scale
    frac = math.erf(0.5 / (math.sqrt(2.0) * sigma_px)) ** 2
    sky_v = float(o.get("dark_sky_v", sky.DARK_SKY_V)) - max(0.0, float(moon_penalty or 0.0))
    sky_e = rate0 * 10 ** (-0.4 * sky_v) * scale ** 2 * float(exposure_s)
    epa, _ = _e_per_adu(optics)
    return (star_e * frac + sky_e) / epa


def estimate_exposure(target: Target, optics, options: dict | None = None, airmass: float = 1.2,
                      moon_penalty: float = 0.0) -> dict:
    """{exposure_s, peak_adu, is_estimate, note}. With target.exposure_s set by the user, that exposure is kept
    and its peak predicted (⚠ when over the linear limit). Otherwise the longest standard exposure whose predicted
    peak stays under target_peak_frac × linear limit — always labelled an estimate."""
    o = _opts(options)
    limit = float(optics.linear_limit_adu or 32000.0) if optics else 32000.0
    aim = float(o.get("target_peak_frac") or 0.8) * limit
    fwhm = float(o.get("fwhm_arcsec") or 3.0)

    def peak(e):
        return predict_peak_adu(target.vmag, e, optics, o, airmass, moon_penalty)

    if target.exposure_s:
        e = float(target.exposure_s)
        if _defocused(target):
            return {"exposure_s": e, "peak_adu": None, "is_estimate": False,
                    "note": "Your exposure; defocused: peak estimate not valid"}
        p = peak(e)
        if p is None:
            return {"exposure_s": e, "peak_adu": None, "is_estimate": False, "note": "Your exposure"}
        # The cold-start model is rough (seeing, focus, transparency): only flag a clear excess.
        if p >= 2.0 * limit:
            note = (f"✖ estimate: peak ~{_k(p)}, over twice your {_k(limit)} limit — shorten {fmt_exposure(e)} "
                    f"or defocus")
        elif p > 1.5 * limit:
            note = f"⚠ estimate: peak ~{_k(p)}, near your {_k(limit)} limit — check a frame"
        else:
            note = f"Your exposure; predicted peak ~{p:,.0f} ADU"
        return {"exposure_s": e, "peak_adu": p, "is_estimate": False, "note": note}
    if target.vmag is None:
        return {"exposure_s": 60.0, "peak_adu": None, "is_estimate": True,
                "note": "estimate: 60 s placeholder (no V magnitude); set your own exposure"}
    if peak(60.0) is None:
        return {"exposure_s": 60.0, "peak_adu": None, "is_estimate": True,
                "note": "estimate: 60 s placeholder (telescope and camera not set up)"}
    best = None
    for e in STANDARD_EXPOSURES:
        if peak(e) <= aim:
            best = e
    if best is None:
        best = STANDARD_EXPOSURES[0]
        p = peak(best)
        note = (f"⚠ estimate: even {fmt_exposure(best)} may saturate (predicted peak ~{p:,.0f} ADU, limit "
                f"{limit:,.0f}); defocus")
    else:
        p = peak(best)
        note = f"estimate from V {float(target.vmag):.1f} and {fwhm:g}\" seeing: peak ~{p:,.0f} ADU"
    return {"exposure_s": float(best), "peak_adu": p, "is_estimate": True, "note": note}


# ---- darkness cache (next-chance searches look at many nights) -------------------------------------------------

_DARK_CACHE: dict = {}


def _dark(d: date, site, level: float | None = None) -> tuple[float, float] | None:
    lvl = site.sun_alt if level is None else level
    key = (round(site.lat, 5), round(site.lon, 5), site.tz, float(lvl), d.isoformat())
    if key not in _DARK_CACHE:
        if len(_DARK_CACHE) > 20000:
            _DARK_CACHE.clear()
        _DARK_CACHE[key] = sky.night_window(d, site, lvl)
    return _DARK_CACHE[key]


def _up_all(t: Target, site, a: float, b: float, min_alt=None, n: int = 9) -> bool:
    jd = np.linspace(a, b, n)
    return bool(np.all(sky.up_mask(t.ra, t.dec, site, jd, min_alt)))


def _rule_min_alt(t: Target):
    for r in t.rules_obj():
        if r.kind == "min_alt" and r.value is not None:
            try:
                return float(r.value)
            except (TypeError, ValueError):
                pass
    return None


def _next_chance(t: Target, d: date, site, options: dict, nights: int = SEARCH_NIGHTS) -> date | None:
    """The first night after d with a feasible full event (fixed targets) or a long-enough dark, up stretch
    (flexible targets). None when none within `nights`."""
    if t.ra is None or t.dec is None or not site.complete():
        return None
    min_alt = _rule_min_alt(t)
    if _is_fixed(t):
        j0 = sky.local_noon_jd(d + timedelta(days=1), site.tz)
        j1 = sky.local_noon_jd(d + timedelta(days=nights + 1), site.tz)
        if t.kind == "transit":
            pl = t.planet_obj()
            evs = [(e["t1"], e["t4"]) for e in events.transit_events(pl, t.ra, t.dec, j0, j1)] if pl and pl.ok() else []
        else:
            el = t.elements_obj()
            evs = [(e["t"], e["t"]) for e in events.variable_events(el, t.ra, t.dec, j0, j1)]
        for a, b in evs:
            nd = sky.night_date_of(0.5 * (a + b), site.tz)
            if nd <= d:
                continue
            w = _dark(nd, site)
            if not w or a < w[0] or b > w[1]:
                continue
            if _up_all(t, site, a, b, min_alt):
                return nd
        return None
    need = float(t.min_block_min or 45.0)
    for k in range(1, nights + 1):
        nd = d + timedelta(days=k)
        w = _dark(nd, site)
        if not w:
            continue
        jd = np.arange(w[0], w[1], 5 * MIN)
        if len(jd) < 2:
            continue
        up = sky.up_mask(t.ra, t.dec, site, jd, min_alt)
        best = max((b - a for a, b in _runs(up)), default=0) * 5
        if best >= need:
            return nd
    return None


# ---- the night builder -----------------------------------------------------------------------------------------

@dataclass
class _P:
    """A placed (or candidate) stretch of time while the night is being built."""
    tg: Target | None
    tid: str
    name: str
    kind: str                       # "locked", "transit", "event", "flex"
    start: float
    end: float
    c_lo: float | None = None       # fixed: the event core (t1…t4, or the event time)
    c_hi: float | None = None
    min_pre: float = 0.0            # days of margin before/after the core that must stay
    min_post: float = 0.0
    ev: dict | None = None
    block: Block | None = None      # the user's block (locked)
    notes: list = field(default_factory=list)
    cautions: list = field(default_factory=list)
    trimmed_by: list = field(default_factory=list)
    dew_cut: bool = False
    fallback_flip: bool = False

    @property
    def trimmable(self) -> bool:
        return self.kind in ("transit", "event")

    @property
    def defocus(self) -> bool:
        return _defocused(self.tg)


class _Night:
    def __init__(self, d, site, optics, targets, options, locked, weather, history, prefer):
        self.d = d
        self.site = site
        self.optics = optics
        self.targets = list(targets or [])
        self.o = _opts(options)
        self.locked = list(locked or [])
        self.weather = weather or None
        self.history = list(history or [])
        self.prefer = set(prefer or ())
        self.tz = site.tz
        self.placed: list[_P] = []
        self.done_ids: set[str] = set()
        self.plan = NightPlan(date=d.isoformat())
        self._arrays: dict = {}
        self._next: dict = {}

    # -- small utilities --
    def hh(self, jd):
        return sky.hhmm(jd, self.tz)

    def gap(self, a: Target | None, b: Target | None) -> float:
        """Overhead between a block of target a and a following block of target b: the slew (none when both are
        the same star, e.g. planets b and c of one system) plus a refocus after a defocused block."""
        same = (a is not None and b is not None and a.ra is not None and b.ra is not None
                and float(sky.sep_deg(a.ra, a.dec, b.ra, b.dec)) < 1.0 / 60.0)
        g = 0.0 if same else float(self.o["overhead_slew_min"])
        if _defocused(a) and not _defocused(b):
            g += float(self.o["overhead_focus_min"])
        return g * MIN

    def next_chance(self, t: Target) -> date | None:
        if t.id not in self._next:
            self._next[t.id] = _next_chance(t, self.d, self.site, self.o)
        return self._next[t.id]

    def next_text(self, t: Target) -> tuple[str, str]:
        nd = self.next_chance(t)
        if nd is None:
            return "", f"no chance in the next {SEARCH_NIGHTS} nights"
        return nd.isoformat(), f"next chance {_fmt_day(nd)}"

    def didnt_fit(self, t: Target, reason: str, with_next: bool = True):
        iso, txt = self.next_text(t) if with_next else ("", "")
        full = f"{reason}; {txt}" if txt else reason
        self.plan.didnt_fit.append({"target_id": t.id, "name": t.name, "reason": full, "next_chance": iso})

    def arr(self, t: Target) -> dict:
        """Per-target arrays on the night grid: alt, lim, up, airmass, moon separation."""
        if t.id in self._arrays:
            return self._arrays[t.id]
        g = self.grid
        alt = np.asarray(sky.target_alt(t.ra, t.dec, self.site, g), dtype=float)
        min_alt = _rule_min_alt(t)
        lim = np.asarray(sky.limit_alt(t.ra, t.dec, self.site, g, min_alt), dtype=float)
        a = {"alt": alt, "lim": lim, "up": alt >= lim, "x": _airmass(alt),
             "moon": np.asarray(sky.target_moon_sep(t.ra, t.dec, g), dtype=float)}
        self._arrays[t.id] = a
        return a

    def idx(self, jd: float) -> int:
        return int(round((jd - self.grid[0]) * 1440.0))

    def meridians(self, t: Target) -> list[float]:
        mid = 0.5 * (self.dusk + self.dawn)
        m = sky.target_meridian(t.ra, t.dec, self.site, mid)
        return [m - SIDEREAL_DAY, m, m + SIDEREAL_DAY]

    def meridian_near(self, t: Target, jd: float) -> float:
        return sky.target_meridian(t.ra, t.dec, self.site, jd)

    @property
    def gem(self) -> bool:
        return (self.site.mount or "GEM").lower() != "altaz"

    # -- the build --
    def build(self) -> NightPlan:
        site, plan = self.site, self.plan
        plan.built = datetime.now(timezone.utc).isoformat(timespec="seconds")
        enabled = [t for t in self.targets if t.enabled or t.id in self.prefer]
        if not site.complete():
            plan.notes.append("✖ Site not set up (latitude, longitude and time zone are needed)")
            for t in enabled:
                self.didnt_fit(t, "site not set up", with_next=False)
            self._keep_locked_without_sky("site not set up")
            return plan
        if not sky.zone_ok(site.tz):
            plan.notes.append(f"✖ Time zone '{site.tz}' not available; times shown in UTC")
        info = sky.night_info(self.d, site)
        self.info = info
        plan.dusk, plan.dawn = info["dusk"], info["dawn"]
        plan.civil_dusk, plan.civil_dawn = info["civil_dusk"], info["civil_dawn"]
        plan.nautical_dusk, plan.nautical_dawn = info["nautical_dusk"], info["nautical_dawn"]
        plan.moon_illum, plan.moonrise, plan.moonset = info["moon_illum"], info["moonrise"], info["moonset"]
        if self.weather:
            plan.weather = self.weather
        if not info["dark_ok"]:
            lowest = info.get("darkest_sun_alt")

            def deg(x):
                return f"{x:.1f}°".replace("-", "−")

            why = (f"no dark time tonight: the Sun gets no lower than {deg(lowest)} (planning darkness "
                   f"{deg(site.sun_alt)})")
            if site.sun_alt < sky.NAUTICAL and lowest is not None and lowest < sky.NAUTICAL:
                why += "; nautical darkness (Sun below −12°) is possible if you allow it in Setup"
            elif lowest is not None and lowest < sky.CIVIL:
                why += f"; a brighter darkness setting in Setup (above {deg(lowest)}) would give some time"
            plan.notes.append("✖ " + why[0].upper() + why[1:])
            for t in enabled:
                self.didnt_fit(t, "no dark time tonight")
            self._keep_locked_without_sky("no dark time tonight")
            return plan
        self.dusk, self.dawn = info["dusk"], info["dawn"]
        # Baselines may run into nautical twilight (Sun ≤ −12°) when planning uses a darker limit.
        if site.sun_alt < sky.NAUTICAL and info["nautical_dusk"] is not None:
            self.base_lo, self.base_hi = info["nautical_dusk"], info["nautical_dawn"]
        else:
            self.base_lo, self.base_hi = self.dusk, self.dawn
        g0 = math.floor(min(self.base_lo, self.dusk) * 1440.0) / 1440.0
        g1 = math.ceil(max(self.base_hi, self.dawn) * 1440.0) / 1440.0
        n = int(round((g1 - g0) * 1440.0)) + 1
        self.grid = g0 + np.arange(n) * MIN
        focus = float(self.o["overhead_focus_min"]) * MIN
        self.flex_dark = (self.grid >= self.dusk + focus - EPS) & (self.grid + MIN <= self.dawn + EPS)
        self.moon_up = np.asarray(sky.moon_alt_at(site, self.grid), dtype=float) > 0.0
        self.dew = None
        if self.weather and self.weather.get("dew_onset"):
            self.dew = float(self.weather["dew_onset"])
            plan.notes.append(f"⚠ Dew expected from {self.hh(self.dew)}")

        by_id = {t.id: t for t in self.targets}
        self._place_locked(by_id)
        no_pos = [t for t in enabled if t.id not in self.done_ids and (t.ra is None or t.dec is None)]
        for t in no_pos:
            self.didnt_fit(t, "no position (RA/Dec missing)", with_next=False)
            self.done_ids.add(t.id)
        todo = [t for t in enabled if t.id not in self.done_ids]
        self._place_fixed([t for t in todo if _is_fixed(t)])
        self._place_flexible([t for t in todo if not _is_fixed(t)])
        self._finish()
        return plan

    def _keep_locked_without_sky(self, why: str):
        for b in self.locked:
            b = Block.from_dict(b.to_dict())
            text = f"⚠ {why[0].upper() + why[1:]}"
            if text not in b.cautions:
                b.cautions = list(b.cautions) + [text]
            self.plan.blocks.append(b.to_dict())
        self.plan.blocks.sort(key=lambda x: x["start"])

    # 1. locked ----------------------------------------------------------------------------------------------------
    def _place_locked(self, by_id):
        for b in sorted(self.locked, key=lambda x: x.start):
            b = Block.from_dict(b.to_dict())
            t = by_id.get(b.target_id)
            p = _P(tg=t, tid=b.target_id, name=b.name or (t.name if t else "?"), kind="locked", start=b.start,
                   end=b.end, block=b)
            if b.end <= b.start:
                p.cautions.append("⚠ Ends before it starts: check the times")
            if b.t1 is not None and b.t4 is not None:
                p.c_lo, p.c_hi = b.t1, b.t4
            for q in self.placed:
                if q.start < p.end and p.start < q.end:
                    p.cautions.append(f"⚠ Overlaps your block {q.name} ({self.hh(q.start)}–{self.hh(q.end)})")
            if t is None:
                p.cautions.append("⚠ This target is no longer in your list")
            elif t.ra is not None:
                jd = np.linspace(b.start, b.end, max(3, int(b.minutes() / 2) + 1))
                up = sky.up_mask(t.ra, t.dec, self.site, jd, _rule_min_alt(t))
                if not np.all(up):
                    bad = jd[~up]
                    p.cautions.append(f"⚠ Below your limits {self.hh(bad[0])}–{self.hh(bad[-1])}")
                if b.start < self.base_lo - EPS or b.end > self.base_hi + EPS:
                    p.cautions.append(f"⚠ Outside the darkness (dark {self.hh(self.dusk)}–{self.hh(self.dawn)})")
                for bo in events.blackouts(t, b.start, b.end):
                    p.cautions.append(f"⚠ Inside a blackout: {bo['reason']} {self.hh(bo['start'])}–"
                                      f"{self.hh(bo['end'])}")
            self.placed.append(p)
            # a Clouded-out / Partial block stays as a record, but its target is planned again tonight
            if b.locked or b.set_by_user or (b.status or "planned") not in ("clouded", "partial"):
                self.done_ids.add(b.target_id)

    # 2. fixed-time targets ----------------------------------------------------------------------------------------
    def _fixed_candidates(self, t: Target) -> tuple[list[_P], str]:
        """Every feasible window of this target tonight (best first), or [] and the reason."""
        j0, j1 = self.grid[0], self.grid[-1]
        o = self.o
        trim = float(o["baseline_trim_min"]) * MIN
        if t.kind == "transit":
            pl = t.planet_obj()
            if not pl or not pl.ok():
                return [], "no ephemeris (period, mid-time and duration are needed)"
            evs = [e for e in events.transit_events(pl, t.ra, t.dec, j0, j1)
                   if e["t4"] > self.base_lo and e["t1"] < self.base_hi]
            if not evs:
                return [], "no transit tonight"
            want = float(o["baseline_min"]) * MIN
        else:
            el = t.elements_obj()
            evs = [e for e in events.variable_events(el, t.ra, t.dec, j0, j1)]
            if not evs:
                return [], f"no timed {'minimum' if (el.epoch_kind or '') == 'min' else 'maximum'} tonight"
            want = float(el.event_halfwidth_min or 60.0) * MIN
        a = self.arr(t)
        up = a["up"]
        moon_lim = None
        for r in t.rules_obj():
            if r.kind == "min_moon_sep" and r.value is not None:
                moon_lim = float(r.value)
        out, reasons = [], []
        for e in evs:
            if t.kind == "transit":
                c_lo, c_hi = e["t1"], e["t4"]
                min_side = trim
                label = f"transit {self.hh(c_lo)}–{self.hh(c_hi)}"
            else:
                c_lo = c_hi = e["t"]
                min_side = want / 2.0
                label = f"{e['kind']} at {self.hh(c_lo)}"
            if c_lo < self.dusk - EPS or c_hi > self.dawn + EPS:
                reasons.append(f"{label} not all in the darkness ({self.hh(self.dusk)}–{self.hh(self.dawn)})")
                continue
            i_lo, i_hi = self.idx(c_lo), self.idx(c_hi)
            if not np.all(up[max(0, i_lo):i_hi + 1]):
                k = max(0, i_lo) + int(np.argmin(up[max(0, i_lo):i_hi + 1]))
                reasons.append(f"{label} below your limits (altitude {a['alt'][k]:.0f}° at {self.hh(self.grid[k])}; "
                               f"limit {a['lim'][k]:.0f}°)")
                continue
            if moon_lim is not None:
                sl = slice(max(0, i_lo), i_hi + 1)
                near = (a["moon"][sl] < moon_lim) & self.moon_up[sl]
                if np.any(near):
                    reasons.append(f"Moon too close during the {label} ({np.min(a['moon'][sl][near]):.0f}°; "
                                   f"your limit {moon_lim:g}°)")
                    continue
            blk = [b for b in events.blackouts(t, c_lo - min_side, c_hi + min_side)]
            if blk:
                reasons.append(f"blacked out ({blk[0]['reason']})")
                continue
            # the up-run around the core, inside the baseline twilight limits
            run = [r for r in _runs(up & (self.grid >= self.base_lo - EPS) & (self.grid <= self.base_hi + EPS))
                   if r[0] <= i_lo and r[1] > i_hi]
            r0, r1 = (self.grid[run[0][0]], self.grid[run[0][1] - 1]) if run else (c_lo, c_hi)
            lo = max(c_lo - want, r0, self.base_lo)
            hi = min(c_hi + want, r1, self.base_hi)
            p = _P(tg=t, tid=t.id, name=t.name, kind="transit" if t.kind == "transit" else "event",
                   start=lo, end=hi, c_lo=c_lo, c_hi=c_hi, min_pre=min_side, min_post=min_side, ev=e)
            if c_lo - lo < min_side - EPS or hi - c_hi < min_side - EPS:
                short = "before ingress" if c_lo - lo < min_side - EPS else "after egress"
                if t.kind != "transit":
                    short = "before the event" if c_lo - lo < min_side - EPS else "after the event"
                got = (c_lo - lo) if "before" in short else (hi - c_hi)
                why = "twilight" if (lo <= self.base_lo + EPS or hi >= self.base_hi - EPS) else "your limits"
                reasons.append(f"{label}: only {got * 1440:.0f} min {short} ({why}); needs ≥ "
                               f"{min_side * 1440:.0f}")
                continue
            if self.gem:
                self._meridian_adjust(p, up)
            out.append(p)
        if not out:
            return [], (reasons[0] if reasons else "no event tonight")
        # best first: lowest mean airmass over the core
        out.sort(key=lambda q: float(np.nanmean(a["x"][self.idx(q.c_lo):self.idx(q.c_hi) + 1])))
        return out, ""

    def _meridian_adjust(self, p: _P, up: np.ndarray):
        """GEM: keep a fixed window from starting just after the meridian and from flipping in a baseline when a
        small trim avoids it (the plan doc's K2-29 b: "stop by 03:38 at the latest")."""
        t = p.tg
        lead = float(self.o["flip_lead_min"]) * MIN
        settle = float(self.o["flip_settle_min"]) * MIN
        margin = float(self.site.past_meridian_min or 0.0) * MIN
        for m in self.meridians(t):
            # meridian in the block (after-egress baseline, or the transit itself with a wide margin): end inside
            # the tracking margin when egress + the minimum baseline still fits there — no flip at all
            if p.start < m < p.end and p.end - m > margin and p.c_hi + p.min_post <= m + margin + EPS:
                # a 2-minute safety margin when the baseline allows it, else right up to the margin
                p.end = max(m + margin - 2 * MIN, p.c_hi + p.min_post)
            # meridian in the before-ingress baseline: start flip_lead before it (pre-flip frames are dropped)
            if p.start < m < p.c_lo:
                if p.c_lo - (m + settle) >= p.min_pre - EPS:
                    s_new = m - lead
                    if s_new >= p.start:
                        p.start = s_new
                    elif m - p.start < FLIP_BEFORE_MIN * MIN:
                        # starting a few minutes before the meridian leaves no time to settle: start earlier
                        i, j = self.idx(s_new), self.idx(p.start)
                        if s_new >= self.base_lo - EPS and i >= 0 and np.all(up[i:j + 1]):
                            p.start = s_new
                else:
                    p.cautions.append(f"⚠ Meridian flip only {(p.c_lo - m) * 1440:.0f} min before the event "
                                      f"({self.hh(m)})")
            # start within an hour after the meridian: move it before the meridian, or later if that cannot be
            if 0.0 <= p.start - m < FLIP_AFTER_MIN * MIN:
                s_new = m - lead
                i = self.idx(s_new)
                j = self.idx(p.start)
                if s_new >= self.base_lo - EPS and i >= 0 and np.all(up[i:j + 1]):
                    p.start = s_new
                elif p.c_lo - (m + FLIP_AFTER_MIN * MIN) >= p.min_pre - EPS:
                    p.start = m + FLIP_AFTER_MIN * MIN
                else:
                    p.fallback_flip = True

    def _rank_key(self, t: Target, rare: dict) -> tuple:
        days = rare.get(t.id, 0)
        return (0 if t.id in self.prefer else 1, PRIORITY_RANK.get(t.priority, 1), -days, -_snr(t), t.name)

    def _place_fixed(self, fixed: list[Target]):
        cands = {}
        for t in fixed:
            lst, why = self._fixed_candidates(t)
            if not lst:
                self.didnt_fit(t, why)
                self.done_ids.add(t.id)
                continue
            cands[t.id] = (t, lst)
        # rarity only matters for targets whose windows touch another's
        rare = {}
        ids = list(cands)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if any(self._overlap(p, q) for p in cands[a][1] for q in cands[b][1]):
                    for x in (a, b):
                        if x not in rare:
                            nd = self.next_chance(cands[x][0])
                            rare[x] = (nd - self.d).days if nd else SEARCH_NIGHTS + 1
        order = sorted(cands.values(), key=lambda tl: self._rank_key(tl[0], rare))
        for t, lst in order:
            loser_of = None
            for c in lst:
                ok, blocker = self._try_fit(c)
                if ok:
                    self.placed.append(c)
                    break
                loser_of = loser_of or blocker
            else:
                if loser_of is not None and loser_of.kind == "locked":
                    why = f"collides with your locked block {loser_of.name}"
                else:
                    why = f"collides with {loser_of.name if loser_of else 'another block'}"
                self.didnt_fit(t, why)
            self.done_ids.add(t.id)

    def _overlap(self, p: _P, q: _P) -> bool:
        g = self.gap(p.tg, q.tg)
        return p.start < q.end + g and q.start < p.end + g

    @staticmethod
    def _split(space: float, a: float, b: float, min_a: float, min_b: float) -> tuple[float, float]:
        """Share `space` between two baselines that want a and b, keeping at least min_a and min_b."""
        if a + b <= space:
            return a, b
        x = min(a, max(space / 2.0, space - b))
        x = max(x, min_a)
        x = min(x, space - min_b)
        return x, min(b, space - x)

    def _bad_start_meridian(self, t: Target, s: float) -> float | None:
        """GEM: the meridian a start at s sits too close to (FLIP_BEFORE_MIN before … FLIP_AFTER_MIN after)."""
        if not self.gem or t is None:
            return None
        for m in self.meridians(t):
            if -FLIP_BEFORE_MIN * MIN < s - m < FLIP_AFTER_MIN * MIN:
                return m
        return None

    def _flip_start(self, q: _P, s: float, earliest: float) -> float:
        """A start for fixed block q (wanted: s; not before `earliest`) that obeys the flip rule: flip_lead
        before the meridian (pre-flip frames dropped, so the baseline after the flip must still be ≥ the minimum),
        else an hour after it; s unchanged when neither works."""
        m = self._bad_start_meridian(q.tg, s)
        if m is None:
            return s
        lead = float(self.o["flip_lead_min"]) * MIN
        settle = float(self.o["flip_settle_min"]) * MIN
        s1 = m - lead
        if s1 >= max(earliest, self.base_lo) - EPS and q.c_lo - (m + settle) >= q.min_pre - EPS:
            i, j = self.idx(s1), self.idx(s)
            if i >= 0 and np.all(self.arr(q.tg)["up"][i:j + 1]):
                return s1
        s2 = m + FLIP_AFTER_MIN * MIN
        if q.c_lo - s2 >= q.min_pre - EPS:
            return s2
        return s

    def _try_fit(self, c: _P) -> tuple[bool, _P | None]:
        """Fit candidate c among the placed blocks, trimming baselines (c's and other fixed blocks') toward their
        minimum. Commits trims only on success. Returns (ok, the block it collides with)."""
        new = {id(p): [p.start, p.end] for p in self.placed}
        cs, ce = c.start, c.end
        c_mid = 0.5 * (c.c_lo + c.c_hi)
        for p in sorted(self.placed, key=lambda q: q.start):
            ps, pe = new[id(p)]
            g_pc, g_cp = self.gap(p.tg, c.tg), self.gap(c.tg, p.tg)
            if pe + g_pc <= cs + EPS or ce + g_cp <= ps + EPS:
                continue
            p_mid = 0.5 * (p.c_lo + p.c_hi) if p.c_lo is not None else 0.5 * (ps + pe)
            if p_mid <= c_mid:                        # p before c
                p_fix_end = (p.c_hi + p.min_post) if p.trimmable else pe
                if p_fix_end + g_pc > c.c_lo - c.min_pre + EPS:
                    return False, p
                if p.trimmable:
                    space = c.c_lo - p.c_hi - g_pc
                    post, pre = self._split(space, pe - p.c_hi, c.c_lo - cs, p.min_post, c.min_pre)
                    s_new = c.c_lo - pre
                    s_flip = self._flip_start(c, s_new, p.c_hi + p.min_post + g_pc)
                    if s_flip != s_new:               # the later block obeys the flip rule; p gives way
                        cs = s_flip
                        new[id(p)][1] = min(pe, s_flip - g_pc)
                    else:
                        new[id(p)][1] = p.c_hi + post
                        cs = s_new
                else:
                    cs = max(cs, pe + g_pc)
                    cs = self._flip_start(c, cs, pe + g_pc)
            else:                                     # p after c
                p_fix_start = (p.c_lo - p.min_pre) if p.trimmable else ps
                if c.c_hi + c.min_post + g_cp > p_fix_start + EPS:
                    return False, p
                if p.trimmable:
                    space = p.c_lo - c.c_hi - g_cp
                    post, pre = self._split(space, ce - c.c_hi, p.c_lo - ps, c.min_post, p.min_pre)
                    s_new = p.c_lo - pre
                    s_flip = self._flip_start(p, s_new, c.c_hi + c.min_post + g_cp)
                    if s_flip != s_new:
                        new[id(p)][0] = s_flip
                        ce = min(ce, s_flip - g_cp)
                    else:
                        ce = c.c_hi + post
                        new[id(p)][0] = s_new
                else:
                    ce = min(ce, ps - g_cp)
            if ce - c.c_hi < c.min_post - EPS or c.c_lo - cs < c.min_pre - EPS:
                return False, p
        # final check: nothing overlaps after all the trims
        for p in self.placed:
            ps, pe = new[id(p)]
            if ps < ce + self.gap(c.tg, p.tg) - EPS and cs < pe + self.gap(p.tg, c.tg) - EPS:
                return False, p
        for p in self.placed:
            ps, pe = new[id(p)]
            if (ps, pe) != (p.start, p.end):
                p.start, p.end = ps, pe
                p.trimmed_by.append(c.name)
        if (cs, ce) != (c.start, c.end):
            others = [p.name for p in self.placed if p.start < ce + 30 * MIN and cs < p.end + 30 * MIN]
            c.trimmed_by.extend(others[:1] or ["another block"])
        c.start, c.end = cs, ce
        return True, None

    # 3. flexible targets ------------------------------------------------------------------------------------------
    def _week_count(self, t: Target) -> int:
        monday = self.d - timedelta(days=self.d.weekday())
        dates = set()
        for r in self.history:
            if r.get("target_id") != t.id:
                continue
            try:
                rd = date.fromisoformat(str(r.get("date") or ""))
            except (TypeError, ValueError):
                continue
            st = r.get("status")
            if monday <= rd < self.d and (st == "done" or (st == "planned" and r.get("scheduled"))):
                dates.add(rd)
        return len(dates)

    def _behind(self, t: Target) -> bool:
        """A campaign is behind its weekly goal (week = Mon–Sun, counting Done nights and nights planned earlier
        in this schedule) when it cannot reach the goal without tonight, or when it is behind an even pace:
        count < ceil(goal × (nights of the week so far, tonight included) / 7). So a goal of 1 is behind from
        Monday until it has a night, a goal of 2 from Monday until one night and from Thursday until two."""
        if t.kind != "campaign" or not t.goal_per_week:
            return False
        goal = int(t.goal_per_week)
        count = self._week_count(t)
        if count >= goal:
            return False
        remaining = 7 - self.d.weekday()            # tonight through Sunday night
        return goal - count >= remaining or count < math.ceil(goal * (self.d.weekday() + 1) / 7.0)

    def _goal_unmet(self, t: Target) -> bool:
        return t.kind == "campaign" and bool(t.goal_per_week) and self._week_count(t) < int(t.goal_per_week)

    def _flex_mask(self, t: Target) -> tuple[np.ndarray, str, list]:
        """Where t may be observed tonight (before occupancy), the reason when nowhere, and its blackouts."""
        a = self.arr(t)
        need = int(math.ceil(float(t.min_block_min or 45.0)))

        def longest(m):
            return max((r1 - r0 for r0, r1 in _runs(m)), default=0)

        m = self.flex_dark & a["up"]
        if longest(m) < need:
            up_min = int(np.sum(m))
            dark = self.flex_dark
            if up_min == 0:
                hi = float(np.max(a["alt"][dark])) if np.any(dark) else float("nan")
                k = int(np.argmax(np.where(dark, a["alt"], -99.0)))
                return m, (f"below your limits all night (highest {hi:.0f}°; limit {a['lim'][k]:.0f}° there)"), []
            return m, f"up only {longest(m)} min in a row in the dark (needs {need})", []
        for r in t.rules_obj():
            if r.kind == "min_moon_sep" and r.value is not None:
                m2 = m & ((a["moon"] >= float(r.value)) | ~self.moon_up)
                if longest(m2) < need:
                    best = float(np.max(a["moon"][m])) if np.any(m) else 0.0
                    return m2, f"Moon too close ({best:.0f}° at best; your limit {float(r.value):g}°)", []
                m = m2
            elif r.kind == "window":
                w = events.clock_window(r, self.d, self.tz)
                if w:
                    m2 = m & (self.grid >= w[0] - EPS) & (self.grid + MIN <= w[1] + EPS)
                    if longest(m2) < need:
                        return m2, f"not up long enough inside its window {r.value}", []
                    m = m2
        bos = events.blackouts(t, self.grid[0], self.grid[-1])
        if bos:
            m2 = m.copy()
            for bo in bos:
                m2 &= ~((self.grid + MIN > bo["start"] + EPS) & (self.grid < bo["end"] - EPS))
            if longest(m2) < need:
                bo = bos[0]
                return m2, (f"blacked out ({_short_planet(bo['reason'].replace(' transits', ''))} transit "
                            f"{self.hh(bo['start'])}–{self.hh(bo['end'])})"), bos
            m = m2
        if self.dew is not None and self.o.get("dew_sensitive_end"):
            m2 = m & (self.grid + MIN <= self.dew + EPS)
            if longest(m2) < need:
                return m2, f"dew expected from {self.hh(self.dew)}", bos
            m = m2
        return m, "", bos

    def _place_flexible(self, flex: list[Target]):
        info = []
        for t in flex:
            m, why, bos = self._flex_mask(t)
            if why:
                self.didnt_fit(t, why)
                self.done_ids.add(t.id)
                continue
            rank = PRIORITY_RANK.get(t.priority, 1)
            behind = self._behind(t)
            if behind:
                rank -= 1
            # same priority: a campaign with an unmet weekly goal goes before targets without one
            info.append(((0 if t.id in self.prefer else 1, rank, 0 if behind else 1,
                          0 if self._goal_unmet(t) else 1, int(np.sum(m)), t.name), t, m, bos, rank))
        info.sort(key=lambda x: x[0])
        missed = []
        for _key, t, m, bos, rank in info:
            p = self._place_one_flex(t, m)
            if p is None:
                missed.append((t, m, bos, rank))
            else:
                self._accept_flex(p, t, bos, rank)
            self.done_ids.add(t.id)
        # second pass: a target that got nothing may take time from same- or lower-priority flexible blocks that
        # are longer than their minimum (they shrink toward min_block_min)
        for t, m, bos, rank in missed:
            p = self._squeeze_for(t, m, rank)
            if p is not None:
                self._accept_flex(p, t, bos, rank)
                continue
            need = int(math.ceil(float(t.min_block_min or 45.0)))
            free = m & self._free_mask(t)
            longest = max((r1 - r0 for r0, r1 in _runs(free)), default=0)
            self.didnt_fit(t, f"no free stretch of {need} min left tonight (longest {longest} min)")

    def _accept_flex(self, p: _P, t: Target, bos: list, rank: int):
        if self._behind(t):
            p.notes.append(f"Behind its weekly goal ({self._week_count(t)} of {t.goal_per_week} so far): "
                           f"moved up one priority level")
        p.ev = {"blackouts": bos}
        p.rank = rank
        self.placed.append(p)

    def _squeeze_for(self, x: Target, mask: np.ndarray, rank: int) -> _P | None:
        """Shrink one placed flexible block (same or lower priority, longer than its minimum) so x fits."""
        x_need = float(x.min_block_min or 45.0)
        peers = [p for p in self.placed if p.kind == "flex" and getattr(p, "rank", 1) >= rank]
        peers.sort(key=lambda p: -(p.end - p.start))
        for p in peers:
            p_min = min(float(p.tg.min_block_min or 45.0), float(p.tg.block_min or 120.0)) * MIN
            if (p.end - p.start) - p_min < x_need * MIN - EPS:
                continue
            orig = (p.start, p.end)
            for var in ((orig[0], orig[0] + p_min), (orig[1] - p_min, orig[1])):
                if var[0] != orig[0] and self._bad_start_meridian(p.tg, var[0]) is not None:
                    continue
                p.start, p.end = var
                q = self._best_flex(x, mask)
                p.start, p.end = orig
                if q is None:
                    continue
                lo, hi = orig
                if q.start >= var[1]:
                    hi = min(hi, q.start - self.gap(p.tg, x))
                else:
                    lo = max(lo, q.end + self.gap(x, p.tg))
                if hi - lo < p_min - EPS:
                    continue
                if lo != orig[0] and self._bad_start_meridian(p.tg, lo) is not None:
                    continue
                p.start, p.end = lo, hi
                p.notes.append(f"Shortened to make room for {x.name}")
                return q
        return None

    def _free_mask(self, t: Target) -> np.ndarray:
        free = np.ones(len(self.grid), dtype=bool)
        for p in self.placed:
            g_before = self.gap(t, p.tg)     # t then p
            g_after = self.gap(p.tg, t)      # p then t
            free &= ~((self.grid > p.start - g_before - MIN + 1e-7) & (self.grid < p.end + g_after - 1e-7))
        return free

    def _ideal_start(self, t: Target) -> float:
        lead = float(self.o["flip_lead_min"])
        want = float(t.block_min or 120.0)
        mid = 0.5 * (self.dusk + self.dawn)
        m_ideal = min(self.meridians(t), key=lambda m: abs(m - mid))
        return m_ideal - (lead * MIN if self.gem else want / 2.0 * MIN)

    def _place_one_flex(self, t: Target, mask: np.ndarray) -> _P | None:
        """_best_flex, plus (GEM) the nudge: when an earlier flexible block's end keeps t from starting before its
        meridian, that block may end up to NUDGE_MAX_MIN early (never under its minimum) if that is cheaper
        overall (the lost minutes count at LOST_MIN_WEIGHT)."""
        p, cost = self._best_flex(t, mask, with_cost=True)
        if p is None or not self.gem:
            return p
        ideal = self._ideal_start(t)
        if p.start <= ideal + EPS:
            return p
        for a in sorted((q for q in self.placed if q.kind == "flex"), key=lambda q: q.end):
            g = self.gap(a.tg, t)
            if not (a.start < ideal < a.end + g + MIN):
                continue
            new_end = ideal - g
            a_min = min(float(a.tg.min_block_min or 45.0), float(a.tg.block_min or 120.0)) * MIN
            lost = (a.end - new_end) * 1440.0
            if lost < 0.5 or new_end - a.start < a_min - EPS or lost > NUDGE_MAX_MIN + 1e-6:
                continue
            old = a.end
            a.end = new_end
            q, c2 = self._best_flex(t, mask, with_cost=True)
            if q is not None and c2 + LOST_MIN_WEIGHT * lost < cost:
                a.notes.append(f"Ends {lost:.0f} min early so {t.name} can start before its meridian")
                a.dew_cut = False
                return q
            a.end = old
        return p

    def _best_flex(self, t: Target, mask: np.ndarray, with_cost: bool = False):
        ok = mask & self._free_mask(t)
        a = self.arr(t)
        want = int(round(float(t.block_min or 120.0)))
        need = int(math.ceil(float(t.min_block_min or 45.0)))
        need = min(need, want)
        lead = float(self.o["flip_lead_min"])
        settle = float(self.o["flip_settle_min"])
        margin = float(self.site.past_meridian_min or 0.0)
        mers = self.meridians(t)
        mid = 0.5 * (self.dusk + self.dawn)
        m_ideal = min(mers, key=lambda m: abs(m - mid))
        ideal = m_ideal - (lead * MIN if self.gem else want / 2.0 * MIN)
        xs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a["x"], nan=9.0))])
        g = self.grid
        cands, fallback = [], []
        for i0, i1 in _runs(ok):
            if i1 - i0 < need:
                continue
            for s in range(i0, i1 - need + 1):
                L = min(want, i1 - s)
                ts, te = g[s], g[s] + L * MIN
                usable = float(L)
                bad_start = False
                if self.gem:
                    for m in mers:
                        dm = (ts - m) * 1440.0
                        if -FLIP_BEFORE_MIN < dm < FLIP_AFTER_MIN:
                            bad_start = True
                        if ts < m < te and (te - m) * 1440.0 > margin:
                            usable -= settle
                meanx = (xs[s + L] - xs[s]) / L
                row = (s, L, usable, meanx)
                (fallback if bad_start else cands).append(row)
        pool = cands or fallback
        if not pool:
            return (None, None) if with_cost else None

        def cost(r):     # absolute, so costs of different free layouts compare (the nudge)
            return (abs(g[r[0]] - ideal) * 1440.0 + LOST_MIN_WEIGHT * (want - r[2]) + AIRMASS_WEIGHT * r[3]
                    + (1000.0 if not cands else 0.0))

        best = min(pool, key=lambda r: (cost(r), r[0]))
        s, L = best[0], best[1]
        p = _P(tg=t, tid=t.id, name=t.name, kind="flex", start=g[s], end=g[s] + L * MIN)
        p.fallback_flip = not cands
        if self.dew is not None and self.o.get("dew_sensitive_end") and abs(p.end - self.dew) <= 2 * MIN:
            p.dew_cut = True
        return (p, cost(best)) if with_cost else p

    # 4. finishing --------------------------------------------------------------------------------------------------
    def _finish(self):
        plan = self.plan
        exposures = []
        defocus_names = []
        blocks = []
        for p in sorted(self.placed, key=lambda q: q.start):
            b = self._make_block(p)
            blocks.append(b)
            if b.exposure_s:
                exposures.append(float(b.exposure_s))
            if _defocused(p.tg):
                defocus_names.append(p.name)
        plan.blocks = [b.to_dict() for b in blocks]
        # free gaps in the planning darkness
        minimum = float(self.o["min_gap_report_min"]) * MIN
        cur = self.dusk
        for b in blocks:
            if b.start - cur >= minimum and b.start > cur:
                plan.free.append((cur, min(b.start, self.dawn)))
            cur = max(cur, b.end)
        if self.dawn - cur >= minimum:
            plan.free.append((cur, self.dawn))
        plan.free = [(a, b) for a, b in plan.free if b - a >= minimum]
        if blocks:
            line = "Flats: one set at the night's camera angle; a meridian flip's 180° turn needs no new flats"
            if defocus_names:
                line += f"; flats at the defocus position too ({', '.join(dict.fromkeys(defocus_names))})"
            plan.flats.append(line)
            on_hand = {round(float(x), 3) for x in (self.o.get("darks_on_hand") or [])}
            need = sorted({e for e in exposures if round(e, 3) not in on_hand})
            if need:
                plan.darks.append("Darks to shoot: " + ", ".join(fmt_exposure(e) for e in need))
            elif exposures:
                plan.darks.append("Darks: all on hand (" + ", ".join(fmt_exposure(e)
                                                                    for e in sorted(set(exposures))) + ")")

    def _make_block(self, p: _P) -> Block:
        t, o, site = p.tg, self.o, self.site
        if p.kind == "locked":
            b = p.block
            b.cautions = [c for c in (b.cautions or []) if not c.startswith(("⚠", "✖"))]
            b.notes = []
        else:
            b = Block(target_id=t.id, name=t.name, mode=t.mode, kind=t.kind, fixed=p.kind in ("transit", "event"))
        b.start, b.end = float(p.start), float(p.end)
        cautions = list(b.cautions) + p.cautions
        notes: list[str] = []
        if t is None or t.ra is None:
            b.cautions = cautions
            b.notes = notes
            return b
        b.mode, b.kind = t.mode, t.kind
        b.ra, b.dec = t.ra, t.dec
        minutes = max(1e-6, (b.end - b.start) * 1440.0)
        jd = np.linspace(b.start, b.end, max(3, int(minutes / 2) + 1))
        alt = np.asarray(sky.target_alt(t.ra, t.dec, site, jd), dtype=float)
        x = _airmass(alt)
        b.alt_start, b.alt_end = float(alt[0]), float(alt[-1])
        b.alt_min, b.alt_max = float(alt.min()), float(alt.max())
        b.airmass_start, b.airmass_end = float(x[0]), float(x[-1])
        b.airmass_best = float(np.min(x))
        b.moon_sep = float(np.min(sky.target_moon_sep(t.ra, t.dec, jd)))
        mid = 0.5 * (b.start + b.end)
        m = self.meridian_near(t, mid)
        b.meridian = float(m)

        # the event itself
        base_i = None
        ev = p.ev if p.kind in ("transit", "event") else None
        if p.kind == "locked" and b.t1 is not None:
            ev = {"t1": b.t1, "tc": b.tc, "t4": b.t4, "sigma_min": b.tc_sigma_min}
        if t.kind == "transit" and ev and "t1" in ev:
            b.t1, b.tc, b.t4 = ev["t1"], ev["tc"], ev["t4"]
            b.tc_sigma_min = ev.get("sigma_min")
            s = f"Ingress {self.hh(b.t1)}, mid {self.hh(b.tc)}, egress {self.hh(b.t4)}"
            if b.tc_sigma_min is not None:
                s += f" (±{max(1, int(round(b.tc_sigma_min)))} min)"
            notes.append(s)
            base_i = len(notes)
            notes.append(f"Baseline {max(0.0, (b.t1 - b.start) * 1440):.0f} min before ingress, "
                         f"{max(0.0, (b.end - b.t4) * 1440):.0f} min after egress")
            if b.tc_sigma_min is not None and b.tc_sigma_min > float(o["tc_sigma_warn_min"]):
                cautions.append(f"⚠ Ephemeris uncertain: mid-time ± {b.tc_sigma_min:.0f} min")
        elif p.kind == "event" and ev:
            word = "Minimum" if ev.get("kind") == "min" else "Maximum"
            notes.append(f"{word} at {self.hh(ev['t'])}")
        if p.trimmed_by:
            cautions.append(f"⚠ Baseline trimmed (next to {', '.join(dict.fromkeys(p.trimmed_by))})")

        # meridian and flips
        flip = False
        flip_in_transit = False
        if self.gem:
            margin = float(site.past_meridian_min or 0.0)
            for mm in (m - SIDEREAL_DAY, m, m + SIDEREAL_DAY):
                if b.start < mm < b.end:
                    past = (b.end - mm) * 1440.0
                    if past <= margin + 1e-6:
                        stop = self.hh(mm + margin * MIN)
                        if past < 0.5:
                            notes.append(f"Ends at its meridian; stop by {stop} at the latest")
                        else:
                            notes.append(f"Ends {past:.0f} min past its meridian, inside your tracking margin; "
                                         f"stop by {stop} at the latest")
                    else:
                        flip = True
                        b.meridian = float(mm)
                        before = (mm - b.start) * 1440.0
                        if before <= float(o["flip_lead_min"]) + 10.0:
                            notes.append(f"Start before the meridian and let it flip (~{self.hh(mm)}); "
                                         f"drop the pre-flip frames")
                            if base_i is not None and mm < b.t1:
                                kept = max(0.0, (b.t1 - mm) * 1440 - float(o["flip_settle_min"]))
                                notes[base_i] = (f"Baseline {kept:.0f} min before ingress (after the flip), "
                                                 f"{max(0.0, (b.end - b.t4) * 1440):.0f} min after egress")
                        else:
                            notes.append(f"Meridian flip at ~{self.hh(mm)}; drop the frames during the flip")
                        if b.t1 is not None and b.t1 < mm < b.t4:
                            flip_in_transit = True
                            cautions.append(f"⚠ Meridian flip during the transit ({self.hh(mm)})")
                after = (b.start - mm) * 1440.0
                if 0.0 <= after < FLIP_AFTER_MIN and not (p.kind == "flex" and not p.fallback_flip):
                    cautions.append(f"⚠ Starts {after:.0f} min after its meridian: the mount may flip at once "
                                    f"(start before {self.hh(mm)} if you can)")
            ha = np.abs(np.asarray(sky.target_hour_angle(t.ra, t.dec, site, jd), dtype=float)) / 15.0
            max_ha = float(o.get("max_ha_h", 6.0) or 6.0)
            if np.any(ha > max_ha):
                k = int(np.argmax(ha > max_ha))
                cautions.append(f"⚠ Counterweight up beyond HA ±{max_ha:g} h ({self.hh(jd[k])})")
        elif b.alt_max > 85.0:
            k = int(np.argmax(alt))
            cautions.append(f"⚠ Zenith keyhole: {b.alt_max:.0f}° at {self.hh(jd[k])}")
        b.flip_in_block = flip
        usable = minutes - (float(o["flip_settle_min"]) if flip else 0.0)

        # kind-specific notes
        el = t.elements_obj()
        if t.kind == "variable" and el and el.any_time and el.period:
            cyc = usable / (float(el.period) * 1440.0)
            notes.append(f"About {cyc:.1f} cycles ({_period_text(float(el.period))})")
        if t.kind == "campaign" and t.goal_per_week:
            notes.append(f"Goal: {t.goal_per_week} night{'s' if t.goal_per_week != 1 else ''} a week")
        bos = (p.ev or {}).get("blackouts") if p.kind == "flex" else events.blackouts(t, self.dusk, self.dawn)
        for bo in bos or []:
            if bo["end"] < self.dusk or bo["start"] > self.dawn:
                continue
            who = _short_planet(bo["reason"].replace(" transits", ""))
            if bo["end"] >= self.dawn:
                notes.append(f"No {t.name} after {self.hh(bo['start'])} ({who} transit)")
            elif bo["start"] <= self.dusk:
                notes.append(f"No {t.name} before {self.hh(bo['end'])} ({who} transit)")
            else:
                notes.append(f"No {t.name} {self.hh(bo['start'])}–{self.hh(bo['end'])} ({who} transit)")
        notes.extend(p.notes)
        if t.notes:
            notes.append(t.notes.strip())
        if _defocused(t):
            notes.append("Refocus after this block")

        # exposure and frames
        pen = float(np.mean(sky.moon_sky_penalty(t.ra, t.dec, site, jd[:: max(1, len(jd) // 20)],
                                                 float(o.get("dark_sky_v", sky.DARK_SKY_V)))))
        ex = estimate_exposure(t, self.optics, o, airmass=b.airmass_best or 1.2, moon_penalty=pen)
        b.exposure_s = ex["exposure_s"]
        b.exposure_is_estimate = ex["is_estimate"]
        b.peak_adu = ex["peak_adu"]
        if ex["note"].startswith(("⚠", "✖")):
            cautions.append(ex["note"])
        per = float(b.exposure_s) + float(t.cadence_overhead_s or 0.0)
        b.frames = int(usable * 60.0 / per) if per > 0 else None
        if b.t1 is not None and b.t4 is not None and per > 0:
            ov = max(0.0, (min(b.t4, b.end) - max(b.t1, b.start)) * 1440.0)
            if flip_in_transit:
                ov = max(0.0, ov - float(o["flip_settle_min"]))
            b.frames_in_transit = int(ov * 60.0 / per)

        # weather
        if self.dew is not None:
            if p.kind == "flex" and p.dew_cut:
                cautions.append(f"⚠ Dew expected from {self.hh(self.dew)}: the block ends there")
            elif b.start < self.dew < b.end:
                cautions.append(f"⚠ Dew expected from {self.hh(self.dew)}")
        hours = (self.weather or {}).get("hours") or []
        inside = [h for h in hours if h.get("jd") is not None and b.start - 0.5 / 24 <= h["jd"] <= b.end]
        if inside:
            cloudy = [h for h in inside if (h.get("cloud") or 0) >= CLOUDY_PCT]
            if len(cloudy) > len(inside) / 2.0:
                cautions.append(f"⚠ Cloud ≥ {CLOUDY_PCT:.0f} % forecast for most of this block")
        b.notes = notes
        b.cautions = list(dict.fromkeys(cautions))
        return b


def build_night(d: date, site, optics, targets: list, options: dict, locked: list = (),
                weather: dict | None = None, history: list | None = None,
                prefer: set = frozenset()) -> NightPlan:
    """Plan the night that starts on local date d (see the module docstring for the rules)."""
    return _Night(d, site, optics, targets, options, locked, weather, history, prefer).build()


# ---- several nights --------------------------------------------------------------------------------------------

def _kept_blocks(night: NightPlan | None) -> list[Block]:
    if not night:
        return []
    return [b for b in night.blocks_obj() if b.locked or b.set_by_user or (b.status or "planned") != "planned"]


def build_schedule(start: date, n_nights: int, site, optics, targets, options, prior: Schedule | None = None,
                   weather_by_date: dict | None = None, prefer_by_date: dict | None = None) -> Schedule:
    """A rolling schedule of n_nights from `start`. Every locked / set-by-user block of `prior` (and every block
    already marked Done / Partial / Clouded) stays on its night with its times and status; the rest is rebuilt
    around it. Campaign goals count the history plus the nights planned earlier in this build."""
    n_nights = max(1, min(int(n_nights or 1), 31))
    prior_nights = {n.date: n for n in prior.nights_obj()} if prior else {}
    history = list(prior.history) if prior else []
    running: list[dict] = []
    nights = []
    for k in range(n_nights):
        d = start + timedelta(days=k)
        iso = d.isoformat()
        locked = _kept_blocks(prior_nights.get(iso))
        plan = build_night(d, site, optics, targets, options, locked=locked,
                           weather=(weather_by_date or {}).get(iso), history=history + running,
                           prefer=set((prefer_by_date or {}).get(iso, ())))
        for b in plan.blocks_obj():
            st = b.status or "planned"
            running.append({"date": iso, "target_id": b.target_id, "name": b.name, "status": st,
                            "minutes": round(b.minutes(), 1), "scheduled": True})
        nights.append(plan.to_dict())
    return Schedule(nights=nights, history=history, built=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def rereview(schedule: Schedule, today: date, site, optics, targets, options,
             weather_by_date: dict | None = None) -> Schedule:
    """Roll the schedule forward to `today`: nights before today go into the history (one record per block with
    its status and minutes), the schedule keeps its length (7 nights normally) from today on, and everything not
    locked/marked is rebuilt — so targets whose blocks were Clouded out or Partial are back in the pool, and a
    Clouded/Partial campaign night counts as missed for its weekly goal (only Done counts)."""
    nights = schedule.nights_obj()
    length = len(nights) or 7
    history = list(schedule.history)
    keep = []
    for n in nights:
        try:
            nd = date.fromisoformat(n.date)
        except ValueError:
            continue
        if nd < today:
            for b in n.blocks_obj():
                history.append({"date": n.date, "target_id": b.target_id, "name": b.name,
                                "status": b.status or "planned", "minutes": round(b.minutes(), 1)})
        else:
            keep.append(n.to_dict())
    prior = Schedule(nights=keep, history=history, built=schedule.built)
    return build_schedule(today, length, site, optics, targets, options, prior=prior,
                          weather_by_date=weather_by_date)
