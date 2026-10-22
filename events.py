"""Timed events from ephemerides: planet transits, variable-star maxima/minima, and campaign blackouts.

All results are in JD (UTC). Ephemerides arrive in BJD_TDB (planets) or HJD/BJD (variables); the barycentric
correction is removed with sky.bjd_minus_jd_days (good to seconds)."""
from __future__ import annotations

import math

import sky
from models import Planet, Rule, Target, VarElements


def transit_events(planet: Planet, ra: float, dec: float, jd0: float, jd1: float) -> list[dict]:
    """Every transit whose mid-time falls in [jd0 - t14, jd1 + t14]. Each: epoch, tc, t1, t4 (JD UTC),
    tc_bjd, sigma_min (the predicted Tc uncertainty at that epoch from the ephemeris errors, minutes)."""
    if not planet or not planet.ok():
        return []
    p, t0, t14 = float(planet.period), float(planet.t0), float(planet.t14)
    corr = float(sky.bjd_minus_jd_days(0.5 * (jd0 + jd1), ra, dec))
    n0 = math.ceil((jd0 - t14 + corr - t0) / p)
    n1 = math.floor((jd1 + t14 + corr - t0) / p)
    out = []
    for n in range(n0, n1 + 1):
        tc_bjd = t0 + n * p
        tc = tc_bjd - float(sky.bjd_minus_jd_days(tc_bjd, ra, dec))
        sig = math.hypot(planet.t0_err or 0.0, abs(n) * (planet.period_err or 0.0)) * 1440.0
        out.append({"epoch": n, "tc": tc, "t1": tc - t14 / 2, "t4": tc + t14 / 2, "tc_bjd": tc_bjd,
                    "sigma_min": sig if (planet.t0_err or planet.period_err) else None})
    return out


def variable_events(el: VarElements, ra: float, dec: float, jd0: float, jd1: float) -> list[dict]:
    """Timed maxima (or minima, for eclipsing binaries) inside [jd0, jd1]. Each: t (JD UTC), kind ("max"/"min"),
    start/end = t ∓ event_halfwidth_min. Empty for any-time pulsators or missing elements."""
    if not el or el.any_time or not el.period or not el.epoch:
        return []
    p, e = float(el.period), float(el.epoch)
    corr = float(sky.bjd_minus_jd_days(0.5 * (jd0 + jd1), ra, dec))
    half = float(el.event_halfwidth_min or 60.0) / 1440.0
    n0 = math.ceil((jd0 + corr - e) / p)
    n1 = math.floor((jd1 + corr - e) / p)
    out = []
    for n in range(n0 - 1, n1 + 2):            # one spare cycle each side: the range used a mid-range correction
        t_b = e + n * p
        t = t_b - float(sky.bjd_minus_jd_days(t_b, ra, dec))   # barycentric correction at this event
        if not (jd0 <= t <= jd1):
            continue
        out.append({"cycle": n, "t": t, "kind": el.epoch_kind or "max", "start": t - half, "end": t + half})
    return out


def blackouts(target: Target, jd0: float, jd1: float) -> list[dict]:
    """Times the target must NOT be observed, from its "no_transit" rules: each planet transit (t1 − pad to
    t4 + pad) overlapping [jd0, jd1]. Each: start, end, reason ("planet c transits 04:28–05:53")."""
    out = []
    for rule in target.rules_obj():
        if rule.kind != "no_transit" or not rule.planet:
            continue
        pl = Planet.from_dict(rule.planet)
        if not pl.ok() or target.ra is None:
            continue
        pad = float(rule.pad_min or 0.0) / 1440.0
        for ev in transit_events(pl, target.ra, target.dec, jd0, jd1):
            s, e = ev["t1"] - pad, ev["t4"] + pad
            if e > jd0 and s < jd1:
                label = pl.name or "planet"
                out.append({"start": s, "end": e, "reason": f"{label} transits", "tc": ev["tc"],
                            "sigma_min": ev["sigma_min"]})
    return out


def clock_window(rule: Rule, night_date, tz: str) -> tuple[float, float] | None:
    """A "window" rule ("HH:MM-HH:MM" site clock) as JD on the night that starts on night_date. The end wraps to
    the next morning when it is earlier than the start."""
    from datetime import timedelta
    try:
        a, b = str(rule.value).replace("–", "-").split("-")
        ah, am = map(int, a.strip().split(":"))
        bh, bm = map(int, b.strip().split(":"))
    except Exception:
        return None
    da = night_date if ah >= 12 else night_date + timedelta(days=1)
    db = night_date if bh >= 12 else night_date + timedelta(days=1)
    s, e = sky.local_jd(da, ah, am, tz), sky.local_jd(db, bh, bm, tz)
    if e <= s:
        e += 1.0
    return s, e
