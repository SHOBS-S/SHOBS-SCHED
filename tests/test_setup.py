"""Setup: reading a FITS frame's header, SHOBS-P's finder limits, the old site-name slip, and the source tags.

    cd /home/claude/shobs-sched && xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_setup -v

The FITS and settings tests need no display; the Setup-page tests run the app under xvfb (skipped without Tk)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fits_header  # noqa: E402
import settings  # noqa: E402
from models import Target  # noqa: E402
from tests.test_app import HAVE_TK, Base  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
ASI = os.path.join(FIX, "fits_header_asi2600mc.fits")      # John's camera, header only (no image data)
MONO = os.path.join(FIX, "fits_header_mono.fits")
BIN2 = os.path.join(FIX, "fits_header_bin2.fits")


class TestFitsHeader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched_fits_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_asiair_header(self):
        self.assertEqual(os.path.getsize(ASI), 28800, "header only: the fixture has no image data")
        h = fits_header.read_header(ASI)
        self.assertEqual(h["INSTRUME"], "ZWO ASI2600MC Air")
        self.assertEqual(h["GAIN"], 100)
        self.assertEqual(h["EGAIN"], 0.242862924933434)
        self.assertEqual(h["XPIXSZ"], 3.75999999046326)
        self.assertEqual(h["XBINNING"], 1)
        self.assertEqual(h["FOCALLEN"], 2901)
        self.assertEqual(h["ROTATOR"], 210)
        self.assertEqual(h["BAYERPAT"], "RGGB")
        self.assertIs(h["SIMPLE"], True)
        vals, notes, absent = fits_header.camera_values(h)
        self.assertEqual(vals, {"optics.camera": "ZWO ASI2600MC Air", "optics.color_camera": True,
                                "optics.gain": 100.0, "optics.e_per_adu": 0.242863, "optics.binning": 1,
                                "optics.pixel_um": 3.76, "optics.focal_mm": 2901.0,
                                "optics.camera_angle_deg": 30.0})
        self.assertEqual(notes, ["ROTATOR 210° = 30° (the other pier side)"])
        self.assertEqual(absent, [])
        self.assertNotIn("optics.telescope", vals, "TELESCOP (the mount driver on ASIAIR) is ignored")

    def test_mono_and_missing_keywords(self):
        vals, notes, absent = fits_header.read_camera_values(MONO)
        self.assertIs(vals["optics.color_camera"], False, "BAYERPAT NONE = mono")
        self.assertEqual(vals["optics.gain"], 0.0)
        self.assertEqual(absent, ["EGAIN", "ROTATOR"])
        self.assertNotIn("optics.e_per_adu", vals)
        self.assertEqual(notes, [])
        vals, _n, absent = fits_header.camera_values({"INSTRUME": "Some Cam", "GAIN": 0})
        self.assertNotIn("optics.color_camera", vals, "no BAYERPAT keyword: the sensor type is left alone")
        self.assertIn("BAYERPAT", absent)

    def test_binned_pixel(self):
        vals, notes, _absent = fits_header.read_camera_values(BIN2)
        self.assertEqual(vals["optics.binning"], 2)
        self.assertEqual(vals["optics.pixel_um"], 3.76, "XPIXSZ is the binned pixel: 7.52 / 2")
        self.assertEqual(vals["optics.camera_angle_deg"], 30.0)
        self.assertTrue(any("binned pixel" in n for n in notes))
        self.assertFalse(any("pier side" in n for n in notes), "30° needs no pier-side note")

    def test_full_frame_reads_only_the_header(self):
        path = os.path.join(self.tmp, "frame.fit")
        with open(ASI, "rb") as src, open(path, "wb") as dst:
            dst.write(src.read())
            dst.write(b"\x7f" * 2880 * 50)              # image data after the header
        self.assertEqual(fits_header.read_header(path)["ROTATOR"], 210)

    def test_truncated_and_bad_files(self):
        cut = os.path.join(self.tmp, "cut.fits")         # cut off inside the header (no END card)
        with open(ASI, "rb") as src, open(cut, "wb") as dst:
            dst.write(src.read(2880 + 800))
        h = fits_header.read_header(cut)
        self.assertEqual(h["INSTRUME"], "ZWO ASI2600MC Air")
        self.assertNotIn("IMAGEW", h)
        bad = os.path.join(self.tmp, "notes.fits")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("not a FITS file")
        with self.assertRaises(fits_header.FitsError):
            fits_header.read_header(bad)
        with self.assertRaises(fits_header.FitsError):
            fits_header.read_header(os.path.join(self.tmp, "absent.fits"))
        empty = os.path.join(self.tmp, "empty.fits")
        open(empty, "wb").close()
        with self.assertRaises(fits_header.FitsError):
            fits_header.read_header(empty)

    def test_card_values(self):
        v = fits_header._value
        self.assertEqual(v(" 'O''Brien'  / quoted"), "O'Brien")
        self.assertEqual(v("              1.5D2 / exponent"), 150.0)
        self.assertIsNone(v("     / nothing"))
        self.assertIs(v("                   F"), False)


class SettingsHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched_setup_test_")
        self._env = {k: os.environ.get(k) for k in ("SHOBS_SCHED_HOME", "SHOBS_P_SETTINGS")}
        os.environ["SHOBS_SCHED_HOME"] = self.tmp
        os.environ["SHOBS_P_SETTINGS"] = os.path.join(self.tmp, "no_shobs_p.json")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shobs_p(self, data: dict):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        return p

    def log_text(self) -> str:
        try:
            with open(settings.log_path(), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""


FINDER = {"min_alt": 35, "sun_alt": -15, "baseline": 60, "vmin": 9, "vmax": 13.5, "depth": 0.5, "moon": 0,
          "after": "19:00", "before": "06:00", "full_only": True}


class TestFinderAndSiteName(SettingsHome):
    def test_finder_limits_become_starting_values(self):
        self.shobs_p({"site_lat": 35.3, "finder": FINDER})
        s, taken = settings.merge_shobs_p(settings.load_settings())
        self.assertEqual(s["site"]["min_alt"], 35.0)
        self.assertEqual(s["site"]["sun_alt"], -15.0)
        self.assertEqual(s["options"]["suggest_baseline_min"], 60.0)
        self.assertEqual(s["options"]["suggest_vmin"], 9.0)
        self.assertEqual(s["options"]["suggest_vmax"], 13.5)
        self.assertAlmostEqual(s["options"]["suggest_min_depth"], 0.005, places=12, msg="0.5 % → 0.005")
        for k in ("site.min_alt", "site.sun_alt", "options.suggest_vmax", "options.suggest_min_depth"):
            self.assertIn(k, taken)
        self.assertNotIn("min_moon_sep", json.dumps(s), "no matching Moon-separation option: skipped")

    def test_standalone_keeps_own_defaults(self):
        s, taken = settings.merge_shobs_p(settings.load_settings())        # no SHOBS-P file at all
        self.assertEqual(taken, [])
        self.assertEqual(s["site"]["min_alt"], 30.0)
        self.assertEqual(s["site"]["sun_alt"], -18.0)
        for k in ("suggest_vmin", "suggest_vmax", "suggest_min_depth", "suggest_baseline_min"):
            self.assertEqual(s["options"][k], settings.DEFAULT_OPTIONS[k])
        self.shobs_p({"site_lat": 35.3})                                      # SHOBS-P without a finder section
        s, taken = settings.merge_shobs_p(settings.load_settings())
        self.assertEqual(s["options"]["suggest_vmax"], settings.DEFAULT_OPTIONS["suggest_vmax"])
        self.assertEqual(taken, ["site.lat"])

    def test_garbage_skipped_and_set_here_wins(self):
        self.shobs_p({"finder": {"min_alt": "high", "sun_alt": 5, "baseline": None, "vmin": True, "vmax": "12",
                                 "depth": float("nan")}})
        s, taken = settings.merge_shobs_p(settings.load_settings())
        self.assertEqual(taken, ["options.suggest_vmax"])
        self.assertEqual(s["options"]["suggest_vmax"], 12.0)
        self.assertEqual(s["site"]["sun_alt"], -18.0, "a Sun above −6° is not darkness")
        self.shobs_p({"finder": {"vmin": 14, "vmax": 10}})                   # an inverted range: skipped whole
        self.assertEqual(settings.finder_values(settings.read_shobs_p()), {})
        self.shobs_p({"finder": FINDER})
        s = settings.load_settings()
        s["site"]["min_alt"] = 25.0
        s["set_here"] = ["site.min_alt"]
        s, taken = settings.merge_shobs_p(s)
        self.assertEqual(s["site"]["min_alt"], 25.0, "set here always wins")
        self.assertNotIn("site.min_alt", taken)
        self.assertEqual(s["site"]["sun_alt"], -15.0)

    def test_old_site_name_slip_cleared_and_logged(self):
        self.shobs_p({"telescope_name": "iOptron 14 RC", "site_lat": 35.3})
        s = settings.load_settings()
        s["site"]["name"] = "iOptron 14 RC"
        settings.save_settings(s)
        s, _taken = settings.merge_shobs_p(settings.load_settings())
        self.assertEqual(s["site"]["name"], "")
        self.assertEqual(s["optics"]["telescope"], "iOptron 14 RC")
        lines = [ln for ln in self.log_text().splitlines() if "telescope name" in ln]
        self.assertEqual(len(lines), 1, self.log_text())
        self.assertIn("iOptron 14 RC", lines[0])
        # a real site name stays; the same text typed on purpose ("set here") stays too
        s = settings.load_settings()
        s["site"]["name"] = "Shiloh Hill Observatory"
        self.assertEqual(settings.merge_shobs_p(s)[0]["site"]["name"], "Shiloh Hill Observatory")
        s["site"]["name"] = "iOptron 14 RC"
        s["set_here"] = ["site.name"]
        self.assertEqual(settings.merge_shobs_p(s)[0]["site"]["name"], "iOptron 14 RC")

    def test_from_fits_wins_over_shobs_p(self):
        self.shobs_p({"focal_mm": 2900, "pixel_um": 3.8, "aperture_mm": 356})
        s = settings.load_settings()
        s["optics"]["focal_mm"] = 2901.0
        s["from_fits"] = {"optics.focal_mm": 2901.0}
        s, taken = settings.merge_shobs_p(s)
        self.assertEqual(s["optics"]["focal_mm"], 2901.0)
        self.assertNotIn("optics.focal_mm", taken)
        self.assertEqual(s["optics"]["pixel_um"], 3.8)
        s["from_fits"] = ["not", "a", "dict"]                                # an odd file still loads
        self.assertEqual(settings.from_fits(s), {})


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSetupPage(Base):
    def site(self, setup):
        setup.vars["site.lat"].set("35.3")
        setup.vars["site.lon"].set("-81.94")
        setup.vars["site.tz"].set("America/New_York")

    def test_read_fits_fills_form_tags_and_summary(self):
        app = self.open()
        setup = app.pages[0]
        self.site(setup)
        setup.vars["optics.focal_mm"].set("2901")
        vals = setup.read_fits(ASI)
        self.assertEqual(vals["optics.camera_angle_deg"], 30.0)
        v = setup.vars
        self.assertEqual(v["optics.camera"].get(), "ZWO ASI2600MC Air")
        self.assertEqual(v["optics.color"].get(), "color")
        self.assertEqual(v["optics.gain"].get(), "100")
        self.assertEqual(v["optics.e_per_adu"].get(), "0.242863")
        self.assertEqual(v["optics.pixel_um"].get(), "3.76")
        self.assertEqual(v["optics.binning"].get(), "1")
        self.assertEqual(v["optics.camera_angle_deg"].get(), "30")
        self.assertEqual(v["optics.telescope"].get(), "", "TELESCOP is not the telescope")
        summary = setup.fits_var.get()
        self.assertTrue(summary.startswith("✔ Read the header of fits_header_asi2600mc.fits (ZWO ASI2600MC Air)"),
                        summary)
        self.assertIn("Camera: empty → ZWO ASI2600MC Air", summary)
        self.assertIn("Usual camera angle: empty → 30°", summary)
        self.assertIn("Already the same: Sensor, Binning, Focal length.", summary)
        self.assertIn("ROTATOR 210° = 30° (the other pier side)", summary)
        self.assertTrue(setup.fits_msg.winfo_manager())
        for k in ("optics.camera", "optics.gain", "optics.pixel_um", "optics.focal_mm", "optics.color",
                  "optics.camera_angle_deg", "optics.binning"):
            self.assertEqual(self.tag(setup, k), "from FITS", k)
        self.assertEqual(self.tag(setup, "site.lat"), "set here")
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertIn("Saved.", setup.fits_var.get())
        s = settings.load_settings()
        self.assertEqual(s["from_fits"]["optics.camera_angle_deg"], 30.0)
        self.assertEqual(s["optics"]["e_per_adu"], 0.242863)
        self.assertNotIn("optics.gain", s["set_here"])
        self.assertIn("site.lat", s["set_here"])
        self.assertEqual(self.tag(setup, "optics.gain"), "from FITS")
        # typing over a FITS value makes it the user's own
        v["optics.gain"].set("120")
        self.assertEqual(self.tag(setup, "optics.gain"), "set here")
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertNotIn("optics.gain", s["from_fits"])
        self.assertIn("optics.gain", s["set_here"])

    def test_mono_frame_lists_missing_and_switches_sensor(self):
        app = self.open()
        setup = app.pages[0]
        setup.read_fits(MONO)
        self.assertEqual(setup.vars["optics.color"].get(), "mono")
        text = setup.fits_var.get()
        self.assertTrue(text.startswith("⚠ "), text)
        self.assertIn("Sensor: Color → Mono", text)
        self.assertIn("Not in this frame: e⁻ per ADU (EGAIN), Usual camera angle (ROTATOR).", text)
        self.assertEqual(self.tag(setup, "optics.e_per_adu"), "")
        bad = os.path.join(self.tmp, "x.fits")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("hello")
        self.assertIsNone(setup.read_fits(bad))
        self.assertTrue(setup.fits_var.get().startswith("✖ x.fits could not be read"), setup.fits_var.get())

    def test_fits_values_win_over_shobs_p_on_later_opens(self):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 35.303, "site_lon": -81.9407, "site_tz": "America/New_York", "focal_mm": 2900,
                       "pixel_um": 3.8, "aperture_mm": 356}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        self.assertEqual(self.tag(setup, "optics.focal_mm"), "from SHOBS-P")
        setup.read_fits(ASI)
        self.assertIn("Focal length: 2900 mm → 2901 mm", setup.fits_var.get())
        self.assertIn("Pixel size: 3.8 µm → 3.76 µm", setup.fits_var.get())
        app.show_page(1)                                   # leaving saves (a FITS read counts as a change)
        s = settings.load_settings()
        self.assertEqual(s["optics"]["focal_mm"], 2901.0)
        self.assertIn("optics.focal_mm", s["from_fits"])
        self.assertNotIn("optics.focal_mm", s["set_here"])
        app.destroy()
        self.app = None
        app = self.open()                                  # SHOBS-P still says 2900: the frame's value wins
        app.show_page(0)
        self.assertEqual(app.optics.focal_mm, 2901.0)
        self.assertEqual(app.optics.pixel_um, 3.76)
        self.assertEqual(app.optics.aperture_mm, 356)
        self.assertEqual(self.tag(app.pages[0], "optics.focal_mm"), "from FITS")
        self.assertEqual(self.tag(app.pages[0], "optics.aperture_mm"), "from SHOBS-P")

    def test_finder_limits_on_the_page_and_round_trip(self):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 35.303, "site_lon": -81.9407, "site_tz": "America/New_York",
                       "finder": FINDER}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        self.assertIn("Tonight's-transits finder", setup.prov_var.get())
        self.assertEqual(setup.vars["site.min_alt"].get(), "35")
        self.assertEqual(setup.vars["site.sun_alt"].get(), "Sun −15°")
        self.assertEqual(self.tag(setup, "site.sun_alt"), "from SHOBS-P")
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertEqual(s["site"]["sun_alt"], -15.0, "a darkness that is not −18/−12 survives a Save")
        self.assertNotIn("site.min_alt", s["set_here"])
        setup.vars["site.min_alt"].set("40")
        self.assertTrue(setup.save())
        self.assertIn("site.min_alt", settings.load_settings()["set_here"])
        self.assertEqual(self.tag(setup, "site.min_alt"), "set here")

    def test_overhead_labels_say_what_they_cover(self):
        self.golden_home()
        app = self.open()
        setup = app.pages[0]
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(" ".join(str(c.cget("text")).split()))
                except Exception:
                    pass
                walk(c)
        walk(setup.frame)
        self.assertIn("Per-frame overhead (download/save, all targets)", texts)
        self.assertTrue(any("Added to EVERY target's exposure" in t and "wins for that target" in t for t in texts))
        hd = next(t for t in app.targets if t.name.startswith("HD 219134"))
        dlg = self.sa.EditTargetDialog(app, hd, modal=False)
        labels = []
        for c in dlg.sb.inner.winfo_children():
            try:
                labels.append(" ".join(str(c.cget("text")).split()))
            except Exception:
                pass
        self.assertIn("Seconds between frames (full interval, this target)", labels)
        dlg.win.destroy()

    def test_standalone_defaults_have_no_tags(self):
        app = self.open()
        setup = app.pages[0]
        for k in ("site.min_alt", "site.max_airmass", "optics.linear_limit_adu", "site.mount", "optics.binning"):
            self.assertEqual(self.tag(setup, k), "", k)
        self.site(setup)
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertNotIn("site.min_alt", s["set_here"], "a default is not 'set here'")
        self.assertEqual(self.tag(setup, "site.lat"), "set here")
        self.assertEqual(self.tag(setup, "site.min_alt"), "")


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestLocalTimeLabels(Base):
    def test_setup_targets_and_edit_say_local_time(self):
        self.golden_home()
        app = self.open()
        app.show_page(0)
        hint = str(app.pages[0].tz_hint.cget("text"))
        self.assertRegex(hint, r"^Times show as Local time \(UTC[−+]\d+\) now\.$")
        app.pages[0].vars["site.tz"].set("Mars/Olympus")
        self.assertIn("Not a zone name", str(app.pages[0].tz_hint.cget("text")))
        app.pages[0].load_form()
        app.show_page(1)
        head = app.pages[1].tables["transit"].box.cget("text")
        self.assertRegex(head, r"next event in the \d+ planned nights?, Local time \(UTC[−+]\d+( → UTC[−+]\d+)?\)$")
        dlg = self.sa.EditTargetDialog(app, app.targets[0], modal=False)
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(str(c.cget("text")))
                except Exception:
                    pass
                walk(c)
        walk(dlg.sb.inner)
        self.assertTrue(any(t.startswith("HH:MM-HH:MM, Local time (UTC") for t in texts), texts)
        dlg.vars["window"].set("late")
        self.assertFalse(dlg.save())
        self.assertIn("type two clock times in Local time (UTC", dlg.msg_var.get())
        dlg.win.destroy()

    def test_archive_message_has_no_stray_bracket(self):
        self.golden_home()
        app = self.open()
        dlg = self.sa.EditTargetDialog(app, Target(name="HD 2", kind="campaign", mode="V", ra=1.0, dec=2.0),
                                       modal=False)
        dlg.show_planets(None, "The NASA Exoplanet Archive did not answer (HTTP 503) and there is no saved copy for "
                               "HD 2. Try again later.")
        text = dlg.pl_ind.var.get()
        self.assertEqual(text, "✖ Couldn't reach the NASA Exoplanet Archive to list this star's planets. The NASA "
                               "Exoplanet Archive did not answer (HTTP 503) and there is no saved copy for HD 2. Try "
                               "again later. Type a planet below instead.")
        dlg.show_planets(None, "timed out")
        self.assertEqual(dlg.pl_ind.var.get(), "✖ Couldn't reach the NASA Exoplanet Archive to list this star's "
                                               "planets (timed out). Type a planet below instead.")
        dlg.win.destroy()

    def test_edit_dialog_recentres_when_it_grows(self):
        self.golden_home()
        app = self.open()
        dlg = self.sa.EditTargetDialog(app, app.targets[0], modal=False)
        dlg.win.geometry("200x150+0+0")                    # small, in the corner
        app.update()
        dlg.win.minsize(1, 1)
        dlg._fit()
        app.update()
        w, x = dlg.win.winfo_width(), dlg.win.winfo_rootx()
        self.assertGreater(w, 200)
        mid = x + w / 2
        app_mid = app.winfo_rootx() + app.winfo_width() / 2
        self.assertLess(abs(mid - app_mid), 40, (x, w, app.winfo_rootx(), app.winfo_width()))
        dlg.win.destroy()


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestEditDialogOldFiles(Base):
    def test_unchanged_save_with_inconsistent_kind_and_mode(self):
        self.golden_home()
        app = self.open()
        for kind, mode, want in (("variable", "T", ("variable", "V")), ("campaign", "T", ("campaign", "V")),
                                 ("transit", "V", ("transit", "T")), ("transit", "D", ("transit", "T"))):
            t = Target(name="Old 1", kind=kind, mode=mode, ra=10.0, dec=20.0,
                       planet={"name": "Old 1", "period": 2.0, "t0": 2460000.5, "t14": 0.1}
                       if kind == "transit" else None)
            saved = []
            dlg = self.sa.EditTargetDialog(app, t, on_save=saved.append, modal=False)
            self.assertTrue(dlg.save(), f"{kind}/{mode}: {dlg.msg_var.get()}")
            self.assertEqual((saved[0].kind, saved[0].mode), want)


# ---- review round 2: finder limits are starting values; v1.0.0 files; tags stable; FITS edge cases -----------------

def saved_file() -> dict:
    with open(settings.settings_path(), encoding="utf-8") as fh:
        return json.load(fh)


def v1_file(home: str, **site):
    """A settings file shaped like v1.0.0's: set_here present, no from_fits / from_finder keys."""
    s = settings.default_settings()
    for k in ("from_fits", "from_finder", "asiair_dir"):
        s.pop(k, None)
    s["site"].update({"lat": 35.3, "lon": -81.94, "tz": "America/New_York"}, **site)
    s["set_here"] = ["site.lat"]
    with open(os.path.join(home, ".shobs_sched_settings.json"), "w", encoding="utf-8") as fh:
        json.dump(s, fh)
    return s


