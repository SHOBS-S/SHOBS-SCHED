"""1.4.0 item 1 (GitHub issue #4) and item 2: the noise model (snr.py), the shared profile copy (shobs_profile.py),
reading SHOBS-FLUX's profiles (instrument.py) and the planner's exposure by S/N (planner.exposure_plan)."""
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="sched_snr_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "none.json")

import instrument  # noqa: E402
import planner  # noqa: E402
import shobs_profile as sp  # noqa: E402
import snr  # noqa: E402
from models import Optics, Planet, Target, VarElements  # noqa: E402

FX = os.path.join(HERE, "fixtures", "shobs_data")
SYS_ID = "14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1"
GOLDEN_OPTICS = {"aperture_mm": 356, "focal_mm": 2900, "pixel_um": 3.76, "binning": 1, "camera": "ZWO ASI2600MC Air",
                 "telescope": "14in RC", "color_camera": True, "gain": 100, "e_per_adu": 0.243, "full_well_e": 50000,
                 "linear_limit_adu": 32000}
# the shared file as copied from SHOBS-FLUX (master) at version 1.2.0: a stale or edited copy fails here
PINNED_SHA256 = "e41405b94bed1c5dcf2fb870155e5085f8500c73fdbb8eae2e706774604e94c9"


def optics(**kw):
    d = dict(GOLDEN_OPTICS)
    d.update(kw)
    return Optics.from_dict(d)


def transit(depth=0.01, t14_h=2.5, v=11.5, **kw):
    pl = Planet(name="X b", period=3.0, t0=2460000.0, t14=t14_h / 24.0, depth=depth)
    return Target(name="X b", kind="transit", mode="T", ra=10.0, dec=40.0, vmag=v, planet=pl.__dict__.copy(), **kw)


class DataHome:
    """A SHOBS data folder for one test (SHOBS_DATA_HOME), with the FLUX example profiles or edited copies."""

    def __init__(self, test, measured: bool = False, extra_bands: bool = False, copy_fixture: bool = True):
        self.dir = tempfile.mkdtemp(prefix="sched_shobs_")
        old = os.environ.get("SHOBS_DATA_HOME")
        test.addCleanup(lambda: os.environ.__setitem__("SHOBS_DATA_HOME", old) if old is not None
                        else os.environ.pop("SHOBS_DATA_HOME", None))
        os.environ["SHOBS_DATA_HOME"] = self.dir
        instrument._CACHE.clear()
        if copy_fixture:
            shutil.copytree(FX, self.dir, dirs_exist_ok=True)
        if measured or extra_bands:
            path = os.path.join(self.dir, "systems", SYS_ID + ".json")
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            tg = doc["bands"]["TG"]
            if measured:
                tg["zero_point"] = sp.record(21.30, "mag for 1 e-/s", "measured", sigma=0.02, n=4, date="2026-10-30")
                tg["extinction_k"] = sp.record(0.18, "mag/airmass", "measured", n=2)
                tg["sky_brightness"] = sp.record(20.4, "mag/arcsec2", "measured", n=4)
                tg["fwhm_in_focus"] = sp.record(2.6, "arcsec", "measured", n=4)
                tg["fwhm_defocus"]["+125 steps"] = sp.record(7.5, "arcsec", "measured", n=2)
                tg["scintillation_cy"] = sp.record(1.8, "Young C_Y", "measured", n=3)
            if extra_bands:
                doc["bands"]["TR"]["zero_point"] = sp.record(20.9, "mag for 1 e-/s", "measured", n=3)
            sp.write_json(path, doc)

    def write_system(self, doc):
        sp.write_json(os.path.join(self.dir, "systems", doc["profile_id"] + ".json"), doc)


# ---- the shared file ----------------------------------------------------------------------------------------------

