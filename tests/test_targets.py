"""Tests for targets.py (lookups, cache, Suggested transits, next-event text). No network: every reply is a file
recorded live on 22 Oct 2026 in tests/fixtures/targets_* and served by a fake `targets.http_get`.

Run:  cd /home/claude/shobs-sched && /usr/bin/python3.12 -m unittest tests.test_targets -v
      TZ=Asia/Tokyo /usr/bin/python3.12 -m unittest tests.test_targets -v     (computer zone ≠ site zone)

Fixture notes
  targets_catalog_small.csv  real Archive rows (pscomppars, tran_flag = 1) for 14 planets, EXCEPT HAT-P-23 b whose
                             period/t0 and errors were set to the golden plan's ephemeris (Kokori et al. 2023;
                             the composite row still carries Bakos et al. 2010, ~19 min off in 2026), plus a
                             made-up "Nodur-1 b" with no duration (the parser must drop it).
  targets_vsx_*.json         real AAVSO VSX api.object replies (VSX answered from this workspace on 22 Oct, between
                             504s); targets_vsx_amplitude (hand-made, below) covers a "(0.56) V" amplitude.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import urllib.parse
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
_TMP = tempfile.mkdtemp(prefix="shobs_sched_test_targets_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "no_shobs_p.json")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import NightPlan, Optics, Schedule, Site, Target, Block  # noqa: E402

with open(os.path.join(FIX, "golden_oct19_24.json"), encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)
SITE = Site.from_dict(GOLDEN["site"])
OPTICS = Optics.from_dict(GOLDEN["optics"])
GT = {t["name"]: Target.from_dict(t) for t in GOLDEN["targets"]}
NIGHTS = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]
WHEN = sky.local_jd(date(2026, 10, 22), 12, 0, SITE.tz)


def fx(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


class Router:
    """A fake http_get: rules are (list of substrings that must all be in the decoded URL, reply). A reply is a
    fixture file name, bytes, or an exception instance to raise. Unmatched URLs raise AssertionError."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []
        self.timeouts = []

    def __call__(self, url, timeout=30):
        u = urllib.parse.unquote_plus(url)
        self.calls.append(u)
        self.timeouts.append(timeout)
        for needles, reply in self.rules:
            if all(n in u for n in needles):
                if isinstance(reply, BaseException):
                    raise reply
                return reply if isinstance(reply, bytes) else fx(reply)
        raise AssertionError(f"unexpected URL in test: {u}")


DOWN = T.FetchError("service unavailable, HTTP 503", 503)
TIMEOUT = T.FetchError("no answer within 8 s")
VSX403 = T.FetchError("refused the request, HTTP 403", 403)
EMPTY_NEA = "targets_nea_empty.csv"
EMPTY_VIZ = "targets_vizier_empty.csv"


def clear_cache():
    shutil.rmtree(settings.cache_dir(), ignore_errors=True)
    settings.cache_dir()


