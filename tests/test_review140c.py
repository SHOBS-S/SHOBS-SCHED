"""SHOBS-SCHED 1.4.0, the manual writer's review of 8 Nov 2026: a zero point typed in Setup counts as measured for
the default exposure (and is no rough estimate), a radio button the user clicks in Edit pins the mode, one peak model
for Longest safe and the Saturates check, and consistent wording (flip time on a transit's flip note, "?" on
Saturates, "needs 5" with Out of reach)."""
from __future__ import annotations

import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import planner  # noqa: E402
import settings  # noqa: E402
import snr  # noqa: E402
from models import Target  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402
from tests.test_review140b import Home, optics, transit  # noqa: E402

TYPED = {"snr_zp": 21.0}


class TypedZeroPoint(unittest.TestCase):
    """Item 1: a typed zero point ('set here') is not a rough estimate and makes Best precision a transit's default."""

    def test_typed_zero_point_counts_as_measured(self):
        Home(self, copy_fixture=False)
        self.assertFalse(planner.measured(optics(), {}))
        self.assertTrue(planner.measured(optics(), TYPED))
        p = planner.exposure_plan(transit(), optics(), TYPED, 1.2, 0.0, 280)
        self.assertEqual(p["tag"], "set here")
        self.assertEqual((p["mode"], p["safe_until_measured"], p["rough"]), ("precision", False, False))
        self.assertFalse(p["verdict"].endswith("?"), p["verdict"])
        q = planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280)
        self.assertEqual((q["mode"], q["safe_until_measured"], q["rough"]), ("safe", True, True))
        self.assertTrue(q["verdict"].endswith("?"), q["verdict"])
        # variables and campaigns stay on Longest safe; an explicit choice still wins
        self.assertEqual(planner.exposure_plan(Target(kind="variable", vmag=11, ra=1, dec=40), optics(), TYPED,
                                               1.2)["mode"], "safe")
        self.assertEqual(planner.exposure_plan(transit(exposure_mode="safe"), optics(), TYPED, 1.2)["mode"], "safe")

    def test_default_rule_and_question_mark_rule_agree(self):
        """measured() (the default) is exactly 'not rough' (the '?'), with or without a profile."""
        cases = []
        h = Home(self)                                   # FLUX's example: its own estimate of the zero point
        cases += [({}, False), (TYPED, False)]
        for o, _ in cases:
            p = planner.exposure_plan(transit(), optics(), o, 1.2, 0.0, 280)
            self.assertEqual(planner.measured(optics(), o), not p["rough"], o)
        h.measure()                                      # FLUX measured it: wins over a typed one
        for o in ({}, TYPED):
            p = planner.exposure_plan(transit(), optics(), o, 1.2, 0.0, 280)
            self.assertTrue(planner.measured(optics(), o))
            self.assertFalse(p["rough"])
            self.assertEqual(p["tag"], "from FLUX")
        Home(self, copy_fixture=False)
        for o in ({}, TYPED, {"snr_k": 0.3}, {"snr_sky": 19.0}):
            p = planner.exposure_plan(transit(), optics(), o, 1.2, 0.0, 280)
            self.assertEqual(planner.measured(optics(), o), not p["rough"], o)


