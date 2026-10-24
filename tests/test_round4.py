"""SHOBS-SCHED 1.1.0 round 4 (John's 8 items, 24 Oct 2026): one box per target type with its own scroll bar and a
divider, the Plan tick first, period and next event in the planned nights, no SHOBS-P wording outside Setup,
"Didn't fit" only on the Schedule page, a blank Status entry, Credits and sources, and the 25-row caps."""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credits  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import Block, Site, Target, block_type_code, type_name  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402

SITE = Site(name="Golden site", lat=35.303, lon=-81.941, tz="America/New_York", min_alt=30.0)


class TestNextEvents(unittest.TestCase):
    """targets.next_events: the first event in a DARK window of the given nights."""

    def nights(self, first: date, n: int):
        return [date.fromordinal(first.toordinal() + k) for k in range(n)]

    def test_transit_in_the_planned_nights_only(self):
        # a planet whose transits fall at local midnight every 3 days
        nights = self.nights(date(2026, 10, 25), 7)
        w = sky.night_window(nights[1], SITE)
        mid = (w[0] + w[1]) / 2.0
        ra = float(sky.lst_deg(mid, SITE.lon))
        t = Target(name="Test b", kind="transit", mode="T", ra=ra, dec=40.0,
                   planet={"name": "Test b", "period": 3.0, "t0": mid - 300.0, "t14": 0.08})
        start = w[0] - 2.0                          # "now" = two days before
        ev = T.next_events(t, SITE, nights, start)
        self.assertEqual(ev["night"], nights[1])
        self.assertTrue(ev["full"])
        self.assertAlmostEqual(ev["tc"], mid, delta=0.01)
        self.assertLess(ev["t1"], ev["tc"])
        self.assertLess(ev["tc"], ev["t4"])
        # one night with no transit in it
        ev = T.next_events(t, SITE, [nights[0]], start)
        self.assertEqual(ev, {"none": "none tonight"})
        ev = T.next_events(t, SITE, [nights[2], nights[3]], start)
        self.assertEqual(ev, {"none": "none in the next 2 nights"})

    def test_variable_timed_and_any_time(self):
        nights = self.nights(date(2026, 10, 25), 3)
        w = sky.night_window(nights[0], SITE)
        mid = (w[0] + w[1]) / 2.0
        ra = float(sky.lst_deg(mid, SITE.lon))
        eb = Target(name="EB", kind="variable", ra=ra, dec=40.0,
                    elements={"vtype": "EA", "period": 0.75, "epoch": mid - 0.75 * 4000, "epoch_kind": "min",
                              "any_time": False})
        ev = T.next_events(eb, SITE, nights, w[0])
        self.assertEqual((ev["night"], ev["event"]), (nights[0], "min"))
        self.assertAlmostEqual(ev["t"], mid, delta=0.01)
        mira = Target(name="Mira-ish", kind="variable", ra=ra, dec=40.0, elements={"vtype": "M", "any_time": True})
        ev = T.next_events(mira, SITE, nights, w[0])
        self.assertTrue(ev["any"])
        self.assertEqual(ev["night"], nights[0])
        never = Target(name="South", kind="variable", ra=ra, dec=-80.0, elements={"vtype": "M", "any_time": True})
        self.assertEqual(T.next_events(never, SITE, nights, w[0]), {"none": "none in the next 3 nights"})

    def test_campaign_and_period_text(self):
        c = Target(name="Field", kind="campaign", ra=10.0, dec=50.0)
        self.assertEqual(T.next_events(c, SITE, [date(2026, 10, 25)], sky.now_jd())["text"], "every night")
        self.assertEqual(T.period_text(3.5248), "3.52480 d")
        self.assertEqual(T.period_text(0.2213), "5.31 h")
        self.assertEqual(T.period_text(332.0), "332.000 d")
        self.assertEqual(T.period_text(None), "—")


