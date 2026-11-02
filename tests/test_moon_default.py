"""Setup's default Moon distance (1.3.0, John 26 Oct): by the Moon's phase, fixed, or off; a target's own
"Moon at least" wins; only while the Moon is up; used by the planner (fixed-time and flexible) and by Find
suggested transits; the Setup page's fields, their checks and the stale schedule.

Golden site, nights of 19-24 Oct 2026: the Moon is 65 % lit on the 19th (quarter), 82 % on the 21st (gibbous) and
98.5 % on the 24th (full); on the 21st it sets at 03:51."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="sched_moon_")
os.environ.setdefault("SHOBS_SCHED_HOME", _TMP)
os.environ.setdefault("SHOBS_P_SETTINGS", os.path.join(_TMP, "none.json"))

import planner  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import Optics, Site, Target  # noqa: E402
from tests.test_app import HAVE_TK, Base  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "golden_oct19_24.json")
with open(FIXTURE, encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)
SITE = Site.from_dict(GOLDEN["site"])
OPTICS = Optics.from_dict(GOLDEN["optics"])
D21 = date(2026, 10, 21)


def field_(name, ra, dec, rules=None, block=105):
    return Target(id=name, name=name, kind="campaign", mode="D", ra=ra, dec=dec, vmag=11.0, exposure_s=60,
                  block_min=block, min_block_min=45, rules=list(rules or []))


def build(targets, options=None, d=D21):
    return planner.build_night(d, SITE, OPTICS, targets, options or {})


class TestPhaseFunction(unittest.TestCase):
    def test_phase_names(self):
        f = settings.moon_phase_name
        self.assertEqual([f(x) for x in (0.0, 0.29, 0.30, 0.69, 0.70, 0.94, 0.95, 1.0)],
                         ["crescent", "crescent", "quarter", "quarter", "gibbous", "gibbous", "full", "full"])
        self.assertEqual(f(None), "crescent")

    def test_defaults_and_words(self):
        d = settings.DEFAULT_OPTIONS
        self.assertEqual(d["moon_sep_mode"], "phase")
        self.assertEqual((d["moon_sep_crescent"], d["moon_sep_quarter"], d["moon_sep_gibbous"], d["moon_sep_full"],
                          d["moon_sep_fixed"]), (20.0, 35.0, 50.0, 70.0, 30.0))
        self.assertEqual(settings.default_moon_sep({}, 0.82), (50.0, "default for a gibbous Moon 50°"))
        self.assertEqual(settings.default_moon_sep({}, 0.1), (20.0, "default for a crescent Moon 20°"))
        self.assertEqual(settings.default_moon_sep({"moon_sep_full": 80}, 0.99), (80.0, "default for a full Moon 80°"))
        self.assertEqual(settings.default_moon_sep({"moon_sep_mode": "fixed", "moon_sep_fixed": 25}, 0.99),
                         (25.0, "Setup's default 25°"))
        self.assertEqual(settings.default_moon_sep({"moon_sep_mode": "off"}, 0.99), (None, ""))
        # a hand-edited bad value falls back to the default; an unknown mode to "phase"
        self.assertEqual(settings.default_moon_sep({"moon_sep_gibbous": "x"}, 0.8)[0], 50.0)
        self.assertEqual(settings.default_moon_sep({"moon_sep_gibbous": 400}, 0.8)[0], 50.0)
        self.assertEqual(settings.default_moon_sep({"moon_sep_mode": "?"}, 0.5)[0], 35.0)
        self.assertEqual(settings.moon_sep_text({}), "by phase, 20–70°")
        self.assertEqual(settings.moon_sep_text({"moon_sep_mode": "fixed"}), "30°")
        self.assertEqual(settings.moon_sep_text({"moon_sep_mode": "off"}), "off")

    def test_old_settings_file_gets_the_defaults(self):
        tmp = tempfile.mkdtemp(prefix="sched_moon_old_")
        old = os.environ.get("SHOBS_SCHED_HOME")
        os.environ["SHOBS_SCHED_HOME"] = tmp
        try:
            with open(settings.settings_path(), "w", encoding="utf-8") as fh:
                json.dump({"version": "1.2.0", "options": {"suggest_vmax": 12.5, "list_caps_25": True}}, fh)
            o = settings.load_settings()["options"]
            self.assertEqual((o["moon_sep_mode"], o["moon_sep_gibbous"], o["suggest_vmax"]), ("phase", 50.0, 12.5))
        finally:
            os.environ["SHOBS_SCHED_HOME"] = old


class TestPlanner(unittest.TestCase):
    # (345°, +33°) on 21 Oct: up 20:10-03:30 with the 82 % (gibbous) Moon up; nearest 39.7°
    NEAR = (345.0, 33.0)

    def test_flexible_default_by_phase(self):
        p = build([field_("Near", *self.NEAR)])
        self.assertEqual(p.blocks_obj(), [])
        reason = p.didnt_fit[0]["reason"]
        self.assertIn("Moon too close", reason)
        self.assertIn("default for a gibbous Moon 50°", reason)

    def test_off_and_fixed(self):
        self.assertEqual(len(build([field_("Near", *self.NEAR)], {"moon_sep_mode": "off"}).blocks_obj()), 1)
        self.assertEqual(len(build([field_("Near", *self.NEAR)],
                                   {"moon_sep_mode": "fixed", "moon_sep_fixed": 30}).blocks_obj()), 1)
        p = build([field_("Near", *self.NEAR)], {"moon_sep_mode": "fixed", "moon_sep_fixed": 45})
        self.assertIn("Setup's default 45°", p.didnt_fit[0]["reason"])
        p = build([field_("Near", *self.NEAR)], {"moon_sep_gibbous": 30})
        self.assertEqual(len(p.blocks_obj()), 1, "a lower gibbous value lets it in")

    def test_own_limit_wins_even_low(self):
        t = field_("Near", *self.NEAR, rules=[{"kind": "min_moon_sep", "value": 10}])
        self.assertEqual(len(build([t]).blocks_obj()), 1, "its own 10° wins over the default 50°")
        t = field_("Near", *self.NEAR, rules=[{"kind": "min_moon_sep", "value": 45}])
        p = build([t], {"moon_sep_mode": "off"})
        self.assertIn("your limit 45°", p.didnt_fit[0]["reason"], "its own limit applies with the default off")

    def test_only_while_the_moon_is_up(self):
        # a morning field (RA 8h) mostly up after the 03:51 moonset: even a 180° default leaves the Moon-down time
        t = field_("Morning", 120.0, 30.0, block=60)
        p = build([t], {"moon_sep_mode": "fixed", "moon_sep_fixed": 180})
        b = p.blocks_obj()
        self.assertEqual(len(b), 1, p.didnt_fit)
        moonset = sky.night_info(D21, SITE)["moonset"]
        self.assertGreaterEqual(b[0].start, moonset - 2 / 1440)

    def test_fixed_time_transit(self):
        # a made-up planet transiting at 23:00 on the meridian at +30° (40° from the gibbous Moon)
        mer = sky.local_jd(D21, 23, 0, SITE.tz)
        ra = float(sky.lst_deg(mer, SITE.lon))
        t0 = mer + float(sky.bjd_minus_jd_days(mer, ra, 30.0))
        t = Target(id="P", name="P b", kind="transit", mode="T", ra=ra, dec=30.0, vmag=11,
                   planet={"name": "P b", "period": 2.5, "t0": t0, "t14": 2 / 24, "depth": 0.01,
                           "t0_err": 0.0003, "period_err": 1e-6})
        p = build([t])
        self.assertEqual(p.blocks_obj(), [])
        self.assertIn("default for a gibbous Moon 50°", p.didnt_fit[0]["reason"])
        self.assertIn("Moon too close during the transit", p.didnt_fit[0]["reason"])
        self.assertEqual(len(build([t], {"moon_sep_mode": "off"}).blocks_obj()), 1)
        t.rules = [{"kind": "min_moon_sep", "value": 30}]
        self.assertEqual(len(build([t]).blocks_obj()), 1, "its own 30° wins")
        t.rules = [{"kind": "min_moon_sep", "value": 45}]
        self.assertIn("your limit 45°", build([t], {"moon_sep_mode": "off"}).didnt_fit[0]["reason"])


class TestSuggest(unittest.TestCase):
    NIGHTS = [date(2026, 10, 19 + i) for i in range(5)]

    def test_suggest_skips_a_too_close_moon(self):
        with open(os.path.join(HERE, "fixtures", "targets_catalog_small.csv"), encoding="utf-8") as fh:
            cat = T.parse_catalog_csv(fh.read())
        off = T.suggest_transits(SITE, OPTICS, {"moon_sep_mode": "off"}, self.NIGHTS, catalog=cat)
        self.assertTrue(off)
        # every kept transit has the Moon (while up) at least the phase default away
        on = T.suggest_transits(SITE, OPTICS, {}, self.NIGHTS, catalog=cat)
        for t in on:
            i = t.suggest_info
            night = date.fromisoformat(i["night"])
            lim = settings.default_moon_sep({}, sky.night_info(night, SITE)["moon_illum"])[0]
            import numpy as np
            grid = np.linspace(i["t1"] - 30 / 1440, i["t4"] + 30 / 1440, 50)
            sep = np.asarray(sky.target_moon_sep(t.ra, t.dec, grid))
            up = np.asarray(sky.moon_alt_at(SITE, grid)) > 0
            self.assertTrue(np.all((sep >= lim - 0.5) | ~up), t.name)
        # a 180° fixed default with the Moon up all of these nights leaves only Moon-down transits (none here)
        none = T.suggest_transits(SITE, OPTICS, {"moon_sep_mode": "fixed", "moon_sep_fixed": 180}, self.NIGHTS,
                                  catalog=cat)
        self.assertLess(len(none), len(off))


@unittest.skipUnless(HAVE_TK, "needs Tk")
class TestSetupPage(Base):
    def test_fields_load_save_check_and_stale(self):
        self.golden_home()
        app = self.open()
        app.show_page(2)
        app.pages[2].build()
        self.pump()
        self.assertTrue(app.schedule.nights)
        app.show_page(0)
        setup = app.pages[0]
        v = setup.vars
        self.assertEqual(v["options.moon_sep_mode"].get(), "phase")
        self.assertEqual([v[f"options.moon_sep_{p}"].get() for p in settings.MOON_PHASES], ["20", "35", "50", "70"])
        self.assertEqual(v["options.moon_sep_fixed"].get(), "30")
        app.pages[2].setup_stale = False
        v["options.moon_sep_mode"].set("fixed")
        v["options.moon_sep_fixed"].set("40")
        v["options.moon_sep_gibbous"].set("55")
        self.assertTrue(setup.save(), setup.msg_var.get())
        o = settings.load_settings()["options"]
        self.assertEqual((o["moon_sep_mode"], o["moon_sep_fixed"], o["moon_sep_gibbous"]), ("fixed", 40.0, 55.0))
        self.assertTrue(app.pages[2].setup_stale, "a Moon change makes the schedule stale")
        v["options.moon_sep_full"].set("200")
        self.assertFalse(setup.save())
        self.assertIn("Moon distance for a full Moon must be between 0 and 180", setup.msg_var.get())
        v["options.moon_sep_full"].set("abc")
        self.assertFalse(setup.save())
        self.assertIn("Moon distance for a full Moon must be a number", setup.msg_var.get())
        v["options.moon_sep_full"].set("")
        v["options.moon_sep_fixed"].set("-1")
        self.assertFalse(setup.save())
        self.assertIn("Fixed Moon distance must be between 0 and 180", setup.msg_var.get())
        v["options.moon_sep_fixed"].set("")             # empty = the default
        self.assertTrue(setup.save(), setup.msg_var.get())
        o = settings.load_settings()["options"]
        self.assertEqual((o["moon_sep_fixed"], o["moon_sep_full"]), (30.0, 70.0))

    def test_transit_search_text_names_the_moon_default(self):
        self.golden_home()
        app = self.open()
        dlg = self.sa.TransitSearchDialog(app, app.pages[1], modal=False)
        try:
            texts = []

            def walk(w):
                for c in w.winfo_children():
                    try:
                        texts.append(str(c.cget("text")))
                    except Exception:
                        pass
                    walk(c)
            walk(dlg.win)
            joined = " ".join(texts)
            self.assertIn("Setup's Moon distance (by phase, 20–70°)", joined)
        finally:
            dlg.win.destroy()


if __name__ == "__main__":
    unittest.main()