class TestFinderSeedsOnce(SettingsHome):
    def open_once(self):
        s, taken = settings.merge_shobs_p(settings.load_settings())
        s.pop("site_name_cleared", None)
        settings.save_settings(s)
        return s, taken

    def test_v1_file_keeps_own_limits(self):
        v1_file(self.tmp, min_alt=25.0, sun_alt=-12.0)
        self.shobs_p({"site_lat": 35.3, "finder": {"min_alt": 30, "sun_alt": -18, "vmax": 13.5, "depth": 0.5}})
        s, taken = self.open_once()
        self.assertEqual(s["site"]["min_alt"], 25.0, "John's own minimum altitude survives")
        self.assertEqual(s["site"]["sun_alt"], -12.0, "and his darkness")
        self.assertIn("site.min_alt", s["set_here"])
        self.assertIn("site.sun_alt", s["set_here"])
        self.assertEqual(s["options"]["suggest_vmax"], 13.5, "a limit still at SCHED's default is seeded")
        self.assertEqual(s["from_finder"], {"options.suggest_vmax": 13.5, "options.suggest_min_depth": 0.005})
        self.assertIn("options.suggest_vmax", taken)
        self.assertNotIn("site.min_alt", taken)
        self.assertIn("from_fits", saved_file(),
                      "saved in the new shape: the migration runs once")
        # SHOBS-P's finder changes (it rewrites it on every search): nothing moves
        self.shobs_p({"site_lat": 35.3, "finder": {"min_alt": 40, "sun_alt": -15, "vmax": 14.5, "depth": 1.0}})
        s, taken = self.open_once()
        self.assertEqual((s["site"]["min_alt"], s["site"]["sun_alt"]), (25.0, -12.0))
        self.assertEqual(s["options"]["suggest_vmax"], 13.5, "seeded once, not re-applied")
        self.assertEqual(s["options"]["suggest_min_depth"], 0.005)
        self.assertIn("options.suggest_vmax", taken, "still tagged as SHOBS-P's (its seeded value)")

    def test_defaults_seeded_once(self):
        self.shobs_p({"finder": {"min_alt": 35, "sun_alt": -15}})
        s, _t = self.open_once()                                    # a fresh home: SCHED's defaults
        self.assertEqual((s["site"]["min_alt"], s["site"]["sun_alt"]), (35.0, -15.0))
        self.shobs_p({"finder": {"min_alt": 40, "sun_alt": -18}})
        s, taken = self.open_once()
        self.assertEqual((s["site"]["min_alt"], s["site"]["sun_alt"]), (35.0, -15.0))
        self.assertIn("site.min_alt", taken)

    def test_v1_file_at_defaults_is_seeded(self):
        v1_file(self.tmp)                                           # min_alt 30, sun_alt −18: SCHED's defaults
        self.shobs_p({"finder": {"min_alt": 35}})
        s, _t = self.open_once()
        self.assertEqual(s["site"]["min_alt"], 35.0)
        self.assertNotIn("site.min_alt", s["set_here"])

    def test_site_name_cleared_is_reported(self):
        self.shobs_p({"telescope_name": "iOptron 14 RC"})
        s = settings.load_settings()
        s["site"]["name"] = "iOptron 14 RC"
        s, _t = settings.merge_shobs_p(s)
        self.assertEqual(s["site_name_cleared"], "iOptron 14 RC")

    def test_infinite_numbers_are_not_values(self):
        self.assertIsNone(settings._num(float("inf")))
        self.assertIsNone(settings._num("-inf"))
        self.assertIsNone(settings._num("1e999"))
        self.shobs_p({"focal_mm": "inf", "finder": {"min_alt": float("inf")}})
        s, taken = settings.merge_shobs_p(settings.load_settings())
        self.assertEqual(taken, [])


