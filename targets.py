"""SHOBS-SCHED target lookups, the lookup cache, Suggested transits and the Targets page's "next event" text.

Where things come from
  Planets    NASA Exoplanet Archive (TAP): the composite table `pscomppars` for position, V, depth, duration and a
             first ephemeris; then the most precise published ephemeris for the coming weeks from table `ps`.
             Names: the Archive's alias service first, then spelling variants ("wasp-12b" → "WASP-12 b").
  Variables  AAVSO VSX API (JSON) first; when it does not answer, VizieR's copy of VSX (B/vsx/vsx) by name, then by
             a 1' cone around the SIMBAD position.
  Anything   SIMBAD (sim-script) for a name that is neither: the UI then asks which mode (NeedsMode).
Network      urllib only, User-Agent "SHOBS-SCHED/1.0", timeouts; every call goes through `http_get` (tests replace
             it). Failures become FetchError with a plain reason ("HTTP 503", "no network connection", "the
             Archive's firewall (Cloudflare) blocked the query"), and then either the cached answer (with a note of
             its age) or LookupError_ with that reason. Call these from a worker thread, never the UI thread.

Lookup cache (stand-alone, in settings.cache_dir(); meant to merge with SHOBS-P's future cache, SHOBS-P #21)
  One JSON file per lookup key:  lookup_<source>_<slug>_<hash8>.json
    {
      "format": "shobs-lookup-cache/1",     # bump when the layout changes; unknown formats are ignored
      "app": "SHOBS-SCHED",                 # the app that wrote it (SHOBS-P will write "SHOBS-P")
      "source": "NEA",                      # NEA | VSX | SIMBAD | NEA catalog
      "key": "nea:planet:hat-p-23 b",       # <source>:<what>:<normalized name>  (lower case, single spaces)
      "query": "HAT-P-23 b",                # what was asked, as typed
      "fetched": "2026-10-22T14:03:11+00:00",   # UTC, ISO 8601
      "fetched_unix": 1792677791.0,         # the same moment as Unix seconds (the age is computed from this)
      "data": { ... }                       # the PARSED reply (not the raw text), shape per key family:
    }
    nea:planet:<name>   {"composite": {pscomppars row}, "ps": [ps rows]}  or  {"choice": {"system", "planets",
                        "transiting"}}  (a star with several planets)
    vsx:variable:<name> {"name","ra","dec","vtype","period","epoch","max","min","band","eclipse_pct",
                        "via": "VSX" | "VizieR VSX"}
    simbad:object:<name> {"main_id","ra","dec","vmag","otype","otype_short"}
    nea:catalog:transiting  {"rows": [catalog dicts]}   (file lookup_nea_catalog_transiting_<hash>.json)
  Ages: planet ephemerides and the catalog are refetched after 7 days, VSX and SIMBAD after 30. A failed refetch
  keeps using the old copy (and says how old). Names that are not found are not cached.
"""
from __future__ import annotations

import csv
import hashlib
import html
import http.client
import io
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np

import events
import settings
import sky
from models import Planet, Schedule, Target, VarElements

USER_AGENT = "SHOBS-SCHED/1.0"
TIMEOUT_S = 30.0
CACHE_FORMAT = "shobs-lookup-cache/1"
PLANET_MAX_AGE_DAYS = 7.0
CATALOG_MAX_AGE_DAYS = 7.0
VSX_MAX_AGE_DAYS = 30.0
SIMBAD_MAX_AGE_DAYS = 30.0

NEA_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?"
NEA_ALIAS = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Lookup/nph-aliaslookup.py?"
VSX_API = "https://www.aavso.org/vsx/index.php?"
VIZIER_TAP = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync?"
SIMBAD_SCRIPT = "https://simbad.cds.unistra.fr/simbad/sim-script?"

SOURCE_WORDS = {"NEA": "the NASA Exoplanet Archive", "VSX": "AAVSO VSX", "VizieR": "VizieR",
                "SIMBAD": "SIMBAD", "NEA catalog": "the NASA Exoplanet Archive"}

# pscomppars columns for one planet (the composite table also carries the first-error columns)
NEA_COLUMNS = ("pl_name,hostname,pl_orbper,pl_orbpererr1,pl_tranmid,pl_tranmiderr1,pl_trandur,pl_trandep,"
               "pl_ratror,ra,dec,sy_vmag,tran_flag,pl_tranmid_reflink,pl_orbper_reflink")
PS_COLUMNS = ("pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_tranmid,pl_tranmiderr1,pl_tranmiderr2,pl_tsystemref,"
              "pl_refname")
CATALOG_COLUMNS = ("pl_name,hostname,pl_orbper,pl_orbpererr1,pl_tranmid,pl_tranmiderr1,pl_trandur,pl_trandep,"
                   "pl_ratror,ra,dec,sy_vmag")


# ---- exceptions ----------------------------------------------------------------------------------------------------

class LookupError_(Exception):
    """A lookup failed; str(e) / e.message is a plain sentence for the user."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFound(LookupError_):
    """The service answered but does not know the name (as opposed to: the service did not answer)."""


class NeedsMode(Exception):
    """The name resolved (SIMBAD) but is not a known planet or VSX variable: the UI asks which mode."""

    def __init__(self, name: str, ra: float, dec: float, vmag: float | None, main_id: str, otype: str = "",
                 source: str = "SIMBAD"):
        self.name, self.ra, self.dec, self.vmag, self.main_id, self.otype = name, ra, dec, vmag, main_id, otype
        self.source = source
        self.message = (f"{name} was found in {source} ({main_id}{', ' + otype if otype else ''}) but is not a "
                        "known transiting planet or VSX variable. Choose the mode to observe it in.")
        super().__init__(self.message)


class PlanetChoice(Exception):
    """A star name with several planets: planets = their Archive names (transiting ones first)."""

    def __init__(self, system: str, planets: list, transiting=None):
        self.system = system
        self.planets = list(planets)
        self.transiting = set(transiting or [])
        self.message = (f"{system} has {len(self.planets)} planets ({', '.join(self.planets)}). "
                        "Choose which one to plan.")
        super().__init__(self.message)


class FetchError(Exception):
    """A network call failed. reason is plain words ("HTTP 503", "no network connection")."""

    def __init__(self, reason: str, status: int | None = None):
        self.reason, self.status = reason, status
        super().__init__(reason)


# ---- network -------------------------------------------------------------------------------------------------------

def http_get(url: str, timeout: float = TIMEOUT_S) -> bytes:
    """GET a URL → bytes. Every failure becomes FetchError with a plain reason. Tests replace this function."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        words = {403: "refused the request", 404: "page not found", 429: "too many requests",
                 500: "server error", 502: "server error", 503: "service unavailable", 504: "gateway timeout"}
        w = words.get(exc.code, "error")
        raise FetchError(f"{w}, HTTP {exc.code}", exc.code) from None
    except urllib.error.URLError as exc:
        r = exc.reason
        if isinstance(r, socket.timeout) or "timed out" in str(r):
            raise FetchError(f"no answer within {timeout:.0f} s") from None
        if isinstance(r, socket.gaierror):
            raise FetchError("no network connection (name lookup failed)") from None
        if isinstance(r, ConnectionRefusedError):
            raise FetchError("connection refused") from None
        raise FetchError(f"network error: {r}") from None
    except (socket.timeout, TimeoutError):
        raise FetchError(f"no answer within {timeout:.0f} s") from None
    except OSError as exc:
        raise FetchError(f"network error: {exc}") from None
    except http.client.HTTPException as exc:      # IncompleteRead, BadStatusLine, RemoteDisconnected...
        raise FetchError(f"network error: the reply was cut off or garbled ({type(exc).__name__})") from None
    except ValueError as exc:                     # a malformed URL or reply header
        raise FetchError(f"network error: {exc}") from None


def _get_text(url: str, timeout: float = TIMEOUT_S) -> str:
    raw = http_get(url, timeout)     # looked up at call time, so tests can replace targets.http_get
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    low = text[:3000].lower()
    if ("cloudflare" in low or "just a moment" in low or "attention required" in low) and "<html" in low:
        raise FetchError("the service's firewall (Cloudflare) blocked the query")
    return text


def _expected_first_column(adql: str) -> str | None:
    m = re.match(r"\s*select\s+(?:top\s+\d+\s+)?([A-Za-z_][\w.]*)", adql, flags=re.IGNORECASE)
    return m.group(1).split(".")[-1].lower() if m else None


