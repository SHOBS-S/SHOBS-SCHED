"""SHOBS-SCHED sky arithmetic: Sun, Moon, twilight, altitude, azimuth, airmass, meridian times, horizon profile,
and the Moon's added sky brightness.

Started from SHOBS-P's transit_core.py planner functions (sun_radec, moon_radec, altitude, night_window, ...), copied
here so SCHED never needs SHOBS-P at run time. Two changes from that copy:
  * "local" always means the SITE's time zone (zoneinfo; the tzdata package supplies the zones on Windows), never
    the computer's, so a remote or overseas site plans correctly (SHOBS-P issue #22 is the same gap there);
  * the old "avoid the meridian" option is gone (the flip rule lives in planner.py).
Low-precision formulas: Sun ~0.01°, Moon ~0.3°, times good to a minute or two — what planning needs."""
from __future__ import annotations

import math
from datetime import date, datetime, time as dtime, timedelta, timezone

import numpy as np

JD_UNIX_EPOCH = 2440587.5
SUN_RISE_ALT = -0.833          # apparent upper limb on the horizon (refraction + semi-diameter)
MOON_RISE_ALT = 0.125          # geocentric centre altitude at moonrise/set (parallax − refraction − semi-diameter)
CIVIL, NAUTICAL, ASTRONOMICAL = -6.0, -12.0, -18.0
AIRMASS_CAP = 8.0              # as SHOBS-P: beyond this the number is meaningless


# ---- time ----------------------------------------------------------------------------------------------------------

def zone(tz_name: str):
    """The site's tzinfo. Unknown or empty name → UTC (the caller shows a caution)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        return timezone.utc


def zone_ok(tz_name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz_name)
        return bool(tz_name)
    except Exception:
        return False


def jd_from_datetime(dt: datetime) -> float:
    """A timezone-aware datetime → JD (UTC). A naive datetime is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0 + JD_UNIX_EPOCH


def to_local(jd: float, tz_name: str) -> datetime:
    """JD (UTC) → aware datetime on the site's clock (daylight saving included)."""
    return datetime.fromtimestamp((float(jd) - JD_UNIX_EPOCH) * 86400.0, tz=timezone.utc).astimezone(zone(tz_name))


def to_utc(jd: float) -> datetime:
    return datetime.fromtimestamp((float(jd) - JD_UNIX_EPOCH) * 86400.0, tz=timezone.utc)


def hhmm(jd: float | None, tz_name: str) -> str:
    """'21:32' on the site's clock; '—' for None. Rounded to the nearest minute."""
    if jd is None or not math.isfinite(jd):
        return "—"
    t = to_local(float(jd) + 0.5 / 1440.0, tz_name)
    return t.strftime("%H:%M")


def local_jd(d: date, hh: int, mm: int, tz_name: str) -> float:
    """JD (UTC) of a site clock time on a local date. In a daylight-saving gap/overlap, zoneinfo's fold=0 applies."""
    return jd_from_datetime(datetime.combine(d, dtime(hh, mm), tzinfo=zone(tz_name)))


def local_noon_jd(d: date, tz_name: str) -> float:
    return local_jd(d, 12, 0, tz_name)


def now_jd() -> float:
    return datetime.now(tz=timezone.utc).timestamp() / 86400.0 + JD_UNIX_EPOCH


def night_date_of(jd: float, tz_name: str) -> date:
    """The local date a night STARTS on: before local noon belongs to the previous evening's night."""
    t = to_local(jd, tz_name)
    return (t - timedelta(hours=12)).date()


# ---- Sun, Moon, coordinates -----------------------------------------------------------------------------------------

def _gmst_deg(jd):
    d = np.asarray(jd, dtype=float) - 2451545.0
    return (280.46061837 + 360.98564736629 * d) % 360.0


def lst_deg(jd, lon_deg: float):
    return (_gmst_deg(jd) + lon_deg) % 360.0