class OnePeakModel(unittest.TestCase):
    """Item 3: with a profile or typed values, Longest safe uses the noise model's peak (the Saturates check's)."""

    def sweep(self, o, **kw):
        for v in (5.0, 6.5, 8.0, 9.0, 10.0, 11.5, 13.0, 15.0):
            for x, moon in ((1.0, 0.0), (1.6, 2.0)):
                for dfc in ("", "defocus"):
                    t = transit(v=v, notes=dfc)
                    p = planner.exposure_plan(t, optics(**kw), o, x, moon, 280, mode="safe")
                    yield v, x, moon, dfc, p

    def test_longest_safe_never_saturates_with_a_profile_or_typed_values(self):
        for setup in ("profile", "measured", "typed"):
            h = Home(self, copy_fixture=setup != "typed")
            if setup == "measured":
                h.measure(sky=18.84)
            o = TYPED if setup == "typed" else {}
            for v, x, moon, dfc, p in self.sweep(o):
                self.assertEqual(p["mode"], "safe")
                self.assertAlmostEqual(p["peak_adu"], p["model_peak_adu"], delta=1e-6)
                if p["saturated"]:
                    # only when even the shortest standard exposure is over the limit (then: defocus)
                    self.assertEqual(p["exposure_s"], planner.STANDARD_EXPOSURES[0], (setup, v, x, moon, dfc))
                    self.assertTrue(p["note"].startswith("⚠"))
                elif p["peak_frac"] > 0.8:
                    # over the aim but under the limit: only the shortest, flagged
                    self.assertEqual(p["exposure_s"], planner.STANDARD_EXPOSURES[0], (setup, v, x, moon, dfc))
                    self.assertTrue(p["note"].startswith("⚠"))
                else:
                    # the longest one under the aim: the next standard exposure would be over it
                    i = planner.STANDARD_EXPOSURES.index(p["exposure_s"])
                    if i + 1 < len(planner.STANDARD_EXPOSURES) and planner.STANDARD_EXPOSURES[i + 1] <= \
                            p["bounds"]["e_max"]:          # 1.4.1 (#6): or the next is past the cadence cap
                        nxt = [c for c in p["curve"] if c["t"] == planner.STANDARD_EXPOSURES[i + 1]][0]
                        self.assertGreater(nxt["peak_frac"], 0.8, (setup, v, x, moon, dfc))

    def test_old_split_would_have_saturated(self):
        """The case the review found: V 9 with the example profile, 1.3.0's rule took 10 s, which the profile's
        peak calls Saturates; Longest safe now takes 5 s."""
        Home(self)
        t = transit(v=9.0)
        old = planner.estimate_exposure(t, optics(), {}, 1.3)["exposure_s"]
        own = planner.exposure_plan(t, optics(), {}, 1.3, 0.0, 280, mode="own", exposure_s=old)
        safe = planner.exposure_plan(t, optics(), {}, 1.3, 0.0, 280, mode="safe")
        self.assertTrue(own["saturated"])
        self.assertFalse(safe["saturated"])
        self.assertLess(safe["exposure_s"], old)

    def test_my_exposure_peak_is_the_model_peak_with_a_profile(self):
        Home(self)
        p = planner.exposure_plan(transit(), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=60)
        self.assertAlmostEqual(p["peak_adu"], p["model_peak_adu"], delta=1e-6)

    def test_cold_start_is_still_1_3_0(self):
        Home(self, copy_fixture=False)
        for v in (5.0, 7.0, 9.0, 10.0, 11.5, 13.0, 15.0, None):
            for dfc in ("", "defocus"):
                for x, moon in ((1.2, 0.0), (1.8, 3.0)):
                    t = transit(v=v, notes=dfc)
                    p = planner.exposure_plan(t, optics(), {}, x, moon, 280, mode="safe")
                    ex = planner.estimate_exposure(t, optics(), {}, x, moon)
                    # 1.3.0's choice, capped by the 1.4.1 bounds (#6: ingress ÷ 10 for a transit)
                    e_max = (p.get("bounds") or {}).get("e_max", math.inf)
                    want = ex["exposure_s"]
                    if want > e_max and not ex["note"].startswith("⚠"):
                        want = max([s for s in planner.STANDARD_EXPOSURES if s <= e_max] or
                                   [planner.STANDARD_EXPOSURES[0]])
                    self.assertEqual(p["exposure_s"], want, (v, dfc, x))
                    if p.get("model_peak_adu") is not None:
                        # the note's and the readout's peak are one number (the noise model's)
                        self.assertAlmostEqual(p["peak_adu"], p["model_peak_adu"], delta=1e-6)
                        for n in re.findall(r"peak ~([\d,]+) ADU", p["note"]):
                            self.assertEqual(n, f"{p['model_peak_adu']:,.0f}")
                    self.assertTrue(planner.cold_inputs(p["inputs"]))
                    if p.get("saturated"):
                        self.assertEqual(p["exposure_s"], planner.STANDARD_EXPOSURES[0])

    def test_cold_inputs(self):
        Home(self, copy_fixture=False)
        import instrument
        self.assertTrue(planner.cold_inputs(instrument.inputs(optics(), {})))
        self.assertFalse(planner.cold_inputs(instrument.inputs(optics(), TYPED)))
        self.assertFalse(planner.cold_inputs(instrument.inputs(optics(), {"snr_fwhm": 2.5})))
        Home(self)
        self.assertFalse(planner.cold_inputs(instrument.inputs(optics(), {})))