def _tap_csv(base: str, adql: str, nea: bool = True, expect: str | None = None) -> list[dict]:
    """Run a TAP query → rows. A reply whose header does not start with the expected column (the first one
    selected, or `expect` for SELECT *) is an error page, not data: FetchError."""
    if nea:
        url = base + urllib.parse.urlencode({"query": adql, "format": "csv"})
    else:
        url = base + urllib.parse.urlencode({"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql})
    text = _get_text(url)
    head = text.lstrip()[:400]
    if head.startswith("<"):
        if "error" in head.lower() or "<votable" in head.lower():
            raise FetchError("the service rejected the query")
        raise FetchError("an unexpected web page came back instead of data")
    reader = csv.DictReader(io.StringIO(text))
    fields = [str(f or "").strip().lower() for f in (reader.fieldnames or [])]
    want = (expect or _expected_first_column(adql) or "").lower()
    if not fields or (want and want != "*" and want not in fields):
        raise FetchError("an unexpected reply came back instead of data")
    return list(reader)


def nea_query(adql: str) -> list[dict]:
    return _tap_csv(NEA_TAP, adql, nea=True)


def vizier_query(adql: str) -> list[dict]:
    return _tap_csv(VIZIER_TAP, adql, nea=False, expect="Name")


# ---- small helpers -------------------------------------------------------------------------------------------------

def _f(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _clean(name: str) -> str:
    """Single spaces; typographic apostrophes → "'" (kept: "Barnard's Star"; ADQL escapes it)."""
    s = str(name or "")
    for ch in ("\u2019", "\u2018", "\u02bc", "`"):
        s = s.replace(ch, "'")
    return " ".join(s.split())


def _key_name(name: str) -> str:
    return _clean(name).lower()


def strip_tags(text: str) -> str:
    """HTML link → its text. The Archive double-escapes names ("Kab&amp;aacute;th"): unescape twice."""
    return " ".join(html.unescape(html.unescape(re.sub(r"<[^>]+>", "", text or ""))).split())


def _adql_str(s: str) -> str:
    return s.replace("'", "''")


def today_iso(site=None) -> str:
    """The date for Target.added: tonight's date on the SITE's clock (night_date_of: before local noon it is still
    last night) when a site is known — the one given, else the saved settings' — or the computer's date."""
    try:
        if site is None:
            site = settings.site_of(settings.load_settings())
        if site is not None and site.complete() and sky.zone_ok(site.tz):
            return sky.night_date_of(sky.now_jd(), site.tz).isoformat()
    except Exception:
        pass
    return date.today().isoformat()


def _age_words(days: float) -> str:
    if days < 1.0:
        return "earlier today" if days * 24 >= 1 else "less than an hour ago"
    n = int(round(days))
    return "1 day ago" if n == 1 else f"{n} days ago"


# ---- cache ---------------------------------------------------------------------------------------------------------

def _cache_path(source: str, key: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower().split(":", 1)[-1]).strip("_")[:60]
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    src = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    return os.path.join(settings.cache_dir(), f"lookup_{src}_{slug}_{h}.json")


def cache_read(source: str, key: str) -> tuple[dict | list | None, float | None]:
    """(data, age in days) of a cached lookup, or (None, None). A missing, unreadable or damaged cache (or a cache
    folder that cannot be created) is simply no cache."""
    try:
        with open(_cache_path(source, key), encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception:
        return None, None
    if not isinstance(rec, dict) or rec.get("format") != CACHE_FORMAT or rec.get("key") != key:
        return None, None
    t = _f(rec.get("fetched_unix"))
    if t is None:
        return None, None
    data = rec.get("data")
    return (data, (time.time() - t) / 86400.0) if isinstance(data, dict) else (None, None)


def cache_write(source: str, key: str, query: str, data) -> None:
    now = time.time()
    rec = {"format": CACHE_FORMAT, "app": settings.APP_NAME, "source": source, "key": key, "query": query,
           "fetched": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
           "fetched_unix": now, "data": data}
    try:                                   # an unwritable cache folder means no cache, never an error
        path = _cache_path(source, key)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        settings.log(f"cache: could not write the {source} lookup {key}: {exc}")


def cached_lookup(source: str, key: str, query: str, max_age_days: float, fetch, refresh: bool = False):
    """Return (data, note). A fresh cached copy is used as is (note ""). Otherwise fetch() runs: its answer is
    cached (None = not found, not cached → NotFound from the caller). When fetch() raises FetchError, the cached
    copy (any age) comes back with a note of its age, or LookupError_ with the reason."""
    data, age = cache_read(source, key)       # always a dict or None
    if data is not None and age is not None and age < max_age_days and not refresh:
        return data, ""
    try:
        fresh = fetch()
    except FetchError as exc:
        who = SOURCE_WORDS.get(source, source)
        if data is not None:
            note = f"{who} did not answer ({exc.reason}); using the copy from {_age_words(age or 0.0)}"
            settings.log(f"lookup {key}: {note}")
            return data, note
        settings.log(f"lookup {key}: {who} did not answer ({exc.reason}); nothing cached")
        raise LookupError_(f"{who[0].upper() + who[1:]} did not answer ({exc.reason}) and there is no saved "
                           f"copy for {query}. Try again later.") from None
    if isinstance(fresh, dict):
        cache_write(source, key, query, fresh)
    return fresh, ""


# ---- planet names --------------------------------------------------------------------------------------------------

# Survey prefixes as the Archive spells them, so "Wasp-12 b" or "tres-3b" still matches (from SHOBS-P).
_PREFIXES = {"wasp": "WASP", "tres": "TrES", "hat-p": "HAT-P", "hats": "HATS", "kelt": "KELT", "xo": "XO",
             "qatar": "Qatar", "kepler": "Kepler", "k2": "K2", "toi": "TOI", "corot": "CoRoT", "gj": "GJ",
             "hd": "HD", "hip": "HIP", "tic": "TIC", "ngts": "NGTS", "wts": "WTS", "kps": "KPS", "mascara": "MASCARA",
             "lhs": "LHS", "gl": "GL", "koi": "KOI", "lp": "LP", "kmt": "KMT", "ogle": "OGLE",
             "trappist": "TRAPPIST", "wendelstein": "Wendelstein", "dmpp": "DMPP", "epic": "EPIC",
             "hat": "HAT"}
# Surveys whose Archive names join the number with a hyphen ("TOI-2046 b", "HAT-P-23 b") rather than a space.
_HYPHEN_SURVEYS = {"WASP", "TrES", "HAT-P", "HATS", "KELT", "XO", "Qatar", "Kepler", "K2", "TOI", "CoRoT", "NGTS",
                   "WTS", "KPS", "MASCARA", "KOI", "TRAPPIST", "DMPP", "Wendelstein"}
_PLANETLIKE = re.compile(r"(\d\s?[b-i]|\.\d{1,2})$", re.IGNORECASE)
_SURVEY_RE = re.compile(r"^(wasp|tres|hat-?p|hats|kelt|xo|qatar|kepler|k2|toi|corot|ngts|wts|kps|mascara|koi|"
                        r"trappist|wendelstein|dmpp)[- ]?\d", re.IGNORECASE)


def name_variants(name: str) -> list[str]:
    """Spellings the Archive might use for a typed planet name, most likely first:
    "wasp-12b" → "WASP-12 b"; "TrES-3b" → "TrES-3 b"; "toi 2046 b" → "TOI-2046 b". Ported from SHOBS-P's
    _name_variants, plus the hyphen/space swap for the survey names."""
    clean = _clean(name)
    if not clean:
        return []
    base = [clean]
    if len(clean) > 2 and clean[-1].isalpha() and clean[-2].isdigit():
        base.insert(0, clean[:-1] + " " + clean[-1])            # "TrES-3b" -> "TrES-3 b" (the likelier form)
    if clean and not clean[-1].isalpha():
        base.append(clean + " b")
    known = sorted((k.rstrip("-") for k in _PREFIXES), key=len, reverse=True)
    out = []
    for v in base:
        m = None
        for k in known:                          # a known survey prefix ("K2", "HAT-P") before the generic rule
            m = re.match(rf"^({re.escape(k)})([- ]?)(\d.*)$", v, flags=re.IGNORECASE)
            if m:
                break
        if m is None:
            m = re.match(r"^([A-Za-z]+(?:-P)?)([- ]?)(.*)$", v, flags=re.IGNORECASE)
        if m and m.group(3):
            prefix = _PREFIXES.get(m.group(1).lower(), m.group(1).upper())
            rest = m.group(3)
            if len(rest) >= 2 and rest[-1].isalpha() and rest[-2] == " ":
                rest = rest[:-1] + rest[-1].lower()              # the planet letter is lower case
            seps = [m.group(2)]
            if rest[:1].isdigit():
                seps = (["-", " "] if prefix in _HYPHEN_SURVEYS else [" ", "-"]) + seps
            for sep in seps:
                out.append(prefix + sep + rest)
        out.append(v)
    return list(dict.fromkeys(out))


def looks_like_planet(name: str) -> bool:
    """A planet letter or TOI decimal at the end ("WASP-12 b", "TOI-2046.01"), or a transit-survey name."""
    c = _clean(name)
    return bool(_PLANETLIKE.search(c) or _SURVEY_RE.match(c))


# ---- planets: NASA Exoplanet Archive -------------------------------------------------------------------------------

def _parse_alias_reply(data) -> dict | None:
    """The Archive's alias service reply → {"resolved": planet or None, "system", "planets", "matched_planet"}."""
    def obj(v):
        return v if isinstance(v, dict) else {}
    data = obj(data)
    manifest = obj(data.get("manifest"))
    if str(manifest.get("lookup_status", "")).upper() != "OK":
        return None
    planets_info = obj(obj(obj(obj(data.get("system")).get("objects")).get("planet_set")).get("planets"))
    planets = sorted(k for k in planets_info if isinstance(k, str))
    resolved = manifest.get("resolved_name") or None
    matched = resolved in planets_info
    if not matched:
        resolved = planets[0] if len(planets) == 1 else None
    return {"resolved": resolved, "system": manifest.get("system_name") or "", "planets": planets,
            "matched_planet": matched}


ALIAS_TIMEOUT_S = 8.0          # the alias service answers known names in seconds but hangs on unknown ones


def nea_alias(name: str) -> dict | None:
    """Alias service; None when it does not know the name. FetchError passes up (a timeout usually just means
    "unknown name": the service hangs on those, so the timeout is short)."""
    text = _get_text(NEA_ALIAS + urllib.parse.urlencode({"objname": _clean(name)}), ALIAS_TIMEOUT_S)
    try:
        return _parse_alias_reply(json.loads(text))
    except ValueError:
        return None


def _transiting(system: str) -> list[str]:
    try:
        rows = nea_query(f"select pl_name, tran_flag from pscomppars where hostname = '{_adql_str(system)}'")
    except FetchError:
        return []
    return [r.get("pl_name", "") for r in rows if _transits(r)]


def _transits(row: dict) -> bool:
    return _f(row.get("tran_flag")) == 1


def _fetch_planet(name: str):
    """Network part of lookup_planet: {"composite": row, "ps": rows} / {"choice": {...}} / None (not known)."""
    clean = _clean(name)
    info = None
    alias_err = None
    try:
        info = nea_alias(clean)
    except FetchError as exc:
        alias_err = exc                      # the alias service is a nicety: try the spellings next
    if info is not None and looks_like_planet(clean) and not info["matched_planet"] and _PLANETLIKE.search(clean):
        info = None                          # "TOI-1234 b" matched only to its star: don't hand back another planet
    if info is not None and info["resolved"] is None and len(info["planets"]) > 1:
        tr = _transiting(info["system"] or clean)
        planets = sorted(info["planets"], key=lambda n: (n not in tr, n))
        return {"choice": {"system": info["system"] or clean, "planets": planets, "transiting": tr}}
    row = None
    if info is not None and info["resolved"]:
        rows = nea_query(f"select {NEA_COLUMNS} from pscomppars where pl_name = '{_adql_str(info['resolved'])}'")
        row = rows[0] if rows else None
    if row is None:
        last_err = None
        for v in name_variants(clean)[:6]:   # one simple query per spelling (Cloudflare dislikes long OR lists)
            try:
                rows = nea_query(f"select {NEA_COLUMNS} from pscomppars where pl_name = '{_adql_str(v)}'")
            except FetchError as exc:
                last_err = exc
                break
            if rows:
                row = rows[0]
                break
        if row is None and last_err is None and info is None:
            # a star name the alias service did not answer for: its planets by host name
            try:
                rows = nea_query(f"select pl_name, tran_flag from pscomppars where hostname = '{_adql_str(clean)}'")
            except FetchError as exc:
                rows, last_err = [], exc
            tr = [r["pl_name"] for r in rows if _transits(r)]
            names = sorted({r["pl_name"] for r in rows}, key=lambda n: (n not in tr, n))
            if len(names) > 1:
                return {"choice": {"system": clean, "planets": names, "transiting": tr}}
            if len(names) == 1:
                rows = nea_query(f"select {NEA_COLUMNS} from pscomppars where pl_name = '{_adql_str(names[0])}'")
                row = rows[0] if rows else None
        if row is None and last_err is not None:
            raise last_err                   # the Archive itself did not answer
        if row is None and alias_err is not None:
            settings.log(f"alias service: {alias_err.reason} for {clean}; the Archive has no such planet name")
    if row is None:
        return None
    try:
        ps = nea_query(f"select {PS_COLUMNS} from ps where pl_name = '{_adql_str(row['pl_name'])}'")
    except FetchError as exc:
        settings.log(f"ps table for {row['pl_name']} not available ({exc.reason}); composite ephemeris only")
        ps = []
    return {"composite": row, "ps": ps}


# Time systems the Archive marks as not barycentric-dynamical (UTC-based, heliocentric, plain JD): a few seconds to
# a minute off BJD_TDB, so such rows rank as if their predicted sigma were 1 minute larger.
def _time_system_doubtful(sysref) -> bool:
    s = str(sysref or "").strip().upper()
    return bool(s) and ("UTC" in s or s.startswith("HJD") or s == "JD")


def _predicted_sigma_days(period, period_err, t0, t0_err, when_jd) -> float:
    if not period or not t0 or period_err is None or t0_err is None:
        return float("inf")
    n = round((when_jd - t0) / period)
    return math.hypot(abs(t0_err), abs(n) * abs(period_err))


def most_precise_ephemeris(ps_rows: list[dict], when_jd: float, composite=None) -> dict | None:
    """Of the published ephemerides (Archive table `ps` rows for one planet with period, t0 and both errors), the
    one whose predicted mid-transit time at when_jd has the smallest uncertainty. composite = (period, period_err,
    t0, t0_err) from the composite table, kept (→ None) unless a row beats it by more than 5 %. Rows whose
    pl_tsystemref is UTC-based, HJD or plain JD rank with 1 minute added to their sigma. Returns
    {"period","period_err","t0","t0_err","reference","sigma_min","n"} or None. Ported from SHOBS-P."""
    cands = []
    for row in ps_rows or []:
        period, t0 = _f(row.get("pl_orbper")), _f(row.get("pl_tranmid"))
        if not period or not t0 or t0 < 2.4e6:
            continue
        perrs = [abs(v) for v in (_f(row.get("pl_orbpererr1")), _f(row.get("pl_orbpererr2"))) if v is not None]
        terrs = [abs(v) for v in (_f(row.get("pl_tranmiderr1")), _f(row.get("pl_tranmiderr2"))) if v is not None]
        if not perrs or not terrs:
            continue
        perr, terr = max(perrs), max(terrs)
        if perr <= 0 or terr <= 0:
            continue
        sigma = _predicted_sigma_days(period, perr, t0, terr, when_jd)
        if _time_system_doubtful(row.get("pl_tsystemref")):
            sigma += 1.0 / 1440.0
        cands.append((sigma, {"period": period, "period_err": perr, "t0": t0, "t0_err": terr,
                              "reference": strip_tags(row.get("pl_refname", ""))}))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    sigma, best = cands[0]
    if composite is not None:
        base = _predicted_sigma_days(*composite[:4], when_jd)
        if base <= sigma * 1.05:
            return None
    best["sigma_min"] = sigma * 1440.0
    best["n"] = len(cands)
    return best


def planet_target(data: dict, when_jd: float | None = None) -> Target:
    """Build the transit Target from cached Archive data ({"composite": row, "ps": rows})."""
    row = data["composite"]
    when = sky.now_jd() if when_jd is None else when_jd
    name = row.get("pl_name") or ""
    period, period_err = _f(row.get("pl_orbper")), _f(row.get("pl_orbpererr1"))
    t0, t0_err = _f(row.get("pl_tranmid")), _f(row.get("pl_tranmiderr1"))
    ref = strip_tags(row.get("pl_tranmid_reflink", "")) or strip_tags(row.get("pl_orbper_reflink", ""))
    source = "NEA"
    best = most_precise_ephemeris(data.get("ps") or [], when, (period, period_err, t0, t0_err))
    if best is not None:
        period, period_err, t0, t0_err = best["period"], best["period_err"], best["t0"], best["t0_err"]
        ref = best["reference"] or ref
        source = "NEA (most precise)"
    k = _f(row.get("pl_ratror"))
    dep = _f(row.get("pl_trandep"))
    depth = k * k if k else (dep / 100.0 if dep else None)
    dur = _f(row.get("pl_trandur"))
    pl = Planet(name=name, period=period, period_err=period_err, t0=t0, t0_err=t0_err,
                t14=dur / 24.0 if dur else None, depth=depth, reference=ref, source=source)
    return Target(name=name, kind="transit", mode="T", ra=_f(row.get("ra")), dec=_f(row.get("dec")),
                  vmag=_f(row.get("sy_vmag")), source=source, planet=pl.__dict__.copy(), added=today_iso())


def lookup_planet(name: str, when_jd: float | None = None, refresh: bool = False) -> Target:
    """A transiting planet from the NASA Exoplanet Archive (see the module docstring). Raises PlanetChoice for a
    star with several planets, NotFound when the Archive does not know the name, LookupError_ when it cannot be
    reached and nothing is cached, and NeedsMode (with the Archive's position and V) for a planet that does not
    transit or has no transit ephemeris. The returned Target carries .lookup_note (cache age note or "")."""
    clean = _clean(name)
    if not clean:
        raise NotFound("Type a planet name first.")
    data, note = cached_lookup("NEA", f"nea:planet:{_key_name(clean)}", clean, PLANET_MAX_AGE_DAYS,
                               lambda: _fetch_planet(clean), refresh)
    if not data:
        raise NotFound(f"The NASA Exoplanet Archive has no planet called {clean}. Check the spelling "
                       "(for example WASP-12 b, TOI-2046 b), or type the planet's values by hand.")
    if "choice" in data:
        c = data["choice"]
        raise PlanetChoice(c["system"], c["planets"], c.get("transiting"))
    t = planet_target(data, when_jd)
    t.lookup_note = note
    row = data["composite"]
    flag = row.get("tran_flag")
    no_transit = flag not in (None, "") and not _transits(row)
    if no_transit or not t.planet or not t.planet.get("t14") or not t.planet.get("t0") or not t.planet.get("period"):
        why = "a planet that does not transit" if no_transit else "a planet with no transit ephemeris"
        if t.ra is None or t.dec is None:
            raise NotFound(f"The NASA Exoplanet Archive lists {t.name} as {why}, with no position.")
        raise NeedsMode(clean, t.ra, t.dec, t.vmag, t.name, why, source="the NASA Exoplanet Archive")
    return t


# ---- a star's planets (for "no data while a planet transits") ------------------------------------------------------

HOST_CONE_ARCSEC = 60.0        # SIMBAD's J2000 position vs the Archive's (Gaia epoch): ~30″ for a fast star
_STAR_PREFIX = re.compile(r"^(?:\*\*|\*|V\*|NAME|EM\*)\s+", re.IGNORECASE)


def host_name_variants(name: str) -> list[str]:
    """Spellings the Archive might use for a host star name: "HD219134" → "HD 219134"; "wasp-12" → "WASP-12";
    SIMBAD's "* 51 Peg" → "51 Peg". The Archive's usual spelling first (it is the one that answers, so an offline
    lookup costs one call, not two), the name as typed last; at most four. The first one is also the cache key."""
    clean = _STAR_PREFIX.sub("", _clean(name))
    if not clean:
        return []
    out = []
    m = re.match(r"^([A-Za-z]+(?:-[Pp])?)[- ]?(\d.*)$", clean)
    if m:
        word, rest = m.group(1), m.group(2)
        prefix = _PREFIXES.get(word.lower(), word.upper() if len(word) <= 3 else word)
        seps = ["-", " "] if prefix in _HYPHEN_SURVEYS else [" ", "-"]
        out += [prefix + sep + rest for sep in seps]
    out.append(clean)
    return list(dict.fromkeys(out))[:4]


def _host_rows_by_name(name: str) -> tuple[str, list[dict]]:
    for v in host_name_variants(name):
        rows = nea_query(f"select {NEA_COLUMNS} from pscomppars where hostname = '{_adql_str(v)}'")
        if rows:
            return (rows[0].get("hostname") or v), rows
    return "", []


def _host_rows_by_position(ra: float, dec: float, vmag=None) -> tuple[str, list[dict]]:
    """The planet host nearest to (ra, dec) within HOST_CONE_ARCSEC. A plain RA/Dec box (the Archive's Cloudflare
    front refuses CONTAINS/POINT/CIRCLE queries), then the true separation; a host whose V differs from the star's
    by more than 1.5 mag is another star."""
    r = HOST_CONE_ARCSEC / 3600.0
    dra = min(180.0, r / max(0.01, math.cos(math.radians(min(89.9, abs(dec))))))
    lo, hi = ra - dra, ra + dra
    spans = [(lo, hi)] if lo >= 0 and hi < 360 else [(lo % 360, 360.0), (0.0, hi % 360)]
    rows = []
    for a, b in spans:
        rows += nea_query(f"select {NEA_COLUMNS} from pscomppars where ra between {a:.6f} and {b:.6f} "
                          f"and dec between {dec - r:.6f} and {dec + r:.6f}")
    best, best_sep = "", None
    for row in rows:
        pra, pdec = _f(row.get("ra")), _f(row.get("dec"))
        if pra is None or pdec is None:
            continue
        sep = float(sky.sep_deg(ra, dec, pra, pdec)) * 3600.0
        pv = _f(row.get("sy_vmag"))
        if sep > HOST_CONE_ARCSEC or (vmag is not None and pv is not None and abs(pv - float(vmag)) > 1.5):
            continue
        if best_sep is None or sep < best_sep:
            best, best_sep = row.get("hostname") or "", sep
    return best, [row for row in rows if best and row.get("hostname") == best]


def _fetch_host(name: str, ra, dec, vmag):
    host, rows = _host_rows_by_name(name) if name else ("", [])
    matched = "name" if rows else ""
    if not rows and ra is not None and dec is not None:
        host, rows = _host_rows_by_position(float(ra), float(dec), vmag)
        matched = "position" if rows else ""
    rows = [dict(r) for r in rows]
    for row in rows:                         # the most precise published ephemeris, as lookup_planet uses
        if _transits(row) and row.get("pl_name"):
            try:
                row["_ps"] = nea_query(f"select {PS_COLUMNS} from ps where pl_name = '{_adql_str(row['pl_name'])}'")
            except FetchError as exc:
                settings.log(f"ps table for {row['pl_name']} not available ({exc.reason}); composite ephemeris only")
    return {"host": host, "matched": matched, "rows": rows}


def host_planet_entry(row: dict) -> dict:
    """One Archive row → {"name", "transits", "usable", "why", "planet"}: usable = a transiting planet with period,
    Tc and duration (its no-transit window can be computed); planet = its Planet fields (composite ephemeris)."""
    t = planet_target({"composite": row, "ps": row.get("_ps") or []}, sky.now_jd())
    pl = t.planet or {}
    transits = _transits(row)
    usable = transits and bool(pl.get("period") and pl.get("t0") and pl.get("t14"))
    why = "" if usable else ("does not transit" if not transits else "no transit ephemeris at the Archive")
    return {"name": row.get("pl_name") or "", "transits": transits, "usable": usable, "why": why,
            "planet": pl if usable else None}


def host_planets(name: str, ra=None, dec=None, vmag=None, refresh: bool = False) -> dict:
    """The known planets of a star at the NASA Exoplanet Archive, by host name (its spellings: "HD219134" finds
    "HD 219134"), else by position (nearest host within HOST_CONE_ARCSEC). Returns {"host", "matched" ("name",
    "position" or "" = no known planets), "planets": [host_planet_entry ...] transiting first, "note"} — cached a
    week like the planet lookups; LookupError_ when the Archive does not answer and nothing is cached."""
    clean = _clean(name)
    spellings = host_name_variants(clean) if clean else []
    if spellings:                    # keyed on the normalized name: "HD219134" and "hd 219134" share one copy
        key = f"nea:host:{_key_name(spellings[0])}"
    elif ra is not None and dec is not None:
        key = f"nea:host:@{float(ra):.4f},{float(dec):+.4f}"
    else:
        raise NotFound("Type a star name first.")
    data, note = cached_lookup("NEA", key, clean or "this position", PLANET_MAX_AGE_DAYS,
                               lambda: _fetch_host(clean, ra, dec, vmag), refresh)
    data = data or {}
    seen, planets = set(), []
    for row in data.get("rows") or []:
        e = host_planet_entry(row)
        if e["name"] and e["name"] not in seen:
            seen.add(e["name"])
            planets.append(e)
    planets.sort(key=lambda e: (not e["usable"], not e["transits"], e["name"]))
    return {"host": data.get("host") or "", "matched": data.get("matched") or "", "planets": planets,
            "note": note}


# ---- variables: AAVSO VSX, VizieR B/vsx ---------------------------------------------------------------------------

PULSATOR_PREFIXES = ("DSCT", "HADS", "SXPHE", "RR", "CEP", "DCEP", "CW", "BCEP", "ROA", "GDOR", "SPB", "ACYG",
                     "BLAP", "ZZ", "V361HYA", "V1093HER", "BLBOO", "ACEP", "PPN", "PVTEL", "RV", "LPB", "SDOR")
ECLIPSING_PREFIXES = ("EA", "EB", "EW", "EP", "ELL", "E")
LONG_PERIOD_PREFIXES = ("M", "SR", "L", "I", "UV", "GCAS", "BE", "RCB", "YSO", "INS", "FU")


def classify_vtype(vtype: str) -> str:
    """'pulsator', 'eclipsing', 'long' (long-period/irregular) or 'other' from a VSX type such as "EA/SD",
    "EA+GDOR", "HADS:", "RRAB". A combined type with an eclipsing part counts as eclipsing (timed minima)."""
    parts = [p.strip().rstrip(":").upper() for p in re.split(r"[+|,]", vtype or "") if p.strip()]
    kinds = []
    for p in parts:
        head = p.split("/")[0].strip("()")
        if head.startswith(("EA", "EB", "EW", "EP", "ELL")) or head == "E":
            kinds.append("eclipsing")
        elif head.startswith(PULSATOR_PREFIXES):
            kinds.append("pulsator")
        elif head.startswith(LONG_PERIOD_PREFIXES):
            kinds.append("long")
        else:
            kinds.append("other")
    for k in ("eclipsing", "pulsator", "long"):
        if k in kinds:
            return k
    return "other"


def _parse_mag(text) -> tuple[float | None, str, bool]:
    """'10.56 V' → (10.56, 'V', False); '(0.56) V' → (0.56, 'V', True = an amplitude); '<15.2 V' → (15.2, 'V', False)."""
    s = str(text or "").strip()
    if not s:
        return None, "", False
    amp = s.startswith("(")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None, "", amp
    band = s[m.end():].replace(")", "").strip().split(" ")[0] if s[m.end():].strip() else ""
    return float(m.group(0)), band, amp


def parse_vsx_json(text: str) -> dict | None:
    """AAVSO VSX api.object JSON → the common variable dict, or None when VSX does not know the name
    ({"VSXObject": []}). Documented fields: Name, RA2000, Declination2000, VariabilityType, Period, Epoch,
    MaxMag ("10.00 V"), MinMag ("10.56 V" or "(0.56) V" for an amplitude), EclipseDuration (% of the period, for
    eclipsing binaries), RiseDuration (pulsators)."""
    try:
        data = json.loads(text)
    except ValueError:
        raise FetchError("an unexpected reply came back instead of data") from None
    obj = data.get("VSXObject") if isinstance(data, dict) else None
    if not isinstance(obj, dict) or not obj.get("Name"):
        return None
    mx, band, _ = _parse_mag(obj.get("MaxMag"))
    mn, band2, amp = _parse_mag(obj.get("MinMag"))
    if amp and mn is not None and mx is not None:
        mn = mx + mn
    return {"name": _clean(obj.get("Name")), "ra": _f(obj.get("RA2000")), "dec": _f(obj.get("Declination2000")),
            "vtype": str(obj.get("VariabilityType") or "").strip(), "period": _f(obj.get("Period")),
            "epoch": _f(obj.get("Epoch")), "max": mx, "min": mn, "band": band or band2,
            "eclipse_pct": _f(obj.get("EclipseDuration")), "auid": str(obj.get("AUID") or ""), "via": "VSX"}


def parse_vizier_vsx_row(row: dict) -> dict:
    """One B/vsx/vsx CSV row (space-padded strings) → the common variable dict. f_min "(" = min is an amplitude."""
    g = lambda k: str(row.get(k, "") or "").strip()
    mx, mn = _f(g("max")), _f(g("min"))
    # MinIsAmplitude: "(" in the ASU text output, "Y" in today's TAP/ASU replies (both seen)
    if g("f_min") in ("(", "Y") and mx is not None and mn is not None:
        mn = mx + mn
    return {"name": _clean(g("Name")), "ra": _f(g("RAJ2000")), "dec": _f(g("DEJ2000")), "vtype": g("Type"),
            "period": _f(g("Period")), "epoch": _f(g("Epoch")), "max": mx, "min": mn, "band": g("n_max"),
            "eclipse_pct": None, "auid": "", "via": "VizieR VSX"}


def vsx_name_variants(name: str) -> list[str]:
    """VSX spellings: "V404 Lyr" → "V0404 Lyr" (VSX pads V-numbers to four digits), constellation capitalized."""
    c = _clean(name)
    out = [c]
    m = re.match(r"^(?i:v)\s?(\d{1,4})\s+([A-Za-z]{3})$", c)
    if m:
        out.append(f"V{int(m.group(1)):04d} {m.group(2).capitalize()}")
    m = re.match(r"^([A-Za-z]{1,2})\s+([A-Za-z]{3})$", c)
    if m and m.group(1).lower() not in ("v",):
        out.append(f"{m.group(1).upper()} {m.group(2).capitalize()}")
    return list(dict.fromkeys(out))


# VizieR's plain (ASU) interface runs on other servers than its TAP service: when TAP is down (HTTP 503 "No server
# is available", seen 22 Oct 2026 while ASU answered) the same B/vsx rows come from ASU at CDS, then the CfA mirror.
VIZIER_ASU = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?", "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv?")


def parse_asu_tsv(text: str) -> list[dict]:
    """VizieR ASU TSV → rows keyed by column name (comment lines, the units line and the dashes line skipped)."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines or "\t" not in lines[0]:
        raise FetchError("an unexpected reply came back instead of data")
    head = [h.strip() for h in lines[0].split("\t")]
    if "Name" not in head:
        raise FetchError("an unexpected reply came back instead of data")
    k = head.index("Name")
    rows = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        name = cells[k].strip() if k < len(cells) else ""
        if not name or set(name) <= {"-"}:
            continue                          # the units line and the dashes line
        rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(head)})
    return rows


def vizier_asu(params: dict) -> list[dict]:
    """B/vsx/vsx rows from VizieR ASU (CDS, then the CfA mirror). FetchError only when both fail."""
    q = {"-source": "B/vsx/vsx", "-out.all": "", "-oc.form": "d", "-out.max": "20"}
    q.update(params)
    last = None
    for base in VIZIER_ASU:
        try:
            return parse_asu_tsv(_get_text(base + urllib.parse.urlencode(q)))
        except FetchError as exc:
            last = exc
    raise last if last else FetchError("no answer")


def _vizier_rows_by_name(v: str) -> list[dict]:
    try:
        return vizier_query(f"SELECT TOP 5 * FROM \"B/vsx/vsx\" WHERE Name = '{_adql_str(v)}'")
    except FetchError as exc:
        settings.log(f"VizieR TAP did not answer ({exc.reason}); trying VizieR ASU")
        return vizier_asu({"Name": v})


def _vizier_by_name(name: str) -> dict | None:
    for v in vsx_name_variants(name):
        rows = _vizier_rows_by_name(v)
        for r in rows:
            if str(r.get("Name", "")).strip().lower() == v.lower():
                return parse_vizier_vsx_row(r)
    return None


def _vizier_cone(ra: float, dec: float, radius_deg: float = 1.0 / 60.0) -> dict | None:
    try:
        rows = vizier_query("SELECT TOP 20 * FROM \"B/vsx/vsx\" WHERE 1=CONTAINS(POINT('ICRS',RAJ2000,DEJ2000),"
                            f"CIRCLE('ICRS',{ra:.6f},{dec:.6f},{radius_deg:.6f}))")
    except FetchError as exc:
        settings.log(f"VizieR TAP did not answer ({exc.reason}); trying VizieR ASU")
        rows = vizier_asu({"-c": f"{ra:.6f} {dec:+.6f}", "-c.rs": f"{radius_deg * 3600.0:.1f}"})
    best, best_sep = None, None
    for r in rows:
        p = parse_vizier_vsx_row(r)
        if p["ra"] is None or p["dec"] is None:
            continue
        s = float(sky.sep_deg(ra, dec, p["ra"], p["dec"]))
        if s <= radius_deg and (best_sep is None or s < best_sep):
            best, best_sep = p, s
    return best


VSX_TIMEOUT_S = 10.0           # VSX often answers 504 after ~13 s; VizieR's copy is the quicker way on


# SIMBAD short object types that are groups of stars, not one star (a variable within 1' of a cluster centre is not
# the cluster): clusters, associations, asterisms, groups, double/multiple stars, moving groups, streams.
_GROUP_OTYPE_PREFIXES = ("Cl", "OpC", "GlC", "As", "Gr", "**", "C?", "MGr", "St*", "SCG", "PoC", "SC", "Gl?")


def is_single_star_otype(short: str) -> bool:
    """True for a SIMBAD short type that is one star ("*", "SB*", "EB*", "V*", "Mi*", "RR*"...)."""
    s = str(short or "").strip()
    return bool(s) and "*" in s and not s.startswith(_GROUP_OTYPE_PREFIXES)


def _fetch_variable(name: str):
    clean = _clean(name)
    try:                                     # one VSX call: VSX resolves other names itself ("Algol" → bet Per)
        text = _get_text(VSX_API + urllib.parse.urlencode({"view": "api.object", "ident": clean, "format": "json"}),
                         VSX_TIMEOUT_S)
        return parse_vsx_json(text)          # None: VSX answered that it does not know the name
    except FetchError as exc:
        vsx_reason = exc
    settings.log(f"VSX did not answer for {clean} ({vsx_reason.reason}); trying VizieR's copy")
    try:
        got = _vizier_by_name(clean)         # FetchError here → both down → cache / LookupError_
    except FetchError as exc:                # name both causes, so the message doesn't pin VizieR's 503 on VSX
        raise FetchError(f"{vsx_reason.reason}; VizieR's copy: {exc.reason}", exc.status) from None
    if got:
        return got
    not_found = NotFound(f"VSX did not answer ({vsx_reason.reason}) and VizieR's copy of VSX has no star called "
                         f"{clean}. Check the spelling, or try again later.")
    try:
        s = resolve_simbad(clean)
    except LookupError_:
        raise not_found from None
    if s.get("ra") is None or not is_single_star_otype(s.get("otype_short")):
        raise not_found                      # a cluster, a galaxy, a double star: a variable nearby is not it
    got = _vizier_cone(s["ra"], s["dec"])
    if not got or got.get("vtype", "").upper().startswith("CST") or not got.get("period"):
        raise not_found                      # a constant or period-less entry found only by position: not it
    got["matched_by_position"] = True
    got["simbad_id"] = s.get("main_id", "")
    got["sep_arcsec"] = round(float(sky.sep_deg(s["ra"], s["dec"], got["ra"], got["dec"])) * 3600.0, 1)
    return got


def elements_from_vsx(v: dict) -> VarElements:
    """The common variable dict → VarElements (rules in the spec: pulsators and long-period any time from maxima;
    eclipsing timed minima with ±60 min, or ±0.6 × the eclipse duration when VSX gives it, at most ±240 min)."""
    kind = classify_vtype(v.get("vtype", ""))
    el = VarElements(vtype=v.get("vtype", ""), period=v.get("period"), epoch=v.get("epoch"),
                     mag_max=v.get("max"), mag_min=v.get("min"))
    if kind == "eclipsing":
        el.epoch_kind, el.any_time = "min", False
        pct, p = v.get("eclipse_pct"), v.get("period")
        if pct and p and 0 < pct <= 100:
            el.event_halfwidth_min = round(min(240.0, max(20.0, 0.6 * pct / 100.0 * p * 1440.0)), 1)
        if not (el.period and el.epoch):
            el.any_time = True               # no elements: nothing to time
    else:
        el.epoch_kind, el.any_time = "max", True
    return el


def lookup_variable(name: str, refresh: bool = False) -> Target:
    """A variable star from AAVSO VSX (or VizieR's copy). NotFound when VSX does not know it; LookupError_ when
    nothing answers and nothing is cached. The Target carries .lookup_note."""
    clean = _clean(name)
    if not clean:
        raise NotFound("Type a star name first.")
    data, note = cached_lookup("VSX", f"vsx:variable:{_key_name(clean)}", clean, VSX_MAX_AGE_DAYS,
                               lambda: _fetch_variable(clean), refresh)
    if not data:
        raise NotFound(f"VSX has no variable star called {clean}. Check the spelling (for example DY Peg, "
                       "V0404 Lyr).")
    el = elements_from_vsx(data)
    t = Target(name=data.get("name") or clean, kind="variable", mode="V", ra=data.get("ra"), dec=data.get("dec"),
               vmag=data.get("max"), source=data.get("via") or "VSX", elements=el.__dict__.copy(),
               added=today_iso())
    if data.get("matched_by_position"):
        pos = (f"found by position: VSX's {t.name} is {data.get('sep_arcsec', 0):.0f}″ from SIMBAD's "
               f"{data.get('simbad_id') or clean}")
        note = f"{note}; {pos}" if note else pos
    t.lookup_note = note
    return t


# ---- SIMBAD --------------------------------------------------------------------------------------------------------

_SIMBAD_FMT = 'format object "%IDLIST(1)|%COO(d;A D)|%FLUXLIST(V;F)|%OTYPE(S)|%OTYPE(3)"'


def parse_simbad_script(text: str) -> dict | None:
    """sim-script output "V* DY Peg|347.21327 +17.21555|10.26|SXPheV*|SX*" → dict; None for SIMBAD's '::error::'
    (it does not know the name). Anything else unreadable (a web page, a throttling notice, a cut-off reply) is a
    FetchError, so a cached copy or a plain "SIMBAD did not answer" follows, never "not found"."""
    if "::error::" in text:
        return None
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        coo = parts[1].split()
        if len(coo) != 2:
            continue
        ra, dec = _f(coo[0]), _f(coo[1])
        if ra is None or dec is None:
            continue
        return {"main_id": " ".join(parts[0].split()), "ra": ra, "dec": dec, "vmag": _f(parts[2].strip()),
                "otype": parts[3].strip() if len(parts) > 3 else "",
                "otype_short": parts[4].strip() if len(parts) > 4 else ""}
    raise FetchError("SIMBAD sent a reply that could not be read")


def _fetch_simbad(name: str):
    script = f"output console=off script=off\n{_SIMBAD_FMT}\nquery id {_clean(name)}\n"
    return parse_simbad_script(_get_text(SIMBAD_SCRIPT + urllib.parse.urlencode({"script": script})))


def resolve_simbad(name: str, refresh: bool = False) -> dict:
    """{main_id, ra, dec (J2000 degrees), vmag (None if SIMBAD has no V), otype}. NotFound / LookupError_."""
    clean = _clean(name)
    if not clean:
        raise NotFound("Type a name first.")
    data, note = cached_lookup("SIMBAD", f"simbad:object:{_key_name(clean)}", clean, SIMBAD_MAX_AGE_DAYS,
                               lambda: _fetch_simbad(clean), refresh)
    if not data:
        raise NotFound(f"SIMBAD does not know {clean}. Check the spelling, or type the coordinates by hand.")
    out = dict(data)
    out["note"] = note
    return out


# ---- add a target --------------------------------------------------------------------------------------------------

def add_target(name: str, when_jd: float | None = None) -> Target:
    """Planet first (when the name looks like one, or the Archive knows it), else a VSX variable, else SIMBAD →
    NeedsMode. Raises PlanetChoice (several planets), NeedsMode, NotFound or LookupError_. When the Archive or
    VSX/VizieR did not answer, the result is LookupError_ with that cause, never NeedsMode (the name may well be a
    planet or a variable). VSX down but VizieR's copy answering counts as answered."""
    clean = _clean(name)
    if not clean:
        raise NotFound("Type a name first.")
    problems = []
    planetish = looks_like_planet(clean)

    def try_planet():
        try:
            return lookup_planet(clean, when_jd)
        except NotFound:
            return None
        except LookupError_ as exc:
            problems.append(exc.message)
            return None

    if planetish:
        t = try_planet()
        if t:
            return t
    else:
        # A star name the Archive knows (a planet host) → its planet, or the choice of planets. One quick host-name
        # query decides, so "DY Peg" costs a single Archive call (the alias service hangs on unknown names).
        try:
            hosts = nea_query(f"select pl_name from pscomppars where hostname = '{_adql_str(clean)}'")
        except FetchError as exc:
            hosts = []
            problems.append(f"The NASA Exoplanet Archive did not answer ({exc.reason}).")
        if hosts:
            t = try_planet()
            if t:
                return t
    try:
        return lookup_variable(clean)
    except NotFound:
        pass
    except LookupError_ as exc:
        problems.append(exc.message)
    try:
        s = resolve_simbad(clean)
    except NotFound:
        if problems:
            raise LookupError_(" ".join(problems)) from None
        raise NotFound(f"No planet, variable star or SIMBAD object is called {clean}. Check the spelling.") from None
    except LookupError_ as exc:
        problems.append(exc.message)
        raise LookupError_(" ".join(problems)) from None
    if problems:
        # a service that should have known the name did not answer: say so rather than ask for a mode
        raise LookupError_(" ".join(problems) + f" (SIMBAD knows {clean} as {s['main_id']}.)")
    if str(s.get("otype_short") or "").startswith("Pl") or "planet" in str(s.get("otype") or "").lower():
        raise NotFound(f"SIMBAD lists {clean} ({s['main_id']}) as a planet, but the NASA Exoplanet Archive has no "
                       "transit ephemeris under that name. Try its Archive name (for example WASP-12 b), or type "
                       "the planet's values by hand.")
    raise NeedsMode(clean, s["ra"], s["dec"], s.get("vmag"), s["main_id"], s.get("otype", ""))


# ---- the transit catalog --------------------------------------------------------------------------------------------

def parse_catalog_csv(text: str) -> list[dict]:
    """Archive CSV (CATALOG_COLUMNS) → catalog dicts with period, t0 (BJD_TDB), t14 (days), depth, ra, dec, vmag
    and the errors. Planets without period, t0, duration or position are left out."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        p = {"name": (row.get("pl_name") or "").strip(), "host": (row.get("hostname") or "").strip(),
             "period": _f(row.get("pl_orbper")), "period_err": _f(row.get("pl_orbpererr1")),
             "t0": _f(row.get("pl_tranmid")), "t0_err": _f(row.get("pl_tranmiderr1")),
             "t14": (_f(row.get("pl_trandur")) or 0) / 24.0 or None,
             "depth": (_f(row.get("pl_trandep")) or 0) / 100.0 or None, "k": _f(row.get("pl_ratror")),
             "ra": _f(row.get("ra")), "dec": _f(row.get("dec")), "vmag": _f(row.get("sy_vmag"))}
        if p["k"]:
            p["depth"] = p["k"] ** 2           # (Rp/R*)², as SHOBS-P does
        if p["period"] and p["t0"] and p["t14"] and p["ra"] is not None and p["dec"] is not None:
            out.append(p)
    return out


def _fetch_catalog():
    url = NEA_TAP + urllib.parse.urlencode({"query": f"select {CATALOG_COLUMNS} from pscomppars where tran_flag = 1",
                                            "format": "csv"})
    text = _get_text(url, timeout=120)
    if text.lstrip().startswith("<"):
        raise FetchError("the Archive sent a web page instead of the planet list")
    rows = parse_catalog_csv(text)
    if not rows:
        raise FetchError("the Archive sent an empty planet list")
    return {"rows": rows}


def load_transit_catalog(refresh: bool = False) -> tuple[list[dict], str]:
    """Every transiting planet with period, t0 and duration (Archive composite table), cached in cache_dir and
    refreshed weekly; a failed refresh uses the saved copy. Returns (planets, note). LookupError_ if there is
    neither an answer nor a saved copy."""
    data, note = cached_lookup("NEA catalog", "nea:catalog:transiting", "the transiting-planet list",
                               CATALOG_MAX_AGE_DAYS, _fetch_catalog, refresh)
    rows = list((data or {}).get("rows") or [])
    if not note:
        _d, age = cache_read("NEA catalog", "nea:catalog:transiting")
        note = ("downloaded today from the NASA Exoplanet Archive" if (age or 0) < 1
                else f"saved copy from {_age_words(age)}")
    return rows, note


# ---- Suggested transits ---------------------------------------------------------------------------------------------

V_ZERO_PHOTONS = 8.8e5        # photons s⁻¹ cm⁻² from a V = 0 star in the V band (≈1000 ph/s/cm²/Å × 880 Å)
SYSTEM_EFFICIENCY = 0.4       # optics × filter/Bayer × atmosphere, on top of the camera QE (a rough proxy)


def noise_per_minute(vmag: float, sky_v: float, optics, airmass: float = 1.3, fwhm_arcsec: float = 3.0,
                     elevation_m: float = 0.0) -> float:
    """Relative flux noise of one minute of data (a documented proxy, not an exposure calculator):
    photon noise of star + sky in an aperture of radius 2 × FWHM, plus Young's scintillation
    0.09 D^(-2/3) X^1.75 e^(-h/8000) / sqrt(2 t) (D in cm, t = 60 s). Aperture area allows a 30 % obstruction."""
    d_cm = (float(getattr(optics, "aperture_mm", None) or 200.0)) / 10.0
    area = math.pi * (d_cm / 2) ** 2 * (1 - 0.3 ** 2)
    eff = float(getattr(optics, "qe", None) or 0.8) * SYSTEM_EFFICIENCY
    t = 60.0
    star = V_ZERO_PHOTONS * area * eff * 10 ** (-0.4 * vmag) * t
    ap_area = math.pi * (2.0 * fwhm_arcsec) ** 2
    skyc = V_ZERO_PHOTONS * area * eff * 10 ** (-0.4 * sky_v) * ap_area * t
    phot = math.sqrt(star + skyc) / star if star > 0 else 1.0
    scint = 0.09 * d_cm ** (-2.0 / 3.0) * airmass ** 1.75 * math.exp(-elevation_m / 8000.0) / math.sqrt(2 * t)
    return math.hypot(phot, scint)


def tc_sigma_minutes(planet, when_jd: float) -> float | None:
    """Predicted mid-transit uncertainty (minutes) at when_jd from the ephemeris errors; None when the ephemeris
    has no errors (a missing one of the two counts as 0, as events.transit_events does)."""
    if planet is None:
        return None
    p = planet if isinstance(planet, Planet) else Planet.from_dict(planet if isinstance(planet, dict) else {})
    if not p.period or not p.t0 or (p.t0_err is None and p.period_err is None):
        return None
    n = round((when_jd - float(p.t0)) / float(p.period))
    return math.hypot(abs(p.t0_err or 0.0), abs(n) * abs(p.period_err or 0.0)) * 1440.0


def _fmt_day(jd: float, site) -> str:
    t = sky.to_local(jd, site.tz)
    return f"{t.strftime('%a')} {t.day} {t.strftime('%b')}"


def _night_label(d: date, site) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


COMPOSITE_REF = "Archive composite (planet list)"


def precise_planet(name: str, when_jd: float | None = None) -> dict | None:
    """The planet's most precise published ephemeris (lookup_planet: Archive `ps` rows, cached) as a Planet dict, or
    None when the Archive cannot say. The composite table's ephemeris can be years old (TOI-4329 b: Grunblatt 2022,
    ±190 min in 2026, vs ExoFOP ±2 min), so suggestions and kept transits use this instead (John, 22 Oct)."""
    try:
        t = lookup_planet(name, when_jd)
    except Exception:
        return None
    return dict(t.planet) if t is not None and t.planet and t.planet.get("period") and t.planet.get("t0") else None


def refine_transit_target(t: Target, when_jd: float | None = None) -> bool:
    """Replace a transit target's composite-table ephemeris with the most precise one (keeps its depth/duration
    when the lookup lacks them). True when the ephemeris changed."""
    if t.kind != "transit" or not t.planet:
        return False
    pl = precise_planet(t.planet.get("name") or t.name, when_jd)
    if not pl:
        return False
    new = dict(t.planet)
    for k in ("period", "period_err", "t0", "t0_err", "reference", "source"):
        if pl.get(k) is not None:
            new[k] = pl[k]
    for k in ("t14", "depth"):
        if not new.get(k) and pl.get(k):
            new[k] = pl[k]
    changed = any(new.get(k) != t.planet.get(k) for k in ("period", "t0", "period_err", "t0_err", "reference"))
    t.planet = new
    return changed


def suggest_transits(site, optics, options, nights: list, exclude_names=frozenset(), catalog: list | None = None,
                     progress=None, refine: bool = False) -> list[Target]:
    """Other good transits in the given nights, in John's order (thresholds from options):
      1. FULL transit: ingress − suggest_baseline_min … egress + suggest_baseline_min above every limit (sky.up_mask);
         the transit itself (ingress…egress) inside the planning darkness (site.sun_alt); the baselines may run
         into nautical twilight (Sun ≤ −12°, or the planning darkness if that is brighter), as the planner does
         (rule 2 in SPEC B: the plan doc's HAT-P-23 b baseline starts in twilight);
      2. host V in [suggest_vmin, suggest_vmax];  3. depth ≥ suggest_min_depth;
      4. mean sky.moon_sky_penalty over the window ≤ suggest_moon_penalty_max;
      5. ranked by S/N = depth × sqrt(minutes in transit) / noise_per_minute(V, Moon-brightened sky, airmass),
         × (1 + 0.5 × min(1, Tc sigma / 30 min)) — new timings of an uncertain ephemeris add the most (no errors
         given: the half bonus).
    Each planet once (its best night); at most suggest_max. catalog = load_transit_catalog()'s rows (loaded when
    None). progress(i, n) is called per planet when given. refine=True: the shortlist (2 × suggest_max) is checked
    again with each planet's most precise ephemeris (precise_planet) before the final ranking, so a stale composite
    ephemeris neither offers a transit that is not there nor hides one that is."""
    o = dict(settings.DEFAULT_OPTIONS)
    o.update(options or {})
    if catalog is None:
        catalog, _note = load_transit_catalog()
    if not site or not site.complete() or not nights:
        return []
    vmin, vmax = float(o["suggest_vmin"]), float(o["suggest_vmax"])
    min_depth = float(o["suggest_min_depth"])
    base = float(o["suggest_baseline_min"]) / 1440.0
    moon_max = float(o["suggest_moon_penalty_max"])
    warn = float(o["tc_sigma_warn_min"])
    dark_v = float(o.get("dark_sky_v", sky.DARK_SKY_V))
    fwhm = float(o.get("fwhm_arcsec", 3.0))
    excl = {_key_name(n) for n in (exclude_names or ())}

    wins = []                                   # (date, dark0, dark1, base0, base1)
    base_level = max(float(site.sun_alt), sky.NAUTICAL)
    for d in nights:
        w = sky.night_window(d, site)
        if w is None:
            continue
        wb = sky.night_window(d, site, base_level) or w
        wins.append((d, w[0], w[1], min(wb[0], w[0]), max(wb[1], w[1])))
    if not wins:
        return []
    jd_lo, jd_hi = min(x[3] for x in wins), max(x[4] for x in wins)
    floor = max(float(site.min_alt or 0.0), sky.alt_for_airmass(site.max_airmass) if site.max_airmass else 0.0)

    best: dict[str, tuple] = {}
    first: dict[str, tuple] = {}
    cands = [p for p in catalog if p.get("vmag") is not None and vmin <= p["vmag"] <= vmax
             and (p.get("depth") or 0) >= min_depth and p.get("t14") and _key_name(p.get("name")) not in excl
             and sky.culmination_alt(p["dec"], site.lat) >= floor]

    def score_planet(p, best):
        pl = Planet(name=p["name"], period=p["period"], period_err=p.get("period_err"), t0=p["t0"],
                    t0_err=p.get("t0_err"), t14=p["t14"], depth=p["depth"])
        for ev in events.transit_events(pl, p["ra"], p["dec"], jd_lo, jd_hi):
            s, e = ev["t1"] - base, ev["t4"] + base
            night = next((x for x in wins if x[1] <= ev["t1"] and ev["t4"] <= x[2] and x[3] <= s and e <= x[4]),
                         None)
            if night is None:
                continue
            grid = np.linspace(s, e, max(9, int((e - s) * 1440 / 5) + 1))
            if not bool(np.all(sky.up_mask(p["ra"], p["dec"], site, grid))):
                continue
            pen = float(np.mean(sky.moon_sky_penalty(p["ra"], p["dec"], site, grid, dark_v)))
            if pen > moon_max:
                continue
            x = float(np.nanmean(sky.target_airmass(p["ra"], p["dec"], site, grid)))
            noise = noise_per_minute(p["vmag"], dark_v - pen, optics, x, fwhm, float(site.elevation_m or 0.0))
            snr = p["depth"] * math.sqrt(p["t14"] * 1440.0) / noise
            sig = ev["sigma_min"]
            bonus = 1.0 + 0.5 * (min(1.0, sig / 30.0) if sig is not None else 0.5)
            score = snr * bonus
            k = _key_name(p["name"])
            if k not in best or score > best[k][0]:
                best[k] = (score, p, ev, night[0], sig, pen, snr)
            if k not in first or ev["t1"] < first[k][0]:
                first[k] = (ev["t1"], ev["t4"])           # the soonest good transit: the page lists by it

    n_ref = 2 * int(o["suggest_max"]) if refine else 0
    total = len(cands) + n_ref
    for i, p in enumerate(cands):
        if progress:
            try:
                progress(i, total)
            except Exception:
                pass
        score_planet(p, best)
    if refine:
        keys = [k for k, _v in sorted(best.items(), key=lambda kv: -kv[1][0])[:n_ref]]
        short = [best.pop(k) for k in keys]
        mid = (jd_lo + jd_hi) / 2.0
        for j, entry in enumerate(short):
            if progress:
                try:
                    progress(len(cands) + j, total)
                except Exception:
                    pass
            p = entry[1]
            first.pop(_key_name(p["name"]), None)
            pl = precise_planet(p["name"], mid)
            if pl:
                p = dict(p, period=pl["period"], period_err=pl.get("period_err"), t0=pl["t0"],
                         t0_err=pl.get("t0_err"), reference=pl.get("reference") or "")
            score_planet(p, best)                    # a transit gone with the better ephemeris drops out here
    ranked = sorted(best.values(), key=lambda b: -b[0])[: int(o["suggest_max"])]
    out = []
    for score, p, ev, d, sig, pen, snr in ranked:
        note = (f"{_night_label(d, site)}: ingress {sky.hhmm(ev['t1'], site.tz)}, egress "
                f"{sky.hhmm(ev['t4'], site.tz)}, {sky.local_label(site.tz, ev['t1'])}")
        if sig is not None and sig > warn:
            note += f"; ⚠ Tc uncertain ±{sig:.0f} min"
        pl = Planet(name=p["name"], period=p["period"], period_err=p.get("period_err"), t0=p["t0"],
                    t0_err=p.get("t0_err"), t14=p["t14"], depth=p["depth"],
                    reference=p.get("reference") or COMPOSITE_REF, source="NEA")
        t = Target(name=p["name"], kind="transit", mode="T", ra=p["ra"], dec=p["dec"], vmag=p["vmag"],
                   enabled=False, suggested=True, source="NEA", planet=pl.__dict__.copy(), notes=note,
                   added=today_iso(site))
        f1, f4 = first.get(_key_name(p["name"]), (ev["t1"], ev["t4"]))
        t.suggest_info = {"night": d.isoformat(), "t1": ev["t1"], "tc": ev["tc"], "t4": ev["t4"], "score": score,
                          "snr": snr, "moon_penalty": pen, "tc_sigma_min": sig, "when": f1,
                          "text": f"{_fmt_day(f1, site)} {sky.hhmm(f1, site.tz)}–{sky.hhmm(f4, site.tz)} (full)"}
        out.append(t)
    return out


# ---- suggested variables (VSX via VizieR B/vsx) --------------------------------------------------------------------

# Type families of the "Find suggested variables" pop-up → VSX type codes, matched as prefixes of the type's first
# part ("EA/SD", "RRAB/BL", "DSCTC", "SRB", "CWA"); "M" (Mira) only as itself (MISC is not a Mira). A trailing ":"
# marks an uncertain type (left out unless asked for). "other" = any type outside the five families.
VAR_FAMILIES = (
    ("eclipsing", "Eclipsing binaries (EA, EB, EW)", ("EA", "EB", "EW")),
    ("rrlyr", "RR Lyrae (RRAB, RRC)", ("RRAB", "RRC", "RRD", "RR")),
    ("dsct", "δ Scuti, HADS, SX Phe", ("HADS", "DSCTC", "DSCT", "SXPHE")),
    ("long", "Miras and semi-regulars (M, SR)", ("M", "SR")),
    ("cepheid", "Cepheids (DCEP, CW)", ("DCEP", "CEP", "CW")),
    ("other", "Other types", ()),
)
VAR_FAMILY_NAMES = {k: lab for k, lab, _c in VAR_FAMILIES}
NOT_VARIABLE_TYPES = ("CST", "NON-CV", "VAR", "*", "")       # constant, not a variable, or no type at all
VISUAL_BANDS = ("V", "CV", "Vj", "vis", "VT", "G", "Hp", "g")   # max given in (near-)V light; R, I, W1, Kp... are not
VAR_SEARCH_MAX_ROWS = 3000
VAR_SEARCH_MAX_AGE_DAYS = 1.0
VAR_SEARCH_DEFAULTS = {"families": ["eclipsing", "rrlyr", "dsct"], "uncertain": False, "pmin": None, "pmax": None,
                       "punit": "d", "vmin": None, "vmax": None, "min_amp": 0.1, "max_results": 12}
SHORT_PULSATOR_DAYS = 0.5          # a pulsator this short (or shorter) needs a whole cycle in one night
FILLER_MIN_RUN_MIN = 60.0          # an any-time star needs at least this long above the limits in the dark
CYCLE_MARGIN_MIN = 30.0            # a full cycle plus this margin


def variable_family(vtype: str) -> tuple[str | None, bool]:
    """(family key, uncertain) of a VSX type: "EA/SD" → ("eclipsing", False), "RRAB:" → ("rrlyr", True),
    "DSCT|GDOR|SXPHE" → ("dsct", False), "MISC" → ("other", False), "CST" → (None, False)."""
    s = str(vtype or "").strip()
    first = re.split(r"[|+,]", s)[0].strip() if s else ""
    uncertain = first.endswith(":") or s.endswith(":")
    head = first.rstrip(":").split("/")[0].strip("() ").upper()
    if head in NOT_VARIABLE_TYPES:
        return None, uncertain
    for key, _label, codes in VAR_FAMILIES:
        for c in codes:
            if (head == c) if c == "M" else head.startswith(c):
                return key, uncertain
    return "other", uncertain


def var_search_params(options: dict | None) -> dict:
    """The pop-up's parameters: the saved ones (options["var_search"]) over the defaults; the V range defaults to
    Setup's Suggested-transits range (suggest_vmin / suggest_vmax)."""
    o = dict(settings.DEFAULT_OPTIONS)
    o.update(options or {})
    p = dict(VAR_SEARCH_DEFAULTS)
    saved = o.get("var_search")
    if isinstance(saved, dict):
        p.update({k: v for k, v in saved.items() if k in VAR_SEARCH_DEFAULTS})
    if p.get("vmin") is None:
        p["vmin"] = float(o["suggest_vmin"])
    if p.get("vmax") is None:
        p["vmax"] = float(o["suggest_vmax"])
    fams = [f for f in (p.get("families") or []) if f in VAR_FAMILY_NAMES]
    p["families"] = fams
    p["punit"] = "h" if p.get("punit") == "h" else "d"
    return p


def _period_days(p: dict) -> tuple[float | None, float | None]:
    k = 24.0 if p.get("punit") == "h" else 1.0
    lo, hi = _f(p.get("pmin")), _f(p.get("pmax"))
    return (lo / k if lo is not None else None), (hi / k if hi is not None else None)


def _dec_reach(site, floor: float) -> tuple[float, float]:
    """Declinations whose culmination clears the floor altitude: |lat − dec| ≤ 90 − floor."""
    span = 90.0 - float(floor)
    return max(-90.0, float(site.lat) - span), min(90.0, float(site.lat) + span)


POLAR_CAP_DEC = 60.0               # stars nearer the (site's) pole than this are searched at every RA


def _max_hour_angle(site, floor: float, dec_lo: float, dec_hi: float) -> float:
    """The largest hour angle (degrees) at which a star of the searched declinations (outside the polar cap) still
    clears the floor altitude, + 2° margin."""
    lat = math.radians(float(site.lat))
    cap = POLAR_CAP_DEC if site.lat >= 0 else -POLAR_CAP_DEC
    lo, hi = (dec_lo, min(dec_hi, cap)) if site.lat >= 0 else (max(dec_lo, cap), dec_hi)
    if hi < lo:
        return 0.0
    dec = np.radians(np.linspace(lo, hi, 61))
    c = (math.sin(math.radians(floor)) - math.sin(lat) * np.sin(dec)) / np.maximum(1e-9, math.cos(lat) * np.cos(dec))
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))).max()) + 2.0