class Base(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self._orig = T.http_get

    def tearDown(self):
        T.http_get = self._orig

    def route(self, rules):
        r = Router(rules)
        T.http_get = r
        return r


# ---- names ---------------------------------------------------------------------------------------------------------

class TestNames(unittest.TestCase):
    def test_variants_first_is_archive_spelling(self):
        self.assertEqual(T.name_variants("wasp-12b")[0], "WASP-12 b")
        self.assertEqual(T.name_variants("TrES-3b")[0], "TrES-3 b")
        self.assertEqual(T.name_variants("toi 2046 b")[0], "TOI-2046 b")
        self.assertEqual(T.name_variants("hat-p-23b")[0], "HAT-P-23 b")
        self.assertEqual(T.name_variants("k2 29b")[0], "K2-29 b")
        self.assertEqual(T.name_variants("GJ 1214b")[0], "GJ 1214 b")
        self.assertIn("TOI-2046 b", T.name_variants("toi-2046")[:6])
        self.assertEqual(T.name_variants("  "), [])

    def test_looks_like_planet(self):
        for n in ("WASP-12 b", "wasp-12b", "TOI-2046", "TOI-1234.01", "HAT-P-23"):
            self.assertTrue(T.looks_like_planet(n), n)
        for n in ("DY Peg", "V404 Lyr", "NGC 7789", "HD 219134", "Algol"):
            self.assertFalse(T.looks_like_planet(n), n)

    def test_vsx_variants(self):
        self.assertIn("V0404 Lyr", T.vsx_name_variants("V404 Lyr"))
        self.assertIn("V0404 Lyr", T.vsx_name_variants("v404 lyr"))
        self.assertIn("DY Peg", T.vsx_name_variants("dy peg"))

    def test_strip_tags_unescapes_twice(self):
        self.assertEqual(T.strip_tags('<a href="x">Kab&amp;aacute;th et al. 2022</a>'), "Kabáth et al. 2022")
        self.assertEqual(T.strip_tags("<a refstr=X href=y target=ref>Sada &amp; Ram&oacute;n-Fox 2016</a>"),
                         "Sada & Ramón-Fox 2016")
        self.assertEqual(T.strip_tags("<a refstr=B target=ref> Bakos et al. 2010 </a>"), "Bakos et al. 2010")

    def test_classify(self):
        self.assertEqual(T.classify_vtype("SXPHE"), "pulsator")
        self.assertEqual(T.classify_vtype("HADS:"), "pulsator")
        self.assertEqual(T.classify_vtype("RRAB"), "pulsator")
        self.assertEqual(T.classify_vtype("DCEP"), "pulsator")
        self.assertEqual(T.classify_vtype("EA/SD"), "eclipsing")
        self.assertEqual(T.classify_vtype("EW/KW"), "eclipsing")
        self.assertEqual(T.classify_vtype("EA+GDOR"), "eclipsing")
        self.assertEqual(T.classify_vtype("ELL"), "eclipsing")
        self.assertEqual(T.classify_vtype("M"), "long")
        self.assertEqual(T.classify_vtype("SRB"), "long")
        self.assertEqual(T.classify_vtype("LB"), "long")


# ---- most precise ephemeris ------------------------------------------------------------------------------------------

class TestMostPrecise(unittest.TestCase):
    def ps_rows(self):
        import csv, io
        return list(csv.DictReader(io.StringIO(fx("targets_nea_ps_hatp23b.csv").decode())))

    def test_picks_smallest_predicted_sigma(self):
        # composite = Bakos et al. 2010 (period err 2e-6 d over ~4900 epochs ≈ 14 min); Kokori 2023 is ~1 min
        comp = (1.212884, 2e-6, 2454852.26464, 0.00018)
        best = T.most_precise_ephemeris(self.ps_rows(), WHEN, comp)
        self.assertIsNotNone(best)
        self.assertEqual(best["reference"], "Kokori et al. 2023")
        self.assertAlmostEqual(best["period"], 1.212886397)
        self.assertAlmostEqual(best["t0"], 2457742.57379)
        self.assertLess(best["sigma_min"], 2.0)
        self.assertEqual(best["n"], 8)          # rows without period, t0 or both errors are skipped

    def test_composite_kept_unless_beaten_by_5_percent(self):
        rows = [{"pl_orbper": "2.0", "pl_orbpererr1": "1e-6", "pl_orbpererr2": "-1e-6", "pl_tranmid": "2460000.0",
                 "pl_tranmiderr1": "0.001", "pl_tranmiderr2": "-0.001", "pl_refname": "<a>Row</a>"}]
        sig = T._predicted_sigma_days(2.0, 1e-6, 2460000.0, 0.001, WHEN)
        just_worse = (2.0, 0.0, 2460000.0, sig * 1.04)            # row better by only 4 %: keep composite
        self.assertIsNone(T.most_precise_ephemeris(rows, WHEN, just_worse))
        much_worse = (2.0, 0.0, 2460000.0, sig * 1.2)             # row better by 20 %: take it
        self.assertEqual(T.most_precise_ephemeris(rows, WHEN, much_worse)["reference"], "Row")
        no_errors = (2.0, None, 2460000.0, None)                   # composite without errors: any row wins
        self.assertIsNotNone(T.most_precise_ephemeris(rows, WHEN, no_errors))

    def test_unusable_rows(self):
        rows = [{"pl_orbper": "2.0", "pl_tranmid": "2460000.0", "pl_orbpererr1": "", "pl_tranmiderr1": "0.001"},
                {"pl_orbper": "2.0", "pl_tranmid": "5000.5", "pl_orbpererr1": "1e-6", "pl_tranmiderr1": "0.001"}]
        self.assertIsNone(T.most_precise_ephemeris(rows, WHEN, None))

    def test_tc_sigma_minutes(self):
        pl = GT["TOI-2046 b"].planet
        n = round((WHEN - pl["t0"]) / pl["period"])
        want = ((pl["t0_err"] ** 2 + (n * pl["period_err"]) ** 2) ** 0.5) * 1440
        self.assertAlmostEqual(T.tc_sigma_minutes(pl, WHEN), want, places=6)
        self.assertIsNone(T.tc_sigma_minutes({"period": 2.0, "t0": 2460000.0, "t14": 0.1}, WHEN))
        self.assertIsNone(T.tc_sigma_minutes(None, WHEN))


# ---- planet lookups ------------------------------------------------------------------------------------------------

def planet_rules(alias=None):
    return [
        (["nph-aliaslookup", "hat-p-23b"], alias or "targets_nea_alias_hatp23b.json"),
        (["pscomppars", "pl_name = 'HAT-P-23 b'"], "targets_nea_comp_hatp23b.csv"),
        (["from ps where pl_name = 'HAT-P-23 b'"], "targets_nea_ps_hatp23b.csv"),
    ]


class TestLookupPlanet(Base):
    def test_alias_composite_and_most_precise(self):
        r = self.route(planet_rules())
        t = T.lookup_planet("hat-p-23b", WHEN)
        self.assertEqual(t.name, "HAT-P-23 b")
        self.assertEqual((t.kind, t.mode), ("transit", "T"))
        self.assertEqual(t.source, "NEA (most precise)")
        p = t.planet
        self.assertEqual(p["source"], "NEA (most precise)")
        self.assertEqual(p["reference"], "Kokori et al. 2023")
        self.assertAlmostEqual(p["period"], 1.212886397)
        self.assertAlmostEqual(p["t0"], 2457742.57379)
        self.assertAlmostEqual(p["depth"], 0.1169 ** 2)            # (Rp/R*)² when pl_ratror is known
        self.assertAlmostEqual(p["t14"], 2.1792 / 24.0)            # hours → days
        self.assertAlmostEqual(t.ra, 306.1239076)
        self.assertAlmostEqual(t.vmag, 11.937)
        self.assertEqual(t.lookup_note, "")
        self.assertEqual(len(r.calls), 3)
        for u in r.calls[1:]:
            self.assertNotIn(" in (", u)                            # Cloudflare-safe query shapes only

    def test_variants_when_alias_service_hangs(self):
        r = self.route([
            (["nph-aliaslookup"], TIMEOUT),
            (["pscomppars", "pl_name = 'TrES-3 b'"], "targets_nea_comp_tres3b.csv"),
            (["from ps where"], EMPTY_NEA),
            (["pscomppars"], EMPTY_NEA),
        ])
        t = T.lookup_planet("TrES-3b", WHEN)
        self.assertEqual(t.name, "TrES-3 b")
        self.assertEqual(t.source, "NEA")                           # no ps rows: the composite ephemeris
        self.assertEqual(t.planet["reference"], "Sozzetti et al. 2009")
        self.assertIsNone(t.planet["t0_err"])
        self.assertAlmostEqual(t.planet["depth"], 0.1655 ** 2)

    def test_depth_from_trandep_when_no_ratror(self):
        import csv, io
        row = next(csv.DictReader(io.StringIO(fx("targets_nea_comp_tres3b.csv").decode())))
        row["pl_ratror"] = ""
        t = T.planet_target({"composite": row, "ps": []}, WHEN)
        self.assertAlmostEqual(t.planet["depth"], 2.739 / 100.0)

    def test_star_with_several_planets(self):
        self.route([
            (["nph-aliaslookup", "HD 219134"], "targets_nea_alias_hd219134.json"),
            (["hostname = 'HD 219134'"], "targets_nea_host_hd219134.csv"),
        ])
        with self.assertRaises(T.PlanetChoice) as cm:
            T.lookup_planet("HD 219134", WHEN)
        e = cm.exception
        self.assertEqual(e.system, "HD 219134")
        self.assertEqual(len(e.planets), 6)
        self.assertEqual(set(e.planets[:2]), {"HD 219134 b", "HD 219134 c"})   # transiting ones first
        self.assertEqual(e.transiting, {"HD 219134 b", "HD 219134 c"})
        self.assertIn("Choose", e.message)

    def test_not_found(self):
        self.route([(["nph-aliaslookup"], TIMEOUT), (["pscomppars"], EMPTY_NEA)])
        with self.assertRaises(T.NotFound) as cm:
            T.lookup_planet("Nosuch-9 b", WHEN)
        self.assertIn("has no planet called Nosuch-9 b", cm.exception.message)
        self.assertEqual(os.listdir(settings.cache_dir()), [])     # not-found answers are not cached


# ---- cache ---------------------------------------------------------------------------------------------------------

class TestCache(Base):
    def _age_cache(self, days):
        for f in os.listdir(settings.cache_dir()):
            p = os.path.join(settings.cache_dir(), f)
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
            rec["fetched_unix"] -= days * 86400
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)

    def test_file_format(self):
        self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        files = os.listdir(settings.cache_dir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("lookup_nea_planet_hat_p_23b_"))
        with open(os.path.join(settings.cache_dir(), files[0]), encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["format"], "shobs-lookup-cache/1")
        self.assertEqual(rec["app"], "SHOBS-SCHED")
        self.assertEqual(rec["source"], "NEA")
        self.assertEqual(rec["key"], "nea:planet:hat-p-23b")
        self.assertEqual(rec["query"], "hat-p-23b")
        self.assertIn("composite", rec["data"])
        self.assertTrue(rec["fetched"].endswith("+00:00"))
        self.assertLess(abs(rec["fetched_unix"] - time.time()), 60)

    def test_hit_makes_no_calls(self):
        self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        r = self.route([])                                          # any call would fail the test
        t = T.lookup_planet("HAT-P-23b ", WHEN)                     # same key (case and spaces normalized)
        self.assertEqual(r.calls, [])
        self.assertEqual(t.planet["reference"], "Kokori et al. 2023")

    def test_stale_is_refetched(self):
        self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        self._age_cache(8)                                          # planets refresh after 7 days
        r = self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        self.assertEqual(len(r.calls), 3)
        _d, age = T.cache_read("NEA", "nea:planet:hat-p-23b")
        self.assertLess(age, 0.01)

    def test_refresh_flag(self):
        self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        r = self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN, refresh=True)
        self.assertEqual(len(r.calls), 3)

    def test_offline_uses_old_copy_with_its_age(self):
        self.route(planet_rules())
        T.lookup_planet("hat-p-23b", WHEN)
        self._age_cache(8)
        self.route([([""], DOWN)])
        t = T.lookup_planet("hat-p-23b", WHEN)
        self.assertEqual(t.name, "HAT-P-23 b")
        self.assertIn("did not answer (service unavailable, HTTP 503)", t.lookup_note)
        self.assertIn("8 days ago", t.lookup_note)

    def test_offline_without_copy(self):
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_planet("hat-p-23b", WHEN)
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("HTTP 503", cm.exception.message)
        self.assertIn("NASA Exoplanet Archive", cm.exception.message)

    def test_cloudflare_page(self):
        page = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
                b"Checking your browser - Cloudflare</body></html>")
        self.route([(["nph-aliaslookup"], TIMEOUT), ([""], page)])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_planet("hat-p-23b", WHEN)
        self.assertIn("Cloudflare", cm.exception.message)

    def test_tap_error_reply(self):
        err = b'<?xml version="1.0"?><VOTABLE><RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">' \
              b'bad</INFO></RESOURCE></VOTABLE>'
        self.route([(["nph-aliaslookup"], TIMEOUT), ([""], err)])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_planet("hat-p-23b", WHEN)
        self.assertIn("rejected the query", cm.exception.message)

    def test_vsx_and_simbad_ages(self):
        self.assertEqual(T.PLANET_MAX_AGE_DAYS, 7)
        self.assertEqual(T.VSX_MAX_AGE_DAYS, 30)
        self.assertEqual(T.SIMBAD_MAX_AGE_DAYS, 30)
        self.route([(["api.object", "DY Peg"], "targets_vsx_dypeg.json")])
        T.lookup_variable("DY Peg")
        self._age_cache(20)                                         # 20 days: still fresh for VSX
        r = self.route([])
        T.lookup_variable("DY Peg")
        self.assertEqual(r.calls, [])

    def test_http_get_maps_errors(self):
        import urllib.error, socket
        orig = T.urllib.request.urlopen

        def boom(exc):
            def f(*a, **k):
                raise exc
            return f
        try:
            T.urllib.request.urlopen = boom(urllib.error.HTTPError("u", 503, "x", {}, None))
            with self.assertRaises(T.FetchError) as cm:
                T.http_get("https://example.org")
            self.assertEqual(cm.exception.status, 503)
            self.assertIn("HTTP 503", cm.exception.reason)
            T.urllib.request.urlopen = boom(urllib.error.URLError(socket.gaierror(-2, "Name not known")))
            with self.assertRaises(T.FetchError) as cm:
                T.http_get("https://example.org")
            self.assertIn("no network connection", cm.exception.reason)
            T.urllib.request.urlopen = boom(TimeoutError("The read operation timed out"))
            with self.assertRaises(T.FetchError) as cm:
                T.http_get("https://example.org", timeout=8)
            self.assertIn("no answer within 8 s", cm.exception.reason)
        finally:
            T.urllib.request.urlopen = orig