class TestFitsEdgeCases(unittest.TestCase):
    def test_bayerpat_words_for_mono(self):
        for word in ("NONE", "mono", "N/A", " none "):
            vals, _n, _m = fits_header.camera_values({"BAYERPAT": word})
            self.assertIs(vals["optics.color_camera"], False, word)
        self.assertIs(fits_header.camera_values({"BAYERPAT": "GRBG"})[0]["optics.color_camera"], True)

    def test_infinite_egain_and_rotation_rounding(self):
        vals, notes, missing = fits_header.camera_values({"EGAIN": float("inf"), "ROTATOR": 179.99999,
                                                          "GAIN": float("-inf")})
        self.assertNotIn("optics.e_per_adu", vals)
        self.assertIn("EGAIN", missing)
        self.assertIn("GAIN", missing)
        self.assertEqual(vals["optics.camera_angle_deg"], 0.0, "179.99999° is 180° = 0°, never 180")
        self.assertEqual(fits_header.camera_values({"ROTATOR": 359.9999})[0]["optics.camera_angle_deg"], 0.0)
        self.assertEqual(fits_header.camera_values({"ROTATOR": 30.0})[1], [])

    def test_continue_cards_joined(self):
        def c(text):
            return text.ljust(80)
        data = (c("SIMPLE  =                    T") + c("OBJECT  = 'HD 219134 with a long &'")
                + c("CONTINUE  'name over &'") + c("CONTINUE  'three cards'") + c("NOTE    = 'cut&'")
                + c("GAIN    =                  100") + c("END")).encode("ascii")
        h = fits_header.parse_header(data)
        self.assertEqual(h["OBJECT"], "HD 219134 with a long name over three cards")
        self.assertEqual(h["NOTE"], "cut", "a dangling & is dropped")
        self.assertEqual(h["GAIN"], 100)


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSetupReviewRound2(Base):
    def shobs_p(self, data):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.environ["SHOBS_P_SETTINGS"] = p

    def tags(self, setup):
        return {k: self.tag(setup, k) for k in setup.tags}

    def test_no_edit_save_changes_no_tags(self):
        s = v1_file(self.tmp, min_alt=25.0)
        s["optics"].update({"camera": "ZWO ASI2600MC Air", "gain": 100.0, "e_per_adu": 0.24, "full_well_e": 50000.0,
                            "frame_overhead_s": 3.0, "camera_angle_deg": 30.0})
        with open(settings.settings_path(), "w", encoding="utf-8") as fh:
            json.dump(s, fh)
        self.shobs_p({"site_lat": 35.3, "site_lon": -81.94, "aperture_mm": 356, "focal_mm": 2900,
                      "finder": {"min_alt": 30, "vmax": 13.5}})
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        before = self.tags(setup)
        self.assertEqual(before["optics.camera"], "")
        self.assertEqual(before["site.min_alt"], "set here", "v1.0.0's own limit")
        self.assertEqual(before["optics.aperture_mm"], "from SHOBS-P")
        set_here = sorted(settings.load_settings().get("set_here") or [])
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertEqual(self.tags(setup), before, "an untouched form saves with the tags it opened with")
        after = settings.load_settings()
        self.assertEqual(after["site"]["min_alt"], 25.0)
        for k in ("optics.camera", "optics.gain", "optics.e_per_adu", "optics.full_well_e",
                  "optics.frame_overhead_s", "optics.camera_angle_deg"):
            self.assertNotIn(k, after["set_here"], k)
        self.assertTrue(set(set_here) <= set(after["set_here"]) | {"site.min_alt", "site.sun_alt"})
        # typing over a seeded limit makes it the user's own (never seeded again)
        setup.vars["options.defocus_fwhm_factor"].set("2")
        setup.vars["site.min_alt"].set("28")
        self.assertTrue(setup.save())
        self.assertEqual(self.tag(setup, "site.min_alt"), "set here")
        self.assertEqual(self.tag(setup, "options.defocus_fwhm_factor"), "set here")

    def test_seeded_limit_typed_over_leaves_from_finder(self):
        self.shobs_p({"site_lat": 35.3, "site_lon": -81.94, "site_tz": "America/New_York",
                      "finder": {"min_alt": 35}})
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        self.assertEqual(self.tag(setup, "site.min_alt"), "from SHOBS-P")
        setup.vars["site.min_alt"].set("33")
        self.assertTrue(setup.save())
        s = settings.load_settings()
        self.assertNotIn("site.min_alt", s["from_finder"])
        self.assertIn("site.min_alt", s["set_here"])

    def test_site_name_cleared_said_once_on_setup(self):
        s = settings.default_settings()
        s["site"]["name"] = "iOptron 14 RC"
        settings.save_settings(s)
        self.shobs_p({"telescope_name": "iOptron 14 RC", "site_lat": 35.3})
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        text = setup.prov_var.get()
        self.assertTrue(text.startswith("⚠ Site name cleared: it was SHOBS-P's telescope name (“iOptron 14 RC”); "
                                        "type your observatory's name."), text)
        self.assertNotIn("site_name_cleared", settings.load_settings())
        setup.vars["site.name"].set("Shiloh Hill Observatory")
        setup.vars["site.lon"].set("-81.94")
        setup.vars["site.tz"].set("America/New_York")
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertFalse(setup.prov_var.get().startswith("⚠"))
        self.assertNotIn("site_name_cleared", saved_file())

    def test_infinite_egain_does_not_break_the_button(self):
        path = os.path.join(self.tmp, "inf.fits")
        cards = ["SIMPLE  =                    T", "EGAIN   =                  INF", "GAIN    =                 1E999",
                 "INSTRUME= 'Test Cam'", "END"]
        with open(path, "wb") as fh:
            fh.write("".join(c.ljust(80) for c in cards).ljust(2880).encode("ascii"))
        app = self.open()
        setup = app.pages[0]
        vals = setup.read_fits(path)
        self.assertNotIn("optics.e_per_adu", vals)
        self.assertIn("e⁻ per ADU (EGAIN)", setup.fits_var.get())
        self.assertEqual(setup.vars["optics.camera"].get(), "Test Cam")

    def test_dialog_not_clamped_when_main_window_is_on_another_monitor(self):
        class FakeApp:
            def __init__(self, x):
                self.x = x

            def winfo_rootx(self):
                return self.x

            def winfo_rooty(self):
                return 100
        self.assertEqual(self.sa._clamp_to_screen(FakeApp(-1800), -1500, 200, 600, 400, 1920, 1080), (-1500, 200))
        self.assertEqual(self.sa._clamp_to_screen(FakeApp(2400), 2600, 200, 600, 400, 1920, 1080), (2600, 200))
        self.assertEqual(self.sa._clamp_to_screen(FakeApp(100), 1700, 900, 600, 400, 1920, 1080), (1320, 640))


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSetupReviewRound3(Base):
    def shobs_p(self, data):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.environ["SHOBS_P_SETTINGS"] = p

    def built_home(self):
        """The golden home with a built schedule (so a Setup change can make it stale)."""
        self.golden_home()
        app = self.open()
        app.show_page(2)
        app.pages[2].build()
        self.pump()
        self.assertTrue(app.schedule.nights)
        app.pages[2].setup_stale = False
        return app

    def test_no_edit_save_does_not_make_the_schedule_stale(self):
        app = self.built_home()
        app.show_page(0)
        setup = app.pages[0]
        app.night_info(app.tonight())
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertFalse(app.pages[2].setup_stale, "nothing changed: the schedule is not stale")
        self.assertTrue(app._info_cache, "and the night-info cache is kept")
        # a FITS frame that changes nothing, then leaving the page (which saves)
        setup.read_fits(ASI)                               # the first read changes the camera: stale
        self.assertTrue(setup.save())
        self.assertTrue(app.pages[2].setup_stale)
        app.pages[2].setup_stale = False
        setup.read_fits(ASI)                               # the same frame again changes nothing
        self.assertTrue(setup.dirty_form())
        app.show_page(1)
        self.assertFalse(app.pages[2].setup_stale)
        # a real change does
        app.show_page(0)
        setup.vars["site.min_alt"].set("33")
        self.assertTrue(setup.save())
        self.assertTrue(app.pages[2].setup_stale)
        self.assertFalse(self.sa._differs({"elevation_m": 280.123, "horizon": [[90.0, 25.0]]},
                                          {"elevation_m": 280.1, "horizon": [(90.0, 25.0)]}))
        self.assertTrue(self.sa._differs({"e_per_adu": 0.24}, {"e_per_adu": 0.26}))

    def test_fits_value_typed_back_to_shobs_p_tags_from_shobs_p(self):
        self.shobs_p({"site_lat": 35.303, "site_lon": -81.9407, "site_tz": "America/New_York", "focal_mm": 2900,
                      "aperture_mm": 356})
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        setup.read_fits(ASI)
        self.assertTrue(setup.save())
        self.assertEqual(self.tag(setup, "optics.focal_mm"), "from FITS")
        setup.vars["optics.focal_mm"].set("2900")
        self.assertTrue(setup.save())
        s = settings.load_settings()
        self.assertNotIn("optics.focal_mm", s["set_here"])
        self.assertNotIn("optics.focal_mm", s["from_fits"])
        self.assertEqual(self.tag(setup, "optics.focal_mm"), "from SHOBS-P", "follows SHOBS-P again, said at once")

    def test_provenance_worded_from_the_sources(self):
        self.shobs_p({"site_lat": 35.303, "site_lon": -81.9407, "site_tz": "America/New_York"})
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        self.assertTrue(setup.prov_var.get().startswith("✔ Site read from SHOBS-P (settings of"),
                        setup.prov_var.get())
        self.assertNotIn("camera", setup.prov_var.get().split(".")[0])
        setup.read_fits(ASI)
        self.assertIn("Camera values read from a FITS frame.", setup.prov_var.get())
        os.environ["SHOBS_P_SETTINGS"] = os.path.join(self.tmp, "none.json")
        app.destroy()
        self.app = None
        app = self.open()                                      # stand-alone, camera from FITS only
        app.show_page(0)
        app.pages[0].read_fits(ASI)
        self.assertTrue(app.pages[0].prov_var.get().startswith("✔ Camera values read from a FITS frame."),
                        app.pages[0].prov_var.get())

    def test_site_name_cleared_said_in_the_status_bar_at_start(self):
        self.golden_home()
        s = settings.load_settings()
        s["site"]["name"] = "iOptron 14 RC"
        settings.save_settings(s)
        self.shobs_p({"telescope_name": "iOptron 14 RC"})
        app = self.open()
        self.assertEqual(app.page_index, 0, "always opens on Setup (John 24 Oct)")
        self.assertTrue(app.status_var.get().startswith("⚠ Site name cleared: it was SHOBS-P's telescope name "
                                                        "(“iOptron 14 RC”)"), app.status_var.get())
        self.assertIn("Setup page", app.status_var.get())


if __name__ == "__main__":
    unittest.main()