def _ra_ranges(site, wins: list, widen: float = 90.0) -> list[tuple[float, float]] | None:
    """RA ranges that can be up in the dark windows (LST at dusk − widen … LST at dawn + widen, widen = the largest
    useful hour angle; the exact test is made per star later), as non-wrapping (lo, hi) pairs; None = every RA."""
    starts = [float(sky.lst_deg(w[1], site.lon)) for w in wins]
    lengths = [(w[2] - w[1]) * 360.985647 for w in wins]          # sidereal degrees of each dark window
    a0 = starts[0]
    offs = [(s - a0 + 180.0) % 360.0 - 180.0 for s in starts]      # the nights start within a few degrees
    lo = a0 + min(offs) - widen
    hi = a0 + max(o + n for o, n in zip(offs, lengths)) + widen
    if hi - lo >= 360.0:
        return None
    start = lo % 360.0
    stop = start + (hi - lo)
    if stop <= 360.0:
        return [(start, stop)]
    return [(start, 360.0), (0.0, stop - 360.0)]


def var_search_adql(p: dict, dec_lo: float, dec_hi: float, ras: list | None, top: int = VAR_SEARCH_MAX_ROWS,
                    north: bool = True) -> str:
    """The VizieR TAP query for the search: variables (VarFlag 0) brighter-than/inside the V range in a visual band,
    reachable declinations, RAs up in the dark, the period range and the chosen families (none when "other" is
    chosen: that is every type)."""
    vmin, vmax = float(p["vmin"]), float(p["vmax"])
    where = ["V = 0", f"max BETWEEN {vmin:.2f} AND {vmax:.2f}",
             "n_max IN (" + ", ".join(f"'{b}'" for b in VISUAL_BANDS) + ")",
             f"DEJ2000 BETWEEN {dec_lo:.3f} AND {dec_hi:.3f}"]
    if ras:
        polar = (f"DEJ2000 >= {POLAR_CAP_DEC:.0f}" if (north and dec_hi > POLAR_CAP_DEC) else
                 f"DEJ2000 <= {-POLAR_CAP_DEC:.0f}" if (not north and dec_lo < -POLAR_CAP_DEC) else "")
        ors = [f"RAJ2000 BETWEEN {a:.3f} AND {b:.3f}" for a, b in ras] + ([polar] if polar else [])
        where.append("(" + " OR ".join(ors) + ")")
    plo, phi = _period_days(p)
    if plo is not None:
        where.append(f"Period >= {plo:.8f}")
    if phi is not None:
        where.append(f"Period <= {phi:.8f}")
    amp = float(_f(p.get("min_amp")) or 0.0)
    if amp > 0:                         # f_min 'Y': min is the amplitude; else min − max is (same band, checked later)
        where.append(f"((f_min = 'Y' AND min >= {amp:.3f}) OR (f_min IS NULL AND min - max >= {amp:.3f}))")
    fams = p.get("families") or []
    if fams and "other" not in fams:
        groups = []
        for key, _l, codes in VAR_FAMILIES:
            if key not in fams:
                continue
            likes = [f"Type = '{c}'" if c == "M" else f"Type LIKE '{c}%'" for c in codes]
            if key == "long":
                likes.append("Type LIKE 'M:%'")
                groups.append("(" + " OR ".join(likes) + ")")
            elif key == "eclipsing":               # minima are timed: epoch and period needed
                groups.append("((" + " OR ".join(likes) + ") AND Period > 0 AND Epoch > 0)")
            else:                                  # pulsators: a period at least
                groups.append("((" + " OR ".join(likes) + ") AND Period > 0)")
        where.append("(" + " OR ".join(groups) + ")")
    # oldest VSX entries first (OID order: the GCVS's named variables, then the big surveys): when there are more
    # matches than the cap, the best-studied stars are the ones checked, from every part of the sky
    return (f"SELECT TOP {int(top)} Name, V, RAJ2000, DEJ2000, Type, max, n_max, f_min, min, n_min, Epoch, Period "
            "FROM \"B/vsx/vsx\" WHERE " + " AND ".join(where) + " ORDER BY OID")


