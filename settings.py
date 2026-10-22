"""SHOBS-SCHED settings, target list, saved schedule and cache locations.

Files (in the user's home folder, like SHOBS-P):
  .shobs_sched_settings.json   site, optics, planner options, weather source, display units
  .shobs_sched_targets.json    targets, priorities, campaign rules and goals
  .shobs_sched/                schedule.json, cache/, sched.log
SHOBS-P's .shobs_p_settings.json is READ (site, optics, observer) and never written: each app owns its settings.
Tests point everything elsewhere with the SHOBS_SCHED_HOME and SHOBS_P_SETTINGS environment variables."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from models import Optics, Schedule, Site, Target

APP_NAME = "SHOBS-SCHED"
APP_TITLE = "Shiloh Hill Observatory – Scheduler (SHOBS-SCHED)"
APP_VERSION = "1.0.0"


def home() -> str:
    return os.environ.get("SHOBS_SCHED_HOME") or os.path.expanduser("~")


def settings_path() -> str:
    return os.path.join(home(), ".shobs_sched_settings.json")


def targets_path() -> str:
    return os.path.join(home(), ".shobs_sched_targets.json")


def data_dir() -> str:
    d = os.path.join(home(), ".shobs_sched")
    os.makedirs(d, exist_ok=True)
    return d


def schedule_path() -> str:
    return os.path.join(data_dir(), "schedule.json")


def cache_dir() -> str:
    d = os.path.join(data_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def log_path() -> str:
    return os.path.join(data_dir(), "sched.log")


def shobs_p_settings_path() -> str:
    return os.environ.get("SHOBS_P_SETTINGS") or os.path.join(os.path.expanduser("~"), ".shobs_p_settings.json")


# ---- planner and display options (all settable; defaults from the Requirements doc) -------------------------------

DEFAULT_OPTIONS = {
    # building the night
    "overhead_slew_min": 3.0,          # slew + settle + plate solve/re-center between blocks
    "overhead_focus_min": 5.0,         # autofocus at the start of the night and when a block asks for it
    "flip_lead_min": 12.0,             # start a block this long before its meridian and let the mount flip (10–15)
    "flip_settle_min": 4.0,            # time lost to the flip itself (slew, solve, guide restart)
    "baseline_min": 60.0,              # wanted out-of-transit baseline each side for planned transits
    "baseline_trim_min": 20.0,         # shortest baseline each side when two fixed targets collide
    "min_gap_report_min": 15.0,        # free gaps shorter than this are not listed
    "nights": 1,                       # schedule length (1–7)
    "dew_sensitive_end": True,         # end dew-sensitive runs at the predicted dew onset
    # Suggested transits (John's order, 20 Oct)
    "suggest_vmin": 9.0,
    "suggest_vmax": 13.0,
    "suggest_min_depth": 0.005,
    "suggest_baseline_min": 30.0,
    "suggest_max": 12,
    "suggest_moon_penalty_max": 1.5,   # magnitudes of Moon-brightened sky allowed
    "tc_sigma_warn_min": 10.0,         # ⚠ when a transit's predicted Tc uncertainty is over this
    # sky
    "dark_sky_v": 21.5,                # mag/arcsec², for the Moon penalty
    # weather
    "weather_source": "Open-Meteo",
    "dew_spread_c": 2.5,               # dew onset when temperature − dew point falls under this
    # exposures
    "target_peak_frac": 0.8,           # aim the predicted peak at this fraction of the linear limit
    "fwhm_arcsec": 3.0,                # typical seeing FWHM for the cold-start estimate
}


def default_settings() -> dict:
    return {"version": APP_VERSION, "site": Site().to_dict(), "optics": Optics().to_dict(),
            "options": dict(DEFAULT_OPTIONS), "observer_code": "", "set_here": [], "shobs_p_read": ""}


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> bool:
    """Write atomically: a crash or a bad value never leaves a half-written file."""
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_settings() -> dict:
    """SCHED's settings merged over the defaults (new options appear with their defaults)."""
    data = _read_json(settings_path())
    out = default_settings()
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "options" and isinstance(v, dict):
                out["options"].update(v)
            else:
                out[k] = v
    return out


def save_settings(s: dict) -> bool:
    return _write_json(settings_path(), s)


def site_of(s: dict) -> Site:
    return Site.from_dict(s.get("site"))


def optics_of(s: dict) -> Optics:
    return Optics.from_dict(s.get("optics"))


# ---- SHOBS-P settings (read only) ------------------------------------------------------------------------------

SHOBS_P_FIELDS = {
    # SHOBS-P key -> (section, SCHED key)
    "site_lat": ("site", "lat"), "site_lon": ("site", "lon"), "site_elevation": ("site", "elevation_m"),
    "aperture_mm": ("optics", "aperture_mm"), "focal_mm": ("optics", "focal_mm"), "pixel_um": ("optics", "pixel_um"),
    "telescope_name": ("site", "name"), "linear_limit": ("optics", "linear_limit_adu"),
    "linear_limit_adu": ("optics", "linear_limit_adu"), "site_tz": ("site", "tz"),
}