# ---- variables -----------------------------------------------------------------------------------------------------

class TestVSXParse(unittest.TestCase):
    def el(self, fixture):
        v = T.parse_vsx_json(fx(fixture).decode())
        return v, T.elements_from_vsx(v)

    def test_pulsator(self):
        v, el = self.el("targets_vsx_dypeg.json")
        self.assertEqual(v["name"], "DY Peg")
        self.assertAlmostEqual(v["ra"], 347.21329)
        self.assertEqual((el.vtype, el.epoch_kind, el.any_time), ("SXPHE", "max", True))
        self.assertAlmostEqual(el.period, 0.07292611)
        self.assertEqual((el.mag_max, el.mag_min), (10.0, 10.56))
        v, el = self.el("targets_vsx_gpand.json")
        self.assertEqual((el.vtype, el.any_time), ("HADS", True))

    def test_eclipsing_with_duration(self):
        v, el = self.el("targets_vsx_algol.json")                  # VSX resolves Algol → bet Per
        self.assertEqual(v["name"], "bet Per")
        self.assertEqual((el.epoch_kind, el.any_time), ("min", False))
        # EclipseDuration 14 % of 2.867343 d = 578 min; half-width ≈ 0.6 × that
        # 0.6 × 578 min = 347 min, capped at ±240 min
        self.assertEqual(el.event_halfwidth_min, 240.0)
        short = dict(v, period=0.5, eclipse_pct=10.0)               # 72-min eclipse → ±43.2 min
        self.assertAlmostEqual(T.elements_from_vsx(short).event_halfwidth_min, 43.2)

    def test_eclipsing_without_duration(self):
        v, el = self.el("targets_vsx_v404lyr.json")                # "EA+GDOR": the eclipses are timed
        self.assertEqual((v["name"], el.epoch_kind, el.any_time, el.event_halfwidth_min),
                         ("V0404 Lyr", "min", False, 60.0))
        v, el = self.el("targets_vsx_tzboo.json")
        self.assertEqual((el.vtype, el.epoch_kind, el.any_time), ("EW/KW", "min", False))

    def test_long_period(self):
        v, el = self.el("targets_vsx_rleo.json")
        self.assertEqual((el.vtype, el.any_time, el.mag_max, el.mag_min), ("M", True, 4.4, 11.0))

    def test_not_found_and_amplitude(self):
        self.assertIsNone(T.parse_vsx_json(fx("targets_vsx_none.json").decode()))
        hand = json.dumps({"VSXObject": {"Name": "XX Tst", "RA2000": "10.0", "Declination2000": "20.0",
                                         "VariabilityType": "DSCT", "Period": "0.1", "Epoch": "2450000.1",
                                         "MaxMag": "11.20 V", "MinMag": "(0.56) V"}})
        v = T.parse_vsx_json(hand)
        self.assertAlmostEqual(v["min"], 11.76)
        self.assertEqual(v["band"], "V")
        with self.assertRaises(T.FetchError):
            T.parse_vsx_json("<html>oops</html>")

    def test_vizier_row(self):
        import csv, io
        row = next(csv.DictReader(io.StringIO(fx("targets_vizier_name_dypeg.csv").decode())))
        v = T.parse_vizier_vsx_row(row)
        self.assertEqual(v["name"], "DY Peg")                       # space-padded in B/vsx: stripped
        self.assertEqual(v["vtype"], "SXPHE")
        self.assertEqual((v["max"], v["min"], v["period"], v["epoch"]), (10.0, 10.56, 0.07292611, 2454710.722))
        row["f_min"], row["min"] = "(", "0.56"
        self.assertAlmostEqual(T.parse_vizier_vsx_row(row)["min"], 10.56)