VAR_SEARCH_ASU_ROWS = 20000        # ASU filters less on the server (no types / bands / amplitude): fetch more


def _var_search_asu(p: dict, dec_lo: float, dec_hi: float, top: int = VAR_SEARCH_ASU_ROWS) -> list[dict]:
    """The same search through VizieR ASU (TAP down): position / brightness / period constraints on the server, the
    types, bands, amplitudes and RAs filtered here."""
    q = {"-out": "Name V RAJ2000 DEJ2000 Type max n_max f_min min n_min Epoch Period", "V": "0",
         "max": f"{float(p['vmin']):.2f}..{float(p['vmax']):.2f}", "DEJ2000": f"{dec_lo:.3f}..{dec_hi:.3f}",
         "-out.max": str(int(top)), "-sort": "OID"}
    plo, phi = _period_days(p)
    if plo is not None or phi is not None:
        q["Period"] = f"{plo if plo is not None else 0:.8f}..{phi if phi is not None else 30000:.8f}"
    return vizier_asu(q)


def fetch_variable_candidates(p: dict, site, wins: list, refresh: bool = False) -> tuple[list[dict], str, bool]:
    """B/vsx rows for the search (TAP, then ASU at CDS / CfA), cached a day per query: (rows as common variable
    dicts with "amp", the cache note, truncated). LookupError_ when nothing answers and nothing is cached."""
    floor = max(float(site.min_alt or 0.0), sky.alt_for_airmass(site.max_airmass) if site.max_airmass else 0.0)
    dec_lo, dec_hi = _dec_reach(site, floor)
    ras = _ra_ranges(site, wins, _max_hour_angle(site, floor, dec_lo, dec_hi))
    adql = var_search_adql(p, dec_lo, dec_hi, ras, north=site.lat >= 0)
    key = "vizier:varsearch:" + hashlib.sha1(adql.encode("utf-8")).hexdigest()[:16]

    cols = ("Name", "V", "RAJ2000", "DEJ2000", "Type", "max", "n_max", "f_min", "min", "n_min", "Epoch", "Period")

    def fetch():
        try:
            rows = vizier_query(adql)
            full = len(rows) >= VAR_SEARCH_MAX_ROWS
        except FetchError as exc:
            settings.log(f"VizieR TAP did not answer ({exc.reason}); trying VizieR ASU for the variable search")
            rows = _var_search_asu(p, dec_lo, dec_hi)
            full = len(rows) >= VAR_SEARCH_ASU_ROWS
            rows = [r for r in rows if _row_wanted(p, r)]
            full = full or len(rows) > VAR_SEARCH_MAX_ROWS
            rows = rows[:VAR_SEARCH_MAX_ROWS]
        return {"rows": [{k: str(r.get(k, "") or "").strip() for k in cols} for r in rows], "truncated": full}
    data, note = cached_lookup("VizieR", key, "this variable-star search", VAR_SEARCH_MAX_AGE_DAYS, fetch, refresh)
    rows = (data or {}).get("rows") or []
    return [candidate_from_row(r) for r in rows], note, bool((data or {}).get("truncated"))


