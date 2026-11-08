"""SHOBS-SCHED 1.4.0, the independent (Fable) review of build-1.4.0 and John's decisions of 8 Nov 2026: the Moon
against a measured sky, "?" on every rough verdict, "Saturates" in every mode, the gain ambiguity of Automatic,
Setup's overhead in the S/N, a typical sky colour for other bands, the exact flux range, fresh suggestion verdicts,
the bias in the peak, a newer profile format, no "(S/N)" for an exposure that cannot keep under the aim, and the
default exposure mode (Best precision only once SHOBS-FLUX has measured the system)."""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export  # noqa: E402
import instrument  # noqa: E402
import planner  # noqa: E402
import shobs_profile as sp  # noqa: E402
import snr  # noqa: E402
from models import Block, NightPlan, Optics, Planet, Site, Target, VarElements  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(ROOT, "tests", "fixtures", "shobs_data")
SYS_ID = "14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1"
CAM_ID = "zwo-asi2600mc-air__g100__o50__bin1"
OPTICS = dict(aperture_mm=356, focal_mm=2900, pixel_um=3.76, binning=1, camera="ZWO ASI2600MC Air",
              telescope="14in RC", color_camera=True, gain=100, e_per_adu=0.243, linear_limit_adu=32000)


def optics(**kw):
    return Optics.from_dict(dict(OPTICS, **kw))


def transit(v=11.5, depth=0.01, t14_h=2.0, **kw):
    pl = Planet(name="X b", period=3.0, t0=2460000.0, t14=t14_h / 24.0, depth=depth)
    return Target(name="X b", kind="transit", mode="T", ra=10.0, dec=40.0, vmag=v, planet=pl.__dict__.copy(), **kw)


class Home:
    """A SHOBS data folder for one test, with the FLUX example (edited by the test)."""

    def __init__(self, test, copy_fixture=True):
        self.dir = tempfile.mkdtemp(prefix="sched_r140b_")
        old = os.environ.get("SHOBS_DATA_HOME")
        test.addCleanup(lambda: os.environ.__setitem__("SHOBS_DATA_HOME", old) if old is not None
                        else os.environ.pop("SHOBS_DATA_HOME", None))
        os.environ["SHOBS_DATA_HOME"] = self.dir
        if copy_fixture:
            shutil.copytree(FX, self.dir, dirs_exist_ok=True)
        instrument._CACHE.clear()

    def path(self, kind, pid):
        return os.path.join(self.dir, kind, pid + ".json")

    def read(self, kind, pid):
        with open(self.path(kind, pid), encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, kind, doc):
        sp.write_json(self.path(kind, doc["profile_id"]), doc)
        instrument._CACHE.clear()

    def measure(self, **band):
        doc = self.read("systems", SYS_ID)
        tg = doc["bands"]["TG"]
        tg["zero_point"] = sp.record(band.get("zp", 21.3), "mag for 1 e-/s", "measured", n=4)
        if "sky" in band:
            tg["sky_brightness"] = sp.record(band["sky"], "mag/arcsec2", "measured", n=4)
        self.write("systems", doc)


class NumericCase(unittest.TestCase):
    def inp(self, **kw):
        d = dict(zp=20.59, k=0.208, sky_mag=18.84, fwhm=5.0, scale=0.267, aperture_m=0.356, gain=0.243,
                 read_noise_e=1.3, dark_e_s=0.007, cy=1.68, overhead_s=3.0, elevation_m=280, pix_per_cell=2)
        d.update(kw)
        return snr.Inputs(**d)

    def test_fixed_bin_precision(self):
        # the reviewer's hand calculation: V 11.5, 1 % deep, 2 h, 0.356 m, ZP 20.59, k 0.208, sky 18.84, seeing 5″,
        # C_Y 1.68, gain 0.243, RN 1.3, 280 m, 30 s, X 1.2 → ~0.935 ppt per 10 min, S/N ~37
        b = snr.bin_precision(self.inp(), 11.5, 30.0, 1.2)
        self.assertAlmostEqual(b["sigma"] * 1000, 0.935, delta=0.002)
        self.assertAlmostEqual(snr.transit_snr(0.01, b["sigma"], 2 / 24.0), 37.1, delta=0.2)

    def test_read_term_scales_with_band_pixels(self):
        tg = snr.frame_variances(self.inp(pix_per_cell=2), 14.0, 30.0, 1.2)
        mono = snr.frame_variances(self.inp(pix_per_cell=4), 14.0, 30.0, 1.2)
        red = snr.frame_variances(self.inp(pix_per_cell=1), 14.0, 30.0, 1.2)
        self.assertAlmostEqual(mono["read"] / tg["read"], 2.0, places=9)    # all 4 pixels of a cell vs the 2 green
        self.assertAlmostEqual(tg["read"] / red["read"], 2.0, places=9)
        self.assertAlmostEqual(mono["photon"], tg["photon"], places=15)

    def test_bias_in_the_peak(self):
        self.assertAlmostEqual(snr.peak_adu(self.inp(bias_adu=500), 12, 30) - snr.peak_adu(self.inp(), 12, 30), 500)


