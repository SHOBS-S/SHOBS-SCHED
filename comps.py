"""The stars whose peaks the exposure must protect, besides the target (1.4.1, GitHub issue #6, bound 1).

SCHED has no image, so it cannot check every field star. It checks the stars the user will actually measure:

  1. The target has a SHOBS-P series (Target.shobs_p_series, a .cvseries file): its comp and check stars, with the
     magnitudes the series uses. The file is READ ONLY: SCHED never writes to SHOBS-P's files.
  2. No series: the likely comp candidates, the APASS stars in the frame from the target's V down to
     CANDIDATE_BRIGHTER mag brighter than it, from the saved comp check (targets.comp_stars, the same cached APASS
     cone as the Edit dialog's Check comps). Brighter field stars are nobody's comps: a plain note when they would
     saturate, never a bound.
  3. Neither: target only, with a plain note saying why ("catalog unreachable" when the Build's comp step could not
     reach APASS; "not checked yet" when it was never asked).

No network here: measured_stars reads files and the cache only (the planner calls it for every block). prefetch()
is the network part; the Build runs it first, on its worker thread, inside the busy bar."""
from __future__ import annotations

import json
import math
import os
import re

CANDIDATE_BRIGHTER = 1.5       # comp candidates: up to this many magnitudes brighter than the target …
APASS_BRIGHT_V = 10.0          # APASS is incomplete (saturated) brighter than about this (Fable S2-4)
CANDIDATE_FAINTER = 1.0        # … and down to this much fainter (targets.COMP_DV: the comps the comp check counts)
TARGET_MATCH_ARCSEC = 6.0      # the APASS star at the target's own place (not a comp)
MAX_COMPS = 10                 # SHOBS-P's comp slots: comp, comp2 … comp10
PREFETCH_GIVE_UP = 2           # the Build's comp step stops asking after this many targets in a row got no answer
PREFETCH_TIMEOUT_S = 8.0       # … waits at most this long for each request (Fable S2-1) …
PREFETCH_DEADLINE_S = 20.0     # … and this long in all, then leaves the rest for the next Build …
PREFETCH_DOWN_S = 600.0        # … and after a step where the catalog never answered, the next Builds skip it this long
_DOWN = {"at": None}

_SERIES_CACHE: dict = {}       # path → (mtime, size, result)
_UNREACHABLE: set = set()      # comp-check cache keys the last Build could not fetch


def _key(name) -> str:
    return re.sub(r"[\s_\-]+", "", str(name or "")).lower()


def _mag(v):
    try:
        x = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and -5.0 < x < 25.0 else None


def read_series(path: str) -> dict:
    """{"stars": [{"name", "role" ("Comp 1" … "Check"), "v"}], "star_id", "error"} from a SHOBS-P series file.
    Comps: every comp slot with a name and a magnitude, narrowed to the series' active comps when it lists them;
    the check star when it has a magnitude. error is plain words when the file could not be read (never raises)."""
    out = {"stars": [], "star_id": "", "error": ""}
    p = str(path or "").strip()
    if not p:
        out["error"] = "no series file"
        return out
    try:
        st = os.stat(p)
    except OSError:
        out["error"] = f"{os.path.basename(p)} not found"
        return out
    hit = _SERIES_CACHE.get(p)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        with open(p, encoding="utf-8") as fh:
            meta = (json.load(fh) or {}).get("meta") or {}
        if not isinstance(meta, dict):
            raise ValueError("no series header")
    except Exception as exc:
        out["error"] = f"{os.path.basename(p)} could not be read ({type(exc).__name__})"
        return out
    out["star_id"] = str(meta.get("star_id") or "")
    roles = ["comp"] + [f"comp{i}" for i in range(2, MAX_COMPS + 1)]
    comps = []
    for i, r in enumerate(roles, 1):
        name, v = str(meta.get(f"{r}_name") or "").strip(), _mag(meta.get(f"{r}_mag"))
        if v is not None:                              # SHOBS-P allows a comp with no name (Fable S3)
            comps.append({"name": name or f"comp{i if i > 1 else ''}", "role": f"Comp {i}", "v": v})
    active = {_key(n) for n in (meta.get("active_comps") or meta.get("comps_used") or []) if str(n).strip()}
    if active:
        used = [c for c in comps if _key(c["name"]) in active]
        comps = used or comps
    stars = list(comps)
    cname, cv = str(meta.get("check_name") or "").strip(), _mag(meta.get("check_mag"))
    if cv is not None:
        stars.append({"name": cname or "check", "role": "Check", "v": cv})
    out["stars"] = stars
    if not stars:
        out["error"] = f"{os.path.basename(p)} names no comp or check star with a magnitude"
    _SERIES_CACHE[p] = (st.st_mtime, st.st_size, out)
    return out