def _row_wanted(p: dict, row: dict) -> bool:
    """The server-side filters of var_search_adql, here (for ASU replies): a chosen family, a visual band, the
    amplitude, a period (epoch too for eclipsing)."""
    v = candidate_from_row(row)
    fam, _u = variable_family(v.get("vtype", ""))
    fams = set(p.get("families") or [])
    if fam is None or (fams and "other" not in fams and fam not in fams):
        return False
    if v.get("band") not in VISUAL_BANDS:
        return False
    if v.get("amp") is None or v["amp"] < float(_f(p.get("min_amp")) or 0.0):
        return False
    if fam in ("eclipsing", "rrlyr", "dsct", "cepheid") and not v.get("period"):
        return False
    return not (fam == "eclipsing" and not v.get("epoch"))


def candidate_from_row(row: dict) -> dict:
    """A B/vsx row → the common variable dict plus "amp" (mag; None unknown), "band", "varflag"."""
    v = parse_vizier_vsx_row(row)
    v["band"] = str(row.get("n_max", "") or "").strip()
    v["varflag"] = str(row.get("V", "") or "").strip()
    v["amp"] = (v["min"] - v["max"]) if (v.get("min") is not None and v.get("max") is not None) else None
    return v


def _runs(mask, grid) -> list[tuple[float, float]]:
    """Consecutive True stretches of a boolean grid as (start, end) JD."""
    out, start = [], None
    for ok, t in zip(mask, grid):
        if ok and start is None:
            start = t
        if not ok and start is not None:
            out.append((start, prev))
            start = None
        prev = t
    if start is not None:
        out.append((start, grid[-1]))
    return out