class SharedCopy(unittest.TestCase):
    def test_version_pinned(self):
        self.assertEqual(sp.SHOBS_PROFILE_VERSION, instrument.BUILT_AGAINST)
        self.assertEqual(sp.SHOBS_PROFILE_VERSION, "1.2.0")

    def test_byte_identical_to_the_pinned_master(self):
        with open(os.path.join(ROOT, "shobs_profile.py"), "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), PINNED_SHA256,
                             "shobs_profile.py differs from SHOBS-FLUX's 1.2.0 master: copy it with SHOBS-FLUX's "
                             "tools/sync_shared.py, never edit it here")

    def test_same_as_a_sibling_flux_checkout(self):
        master = os.path.join(os.path.dirname(ROOT), "SHOBS-FLUX", "shobs_profile.py")
        if not os.path.exists(master):
            self.skipTest("no SHOBS-FLUX checkout beside this one")
        with open(master, "rb") as a, open(os.path.join(ROOT, "shobs_profile.py"), "rb") as b:
            ma = a.read()
        if b"SHOBS_PROFILE_VERSION = \"1.2.0\"" not in ma:
            self.skipTest("the FLUX checkout has moved on from 1.2.0")
        with open(os.path.join(ROOT, "shobs_profile.py"), "rb") as b:
            self.assertEqual(ma, b.read())

    def test_self_check(self):
        self.assertEqual(sp.self_check(), [])


# ---- the pure noise model ---------------------------------------------------------------------------------------

