"""SHOBS-SCHED 1.4.1, GitHub issue #6: one balanced exposure rule with three bounds (saturation of the target AND the
stars that will be measured, a time-resolution cap, precision inside them), the readout naming the bound that won,
and 'When nothing fits: SCHED always answers' (one test per row of the issue's table: the exposure is a number and
the explanation is a non-empty sentence, never a bare '?'). Also the CDS skip (1.4.1 nit)."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comps  # noqa: E402
import planner  # noqa: E402
import targets as tg  # noqa: E402
from models import Planet, Target, VarElements  # noqa: E402

from tests.test_review140b import Home, optics, transit  # noqa: E402

S = planner.STANDARD_EXPOSURES


def sched_home(test):
    d = tempfile.mkdtemp(prefix="sched_141_")
    old = os.environ.get("SHOBS_SCHED_HOME")
    os.environ["SHOBS_SCHED_HOME"] = d
    test.addCleanup(lambda: os.environ.__setitem__("SHOBS_SCHED_HOME", old) if old is not None
                    else os.environ.pop("SHOBS_SCHED_HOME", None))
    oldp = os.environ.get("SHOBS_P_SETTINGS")
    os.environ["SHOBS_P_SETTINGS"] = os.path.join(d, "no_shobs_p.json")      # SHOBS-P not installed
    test.addCleanup(lambda: os.environ.__setitem__("SHOBS_P_SETTINGS", oldp) if oldp is not None
                    else os.environ.pop("SHOBS_P_SETTINGS", None))
    comps._SERIES_CACHE.clear()
    comps._UNREACHABLE.clear()
    comps._DOWN["at"] = None
    return d


def variable(v=11.6, period=0.149082, kind="campaign", amp=0.07, **kw):
    el = VarElements(vtype="DSCT", period=period, epoch=2460000.0, any_time=True, mag_max=v, mag_min=None if v is None else v + amp)
    return Target(name="GSC 03308-00462", kind=kind, mode="D" if kind == "campaign" else "V", ra=300.0, dec=50.0,
                  vmag=v, elements=el.__dict__.copy(), **kw)


def write_series(folder, comps_v, check_v=None, active=None, name="s.cvseries"):
    meta = {"star_id": "GSC 03308-00462"}
    roles = ["comp"] + [f"comp{i}" for i in range(2, 11)]
    for r, (nm, v) in zip(roles, comps_v):
        meta[f"{r}_name"], meta[f"{r}_mag"] = nm, f"{v}"
    if check_v is not None:
        meta["check_name"], meta["check_mag"] = "CHK", f"{check_v}"
    if active is not None:
        meta["active_comps"] = active
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "meta": meta, "observations": []}, fh)
    return p


def save_cone(t, op, rows):
    rad = tg.comp_radius_arcmin(op)
    tg.cache_write("VizieR", tg._comp_key(t.ra, t.dec, rad), "test cone", {"rows": rows, "radius_arcmin": rad})


def all_words(p):
    return [p.get("note") or ""] + list(p.get("exposure_cautions") or []) + list(p.get("exposure_notes") or [])


class AlwaysAnswers:
    def check_answer(self, p):
        e = p.get("exposure_s")
        self.assertIsInstance(e, float)
        self.assertTrue(math.isfinite(e) and e > 0, e)
        words = [w for w in all_words(p) if w]
        self.assertTrue(words, "an explanation comes back")
        for w in words:
            self.assertNotEqual(w.strip(), "?")
            self.assertGreater(len(w.strip()), 3, w)


class Ingress(unittest.TestCase):
    def test_from_b_and_k(self):
        pl = Planet(t14=3.0 / 24, ratror=0.1, impact=0.0)
        ing, est = planner.ingress_s(pl)
        self.assertFalse(est)
        self.assertAlmostEqual(ing, 3 * 3600 * 0.1, delta=1)            # 18 min
        cap = planner.cadence_cap(Target(kind="transit", planet=pl.__dict__.copy()))
        self.assertAlmostEqual(cap["cap_s"], 108.0, delta=0.5)
        self.assertEqual(cap["words"], "ingress 18 min ÷ 10 = 108 s")

    def test_grazing_and_unknown(self):
        ing, est = planner.ingress_s(Planet(t14=2 / 24, ratror=0.12, impact=0.99))
        self.assertTrue(est)                                              # 1 − b² too small: the 10 % rule
        self.assertAlmostEqual(ing, 720.0)
        ing, est = planner.ingress_s(Planet(t14=2 / 24))
        self.assertEqual((ing, est), (720.0, True))
        self.assertEqual(planner.ingress_s(Planet()), (None, False))
        self.assertLessEqual(planner.ingress_s(Planet(t14=1 / 24, ratror=0.9, impact=0.5))[0], 1800.0)

    def test_variable_p50(self):
        cap = planner.cadence_cap(variable())
        self.assertAlmostEqual(cap["cap_s"], 0.149082 * 86400 / 50, delta=0.01)   # 257.6 s
        self.assertEqual(cap["rule"], "P/50")
        self.assertIsNone(planner.cadence_cap(Target(kind="campaign", vmag=11, ra=1, dec=1)))   # Discovery field


class CadenceBound(unittest.TestCase):
    def test_gsc_03308_takes_180_not_300(self):
        """The case that opened #6: 1.4.0 took 300 s (the top of the range); P/50 = 258 s caps the frame cycle, so
        1.4.1 takes 180 s, what John shot (and the night was good)."""
        sched_home(self)
        Home(self)
        free = planner.exposure_plan(variable(v=13.5, period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.assertEqual(free["exposure_s"], 300.0)      # the top of the range, as 1.4.0 took
        p = planner.exposure_plan(variable(v=13.5), optics(), {}, 1.1, 0.0, 280)
        self.assertEqual(p["mode"], "safe")
        self.assertEqual(p["exposure_s"], 180.0)
        self.assertTrue(p["bound"].startswith("cadence-limited (P/50 = 4.3 min)"), p["bound"])
        self.assertIn("cap costs", p["bounds_line"])
        self.assertIn("vs 300 s", p["bounds_line"])
        self.assertEqual(p["bound_note"], "Exposure 180 s: cadence-limited (P/50 = 4.3 min)")

    def test_transit_ingress_cap_with_best_precision(self):
        sched_home(self)
        h = Home(self)
        h.measure()
        t = transit(v=13.0, t14_h=2.0)                 # ingress ~12 min (10 % of T14) ÷ 10 = 72 s
        p = planner.exposure_plan(t, optics(), {}, 1.2, 0.0, 280)
        self.assertEqual(p["mode"], "precision")
        self.assertLessEqual(p["exposure_s"] + 3.0, 72.0 + 1e-6)
        self.assertIn("ingress taken as ~10 % of T14", " ".join(p["exposure_notes"]) + p["bounds_line"]
                      if p["bound"].startswith("cadence") else "ingress taken as ~10 % of T14")

    def test_own_exposure_is_kept_and_said(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(), optics(), {}, 1.1, 0.0, 280, mode="own", exposure_s=300)
        self.assertEqual(p["exposure_s"], 300.0)
        self.assertEqual(p["bound"], "your exposure")
        self.assertTrue(any("longer than P/50" in n for n in p["exposure_notes"]), p["exposure_notes"])

    def test_cycle_s_is_the_users(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(cycle_s=400), optics(), {}, 1.1, 0.0, 280)
        self.assertTrue(any("seconds between frames (400 s)" in n for n in p["exposure_notes"]), p["exposure_notes"])


class SaturationBound(unittest.TestCase):
    def test_series_comps_cap_the_exposure(self):
        d = sched_home(self)
        Home(self)
        t = variable(v=11.6, period=2.0)               # a long period: no cadence cap in the way
        free = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        t.shobs_p_series = write_series(d, [("C1", 9.9), ("C2", 11.8)], check_v=11.0)
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.assertEqual(p["bounds"]["measured"]["source"], "series")
        self.assertLess(p["exposure_s"], free["exposure_s"])
        self.assertTrue(p["bound"].startswith("comp-limited (Comp 1 (C1), V 9.90)"), p["bound"])
        self.assertIn("(brightest comp: C1)", p["bounds_line"])
        c1 = [s for s in p["bounds"]["stars"] if s["name"] == "C1"][0]
        frac = [c for c in c1["curve"] if c["t"] == p["exposure_s"]][0]["peak_frac"]
        self.assertLessEqual(frac, 0.8)

    def test_active_comps_only(self):
        d = sched_home(self)
        path = write_series(d, [("C1", 8.0), ("C2", 11.8), ("C3", 12.0)], active=["C2", "c 3"])
        got = comps.read_series(path)
        self.assertEqual([s["name"] for s in got["stars"]], ["C2", "C3"])
        self.assertEqual(comps.read_series(os.path.join(d, "none.cvseries"))["error"], "none.cvseries not found")

    def test_series_unreadable_falls_back_with_a_note(self):
        d = sched_home(self)
        Home(self)
        bad = os.path.join(d, "bad.cvseries")
        with open(bad, "w") as fh:
            fh.write("{not json")
        t = variable(period=2.0, shobs_p_series=bad)
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.assertTrue(any(n.startswith("SHOBS-P series not used: bad.cvseries could not be read")
                            for n in p["exposure_notes"]), p["exposure_notes"])

    def test_catalog_candidates_and_field_stars(self):
        sched_home(self)
        Home(self)
        t = variable(v=11.6, period=2.0)
        op = optics()
        save_cone(t, op, [{"ra": t.ra, "dec": t.dec, "v": 11.6, "bv": 0.5},           # the target itself
                          {"ra": t.ra + 0.05, "dec": t.dec, "v": 10.4, "bv": 0.6},     # a candidate (1.2 brighter)
                          {"ra": t.ra - 0.05, "dec": t.dec, "v": 6.0, "bv": 0.6}])     # a bright field star
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        m = p["bounds"]["measured"]
        self.assertEqual(m["source"], "catalog")
        self.assertEqual([s["v"] for s in m["stars"]], [10.4])
        self.assertEqual([s["v"] for s in m["field"]], [6.0])
        self.assertTrue(p["bound"].startswith("comp-candidate-limited (APASS V 10.40"), p["bound"])
        self.assertTrue(any(n.startswith("1 brighter field star would be over the linear limit")
                            and "APASS V 6.00" in n for n in p["exposure_notes"]), p["exposure_notes"])
        self.assertFalse(any(c.startswith("⚠") and "6.00" in c for c in p["exposure_cautions"]))


class NothingFits(unittest.TestCase, AlwaysAnswers):
    """#6's table, one test per row."""

    def test_row1_saturation_even_at_the_shortest(self):
        d = sched_home(self)
        Home(self)
        t = variable(v=11.6, period=2.0)
        t.shobs_p_series = write_series(d, [("BRIGHT", 3.0), ("C2", 11.8)])
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], S[0])
        self.assertTrue(any(c.startswith("⚠ even 0.75 s may saturate Comp 1 (BRIGHT)") and "pick a fainter comp" in c
                            for c in p["exposure_cautions"]), p["exposure_cautions"])

    def test_row1_target_itself(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(v=3.0, period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], S[0])
        self.assertTrue(p["note"].startswith("⚠ even 0.75 s may saturate"), p["note"])
        self.assertIn("defocus", p["note"])

    def test_row2_timing_cap_under_the_frame_cycle(self):
        sched_home(self)
        Home(self)
        t = variable(v=13.0, period=0.002)             # P/50 = 3.5 s; the cycle at 0.75 s is 3.75 s
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], S[0])
        n = [x for x in p["exposure_notes"] if x.startswith("cadence-limited: frame cycle")]
        self.assertTrue(n, p["exposure_notes"])
        self.assertIn("can't reach P/50 = 3.5 s; timing ~1.1× coarser than the rule", n[0])
        self.assertFalse(any(c.startswith("⚠") for c in p["exposure_cautions"]))     # plain: not the user's fault

    def test_row3_both_saturation_first(self):
        d = sched_home(self)
        Home(self)
        t = variable(v=13.0, period=0.002)
        t.shobs_p_series = write_series(d, [("BRIGHT", 3.0)])
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], S[0])
        self.assertTrue(p["exposure_cautions"] and p["exposure_cautions"][0].startswith("⚠ even 0.75 s"))
        self.assertTrue(any(x.startswith("cadence-limited") for x in p["exposure_notes"]))
        b = "\n".join(planner.exposure_words(p))
        self.assertLess(b.index("⚠ even 0.75 s"), b.index("cadence-limited"))

    def test_row4_caps_leave_poor_precision(self):
        sched_home(self)
        Home(self)
        t = variable(v=16.0, period=0.004, amp=0.05)   # faint, P/50 = 7 s: Marginal? at 300 s, Out of reach? at 2 s
        p = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertTrue(p["bound"].startswith("cadence-limited"), p["bound"])
        n = [x for x in p["exposure_notes"] if x.startswith("cap costs")]
        self.assertTrue(n, (p["exposure_notes"], p["bounds_line"]))
        self.assertIn("(Marginal? at 300 s, but timing coarser than P/50)", n[0])
        self.assertIn("My exposure", n[0])
        self.assertEqual(p["verdict"], "Out of reach?")

    def test_row5_missing_inputs(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(v=None), optics(), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], 60.0)
        self.assertIn("no V magnitude", p["note"])
        p = planner.exposure_plan(variable(), optics(aperture_mm=None), {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertEqual(p["exposure_s"], 60.0)
        self.assertIn("telescope and camera not set up", p["note"])

    def test_row6_catalog_unreachable(self):
        sched_home(self)
        Home(self)
        t = variable(period=2.0)
        op = optics()
        old = tg.comp_stars

        def down(*a, **k):
            raise tg.LookupError_("VizieR did not answer (no answer within 30 s) and there is no saved copy")
        tg.comp_stars = down
        self.addCleanup(lambda: setattr(tg, "comp_stars", old))
        res = comps.prefetch([t], op)
        self.assertEqual(res["failed"], 1)
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        self.check_answer(p)
        self.assertIn("comps not checked: catalog unreachable; check comp peaks in your photometry software",
                      p["exposure_notes"])                # SHOBS-P not installed: SCHED stands alone (round 4)
        sp = os.path.join(os.environ["SHOBS_SCHED_HOME"], "p.json")
        with open(sp, "w") as fh:
            fh.write("{}")
        old_env = os.environ.get("SHOBS_P_SETTINGS")
        os.environ["SHOBS_P_SETTINGS"] = sp
        self.addCleanup(lambda: os.environ.__setitem__("SHOBS_P_SETTINGS", old_env) if old_env is not None
                        else os.environ.pop("SHOBS_P_SETTINGS", None))
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        self.assertIn("comps not checked: catalog unreachable; check comp peaks in SHOBS-P", p["exposure_notes"])
        self.assertEqual(p["bounds"]["measured"]["source"], "none")

    def test_never_asked_says_not_checked_yet(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.assertTrue(any(n.startswith("comps not checked yet") for n in p["readout_notes"]))
        self.assertFalse(any(n.startswith("comps not checked yet") for n in p["exposure_notes"]))

    def test_cold_start_too(self):
        sched_home(self)
        Home(self, copy_fixture=False)
        for t in (variable(), variable(v=3.0), variable(v=13.0, period=0.002), variable(v=None),
                  transit(v=12.0), transit(v=4.0)):
            for mode in (None, "safe", "precision"):
                p = planner.exposure_plan(t, optics(), {}, 1.2, 0.0, 280, mode=mode)
                self.check_answer(p)


class Prefetch(unittest.TestCase):
    def test_gives_up_after_two_misses_and_skips_series(self):
        d = sched_home(self)
        Home(self)
        calls = []
        old = tg.comp_stars

        def down(*a, **k):
            calls.append(a)
            raise tg.LookupError_("down")
        tg.comp_stars = down
        self.addCleanup(lambda: setattr(tg, "comp_stars", old))
        ts = [Target(name=f"T{i}", kind="variable", ra=10.0 + i, dec=20.0, vmag=11.0) for i in range(5)]
        ts[0].shobs_p_series = write_series(d, [("C1", 11.0)])
        ts[1].enabled = False
        res = comps.prefetch(ts, optics())
        self.assertEqual(len(calls), 2)                  # T2, T3; T4 not asked
        self.assertEqual(res, {"asked": 2, "got": 0, "failed": 3})


class CdsSkip(unittest.TestCase):
    def setUp(self):
        tg._CDS_DOWN.update(at=None, why="")
        self.addCleanup(lambda: tg._CDS_DOWN.update(at=None, why=""))
        self.old = tg._http_get
        self.addCleanup(lambda: setattr(tg, "_http_get", self.old))
        self.calls = []

    def test_skip_for_ten_minutes_then_retry(self):
        def fail(url, timeout=None):
            self.calls.append(url)
            raise tg.FetchError("no answer within 30 s")
        tg._http_get = fail
        url = "https://simbad.cds.unistra.fr/simbad/sim-script?x"
        with self.assertRaises(tg.FetchError):
            tg.http_get(url)
        with self.assertRaises(tg.FetchError) as cm:
            tg.http_get("https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync?q")
        self.assertEqual(len(self.calls), 1)             # the second CDS host was not asked
        self.assertIn("skipped for 10 min", cm.exception.reason)
        # other hosts are asked as usual
        with self.assertRaises(tg.FetchError):
            tg.http_get("https://vizier.cfa.harvard.edu/viz-bin/asu-tsv?x")
        self.assertEqual(len(self.calls), 2)
        # 10 minutes later CDS is tried again, and an answer ends the skip
        tg._CDS_DOWN["at"] -= tg.CDS_SKIP_S + 1
        tg._http_get = lambda url, timeout=None: b"ok"
        self.assertEqual(tg.http_get(url), b"ok")
        self.assertIsNone(tg._CDS_DOWN["at"])

    def test_a_4xx_does_not_start_the_skip(self):
        def nf(url, timeout=None):
            raise tg.FetchError("page not found, HTTP 404", 404)
        tg._http_get = nf
        with self.assertRaises(tg.FetchError):
            tg.http_get("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?x")
        self.assertIsNone(tg.cds_skip_reason())
        tg._CDS_DOWN.update(at=None)

        def busy(url, timeout=None):
            raise tg.FetchError("service unavailable, HTTP 503", 503)
        tg._http_get = busy
        with self.assertRaises(tg.FetchError):
            tg.http_get("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?x")
        self.assertIsNotNone(tg.cds_skip_reason())


class BackTestFixes(unittest.TestCase):
    """The release gate's findings (#6): the peak calibration (measured zero point only), the comps in the
    10-minute precision, the short-exposure note."""

    def test_peak_factor(self):
        import snr
        self.assertAlmostEqual(snr.peak_factor(0.75), 1.85, delta=0.01)
        self.assertAlmostEqual(snr.peak_factor(60), 1.45, delta=0.01)
        self.assertAlmostEqual(snr.peak_factor(180), 1.37, delta=0.01)
        self.assertGreaterEqual(snr.peak_factor(1e9), 1.0)
        self.assertLessEqual(snr.peak_factor(0.001), 2.0)
        inp = snr.Inputs(zp=20.6, fwhm=4.9, scale=0.267, gain=0.243)
        a = snr.peak_adu(inp, 11.0, 60)
        b = snr.peak_adu(snr.replace(inp, peak_cal=True), 11.0, 60)
        self.assertGreater(b / a, 1.35)

    def test_calibration_only_with_a_measured_zero_point(self):
        sched_home(self)
        h = Home(self)
        p = planner.exposure_plan(variable(period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.assertFalse(p["inputs"].peak_cal)
        h.measure()
        q = planner.exposure_plan(variable(period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.assertTrue(q["inputs"].peak_cal)
        Home(self, copy_fixture=False)
        self.assertFalse(planner.exposure_plan(variable(), optics(), {}, 1.1)["inputs"].peak_cal)

    def test_series_comps_join_the_precision(self):
        d = sched_home(self)
        Home(self)
        t = variable(v=6.0, period=2.0)
        alone = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280, mode="own", exposure_s=1)
        t.shobs_p_series = write_series(d, [("C1", 9.5), ("C2", 10.0)])
        withc = planner.exposure_plan(t, optics(), {}, 1.1, 0.0, 280, mode="own", exposure_s=1)
        self.assertGreater(withc["sigma"], alone["sigma"] * 1.2)            # faint comps dominate a bright target
        self.assertAlmostEqual(withc["sigma_target"], alone["sigma"], delta=1e-12)
        self.assertEqual(withc["sigma_with"], "the target and its 2 comps (SHOBS-P series)")
        self.assertEqual(alone["sigma_with"], "the target star alone")

    def test_short_exposure_note(self):
        sched_home(self)
        Home(self)
        p = planner.exposure_plan(variable(v=7.0, period=2.0), optics(), {}, 1.1, 0.0, 280)
        self.assertLess(p["exposure_s"], planner.SHORT_EXPOSURE_S)
        self.assertTrue(any(n.startswith("short exposure: the peak varies") for n in p["exposure_notes"]))


class ReviewFixes(unittest.TestCase):
    """The Fable review of build-1.4.1 (S2-1 … S3)."""

    def cone(self, t, n=60):
        rows = [{"ra": t.ra, "dec": t.dec, "v": t.vmag}]
        for i in range(n):
            rows.append({"ra": t.ra + 0.002 * (i + 1), "dec": t.dec, "v": 7.0 + 0.1 * i})
        return rows

    def test_one_line_for_many_field_stars_and_candidates(self):
        sched_home(self)
        Home(self)
        t = variable(v=12.6, period=2.0)
        op = optics()
        save_cone(t, op, self.cone(t))
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        self.assertEqual(sum(1 for n in p["exposure_notes"] if "field star" in n), 1, p["exposure_notes"])
        q = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280, mode="own", exposure_s=120)
        self.assertFalse([c for c in q["exposure_cautions"] if c.startswith("⚠ Your exposure")], q["exposure_cautions"])
        self.assertLessEqual(len(q["exposure_notes"]), 5, q["exposure_notes"])

    def test_bright_target_says_apass_is_incomplete(self):
        sched_home(self)
        Home(self)
        t = variable(v=9.0, period=2.0)
        op = optics()
        save_cone(t, op, [{"ra": t.ra + 0.01, "dec": t.dec, "v": 9.5}])
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        self.assertTrue(any("may be missing (APASS is incomplete" in n for n in p["exposure_notes"]))

    def test_ra_wrap(self):
        c, b = comps.catalog_split([{"ra": 359.999, "dec": 10.0, "v": 11.0}, {"ra": 0.02, "dec": 10.0, "v": 10.5}],
                                   0.0005, 10.0, 11.0)
        self.assertEqual([s["v"] for s in c], [10.5])        # the target's own row left out across RA 0
        self.assertIn("(1.2′ away)", c[0]["name"])

    def test_unnamed_series_comp(self):
        d = sched_home(self)
        p = write_series(d, [("", 9.0), ("C2", 11.0)], check_v=10.0)
        meta = json.load(open(p))
        meta["meta"]["check_name"] = ""
        json.dump(meta, open(p, "w"))
        got = comps.read_series(p)
        self.assertEqual([(s["role"], s["name"]) for s in got["stars"]],
                         [("Comp 1", "comp"), ("Comp 2", "C2"), ("Check", "check")])

    def test_prefetch_deadline_and_down_memory(self):
        sched_home(self)
        Home(self)
        calls = []
        old = tg.comp_stars

        def slow(*a, **k):
            calls.append(tg._QUICK.cap)
            raise tg.LookupError_("no answer")
        tg.comp_stars = slow
        self.addCleanup(lambda: setattr(tg, "comp_stars", old))
        ts = [Target(name=f"T{i}", kind="variable", ra=10.0 + i, dec=20.0, vmag=11.0) for i in range(4)]
        comps.prefetch(ts, optics())
        self.assertEqual(calls, [comps.PREFETCH_TIMEOUT_S] * 2)    # the short timeout, then it gave up
        res = comps.prefetch(ts, optics())                          # the next Build: not asked again
        self.assertEqual((len(calls), res["asked"], res["failed"]), (2, 0, 4))
        comps._DOWN["at"] -= comps.PREFETCH_DOWN_S + 1
        comps.prefetch(ts, optics())
        self.assertEqual(len(calls), 4)
        self.assertIsNone(getattr(tg._QUICK, "cap", None))

    def test_quick_timeouts_cap(self):
        seen = []
        old = tg._http_get
        tg._http_get = lambda url, timeout=None: seen.append(timeout) or b"x"
        self.addCleanup(lambda: setattr(tg, "_http_get", old))
        with tg.quick_timeouts(8):
            tg.http_get("https://example.org/a", timeout=30)
        tg.http_get("https://example.org/a", timeout=30)
        self.assertEqual(seen, [8.0, 30])


class PlannerNotes(unittest.TestCase):
    def test_planet_lookup_keeps_b_and_k(self):
        row = {"pl_name": "W b", "pl_orbper": "1.09", "pl_tranmid": "2457010.5", "pl_trandur": "3.0",
               "pl_ratror": "0.117", "pl_imppar": "0.34", "ra": "97.6", "dec": "29.7", "sy_vmag": "11.6"}
        t = tg.planet_target({"composite": row, "ps": []}, 2460000.0)
        self.assertAlmostEqual(t.planet["ratror"], 0.117)
        self.assertAlmostEqual(t.planet["impact"], 0.34)
        self.assertIn("pl_imppar", tg.NEA_COLUMNS)


if __name__ == "__main__":
    unittest.main()