def _var_kind(fam: str, v: dict) -> str:
    """How a star is planned in the search: "timed" (minima / RR Lyrae maxima from epoch + period), "cycle" (a short
    pulsator: one whole cycle in a night), "any" (a filler: any time)."""
    period, epoch = v.get("period"), v.get("epoch")
    kind = classify_vtype(v.get("vtype", ""))
    if fam == "eclipsing" or (fam == "other" and kind == "eclipsing"):
        return "timed" if (period and epoch) else ""
    if fam == "rrlyr":
        return "timed" if (period and epoch) else "cycle"
    if fam == "dsct":
        return "cycle" if period else ""          # a δ Scuti without a period is only a candidate
    if fam == "cepheid" or (fam == "other" and kind == "pulsator"):
        return "cycle" if (period and period <= SHORT_PULSATOR_DAYS) else "any"
    return "any"


def suggest_variables(site, options, nights: list, params: dict | None = None, exclude_names=frozenset(),
                      rows: list | None = None, progress=None) -> tuple[list[Target], str, str]:
    """Variable stars worth a look on the given nights (the variables twin of suggest_transits). Returns (targets,
    note, info): note = a saved-copy / source message ("" when fresh), info = what was checked. params =
    var_search_params(...) (families, period range, V range, minimum amplitude, max results, uncertain types).
    rows = candidate dicts (fetched from VizieR when None). Each star is kept when, on at least one night:
      timed (eclipsing minima, RR Lyrae maxima from the VSX epoch + period): an event ± its half-width falls in the
        planning darkness above every limit (sky.up_mask);
      cycle (δ Scuti / HADS / SX Phe, short Cepheids, RR Lyrae without an epoch): one whole cycle + 30 min fits in
        one dark stretch above the limits;
      any (Miras, semi-regulars, long-period pulsators): at least an hour above the limits in the dark;
    and the Moon-brightened sky stays within suggest_moon_penalty_max. Ranked by amplitude, brightness in the V range
    and a timed event (sooner = better), capped at max results; each carries suggest_info["when"] = its first event
    (or the start of its first good stretch) for the page's date order."""
    o = dict(settings.DEFAULT_OPTIONS)
    o.update(options or {})
    p = var_search_params(dict(o, var_search=params) if params is not None else o)
    if not site or not site.complete() or not nights:
        return [], "", ""
    wins = []
    for d in nights:
        w = sky.night_window(d, site)
        if w is not None:
            wins.append((d, w[0], w[1]))
    if not wins:
        return [], "", "no dark time on these nights"
    note, truncated = "", False
    if rows is None:
        rows, note, truncated = fetch_variable_candidates(p, site, wins)
    vmin, vmax = float(p["vmin"]), float(p["vmax"])
    plo, phi = _period_days(p)
    min_amp = float(_f(p.get("min_amp")) or 0.0)
    fams = set(p.get("families") or [])
    moon_max = float(o["suggest_moon_penalty_max"])
    dark_v = float(o.get("dark_sky_v", sky.DARK_SKY_V))
    excl = {_key_name(n) for n in (exclude_names or ())}
    floor = max(float(site.min_alt or 0.0), sky.alt_for_airmass(site.max_airmass) if site.max_airmass else 0.0)
    grids = [np.arange(w[1], w[2], 10.0 / 1440.0) for w in wins]

    cands = []
    for v in rows:
        fam, unsure = variable_family(v.get("vtype", ""))
        if fam is None or fam not in fams or (unsure and not p.get("uncertain")):
            continue
        if v.get("varflag") not in (None, "", "0"):
            continue
        if v.get("band") and v["band"] not in VISUAL_BANDS:
            continue
        if v.get("ra") is None or v.get("dec") is None or v.get("max") is None or not (vmin <= v["max"] <= vmax):
            continue
        per = v.get("period")
        if (plo is not None and (per is None or per < plo)) or (phi is not None and (per is None or per > phi)):
            continue
        if v.get("amp") is None or v["amp"] < min_amp:
            continue
        if _key_name(v.get("name")) in excl or sky.culmination_alt(v["dec"], site.lat) < floor:
            continue
        kind = _var_kind(fam, v)
        if kind:
            cands.append((v, fam, kind, unsure))

    found = []
    for i, (v, fam, kind, unsure) in enumerate(cands):
        if progress:
            try:
                progress(i, len(cands))
            except Exception:
                pass
        ra, dec = v["ra"], v["dec"]
        el = elements_from_vsx(v)
        good_nights, first = 0, None
        for (d, w0, w1), grid in zip(wins, grids):
            if len(grid) < 2:
                continue
            up = sky.up_mask(ra, dec, site, grid)
            hit = None
            if kind == "timed":
                tel = VarElements(vtype=el.vtype, period=v["period"], epoch=v["epoch"], any_time=False,
                                  epoch_kind="min" if fam != "rrlyr" else "max",
                                  event_halfwidth_min=el.event_halfwidth_min or 60.0)
                for ev in events.variable_events(tel, ra, dec, w0, w1):
                    if ev["start"] < w0 or ev["end"] > w1:
                        continue
                    span = np.linspace(ev["start"], ev["end"], 7)
                    if bool(np.all(sky.up_mask(ra, dec, site, span))):
                        hit = (ev["t"], ev["start"], ev["end"], ev["kind"])
                        break
            else:
                need = ((v["period"] * 1440.0 + CYCLE_MARGIN_MIN) if kind == "cycle" else FILLER_MIN_RUN_MIN) / 1440.0
                for a, b in _runs(up, grid):
                    if b - a >= need:
                        hit = (a, a, min(b, a + max(need, 1.0 / 24.0)), "cycle" if kind == "cycle" else "any")
                        break
            if hit is None:
                continue
            span = np.linspace(hit[1], hit[2], 7)
            if float(np.mean(sky.moon_sky_penalty(ra, dec, site, span, dark_v))) > moon_max:
                continue
            good_nights += 1
            if first is None:
                first = (d, hit)
        if first is None:
            continue
        d, hit = first
        amp = float(v["amp"])
        soon = (d - wins[0][0]).days
        score = (2.0 * min(amp, 1.0) + (vmax - v["max"]) / max(0.1, vmax - vmin)
                 + (1.0 + (0.5 if soon <= 1 else 0.0) if kind == "timed" else (0.5 if kind == "cycle" else 0.0))
                 + 0.5 * good_nights / len(wins))
        if unsure:
            score *= 0.8
        found.append((score, v, fam, kind, d, hit, good_nights, amp))

    found.sort(key=lambda x: -x[0])
    out = []
    for score, v, fam, kind, d, hit, good, amp in found[: int(p.get("max_results") or 12)]:
        t0 = hit[0]
        when_text = f"{_fmt_day(t0, site)} {sky.hhmm(t0, site.tz)}"
        if kind == "timed":
            text = f"{hit[3]} {when_text}"
        elif kind == "cycle":
            text = f"full cycle from {when_text}"
        else:
            text = f"from {when_text}"
        el = elements_from_vsx(v)
        rng = (f"{v['max']:.2f}–{v['min']:.2f} {v.get('band') or ''}".strip() if v.get("min") is not None
               else f"{v['max']:.2f}")
        per = v.get("period")
        per_text = (f"P = {per * 24:.2f} h" if per and per < 1 else (f"P = {per:.4g} d" if per else "no period"))
        note_text = (f"{v.get('vtype')}, {per_text}, {rng} (Δ {amp:.2f} mag); {text}, "
                     f"{sky.local_label(site.tz, t0)}")
        t = Target(name=v["name"], kind="variable", mode="V", ra=v["ra"], dec=v["dec"], vmag=v["max"],
                   enabled=False, suggested=True, source="VizieR VSX", elements=el.__dict__.copy(), notes=note_text,
                   added=today_iso(site))
        t.suggest_info = {"night": d.isoformat(), "when": float(t0), "event": hit[3], "text": text,
                          "score": round(float(score), 3), "amp": round(amp, 3), "family": fam, "kind": kind,
                          "good_nights": good, "nights": len(wins)}
        out.append(t)
    info = (f"{len(cands)} of {len(rows)} catalog stars matched"
            + (f" (the first {VAR_SEARCH_MAX_ROWS} VSX matches, best-studied first: narrow the V or period range or "
               "the types to reach the rest)" if truncated else ""))
    return out, note, info


