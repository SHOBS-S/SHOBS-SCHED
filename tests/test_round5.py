"""SHOBS-SCHED 1.2.0 (the fix list, 31 Oct 2026): Setup's Suggestions-and-darks box, a ticked suggestion is kept,
hand-set blocks of unplanned targets, SIMBAD service errors and mirrors, the Archive's host position when SIMBAD is
down, campaign events from the first planned night, staged Find progress, the night tab's ⚠ only when serious, the
Moon's night in the dark's order, one no-dark-time wording, the app's day style, and flexible blocks that avoid a
mid-run meridian flip."""
from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export  # noqa: E402
import planner  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import Block, Optics, Site, Target, serious_caution, serious_night  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402

SITE = Site(name="Golden site", lat=35.303, lon=-81.941, tz="America/New_York", min_alt=30.0,
            past_meridian_min=5.0)
OPTICS = Optics(aperture_mm=356, focal_mm=2900, pixel_um=3.76, color_camera=True, e_per_adu=0.243,
                linear_limit_adu=32000)


def ra_on_meridian(site, jd):
    return float(sky.lst_deg(jd, site.lon)) % 360.0


class TestSerious(unittest.TestCase):
    """Fix list 25: the night tab's ⚠ only for cautions that need action, or a night-wide ✖."""

    def test_rules(self):
        self.assertTrue(serious_caution("⚠ Meridian flip during the transit (22:10)"))
        self.assertTrue(serious_caution("⚠ Dew expected from 01:00: the block ends there"))
        self.assertFalse(serious_caution("⚠ Baseline trimmed (next to X)"))
        self.assertFalse(serious_caution("⚠ Ephemeris uncertain: mid-time ± 14 min"))
        self.assertFalse(serious_caution("Cloud ≥ 70 % forecast for most of this block"))
        night = {"blocks": [{"cautions": ["⚠ Baseline trimmed (next to X)"]}],
                 "didnt_fit": [{"name": "A", "reason": "no free stretch"}] * 12,
                 "notes": ["⚠ Dew expected from 03:00"]}
        self.assertFalse(serious_night(night), "didn't fit, info cautions and night-wide ⚠ are not serious")
        night["blocks"].append({"cautions": ["⚠ Counterweight up beyond HA ±6 h (03:10)"]})
        self.assertTrue(serious_night(night))
        night["blocks"][-1]["status"] = "done"
        self.assertFalse(serious_night(night), "a marked block no longer needs action")
        night["notes"].append("✖ No dark time tonight (planning darkness −18.0°)")
        self.assertTrue(serious_night(night))


class TestWords(unittest.TestCase):
    def test_day_style_and_no_dark_note(self):
        self.assertEqual(planner._fmt_day(date(2026, 11, 4)), "Wed 04 Nov")        # fix list 16
        a = planner.no_dark_note(-15.2, -18.0)                                     # fix list 19
        self.assertTrue(a.startswith("✖ No dark time tonight: the Sun gets no lower than −15.2°"), a)
        self.assertIn("nautical darkness", a)
        self.assertTrue(planner.no_dark_note(None, -18.0).startswith("✖ No dark time tonight"))

    def test_moon_in_the_dark_order(self):
        # fix list 17: an afternoon moonset never leads; the state at dusk comes first
        dusk, dawn = 2461340.5, 2461340.9
        txt = export.moon_events_text(dusk, dawn, dusk + 0.2, dusk - 0.15, "UTC")
        self.assertTrue(txt.startswith("down at dusk, rises "), txt)
        self.assertNotIn("sets", txt)
        self.assertEqual(export.moon_events_text(dusk, dawn, dawn + 0.1, dusk - 0.2, "UTC"), "down all night")
        self.assertEqual(export.moon_events_text(dusk, dawn, dusk - 0.3, dawn + 0.2, "UTC"), "up all night")
        txt = export.moon_events_text(dusk, dawn, dusk - 0.3, dusk + 0.1, "UTC")
        self.assertTrue(txt.startswith("up at dusk, sets "), txt)
        self.assertEqual(export.moon_events_text(None, None, dusk + 0.1, None, "UTC").split()[0], "rises")