class TestLookupVariable(Base):
    def test_vsx_api(self):
        self.route([(["api.object", "DY Peg"], "targets_vsx_dypeg.json")])
        t = T.lookup_variable("DY Peg")
        self.assertEqual((t.name, t.kind, t.mode, t.source, t.vmag), ("DY Peg", "variable", "V", "VSX", 10.0))
        self.assertTrue(t.elements["any_time"])

    def test_vizier_fallback_by_name(self):
        r = self.route([
            (["api.object"], VSX403),
            (["B/vsx/vsx", "Name = 'DY Peg'"], "targets_vizier_name_dypeg.csv"),
        ])
        t = T.lookup_variable("DY Peg")
        self.assertEqual((t.name, t.source), ("DY Peg", "VizieR VSX"))
        self.assertEqual(t.elements["vtype"], "SXPHE")
        self.assertAlmostEqual(t.elements["epoch"], 2454710.722)
        self.assertTrue(any("tapvizier" in u for u in r.calls))

    def test_vizier_zero_padded_name(self):
        self.route([
            (["api.object"], VSX403),
            (["B/vsx/vsx", "Name = 'V0404 Lyr'"], "targets_vizier_name_v404lyr.csv"),
            (["B/vsx/vsx", "Name = "], EMPTY_VIZ),
        ])
        t = T.lookup_variable("V404 Lyr")
        self.assertEqual(t.name, "V0404 Lyr")
        self.assertFalse(t.elements["any_time"])
        self.assertEqual(t.elements["epoch_kind"], "min")

    def test_vizier_tap_down_uses_asu(self):
        r = self.route([
            (["api.object"], TIMEOUT),
            (["tapvizier"], DOWN),
            (["vizier.cds.unistra.fr/viz-bin/asu-tsv", "Name=BL Cam"], "targets_vizier_asu_blcam.tsv"),
        ])
        t = T.lookup_variable("BL Cam")
        self.assertEqual((t.name, t.source), ("BL Cam", "VizieR VSX"))
        self.assertAlmostEqual(t.ra, 56.8326, places=3)
        self.assertTrue(any("asu-tsv" in u for u in r.calls))

    def test_all_vsx_sources_down_names_both_causes(self):
        self.route([(["api.object"], TIMEOUT), (["tapvizier"], DOWN), (["asu-tsv"], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_variable("BL Cam")
        self.assertIn("no answer within 8 s; VizieR's copy: service unavailable, HTTP 503", str(cm.exception))

    def test_vizier_cone_via_simbad(self):
        self.route([
            (["api.object"], VSX403),
            (["B/vsx/vsx", "Name = "], EMPTY_VIZ),
            (["sim-script", "query id Algol"], "targets_simbad_algol.txt"),
            (["B/vsx/vsx", "CONTAINS", "CIRCLE('ICRS',47.04"], "targets_vizier_cone_betper.csv"),
        ])
        t = T.lookup_variable("Algol")
        self.assertEqual((t.name, t.source), ("bet Per", "VizieR VSX"))
        self.assertIn("found by position", t.lookup_note)
        self.assertEqual(t.elements["epoch_kind"], "min")

    def test_cluster_does_not_take_a_nearby_variable(self):
        self.route([
            (["api.object"], VSX403),
            (["B/vsx/vsx", "Name = "], EMPTY_VIZ),
            (["sim-script", "query id NGC 7789"], "targets_simbad_ngc7789.txt"),
            (["B/vsx/vsx", "CONTAINS"], "targets_vizier_cone_ngc7789.csv"),     # V0734 Cas, 45" away
        ])
        with self.assertRaises(T.NotFound) as cm:
            T.lookup_variable("NGC 7789")
        self.assertEqual(cm.exception.message,
                         "VSX did not answer (refused the request, HTTP 403) and VizieR's copy of VSX has no star "
                         "called NGC 7789. Check the spelling, or try again later.")

    def test_cluster_typed_cl_star(self):
        """SIMBAD types some clusters "Cl*" (with a star sign): still not one star (review MUST 4)."""
        self.route([
            (["api.object"], DOWN),
            (["B/vsx/vsx", "Name = "], EMPTY_VIZ),
            (["sim-script"], b"NGC  7789|359.3342 +56.7261|6.7|Cluster*|Cl*\n"),
            (["B/vsx/vsx", "CONTAINS"], "targets_vizier_cone_ngc7789.csv"),
        ])
        with self.assertRaises(T.NotFound):
            T.lookup_variable("NGC 7789")

    def test_cone_ignores_constant_or_periodless(self):
        cst = fx(EMPTY_VIZ) + (b'1,2,"bet Per                       ",0,47.0422,40.9557,"CST                           "'
                               b',,2.09,,"V         ",,,2.1,,"V       ",,,,,,"B8V",B\n')
        self.route([
            (["api.object"], VSX403),
            (["B/vsx/vsx", "Name = "], EMPTY_VIZ),
            (["sim-script", "query id Algol"], "targets_simbad_algol.txt"),
            (["B/vsx/vsx", "CONTAINS"], cst),
        ])
        with self.assertRaises(T.NotFound):
            T.lookup_variable("Algol")

    def test_vsx_says_unknown(self):
        r = self.route([(["api.object"], "targets_vsx_none.json")])
        with self.assertRaises(T.NotFound) as cm:
            T.lookup_variable("Nosuch Star")
        self.assertIn("VSX has no variable star called Nosuch Star", cm.exception.message)
        self.assertFalse(any("tapvizier" in u for u in r.calls))   # VSX answered: no fallback needed
        self.assertEqual(r.timeouts, [T.VSX_TIMEOUT_S])              # one VSX call, short timeout

    def test_everything_down(self):
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_variable("DY Peg")
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("HTTP 503", cm.exception.message)


# ---- SIMBAD and add_target -----------------------------------------------------------------------------------------

class TestSimbad(Base):
    def test_parse(self):
        s = T.parse_simbad_script(fx("targets_simbad_dypeg.txt").decode())
        self.assertEqual(s["main_id"], "V* DY Peg")
        self.assertAlmostEqual(s["ra"], 347.21327418438)
        self.assertAlmostEqual(s["vmag"], 10.26)
        self.assertEqual(s["otype"], "SXPheV*")
        s = T.parse_simbad_script(fx("targets_simbad_ngc7789.txt").decode())
        self.assertEqual(s["main_id"], "NGC 7789")                  # SIMBAD pads "NGC  7789"
        self.assertIsNone(T.parse_simbad_script(fx("targets_simbad_none.txt").decode()))

    def test_resolve(self):
        self.route([(["sim-script", "query id dy peg"], "targets_simbad_dypeg.txt")])
        s = T.resolve_simbad("dy peg")
        self.assertEqual(set(s) >= {"main_id", "ra", "dec", "vmag", "otype"}, True)
        self.route([(["sim-script"], "targets_simbad_none.txt")])
        with self.assertRaises(T.NotFound):
            T.resolve_simbad("nosuchthing 12345")


class TestAddTarget(Base):
    def test_planet(self):
        self.route(planet_rules())
        t = T.add_target("hat-p-23b", WHEN)
        self.assertEqual((t.kind, t.mode, t.name), ("transit", "T", "HAT-P-23 b"))
        self.assertRegex(t.added, r"^\d{4}-\d\d-\d\d$")
        self.assertLessEqual(abs((date.fromisoformat(t.added) - date.today()).days), 1)   # midnight-safe

    def test_variable(self):
        r = self.route([
            (["hostname = 'DY Peg'"], EMPTY_NEA),
            (["api.object", "DY Peg"], "targets_vsx_dypeg.json"),
        ])
        t = T.add_target("DY Peg")
        self.assertEqual((t.kind, t.mode, t.source), ("variable", "V", "VSX"))
        self.assertFalse(any("aliaslookup" in u for u in r.calls))  # the alias service hangs on unknown names

    def test_planet_host_star(self):
        self.route([
            (["hostname = 'HD 219134'"], "targets_nea_host_hd219134.csv"),
            (["nph-aliaslookup", "HD 219134"], "targets_nea_alias_hd219134.json"),
        ])
        with self.assertRaises(T.PlanetChoice):
            T.add_target("HD 219134")

    def test_needs_mode(self):
        self.route([
            (["hostname = 'NGC 7789'"], EMPTY_NEA),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script", "NGC 7789"], "targets_simbad_ngc7789.txt"),
        ])
        with self.assertRaises(T.NeedsMode) as cm:
            T.add_target("NGC 7789")
        e = cm.exception
        self.assertEqual(e.main_id, "NGC 7789")
        self.assertAlmostEqual(e.ra, 359.3342)
        self.assertAlmostEqual(e.dec, 56.7261)
        self.assertAlmostEqual(e.vmag, 6.7)
        self.assertIn("Choose the mode", e.message)

    def test_unknown(self):
        self.route([
            (["hostname"], EMPTY_NEA),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script"], "targets_simbad_none.txt"),
        ])
        with self.assertRaises(T.NotFound) as cm:
            T.add_target("nosuchthing 12345")
        self.assertIn("No planet, variable star or SIMBAD object", cm.exception.message)

    def test_offline(self):
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.add_target("DY Peg")
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("did not answer", cm.exception.message)


# ---- catalog and Suggested transits ---------------------------------------------------------------------------------

def small_catalog():
    return T.parse_catalog_csv(fx("targets_catalog_small.csv").decode())


class TestCatalog(Base):
    def test_load_and_fallback(self):
        r = self.route([(["pscomppars", "tran_flag = 1"], "targets_catalog_small.csv")])
        rows, note = T.load_transit_catalog()
        self.assertEqual(len(rows), 14)                              # Nodur-1 b (no duration) left out
        self.assertNotIn(" in (", r.calls[0])
        self.assertIn("downloaded today", note)
        h = next(p for p in rows if p["name"] == "HAT-P-23 b")
        self.assertAlmostEqual(h["depth"], 0.1169 ** 2)
        self.assertAlmostEqual(h["t14"], 2.1792 / 24)
        r = self.route([])
        rows2, _ = T.load_transit_catalog()                         # weekly refresh: cached copy now
        self.assertEqual((len(rows2), r.calls), (14, []))
        self.route([([""], DOWN)])
        rows3, note3 = T.load_transit_catalog(refresh=True)
        self.assertEqual(len(rows3), 14)
        self.assertIn("did not answer (service unavailable, HTTP 503); using the copy from", note3)

    def test_no_copy(self):
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_):
            T.load_transit_catalog()


class TestSuggest(Base):
    def names(self, res):
        return [t.name for t in res]

    def test_golden_nights_default_options(self):
        res = T.suggest_transits(SITE, OPTICS, settings.DEFAULT_OPTIONS, NIGHTS, catalog=small_catalog())
        names = self.names(res)
        self.assertIn("WASP-12 b", names)
        self.assertIn("HAT-P-16 b", names)
        for n in ("TOI-2886 b", "WASP-80 b", "TrES-1 b", "WASP-50 b"):   # deep, but only part fits
            self.assertNotIn(n, names)
        self.assertNotIn("GJ 1214 b", names)                         # V 15.1: too faint
        self.assertNotIn("55 Cnc e", names)                          # V 5.95: too bright
        # HAT-P-23 b's 23 Oct transit is full; a 95 % Moon brightens its sky by ~2.6 mag, inside the 3.0 allowed
        # (John observed it that night)
        self.assertIn("HAT-P-23 b", names)
        self.assertEqual(len(names), len(set(names)))               # each planet once
        for t in res:
            self.assertEqual((t.kind, t.mode, t.suggested, t.enabled), ("transit", "T", True, False))
            self.assertRegex(t.notes, r"^\w{3} \d{1,2} \w{3}: ingress \d\d:\d\d, egress \d\d:\d\d \(site clock\)")
            self.assertTrue(t.planet_obj().ok())
            self.assertLessEqual(t.suggest_info["moon_penalty"], settings.DEFAULT_OPTIONS["suggest_moon_penalty_max"])
        scores = [t.suggest_info["score"] for t in res]
        self.assertEqual(scores, sorted(scores, reverse=True))
        toi = next(t for t in res if t.name == "TOI-2046 b")
        self.assertIn("⚠ Tc uncertain ±11 min", toi.notes)          # catalog ephemeris sigma > 10 min

    def test_hat_p_23_full_on_23_oct(self):
        opts = dict(settings.DEFAULT_OPTIONS, suggest_moon_penalty_max=3.5)
        res = T.suggest_transits(SITE, OPTICS, opts, NIGHTS, catalog=small_catalog())
        h = next(t for t in res if t.name == "HAT-P-23 b")
        self.assertEqual(h.suggest_info["night"], "2026-10-23")
        m = re.match(r"Fri 23 Oct: ingress (\d\d:\d\d), egress (\d\d:\d\d) \(site clock\)$", h.notes)
        self.assertIsNotNone(m, h.notes)
        gold = GOLDEN["nights"][4]["transits"]["HAT-P-23 b"]
        self.assertLessEqual(minutes_apart(m.group(1), gold[0]), 2)
        self.assertLessEqual(minutes_apart(m.group(2), gold[2]), 2)
        # the 30-min pre-ingress baseline (≈20:01) is before astronomical dusk (20:07) but in nautical twilight
        self.assertLess(h.suggest_info["t1"] - 30 / 1440, sky.night_window(NIGHTS[4], SITE)[0])
        names = self.names(res)
        self.assertNotIn("TOI-2886 b", names)                        # Moon is no excuse: it only half fits

    def test_half_fit_is_the_only_reason(self):
        # TOI-2886 b passes every other test (V 12.3, depth 1.9 %, no Moon) but its 21 Oct egress is after dawn
        cat = [p for p in small_catalog() if p["name"] == "TOI-2886 b"]
        self.assertEqual(T.suggest_transits(SITE, OPTICS, {}, NIGHTS, catalog=cat), [])
        p = dict(cat[0], t0=cat[0]["t0"] - 0.04, t14=0.04)         # shorter and ~1 h earlier: now it fits
        self.assertEqual(self.names(T.suggest_transits(SITE, OPTICS, {}, NIGHTS, catalog=[p])), ["TOI-2886 b"])

    def test_options_exclude_and_max(self):
        cat = small_catalog()
        res = T.suggest_transits(SITE, OPTICS, {"suggest_max": 2}, NIGHTS, catalog=cat)
        self.assertEqual(len(res), 2)
        res = T.suggest_transits(SITE, OPTICS, {}, NIGHTS, exclude_names={"wasp-12 b", "HAT-P-70 b"}, catalog=cat)
        self.assertNotIn("WASP-12 b", self.names(res))
        self.assertNotIn("HAT-P-70 b", self.names(res))
        res = T.suggest_transits(SITE, OPTICS, {"suggest_min_depth": 0.012}, NIGHTS, catalog=cat)
        self.assertTrue(all(t.planet["depth"] >= 0.012 for t in res))
        res = T.suggest_transits(SITE, OPTICS, {"suggest_vmax": 11.0}, NIGHTS, catalog=cat)
        self.assertTrue(all(t.vmag <= 11.0 for t in res))

    def test_limits_respected(self):
        import numpy as np
        res = T.suggest_transits(SITE, OPTICS, {}, NIGHTS, catalog=small_catalog())
        for t in res:
            i = t.suggest_info
            g = np.linspace(i["t1"] - 30 / 1440, i["t4"] + 30 / 1440, 40)
            self.assertTrue(sky.up_mask(t.ra, t.dec, SITE, g).all(), t.name)
            d = date.fromisoformat(i["night"])
            dusk, dawn = sky.night_window(d, SITE)
            self.assertTrue(dusk <= i["t1"] and i["t4"] <= dawn, t.name)

    def test_noise_proxy(self):
        a = T.noise_per_minute(10.0, 21.5, OPTICS)
        b = T.noise_per_minute(13.0, 21.5, OPTICS)
        c = T.noise_per_minute(13.0, 19.0, OPTICS)
        self.assertLess(a, b)
        self.assertLess(b, c)                                        # Moon-brightened sky adds noise
        self.assertTrue(0.0005 < a < 0.005)


def minutes_apart(a: str, b: str) -> int:
    ha, ma = map(int, a.split(":"))
    hb, mb = map(int, b.split(":"))
    d = abs((ha * 60 + ma) - (hb * 60 + mb))
    return min(d, 1440 - d)


# ---- next event text -----------------------------------------------------------------------------------------------

def noon(d: int) -> float:
    return sky.local_jd(date(2026, 10, d), 12, 0, SITE.tz)


class TestNextEvent(unittest.TestCase):
    def check_transit(self, name, from_day, want_day, gold_night_idx):
        s = T.next_event_text(GT[name], SITE, noon(from_day))
        m = re.match(r"^(\w{3} \d{1,2} \w{3}) (\d\d:\d\d)–(\d\d:\d\d) \(full\)$", s)
        self.assertIsNotNone(m, s)
        self.assertEqual(m.group(1), want_day)
        gold = GOLDEN["nights"][gold_night_idx]["transits"][name]
        self.assertLessEqual(minutes_apart(m.group(2), gold[0]), 2, s)
        self.assertLessEqual(minutes_apart(m.group(3), gold[2]), 2, s)
        return s

    def test_transits(self):
        self.check_transit("TOI-2046 b", 20, "Tue 20 Oct", 1)
        self.check_transit("K2-29 b", 22, "Fri 23 Oct", 3)           # after midnight: the calendar date
        self.check_transit("HAT-P-23 b", 19, "Fri 23 Oct", 4)

    def test_partial_and_none(self):
        cat = {p["name"]: p for p in small_catalog()}
        p = cat["TOI-2886 b"]
        t = Target(name=p["name"], kind="transit", mode="T", ra=p["ra"], dec=p["dec"],
                   planet={k: p[k] for k in ("name", "period", "t0", "t14", "depth")})
        s = T.next_event_text(t, SITE, noon(21), days=1)
        self.assertRegex(s, r"^(Wed 21|Thu 22) Oct \d\d:\d\d–\d\d:\d\d \(partial\)$")
        long_p = Target(name="Slow b", kind="transit", ra=p["ra"], dec=p["dec"],
                        planet={"name": "Slow b", "period": 400.0, "t0": noon(21) + 100, "t14": 0.2})
        self.assertEqual(T.next_event_text(long_p, SITE, noon(21)), "none in 30 nights")
        self.assertEqual(T.next_event_text(Target(name="x", kind="transit", ra=1, dec=1), SITE, noon(21)),
                         "no ephemeris")

    def test_variables(self):
        self.assertEqual(T.next_event_text(GT["DY Peg"], SITE, noon(22)), "any time")
        algol = Target(name="bet Per", kind="variable", ra=47.04221, dec=40.95567,
                       elements={"vtype": "EA/SD", "period": 2.867343, "epoch": 2457675.72, "epoch_kind": "min",
                                 "any_time": False})
        s = T.next_event_text(algol, SITE, noon(22))
        self.assertRegex(s, r"^min \w{3} \d{1,2} \w{3} \d\d:\d\d$")
        # the event is in darkness and above the limits, on the site's clock
        m = re.match(r"^min \w{3} (\d{1,2}) (\w{3}) (\d\d):(\d\d)$", s)
        d = date(2026, 10 if m.group(2) == "Oct" else 11, int(m.group(1)))
        jd = sky.local_jd(d, int(m.group(3)), int(m.group(4)), SITE.tz)
        self.assertLess(float(sky.sun_alt_at(SITE, jd)), SITE.sun_alt + 0.5)
        self.assertTrue(bool(sky.up_mask(algol.ra, algol.dec, SITE, [jd])[0]))

    def test_campaigns(self):
        s = T.next_event_text(GT["HD 219134"], SITE, noon(22))
        # golden 22 Oct night: "No HD 219134 after 04:28" (planet c transits 04:28–05:53)
        self.assertEqual(s, "every night; next blackout Fri 23 Oct 04:28–05:53 (HD 219134 c transits)")
        self.assertEqual(T.next_event_text(GT["NGC 7789"], SITE, noon(22)), "every night")

    def test_site_zone_not_computer_zone(self):
        """Same strings with the computer's zone set to Tokyo (the site is in New York)."""
        want = [T.next_event_text(GT[n], SITE, noon(20)) for n in ("TOI-2046 b", "K2-29 b", "HD 219134")]
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Tokyo" if old != "Asia/Tokyo" else "America/Los_Angeles"
            time.tzset()
            got = [T.next_event_text(GT[n], SITE, noon(20)) for n in ("TOI-2046 b", "K2-29 b", "HD 219134")]
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.assertEqual(want, got)
        self.assertTrue(want[0].startswith("Tue 20 Oct 21:3"))

    def test_other_site_zone(self):
        """A site whose clock differs from New York: Tokyo times for a Tokyo site."""
        site = Site(name="JP", lat=35.0, lon=139.0, tz="Asia/Tokyo")
        t = GT["HAT-P-23 b"]
        s = T.next_event_text(t, site, sky.local_jd(date(2026, 10, 19), 12, 0, "Asia/Tokyo"))
        m = re.match(r"^\w{3} (\d{1,2}) \w{3} (\d\d):(\d\d)–", s)
        self.assertIsNotNone(m, s)
        from models import Planet
        import events
        evs = events.transit_events(Planet.from_dict(t.planet), t.ra, t.dec, noon(19), noon(19) + 30)
        local = [(sky.to_local(e["t1"], "Asia/Tokyo").day, sky.hhmm(e["t1"], "Asia/Tokyo")) for e in evs]
        self.assertIn((int(m.group(1)), f"{m.group(2)}:{m.group(3)}"), local, s)

    def test_no_site(self):
        self.assertEqual(T.next_event_text(GT["DY Peg"], Site(), noon(22)), "set up your site first")


# ---- campaign progress ---------------------------------------------------------------------------------------------

class TestCampaignProgress(unittest.TestCase):
    def test_week_counts_done_only(self):
        hd = GT["HD 219134"]
        other = GT["NGC 7789"]
        today = date(2026, 10, 22)                                   # Thursday; week Mon 19 – Sun 25
        history = [
            {"date": "2026-10-19", "target_id": hd.id, "name": hd.name, "status": "done", "minutes": 90},
            {"date": "2026-10-20", "target_id": hd.id, "name": hd.name, "status": "partial", "minutes": 40},
            {"date": "2026-10-18", "target_id": hd.id, "name": hd.name, "status": "done", "minutes": 90},  # last week
            {"date": "2026-10-19", "target_id": other.id, "name": other.name, "status": "done", "minutes": 90},
            {"date": "2026-10-21", "target_id": "", "name": "hd 219134", "status": "done", "minutes": 60},  # by name
        ]
        b1 = Block(target_id=hd.id, name=hd.name, status="done").to_dict()
        b2 = Block(target_id=hd.id, name=hd.name, status="done").to_dict()
        b3 = Block(target_id=hd.id, name=hd.name, status="clouded").to_dict()
        nights = [NightPlan(date="2026-10-22", blocks=[b1, b2]).to_dict(),     # two blocks, one night
                  NightPlan(date="2026-10-23", blocks=[b3]).to_dict(),
                  NightPlan(date="2026-10-26", blocks=[dict(b1)]).to_dict()]  # next week
        sch = Schedule(nights=nights, history=history)
        self.assertEqual(T.campaign_progress(hd, sch, today), (3, 5))  # 19, 21, 22
        self.assertEqual(T.campaign_progress(other, sch, today), (1, 1))
        self.assertEqual(T.campaign_progress(hd, Schedule(), today), (0, 5))


# ---- review fixes (Fable review of 22 Oct) ---------------------------------------------------------------------------

class TestReview(Base):
    def test_http_exceptions_become_fetch_errors(self):
        import http.client
        orig = T.urllib.request.urlopen
        try:
            for exc in (http.client.IncompleteRead(b"x"), http.client.BadStatusLine("garbage"),
                        http.client.RemoteDisconnected("closed")):
                def boom(*a, _e=exc, **k):
                    raise _e
                T.urllib.request.urlopen = boom
                with self.assertRaises(T.FetchError) as cm:
                    T.http_get("https://example.org")
                self.assertIn("network error", cm.exception.reason)
        finally:
            T.urllib.request.urlopen = orig

    def test_cut_off_reply_falls_back_to_cache(self):
        self.route([(["api.object", "DY Peg"], "targets_vsx_dypeg.json")])
        T.lookup_variable("DY Peg", refresh=True)
        self.route([([""], T.FetchError("network error: the reply was cut off or garbled (IncompleteRead)"))])
        t = T.lookup_variable("DY Peg", refresh=True)
        self.assertEqual(t.name, "DY Peg")
        self.assertIn("cut off", t.lookup_note)

    def test_archive_down_and_simbad_says_planet(self):
        self.route([
            (["exoplanetarchive"], DOWN),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script"], b"WASP-12b|097.6366525384800 +29.6722954173500||Planet|Pl\n"),
        ])
        with self.assertRaises(T.LookupError_) as cm:
            T.add_target("wasp-12 b")
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("NASA Exoplanet Archive did not answer", cm.exception.message)
        self.assertIn("HTTP 503", cm.exception.message)

    def test_archive_down_for_a_star_name_is_reported_not_needs_mode(self):
        self.route([
            (["exoplanetarchive"], DOWN),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script"], "targets_simbad_ngc7789.txt"),
        ])
        with self.assertRaises(T.LookupError_) as cm:
            T.add_target("NGC 7789")
        self.assertIn("did not answer (service unavailable, HTTP 503)", cm.exception.message)

    def test_simbad_planet_with_archive_up(self):
        self.route([
            (["nph-aliaslookup"], TIMEOUT),
            (["exoplanetarchive"], EMPTY_NEA),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script"], b"Foo-1b|097.6 +29.6||Planet|Pl\n"),
        ])
        with self.assertRaises(T.NotFound) as cm:
            T.add_target("Foo-1 b")
        self.assertIn("as a planet", cm.exception.message)

    def test_unusable_cache_folder(self):
        orig = settings.cache_dir

        def no_cache():
            raise PermissionError(13, "Permission denied", "/read-only/cache")
        settings.cache_dir = no_cache
        try:
            self.route([(["api.object", "DY Peg"], "targets_vsx_dypeg.json")])
            self.assertEqual(T.lookup_variable("DY Peg").name, "DY Peg")
            self.route([([""], DOWN)])
            with self.assertRaises(T.LookupError_):
                T.lookup_variable("DY Peg")
        finally:
            settings.cache_dir = orig

    def test_damaged_cache_files(self):
        path = T._cache_path("VSX", "vsx:variable:dy peg")
        for text in ("{not json",
                     '{"format":"shobs-lookup-cache/1","key":"vsx:variable:dy peg","fetched_unix":"abc",'
                     '"data":{"name":"X"}}',
                     '{"format":"shobs-lookup-cache/1","key":"vsx:variable:dy peg","fetched_unix":1,'
                     '"data":"garbage"}'):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.assertEqual(T.cache_read("VSX", "vsx:variable:dy peg"), (None, None))
            self.route([(["api.object", "DY Peg"], "targets_vsx_dypeg.json")])
            self.assertEqual(T.lookup_variable("DY Peg").name, "DY Peg")
            os.remove(path)

    def test_apostrophes_kept_and_escaped(self):
        self.assertEqual(T._clean("Barnard\u2019s  Star"), "Barnard's Star")
        r = self.route([
            (["hostname = 'Barnard''s Star'"], EMPTY_NEA),
            (["api.object", "Barnard's Star"], "targets_vsx_none.json"),
            (["sim-script", "query id Barnard's Star"], b"NAME Barnard's Star|269.452 +04.693|9.51|BYDraV*|BY*\n"),
        ])
        with self.assertRaises(T.NeedsMode) as cm:
            T.add_target("Barnard\u2019s Star")
        self.assertEqual(cm.exception.main_id, "NAME Barnard's Star")
        self.assertEqual(len(r.calls), 3)

    def test_unreadable_simbad_reply(self):
        with self.assertRaises(T.FetchError):
            T.parse_simbad_script("<html><body>SIMBAD is temporarily unavailable</body></html>")
        with self.assertRaises(T.FetchError):
            T.parse_simbad_script("V* DY Peg|347.21")                  # cut off
        self.route([
            (["exoplanetarchive"], EMPTY_NEA),
            (["api.object"], "targets_vsx_none.json"),
            (["sim-script"], b"<html><body>SIMBAD is temporarily unavailable</body></html>"),
        ])
        with self.assertRaises(T.LookupError_) as cm:
            T.add_target("DY Peg")
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("SIMBAD did not answer", cm.exception.message)

    def test_non_transiting_planet_needs_mode(self):
        comp = fx("targets_nea_comp_tres3b.csv").decode()
        head, row = comp.strip().split("\n")
        cells = row.split(",")
        cols = head.split(",")
        cells[cols.index("tran_flag")] = "0"
        not_transiting = (head + "\n" + ",".join(cells) + "\n").encode()
        self.route([
            (["nph-aliaslookup"], TIMEOUT),
            (["pscomppars", "pl_name = 'TrES-3 b'"], not_transiting),
            (["from ps where"], EMPTY_NEA),
            (["pscomppars"], EMPTY_NEA),
        ])
        with self.assertRaises(T.NeedsMode) as cm:
            T.lookup_planet("TrES-3 b", WHEN)
        e = cm.exception
        self.assertEqual((e.main_id, e.source), ("TrES-3 b", "the NASA Exoplanet Archive"))
        self.assertAlmostEqual(e.ra, 268.0291106)
        self.assertAlmostEqual(e.vmag, 12.362)
        self.assertIn("does not transit", e.message)

    def test_planet_without_duration_needs_mode(self):
        import csv, io
        row = next(csv.DictReader(io.StringIO(fx("targets_nea_comp_tres3b.csv").decode())))
        row["pl_trandur"] = ""
        clear_cache()
        T.cache_write("NEA", "nea:planet:tres-3 b", "TrES-3 b", {"composite": row, "ps": []})
        self.route([])
        with self.assertRaises(T.NeedsMode) as cm:
            T.lookup_planet("TrES-3 b", WHEN)
        self.assertIn("no transit ephemeris", cm.exception.message)

    def test_error_text_instead_of_csv(self):
        self.route([(["nph-aliaslookup"], TIMEOUT), ([""], b"ERROR\nsomething went wrong\n")])
        with self.assertRaises(T.LookupError_) as cm:
            T.lookup_planet("WASP-12 b", WHEN)
        self.assertNotIsInstance(cm.exception, T.NotFound)
        self.assertIn("unexpected reply", cm.exception.message)
        self.route([([""], b"")])
        with self.assertRaises(T.FetchError):
            T.vizier_query("SELECT TOP 5 * FROM \"B/vsx/vsx\" WHERE Name = 'x'")

    def test_utc_and_hjd_rows_are_demoted(self):
        base = {"pl_orbper": "2.0", "pl_orbpererr1": "1e-6", "pl_tranmid": "2460000.0", "pl_tranmiderr1": "0.0005"}
        utc = dict(base, pl_tsystemref="BJD-UTC", pl_refname="UTC row")
        tdb = dict(base, pl_tranmiderr1="0.0006", pl_tsystemref="BJD-TDB", pl_refname="TDB row")
        self.assertEqual(T.most_precise_ephemeris([utc, tdb], WHEN)["reference"], "TDB row")
        self.assertEqual(T.most_precise_ephemeris([dict(utc, pl_tsystemref=""), tdb], WHEN)["reference"], "UTC row")
        for ref, doubtful in (("HJD", True), ("HJD-UTC", True), ("JD", True), ("BJD", False), ("BJD-TDB", False),
                              ("", False)):
            self.assertEqual(T._time_system_doubtful(ref), doubtful, ref)

    def test_tran_flag_values(self):
        self.assertTrue(T._transits({"tran_flag": "1"}))
        self.assertTrue(T._transits({"tran_flag": "1.0"}))
        self.assertTrue(T._transits({"tran_flag": " 1 "}))
        self.assertFalse(T._transits({"tran_flag": "0"}))
        self.assertFalse(T._transits({}))

    def test_single_star_otypes(self):
        for o in ("*", "SB*", "EB*", "V*", "SX*", "RR*", "Mi*", "BY*"):
            self.assertTrue(T.is_single_star_otype(o), o)
        for o in ("Cl*", "OpC", "GlC", "As*", "**", "Gr*", "C?*", "MGr", "St*", "G", "AGN", "", None, "Pl"):
            self.assertFalse(T.is_single_star_otype(o), o)

    def test_added_is_the_sites_tonight(self):
        tokyo = Site(name="JP", lat=35.0, lon=139.0, tz="Asia/Tokyo")
        self.assertEqual(T.today_iso(tokyo), sky.night_date_of(sky.now_jd(), "Asia/Tokyo").isoformat())
        res = T.suggest_transits(SITE, OPTICS, {}, NIGHTS, catalog=small_catalog())
        self.assertTrue(res)
        want = sky.night_date_of(sky.now_jd(), SITE.tz).isoformat()
        self.assertTrue(all(t.added == want for t in res))


class TestHostPlanets(Base):
    """A star's planets for "no data while a planet transits" (targets_nea_hostrows_hd219134.csv and
    targets_nea_ps_hd219134{b,c}.csv recorded live on 22 Oct 2026)."""

    HOST = [(["from ps where pl_name = 'HD 219134 b'"], "targets_nea_ps_hd219134b.csv"),
            (["from ps where pl_name = 'HD 219134 c'"], "targets_nea_ps_hd219134c.csv")]

    def test_host_name_variants(self):
        self.assertEqual(T.host_name_variants("HD219134")[:2], ["HD219134", "HD 219134"])
        self.assertIn("WASP-12", T.host_name_variants("wasp 12"))
        self.assertEqual(T.host_name_variants("* 51 Peg"), ["51 Peg"])
        self.assertIn("HAT-P-23", T.host_name_variants("hat-p 23"))
        self.assertEqual(T.host_name_variants("Gliese 876"), ["Gliese 876", "Gliese-876"])

    def check_hd219134(self, info):
        names = [p["name"] for p in info["planets"]]
        self.assertEqual(names[:2], ["HD 219134 b", "HD 219134 c"], "transiting (usable) ones first")
        self.assertEqual(set(names), {f"HD 219134 {x}" for x in "bcdfgh"})
        by = {p["name"]: p for p in info["planets"]}
        for x in "bc":
            p = by[f"HD 219134 {x}"]
            self.assertTrue(p["transits"] and p["usable"])
            self.assertTrue(p["planet"]["period"] and p["planet"]["t0"] and p["planet"]["t14"])
        d = by["HD 219134 d"]
        self.assertFalse(d["usable"])
        self.assertIsNone(d["planet"])
        self.assertEqual(d["why"], "does not transit")

    def test_by_host_name_spelling(self):
        r = self.route([(["hostname = 'HD219134'"], EMPTY_NEA),
                        (["hostname = 'HD 219134'"], "targets_nea_hostrows_hd219134.csv")] + self.HOST)
        info = T.host_planets("HD219134")
        self.assertEqual((info["host"], info["matched"], info["note"]), ("HD 219134", "name", ""))
        self.check_hd219134(info)
        self.assertFalse(any("ra between" in u for u in r.calls), "no position query when the name matched")
        n = len(r.calls)
        T.host_planets("HD219134")
        self.assertEqual(len(r.calls), n, "a cached answer makes no calls")

    def test_by_position(self):
        r = self.route([(["hostname ="], EMPTY_NEA), (["ra between"], "targets_nea_hostrows_hd219134.csv")]
                       + self.HOST)
        info = T.host_planets("My Odd Name", 348.320833, 57.168333, 5.57)      # SIMBAD J2000: ~32″ from Gaia's
        self.assertEqual((info["host"], info["matched"]), ("HD 219134", "position"))
        self.check_hd219134(info)
        q = next(u for u in r.calls if "ra between" in u)
        self.assertIn("dec between 57.15", q)
        self.assertNotIn("CONTAINS", q.upper(), "the Archive's Cloudflare front refuses CONTAINS/CIRCLE")

    def test_position_needs_matching_brightness_and_distance(self):
        self.route([(["hostname ="], EMPTY_NEA), (["ra between"], "targets_nea_hostrows_hd219134.csv")])
        info = T.host_planets("Faint Neighbour", 348.320833, 57.168333, 12.0)
        self.assertEqual((info["matched"], info["planets"]), ("", []))
        clear_cache()
        info = T.host_planets("Further Away", 348.3372, 57.19, None)           # ~74″ away
        self.assertEqual(info["planets"], [])

    def test_ra_wrap(self):
        r = self.route([(["hostname ="], EMPTY_NEA), (["ra between"], EMPTY_NEA)])
        T.host_planets("Near Zero", 0.001, 10.0)
        boxes = [u for u in r.calls if "ra between" in u]
        self.assertEqual(len(boxes), 2, "two boxes either side of RA 0")

    def test_no_planets_and_offline(self):
        self.route([(["hostname ="], EMPTY_NEA), (["ra between"], EMPTY_NEA)])
        info = T.host_planets("DY Peg", 347.2, 17.2, 10.3)
        self.assertEqual((info["matched"], info["planets"]), ("", []))
        clear_cache()
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.host_planets("HD 219134", 348.32, 57.17)
        self.assertIn("did not answer", cm.exception.message)
        # an old copy is used when the Archive is down, with its age
        self.route([(["hostname = 'HD 219134'"], "targets_nea_hostrows_hd219134.csv"), (["from ps"], DOWN)])
        info = T.host_planets("HD 219134")
        self.assertTrue(info["planets"][0]["usable"], "composite ephemeris when the ps table is down")
        T.PLANET_MAX_AGE_DAYS, old = -1.0, T.PLANET_MAX_AGE_DAYS
        try:
            self.route([([""], DOWN)])
            info = T.host_planets("HD 219134")
        finally:
            T.PLANET_MAX_AGE_DAYS = old
        self.assertIn("using the copy from", info["note"])
        self.assertEqual(len(info["planets"]), 6)

    def test_planet_letter(self):
        self.assertEqual(T.planet_letter(" C "), "c")
        self.assertEqual(T.planet_letter("b"), "b")
        self.assertEqual(T.planet_letter("a"), "")
        self.assertEqual(T.planet_letter("bc"), "")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