class Moon(unittest.TestCase):
    def test_rebase_is_the_moon_flux_added(self):
        self.assertEqual(snr.rebase_moon_penalty(0.0, 21.5, 18.84), 0.0)
        # a 3-mag brightening of a 21.5 sky (to 18.5) is the Moon at ~18.56; added to an 18.84 sky: ~17.94
        pen = snr.rebase_moon_penalty(3.0, 21.5, 18.84)
        moon = 10 ** (-0.4 * 18.5) - 10 ** (-0.4 * 21.5)
        self.assertAlmostEqual(18.84 - pen, -2.5 * math.log10(10 ** (-0.4 * 18.84) + moon), places=9)
        self.assertLess(pen, 1.0)                      # not 3 mag again on the bright measured sky
        self.assertAlmostEqual(snr.rebase_moon_penalty(1.0, 21.5, 21.5), 1.0, places=9)

    def test_moon_against_a_measured_sky(self):
        h = Home(self)
        h.measure(sky=18.84)
        t = transit()
        p = planner.exposure_plan(t, optics(), {}, 1.2, moon_penalty=3.0, elevation_m=280, mode="precision")
        self.assertAlmostEqual(p["moon_penalty_snr"], snr.rebase_moon_penalty(3.0, 21.5, 18.84))
        inp = p["inputs"]
        right = snr.bin_precision(snr.replace(inp, overhead_s=3.0), 11.5, p["exposure_s"], 1.2,
                                  snr.rebase_moon_penalty(3.0, 21.5, 18.84))
        wrong = snr.bin_precision(snr.replace(inp, overhead_s=3.0), 11.5, p["exposure_s"], 1.2, 3.0)
        self.assertAlmostEqual(p["sigma"], right["sigma"], places=12)
        self.assertLess(p["sigma"], wrong["sigma"])
        # Longest safe keeps 1.3.0's 21.5-based penalty
        safe = planner.exposure_plan(t, optics(), {}, 1.2, 3.0, 280, mode="safe")
        self.assertEqual(safe["exposure_s"], planner.estimate_exposure(t, optics(), {}, 1.2, 3.0)["exposure_s"])