# ---- next useful event (Targets page column) -----------------------------------------------------------------------

def _dark_windows(site, from_jd: float, days: int) -> list[tuple[float, float]]:
    d0 = sky.night_date_of(from_jd, site.tz)
    out = []
    for i in range(int(days) + 1):
        w = sky.night_window(d0 + timedelta(days=i), site)
        if w and w[1] > from_jd:
            out.append((max(w[0], from_jd), w[1]))
    return out


def _min_alt_of(target: Target) -> float | None:
    for r in target.rules_obj():
        if r.kind == "min_alt" and _f(r.value) is not None:
            return float(r.value)
    return None


def next_event_text(target: Target, site, from_jd: float, days: int = 30) -> str:
    """The Targets page's "Next useful event", on the site's clock:
    transits "Tue 20 Oct 21:32–23:56 (full)" (first transit whose ingress…egress is all in darkness and above the
    limits; else the first partly visible one "(partial)"; else "none in 30 nights"); timed variables
    "min Thu 22 Oct 01:14"; any-time variables "any time"; campaigns "every night" or
    "every night; next blackout Fri 23 Oct 04:28–05:53 (HD 219134 c transits)"."""
    if site is None or not site.complete():
        return "set up your site first"
    if target.ra is None or target.dec is None:
        return "no coordinates"
    wins = _dark_windows(site, from_jd, days)
    end_jd = from_jd + days
    mina = _min_alt_of(target)

    def dark(jds):
        jds = np.atleast_1d(np.asarray(jds, dtype=float))
        in_dark = np.zeros(jds.shape, dtype=bool)
        for a, b in wins:
            in_dark |= (jds >= a) & (jds <= b)
        return in_dark

    def dark_up(jds):
        jds = np.atleast_1d(np.asarray(jds, dtype=float))
        return dark(jds) & sky.up_mask(target.ra, target.dec, site, jds, mina)

    if target.kind == "transit":
        pl = target.planet_obj()
        if not pl or not pl.ok():
            return "no ephemeris"
        if not wins:
            return "no dark time"
        partial = None
        for ev in events.transit_events(pl, target.ra, target.dec, from_jd, end_jd):
            if ev["t4"] < from_jd or ev["t1"] > end_jd:
                continue
            grid = np.linspace(ev["t1"], ev["t4"], 25)
            ok = dark_up(grid)
            span = f"{_fmt_day(ev['t1'], site)} {sky.hhmm(ev['t1'], site.tz)}–{sky.hhmm(ev['t4'], site.tz)}"
            if ok.all() and ev["t1"] >= from_jd:
                return f"{span} (full)"
            if ok.any() and partial is None:
                partial = f"{span} (partial)"
        return partial or f"none in {days} nights"

    if target.kind == "variable":
        el = target.elements_obj()
        if not el or el.any_time or not el.period or not el.epoch:
            return "any time"
        if not wins:
            return "no dark time"
        word = "min" if (el.epoch_kind or "max") == "min" else "max"
        for ev in events.variable_events(el, target.ra, target.dec, from_jd, end_jd):
            if ev["t"] >= from_jd and bool(dark_up([ev["t"]])[0]):
                return f"{word} {_fmt_day(ev['t'], site)} {sky.hhmm(ev['t'], site.tz)}"
        return f"none in {days} nights"

    # campaigns
    if not any(r.kind == "no_transit" for r in target.rules_obj()):
        return "every night"
    for b in sorted(events.blackouts(target, from_jd, end_jd), key=lambda x: x["start"]):
        if b["end"] < from_jd:
            continue
        grid = np.linspace(b["start"], b["end"], 13)
        if dark(grid).any():                 # any blackout in darkness (even with the target low: it is a rule)
            return (f"every night; next blackout {_fmt_day(b['start'], site)} {sky.hhmm(b['start'], site.tz)}–"
                    f"{sky.hhmm(b['end'], site.tz)} ({b['reason']})")
    return "every night"


