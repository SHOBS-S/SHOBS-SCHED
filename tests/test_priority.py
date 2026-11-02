"""ExoClock / ExoFOP priorities in Find suggested transits (1.3.0 item 9). No network: small hand-made replies in
the shapes seen live on John's PC on 2 Nov 2026 (ExoClock planets_json keys "WASP-12b", "55Cnce", "TOI-150.01";
sexagesimal ra_j2000 / dec_j2000; the ExoFOP TOI CSV header) served by a fake targets.http_get.

Run:  xvfb-run -a python -m unittest tests.test_priority -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_targets import (DOWN, NIGHTS, OPTICS, SITE, Base, small_catalog)  # noqa: E402
from tests.test_app import HAVE_TK, Base as AppBase  # noqa: E402

import credits  # noqa: E402
import settings  # noqa: E402
import targets as T  # noqa: E402
from models import Target  # noqa: E402


def exoclock_json(extra: dict | None = None) -> bytes:
    def planet(name, pri, ra, dec, v, mmag, hours, t0, per, recent):
        return {"name": name, "priority": pri, "star": name[:-1], "ra_j2000": ra, "dec_j2000": dec, "v_mag": v,
                "r_mag": v, "gaia_g_mag": v, "depth_r_mmag": mmag, "duration_hours": hours, "ephem_mid_time": t0,
                "ephem_mid_time_e1": 0.0002, "ephem_mid_time_e2": 0.0003, "ephem_period": per,
                "ephem_period_e1": 1e-7, "ephem_period_e2": 1e-7, "total_observations": 120,
                "total_observations_recent": recent, "exoclock_observations_recent": recent, "current_oc_min": 0.0,
                "min_telescope_inches": 6}
    d = {"WASP-12b": planet("WASP-12b", "high", "06:30:32.79", "+29:40:20.2", 11.57, 14.0, 3.0, 2457607.5193,
                            1.0914189, 3),
         "55Cnce": planet("55Cnce", "high", "08:52:35.8113", "+28:19:50.956", 5.96, 0.45, 1.56, 2459554.20747,
                          0.73654635, 0),
         "HAT-P-23b": planet("HAT-P-23b", "low", "20:24:29.72", "+16:45:43.8", 11.94, 13.0, 2.18, 2457742.57379,
                             1.212886397, 5),
         "TOI-2046b": planet("TOI-2046b", "medium", "01:04:44.38", "+74:19:52.7", 11.55, 17.0, 2.41, 2457792.2767,
                             1.4971842, 1)}
    d.update(extra or {})
    return json.dumps(d).encode()


TOI_HEADER = ("TIC ID,TOI,Previous CTOI,Master,SG1A,SG1B,SG2,SG3,SG4,SG5,ESM,TSM,Predicted Mass (M_Earth),"
              "Predicted RV Semi-amplitude (m/s),Time Series Observations,Spectroscopy Observations,"
              "Imaging Observations,TESS Disposition,TFOPWG Disposition,TESS Mag,TESS Mag err,Planet Name,"
              "Pipeline Signal ID,Source,Detection,RA,Dec,PM RA (mas/yr),PM RA err (mas/yr),PM Dec (mas/yr),"
              "PM Dec err (mas/yr),Epoch (BJD),Epoch (BJD) err,Period (days),Period (days) err,Duration (hours),"
              "Duration (hours) err,Depth (mmag),Depth (mmag) err,Depth (ppm),Depth (ppm) err,"
              "Planet Radius (R_Earth),Sectors,Date TOI Alerted (UTC),Date TOI Updated (UTC),Date Modified,Comments")


def toi_row(toi, sg1b, disp, tmag, pname, ra, dec, t0, per, hours, ppm) -> str:
    cols = {"TOI": toi, "SG1A": "4", "SG1B": sg1b, "TESS Disposition": "PC", "TFOPWG Disposition": disp,
            "TESS Mag": tmag, "Planet Name": pname, "RA": ra, "Dec": dec, "Epoch (BJD)": t0,
            "Epoch (BJD) err": "0.0004", "Period (days)": per, "Period (days) err": "0.000002",
            "Duration (hours)": hours, "Depth (ppm)": ppm}
    return ",".join(cols.get(h, "") for h in TOI_HEADER.split(","))


def toi_csv(rows=None) -> bytes:
    rows = rows if rows is not None else [
        # the real TOI-139.01 sample (CP, SG1B 1; too far south for the golden site)
        toi_row("139.01", "1", "CP", "9.3636", "", "22:25:36.58", "-34:54:34.97", "2458334.891563",
                "11.0708575296637", "2.19376165846134", "1149.7328034023"),
        # a candidate with HAT-P-16 b's values (the test catalog drops HAT-P-16 b): a TOI not in the Archive
        toi_row("9999.01", "2", "PC", "10.41", "", "00:38:17.57", "+42:27:47.1", "2455027.60356", "2.77596727",
                "3.0624", "13613"),
        # a false positive at SG1B 1: left out
        toi_row("9998.01", "1", "FP", "10.0", "", "01:00:00", "+40:00:00", "2459000.5", "2.0", "2.0", "9000"),
        # a confirmed planet named in the Archive's spelling: matched by name, SG1B 3
        toi_row("1000.01", "3", "KP", "11.0", "WASP-12 b", "06:30:32.79", "+29:40:20.2", "2457607.5193",
                "1.0914189", "3.0", "15000"),
    ]
    return (TOI_HEADER + "\n" + "\n".join(rows) + "\n").encode()


def catalog_without_hat_p_16():
    return [p for p in small_catalog() if p["name"] != "HAT-P-16 b"]


class TestParse(unittest.TestCase):
    def test_angles(self):
        self.assertAlmostEqual(T.parse_angle("08:52:35.8113", hours=True), 133.149214, places=5)
        self.assertAlmostEqual(T.parse_angle("+28:19:50.956"), 28.330821, places=5)
        self.assertAlmostEqual(T.parse_angle("-34:54:34.97"), -34.909714, places=5)
        self.assertAlmostEqual(T.parse_angle("-00 30 00"), -0.5)
        self.assertAlmostEqual(T.parse_angle("133.1492"), 133.1492)
        self.assertAlmostEqual(T.parse_angle(28.33), 28.33)
        self.assertIsNone(T.parse_angle(""))
        self.assertIsNone(T.parse_angle("n/a x"))

    def test_keys(self):
        self.assertEqual(T.prio_key("WASP-12b"), T.prio_key("WASP-12 b"))
        self.assertEqual(T.prio_key("WASP12b"), "wasp12b")
        self.assertEqual(T.prio_key("55Cnce"), T.prio_key("55 Cnc e"))
        self.assertEqual(T.prio_key("TOI-150.01"), T.prio_key("TOI-" + "150.01"))

    def test_exoclock(self):
        rows = T.parse_exoclock_json(exoclock_json().decode())
        w = next(r for r in rows if r["name"] == "WASP-12b")
        self.assertEqual((w["priority"], w["recent"]), ("high", 3.0))
        self.assertAlmostEqual(w["ra"], 97.63663, places=4)
        self.assertAlmostEqual(w["t14"], 3.0 / 24)
        self.assertAlmostEqual(w["t0_err"], 0.0003)
        self.assertAlmostEqual(w["depth"], 1 - 10 ** (-0.4 * 0.014), places=9)
        with self.assertRaises(T.PriorityFormatError):
            T.parse_exoclock_json("[not json")
        with self.assertRaises(T.PriorityFormatError):
            T.parse_exoclock_json(json.dumps({"a": {"name": "X b", "priority": "urgent"}}))
        with self.assertRaises(T.PriorityFormatError):
            T.parse_exoclock_json(json.dumps({"planets": 3}))

    def test_exofop(self):
        rows = T.parse_exofop_csv(toi_csv().decode())
        self.assertEqual([r["name"] for r in rows], ["TOI-139.01", "TOI-9999.01", "TOI-1000.01"])   # FP left out
        r = rows[0]
        self.assertEqual((r["sg1"], r["disp"]), (1, "CP"))
        self.assertAlmostEqual(r["ra"], 336.402417, places=5)
        self.assertAlmostEqual(r["dec"], -34.909714, places=5)
        self.assertAlmostEqual(r["vmag"], 9.3636 + T.TESS_TO_V)
        self.assertAlmostEqual(r["depth"], 1149.7328034023e-6)
        self.assertAlmostEqual(r["t14"], 2.19376165846134 / 24)
        with self.assertRaises(T.PriorityFormatError):
            T.parse_exofop_csv("a,b,c\n1,2,3\n")
        with self.assertRaises(T.PriorityFormatError):
            T.parse_exofop_csv(TOI_HEADER + "\n")


class TestLoad(Base):
    def test_weekly_cache_and_saved_copy(self):
        r = self.route([(["exoclock.space"], exoclock_json())])
        rows, note, warn = T.load_exoclock_priorities()
        self.assertEqual((len(rows), note, warn), (4, "downloaded today", False))
        self.assertEqual(r.timeouts, [T.PRIORITY_TIMEOUT_S])
        r = self.route([])
        rows, note, warn = T.load_exoclock_priorities()          # cached: no call
        self.assertEqual((len(rows), r.calls, warn), (4, [], False))
        self.route([(["exoclock.space"], DOWN)])
        rows, note, warn = T.load_exoclock_priorities(refresh=True)
        self.assertEqual(len(rows), 4)
        self.assertTrue(warn)
        self.assertIn("ExoClock did not answer (service unavailable, HTTP 503); using the saved copy from", note)

    def test_soft_failures(self):
        self.route([(["exoclock.space"], DOWN)])
        rows, note, warn = T.load_exoclock_priorities()
        self.assertIsNone(rows)
        self.assertTrue(warn)
        self.assertEqual(note, "ExoClock did not answer (service unavailable, HTTP 503) — searched without its "
                               "priorities")
        self.route([(["exofop"], T.FetchError("refused the request, HTTP 403", 403))])
        rows, note, _w = T.load_exofop_priorities()
        self.assertIsNone(rows)
        self.assertIn("ExoFOP's list now needs a login (HTTP 403)", note)
        self.route([(["exofop"], b"<!DOCTYPE html><html><body><form>Log in to ExoFOP</form></body></html>")])
        rows, note, _w = T.load_exofop_priorities()
        self.assertIsNone(rows)
        self.assertIn("ExoFOP's list now needs a login", note)

    def test_parse_error_is_not_hidden(self):
        self.route([(["exoclock.space"], b'{"planets": 3}')])
        with self.assertRaises(T.PriorityFormatError):
            T.load_exoclock_priorities()
        self.assertEqual(T.cache_read("ExoClock", "exoclock:priorities:planets"), (None, None), "not saved")
        self.route([(["exofop"], b"TIC ID,Something\n1,2\n")])
        with self.assertRaises(T.PriorityFormatError):
            T.load_exofop_priorities()


class TestApply(Base):
    def setUp(self):
        super().setUp()
        self.route([(["exoclock.space"], exoclock_json()), (["exofop"], toi_csv())])
        self._precise = T.precise_planet
        self.precise_calls = []

        def precise(name, when_jd=None):
            self.precise_calls.append(name)
            return None
        T.precise_planet = precise

    def tearDown(self):
        T.precise_planet = self._precise
        super().tearDown()

    def test_none_chosen_is_1_2_0(self):
        cat = small_catalog()
        out, summary, warns = T.apply_priorities(cat, {})
        self.assertIs(out, cat)
        self.assertEqual((summary, warns), ("", []))
        out, _s, _w = T.apply_priorities(cat, {"suggest_exoclock_levels": [], "suggest_exofop_max": None})
        self.assertIs(out, cat)

    def test_exoclock_high(self):
        cat = small_catalog()
        opts = {"suggest_exoclock_levels": ["high"]}
        out, summary, warns = T.apply_priorities(cat, opts)
        self.assertEqual(sorted(p["name"] for p in out), ["55 Cnc e", "WASP-12 b"])
        self.assertEqual(warns, [])
        self.assertIn("ExoClock High (2 planets", summary)
        self.assertIn("either list: 2 planets searched", summary)
        res = T.suggest_transits(SITE, OPTICS, dict(settings.DEFAULT_OPTIONS, **opts), NIGHTS, catalog=out)
        self.assertEqual([t.name for t in res], ["WASP-12 b"])       # 55 Cnc e: V 5.95, outside Setup's V range
        w = res[0]
        self.assertIn("; ExoClock: HIGH priority (3 recent obs)", w.notes)
        self.assertEqual(w.suggest_info["priority"], "ExoClock high")
        self.assertEqual((w.source, w.planet["source"]), ("NEA", "NEA"))

    def test_exofop_toi_not_in_archive(self):
        cat = catalog_without_hat_p_16()
        opts = dict(settings.DEFAULT_OPTIONS, suggest_exofop_max=2)
        out, summary, _w = T.apply_priorities(cat, opts)
        self.assertEqual(sorted(p["name"] for p in out), ["TOI-139.01", "TOI-9999.01"])   # SG1B 3 and FP not
        self.assertIn("ExoFOP SG1 1–2 (2 TOIs", summary)
        self.assertIn("2 of them not in the Archive's list", summary)
        res = T.suggest_transits(SITE, OPTICS, opts, NIGHTS, catalog=out, refine=True)
        self.assertEqual([t.name for t in res], ["TOI-9999.01"])
        t = res[0]
        self.assertEqual((t.source, t.planet["source"], t.planet["reference"]),
                         ("ExoFOP", "ExoFOP", "ExoFOP-TESS TOI table"))
        self.assertAlmostEqual(t.vmag, 10.91)
        self.assertIn("ExoFOP SG1 priority 2", t.notes)
        self.assertIn("V 10.9 (est. from TESS mag, Sun-like star)", t.notes)
        self.assertNotIn("TOI-9999.01", self.precise_calls, "ExoFOP's ephemeris is kept")
        self.assertAlmostEqual(t.planet["depth"], 0.013613)

    def test_either_list_and_name_match(self):
        opts = {"suggest_exoclock_levels": ["low"], "suggest_exofop_max": 3}
        out, summary, _w = T.apply_priorities(small_catalog(), opts)
        names = {p["name"]: p for p in out}
        # HAT-P-23 b from ExoClock Low; WASP-12 b from ExoFOP's "Planet Name"; TOI-139.01 and TOI-9999.01 new
        # (HAT-P-16 b is in this catalog: TOI-9999.01 matches it by position and period)
        self.assertEqual(sorted(names), ["HAT-P-16 b", "HAT-P-23 b", "TOI-139.01", "WASP-12 b"])
        self.assertEqual(names["WASP-12 b"]["priority_text"], "ExoFOP SG1 priority 3")
        self.assertEqual(names["HAT-P-16 b"]["priority_short"], "SG1 2")
        self.assertEqual(names["HAT-P-23 b"]["priority_text"], "ExoClock: LOW priority (5 recent obs)")
        self.assertIn("ExoClock Low", summary)
        self.assertIn("ExoFOP SG1 1–3", summary)

    def test_toi_on_both_lists_once(self):
        extra = {"TOI-150.01": {"name": "TOI-150.01", "priority": "alert", "ra_j2000": "00:38:17.57",
                                "dec_j2000": "+42:27:47.1", "v_mag": 10.9, "depth_r_mmag": 15.0,
                                "duration_hours": 3.06, "ephem_mid_time": 2455027.6036, "ephem_period": 2.775967,
                                "total_observations_recent": 0}}
        self.route([(["exoclock.space"], exoclock_json(extra)), (["exofop"], toi_csv())])
        opts = {"suggest_exoclock_levels": ["alert"], "suggest_exofop_max": 2}
        out, _s, _w = T.apply_priorities(catalog_without_hat_p_16(), opts)
        hits = [p for p in out if p["name"] in ("TOI-150.01", "TOI-9999.01")]
        self.assertEqual(len(hits), 1, "one planet on both lists is searched once")
        self.assertEqual(hits[0]["source"], "ExoFOP")
        self.assertEqual(hits[0]["priority_short"], "ExoClock alert, SG1 2")

    def test_soft_fail_and_format_error(self):
        self.route([(["exoclock.space"], DOWN), (["exofop"], toi_csv())])
        opts = {"suggest_exoclock_levels": ["high"], "suggest_exofop_max": 1}
        out, summary, warns = T.apply_priorities(small_catalog(), opts)
        self.assertEqual([p["name"] for p in out], ["TOI-139.01"])
        self.assertEqual(len(warns), 1)
        self.assertIn("ExoClock did not answer", warns[0])
        self.assertNotIn("ExoClock", summary)
        # every chosen list down: the whole catalog, as without priorities
        self.route([(["exoclock.space"], DOWN)])
        cat = small_catalog()
        out, summary, warns = T.apply_priorities(cat, {"suggest_exoclock_levels": ["high"]})
        self.assertIs(out, cat)
        self.assertEqual(summary, "")
        self.assertEqual(len(warns), 1)
        self.route([(["exoclock.space"], b"[1, 2]")])
        with self.assertRaises(T.PriorityFormatError):
            T.apply_priorities(cat, {"suggest_exoclock_levels": ["high"]})

    def test_choice_cleaning(self):
        self.assertEqual(T.priority_choice({"suggest_exoclock_levels": ["High", "x", "alert"],
                                            "suggest_exofop_max": 9}), (["alert", "high"], None))
        self.assertEqual(T.priority_choice({"suggest_exoclock_levels": "high", "suggest_exofop_max": "2"}), ([], 2))


class TestCredits(unittest.TestCase):
    def test_both_credited(self):
        text = credits.credits_text()
        self.assertIn("ExoClock", text)
        self.assertIn("Kokori", text)
        self.assertIn("ExoFOP", text)
        self.assertIn("10.26134/ExoFOP5", text)


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestDialog(AppBase):
    def test_priority_rows_saved_and_used(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        dlg = page.ask_transit_search()
        self.assertEqual(dlg.priority_values(), ([], None), "none chosen by default (as 1.2.0)")
        dlg.exoclock_vars["high"].set(True)
        dlg.exoclock_vars["alert"].set(True)
        dlg.exofop_var.set("1–3")
        seen = {}
        tg = self.fakes["targets"]

        def apply_priorities(catalog, options, refresh=False):
            seen["opts"] = (options.get("suggest_exoclock_levels"), options.get("suggest_exofop_max"))
            return catalog, "priorities from ExoClock Alert/High (12 planets) and ExoFOP SG1 1–3 (40 TOIs), " \
                            "either list: 52 planets searched", ["ExoFOP did not answer (HTTP 503) — searched " \
                                                                 "without its priorities"]
        tg.apply_priorities = apply_priorities
        self.assertTrue(dlg.search())
        self.pump()
        o = settings.load_settings()["options"]
        self.assertEqual((o["suggest_exoclock_levels"], o["suggest_exofop_max"]), (["alert", "high"], 3))
        self.assertEqual(seen["opts"], (["alert", "high"], 3))
        msg = page.find_ind.var.get()
        self.assertTrue(msg.startswith("⚠ "), msg)
        self.assertIn("Used priorities from ExoClock Alert/High (12 planets)", msg)
        self.assertIn("⚠ ExoFOP did not answer", msg)
        # the choices come back next time; "Don't use" clears ExoFOP
        dlg = page.ask_transit_search()
        self.assertEqual(dlg.priority_values(), (["alert", "high"], 3))
        dlg.exofop_var.set("Don't use")
        for v in dlg.exoclock_vars.values():
            v.set(False)
        self.assertTrue(dlg.search())
        self.pump()
        o = settings.load_settings()["options"]
        self.assertEqual((o["suggest_exoclock_levels"], o["suggest_exofop_max"]), ([], None))

    def test_priority_shown_in_list(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        t = Target(name="TOI-9999.01", kind="transit", mode="T", ra=9.57, dec=42.46, vmag=10.9, source="ExoFOP",
                   suggested=True, enabled=False,
                   planet={"name": "TOI-9999.01", "period": 2.776, "t0": 2455027.6, "t14": 0.13, "depth": 0.0136})
        t.suggest_info = {"priority": "ExoClock high, SG1 2", "when": 2461337.6}
        app.targets.append(t)
        page.fill()
        self.assertIn("suggested · ExoFOP · ExoClock high, SG1 2", page.tables["transit"].tree.set(t.id, "source"))


if __name__ == "__main__":
    unittest.main()