class TestTypesAndCredits(unittest.TestCase):
    def test_block_colors_follow_the_type(self):
        self.assertEqual(block_type_code(Block(kind="transit", mode="T")), "T")
        self.assertEqual(block_type_code(Block(kind="campaign", mode="V")), "D", "a campaign is plum")
        self.assertEqual(block_type_code({"kind": "variable", "mode": "V"}), "V")
        self.assertEqual(block_type_code(Block(mode="T")), "T", "an older block known only by its T mode")
        self.assertEqual([type_name(k) for k in ("transit", "variable", "campaign")],
                         ["Transit", "Variable", "Campaign"])

    def test_credits_list_sources(self):
        text = credits.credits_text()
        for s in ("NASA Exoplanet Archive", "AAVSO VSX", "VizieR", "SIMBAD", "Open-Meteo", "TAPIR",
                  "Jensen", "NumPy", "Matplotlib", "tzdata", "SHOBS-P"):
            self.assertIn(s, text)
        self.assertIn("Shiloh Hill Observatory", credits.COPYRIGHT)
        self.assertEqual(credits.citation_text("1.1.0"), "", "no DOI yet: no How to cite line")


class TestCapsMigration(unittest.TestCase):
    def test_load_settings_migrates_old_files_only(self):
        import tempfile
        old_path = settings.settings_path
        d = tempfile.mkdtemp()
        p = os.path.join(d, "s.json")
        settings.settings_path = lambda: p
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"options": {"suggest_max": 12, "var_search": {"max_results": 12}}}, fh)
            o = settings.load_settings()["options"]
            self.assertEqual((o["suggest_max"], o["var_search"]["max_results"]), (25, 25), "old file migrates")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"options": {"suggest_max": 12, "list_caps_25": True}}, fh)
            self.assertEqual(settings.load_settings()["options"]["suggest_max"], 12, "a new choice of 12 stays")
        finally:
            settings.settings_path = old_path

    def test_old_caps_of_12_become_25_once(self):
        s = {"options": {"suggest_max": 12, "var_search": {"max_results": 12, "min_amp": 0.2}}}
        settings.migrate_list_caps(s)
        self.assertEqual(s["options"]["suggest_max"], 25)
        self.assertEqual(s["options"]["var_search"]["max_results"], 25)
        s["options"]["suggest_max"] = 12                  # chosen again later: kept
        settings.migrate_list_caps(s)
        self.assertEqual(s["options"]["suggest_max"], 12)
        s = {"options": {"suggest_max": 8}}
        settings.migrate_list_caps(s)
        self.assertEqual(s["options"]["suggest_max"], 8, "a chosen cap stays")
        self.assertEqual(settings.DEFAULT_OPTIONS["suggest_max"], 25)
        self.assertTrue(settings.DEFAULT_OPTIONS["list_caps_25"], "a new install's own 12 is never migrated")
        self.assertEqual(T.VAR_SEARCH_DEFAULTS["max_results"], 25)


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestRound4Page(Base):
    def test_sidebar_and_tables_say_no_shobs_p(self):
        self.golden_home()
        app = self.open()

        def texts(w, out):
            for k in w.winfo_children():
                try:
                    out.append(str(k.cget("text")))
                except Exception:
                    pass
                texts(k, out)
            return out
        side = " ".join(texts(app.side, []))
        self.assertNotIn("SHOBS-P", side)
        self.assertNotIn("Discovery", side)
        self.assertNotIn("BLOCK COLORS", side, "no color legend in the sidebar (John 24 Oct)")
        self.assertIn("Credits and sources", side)
        for i in (1, 2, 3):
            app.show_page(i)
            self.pump()
            self.assertNotIn("SHOBS-P", app.subtitle_var.get())
        app.show_page(2)
        app.pages[2].build(date(2026, 10, 22), 1)
        self.pump()
        tab = app.pages[2].tabs[0]
        heads = [tab.table.heading(c, "text") for c in tab.table["columns"]]
        self.assertFalse(any("mode" in h.lower() for h in heads), heads)
        self.assertNotIn("SHOBS-P mode", app.pages[3].COLUMNS)
        self.assertIn("Didn't fit, and why", str(tab.nofit_box.cget("text")), "Didn't fit stays on Schedule")
        # the Edit dialog has no SHOBS-P mode row
        dlg = self.sa.EditTargetDialog(app, app.targets[0], modal=False)
        self.assertNotIn("SHOBS-P", " ".join(texts(dlg.win, [])))
        dlg.win.destroy()

    def test_status_blank_clears_a_mark(self):
        self.golden_home()
        app = self.open()
        app.show_page(2)
        page = app.pages[2]
        page.build(date(2026, 10, 22), 1)
        self.pump()
        tab = page.tabs[0]
        self.assertEqual(self.sa.STATUS_CHOICES[0], "", "a blank entry on top")
        self.assertNotIn("Planned", self.sa.STATUS_CHOICES)
        i, b = tab.blocks()[0]
        tab.select_block(i)
        tab.status_var.set("Done")
        tab.status_box.event_generate("<<ComboboxSelected>>")
        self.assertEqual(page.night(0).blocks_obj()[i].status, "done")
        tab.select_block(i)
        self.assertEqual(tab.status_var.get(), "Done")
        tab.status_var.set("")
        tab.status_box.event_generate("<<ComboboxSelected>>")
        self.assertEqual(page.night(0).blocks_obj()[i].status, "planned", "blank = the mark removed")
        tab.select_block(i)
        self.assertEqual(tab.status_var.get(), "")
        saved = json.load(open(settings.schedule_path(), encoding="utf-8"))
        self.assertEqual(saved["nights"][0]["blocks"][i]["status"], "planned", "saved")

    def test_uncertain_timing_never_shown_as_a_time(self):
        """Review round 4 must-fix: an EW with "timed" elements but an imprecise VSX epoch keeps its ⚠, before and
        after its events are found, and after Keep."""
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        t = Target(name="EW-like", kind="variable", mode="V", ra=20.0, dec=40.0, suggested=True, enabled=False,
                   elements={"vtype": "EW", "period": 0.31, "epoch": 2442582.406, "epoch_kind": "min",
                             "any_time": False},
                   suggest_info={"when": sky.now_jd() + 0.5, "night": "2026-10-25", "kind": "cycle",
                                 "time_uncertain": True, "timing_sigma_min": 435540.0})
        app.targets.append(t)
        page.events[t.id] = {"night": date(2026, 10, 25), "t": sky.now_jd() + 0.5, "event": "min"}
        page.fill()
        tv = page.tables["variable"].tree
        self.assertTrue(tv.set(t.id, "event").startswith("any time  ⚠ time uncertain ±"), tv.set(t.id, "event"))
        page.select_target(t.id)
        page.keep_selected()
        self.assertTrue(tv.set(t.id, "event").startswith("any time  ⚠"), "kept: the marker stays")

    def test_credits_page_and_back(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        app.show_credits()
        self.assertTrue(app.on_credits)
        self.assertEqual(app.title_var.get(), "Credits and sources")
        self.assertIn("NASA Exoplanet Archive", app.credits_page.text.get("1.0", "end"))
        self.assertIn("© Shiloh Hill Observatory", str(app.credits_line.cget("text")))
        path = os.path.join(self.tmp, "credits.txt")
        self.assertEqual(app.credits_page.save_text(path), path)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("SHOBS-SCHED", body)
        self.assertIn("GNU General Public License", body)
        app.credits_page.back()
        self.assertFalse(app.on_credits)
        self.assertEqual(app.page_index, 1)
        self.assertEqual(app.title_var.get(), "Targets")

    def test_transit_popup_cap_and_wheel(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        dlg = page.ask_transit_search()
        self.assertEqual(dlg.max_var.get(), "25")
        dlg.max_var.set("0")
        self.assertFalse(dlg.search())
        self.assertIn("1 to 100", dlg.msg_var.get())
        dlg.max_var.set("40")
        self.assertTrue(dlg.search())
        self.pump()
        self.assertEqual(settings.load_settings()["options"]["suggest_max"], 40)
        # a long list scrolls with the wheel, in its own box
        tv = page.tables["variable"].tree
        for k in range(40):
            app.targets.append(Target(name=f"Var {k:02d}", kind="variable", mode="V", ra=10.0 + k, dec=40.0,
                                      elements={"vtype": "M", "any_time": True}))
        page.fill()
        app.update()
        self.assertEqual(tv.yview()[0], 0.0)
        tv.event_generate("<Button-5>")
        app.update()
        self.assertGreater(tv.yview()[0], 0.0, "the wheel scrolls the Variables list")
        self.assertEqual(page.tables["transit"].tree.yview()[0], 0.0, "the other lists stay put")


if __name__ == "__main__":
    unittest.main()
