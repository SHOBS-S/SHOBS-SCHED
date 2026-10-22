"""SHOBS-SCHED weather: the night's hourly forecast (cloud, temperature, dew point, humidity, wind) and the dew onset.

v1 has one source, Open-Meteo (free, no key): https://api.open-meteo.com/v1/forecast. Every source returns, per
night (keyed by the ISO date the night STARTS on, site clock), a plain dict:

    {"source": "Open-Meteo", "fetched": ISO UTC time of the download, "age_h": hours since then,
     "ok": True | False, "note": "" | "Open-Meteo did not answer (timed out); using the forecast from 5 h ago",
     "hours": [{"jd", "cloud", "temp_c", "dew_c", "spread_c", "rh", "wind_kmh"}, ...]   sunset → sunrise,
     "dew_onset": JD (UTC) of the first moment in the planning darkness when temperature − dew point falls under
                  options["dew_spread_c"], or None,
     "dew_spread_c": the threshold used,
     "summary": "Clear until 02:00, then 60–80 % cloud; dew from 04:05"}

This is what planner.build_night(weather=...) and NightPlan.weather take. Times inside are JD (UTC); the summary is in
the site's clock. Nothing here raises for network trouble: a failed download falls back to the last saved answer for
the site (with its age in the note), or returns nights with no hours and summary "No forecast (<reason>)".

Cache: one file per site in settings.cache_dir(), weather_<lat>_<lon>.json =
    {"source", "fetched" (ISO UTC), "fetched_jd", "lat", "lon", "start", "end" (ISO dates the request covered),
     "reply": Open-Meteo's JSON reply as received}.
A cached answer younger than MAX_AGE_H that covers the asked nights is used without a download."""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import sky

USER_AGENT = "SHOBS-SCHED/1.0"
TIMEOUT_S = 20
MAX_AGE_H = 3.0                 # forecasts older than this are fetched again
MAX_FORECAST_DAYS = 16          # Open-Meteo's limit
DEFAULT_DEW_SPREAD_C = 2.5
CLEAR_MAX = 25                  # cloud % classes for the summary line
CLOUDY_MIN = 55


class WeatherError(Exception):
    """A download that failed, with the reason in plain words."""


