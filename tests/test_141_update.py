"""SHOBS-SCHED 1.4.1: the new-version check (GitHub #5, shared shobs_update.py) with the What's new blurb from the
release notes' Highlights section (John, 10 Nov 2026), its status-strip note and New version window, the Setup
checkbox; and the Edit dialog's 1.4.1 pieces (SHOBS-P series field, the bounds line, the plot's words never clipped)."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
import shobs_update as U  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402
from tests.test_141_bounds import write_series  # noqa: E402

NOTES = """## SHOBS-SCHED v1.4.1

Highlights:
- Exposure picks now say which bound decided them (saturation, cadence, precision)
- Your SHOBS-P series' comp and check stars join the saturation check
- A note on the status strip when a newer version is out
- (a fourth one is not shown)

### Fixes
- the 'over linear' label on the precision plot is no longer clipped
"""


def reply(tag="v1.4.1", body=NOTES, **kw):
    doc = {"tag_name": tag, "html_url": f"https://github.com/SHOBS-S/SHOBS-SCHED/releases/tag/{tag}",
           "name": tag, "draft": False, "prerelease": False, "body": body, "published_at": "2026-11-11T15:00:00Z"}
    doc.update(kw)

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda req, timeout=None: R(json.dumps(doc).encode())


class Highlights(unittest.TestCase):
    def test_parse(self):
        hl = U.parse_highlights(NOTES)
        self.assertEqual(len(hl), 3)
        self.assertEqual(hl[1], "Your SHOBS-P series' comp and check stars join the saturation check")
        self.assertTrue(all(len(h) <= U.HIGHLIGHT_CHARS for h in hl))

    def test_heading_forms_and_markdown(self):
        for head in ("## Highlights", "**Highlights:**", "Highlights", "### highlights:"):
            body = f"{head}\n\n* **Bold** and `code` and [a link](https://x)\n* two\n\nmore"
            self.assertEqual(U.parse_highlights(body), ["Bold and code and a link", "two"], head)

    def test_no_section_or_empty(self):
        self.assertEqual(U.parse_highlights("## Fixes\n- one"), [])
        self.assertEqual(U.parse_highlights(None), [])
        self.assertEqual(U.parse_highlights("Highlights:\nnot a bullet\n- late"), [])

    def test_long_line_trimmed(self):
        hl = U.parse_highlights("Highlights:\n- " + "word " * 40)
        self.assertEqual(len(hl[0]), U.HIGHLIGHT_CHARS)
        self.assertTrue(hl[0].endswith("…"))

    def test_note_text(self):
        t = U.note_text("SHOBS-SCHED", "1.4.1", ["Exposure picks explain themselves"])
        self.assertEqual(t, "SHOBS-SCHED 1.4.1 is available: Exposure picks explain themselves — click for details")
        self.assertEqual(U.note_text("SHOBS-SCHED", "1.4.1", []),
                         "SHOBS-SCHED 1.4.1 is available — see the release notes")
        self.assertLessEqual(len(U.note_text("SHOBS-SCHED", "1.4.1", ["x" * 200])), U.NOTE_CHARS)


class Check(unittest.TestCase):
    def test_fetch_and_check(self):
        got = U.fetch_latest("SHOBS-S/SHOBS-SCHED", opener=reply())
        self.assertEqual(got["published"], "2026-11-11")
        self.assertEqual(len(got["highlights"]), 3)
        state = {"check_updates": True}
        note = U.check("SHOBS-SCHED", "SHOBS-S/SHOBS-SCHED", "1.4.0", state, now=1000.0, fetch=lambda: got)
        self.assertEqual(note["version"], "1.4.1")
        self.assertTrue(note["text"].startswith("SHOBS-SCHED 1.4.1 is available: Exposure picks"))
        self.assertEqual(note["highlights"], got["highlights"])
        self.assertEqual(state["update_highlights"], got["highlights"])
        # the next start within the day: no question, the kept answer (with its highlights)
        again = U.check("SHOBS-SCHED", "SHOBS-S/SHOBS-SCHED", "1.4.0", state, now=2000.0,
                        fetch=lambda: self.fail("asked twice in a day"))
        self.assertEqual(again["highlights"], got["highlights"])
        # up to date: nothing to say
        self.assertIsNone(U.check("SHOBS-SCHED", "SHOBS-S/SHOBS-SCHED", "1.4.1", state, now=3000.0))

    def test_silent_and_never_downloads(self):
        self.assertIsNone(U.fetch_latest("x/y", opener=reply(draft=True)))
        self.assertIsNone(U.fetch_latest("x/y", opener=reply(prerelease=True)))

        def boom(req, timeout=None):
            raise OSError("offline")
        self.assertIsNone(U.fetch_latest("x/y", opener=boom))
        src = open(U.__file__, encoding="utf-8").read()
        self.assertNotIn("urlretrieve", src)
        self.assertNotIn("assets", src)

    def test_old_state_without_highlights(self):
        state = {"update_checked_at": 1000.0, "update_tag": "v1.4.1",
                 "update_url": "https://github.com/SHOBS-S/SHOBS-SCHED/releases/tag/v1.4.1"}
        note = U.check("SHOBS-SCHED", "r", "1.4.0", state, now=1001.0)
        self.assertEqual(note["text"], "SHOBS-SCHED 1.4.1 is available — see the release notes")
        self.assertEqual(note["highlights"], [])

    def test_settings_default_on(self):
        self.assertTrue(settings.default_settings()["check_updates"])
        self.assertEqual(settings.REPO, "SHOBS-S/SHOBS-SCHED")
        self.assertEqual(settings.APP_VERSION, "1.4.1")


@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class Window(Base):
    use_fakes = False

    def test_note_window_and_checkbox(self):
        app = self.open()
        opened = []
        self.sa.open_url = opened.append
        note = U.check("SHOBS-SCHED", "r", "1.4.0", {}, now=1.0, fetch=lambda: U.fetch_latest("r", opener=reply()))
        app.show_update(note)
        self.assertTrue(app.update_var.get().startswith("SHOBS-SCHED 1.4.1 is available: Exposure picks"))
        app.open_update()
        app.update()
        wins = [w for w in app.winfo_children() if str(w.winfo_class()) == "Toplevel"]
        self.assertTrue(wins)
        nv = self.sa.NewVersionWindow(app, note)
        self.assertEqual(len(nv.lines), 3)
        nv.open_page()
        self.assertEqual(opened, [note["url"]])
        # no highlights: the click goes straight to the release page
        app.show_update(dict(note, highlights=[], text="SHOBS-SCHED 1.4.1 is available — see the release notes"))
        app.open_update()
        self.assertEqual(opened[-1], note["url"])
        # Setup's checkbox: off clears the note and is saved at once
        setup = app.pages[0]
        setup.check_updates_var.set(False)
        setup._set_check_updates()
        self.assertEqual(app.update_var.get(), "")
        self.assertFalse(settings.load_settings()["check_updates"])

    def test_edit_dialog_series_and_bounds(self):
        s = settings.default_settings()
        s["site"].update(lat=35.3, lon=-81.9, tz="America/New_York", elevation_m=280)
        s["optics"].update(aperture_mm=356, focal_mm=2900, pixel_um=3.76, binning=1, color_camera=True,
                           e_per_adu=0.243, linear_limit_adu=32000)
        settings.save_settings(s)
        from models import Target, VarElements
        el = VarElements(vtype="DSCT", period=0.149082, epoch=2460000.0, any_time=True, mag_max=13.5, mag_min=13.57)
        t = Target(name="GSC 03308-00462", kind="campaign", mode="D", ra=300.0, dec=50.0, vmag=13.5,
                   elements=el.__dict__.copy())
        settings.save_targets([t])
        with open(os.environ["SHOBS_P_SETTINGS"], "w", encoding="utf-8") as fh:
            json.dump({"lat": "35.3"}, fh)                # SHOBS-P is installed: the series row is shown
        app = self.open()
        app.show_page(1)
        out = []
        dlg = self.sa.EditTargetDialog(app, app.targets[0], on_save=out.append, modal=False)
        app.update()
        self.assertEqual(dlg.plan["exposure_s"], 180.0)
        self.assertTrue(dlg.ro_vars["bounds"].get().startswith("Bounds: 180 s, cadence-limited (P/50 = 4.3 min)"))
        # the plot: every word on its own backing, drawn after the shading, and the cap line
        texts = [dlg.plot.itemcget(i, "text") for i in dlg.plot.find_all() if dlg.plot.type(i) == "text"]
        self.assertIn("cadence cap", texts)
        for word in ("over linear", "saturated", "cadence cap"):
            ids = [i for i in dlg.plot.find_all() if dlg.plot.type(i) == "text"
                   and dlg.plot.itemcget(i, "text") == word]
            for i in ids:
                above = dlg.plot.find_above(i)
                for j in above:
                    if dlg.plot.type(j) == "rectangle":
                        self.fail(f"a band is drawn over '{word}'")
        # the series field: saved, and its comps checked
        path = write_series(self.tmp, [("C1", 10.0)])
        dlg.vars["shobs_p_series"].set(path)
        dlg.update_readout()
        self.assertEqual(dlg.plan["bounds"]["measured"]["source"], "series")
        dlg.save()
        app.update()
        self.assertEqual(out[-1].shobs_p_series, path)
        self.assertEqual(Target.from_dict(out[-1].to_dict()).shobs_p_series, path)       # kept in the file

    def test_no_series_row_without_shobs_p(self):
        app = self.open()
        from models import Target
        dlg = self.sa.EditTargetDialog(app, Target(name="X", kind="variable", ra=1.0, dec=2.0, vmag=11.0),
                                       modal=False)
        app.update()
        self.assertNotIn("shobs_p_series", dlg.vars)
        t, err = dlg.build()
        self.assertEqual((err, t.shobs_p_series), ("", ""))


if __name__ == "__main__":
    unittest.main()