def sun_radec(jd):
    """Low-precision Sun (Astronomical Almanac): RA, Dec in degrees, distance in au."""
    n = np.asarray(jd, dtype=float) - 2451545.0
    L = np.radians((280.460 + 0.9856474 * n) % 360)
    g = np.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 4e-7 * n)
    ra = np.degrees(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))) % 360
    dec = np.degrees(np.arcsin(np.sin(eps) * np.sin(lam)))
    dist = 1.00014 - 0.01671 * np.cos(g) - 0.00014 * np.cos(2 * g)
    return ra, dec, dist


def moon_radec(jd):
    """Low-precision Moon (about 0.3 degrees): RA, Dec in degrees, and illuminated fraction."""
    d = np.asarray(jd, dtype=float) - 2451545.0
    L = np.radians((218.316 + 13.176396 * d) % 360)
    M = np.radians((134.963 + 13.064993 * d) % 360)
    F = np.radians((93.272 + 13.229350 * d) % 360)
    lam = L + np.radians(6.289) * np.sin(M)
    beta = np.radians(5.128) * np.sin(F)
    eps = np.radians(23.439)
    ra = np.degrees(np.arctan2(np.sin(lam) * np.cos(eps) - np.tan(beta) * np.sin(eps), np.cos(lam))) % 360
    dec = np.degrees(np.arcsin(np.sin(beta) * np.cos(eps) + np.cos(beta) * np.sin(eps) * np.sin(lam)))
    sra, sdec, _ = sun_radec(jd)
    elong = sep_deg(ra, dec, sra, sdec)
    illum = (1 - np.cos(np.radians(elong))) / 2
    return ra, dec, illum


def moon_phase_angle(jd) -> np.ndarray:
    """Phase angle α in degrees (0 = full, 180 = new), from the elongation."""
    ra, dec, _ = moon_radec(jd)
    sra, sdec, _ = sun_radec(jd)
    return 180.0 - sep_deg(ra, dec, sra, sdec)


