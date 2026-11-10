"""SHOBS-SCHED 1.3.0 (the 1.2.0 manual review, 2 Nov 2026): the DOI, the Archive's host position when SIMBAD fails,
the hand-set block question, the flip rule's edge cases, an empty "Tracks past the meridian", one Move refusal,
the Mac/Linux reinstall hint, the real version in the User-Agent, the Moon in the dark, the (flip)/(est.) marks and
the green busy bar."""
from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credits  # noqa: E402
import export  # noqa: E402
import nightplot  # noqa: E402
import planner  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
import weather  # noqa: E402
from models import Block, NightPlan, Optics, Site, Target, serious_caution  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = Site(name="Golden site", lat=35.303, lon=-81.941, tz="America/New_York", min_alt=30.0,
            past_meridian_min=5.0)
OPTICS = Optics(aperture_mm=356, focal_mm=2900, pixel_um=3.76, color_camera=True, e_per_adu=0.243,
                linear_limit_adu=32000)
M = 1.0 / 1440.0


def ra_on_meridian(site, jd):
    return float(sky.lst_deg(jd, site.lon)) % 360.0


class TestVersionAndDoi(unittest.TestCase):
    def test_version_and_user_agents(self):
        self.assertEqual(settings.APP_VERSION, "1.4.1")
        self.assertEqual(T.USER_AGENT, f"SHOBS-SCHED/{settings.APP_VERSION}")
        self.assertEqual(weather.USER_AGENT, f"SHOBS-SCHED/{settings.APP_VERSION}")
        with open(os.path.join(HERE, "selftest.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("SHOBS-SCHED/1.0", src)
        self.assertIn("SHOBS-SCHED/{settings.APP_VERSION} selftest", src)

    def test_citation(self):
        self.assertEqual(credits.CITATION_DOI, "10.5281/zenodo.23674791")
        cite = credits.citation_text("1.3.0")
        self.assertEqual(cite, "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-SCHED: Shiloh Hill Observatory "
                               "Photometry Scheduler, version 1.3.0, Zenodo, doi:10.5281/zenodo.23674791")
        with open(os.path.join(HERE, "CITATION.cff"), encoding="utf-8") as fh:
            cff = fh.read()
        for s in ('title: "SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler"', "version: 1.4.1",
                  "doi: 10.5281/zenodo.23674791", "family-names: Armbrust", "given-names: John William"):
            self.assertIn(s, cff)
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("(https://zenodo.org/badge/DOI/10.5281/zenodo.23674791.svg)]"
                      "(https://doi.org/10.5281/zenodo.23674791)", readme)
        long = [n for n, line in enumerate(readme.splitlines(), 1) if len(line) > 120]
        self.assertEqual(long, [], "README lines over 120 characters")


class TestArchiveFallback(unittest.TestCase):
    """The Archive's host position when SIMBAD fails: said, and recorded as the source."""

    def setUp(self):
        self._get = T.http_get
        self._home = os.environ.get("SHOBS_SCHED_HOME")
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="sched_r6_")
        os.environ["SHOBS_SCHED_HOME"] = self.tmp

    def tearDown(self):
        T.http_get = self._get
        if self._home is None:
            os.environ.pop("SHOBS_SCHED_HOME", None)
        else:
            os.environ["SHOBS_SCHED_HOME"] = self._home
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_neither_knows_the_name(self):
        def get(url, timeout=None):
            if "simbad" in url:
                return b"::error:::::\n\n[3] Identifier not found in the database : Zork 1\n"
            if "exoplanetarchive" in url:
                return (T.NEA_COLUMNS + "\n").encode()
            raise T.FetchError("offline")
        T.http_get = get
        with self.assertRaises(T.NotFound) as cm:
            T.resolve_star("Zork 1")
        self.assertIn("Neither SIMBAD nor the NASA Exoplanet Archive knows Zork 1", str(cm.exception))

    def test_needs_mode_carries_the_archive_note(self):
        exc = T.NeedsMode("V1298 Tau", 61.33, 20.16, 10.1, "V1298 Tau", "Star (planet host)", via="NEA",
                          note="position from the NASA Exoplanet Archive (SIMBAD did not answer)")
        self.assertIn("found in the NASA Exoplanet Archive", exc.message)
        self.assertEqual(exc.source, "SIMBAD", "source naming the Archive means a non-transiting planet to the UI")
        self.assertEqual(exc.via, "NEA")
        plain = T.NeedsMode("HD 1", 1.0, 2.0, 8.0, "HD 1")
        self.assertEqual((plain.via, plain.note), ("", ""))
        self.assertIn("found in SIMBAD", plain.message)


class TestFlipRule(unittest.TestCase):
    """Item 4: the no-flip spot needs a FULL block; the flip-at-start note survives every retry round."""

    def test_short_room_before_the_meridian_flips_at_the_start(self):
        # a locked block 25–15 min before the meridian leaves only a short room before it: a no-flip run there
        # would be cut short, so the block flips at its start and runs whole (min(want, longest) let the short
        # room count as "a full block fits")
        d = date(2026, 10, 29)
        w = sky.night_window(d, SITE)
        mer = w[0] + 150 * M
        lo, hi = sky.to_local(w[0], SITE.tz), sky.to_local(mer + 80 * M, SITE.tz)
        t = Target(id="A", name="A", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mer), dec=57.0, vmag=6.0,
                   block_min=120, min_block_min=45, exposure_s=1.0, enabled=True,
                   rules=[{"kind": "window", "value": f"{lo:%H:%M}-{hi:%H:%M}"}])
        lk = Block(target_id="L", name="L", mode="V", kind="campaign", start=mer - 25 * M, end=mer - 15 * M,
                   locked=True)
        n = planner._Night(d, SITE, OPTICS, [t], {}, [lk], None, [], set())
        n.build()
        p = next(q for q in n.placed if q.tid == "A")
        self.assertFalse(p.noflip_ideal, "no full block fits before the meridian: not the no-flip ideal")
        b = next(x for x in n.plan.blocks_obj() if x.target_id == "A")
        self.assertTrue(b.start < b.meridian < b.end, "flip at the start")
        self.assertGreater(b.minutes(), 80)

    def test_full_block_before_the_meridian_still_runs_there(self):
        d = date(2026, 10, 29)
        w = sky.night_window(d, SITE)
        mer = w[0] + 150 * M
        t = Target(id="A", name="A", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mer), dec=57.0, vmag=6.0,
                   block_min=120, min_block_min=45, exposure_s=1.0, enabled=True)
        n = planner._Night(d, SITE, OPTICS, [t], {}, [], None, [], set())
        n.build()
        p = n.placed[0]
        self.assertTrue(p.noflip_ideal)
        self.assertLessEqual((p.end - mer) * 1440.0, 5.5)
        self.assertGreaterEqual((p.end - p.start) * 1440.0, 119)

    def test_note_kept_across_rounds(self):
        # two rounds: the note of the first round's target must survive the second round's new plan
        real = planner._Night
        rounds = {"n": 0}

        class Fake:
            def __init__(self, d, site, optics, targets, options, locked, weather, history, prefer):
                self.flip_at_start = set()
                self.placed = []

            def build(self):
                rounds["n"] += 1
                fas = self.flip_at_start
                ids = ["A", "B"] + (["C"] if "A" in fas else []) + (["D"] if {"A", "B"} <= fas else [])
                left = [x for x in ("C", "D") if x not in ids]
                plan = NightPlan(date="2026-11-03")
                plan.blocks = [Block(target_id=i, name=i, mode="D", kind="campaign", start=1.0 + k,
                                     end=1.5 + k).to_dict() for k, i in enumerate(ids)]
                plan.didnt_fit = [{"target_id": x, "name": x, "reason": "no room"} for x in left]

                class P:
                    def __init__(self, tid, noflip):
                        self.tid, self.kind, self.noflip_ideal = tid, "flex", noflip
                self.placed = [P(i, i in ("A", "B") and i not in fas) for i in ids]
                return plan
        planner._Night = Fake
        try:
            plan = planner.build_night(date(2026, 11, 3), SITE, OPTICS, [], {})
        finally:
            planner._Night = real
        by = {b["target_id"]: b for b in plan.blocks}
        self.assertEqual(set(by), {"A", "B", "C", "D"})
        self.assertIn(planner.FLIP_AT_START_NOTE, by["A"].get("notes", []), "first round's note kept")
        self.assertIn(planner.FLIP_AT_START_NOTE, by["B"].get("notes", []))
        self.assertNotIn(planner.FLIP_AT_START_NOTE, by["C"].get("notes", []))

    def test_unticked_kept_block_says_so(self):
        d = date(2026, 10, 29)
        w = sky.night_window(d, SITE)
        mer = w[0] + 150 * M
        t = Target(id="A", name="A", kind="campaign", mode="D", ra=ra_on_meridian(SITE, mer), dec=57.0, vmag=6.0,
                   block_min=60, exposure_s=1.0, enabled=False)
        lk = Block(target_id="A", name="A", mode="D", kind="campaign", start=mer - 60 * M, end=mer, set_by_user=True)
        gone = Block(target_id="Z", name="Z", mode="D", kind="campaign", start=mer + 60 * M, end=mer + 90 * M,
                     locked=True)
        plan = planner.build_night(d, SITE, OPTICS, [t], {}, locked=[lk, gone])
        by = {b.target_id: b for b in plan.blocks_obj()}
        self.assertIn("⚠ This target is not ticked to plan", by["A"].cautions)
        self.assertNotIn("⚠ This target is no longer in your list", by["A"].cautions)
        self.assertIn("⚠ This target is no longer in your list", by["Z"].cautions)
        self.assertFalse(serious_caution("⚠ This target is not ticked to plan"), "information only")
        self.assertEqual(len(plan.blocks), 2, "the unticked target is not planned again")