class Verdicts(unittest.TestCase):
    def setUp(self):
        Home(self, copy_fixture=False)

    def test_rough_out_of_reach_keeps_its_mark(self):
        p = planner.exposure_plan(transit(v=15.5, depth=0.0005, t14_h=1.0), optics(), {}, 1.8, 2.0, 280,
                                  mode="precision")
        self.assertEqual(p["verdict"], "Out of reach?")
        note = planner.verdict_note(p)
        self.assertTrue(note.startswith("Out of reach? (S/N ~"), note)
        self.assertIn("rough estimate", note)

    def test_saturates_in_every_mode(self):
        own = planner.exposure_plan(transit(v=8.0), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=60)
        self.assertGreater(own["peak_frac"], 1.0)
        self.assertEqual(own["verdict"], "Saturates?")               # cold start: the peak itself is a guess
        self.assertRegex(own["reason"], r"^peak ~\d+ % of your linear limit at 60 s \(shorten it or defocus\)")
        self.assertTrue(planner.verdict_note(own).startswith("Saturates? (rough estimate): peak ~"))
        h = Home(self)
        h.measure()
        m = planner.exposure_plan(transit(v=8.0), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=60)
        self.assertEqual(m["verdict"], "Saturates")                   # measured: the hard limit, no "?"
        safe = planner.exposure_plan(transit(v=2.0), optics(), {}, 1.2, 0.0, 280, mode="safe")
        self.assertEqual(safe["verdict"], "Saturates")
        ok = planner.exposure_plan(transit(v=11.5), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=30)
        self.assertLess(ok["peak_frac"], 1.0)
        self.assertNotIn("Saturates", ok["verdict"])

    def test_no_sn_tag_when_the_aim_cannot_be_kept(self):
        site = Site(name="t", lat=35.3, lon=-81.9, elevation_m=280, tz="America/New_York")
        from datetime import date
        import sky
        d = date(2026, 10, 21)
        mid = sky.local_jd(d, 23, 30, site.tz)
        pl = Planet(name="Bright b", period=10.0, t0=mid, t14=2.0 / 24, depth=0.01)
        t = Target(id="b", name="Bright b", kind="transit", mode="T", ra=float(sky.lst_deg(mid, site.lon)) % 360,
                   dec=40.0, vmag=2.0, planet=pl.__dict__.copy(), exposure_mode="precision")
        night = planner.build_night(d, site, optics(), [t], {})
        b = night.blocks_obj()[0]
        self.assertEqual(b.exposure_how, "precision_over")
        r = export.table_rows(night, site)[0]
        self.assertNotIn("(S/N)", r["exposure"])
        self.assertTrue(r["exposure_is_estimate"])

    def test_variable_range_is_exact(self):
        v = Target(kind="variable", vmag=11, ra=1, dec=40,
                   elements=VarElements(vtype="EA", mag_max=11.0, mag_min=12.0).__dict__.copy())
        p = planner.exposure_plan(v, optics(), {}, 1.2)
        self.assertAlmostEqual(p["depth"], 1 - 10 ** -0.4)
        self.assertAlmostEqual(p["snr"], (1 - 10 ** -0.4) / p["sigma"])
        self.assertAlmostEqual(planner._depth_of(v), 1 - 10 ** -0.4)


class DefaultMode(unittest.TestCase):
    def test_precision_only_when_measured(self):
        h = Home(self)                                    # the example: FLUX's own estimate of the zero point
        self.assertFalse(planner.measured(optics(), {}))
        p = planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual((p["mode"], p["safe_until_measured"]), ("safe", True))
        h.measure()
        self.assertTrue(planner.measured(optics(), {}))
        p = planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual((p["mode"], p["safe_until_measured"]), ("precision", False))
        self.assertEqual(planner.exposure_plan(transit(exposure_mode="safe"), optics(), {}, 1.2)["mode"], "safe")
        self.assertEqual(planner.exposure_plan(Target(kind="variable", vmag=11, ra=1, dec=40), optics(), {},
                                               1.2)["mode"], "safe")
        # a typed Setup zero point is not a FLUX measurement: Longest safe stays the default
        Home(self, copy_fixture=False)
        self.assertFalse(planner.measured(optics(), {"snr_zp": 21.0}))


class Profiles(unittest.TestCase):
    def two_gains(self, h):
        cam = h.read("cameras", CAM_ID)
        sysd = h.read("systems", SYS_ID)
        cam0 = copy.deepcopy(cam)
        cam0["profile_id"] = "zwo-asi2600mc-air__g0__o50__bin1"
        cam0["mode"]["gain_setting"] = 0
        cam0["values"]["gain_e_per_adu"] = sp.record(0.78, "e-/ADU", "header")
        h.write("cameras", cam0)
        sys0 = copy.deepcopy(sysd)
        sys0["profile_id"] = "14in-rc-2900mm__" + cam0["profile_id"]
        sys0["camera_profile"] = cam0["profile_id"]
        sys0["telescope"]["focal_mm"] = 2901                  # a hair nearer Setup's 2901 than the g100 one
        h.write("systems", sys0)
        return sys0["profile_id"]

    def test_gain_ambiguity(self):
        h = Home(self)
        g0 = self.two_gains(h)
        op = optics(gain=None, focal_mm=2901)
        res = instrument.resolve(op, {})
        self.assertEqual(res["id"], SYS_ID, "the camera profile whose e⁻/ADU is Setup's 0.243 wins")
        self.assertEqual(instrument.inputs(op, {}, 280).gain, 0.2429)
        self.assertEqual(instrument.resolve(optics(gain=0, focal_mm=2901), {})["id"], g0)
        # no gain setting and no e⁻/ADU in Setup: Automatic does not guess; Setup says why
        vague = optics(gain=None, e_per_adu=None, focal_mm=2901)
        self.assertIsNone(instrument.resolve(vague, {}))
        self.assertEqual(sorted(instrument.ambiguous(vague, {})), sorted([CAM_ID, "zwo-asi2600mc-air__g0__o50__bin1"]))
        self.assertIn("Several SHOBS-FLUX camera profiles fit", instrument.resolve_words(vague, {}))
        self.assertEqual(instrument.resolve(vague, {"snr_profile": g0})["id"], g0)

    def test_newer_format_is_said(self):
        h = Home(self)
        doc = h.read("systems", SYS_ID)
        doc["format_version"] = 9
        h.write("systems", doc)
        self.assertIsNone(instrument.resolve(optics(), {}))
        self.assertEqual(instrument.newer_files(), [SYS_ID + ".json"])
        self.assertIn("A newer SHOBS-FLUX wrote 1 profile here", instrument.resolve_words(optics(), {}))
        self.assertIn("update SHOBS-SCHED", instrument.resolve_words(optics(), {}))

    def test_other_band_sky_gets_a_colour(self):
        h = Home(self)
        h.measure(sky=19.6)
        doc = h.read("systems", SYS_ID)
        doc["bands"]["TR"]["zero_point"] = sp.record(20.9, "mag for 1 e-/s", "measured", n=3)
        h.write("systems", doc)
        r = instrument.inputs(optics(), {}, 280, band="TR")
        self.assertAlmostEqual(r.sky_mag, 19.6 - 0.9)
        self.assertEqual(r.sources["sky"], "estimate")
        self.assertEqual(instrument.inputs(optics(), {}, 280).sky_mag, 19.6)

    def test_bias_from_the_camera_profile(self):
        h = Home(self)
        cam = h.read("cameras", CAM_ID)
        cam["values"]["bias_level_adu"] = sp.record(500, "ADU", "measured")
        h.write("cameras", cam)
        self.assertEqual(instrument.inputs(optics(), {}, 280).bias_adu, 500.0)