class SavedChoice(unittest.TestCase):
    """Item 2 in the file: the choice round-trips; an older file without the field still loads."""

    def test_round_trip_and_old_files(self):
        d = transit().to_dict()
        d.pop("exposure_mode", None)
        self.assertEqual(Target.from_dict(d).exposure_mode, "")
        for m in ("", "safe", "precision", "own"):
            self.assertEqual(Target.from_dict(Target.from_dict(dict(d, exposure_mode=m)).to_dict()).exposure_mode, m)


class LaterFindings(unittest.TestCase):
    """The writer's later findings: plural, one shown peak on the cold start, suggestion verdict mode, the S/N
    example, one bracket, APASS on the status line."""

    def test_wasp12_cold_start_note_and_readout_agree(self):
        Home(self, copy_fixture=False)
        from models import Optics, Planet
        op = Optics.from_dict(dict(aperture_mm=250, focal_mm=1600, pixel_um=3.76, binning=1, color_camera=True,
                                   linear_limit_adu=32000))
        pl = Planet(name="WASP-12 b", period=1.0914, t0=2457010.5, t14=3.0 / 24, depth=0.014)
        t = Target(name="WASP-12 b", kind="transit", mode="T", ra=97.64, dec=29.67, vmag=11.57,
                   planet=pl.__dict__.copy())
        p = planner.exposure_plan(t, op, {}, 1.1, 0.0, 280)
        ex = planner.estimate_exposure(t, op, {}, 1.1)
        # 1.3.0's choice (300 s here), capped by 1.4.1's cadence bound (ingress ~18 min, 10 % of T14, ÷ 10 = 108 s)
        self.assertEqual(ex["exposure_s"], 300.0)
        self.assertEqual((p["mode"], p["exposure_s"]), ("safe", 90.0))
        self.assertTrue(p["bound"].startswith("cadence-limited (ingress ~18 min (10 % of T14) ÷ 10 = 108 s)"),
                        p["bound"])
        shown = re.findall(r"peak ~([\d,]+) ADU", p["note"])
        self.assertEqual(shown, [f"{p['model_peak_adu']:,.0f}"])
        self.assertAlmostEqual(p["peak_adu"], p["model_peak_adu"], delta=1e-6)

    def test_example_s_n_arithmetic(self):
        self.assertEqual(round(snr.transit_snr(0.01, 0.0017, 90 / 1440)), 18)
        self.assertIn("1.7 ppt per 10-min", planner.verdict_note.__doc__)

    def test_status_line_names_apass(self):
        import credits
        self.assertIn("APASS", credits.STATUS_LINE)

    def test_suggestion_verdict_mode_is_the_default(self):
        import targets
        self.assertNotIn("(Best precision at", targets._suggest_verdict.__doc__)