# ---- campaign progress ---------------------------------------------------------------------------------------------

def campaign_progress(target: Target, schedule: Schedule, today: date) -> tuple[int, int]:
    """(nights marked Done in the current Mon–Sun week, goal_per_week). Partial and Clouded out count as missed
    (as planner.rereview does), so the Targets page and the planner agree. Counts schedule.history and the
    schedule's own marked blocks; a night counts once however many blocks it had. today = the site's date."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    good = ("done",)
    dates = set()

    def mine(tid, name):
        return (tid and tid == target.id) or (not tid and name and _key_name(name) == _key_name(target.name))

    def in_week(iso):
        try:
            d = date.fromisoformat(str(iso)[:10])
        except ValueError:
            return None
        return d if monday <= d <= sunday else None

    sch = schedule or Schedule()
    for h in sch.history or []:
        if isinstance(h, dict) and str(h.get("status", "")).lower() in good and mine(h.get("target_id"), h.get("name")):
            d = in_week(h.get("date", ""))
            if d:
                dates.add(d)
    for n in sch.nights or []:
        nd = n if isinstance(n, dict) else getattr(n, "__dict__", {})
        d = in_week(nd.get("date", ""))
        if not d:
            continue
        for b in nd.get("blocks") or []:
            bd = b if isinstance(b, dict) else getattr(b, "__dict__", {})
            if str(bd.get("status", "")).lower() in good and mine(bd.get("target_id"), bd.get("name")):
                dates.add(d)
                break
    return len(dates), int(target.goal_per_week or 0)