def _photometry_app() -> str:
    """"SHOBS-P" where it is installed, else plain words (SCHED stands alone: round 4)."""
    try:
        import settings
        return "SHOBS-P" if os.path.exists(settings.shobs_p_settings_path()) else "your photometry software"
    except Exception:
        return "your photometry software"


def _sep_arcsec(r: dict, ra: float, dec: float) -> float:
    cosd = math.cos(math.radians(float(dec)))
    dra = (float(r["ra"]) - float(ra) + 180.0) % 360.0 - 180.0        # across RA 0/360 (Fable S3)
    return math.hypot(dra * cosd, float(r["dec"]) - float(dec)) * 3600.0


def catalog_split(rows: list, ra: float, dec: float, target_v: float) -> tuple[list, list]:
    """(candidates, bright field stars) from APASS rows {ra, dec, v}: the target itself left out; candidates from
    CANDIDATE_BRIGHTER brighter to CANDIDATE_FAINTER fainter than the target; brighter stars are field stars."""
    cands, bright = [], []
    for r in rows or []:
        v = _mag(r.get("v"))
        if v is None or r.get("ra") is None or r.get("dec") is None:
            continue
        sep = _sep_arcsec(r, ra, dec)
        if sep <= TARGET_MATCH_ARCSEC:
            continue
        star = {"name": f"APASS V {v:.2f} ({sep / 60.0:.1f}′ away)", "v": v, "sep_arcsec": sep}
        if v < float(target_v) - CANDIDATE_BRIGHTER:
            bright.append(dict(star, role="Field star"))
        elif v <= float(target_v) + CANDIDATE_FAINTER:
            cands.append(dict(star, role="Comp candidate"))
    cands.sort(key=lambda s: s["v"])
    bright.sort(key=lambda s: s["v"])
    return cands, bright


def _cone(t, optics):
    import targets as tg
    rad = tg.comp_radius_arcmin(optics)
    return tg, rad, tg._comp_key(float(t.ra), float(t.dec), rad)


def measured_stars(t, optics) -> dict:
    """The stars bound 1 protects besides the target (see the module text; files and cache only, never the network):
    {source: "series" | "catalog" | "none", stars: [...], field: [...], candidates: [...], note}. note is a plain
    sentence when the check is incomplete ("" when all is well)."""
    out = {"source": "none", "stars": [], "field": [], "candidates": [], "note": ""}
    series = str(getattr(t, "shobs_p_series", "") or "").strip()
    series_err = ""
    if series:
        got = read_series(series)
        if got["stars"]:
            out.update(source="series", stars=got["stars"])
            return out
        series_err = f"SHOBS-P series not used: {got['error']}"
    if t.vmag is None or t.ra is None or t.dec is None:
        out["note"] = series_err or ("comps not checked: no V magnitude" if t.vmag is None
                                     else "comps not checked: no position")
        return out
    try:
        tg, rad, key = _cone(t, optics)
        data = tg.saved_comp_stars(t.ra, t.dec, rad)
    except Exception:
        key, data = None, None
    if data:
        cands, bright = catalog_split(data.get("rows") or [], t.ra, t.dec, t.vmag)
        out.update(source="catalog", stars=cands, field=bright, candidates=cands)
        notes = [series_err + "; checked APASS comp candidates instead"] if series_err else []
        if float(t.vmag) - CANDIDATE_BRIGHTER < APASS_BRIGHT_V:
            notes.append(f"comp candidates brighter than V {APASS_BRIGHT_V:g} may be missing (APASS is incomplete "
                         f"there); check comp peaks in {_photometry_app()}")
        out["note"] = "; ".join(notes)
        return out
    if key is not None and key in _UNREACHABLE:
        why = f"comps not checked: catalog unreachable; check comp peaks in {_photometry_app()}"
    else:
        why = "comps not checked yet: Build schedule (or Check comps in Edit) asks APASS"
    out["note"] = (series_err + "; " + why) if series_err else why
    return out