def sep_deg(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def hour_angle_deg(ra_deg, lon_deg: float, jd):
    """Hour angle in degrees, wrapped to [-180, 180): negative = east of the meridian (rising)."""
    return ((lst_deg(jd, lon_deg) - np.asarray(ra_deg) + 180.0) % 360.0) - 180.0


def altitude(ra_deg, dec_deg, lat_deg, lon_deg, jd):
    ha = np.radians(hour_angle_deg(ra_deg, lon_deg, jd))
    lat, dec = math.radians(lat_deg), np.radians(dec_deg)
    s = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def azimuth(ra_deg, dec_deg, lat_deg, lon_deg, jd):
    """Azimuth in degrees from true north through east (0..360)."""
    ha = np.radians(hour_angle_deg(ra_deg, lon_deg, jd))
    lat, dec = math.radians(lat_deg), np.radians(dec_deg)
    y = -np.sin(ha) * np.cos(dec)
    x = np.sin(dec) * np.cos(lat) - np.cos(dec) * np.sin(lat) * np.cos(ha)
    return np.degrees(np.arctan2(y, x)) % 360.0


def airmass_from_alt(alt_deg):
    """sec z, as SHOBS-P's light curves report it (no refraction); NaN at or below the horizon, capped at 8."""
    alt = np.asarray(alt_deg, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = 1.0 / np.sin(np.radians(alt))
    x = np.where((alt > 0) & (x <= AIRMASS_CAP), x, np.nan)
    return x if x.ndim else float(x)


def alt_for_airmass(x: float) -> float:
    return math.degrees(math.asin(1.0 / max(1.0, float(x))))


def bjd_minus_jd_days(jd, ra_deg, dec_deg):
    """Approximate BJD_TDB − JD_UTC (days): Roemer delay plus TDB − UTC. Good to a few seconds."""
    sra, sdec, dist = sun_radec(jd)
    ex = -dist * np.cos(np.radians(sdec)) * np.cos(np.radians(sra))
    ey = -dist * np.cos(np.radians(sdec)) * np.sin(np.radians(sra))
    ez = -dist * np.sin(np.radians(sdec))
    nx = math.cos(math.radians(dec_deg)) * math.cos(math.radians(ra_deg))
    ny = math.cos(math.radians(dec_deg)) * math.sin(math.radians(ra_deg))
    nz = math.sin(math.radians(dec_deg))
    return ((ex * nx + ey * ny + ez * nz) * 499.004784 + 69.184) / 86400.0


def meridian_jd(ra_deg: float, lon_deg: float, jd_near: float) -> float:
    """JD of the upper meridian crossing (hour angle 0) nearest jd_near (within half a sidereal day).
    ra_deg is taken as of date; for a catalog (J2000) position use target_meridian()."""
    ha = float(hour_angle_deg(ra_deg, lon_deg, jd_near))
    return float(jd_near - ha / 360.0 * 0.99726957)


def precess(ra_deg, dec_deg, jd):
    """J2000 → mean equator and equinox of date (IAU 1976 precession). 26 years moves a star's meridian time by
    about a minute, so target positions go through this before altitude and meridian work."""
    t = (np.asarray(jd, dtype=float) - 2451545.0) / 36525.0
    zeta = np.radians((2306.2181 * t + 0.30188 * t ** 2 + 0.017998 * t ** 3) / 3600.0)
    z = np.radians((2306.2181 * t + 1.09468 * t ** 2 + 0.018203 * t ** 3) / 3600.0)
    theta = np.radians((2004.3109 * t - 0.42665 * t ** 2 - 0.041833 * t ** 3) / 3600.0)
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    a = np.cos(dec) * np.sin(ra + zeta)
    b = np.cos(theta) * np.cos(dec) * np.cos(ra + zeta) - np.sin(theta) * np.sin(dec)
    c = np.sin(theta) * np.cos(dec) * np.cos(ra + zeta) + np.cos(theta) * np.sin(dec)
    ra2 = (np.degrees(np.arctan2(a, b) + z)) % 360.0
    dec2 = np.degrees(np.arcsin(np.clip(c, -1, 1)))
    return ra2, dec2


def target_alt(ra_deg, dec_deg, site, jd):
    """Altitude of a catalog (J2000) position, precessed to the date."""
    r, d = precess(ra_deg, dec_deg, jd)
    return altitude(r, d, site.lat, site.lon, jd)


def target_az(ra_deg, dec_deg, site, jd):
    r, d = precess(ra_deg, dec_deg, jd)
    return azimuth(r, d, site.lat, site.lon, jd)


def target_airmass(ra_deg, dec_deg, site, jd):
    return airmass_from_alt(target_alt(ra_deg, dec_deg, site, jd))


def target_meridian(ra_deg: float, dec_deg: float, site, jd_near: float) -> float:
    """Upper meridian crossing nearest jd_near for a catalog (J2000) position."""
    r, _d = precess(ra_deg, dec_deg, jd_near)
    return meridian_jd(float(r), site.lon, jd_near)


def target_moon_sep(ra_deg, dec_deg, jd):
    """Moon–target distance in degrees (geocentric Moon; fine for planning)."""
    r, d = precess(ra_deg, dec_deg, jd)
    mra, mdec, _ = moon_radec(jd)
    return sep_deg(mra, mdec, r, d)


def target_hour_angle(ra_deg, dec_deg, site, jd):
    r, _d = precess(ra_deg, dec_deg, jd)
    return hour_angle_deg(r, site.lon, jd)


def culmination_alt(dec_deg: float, lat_deg: float) -> float:
    return 90.0 - abs(lat_deg - dec_deg)


# ---- horizon -------------------------------------------------------------------------------------------------------

def horizon_alt(az_deg, site) -> np.ndarray | float:
    """The surveyed horizon's altitude at these true azimuths (linear between survey points, wrapping at 360°).
    A magnetic survey is turned to true north with the site's declination. Flat (0°) when there is no survey."""
    pts = list(site.horizon or [])
    az = np.asarray(az_deg, dtype=float)
    if not pts:
        out = np.zeros_like(az)
        return out if out.ndim else float(out)
    shift = float(site.magnetic_declination or 0.0) if (site.horizon_north or "true") == "magnetic" else 0.0
    a = np.array([(p[0] + shift) % 360.0 for p in pts])
    h = np.array([float(p[1]) for p in pts])
    order = np.argsort(a)
    a, h = a[order], h[order]
    a_ext = np.concatenate([a[-1:] - 360.0, a, a[:1] + 360.0])
    h_ext = np.concatenate([h[-1:], h, h[:1]])
    out = np.interp(az % 360.0, a_ext, h_ext)
    return out if np.ndim(out) else float(out)


def limit_alt(ra_deg, dec_deg, site, jd, min_alt: float | None = None) -> np.ndarray:
    """The altitude a catalog (J2000) target must exceed at each time: max(minimum altitude, the horizon at its
    azimuth, the max-airmass altitude). min_alt overrides site.min_alt (a campaign's own limit)."""
    az = target_az(ra_deg, dec_deg, site, jd)
    lo = float(site.min_alt or 0.0) if min_alt is None else float(min_alt)
    floor = max(lo, alt_for_airmass(site.max_airmass) if site.max_airmass else 0.0)
    return np.maximum(horizon_alt(az, site), floor)


def up_mask(ra_deg, dec_deg, site, jd, min_alt: float | None = None) -> np.ndarray:
    """True where a catalog (J2000) target is above every limit (see limit_alt)."""
    jd = np.asarray(jd, dtype=float)
    return target_alt(ra_deg, dec_deg, site, jd) >= limit_alt(ra_deg, dec_deg, site, jd, min_alt)


# ---- events: crossings, twilight, rise/set -------------------------------------------------------------------------

def _crossings(f, jd0: float, jd1: float, level: float, step_min: float = 5.0) -> list[tuple[float, int]]:
    """Times where f(jd) crosses `level` between jd0 and jd1, refined by bisection to ~2 s.
    Returns [(jd, +1 rising | -1 setting), ...]."""
    n = max(2, int(math.ceil((jd1 - jd0) * 1440.0 / step_min)) + 1)
    grid = np.linspace(jd0, jd1, n)
    v = np.asarray(f(grid), dtype=float) - level
    out = []
    for i in range(n - 1):
        if not (np.isfinite(v[i]) and np.isfinite(v[i + 1])):
            continue
        if v[i] == 0.0:
            out.append((float(grid[i]), 1 if v[i + 1] > 0 else -1))
            continue
        if v[i] * v[i + 1] < 0:
            a, b, fa = grid[i], grid[i + 1], v[i]
            for _ in range(30):
                m = 0.5 * (a + b)
                fm = float(f(np.array([m]))[0]) - level
                if fa * fm <= 0:
                    b = m
                else:
                    a, fa = m, fm
            out.append((float(0.5 * (a + b)), 1 if v[i + 1] > v[i] else -1))
    return out


def sun_alt_at(site, jd):
    jd = np.asarray(jd, dtype=float)
    ra, dec, _ = sun_radec(jd)
    return altitude(ra, dec, site.lat, site.lon, jd)


def moon_alt_at(site, jd):
    jd = np.asarray(jd, dtype=float)
    ra, dec, _ = moon_radec(jd)
    return altitude(ra, dec, site.lat, site.lon, jd)


def night_span(d: date, site) -> tuple[float, float]:
    """Local noon on date d to local noon the next day, as JD (UTC). 23 or 25 h long on a daylight-saving night."""
    return local_noon_jd(d, site.tz), local_noon_jd(d + timedelta(days=1), site.tz)


def night_window(d: date, site, sun_alt: float | None = None) -> tuple[float, float] | None:
    """The dark interval of the night that starts on local date d: Sun below sun_alt (site.sun_alt by default).
    None when the Sun never gets that low (high-latitude summer). If the Sun is below it all through the span
    (polar night), the whole noon-to-noon span is returned."""
    level = site.sun_alt if sun_alt is None else sun_alt
    j0, j1 = night_span(d, site)
    f = lambda j: sun_alt_at(site, j)
    xs = _crossings(f, j0, j1, level)
    sets = [t for t, s in xs if s < 0]
    rises = [t for t, s in xs if s > 0]
    if not xs:
        return (j0, j1) if float(f(np.array([j0]))[0]) < level else None
    start = sets[0] if sets else j0
    after = [t for t in rises if t > start]
    end = after[0] if after else j1
    return (start, end) if end > start else None


def darkest_sun_alt(d: date, site) -> float:
    j0, j1 = night_span(d, site)
    grid = np.linspace(j0, j1, 289)
    return float(np.min(sun_alt_at(site, grid)))


def moon_rise_set(d: date, site) -> tuple[float | None, float | None]:
    """First moonrise and first moonset between local noon on d and the next local noon (None if none)."""
    j0, j1 = night_span(d, site)
    xs = _crossings(lambda j: moon_alt_at(site, j), j0, j1, MOON_RISE_ALT, step_min=10.0)
    rise = next((t for t, s in xs if s > 0), None)
    sett = next((t for t, s in xs if s < 0), None)
    return rise, sett


def night_info(d: date, site) -> dict:
    """Everything the block bar and the night header need, as JD (UTC) or None:
    dusk/dawn (planning darkness = site.sun_alt), civil_/nautical_/astro_ dusk and dawn, sunset, sunrise,
    moonrise, moonset, moon_illum (at the middle of the night), darkest_sun_alt, and 'dark_ok' (False when the
    planning darkness never comes; the planner then says so)."""
    out = {"date": d.isoformat()}
    for key, lvl in (("civil", CIVIL), ("nautical", NAUTICAL), ("astro", ASTRONOMICAL), ("sun", SUN_RISE_ALT)):
        w = night_window(d, site, lvl)
        out[f"{key}_dusk"], out[f"{key}_dawn"] = (w if w else (None, None))
    w = night_window(d, site)
    out["dusk"], out["dawn"] = (w if w else (None, None))
    out["dark_ok"] = w is not None
    out["sunset"], out["sunrise"] = out.pop("sun_dusk"), out.pop("sun_dawn")
    j0, j1 = night_span(d, site)
    mid = 0.5 * (out["dusk"] + out["dawn"]) if w else 0.5 * (j0 + j1)
    out["moon_illum"] = float(moon_radec(mid)[2])
    out["moonrise"], out["moonset"] = moon_rise_set(d, site)
    out["darkest_sun_alt"] = darkest_sun_alt(d, site)
    return out


# ---- Moon sky brightness (Krisciunas & Schaefer 1991, PASP 103, 1033) ---------------------------------------------

DARK_SKY_V = 21.5          # mag/arcsec², a decent rural site; a setting later (SQM)
EXTINCTION_K = 0.20        # V-band extinction, mag per airmass


def _ks_x(z_deg):
    z = np.radians(np.minimum(np.asarray(z_deg, dtype=float), 89.0))
    return 1.0 / np.sqrt(1.0 - 0.96 * np.sin(z) ** 2)


def moon_sky_brightness(target_ra, target_dec, site, jd, dark_sky_v: float = DARK_SKY_V,
                        k: float = EXTINCTION_K):
    """V sky brightness (mag/arcsec²) at the target with the Moon's light added (K&S 1991, eqs. 15–21).
    With the Moon down it is the dark sky. Arrays in, arrays out."""
    jd = np.asarray(jd, dtype=float)
    mra, mdec, _ = moon_radec(jd)
    alpha = moon_phase_angle(jd)
    tra, tdec = precess(target_ra, target_dec, jd)
    rho = sep_deg(mra, mdec, tra, tdec)
    z_moon = 90.0 - altitude(mra, mdec, site.lat, site.lon, jd)
    z_t = 90.0 - altitude(tra, tdec, site.lat, site.lon, jd)
    i_star = 10 ** (-0.4 * (3.84 + 0.026 * np.abs(alpha) + 4e-9 * alpha ** 4))
    rho_r = np.radians(np.maximum(rho, 1.0))
    f = 10 ** 5.36 * (1.06 + np.cos(rho_r) ** 2) + 10 ** (6.15 - np.maximum(rho, 10.0) / 40.0)
    b_moon = f * i_star * 10 ** (-0.4 * k * _ks_x(z_moon)) * (1 - 10 ** (-0.4 * k * _ks_x(z_t)))
    b_moon = np.where(z_moon < 90.0, b_moon, 0.0)              # nanoLamberts; zero with the Moon down
    b_dark = 34.08 * np.exp(20.7233 - 0.92104 * dark_sky_v)    # V mag/arcsec² → nanoLamberts
    total = b_dark + b_moon
    return (20.7233 - np.log(total / 34.08)) / 0.92104


def moon_sky_penalty(target_ra, target_dec, site, jd, dark_sky_v: float = DARK_SKY_V):
    """How much brighter than the dark sky the Moon makes it at the target (magnitudes, ≥ 0)."""
    return np.maximum(0.0, dark_sky_v - moon_sky_brightness(target_ra, target_dec, site, jd, dark_sky_v))


# ---- coordinates in text --------------------------------------------------------------------------------------------

def fmt_ra(ra_deg: float | None, decimals: int = 1) -> str:
    if ra_deg is None:
        return "—"
    h = (float(ra_deg) % 360.0) / 15.0
    hh = int(h)
    m = (h - hh) * 60
    mm = int(m)
    ss = (m - mm) * 60
    ss = round(ss, decimals)
    if ss >= 60:
        ss -= 60
        mm += 1
    if mm >= 60:
        mm -= 60
        hh = (hh + 1) % 24
    return f"{hh:02d}:{mm:02d}:{ss:0{3 + decimals if decimals else 2}.{decimals}f}"


def fmt_dec(dec_deg: float | None) -> str:
    if dec_deg is None:
        return "—"
    s = "-" if dec_deg < 0 else "+"
    d = abs(float(dec_deg))
    dd = int(d)
    m = (d - dd) * 60
    mm = int(m)
    ss = int(round((m - mm) * 60))
    if ss >= 60:
        ss -= 60
        mm += 1
    if mm >= 60:
        mm -= 60
        dd += 1
    return f"{s}{dd:02d}:{mm:02d}:{ss:02d}"


def parse_ra(text: str) -> float:
    """'23:13:17.0', '23 13 17', '23h13m17s' or decimal degrees (with 'd' or > 24) → degrees."""
    t = str(text).strip().lower().replace("h", " ").replace("m", " ").replace("s", " ").replace(":", " ")
    parts = [p for p in t.split() if p]
    if len(parts) == 1:
        v = float(parts[0].rstrip("d"))
        return v if ("d" in str(text).lower() or v > 24) else v * 15.0
    h, m, s = (list(map(float, parts)) + [0.0, 0.0])[:3]
    return (h + m / 60 + s / 3600) * 15.0


def parse_dec(text: str) -> float:
    t = str(text).strip().lower().replace("d", " ").replace("°", " ").replace("'", " ").replace('"', " ")
    t = t.replace("m", " ").replace("s", " ").replace(":", " ")
    neg = t.startswith("-") or t.startswith("−")
    parts = [p for p in t.replace("−", "-").lstrip("+-").split() if p]
    if len(parts) == 1:
        v = float(parts[0])
        return -v if neg else v
    d, m, s = (list(map(float, parts)) + [0.0, 0.0])[:3]
    v = d + m / 60 + s / 3600
    return -v if neg else v