class Wording(unittest.TestCase):
    """Item 4 (b, c, d) outside the window."""

    def test_flip_note_has_the_flip_time_for_every_type(self):
        for k in ("transit", "variable", "campaign"):
            self.assertIn("let it flip (~22:40). Pre-flip frames", planner.flip_start_note(k, "22:40"))
        self.assertEqual(planner.flip_start_note("transit", "22:40"),
                         "Start before the meridian and let it flip (~22:40). Pre-flip frames are kept; SHOBS-P "
                         "fits the flip step.")

    def test_saturates_question_mark_follows_the_rough_rule(self):
        for o, rough in (({}, True), (TYPED, False)):
            Home(self, copy_fixture=False)
            p = planner.exposure_plan(transit(v=11.5), optics(), o, 1.2, 0.0, 280, mode="own", exposure_s=600)
            self.assertTrue(p["saturated"])
            self.assertEqual(p["verdict"], "Saturates?" if rough else "Saturates")
            self.assertEqual(p["rough"], rough)
            if rough:
                self.assertTrue(p["reason"].endswith("(shorten it or defocus; rough estimate)"), p["reason"])
                self.assertNotIn(") (", p["reason"])
            note = planner.verdict_note(p)
            self.assertTrue(note.startswith("Saturates? (rough estimate): peak ~" if rough else "Saturates: peak ~"),
                            note)

    def test_cold_own_exposure_that_saturates_carries_its_caution(self):
        """Fable S1: on the bare cold start a My exposure between 1x and 1.5x the linear limit read 'Saturates?' but
        its note had no ⚠, so the Schedule showed no caution. Every Saturates own exposure now carries ⚠ or ✖."""
        Home(self, copy_fixture=False)
        hit = 0
        for e in (60, 90, 120, 180, 240, 300, 600, 900):
            p = planner.exposure_plan(transit(v=11.5), optics(), {}, 1.2, 0.0, 280, mode="own", exposure_s=e)
            if p["saturated"]:
                hit += 1
                self.assertTrue(p["note"].startswith(("⚠", "✖")), (e, p["note"]))
                self.assertIn(f"~{planner._k(p['peak_adu'])}", p["note"])
            else:
                self.assertFalse(p["note"].startswith(("⚠", "✖")), (e, p["note"]))
        self.assertGreater(hit, 0)

    def test_out_of_reach_says_needs_5(self):
        for o in ({}, TYPED):
            Home(self, copy_fixture=False)
            p = planner.exposure_plan(transit(v=16.5, depth=0.0005), optics(), o, 1.2, 0.0, 280)
            self.assertTrue(p["verdict"].startswith("Out of reach"), p["verdict"])
            self.assertIn("(needs 5", p["reason"])
            self.assertIn(", needs 5", planner.verdict_note(p))