class TestWordsAndMarks(unittest.TestCase):
    def test_reinstall_hint_per_platform(self):
        self.assertEqual(nightplot.reinstall_hint("win32"), "run Create shortcuts.bat again")
        self.assertIn("Start SHOBS-SCHED.command", nightplot.reinstall_hint("darwin"))
        self.assertIn("start_linux.sh", nightplot.reinstall_hint("linux"))
        for p in ("darwin", "linux"):
            self.assertNotIn(".bat", nightplot.reinstall_hint(p))
        self.assertEqual(nightplot.MISSING_TEXT, "⚠ Night plot needs matplotlib — " + nightplot.reinstall_hint())
        if not sys.platform.startswith("win"):
            self.assertNotIn(".bat", nightplot.MISSING_TEXT + nightplot.TK_MISSING_TEXT)

    def test_moon_rises_and_sets_in_the_dark(self):
        dusk, dawn = 2461340.5, 2461340.9
        txt = export.moon_events_text(dusk, dawn, dusk + 0.1, dusk + 0.3, "UTC")
        self.assertTrue(txt.startswith("down at dusk, rises "), txt)
        self.assertIn(", sets ", txt)
        self.assertLess(txt.index("rises"), txt.index("sets"))
        txt = export.moon_events_text(dusk, dawn, dusk + 0.3, dusk + 0.1, "UTC")
        self.assertTrue(txt.startswith("up at dusk, sets "), txt)
        self.assertLess(txt.index("sets"), txt.index("rises"))

    def test_command_files_name_the_real_logs(self):
        with open(os.path.join(HERE, "Run self-test.command"), encoding="utf-8") as fh:
            st = fh.read()
        self.assertIn("selftest_log.txt in this folder", st)
        self.assertIn("SHOBS-SCHED_selftest_log.txt", st)
        with open(os.path.join(HERE, "selftest.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"selftest_log.txt"', src)
        self.assertIn('"SHOBS-SCHED_selftest_log.txt"', src)
        with open(os.path.join(HERE, "Start SHOBS-SCHED.command"), encoding="utf-8") as fh:
            start = fh.read()
        self.assertIn("Library/Application Support/SHOBS-SCHED/pip_log.txt", start)
        with open(os.path.join(HERE, "shobs_setup.sh"), encoding="utf-8") as fh:
            self.assertIn('log="$HOME2/pip_log.txt"', fh.read())

    def test_busy_bar_green(self):
        import style
        self.assertEqual(style.BUSY_GREEN, "#4caf50")


@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class TestApp(Base):
    def build(self, start=date(2026, 10, 20), n=2):
        page = self.app.pages[2]
        self.app.show_page(2)
        page.build(start, n)
        self.pump()
        return page

    def test_busy_bar_style(self):
        self.golden_home()
        app = self.open()
        from tkinter import ttk
        st = ttk.Style(app)
        for opt in ("background", "lightcolor", "darkcolor"):
            self.assertEqual(str(st.lookup("Status.Horizontal.TProgressbar", opt)).lower(), "#4caf50", opt)
        self.assertEqual(str(app.busy_bar.bar.cget("style")), "Status.Horizontal.TProgressbar")

    def test_empty_tracks_past_meridian_keeps_the_value(self):
        self.golden_home()
        app = self.open()
        setup = app.pages[0]
        app.show_page(0)
        v = setup.vars
        v["site.past_meridian_min"].set("12")
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertEqual(app.site.past_meridian_min, 12.0)
        v["site.past_meridian_min"].set("")
        self.assertTrue(setup.save(), setup.msg_var.get())
        self.assertEqual(app.site.past_meridian_min, 12.0, "an empty field keeps the saved value, never 0")
        v["site.past_meridian_min"].set("lots")
        self.assertFalse(setup.save())
        self.assertIn("Tracks past the meridian", setup.msg_var.get())
        self.assertNotIn("Tracking past", setup.msg_var.get())

    def test_plan_nights_falls_back_to_one(self):
        self.golden_home()
        app = self.open()
        app.options["nights"] = "junk"
        self.assertEqual(len(app.plan_nights()), 1)
        app.options.pop("nights", None)
        self.assertEqual(len(app.plan_nights()), 1)
        app.options["nights"] = 3
        self.assertEqual(len(app.plan_nights()), 3)

    def test_hand_set_question_matches_the_buttons(self):
        self.golden_home()
        app = self.open()
        asked = []
        real = self.sa.ask_yes_no

        def ask(a, title, message, yes_text="Continue"):
            asked.append((title, message, yes_text))
            return False
        self.sa.ask_yes_no = ask
        self.addCleanup(setattr, self.sa, "ask_yes_no", real)
        t = app.targets[0]
        tonight = app.tonight().isoformat()
        app.schedule.nights = [{"date": tonight, "blocks": [
            {"target_id": t.id, "name": t.name, "start": 1.0, "end": 1.1, "set_by_user": True, "status": "planned"}]}]
        app.ask_drop_hand_set(app.hand_set_blocks({t.id}), "A rebuild keeps blocks set by hand.")
        title, msg, yes = asked[-1]
        self.assertEqual((title, yes), ("Blocks set by hand", "Remove"))
        self.assertIn("Cancel keeps it in the schedule", msg)
        self.assertNotIn("No keeps", msg)
        self.assertNotIn("marked ⚠", msg)

    def test_rereview_asks_the_hand_set_question(self):
        self.golden_home()
        app = self.open()
        page = self.build(start=app.tonight(), n=2)
        t = next(x for x in app.targets if x.enabled)
        nd = page.night(0)
        bl = nd.blocks_obj()
        k = next(i for i, b in enumerate(bl) if b.target_id == t.id)
        bl[k].set_by_user = True
        nd.blocks = [x.to_dict() for x in bl]
        page.set_night(0, nd)
        t.enabled = False                                   # unticked behind the page's back (no question yet)
        asked = []
        real = self.sa.ask_yes_no

        def ask(a, title, message, yes_text="Continue"):
            asked.append((title, message))
            return title == "Blocks set by hand"            # Remove
        self.sa.ask_yes_no = ask
        self.addCleanup(setattr, self.sa, "ask_yes_no", real)
        page.rereview()
        self.pump()
        hs = [m for ti, m in asked if ti == "Blocks set by hand"]
        self.assertEqual(len(hs), 1, asked)
        self.assertIn("Its target is not ticked to plan.", hs[0])
        self.assertFalse(any(b.get("target_id") == t.id for n in app.schedule.nights for b in n["blocks"]))

    def test_build_question_says_removed(self):
        self.golden_home()
        app = self.open()
        tonight = app.tonight().isoformat()
        app.schedule.nights = [{"date": tonight, "blocks": [
            {"target_id": "gone", "name": "Gone", "start": 1.0, "end": 1.1, "locked": True, "status": "planned"}]}]
        asked = []
        real = self.sa.ask_yes_no

        def ask(a, title, message, yes_text="Continue"):
            asked.append((title, message))
            return False
        self.sa.ask_yes_no = ask
        self.addCleanup(setattr, self.sa, "ask_yes_no", real)
        app.ask_stale_hand_set(app.tonight())
        self.assertIn("Its target is no longer in your list.", asked[-1][1])

    def test_one_move_refusal(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        tab = page.tabs[0]
        nd = page.night(0)
        bl = nd.blocks_obj()
        bl[0].fixed = True
        nd.blocks = [x.to_dict() for x in bl]
        page.set_night(0, nd)
        j = next(k for k, b in tab.blocks() if b.fixed)
        tab.select_block(j)
        tab.move_selected()
        a = tab.act_var.get()
        self.assertFalse(tab.move_block(j, 1))
        self.assertEqual(tab.act_var.get(), a)
        self.assertEqual(a, self.sa.fixed_move_refusal(bl[0].name))

    def test_flip_and_est_marks_in_the_table(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        tab = page.tabs[0]
        nd = page.night(0)
        bl = nd.blocks_obj()
        bl[0].flip_in_block = True
        bl[0].meridian = (bl[0].start + bl[0].end) / 2
        bl[0].exposure_s, bl[0].exposure_is_estimate = 30.0, True
        bl[1].flip_in_block, bl[1].exposure_is_estimate = False, False
        nd.blocks = [x.to_dict() for x in bl]
        page.set_night(0, nd)
        rows = {b.target_id: r for _i, b, r in tab.table_rows(page.night(0))}
        self.assertTrue(rows[bl[0].target_id]["meridian"].endswith(" (flip)"), rows[bl[0].target_id])
        self.assertTrue(rows[bl[0].target_id]["exposure"].endswith(" (est.)"), rows[bl[0].target_id])
        self.assertNotIn("(flip)", rows[bl[1].target_id]["meridian"])
        self.assertNotIn("(est.)", rows[bl[1].target_id]["exposure"])
        # and the block's own row (export.py missing) carries them too
        own = self.sa.own_row(bl[0], app.site, app.targets_by_id())
        self.assertTrue(own["meridian"].endswith(" (flip)"))
        self.assertTrue(own["exposure"].endswith(" (est.)"))

    def test_add_star_records_the_archive(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        pos = {"ra": 61.33, "dec": 20.16, "vmag": 10.1, "main_id": "V1298 Tau", "via": "NEA",
               "note": "position from the NASA Exoplanet Archive (SIMBAD did not answer)"}
        page.add_star("V1298 Tau", pos, "What is V1298 Tau for you?", [], pick=self.sa.KIND_CHOICES[0][0])
        t = next(x for x in app.targets if x.name == "V1298 Tau")
        self.assertEqual(t.source, "NASA Exoplanet Archive")
        msg = page.add_ind.var.get()
        self.assertTrue(msg.startswith("⚠ Added V1298 Tau (NASA Exoplanet Archive). Position from the NASA "
                                       "Exoplanet Archive (SIMBAD did not answer)."), msg)
        self.assertNotIn("..", msg)
        page.add_star("HD 1", {"ra": 1.0, "dec": 2.0, "vmag": 8.0, "main_id": "HD 1"}, "?", [],
                      pick=self.sa.KIND_CHOICES[0][0])
        self.assertEqual(next(x for x in app.targets if x.name == "HD 1").source, "SIMBAD")

    def test_star_lookup_failure_is_true(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        tg = self.fakes["targets"]
        tg.resolve_star = lambda name: {"ra": None}
        page.star_lookup("Nowhere", None, "")
        self.pump()
        msg = page.add_ind.var.get()
        self.assertIn("neither SIMBAD nor the NASA Exoplanet Archive gave one", msg)
        self.assertNotIn("SIMBAD gave none", msg)


if __name__ == "__main__":
    unittest.main()