class NoiseModel(unittest.TestCase):
    def inp(self, **kw):
        d = dict(zp=21.0, k=0.2, sky_mag=21.0, fwhm=3.0, scale=0.27, aperture_m=0.356, gain=0.25, linear_adu=32000,
                 read_noise_e=1.5, dark_e_s=0.0, cy=1.5, overhead_s=3.0, elevation_m=280, pix_per_cell=2)
        d.update(kw)
        return snr.Inputs(**d)

    def test_zero_point_convention(self):
        i = self.inp()
        self.assertAlmostEqual(snr.star_rate(i, 21.0, 1.0), 1.0)                 # ZP gives 1 e-/s at airmass 1
        self.assertAlmostEqual(snr.star_rate(i, 16.0, 1.0), 100.0)
        self.assertAlmostEqual(snr.star_rate(i, 21.0, 2.0), 10 ** (-0.4 * 0.2))  # k (X - 1)

    def test_osborn_scintillation(self):
        i = self.inp(cy=1.5, aperture_m=0.356, elevation_m=280)
        v = snr.scint_variance(i, 60.0, 1.3)
        want = 1e-5 * 1.5 ** 2 * 0.356 ** (-4 / 3) / 60.0 * 1.3 ** 3 * math.exp(-2 * 280 / 8000.0)
        self.assertAlmostEqual(v, want, places=15)

    def test_parts_sum_to_one_and_bin_scales(self):
        i = self.inp()
        b = snr.bin_precision(i, 12.0, 60.0, 1.2)
        self.assertAlmostEqual(sum(b["parts"].values()), 1.0)
        self.assertAlmostEqual(b["frames"], 600 / 63.0)
        v = snr.frame_variances(i, 12.0, 60.0, 1.2)
        self.assertAlmostEqual(b["sigma"], math.sqrt(sum(v.values()) / (600 / 63.0)))
        # a fainter star is sky- and photon-bound, a bright one scintillation-bound
        self.assertGreater(snr.bin_precision(i, 6.0, 1.0, 1.2)["parts"]["scint"], 0.5)
        self.assertGreater(snr.bin_precision(i, 16.0, 120.0, 1.2)["parts"]["sky"], 0.2)

    def test_transit_snr_formula(self):
        # depth / (σ_bin / sqrt(bins)): 1 % deep, 2 h (12 bins), 1 ppt per bin → 10 × sqrt(12)
        self.assertAlmostEqual(snr.transit_snr(0.01, 0.001, 2 / 24.0), 10 * math.sqrt(12))
        self.assertIsNone(snr.transit_snr(None, 0.001, 0.1))
        self.assertAlmostEqual(snr.amplitude_snr(0.5, 0.01), (1 - 10 ** (-0.2)) / 0.01)      # exact, not 0.921 Δm
        self.assertAlmostEqual(snr.flux_fraction(2.5), 0.9)

    def test_verdict_words(self):
        self.assertEqual(snr.verdict(12), "Detectable")
        self.assertEqual(snr.verdict(10), "Detectable")
        self.assertEqual(snr.verdict(7), "Marginal")
        self.assertEqual(snr.verdict(4.9), "Out of reach")
        # a guess never reads as a measurement: the same thresholds, the word with "?" (John, 8 Nov)
        self.assertEqual(snr.verdict(50, rough=True), "Detectable?")
        self.assertEqual(snr.verdict(7, rough=True), "Marginal?")
        self.assertEqual(snr.verdict(4, rough=True), "Out of reach?")
        self.assertEqual((snr.saturates(), snr.saturates(True)), ("Saturates", "Saturates?"))
        self.assertEqual(snr.verdict_kind("Detectable?"), "good")
        self.assertEqual(snr.verdict(None), "")
        self.assertEqual(snr.verdict_kind("Detectable"), "good")
        self.assertEqual(snr.verdict_kind("Marginal?"), "marginal")
        self.assertEqual(snr.verdict_kind("Out of reach"), "")
        for w in ("Detectable", "Marginal", "Out of reach", "Marginal?", "Detectable?", "Out of reach?", "Saturates"):
            self.assertNotIn("⚠", w)
            self.assertNotIn("✖", w)

    def test_best_exposure_rule(self):
        i = self.inp()
        pts = snr.curve(i, 11.5, planner.STANDARD_EXPOSURES, 1.2)
        pick, ok = snr.best_exposure(pts, 0.8)
        self.assertTrue(ok)
        self.assertLessEqual(pick["peak_frac"], 0.8)
        allowed = [p for p in pts if p["peak_frac"] <= 0.8]
        best = min(p["sigma"] for p in allowed)
        self.assertLessEqual(pick["sigma"], best * 1.02 + 1e-15)
        self.assertTrue(all(p["sigma"] > best * 1.02 for p in allowed if p["t"] < pick["t"]))
        # too bright for every exposure: the shortest, flagged
        pick, ok = snr.best_exposure(snr.curve(i, 2.0, planner.STANDARD_EXPOSURES, 1.2), 0.8)
        self.assertFalse(ok)
        self.assertEqual(pick["t"], planner.STANDARD_EXPOSURES[0])

    def test_peak_scales_with_band_pixels(self):
        g = self.inp(pix_per_cell=2)
        r = self.inp(pix_per_cell=1)
        m = self.inp(pix_per_cell=4, dark_e_s=0.0)
        self.assertAlmostEqual(snr.peak_adu(r, 10, 10) / snr.peak_adu(g, 10, 10), 2.0, places=6)
        self.assertAlmostEqual(snr.peak_adu(g, 10, 10) / snr.peak_adu(m, 10, 10), 2.0, places=6)

    def test_ppt(self):
        self.assertEqual(snr.ppt(0.0018), "1.8 ppt")
        self.assertEqual(snr.ppt(0.00042), "0.42 ppt")


# ---- the profiles ------------------------------------------------------------------------------------------------