@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class Window(Base):
    use_fakes = False

    def home(self, options=None):
        Home(self, copy_fixture=False)
        g = self.golden_home()
        ts = settings.load_targets()
        for t in ts:
            if t.kind == "transit":
                t.exposure_s = None
                t.exposure_mode = ""
        settings.save_targets(ts)
        s = settings.load_settings()
        s["optics"]["telescope"] = "14in RC"
        s["options"].update(options or {})
        settings.save_settings(s)
        return g

    def target(self, name):
        return next(t for t in self.app.targets if t.name == name)

    def radio(self, dlg, value):
        from tkinter import ttk

        def walk(w):
            for c in w.winfo_children():
                yield c
                yield from walk(c)
        return next(w for w in walk(dlg.win) if isinstance(w, ttk.Radiobutton) and str(w.cget("value")) == value)

    def test_typed_zero_point_makes_best_precision_the_default(self):
        self.home(TYPED)
        app = self.open()
        app.show_page(1)
        dlg = self.sa.EditTargetDialog(app, self.target("TOI-2046 b"), on_save=lambda t: None, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "precision")
        self.assertNotIn("Longest safe until", dlg.ro_vars["where"].get())
        self.assertNotIn("Rough estimate", dlg.ro_vars["where"].get())
        self.assertEqual(dlg.src_tag.cget("text"), "set here")
        self.assertFalse(dlg.verdict_lbl.cget("text").endswith("?"))
        page = app.pages[1]
        page.select_target(self.target("TOI-2046 b").id)
        self.assertIn("exposure: Best precision (by S/N)", page.detail_var.get())

    def test_hints_mention_a_typed_zero_point(self):
        self.home()
        app = self.open()
        app.show_page(1)
        dlg = self.sa.EditTargetDialog(app, self.target("TOI-2046 b"), on_save=lambda t: None, modal=False)
        app.update()
        self.assertIn("Longest safe until a zero point is measured or typed in Setup", dlg.ro_vars["where"].get())
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(str(c.cget("text")))
                except Exception:
                    pass
                walk(c)
        walk(dlg.win)
        walk(app)
        joined = " ".join(texts)
        self.assertIn("once a zero point is measured by SHOBS-FLUX or typed in Setup", joined)
        self.assertIn("until a zero point is measured or typed here", joined)
        self.assertIn("(1.3.0's rule while no SHOBS-FLUX profile or typed value is in use)", joined)
        self.assertIn("(Detectable?, Marginal?, Out of reach?, Saturates?)", joined)
        self.assertNotIn("until a profile measures the system", joined)

    def test_untouched_target_follows_the_default(self):
        self.home()
        app = self.open()
        out = []
        t = self.target("TOI-2046 b")
        dlg = self.sa.EditTargetDialog(app, t, on_save=out.append, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "safe")
        self.assertTrue(dlg.save())
        self.assertEqual(out[-1].exposure_mode, "")                 # still follows the default
        self.assertEqual(planner.exposure_mode(out[-1], True), "precision")

    def test_clicking_the_default_radio_pins_it(self):
        self.home()
        app = self.open()
        out = []
        t = self.target("TOI-2046 b")
        dlg = self.sa.EditTargetDialog(app, t, on_save=out.append, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "safe")                 # the default, shown selected
        self.radio(dlg, "safe").invoke()                            # clicked anyway
        app.update()
        self.assertNotIn("Longest safe until", dlg.ro_vars["where"].get())
        self.assertTrue(dlg.save())
        saved = out[-1]
        self.assertEqual(saved.exposure_mode, "safe")
        # it wins later, when Best precision becomes the default (a typed zero point)
        self.assertEqual(planner.exposure_mode(saved, True), "safe")
        p = planner.exposure_plan(saved, app.optics, dict(app.options, **TYPED), 1.2)
        self.assertEqual(p["mode"], "safe")
        # and survives the targets file
        settings.save_targets([saved])
        back = settings.load_targets()[0]
        self.assertEqual(back.exposure_mode, "safe")
        # opened again and saved without a click: kept
        dlg = self.sa.EditTargetDialog(app, back, on_save=out.append, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "safe")
        dlg.save()
        self.assertEqual(out[-1].exposure_mode, "safe")

    def test_clicking_best_precision_as_default_pins_it(self):
        self.home(TYPED)
        app = self.open()
        out = []
        dlg = self.sa.EditTargetDialog(app, self.target("TOI-2046 b"), on_save=out.append, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "precision")
        self.radio(dlg, "precision").invoke()
        dlg.save()
        self.assertEqual(out[-1].exposure_mode, "precision")
        self.assertEqual(planner.exposure_mode(out[-1], False), "precision")   # wins when the default is safe

    def test_out_of_reach_readout_and_details_say_needs_5(self):
        self.home()
        app = self.open()
        app.show_page(1)
        faint = Target.from_dict(dict(self.target("TOI-2046 b").to_dict(), vmag=16.5,
                                      planet=dict(self.target("TOI-2046 b").planet, depth=0.0005)))
        dlg = self.sa.EditTargetDialog(app, faint, on_save=lambda t: None, modal=False)
        app.update()
        dlg.update_readout()
        self.assertRegex(dlg.verdict_lbl.cget("text"), r"^S/N \d+\.\d  Out of reach\?$")
        self.assertIn("(needs 5; rough estimate", dlg.reason_var.get())
        page = app.pages[1]
        text = page.detail_text(faint)
        self.assertRegex(text, r"Out of reach\? \(at its best airmass, dark sky\): S/N ~\d+\.\d \(needs 5; ")



if __name__ == "__main__":
    unittest.main()