def prefetch(targets, optics, step=None, tg=None) -> dict:
    """The Build's comp step (network; on the worker thread): ask APASS once for every ticked target that has no
    readable SHOBS-P series and no saved comp check. A failure is remembered (measured_stars then says "catalog
    unreachable"); after PREFETCH_GIVE_UP failures in a row the rest are not asked this time (CDS and its mirrors
    are down: no point waiting out each timeout). {"asked", "got", "failed"}. tg: the targets module to ask (the
    app passes its own; a copy without the comp check is not asked)."""
    if tg is None:
        import targets as tg
    if not all(hasattr(tg, n) for n in ("comp_stars", "saved_comp_stars", "comp_radius_arcmin", "_comp_key")):
        return {"asked": 0, "got": 0, "failed": 0}
    want = []
    for t in targets:
        if not getattr(t, "enabled", True) or t.vmag is None or t.ra is None or t.dec is None:
            continue
        if str(getattr(t, "shobs_p_series", "") or "").strip() and read_series(t.shobs_p_series)["stars"]:
            continue
        rad = tg.comp_radius_arcmin(optics)
        key = tg._comp_key(float(t.ra), float(t.dec), rad)
        if tg.saved_comp_stars(t.ra, t.dec, rad):
            _UNREACHABLE.discard(key)
            continue
        want.append((t, rad, key))
    res = {"asked": 0, "got": 0, "failed": 0}
    import time as _time
    now = _time.monotonic()
    if _DOWN["at"] is not None and now - _DOWN["at"] < PREFETCH_DOWN_S:
        for _t, _r, key in want:                      # it did not answer a few minutes ago: don't wait again
            _UNREACHABLE.add(key)
        res["failed"] = len(want)
        return res
    quick = getattr(tg, "quick_timeouts", None)
    misses = 0
    t0 = now
    for i, (t, rad, key) in enumerate(want, 1):
        if misses >= PREFETCH_GIVE_UP or _time.monotonic() - t0 > PREFETCH_DEADLINE_S:
            _UNREACHABLE.add(key)
            res["failed"] += 1
            continue
        if step is not None:
            step(f"Checking comp stars for {t.name} ({i} of {len(want)})")
        res["asked"] += 1
        try:
            if quick is not None:
                with quick(PREFETCH_TIMEOUT_S):
                    tg.comp_stars(t.ra, t.dec, rad, t.vmag)
            else:
                tg.comp_stars(t.ra, t.dec, rad, t.vmag)
            _UNREACHABLE.discard(key)
            res["got"] += 1
            misses = 0
        except Exception as exc:
            _UNREACHABLE.add(key)
            res["failed"] += 1
            misses += 1
            try:
                import settings
                settings.log(f"comp check for {t.name}: {exc}")
            except Exception:
                pass
    if res["asked"] and not res["got"]:
        _DOWN["at"] = _time.monotonic()
    elif res["got"]:
        _DOWN["at"] = None
    return res