def read_shobs_p() -> dict | None:
    """SHOBS-P's saved settings, or None when SHOBS-P is not installed (no file). Never writes."""
    data = _read_json(shobs_p_settings_path())
    return data if isinstance(data, dict) else None


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def merge_shobs_p(s: dict) -> tuple[dict, list[str]]:
    """Fill site/optics from SHOBS-P where SCHED has no value of its own ("set here" values always win).
    Returns (settings, the field names taken from SHOBS-P). s['shobs_p_read'] records the file's date."""
    p = read_shobs_p()
    taken: list[str] = []
    if not p:
        s["shobs_p_read"] = ""
        return s, taken
    set_here = set(s.get("set_here") or [])
    for key, (section, field) in SHOBS_P_FIELDS.items():
        if key not in p or f"{section}.{field}" in set_here:
            continue
        val = p[key]
        if field in ("name", "tz"):
            val = str(val or "").strip()
            if not val:
                continue
        else:
            val = _num(val)
            if val is None:
                continue
        s.setdefault(section, {})[field] = val
        taken.append(f"{section}.{field}")
    mount = str(p.get("mount") or "").lower()
    if mount and "site.mount" not in set_here:
        s["site"]["mount"] = "altaz" if ("alt" in mount and "az" in mount) else "GEM"
        taken.append("site.mount")
    if p.get("observer_code") and "observer_code" not in set_here:
        s["observer_code"] = str(p["observer_code"])
        taken.append("observer_code")
    try:
        stamp = os.path.getmtime(shobs_p_settings_path())
        s["shobs_p_read"] = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    except OSError:
        s["shobs_p_read"] = "?"
    return s, taken


def mark_set_here(s: dict, dotted: str) -> None:
    """Remember that the user changed this value in SCHED (it then overrides SHOBS-P's)."""
    lst = list(s.get("set_here") or [])
    if dotted not in lst:
        lst.append(dotted)
    s["set_here"] = lst


# ---- time zone guess ----------------------------------------------------------------------------------------------

_PREFERRED_ZONES = (
    "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
    "America/Anchorage", "Pacific/Honolulu", "America/Halifax", "America/St_Johns", "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires", "America/Santiago", "America/Mexico_City", "Europe/London", "Europe/Dublin",
    "Europe/Lisbon", "Europe/Paris", "Europe/Berlin", "Europe/Warsaw", "Europe/Athens", "Europe/Helsinki",
    "Europe/Moscow", "Africa/Johannesburg", "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo",
    "Australia/Perth", "Australia/Adelaide", "Australia/Brisbane", "Australia/Sydney", "Pacific/Auckland", "UTC",
)


def guess_tz() -> str:
    """An IANA zone whose January and July offsets match this computer's (Windows does not tell Python its IANA
    name). The user can change it on the Setup page; the site's zone, not the computer's, drives every time."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return "UTC"
    y = datetime.now().year

    def offsets(tz):
        return tuple(datetime(y, m, 15, 12, tzinfo=tz).utcoffset() for m in (1, 7))

    try:
        mine = tuple(datetime(y, m, 15, 12).astimezone().utcoffset() for m in (1, 7))
    except Exception:
        return "UTC"
    for name in _PREFERRED_ZONES:
        try:
            if offsets(ZoneInfo(name)) == mine:
                return name
        except Exception:
            continue
    return "UTC"


def all_zones() -> list[str]:
    try:
        from zoneinfo import available_timezones
        zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
        return zones + ["UTC"]
    except Exception:
        return list(_PREFERRED_ZONES)


# ---- targets and schedule ------------------------------------------------------------------------------------------

def load_targets() -> list[Target]:
    data = _read_json(targets_path())
    rows = data.get("targets", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for r in rows:
        if isinstance(r, dict):
            try:
                out.append(Target.from_dict(r))
            except TypeError:
                continue
    return out


def save_targets(targets: list[Target]) -> bool:
    return _write_json(targets_path(), {"version": APP_VERSION, "targets": [t.to_dict() for t in targets]})


def load_schedule() -> Schedule:
    data = _read_json(schedule_path())
    return Schedule.from_dict(data) if isinstance(data, dict) else Schedule()


def save_schedule(sch: Schedule) -> bool:
    return _write_json(schedule_path(), sch.to_dict())


# ---- log -------------------------------------------------------------------------------------------------------

def log(line: str) -> None:
    """One time-stamped line in .shobs_sched/sched.log (rotated at 2 MB). Never raises."""
    try:
        path = log_path()
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".1")
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {line}\n")
    except Exception:
        pass


def file_age_days(path: str) -> float | None:
    try:
        return (time.time() - os.path.getmtime(path)) / 86400.0
    except OSError:
        return None