class Overhead(unittest.TestCase):
    def test_setup_overhead_is_used_as_the_frame_counts(self):
        h = Home(self)
        h.measure()                                       # FLUX's cadence says 3 s
        p = planner.exposure_plan(transit(exposure_mode="precision"), optics(frame_overhead_s=12.0), {}, 1.2, 0.0,
                                  280)
        self.assertAlmostEqual(p["frames_per_bin"], 600.0 / (p["exposure_s"] + 12.0))
        q = planner.exposure_plan(transit(exposure_mode="precision"), optics(), {}, 1.2, 0.0, 280)
        self.assertAlmostEqual(q["frames_per_bin"], 600.0 / (q["exposure_s"] + 3.0))     # FLUX's, not a guess


@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class Window(Base):
    use_fakes = False

    def test_stale_suggestion_verdict_is_worked_out_again(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        app.update()
        t = Target(name="Sug b", kind="transit", mode="T", ra=10.0, dec=40.0, vmag=11.5, suggested=True,
                   enabled=False, planet=Planet(name="Sug b", period=3.0, t0=2460000.0, t14=0.1,
                                                depth=0.01).__dict__.copy(),
                   suggest_info={"verdict": "Out of reach", "verdict_reason": "old", "verdict_sig":
                                 repr(page._verdict_signature())})
        app.targets.append(t)
        page.fill()
        self.assertEqual(page.verdict_text(t), "Out of reach")         # Setup as at the search: its own night's
        app.settings["options"]["snr_zp"] = 22.0                       # Setup changed since
        page.fill()
        self.assertNotEqual(page.verdict_text(t), "Out of reach")

    def test_saturates_readout_in_warn_style(self):
        self.golden_home()
        app = self.open()
        t = next(x for x in app.targets if x.name == "HD 219134")
        dlg = self.sa.EditTargetDialog(app, t, modal=False)
        dlg.vars["exposure_s"].set("60")
        dlg.update_readout()
        self.assertGreater(dlg.plan["peak_frac"], 1.0)
        self.assertEqual(str(dlg.exp_lbl.cget("style")), "Warn.TLabel")
        self.assertIn("of the linear limit", dlg.ro_vars["exposure"].get())
        dlg.vars["exposure_s"].set("0.75")
        dlg.update_readout()
        self.assertEqual(str(dlg.exp_lbl.cget("style")), "Card.TLabel")

    def test_rough_out_of_reach_is_not_hidden(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        page._hidden_out_of_reach, page._rough_kept = 1, 3
        page._found(([], ""), None)
        text = page.find_ind.var.get()
        self.assertIn("1 out of reach left out", text)
        self.assertIn("3 rough estimates kept", text)


if __name__ == "__main__":
    unittest.main()
