"""SHOBS-SCHED 1.4.0 (John's locked list, 8 Nov 2026): exposure by S/N with a verdict (GitHub issue #4) on every page
that shows it, the Setup "Photometry noise" box and its SHOBS-FLUX profile, the comp check, Find suggested
transits' verdict and "Hide out of reach", the flip notes by type, the SHOBS-P concept DOI, and the campaigns'
Type and Period."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credits  # noqa: E402
import export  # noqa: E402
import instrument  # noqa: E402
import planner  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import Block, NightPlan, Optics, Site, Target, VarElements  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(ROOT, "tests", "fixtures")
SYS_ID = "14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1"


def data_home(test, measured: bool = False) -> str:
    """A SHOBS data folder with the FLUX example profiles (zero point measured when asked) for one test."""
    import shobs_profile as sp
    d = tempfile.mkdtemp(prefix="sched_r140_shobs_")
    shutil.copytree(os.path.join(FX, "shobs_data"), d, dirs_exist_ok=True)
    if measured:
        path = os.path.join(d, "systems", SYS_ID + ".json")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["bands"]["TG"]["zero_point"] = sp.record(21.3, "mag for 1 e-/s", "measured", n=4)
        doc["bands"]["TG"]["scintillation_cy"] = sp.record(1.8, "Young C_Y", "measured", n=3)
        sp.write_json(path, doc)
    old = os.environ.get("SHOBS_DATA_HOME")
    os.environ["SHOBS_DATA_HOME"] = d
    test.addCleanup(lambda: os.environ.__setitem__("SHOBS_DATA_HOME", old) if old is not None
                    else os.environ.pop("SHOBS_DATA_HOME", None))
    instrument._CACHE.clear()
    return d


# ---- no window ------------------------------------------------------------------------------------------------------

class TestDoiAndCredits(unittest.TestCase):
    def test_shobs_p_concept_doi(self):
        text = credits.credits_text()
        self.assertIn("doi:10.5281/zenodo.23417063", text)
        self.assertNotIn("23417064", text)
        for path in ("README.md", "docs/manual/09-credits-and-citing.md", "CITATION.cff"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                self.assertNotIn("23417064", fh.read(), path)

    def test_new_sources_credited(self):
        text = credits.credits_text()
        for what in ("APASS", "Osborn, J. et al. 2015, MNRAS 452, 1707", "SHOBS-FLUX", "Willmer"):
            self.assertIn(what, text)

    def test_readme_and_changes_file(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("## New in 1.4.0", readme)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "manual", "_changes_140.md")))


class TestFlipWording(unittest.TestCase):
    def test_no_old_wording_left(self):
        for name in ("planner.py", "SPEC.md"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                self.assertNotIn("drop the pre-flip frames", fh.read(), name)


class TestCompCheck(unittest.TestCase):
    RA, DEC = 100.0, 40.0

    def rows(self, *stars):
        return {"rows": [{"ra": self.RA + dx / 3600.0, "dec": self.DEC + dy / 3600.0, "v": v, "bv": bv}
                         for dx, dy, v, bv in stars], "radius_arcmin": 12.0}

    def test_lines(self):
        me = (0, 1, 11.42, 0.62)
        self.assertEqual(T.comp_check_text(11.4, self.RA, self.DEC, None),
                         "Comps: not checked yet. Check comps asks APASS at VizieR (needs the internet).")
        self.assertEqual(T.comp_check_text(None, self.RA, self.DEC, self.rows(me)),
                         "Comps: not checked (no V magnitude).")
        three_red = self.rows(me, (300, 0, 11.0, 1.2), (0, 400, 12.1, 1.1), (-200, 50, 10.8, 1.4), (90, 90, 14, 0.6))
        self.assertEqual(T.comp_check_text(11.4, self.RA, self.DEC, three_red),
                         "Comps: 3 within 1 mag (APASS, 12′ around the target); none within 0.3 in B−V (the star's "
                         "B−V 0.62). Expect colour and extinction trends.")
        good = self.rows(me, (300, 0, 11.0, 0.7), (0, 400, 12.1, 1.1))
        self.assertIn("1 within 0.3 in B−V", T.comp_check_text(11.4, self.RA, self.DEC, good))
        self.assertNotIn("Expect", T.comp_check_text(11.4, self.RA, self.DEC, good))
        lonely = self.rows(me, (300, 0, 14.5, 0.7))
        self.assertTrue(T.comp_check_text(11.4, self.RA, self.DEC, lonely).startswith("Comps: none within 1 mag"))
        no_own = self.rows((300, 0, 11.0, 0.7))
        self.assertIn("own B−V is not in APASS", T.comp_check_text(11.4, self.RA, self.DEC, no_own))

    def test_lookup_is_cached_and_offline_safe(self):
        tmp = tempfile.mkdtemp(prefix="sched_r140_home_")
        old = os.environ.get("SHOBS_SCHED_HOME")
        os.environ["SHOBS_SCHED_HOME"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SHOBS_SCHED_HOME", old) if old else
                        os.environ.pop("SHOBS_SCHED_HOME", None))
        asked = []
        real = T._tap_csv

        def fake(base, adql, nea=True, expect=None):
            asked.append(adql)
            return [{"RAJ2000": "100.0", "DEJ2000": "40.0003", "Vmag": "11.40", "B-V": "0.60"},
                    {"RAJ2000": "100.05", "DEJ2000": "40.0", "Vmag": "11.9", "B-V": "0.65"}]
        T._tap_csv = fake
        self.addCleanup(setattr, T, "_tap_csv", real)
        self.assertIsNone(T.saved_comp_stars(100.0, 40.0, 12.0))
        data, note = T.comp_stars(100.0, 40.0, 12.0, 11.4)
        self.assertEqual(note, "")
        self.assertIn('"II/336/apass9"', asked[0])
        self.assertIn("CIRCLE('ICRS', 100.000000, 40.000000, 0.20000)", asked[0])
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(T.saved_comp_stars(100.0, 40.0, 12.0), data)
        self.assertIn("1 within 0.3 in B−V", T.comp_check_text(11.4, 100.0, 40.0, data))

        def down(*_a, **_k):
            raise T.FetchError("no network connection")
        T._tap_csv = down
        real_get = T.http_get
        T.http_get = down                                                        # ASU fallback offline too
        self.addCleanup(setattr, T, "http_get", real_get)
        data2, note2 = T.comp_stars(100.0, 40.0, 12.0, 11.4, refresh=True)     # the saved copy, with its age
        self.assertEqual(data2, data)
        self.assertIn("did not answer", note2)

    def test_radius_from_the_field(self):
        op = Optics(aperture_mm=356, focal_mm=2900, pixel_um=3.76, camera="ZWO ASI2600MC Air")
        r = T.comp_radius_arcmin(op)
        self.assertTrue(5.0 <= r <= 30.0)


class TestSuggestionsVerdict(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FX, "golden_oct19_24.json"), encoding="utf-8") as fh:
            g = json.load(fh)
        self.site, self.optics = Site.from_dict(g["site"]), Optics.from_dict(g["optics"])
        self.nights = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]
        with open(os.path.join(FX, "targets_catalog_small.csv"), encoding="utf-8") as fh:
            self.cat = T.parse_catalog_csv(fh.read())

    def test_each_suggestion_carries_a_verdict(self):
        res = T.suggest_transits(self.site, self.optics, {}, self.nights, catalog=self.cat)
        self.assertTrue(res)
        for t in res:
            self.assertIn(t.suggest_info.get("verdict"), ("Detectable?", "Marginal?", "Out of reach?", "Saturates?"),
                          t.name)
            self.assertTrue(t.suggest_info.get("verdict_reason"))

    def test_hide_out_of_reach(self):
        data_home(self, measured=True)
        base = T.suggest_transits(self.site, self.optics, {}, self.nights, catalog=self.cat)
        self.assertTrue(base)
        # make the shallowest one Out of reach: a tiny depth
        victim = min(base, key=lambda t: t.planet["depth"])
        cat = [dict(p, depth=0.0006 if p["name"] == victim.name else p["depth"]) for p in self.cat]
        opts = {"suggest_min_depth": 0.0005}
        shown = T.suggest_transits(self.site, self.optics, opts, self.nights, catalog=cat)
        v = {t.name: t.suggest_info.get("verdict") for t in shown}
        self.assertEqual(v.get(victim.name), "Out of reach", v)
        stats = {}
        hidden = T.suggest_transits(self.site, self.optics, dict(opts, suggest_hide_out_of_reach=True), self.nights,
                                    catalog=cat, stats=stats)
        self.assertNotIn(victim.name, {t.name for t in hidden})
        self.assertGreaterEqual(stats["hidden_out_of_reach"], 1)
        self.assertTrue(all(t.suggest_info.get("verdict") != "Out of reach" for t in hidden))


class TestScheduleAndCard(unittest.TestCase):
    def night(self, how="precision", verdict="Detectable"):
        b = Block(target_id="x", name="X b", kind="transit", mode="T", start=2461000.6, end=2461000.7,
                  exposure_s=120.0, exposure_is_estimate=True, exposure_how=how, verdict=verdict,
                  notes=["Detectable (S/N 18): a 1.00 % dip", "Ingress 21:00"])
        return NightPlan(date="2026-10-20", blocks=[b.to_dict()])

    def test_table_and_card(self):
        site = Site(name="s", lat=35.0, lon=-80.0, tz="America/New_York")
        r = export.table_rows(self.night(), site)[0]
        self.assertEqual(r["exposure"], "120 s (S/N)")
        self.assertFalse(r["exposure_is_estimate"])
        self.assertEqual(r["verdict"], "Detectable")
        self.assertTrue(r["notes"].startswith("Detectable (S/N 18)"))
        html = export.plan_card_html([self.night()], site)
        self.assertIn('120 s (S/N)<br><span class="verdict">Detectable</span>', html)
        self.assertNotIn("120 s (S/N) est.", html)
        text = export.plan_card_text([self.night()], site)
        self.assertIn("exposure 120 s (S/N) · Detectable · frames", text)
        safe = export.table_rows(self.night("safe", ""), site)[0]
        self.assertEqual((safe["exposure"], safe["exposure_is_estimate"], safe["verdict"]), ("120 s", True, ""))


# ---- the window ------------------------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class TestWindow(Base):
    use_fakes = False

    def golden_home(self, transit_exposure=None):
        g = super().golden_home()
        ts = settings.load_targets()
        for t in ts:
            if t.kind == "transit":
                t.exposure_s = transit_exposure
        settings.save_targets(ts)
        s = settings.load_settings()
        s["optics"]["telescope"] = "14in RC"
        settings.save_settings(s)
        return g

    def target(self, name):
        return next(t for t in self.app.targets if t.name == name)

    def test_targets_columns(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        app.update()
        for key in ("campaign", "transit", "variable"):
            self.assertIn("verdict", [c for c, _h in page.COLS[key]], key)
        self.assertEqual([c for c, _h in page.COLS["campaign"]][3:5], ["vtype", "period"])
        # a variable moved to Campaigns keeps its VSX type and period on the row
        dy = self.target("DY Peg")
        dy.kind, dy.mode = "campaign", "V"
        page.fill()
        tbl = page.tables["campaign"]
        vals = dict(zip([c for c, _h in page.COLS["campaign"]], tbl.tree.item(dy.id, "values")))
        self.assertEqual(vals["vtype"], dy.elements["vtype"])
        self.assertNotEqual(vals["period"], "—")
        self.assertEqual(vals["verdict"], "Detectable?")
        hd = dict(zip([c for c, _h in page.COLS["campaign"]], tbl.tree.item(self.target("HD 219134").id, "values")))
        self.assertEqual((hd["vtype"], hd["period"], hd["verdict"]), ("—", "—", "—"))
        tr = page.tables["transit"]
        k2 = dict(zip([c for c, _h in page.COLS["transit"]], tr.tree.item(self.target("K2-29 b").id, "values")))
        self.assertEqual(k2["verdict"], "Detectable?")
        page.select_target(self.target("K2-29 b").id)
        self.assertIn("exposure: Longest safe", page.detail_var.get())          # no FLUX profile yet
        self.assertIn("Detectable? (at its best airmass, dark sky)", page.detail_var.get())

    def test_edit_exposure_section(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        t = self.target("TOI-2046 b")
        saved = []
        dlg = self.sa.EditTargetDialog(app, t, on_save=saved.append, modal=False)
        app.update()
        self.assertEqual(dlg.how_var.get(), "safe")            # rough numbers: Longest safe by default
        self.assertIn("Longest safe until a zero point is measured or typed in Setup", dlg.ro_vars["where"].get())
        dlg.how_var.set("precision")                           # the user's choice wins
        dlg.update_readout()
        self.assertNotIn("until a zero point", dlg.ro_vars["where"].get())
        self.assertEqual(dlg.src_tag.cget("text"), "estimate (no FLUX profile)")
        self.assertEqual(dlg.cat_var.get(), "V 11.55 → your green channel (TG) (taken as V)")
        self.assertIn("(Best precision) · predicted peak ~", dlg.ro_vars["exposure"].get())
        self.assertIn("per 10-minute bin", dlg.ro_vars["precision"].get())
        self.assertTrue(dlg.ro_vars["budget"].get().startswith("Noise budget: photon "))
        self.assertIn("scintillation", dlg.ro_vars["budget"].get())
        self.assertIn("bins in transit", dlg.ro_vars["event"].get())
        self.assertTrue(dlg.verdict_lbl.cget("text").endswith("Detectable?"))
        self.assertEqual(str(dlg.verdict_lbl.cget("style")), "VerdictGood.TLabel")
        for text in (dlg.verdict_lbl.cget("text"), dlg.reason_var.get()):
            self.assertNotIn("⚠", text)
            self.assertNotIn("✖", text)
        self.assertTrue(dlg.plot.find_all(), "the plot is drawn")
        self.assertEqual(dlg.comp_var.get(),
                         "Comps: not checked yet. Check comps asks APASS at VizieR (needs the internet).")
        # Longest safe = 1.3.0's rule
        dlg.how_var.set("safe")
        dlg.update_readout()
        self.assertEqual(dlg.plan["exposure_s"], planner.estimate_exposure(
            Target.from_dict(dict(t.to_dict(), exposure_s=None)), app.optics, app.options,
            planner.best_airmass(t, app.site))["exposure_s"])
        # a click on the plot picks a standard exposure as My exposure
        e = dlg._plot_click(type("E", (), {"x": 50, "y": 50})())
        self.assertIn(e, planner.STANDARD_EXPOSURES)
        self.assertEqual(dlg.how_var.get(), "own")
        self.assertEqual(float(dlg.vars["exposure_s"].get()), e)
        self.assertIn("(My exposure)", dlg.ro_vars["exposure"].get())
        dlg.save()
        self.assertEqual((saved[-1].exposure_s, saved[-1].exposure_mode), (e, "own"))

    def test_edit_modes_saved(self):
        self.golden_home(transit_exposure=60)
        app = self.open()
        t = self.target("K2-29 b")
        out = []
        dlg = self.sa.EditTargetDialog(app, t, on_save=out.append, modal=False)
        self.assertEqual(dlg.how_var.get(), "own", "an older file's exposure is My exposure")
        dlg.how_var.set("precision")
        self.assertTrue(dlg.save())
        self.assertIsNone(out[-1].exposure_s)
        self.assertEqual(out[-1].exposure_mode, "precision")   # chosen while the numbers are rough: kept
        dlg = self.sa.EditTargetDialog(app, out[-1], on_save=out.append, modal=False)
        self.assertEqual(dlg.how_var.get(), "precision")
        dlg.how_var.set("safe")
        dlg.save()
        self.assertEqual(out[-1].exposure_mode, "safe")        # an explicit choice is kept
        dlg = self.sa.EditTargetDialog(app, out[-1], on_save=out.append, modal=False)
        dlg.how_var.set("safe")
        dlg.save()
        self.assertEqual((out[-1].exposure_s, out[-1].exposure_mode), (None, "safe"))
        dlg = self.sa.EditTargetDialog(app, out[-1], on_save=out.append, modal=False)
        self.assertEqual(dlg.how_var.get(), "safe")
        dlg.vars["exposure_s"].set("45")                       # typing an exposure chooses My exposure
        app.update()
        self.assertEqual(dlg.how_var.get(), "own")
        dlg.save()
        self.assertEqual((out[-1].exposure_s, out[-1].exposure_mode), (45.0, "own"))
        dlg = self.sa.EditTargetDialog(app, out[-1], on_save=out.append, modal=False)
        dlg.vars["exposure_s"].set("")
        self.assertFalse(dlg.save())
        self.assertIn("My exposure is empty", dlg.msg_var.get())

    def test_edit_with_a_flux_profile_and_bands(self):
        import shobs_profile as sp
        d = data_home(self, measured=True)
        path = os.path.join(d, "systems", SYS_ID + ".json")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["bands"]["TR"]["zero_point"] = sp.record(20.9, "mag for 1 e-/s", "measured", n=3)
        sp.write_json(path, doc)
        instrument._CACHE.clear()
        self.golden_home()
        app = self.open()
        dlg = self.sa.EditTargetDialog(app, self.target("HAT-P-23 b"), modal=False)
        app.update()
        self.assertEqual(dlg._bands, ["TG", "TR"])
        self.assertEqual(dlg.src_tag.cget("text"), "from FLUX")
        self.assertIn(dlg.verdict_lbl.cget("text").split()[-1], ("Detectable", "Marginal", "reach"))
        dlg.band_var.set(dlg._band_label("TR"))
        dlg.update_readout()
        self.assertIn("your red channel (TR", dlg.cat_var.get())
        out = []
        dlg.on_save = out.append
        dlg.save()
        self.assertEqual(out[-1].snr_band, "TR")

    def test_check_comps_runs_off_the_window(self):
        self.golden_home()
        app = self.open()
        dlg = self.sa.EditTargetDialog(app, self.target("HAT-P-23 b"), modal=False)
        tg = self.sa.mod("targets")
        real = tg.comp_stars
        seen = {}

        def fake(ra, dec, rad, v, refresh=False):
            import threading
            seen["thread"] = threading.current_thread() is threading.main_thread()
            seen["bar"] = dlg.busy_bar.text
            return {"rows": [], "radius_arcmin": rad}, ""
        tg.comp_stars = fake
        self.addCleanup(setattr, tg, "comp_stars", real)
        dlg.check_comps()
        self.assertEqual(dlg.busy_bar.text, "Asking APASS (VizieR) for comparison stars…")
        self.pump()
        self.assertFalse(seen["thread"], "the lookup runs off the window's thread")
        self.assertTrue(dlg.comp_var.get().startswith("Comps: none within 1 mag"))
        self.assertTrue(dlg.comp_ind.var.get().startswith("✔"))

    def test_setup_photometry_box(self):
        data_home(self, measured=True)
        self.golden_home()
        app = self.open()
        setup = app.pages[0]
        app.update()
        self.assertTrue(setup.snr_line.cget("text").startswith("✔ SHOBS-FLUX profile " + SYS_ID))
        self.assertEqual(setup.vars["options.snr_zp"].get(), "21.3")
        self.assertIn("disabled", setup.snr_entries["snr_zp"].state())
        self.assertEqual(setup.snr_tags["snr_zp"].cget("text"), "from FLUX")
        self.assertEqual(setup.snr_tags["snr_read_noise"].cget("text"), "from FLUX")       # the camera's spec
        self.assertTrue(setup.snr_tags["snr_dark"].cget("text").startswith("estimate"))
        setup.vars["options.snr_dark"].set("0.02")
        app.update()
        self.assertEqual(setup.snr_tags["snr_dark"].cget("text"), "set here")
        self.assertTrue(setup.save())
        o = settings.load_settings()["options"]
        self.assertEqual(o["snr_dark"], 0.02)
        self.assertIsNone(o["snr_zp"], "a value shown from FLUX is never saved as the user's")
        self.assertEqual(o["snr_profile"], "auto")
        # None: every field the user's own
        setup.vars["options.snr_profile"].set(setup.SNR_NONE)
        setup._snr_show()
        self.assertNotIn("disabled", setup.snr_entries["snr_zp"].state())
        self.assertEqual(setup.vars["options.snr_zp"].get(), "")
        self.assertTrue(setup.snr_line.cget("text").startswith("No SHOBS-FLUX profile (you chose none)"))
        setup.vars["options.snr_zp"].set("20.8")
        self.assertTrue(setup.save())
        o = settings.load_settings()["options"]
        self.assertEqual((o["snr_profile"], o["snr_zp"]), ("none", 20.8))
        bad = setup.vars["options.snr_cy"]
        bad.set("99")
        self.assertFalse(setup.save())
        self.assertIn("Scintillation C_Y must be between", setup.msg_var.get())

    def test_setup_without_profiles(self):
        self.golden_home()
        app = self.open()
        setup = app.pages[0]
        self.assertTrue(setup.snr_line.cget("text").startswith("No SHOBS-FLUX profile matches"))
        self.assertTrue(setup.snr_tags["snr_zp"].cget("text").startswith("estimate "))
        self.assertEqual(setup.snr_box.cget("values")[:2], (setup.SNR_AUTO, setup.SNR_NONE))

    def test_find_dialog_hide_out_of_reach(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        dlg = self.sa.TransitSearchDialog(app, page, modal=False)
        self.assertFalse(dlg.hide_var.get())
        dlg.hide_var.set(True)
        real = page.find_suggested
        page.find_suggested = lambda: None
        self.addCleanup(setattr, page, "find_suggested", real)
        dlg.search()
        self.assertTrue(settings.load_settings()["options"]["suggest_hide_out_of_reach"])
        page._hidden_out_of_reach = 2
        page._found(([], ""), None)
        self.assertIn("2 out of reach left out", page.find_ind.var.get())

    def test_schedule_and_obsplan_show_it(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        page.build(date(2026, 10, 20), 1)
        self.pump()
        tab = page.tabs[0]
        rows = {b.name: r for _i, b, r in tab.table_rows(page.night(0))}
        toi = rows["TOI-2046 b"]
        self.assertTrue(toi["exposure"].endswith(" s (est.)"), toi)          # Longest safe: no FLUX profile
        self.assertTrue(toi["notes"].startswith("Detectable? (S/N ~"), toi["notes"])
        app.show_page(3)
        app.update()
        self.assertIn("Detectable?", app.pages[3].card_text())


if __name__ == "__main__":
    unittest.main()