def http_get_json(url: str, timeout: float = TIMEOUT_S) -> dict:
    """GET a JSON document (tests replace this function). Raises WeatherError with a plain reason."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        reason = f"HTTP {e.code}"
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("reason")
            if detail:
                reason += f": {detail}"
        except Exception:
            pass
        raise WeatherError(reason) from None
    except urllib.error.URLError as e:
        r = getattr(e, "reason", e)
        raise WeatherError("timed out" if "timed out" in str(r) else f"no connection ({r})") from None
    except TimeoutError:
        raise WeatherError("timed out") from None
    except OSError as e:
        raise WeatherError(f"no connection ({e})") from None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise WeatherError("the answer was not a forecast (not JSON)") from None


# ---- display helpers ------------------------------------------------------------------------------------------------

def fmt_temp(c: float | None, unit: str = "C") -> str:
    """'12 °C' or '54 °F' (unit 'C'/'F', as Site.temp_unit); '—' for None."""
    if c is None or not math.isfinite(float(c)):
        return "—"
    if str(unit).upper().startswith("F"):
        return f"{round(float(c) * 9 / 5 + 32):.0f} °F"
    return f"{round(float(c)):.0f} °C"


def fmt_wind(kmh: float | None, unit_system: str = "metric") -> str:
    """'14 km/h' (metric; also 'm' or 'C') or '9 mph' (imperial; also 'ft', 'F', 'mph'); '—' for None."""
    if kmh is None or not math.isfinite(float(kmh)):
        return "—"
    u = str(unit_system or "").strip().lower()
    if u in ("imperial", "ft", "f", "mph", "us"):
        return f"{round(float(kmh) / 1.609344):.0f} mph"
    return f"{round(float(kmh)):.0f} km/h"


# ---- turning hourly data into nights --------------------------------------------------------------------------------

def _jd_from_iso_utc(text: str) -> float:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return sky.jd_from_datetime(dt)


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def parse_open_meteo(reply: dict) -> list[dict]:
    """Open-Meteo reply (timezone=GMT, metric) → every hour as {jd, cloud, temp_c, dew_c, spread_c, rh, wind_kmh}.
    Missing values (null) stay None. Raises WeatherError when the reply has no hourly block."""
    if not isinstance(reply, dict) or reply.get("error"):
        raise WeatherError(str((reply or {}).get("reason") or "the answer was not a forecast"))
    h = reply.get("hourly") or {}
    times = h.get("time") or []
    if not times:
        raise WeatherError("the answer had no hourly forecast")
    off = float(reply.get("utc_offset_seconds") or 0) / 86400.0

    def col(name):
        v = h.get(name) or []
        return [(_num(v[i]) if i < len(v) else None) for i in range(len(times))]

    temp, dew, rh = col("temperature_2m"), col("dew_point_2m"), col("relative_humidity_2m")
    cloud, wind = col("cloud_cover"), col("wind_speed_10m")
    out = []
    for i, t in enumerate(times):
        try:
            jd = _jd_from_iso_utc(t) - off
        except ValueError:
            continue
        spread = (temp[i] - dew[i]) if (temp[i] is not None and dew[i] is not None) else None
        out.append({"jd": jd, "cloud": cloud[i], "temp_c": temp[i], "dew_c": dew[i],
                    "spread_c": None if spread is None else round(spread, 2), "rh": rh[i], "wind_kmh": wind[i]})
    out.sort(key=lambda r: r["jd"])
    return out


def dew_onset(hours: list[dict], dusk: float | None, dawn: float | None, spread_c: float) -> float | None:
    """First moment in [dusk, dawn] when temperature − dew point < spread_c, interpolated linearly between hourly
    values (to the minute it is no better than the forecast, but it reads naturally). None if never, or no dark."""
    if dusk is None or dawn is None:
        return None
    pts = [(r["jd"], r["spread_c"]) for r in hours if r.get("spread_c") is not None]
    if not pts:
        return None
    # the spread at dusk (interpolated), then each hourly point inside the darkness
    for i, (jd, s) in enumerate(pts):
        if jd < dusk:
            continue
        if jd > dawn:
            # the crossing may still fall between the last point before dawn and this one
            if i > 0 and pts[i - 1][1] >= spread_c > s:
                j0, s0 = pts[i - 1]
                t = j0 + (s0 - spread_c) / (s0 - s) * (jd - j0)
                if t <= dawn:
                    return max(t, dusk)
            return None
        if s < spread_c:
            if i == 0:
                return max(jd, dusk)
            j0, s0 = pts[i - 1]
            if s0 < spread_c:                       # already below before this hour: below at dusk
                return dusk if j0 < dusk else j0
            t = j0 + (s0 - spread_c) / (s0 - s) * (jd - j0)
            return max(t, dusk)
    return None


def _cloud_class(c: float | None) -> str:
    if c is None:
        return "?"
    return "clear" if c < CLEAR_MAX else ("cloudy" if c >= CLOUDY_MIN else "partly")


def _range_text(vals: list[float]) -> str:
    lo = int(math.floor(min(vals) / 10.0) * 10)
    hi = int(min(100, math.ceil(max(vals) / 10.0) * 10))
    if lo >= 90:
        return "overcast"
    return f"about {lo} % cloud" if lo == hi else f"{lo}–{hi} % cloud"


def summary_text(hours: list[dict], dusk, dawn, onset, tz: str) -> str:
    """'Clear until 02:00, then 60–80 % cloud; dew from 04:05' over the darkness (sunset→sunrise if no darkness)."""
    span = [r for r in hours if (dusk is None or r["jd"] >= dusk - 0.5 / 24) and
            (dawn is None or r["jd"] <= dawn + 0.5 / 24)] if hours else []
    span = [r for r in span if r.get("cloud") is not None]
    if not span:
        text = "No cloud forecast for the dark hours"
    else:
        segs = []                                    # [class, first jd, last jd, values]
        for r in span:
            c = _cloud_class(r["cloud"])
            if segs and segs[-1][0] == c:
                segs[-1][2] = r["jd"]
                segs[-1][3].append(r["cloud"])
            else:
                segs.append([c, r["jd"], r["jd"], [r["cloud"]]])
        # a single hour of a different class between two equal ones is noise in a forecast: fold it in
        i = 1
        while i < len(segs) - 1:
            if len(segs[i][3]) == 1 and segs[i - 1][0] == segs[i + 1][0]:
                a, b, c = segs[i - 1], segs[i], segs[i + 1]
                segs[i - 1] = [a[0], a[1], c[2], a[3] + b[3] + c[3]]
                del segs[i:i + 2]
            else:
                i += 1

        def desc(seg):
            return "clear" if seg[0] == "clear" else _range_text(seg[3])

        if len(segs) == 1:
            s = segs[0]
            text = "Clear all night" if s[0] == "clear" else f"{desc(s)[0].upper()}{desc(s)[1:]} all night"
        elif len(segs) > 4:
            text = f"Changeable, {_range_text([r['cloud'] for r in span])}"
        else:
            parts = []
            for k, s in enumerate(segs):
                nxt = segs[k + 1][1] if k + 1 < len(segs) else None
                piece = desc(s) if k == 0 else f"then {desc(s)}"
                if nxt is not None:
                    piece += f" until {sky.hhmm(nxt, tz)}"
                parts.append(piece)
            text = ", ".join(parts)
            text = text[0].upper() + text[1:]
    if onset is not None:
        text += f"; dew from {sky.hhmm(onset, tz)}"
    return text


def _night_bounds(d: date, site) -> tuple[float, float, float | None, float | None]:
    """(sunset-ish start, sunrise-ish end, dusk, dawn) as JD for the night starting on d."""
    j0, j1 = sky.night_span(d, site)
    sun = sky.night_window(d, site, sky.SUN_RISE_ALT)
    dark = sky.night_window(d, site)
    start, end = sun if sun else (dark if dark else (None, None))
    if start is None:                               # midnight Sun: no night at all; keep the noon-to-noon span
        start, end = j0, j1
    return start, end, (dark[0] if dark else None), (dark[1] if dark else None)


def night_weather(hours: list[dict], d: date, site, options: dict | None = None, *, source: str = "",
                  fetched: str = "", age_h: float | None = None, note: str = "", ok: bool = True) -> dict:
    """The night dict (see the module docstring) for the night starting on local date d."""
    opts = options or {}
    thr = float(opts.get("dew_spread_c", DEFAULT_DEW_SPREAD_C))
    start, end, dusk, dawn = _night_bounds(d, site)
    pad = 0.5 / 24.0                                 # the hour nearest sunset/sunrise is kept for the strip
    sel = [dict(r) for r in hours if start - pad <= r["jd"] <= end + pad]
    out = {"source": source, "fetched": fetched, "age_h": None if age_h is None else round(age_h, 2),
           "ok": ok, "note": note, "hours": sel, "dew_onset": None, "dew_spread_c": thr,
           "sunset": start, "sunrise": end}
    if not sel:
        out["ok"] = False
        out["summary"] = f"No forecast ({note})" if note and not ok else (
            "No forecast for this night (beyond the forecast range)")
        return out
    out["dew_onset"] = dew_onset(sel, dusk, dawn, thr)
    out["summary"] = summary_text(sel, dusk if dusk else start, dawn if dawn else end, out["dew_onset"], site.tz)
    return out


def no_forecast(site, start: date, n_nights: int, source: str, reason: str) -> dict[str, dict]:
    """Nights with no hours and summary 'No forecast (<reason>)'."""
    out = {}
    for k in range(max(1, int(n_nights))):
        d = start + timedelta(days=k)
        out[d.isoformat()] = {"source": source, "fetched": "", "age_h": None, "ok": False, "note": reason,
                              "hours": [], "dew_onset": None, "summary": f"No forecast ({reason})"}
    return out


# ---- sources --------------------------------------------------------------------------------------------------------

class WeatherSource:
    """A forecast provider. fetch(site, start, n_nights, options) → {ISO night date: night dict}. Never raises for
    network trouble (see the module docstring)."""
    name = ""

    def fetch(self, site, start: date, n_nights: int, options: dict | None = None) -> dict[str, dict]:
        raise NotImplementedError


def cache_path(site) -> str:
    import settings
    return os.path.join(settings.cache_dir(), f"weather_{float(site.lat):.3f}_{float(site.lon):.3f}.json")


def _read_cache(site) -> dict | None:
    try:
        with open(cache_path(site), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and isinstance(data.get("reply"), dict) else None
    except (OSError, ValueError):
        return None


def _write_cache(site, data: dict) -> None:
    path = cache_path(site)
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def _age_text(hours: float) -> str:
    if hours < 1.5:
        return f"{max(1, round(hours * 60))} min ago"
    if hours < 48:
        return f"{round(hours)} h ago"
    return f"{round(hours / 24)} days ago"


class OpenMeteo(WeatherSource):
    name = "Open-Meteo"
    URL = "https://api.open-meteo.com/v1/forecast"
    HOURLY = "temperature_2m,dew_point_2m,relative_humidity_2m,cloud_cover,wind_speed_10m"

    def __init__(self, now_jd=None):
        self._now = now_jd or sky.now_jd       # tests pass a fixed clock

    def url(self, site, start: date, n_nights: int) -> str:
        today = sky.to_utc(self._now()).date()
        last = start + timedelta(days=max(1, int(n_nights)))          # the morning after the last night
        days = max(1, min(MAX_FORECAST_DAYS, (last - today).days + 1))
        past = max(1, min(92, (today - start).days + 1))
        q = {"latitude": f"{float(site.lat):.4f}", "longitude": f"{float(site.lon):.4f}", "hourly": self.HOURLY,
             "timezone": "GMT", "past_days": past, "forecast_days": days, "temperature_unit": "celsius",
             "wind_speed_unit": "kmh", "precipitation_unit": "mm"}
        return self.URL + "?" + urllib.parse.urlencode(q, safe=",")

    def _nights(self, hours, site, start, n_nights, options, fetched, age_h, note):
        out = {}
        for k in range(max(1, int(n_nights))):
            d = start + timedelta(days=k)
            out[d.isoformat()] = night_weather(hours, d, site, options, source=self.name, fetched=fetched,
                                               age_h=age_h, note=note)
        return out

    def fetch(self, site, start: date, n_nights: int, options: dict | None = None) -> dict[str, dict]:
        import settings
        if site is None or not site.complete():
            return no_forecast(site, start, n_nights, self.name, "the site's position and time zone are not set")
        now = self._now()
        end = start + timedelta(days=max(1, int(n_nights)))
        cached = _read_cache(site)
        if cached:
            age_h = (now - float(cached.get("fetched_jd") or 0)) * 24.0
            covers = (cached.get("start", "9999") <= start.isoformat() and cached.get("end", "") >= end.isoformat())
            if 0 <= age_h < MAX_AGE_H and covers:
                try:
                    hours = parse_open_meteo(cached["reply"])
                    return self._nights(hours, site, start, n_nights, options, cached.get("fetched", ""), age_h, "")
                except WeatherError:
                    pass
        try:
            reply = http_get_json(self.url(site, start, n_nights))
            hours = parse_open_meteo(reply)
        except WeatherError as e:
            reason = f"{self.name} did not answer ({e})"
            if cached:
                try:
                    hours = parse_open_meteo(cached["reply"])
                    age_h = max(0.0, (now - float(cached.get("fetched_jd") or 0)) * 24.0)
                    note = f"{reason}; using the forecast from {_age_text(age_h)}"
                    settings.log(f"weather: {note}")
                    return self._nights(hours, site, start, n_nights, options, cached.get("fetched", ""), age_h,
                                        note)
                except WeatherError:
                    pass
            settings.log(f"weather: {reason}; no saved forecast")
            return no_forecast(site, start, n_nights, self.name, reason)
        fetched = sky.to_utc(now).replace(microsecond=0).isoformat()
        _write_cache(site, {"source": self.name, "fetched": fetched, "fetched_jd": now, "lat": site.lat,
                            "lon": site.lon, "start": start.isoformat(), "end": end.isoformat(), "reply": reply})
        settings.log(f"weather: {self.name} forecast fetched for {start.isoformat()} + {n_nights} nights")
        return self._nights(hours, site, start, n_nights, options, fetched, 0.0, "")


# NWS (api.weather.gov, US only) plugs in here later as another WeatherSource subclass; v1 ships Open-Meteo only.
SOURCES = {"Open-Meteo": OpenMeteo}