class Profiles(unittest.TestCase):
    def test_no_profiles_is_fine_and_creates_nothing(self):
        d = tempfile.mkdtemp(prefix="sched_noshobs_")
        home = os.path.join(d, "not_there")
        os.environ["SHOBS_DATA_HOME"] = home
        self.addCleanup(os.environ.pop, "SHOBS_DATA_HOME", None)
        instrument._CACHE.clear()
        self.assertIsNone(instrument.resolve(optics(), {}))
        self.assertEqual(instrument.list_systems(), [])
        inp = instrument.inputs(optics(), {}, 280)
        self.assertTrue(inp.rough)
        self.assertEqual(inp.sources["all"], "estimate (no FLUX profile)")
        self.assertFalse(os.path.exists(home), "SCHED must never create FLUX's folder")

    def test_never_writes_flux_files(self):
        h = DataHome(self, measured=True)
        before = {os.path.join(r, f): os.path.getmtime(os.path.join(r, f)) for r, _d, fs in os.walk(h.dir) for f in fs}
        instrument.resolve(optics(), {})
        instrument.inputs(optics(), {}, 280)
        planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280)
        after = {os.path.join(r, f): os.path.getmtime(os.path.join(r, f)) for r, _d, fs in os.walk(h.dir) for f in fs}
        self.assertEqual(before, after)

    def test_matches_telescope_camera_focal(self):
        DataHome(self)
        res = instrument.resolve(optics(), {})
        self.assertEqual(res["id"], SYS_ID)
        self.assertEqual(res["how"], "matched")
        self.assertIsNotNone(res["camera"])
        # within 3 % of the profile's 2900 mm: matched (ASIAIR's FOCALLEN wobble); beyond: not
        self.assertIsNotNone(instrument.resolve(optics(focal_mm=2985), {}))
        self.assertIsNone(instrument.resolve(optics(focal_mm=3000), {}))
        self.assertIsNone(instrument.resolve(optics(telescope="8in SCT"), {}))
        self.assertIsNone(instrument.resolve(optics(camera="ZWO ASI294MM Pro"), {}))
        self.assertIsNone(instrument.resolve(optics(gain=0), {}), "another gain is another camera profile")
        self.assertIsNone(instrument.resolve(optics(binning=2), {}))
        self.assertIsNotNone(instrument.resolve(optics(telescope=""), {}), "no telescope name typed: camera + focal")
        self.assertIsNone(instrument.resolve(optics(camera=""), {}), "no camera: no automatic match")

    def test_nearest_focal_wins(self):
        h = DataHome(self)
        with open(os.path.join(h.dir, "systems", SYS_ID + ".json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        other = copy.deepcopy(doc)
        other["profile_id"] = "14in-rc-2850mm__zwo-asi2600mc-air__g100__o50__bin1"
        other["telescope"]["focal_mm"] = 2850
        h.write_system(other)
        self.assertEqual(instrument.resolve(optics(focal_mm=2860), {})["id"], other["profile_id"])
        self.assertEqual(instrument.resolve(optics(focal_mm=2895), {})["id"], SYS_ID)

    def test_choice_none_and_chosen(self):
        DataHome(self)
        self.assertIsNone(instrument.resolve(optics(), {"snr_profile": "none"}))
        res = instrument.resolve(optics(telescope="other"), {"snr_profile": SYS_ID})
        self.assertEqual((res["id"], res["how"]), (SYS_ID, "chosen"))
        self.assertIsNone(instrument.resolve(optics(), {"snr_profile": "gone__x"}))
        self.assertIn("not in", instrument.resolve_words(optics(), {"snr_profile": "gone__x"}))
        self.assertEqual(instrument.list_systems()[0][0], SYS_ID)

    def test_sources_profile_estimate_vs_measured(self):
        DataHome(self)                                   # the example: zero point is FLUX's cold-start estimate
        inp = instrument.inputs(optics(), {}, 280)
        self.assertTrue(inp.rough)
        self.assertEqual(inp.zp, 21.0)
        self.assertEqual(inp.sources["zp"], "estimate")
        self.assertEqual(inp.sources["all"], "estimate (FLUX has no measured zero point yet)")
        self.assertEqual((inp.gain, inp.sources["gain"]), (0.2429, "from FLUX"))
        self.assertEqual((inp.read_noise_e, inp.sources["read_noise"]), (1.3, "from FLUX"))   # spec
        self.assertEqual((inp.overhead_s, inp.sources["overhead"]), (3.0, "from FLUX"))
        # a typed Setup value wins over FLUX's estimate, not over its measurement
        inp = instrument.inputs(optics(), {"snr_zp": 21.4, "snr_read_noise": 9.0}, 280)
        self.assertEqual((inp.zp, inp.sources["zp"], inp.rough), (21.4, "set here", False))
        self.assertEqual(inp.read_noise_e, 1.3)

    def test_measured_profile(self):
        DataHome(self, measured=True)
        inp = instrument.inputs(optics(), {"snr_zp": 19.0, "snr_fwhm": 9.9}, 280)
        self.assertFalse(inp.rough)
        self.assertEqual((inp.zp, inp.sources["all"]), (21.30, "from FLUX"))
        self.assertEqual((inp.k, inp.sky_mag, inp.fwhm, inp.cy), (0.18, 20.4, 2.6, 1.8))
        d = instrument.inputs(optics(), {}, 280, defocused=True, notes="Defocus +125 steps")
        self.assertEqual((d.fwhm, d.sources["fwhm"]), (7.5, "from FLUX"))
        self.assertEqual(instrument.setup_value(optics(), {}, "snr_zp"), (21.30, "from FLUX"))
        self.assertEqual(instrument.setup_value(optics(), {}, "snr_dark"), (None, ""))       # FLUX's estimate

    def test_bands_from_what_is_measured(self):
        DataHome(self, measured=True, extra_bands=True)
        self.assertEqual(instrument.bands(optics(), {}), ["TG", "TR"])
        r = instrument.inputs(optics(), {}, 280, band="TR")
        self.assertEqual((r.zp, r.pix_per_cell), (20.9, 1))
        self.assertIsNone(instrument.inputs(optics(), {}, 280, band="TB"), "no zero point for blue")
        m, line = instrument.catalog_mag(11.4, "TR")
        self.assertAlmostEqual(m, 11.01)
        self.assertIn("V 11.40 → your red channel (TR", line)
        self.assertIn("Sun-like", line)
        self.assertEqual(instrument.catalog_mag(11.4, "TG"), (11.4, "V 11.40 → your green channel (TG) (taken as V)"))

    def test_cold_zero_point_matches_the_1_3_0_peak(self):
        DataHome(self, copy_fixture=False)
        op = optics()
        inp = instrument.inputs(op, {}, 280)
        for v, e in ((11.4, 60.0), (8.0, 5.0), (13.0, 300.0)):
            old = planner.predict_peak_adu(v, e, op, {}, airmass=1.2)
            new = snr.peak_adu(inp, v, e, 1.2)
            self.assertAlmostEqual(new / old, 1.0, delta=0.01)
        mono = optics(color_camera=False)
        self.assertEqual(instrument.main_band(mono), "CV")
        mi = instrument.inputs(mono, {}, 280)
        self.assertAlmostEqual(snr.peak_adu(mi, 11.4, 60, 1.2) / planner.predict_peak_adu(11.4, 60, mono, {},
                                                                                           airmass=1.2), 1.0,
                               delta=0.01)


# ---- the planner's exposure ----------------------------------------------------------------------------------------

class ExposurePlan(unittest.TestCase):
    def setUp(self):
        DataHome(self, copy_fixture=False)

    def test_modes_by_type(self):
        # a transit's default: Best precision only once SHOBS-FLUX has measured the system (John, 8 Nov)
        self.assertEqual(planner.exposure_mode(transit()), "safe")
        self.assertEqual(planner.exposure_mode(transit(), True), "precision")
        self.assertEqual(planner.exposure_mode(Target(kind="variable"), True), "safe")
        self.assertEqual(planner.exposure_mode(transit(exposure_mode="precision")), "precision")   # chosen: wins
        self.assertEqual(planner.exposure_mode(Target(kind="variable")), "safe")
        self.assertEqual(planner.exposure_mode(Target(kind="campaign")), "safe")
        self.assertEqual(planner.exposure_mode(transit(exposure_s=30)), "own")       # an older file's exposure
        self.assertEqual(planner.exposure_mode(transit(exposure_mode="safe")), "safe")
        self.assertEqual(planner.exposure_mode(Target(kind="variable", exposure_mode="precision")), "precision")

    def test_longest_safe_is_1_3_0_exactly(self):
        op = optics()
        for v in (6.0, 9.0, 11.4, 13.0, 15.5):
            t = Target(name="x", kind="variable", vmag=v, ra=10, dec=40)
            old = planner.estimate_exposure(t, op, {}, 1.3, 0.5)
            new = planner.exposure_plan(t, op, {}, 1.3, 0.5, 280, mode="safe")
            # 1.3.0's choice and note; the peak shown is the noise model's (one number with the readout)
            self.assertEqual((new["exposure_s"], re.sub(r"peak ~[\d,]+(?: ADU|k)", "peak ~N", new["note"]), new["is_estimate"]),
                             (old["exposure_s"], re.sub(r"peak ~[\d,]+(?: ADU|k)", "peak ~N", old["note"]), old["is_estimate"]))
            self.assertAlmostEqual(new["peak_adu"], new["model_peak_adu"], delta=1e-6)
            self.assertAlmostEqual(new["peak_adu"] / old["peak_adu"], 1.0, delta=0.03)   # sky, dark: a little

    def test_best_precision_transit(self):
        d = planner.exposure_plan(transit(depth=0.01, t14_h=2.5, v=11.5), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual((d["mode"], d["safe_until_measured"]), ("safe", True))      # rough numbers: Longest safe
        p = planner.exposure_plan(transit(depth=0.01, t14_h=2.5, v=11.5, exposure_mode="precision"), optics(), {},
                                  1.2, 0.0, 280)
        self.assertEqual((p["mode"], p["safe_until_measured"]), ("precision", False))
        self.assertIn(p["exposure_s"], planner.STANDARD_EXPOSURES)
        self.assertLessEqual(p["peak_frac"], 0.8 + 1e-9)
        self.assertTrue(p["note"].startswith("S/N exposure: peak ~"))
        self.assertAlmostEqual(p["bins"], 15.0)
        self.assertAlmostEqual(p["snr"], 0.01 / (p["sigma"] / math.sqrt(15.0)))
        self.assertEqual(p["verdict"], "Detectable?")                   # cold start: the word with "?"
        self.assertTrue(p["rough"])
        self.assertIn("rough estimate", p["reason"])
        self.assertAlmostEqual(sum(p["parts"].values()), 1.0)

    def test_verdict_with_a_measured_profile(self):
        h = DataHome(self, measured=True)
        self.assertTrue(os.path.isdir(h.dir))
        good = planner.exposure_plan(transit(depth=0.01, v=11.5), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual(good["verdict"], "Detectable")
        self.assertEqual(good["tag"], "from FLUX")
        self.assertTrue(good["reason"].startswith("S/N "))
        far = planner.exposure_plan(transit(depth=0.0005, t14_h=1.0, v=15.5), optics(), {}, 1.8, 2.0, 280)
        self.assertEqual(far["verdict"], "Out of reach")
        self.assertIn("needs 5", far["reason"])
        mid = None
        for depth in [x / 100000 for x in range(5, 2000, 5)]:
            p = planner.exposure_plan(transit(depth=depth, t14_h=1.0, v=15.0), optics(), {}, 1.5, 0.0, 280)
            if p["verdict"] == "Marginal":
                mid = p
                break
        self.assertIsNotNone(mid)
        self.assertTrue(5 <= mid["snr"] < 10)

    def test_own_exposure_keeps_1_3_0_notes(self):
        t = transit(exposure_s=60)
        p = planner.exposure_plan(t, optics(), {}, 1.2, 0.0, 280)
        self.assertEqual((p["mode"], p["exposure_s"], p["is_estimate"]), ("own", 60.0, False))
        self.assertEqual(re.sub(r"peak ~[\d,]+(?: ADU|k)", "peak ~N", p["note"]), re.sub(r"peak ~[\d,]+(?: ADU|k)", "peak ~N", planner.estimate_exposure(t, optics(), {}, 1.2)["note"]))
        q = planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=20)
        self.assertEqual(q["exposure_s"], 20.0)

    def test_too_bright_saturates_with_the_reason(self):
        p = planner.exposure_plan(transit(v=3.0, exposure_mode="precision"), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual(p["verdict"], "Saturates?")                    # the peak is from the guessed zero point
        self.assertIn("of your linear limit at 0.75 s (defocus", p["reason"])
        self.assertTrue(p["note"].startswith("⚠ estimate: even 0.75 s may saturate"))

    def test_no_v_or_no_depth(self):
        p = planner.exposure_plan(transit(v=None), optics(), {}, 1.2)
        self.assertEqual((p["verdict"], p["reason"]), ("", "no V magnitude"))
        self.assertEqual(p["mode"], "safe")
        c = planner.exposure_plan(Target(kind="campaign", vmag=11, ra=1, dec=40), optics(), {}, 1.2)
        self.assertEqual(c["verdict"], "")
        self.assertIn("no amplitude", c["reason"])
        v = Target(kind="variable", vmag=11, ra=1, dec=40,
                   elements=VarElements(vtype="EA", mag_max=11.0, mag_min=11.6).__dict__.copy())
        self.assertEqual(planner.exposure_plan(v, optics(), {}, 1.2)["verdict"], "Detectable?")
        self.assertEqual(planner.exposure_plan(transit(), Optics(), {}, 1.2)["reason"][:9], "telescope")

    def test_target_cycle_sets_the_frames_per_bin(self):
        t = transit(cycle_s=200.0)
        p = planner.exposure_plan(t, optics(), {}, 1.2, 0.0, 280)
        self.assertAlmostEqual(p["frames_per_bin"], 3.0)

    def test_block_carries_the_verdict(self):
        from datetime import date
        import sky
        from models import Site
        site = Site(name="t", lat=35.3, lon=-81.9, elevation_m=280, tz="America/New_York")
        d = date(2026, 10, 21)
        mid = sky.local_jd(d, 23, 30, site.tz)
        ra = (sky.lst_deg(mid, site.lon) if hasattr(sky, "lst_deg") else None)
        if ra is None:
            self.skipTest("no sidereal-time helper")
        pl = Planet(name="Mid b", period=10.0, t0=mid, t14=2.0 / 24, depth=0.012)
        t = Target(id="m", name="Mid b", kind="transit", mode="T", ra=ra % 360, dec=40.0, vmag=11.0,
                   planet=pl.__dict__.copy())
        b = planner.build_night(d, site, optics(), [t], {}).blocks_obj()[0]
        self.assertEqual(b.exposure_how, "safe")                       # no FLUX profile: Longest safe
        t.exposure_mode = "precision"
        b = planner.build_night(d, site, optics(), [t], {}).blocks_obj()[0]
        self.assertEqual(b.exposure_how, "precision")
        self.assertEqual(b.verdict, "Detectable?")
        self.assertTrue(b.notes[0].startswith("Detectable? (S/N ~"), b.notes[0])
        import export
        r = export.table_rows(planner.build_night(d, site, optics(), [t], {}), site)[0]
        self.assertTrue(r["exposure"].endswith(" s (S/N)"), r["exposure"])
        self.assertFalse(r["exposure_is_estimate"])
        self.assertEqual(r["verdict"], "Detectable?")


class FlipNotes(unittest.TestCase):
    def test_wording_by_type(self):
        self.assertEqual(planner.flip_start_note("variable", "22:40"),
                         "Start before the meridian and let it flip (~22:40). Pre-flip frames carry no weight in the "
                         "night mean.")
        self.assertEqual(planner.flip_start_note("campaign", "01:05"),
                         "Start before the meridian and let it flip (~01:05). Pre-flip frames carry no weight in the "
                         "night mean.")
        self.assertEqual(planner.flip_start_note("transit", "22:40"),
                         "Start before the meridian and let it flip (~22:40). Pre-flip frames are kept; SHOBS-P fits "
                         "the flip step.")
        for k in ("variable", "campaign", "transit"):
            self.assertNotIn("drop the pre-flip", planner.flip_start_note(k, "22:40"))


if __name__ == "__main__":
    unittest.main()
