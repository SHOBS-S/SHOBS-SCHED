"""The busy bar (House Style, John 26 Oct 2026): one back-and-forth bar per window (lower left of the status strip)
or dialog (its own lower left), words naming the job, counts in the words, never a filling bar."""
from __future__ import annotations

import os
import sys
import threading
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
from tests.test_app import HAVE_TK, Base  # noqa: E402


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestBusyBar(Base):
    def wait_shown(self, bar, ms=600):
        self.app.after(ms, lambda: None)
        end = __import__("time").time() + ms / 1000
        while __import__("time").time() < end:
            self.app.update()

    def test_bar_tokens_words_and_hiding(self):
        app = self.open()
        bar = app.busy_bar
        self.assertFalse(bar.shown)
        a = bar.begin("Looking up RR Lyr…")
        self.wait_shown(bar)
        self.assertTrue(bar.shown)
        self.assertEqual(str(bar.bar.cget("mode")), "indeterminate")
        b = bar.begin("Getting the forecast…")
        self.assertEqual(bar.text, "Getting the forecast…", "the newest job's words show")
        bar.words(a, "Looking up RR Lyr at VSX…")
        self.assertEqual(bar.text, "Getting the forecast…")
        bar.end(b)
        self.assertEqual(bar.text, "Looking up RR Lyr at VSX…")
        self.assertTrue(bar.shown)
        bar.end(a)
        bar.end(a)                                   # idempotent
        self.assertFalse(bar.shown)
        self.assertEqual(bar.text, "")

    def test_short_job_never_flashes(self):
        app = self.open()
        tok = app.busy_bar.begin("Quick…")
        app.busy_bar.end(tok)
        self.wait_shown(app.busy_bar)
        self.assertFalse(app.busy_bar.shown)

    def test_bar_sits_in_the_status_strip_lower_left(self):
        app = self.open()
        app.update()
        self.assertEqual(app.busy_bar.master.master.cget("style"), "Status.TFrame")
        self.assertEqual(app.busy_bar.pack_info()["side"], "left")

    def test_counted_job_stays_back_and_forth_with_count_in_words(self):
        app = self.open()
        seen = []
        go = threading.Event()

        def work(progress):
            progress(3, 40)
            go.wait(5)
            return "ok"
        app.jobs.run(work, lambda r, e: None, None, "Finding suggested transits", counted=True)
        self.wait_shown(app.busy_bar)
        seen.append((app.busy_bar.text, str(app.busy_bar.bar.cget("mode"))))
        go.set()
        app.jobs.wait()
        self.assertEqual(seen[0], ("Finding suggested transits… 3 of 40", "indeterminate"))
        self.assertFalse(app.busy_bar.shown)

    def test_build_words_change_step_by_step(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        words = []
        real = app.busy_bar.words

        def spy(tok, text):
            words.append(text)
            real(tok, text)
        app.busy_bar.words = spy
        page.build(date(2026, 10, 20), 2)
        self.pump()
        self.assertEqual(words[:2], ["Getting the forecast from Open-Meteo…", "Building 2 nights…"])
        self.assertTrue(app.schedule.nights)
        self.assertEqual(page.ind.var.get(), "✔ Done", "the result shows beside the button")
        self.assertFalse(app.busy_bar.jobs)

    def test_result_line_has_no_bar_of_its_own(self):
        app = self.open()
        page = app.pages[1]
        self.assertFalse(hasattr(page.add_ind, "bar"))
        self.assertFalse(hasattr(page.find_ind, "bar"))

    def test_edit_dialog_has_its_own_bar(self):
        self.golden_home()
        app = self.open()
        import sched_app
        t = next(t for t in app.targets if t.kind != "transit")
        dlg = sched_app.EditTargetDialog(app, t, modal=False)
        try:
            self.assertIs(dlg.win.busy_bar, dlg.busy_bar)
            self.assertIs(sched_app.busy_bar_of(dlg.pl_ind), dlg.busy_bar)
            self.assertIs(sched_app.busy_bar_of(app.pages[1].add_ind), app.busy_bar)
        finally:
            self.pump()
            dlg.win.destroy()

    def test_plan_card_plots_drawn_in_the_background(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        page.build(date(2026, 10, 20), 1)
        self.pump()
        app.show_page(3)
        card = app.pages[3]
        threads = []

        def plots(nights, **kw):
            threads.append(threading.current_thread() is threading.main_thread())
            return None
        card.card_plots = plots
        card.plots_var.set(True)
        self.assertIsNone(card.print_card())
        app.jobs.wait()
        self.assertEqual(threads, [False], "the plots are drawn off the main thread")
        self.assertTrue(card.last_card_path and os.path.exists(card.last_card_path))
        self.assertTrue(card.last_card_path.startswith(settings.cache_dir()))


if __name__ == "__main__":
    unittest.main()