class TestSimbad(unittest.TestCase):
    """Fix list 7 and 22: a SIMBAD failure is not 'unknown name'; the IDIA mirror, then the Archive's host."""

    def setUp(self):
        self._get = T.http_get
        self._cache = os.environ.get("SHOBS_SCHED_HOME")
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="sched_r5_")
        os.environ["SHOBS_SCHED_HOME"] = self.tmp

    def tearDown(self):
        T.http_get = self._get
        if self._cache is None:
            os.environ.pop("SHOBS_SCHED_HOME", None)
        else:
            os.environ["SHOBS_SCHED_HOME"] = self._cache
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_error_kinds(self):
        self.assertIsNone(T.parse_simbad_script("::error:::::\n\n[3] Identifier not found in the database : X"))
        self.assertIsNone(T.parse_simbad_script("::error:::::\n\n[3] 'Zzqx 9' this identifier has an incorrect "
                                                "format for catalog:\n\tZ : catalogue Z"))
        with self.assertRaises(T.FetchError):
            T.parse_simbad_script("::error:::::\n\n[1] GluTools: HTTP 403 for http://...\n")

    def test_mirror_then_archive(self):
        calls = []

        def get(url, timeout=None):
            calls.append(urllib.parse.urlparse(url).netloc)
            if "simbad.cds" in url:
                raise T.FetchError("no answer within 30 s")
            if "simbad.idia" in url:
                return b"HD 219134|348.32073 +57.16836|5.57|PM*|PM*\n"
            raise T.FetchError("offline")
        T.http_get = get
        s = T.resolve_star("HD 219134")
        self.assertEqual(calls[:2], ["simbad.cds.unistra.fr", "simbad.idia.ac.za"])
        self.assertAlmostEqual(s["ra"], 348.32073)

        def get2(url, timeout=None):
            if "simbad" in url:
                raise T.FetchError("service unavailable, HTTP 503", 503)
            if "exoplanetarchive" in url:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
                if "hostname = 'V1298 Tau'" in q or "pl_name = 'V1298 Tau d'" in q:
                    head = T.NEA_COLUMNS
                    row = ["V1298 Tau d", "V1298 Tau", "12.4", "", "", "", "", "", "", "61.33", "20.16", "10.1", "1",
                           "", ""]
                    return (head + "\n" + ",".join(row) + "\n").encode()
                return (T.NEA_COLUMNS + "\n").encode()
            raise T.FetchError("offline")
        T.http_get = get2
        s = T.resolve_star("V1298 Tau d")
        self.assertEqual(s["main_id"], "V1298 Tau")
        self.assertAlmostEqual(s["ra"], 61.33)
        self.assertIn("NASA Exoplanet Archive", s["note"])

    def test_vizier_has_idia(self):
        self.assertTrue(T.VIZIER_ASU[-1].startswith("https://vizier.idia.ac.za/"))
        self.assertEqual(T.SIMBAD_MIRRORS[-1], "https://simbad.idia.ac.za/simbad/sim-script?")


class TestCampaignFromFirstNight(unittest.TestCase):
    def test_blackout_before_the_first_night_is_not_shown(self):
        # fix list 8: the campaign's "next blackout" follows the Nights row, not now
        first = date(2026, 11, 2)
        w = sky.night_window(first, SITE)
        mid = 0.5 * (w[0] + w[1])
        planet = {"name": "Star b", "period": 2.0, "t0": mid - 2.0 * 400, "t14": 0.1}
        t = Target(name="Star", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mid), dec=40.0,
                   rules=[{"kind": "no_transit", "value": "Star b", "planet": planet}])
        now = w[0] - 5.0
        ev = T.next_events(t, SITE, [first, first + timedelta(days=1)], now)
        text = ev.get("text", "")
        if "next blackout" in text:
            day = text.split("next blackout ")[1][:10]
            self.assertIn(day, (T._fmt_day(w[0], SITE), T._fmt_day(w[0] + 1, SITE)), text)


class TestFlexFlip(unittest.TestCase):
    """Fix list 24: a flexible block that fits wholly before its meridian runs there (no flip); a mid-run flip
    costs heavily."""

    def test_whole_block_before_the_meridian(self):
        d = date(2026, 10, 29)
        w = sky.night_window(d, SITE)
        mer = w[0] + 3.0 / 24
        t = Target(id="C", name="C", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mer), dec=57.0, vmag=6.0,
                   block_min=120, min_block_min=45, exposure_s=1.0, enabled=True)
        b = planner.build_night(d, SITE, OPTICS, [t], {}).blocks_obj()[0]
        self.assertFalse(b.flip_in_block)
        self.assertLessEqual((b.end - b.meridian) * 1440.0, 5.5)
        self.assertGreaterEqual(b.minutes(), 119)

    def test_blackout_forces_no_midrun_flip(self):
        # John 29 Oct: a blackout right after the meridian squeezed the flip-at-start block; it ran through the
        # flip instead. Now: the whole block before the meridian.
        d = date(2026, 10, 29)
        w = sky.night_window(d, SITE)
        mer = w[0] + 2.3 / 24
        planet = {"name": "C c", "period": 1000.0, "t0": mer + 1.0 / 24 + 0.03, "t14": 0.07}
        t = Target(id="C", name="C", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mer), dec=57.0, vmag=6.0,
                   block_min=105, min_block_min=45, exposure_s=1.0, enabled=True,
                   rules=[{"kind": "no_transit", "value": "C c", "planet": planet}])
        b = planner.build_night(d, SITE, OPTICS, [t], {}).blocks_obj()[0]
        mid_flip = b.start < b.meridian < b.end and (b.end - b.meridian) * 1440.0 > 5.0 \
            and (b.meridian - b.start) * 1440.0 > 22.0
        self.assertFalse(mid_flip, (sky.hhmm(b.start, SITE.tz), sky.hhmm(b.end, SITE.tz),
                                    sky.hhmm(b.meridian, SITE.tz)))


class TestProgress(unittest.TestCase):
    def test_each_stage_counts_up_to_its_own_total(self):
        # fix list 9: one count over two passes ran backwards ("299 of 303" then "292 of 302")
        seen = []
        cat = []
        for k in range(30):
            cat.append({"name": f"P{k} b", "host": f"P{k}", "period": 1.0 + k * 0.1, "t0": 2461000.5 + k * 0.01,
                        "t14": 0.1, "depth": 0.01, "ra": 10.0 * k, "dec": 40.0, "vmag": 11.0})
        nights = [date(2026, 11, 2)]
        opts = dict(settings.DEFAULT_OPTIONS)
        opts["suggest_max"] = 3
        old = T.precise_planet
        T.precise_planet = lambda name, mid: None
        try:
            T.suggest_transits(SITE, OPTICS, opts, nights, catalog=cat, refine=True,
                               progress=lambda i, n, stage="": seen.append((stage, i, n)))
        finally:
            T.precise_planet = old
        by = {}
        for stage, i, n in seen:
            by.setdefault(stage, []).append((i, n))
        for stage, pairs in by.items():
            self.assertTrue(stage, "every report names its step")
            self.assertEqual([i for i, _n in pairs], sorted(i for i, _n in pairs), stage)
            self.assertEqual(pairs[-1][0], pairs[-1][1], f"{stage} ends at n of n")


@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class TestApp(Base):
    def test_setup_suggestions_and_darks(self):
        # fix list 1, 2, 13
        self.golden_home()
        app = self.open()
        setup = app.pages[0]
        app.show_page(0)
        v = setup.vars
        self.assertEqual(v["options.suggest_min_depth"].get(), "0.5")       # shown in percent
        v["options.suggest_vmin"].set("8")
        v["options.suggest_vmax"].set("12.5")
        v["options.suggest_min_depth"].set("0.3")
        v["options.darks_on_hand"].set("60, 120s 180")
        self.assertTrue(setup.save(), setup.msg_var.get())
        o = app.options
        self.assertEqual((o["suggest_vmin"], o["suggest_vmax"]), (8.0, 12.5))
        self.assertAlmostEqual(o["suggest_min_depth"], 0.003)
        self.assertEqual(o["darks_on_hand"], [60.0, 120.0, 180.0])
        v["options.suggest_vmin"].set("14")
        self.assertFalse(setup.save())
        self.assertIn("upside down", setup.msg_var.get())
        v["options.suggest_vmin"].set("8")
        v["options.darks_on_hand"].set("sixty")
        self.assertFalse(setup.save())
        self.assertIn("Darks you already have", setup.msg_var.get())

    def test_tick_keeps_a_suggestion(self):
        # fix list 3 and 21
        self.golden_home()
        app = self.open()
        t = Target(name="Sugg b", kind="transit", mode="T", ra=10.0, dec=20.0, suggested=True, enabled=False)
        app.targets.append(t)
        page = app.pages[1]
        app.show_page(1)
        page.toggle(t.id)
        self.assertTrue(t.enabled)
        self.assertFalse(t.suggested)
        self.assertIn("Kept", page.msg_var.get())
        page.toggle(t.id)
        self.assertFalse(t.suggested, "unticking leaves it kept: a new Find does not drop it")

    def test_untick_asks_about_hand_set_blocks(self):
        # fix list 23
        self.golden_home()
        app = self.open()
        t = app.targets[0]
        t.enabled = True
        tonight = app.tonight().isoformat()
        app.schedule.nights = [{"date": tonight, "blocks": [
            {"target_id": t.id, "name": t.name, "start": 1.0, "end": 1.1, "set_by_user": True, "status": "planned"},
            {"target_id": "other", "name": "Other", "start": 1.2, "end": 1.3, "locked": True, "status": "done"}]}]
        page = app.pages[1]
        app.show_page(1)
        app.auto_answers = [True]
        page.toggle(t.id)
        self.assertFalse(t.enabled)
        names = [b["name"] for b in app.schedule.nights[0]["blocks"]]
        self.assertEqual(names, ["Other"], "the hand-set block went; a marked one stays")
        self.assertIn("removed 1 block", page.msg_var.get())

    def test_added_message_has_no_nested_brackets(self):
        # fix list 11
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        t = Target(name="WASP-12 b", kind="transit", mode="T", ra=97.6, dec=29.67, source="NEA (most precise)")
        page.accept(t)
        self.assertEqual(page.add_ind.var.get(), "✔ Added WASP-12 b (NEA, most precise).")


if __name__ == "__main__":
    unittest.main()
