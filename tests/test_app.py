"""The app (sched_app.py) under a virtual display:

    cd /home/claude/shobs-sched && xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_app -v

TestAppWithFakes swaps targets / planner / weather / export for the small fakes below (so the app's own behaviour is
tested whatever state those modules are in); TestAppWithRealModules runs the same flow on the real modules with the
network switched off (skipped when one of them does not import). Every test uses a fresh temporary home folder."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:          # no display (run under xvfb-run) or no Tk
    HAVE_TK = False

import settings  # noqa: E402
import sky  # noqa: E402
from models import Block, NightPlan, Schedule, Target  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_oct19_24.json")


def golden():
    with open(GOLDEN, encoding="utf-8") as fh:
        return json.load(fh)


# ---- fakes (tests only) --------------------------------------------------------------------------------------------

def make_fakes():
    """Minimal stand-ins with the SPEC's API shapes."""
    calls = {"build_night": [], "suggest": 0}

    # targets
    tg = types.ModuleType("targets")

    class LookupError_(Exception):
        pass

    class NeedsMode(Exception):
        def __init__(self, name, ra, dec, vmag, main_id):
            self.name, self.ra, self.dec, self.vmag, self.main_id = name, ra, dec, vmag, main_id
            super().__init__(f"{name} needs a mode")

    class PlanetChoice(Exception):
        def __init__(self, system, planets):
            self.system, self.planets = system, planets
            super().__init__(f"{system} has several planets")

    def add_target(name, when_jd=None):
        if name == "Nowhere 1":
            raise LookupError_("SIMBAD does not know “Nowhere 1”.")
        if name == "HD 1":
            raise NeedsMode(name, 10.0, 20.0, 8.1, "HD 1")
        if name == "Kepler-9":
            raise PlanetChoice("Kepler-9", ["Kepler-9 b", "Kepler-9 c"])
        if name.startswith("Kepler-9 "):
            return Target(name=name, kind="transit", mode="T", ra=285.6, dec=38.4, vmag=13.9, source="NEA",
                          planet={"name": name, "period": 19.2, "t0": 2455073.4, "t14": 0.17, "depth": 0.006})
        return Target(name=name, kind="variable", mode="V", ra=100.0, dec=10.0, vmag=11.0, source="VSX",
                      elements={"vtype": "RRAB", "period": 0.5, "epoch": 2450000.0, "any_time": True})

    def suggest_transits(site, optics, options, nights, exclude_names=frozenset(), progress=None):
        calls["suggest"] += 1
        if progress:
            progress(1, 1)
        return [Target(name="WASP-2 b", kind="transit", mode="T", ra=307.7, dec=6.4, vmag=11.98, source="NEA",
                       suggested=True, enabled=False,
                       planet={"name": "WASP-2 b", "period": 2.15, "t0": 2453991.5, "t14": 0.07, "depth": 0.017})]

    tg.LookupError_, tg.NeedsMode, tg.PlanetChoice = LookupError_, NeedsMode, PlanetChoice
    tg.add_target = add_target
    tg.suggest_transits = suggest_transits
    tg.next_event_text = lambda t, site, from_jd, days=30: "any time" if t.kind != "transit" else "Fri 23 Oct (full)"
    tg.tc_sigma_minutes = lambda planet, when: 12.5
    tg.campaign_progress = lambda t, sch, today: (1, t.goal_per_week)
    tg.lookup_planet = lambda name, when_jd=None: Target(name=name, kind="transit", planet={
        "name": name, "period": 6.76, "t0": 2460599.5, "t14": 0.06})

    # planner: one block per enabled target, back to back from dusk; locked blocks kept as they are
    pl = types.ModuleType("planner")

    def build_night(d, site, optics, targets, options, locked=(), weather=None, history=None, prefer=frozenset()):
        calls["build_night"].append({"date": d, "prefer": set(prefer), "locked": [b.target_id for b in locked]})
        info = sky.night_info(d, site)
        n = NightPlan(date=d.isoformat(), dusk=info["dusk"], dawn=info["dawn"], civil_dusk=info["civil_dusk"],
                      civil_dawn=info["civil_dawn"], nautical_dusk=info["nautical_dusk"],
                      nautical_dawn=info["nautical_dawn"], moon_illum=info["moon_illum"],
                      moonrise=info["moonrise"], moonset=info["moonset"], weather=weather)
        blocks = [Block.from_dict(b.to_dict()) for b in locked]
        taken = {b.target_id for b in blocks}
        t = info["dusk"]
        order = sorted(targets, key=lambda x: (x.id not in prefer, x.name))
        for tgt in order:
            if not tgt.enabled or tgt.id in taken:
                continue
            start, end = t, t + 50 / 1440.0
            if any(b.start < end and start < b.end for b in blocks):
                start = max(b.end for b in blocks) + 3 / 1440.0
                end = start + 50 / 1440.0
            if end > info["dawn"]:
                n.didnt_fit.append({"target_id": tgt.id, "name": tgt.name, "reason": "no room left",
                                    "next_chance": (d + timedelta(days=1)).isoformat()})
                continue
            mer = sky.target_meridian(tgt.ra, tgt.dec, site, (start + end) / 2)
            xs = [float(sky.target_airmass(tgt.ra, tgt.dec, site, j)) for j in (start, (start + end) / 2, end)]
            blocks.append(Block(target_id=tgt.id, name=tgt.name, mode=tgt.mode, kind=tgt.kind, start=start, end=end,
                                meridian=mer, airmass_start=xs[0], airmass_best=min(xs), airmass_end=xs[2],
                                exposure_s=tgt.exposure_s or 30, frames=90, notes=[f"note for {tgt.name}"],
                                cautions=["⚠ test caution"] if tgt.kind == "transit" else []))
            t = end + 3 / 1440.0
        blocks.sort(key=lambda b: b.start)
        n.blocks = [b.to_dict() for b in blocks]
        n.free = [(info["dawn"] - 30 / 1440.0, info["dawn"])]
        n.flats = ["Flats: one set at the night's camera angle"]
        n.darks = ["Darks to shoot: 30 s"]
        return n

    def build_schedule(start, n_nights, site, optics, targets, options, prior=None, weather_by_date=None,
                       prefer_by_date=None):
        prior_n = {x.date: x for x in prior.nights_obj()} if prior else {}
        nights = []
        for k in range(n_nights):
            d = start + timedelta(days=k)
            p = prior_n.get(d.isoformat())
            locked = [b for b in p.blocks_obj() if b.locked or b.set_by_user or b.status != "planned"] if p else []
            nights.append(build_night(d, site, optics, targets, options, locked,
                                      (weather_by_date or {}).get(d.isoformat())).to_dict())
        return Schedule(nights=nights, history=list(prior.history) if prior else [], built="now")

    def rereview(schedule, today, site, optics, targets, options, weather_by_date=None):
        hist = list(schedule.history)
        keep = []
        for nd in schedule.nights_obj():
            if date.fromisoformat(nd.date) < today:
                hist += [{"date": nd.date, "target_id": b.target_id, "name": b.name, "status": b.status,
                          "minutes": b.minutes()} for b in nd.blocks_obj()]
            else:
                keep.append(nd.to_dict())
        return build_schedule(today, max(1, len(schedule.nights)), site, optics, targets, options,
                              prior=Schedule(nights=keep, history=hist), weather_by_date=weather_by_date)

    pl.build_night, pl.build_schedule, pl.rereview = build_night, build_schedule, rereview

    # weather: a synthetic cloudy-later night with dew onset at 03:00
    wx = types.ModuleType("weather")

    class FakeSource:
        name = "Open-Meteo"

        def fetch(self, site, start, n_nights, options=None):
            out = {}
            for k in range(n_nights):
                d = start + timedelta(days=k)
                info = sky.night_info(d, site)
                hours, j = [], info["sunset"]
                i = 0
                while j <= info["sunrise"]:
                    hours.append({"jd": j, "cloud": min(100, i * 9), "temp_c": 14 - i * 0.6, "dew_c": 6 + i * 0.1,
                                  "spread_c": 8 - i * 0.7, "rh": 70, "wind_kmh": 8})
                    j += 1 / 24
                    i += 1
                out[d.isoformat()] = {"source": "Open-Meteo", "ok": True, "note": "", "hours": hours,
                                      "dew_onset": hours[len(hours) * 2 // 3]["jd"], "summary": "Clear, then cloud"}
            return out
    wx.SOURCES = {"Open-Meteo": FakeSource}

    # export
    ex = types.ModuleType("export")

    def table_rows(night, site, targets_by_id=None):
        return [{"time": f"{sky.hhmm(b['start'], site.tz)}–{sky.hhmm(b['end'], site.tz)}", "target": b["name"],
                 "radec": "00:00:00 +00:00:00", "mode": b["mode"], "alt_x": "60° (X 1.15)", "meridian": "—",
                 "exposure": "30 s", "frames": "~90", "notes": "; ".join(b["notes"] + b["cautions"]),
                 "mode_code": b["mode"]} for b in night.blocks]

    def asiair_targets(nights, targets_by_id, site=None):
        seen, rows = set(), []
        for n in nights:
            for b in n.blocks_obj():
                if b.target_id not in seen:
                    seen.add(b.target_id)
                    rows.append({"name": b.name})
        return rows, []

    def asiair_csv(nights, targets_by_id, site=None, optics=None):
        rows, _ = asiair_targets(nights, targets_by_id, site)
        return "\n".join(["Pane, RA, DEC"] + [f"{r['name']}, 1, 2" for r in rows])

    ex.table_rows, ex.asiair_csv, ex.asiair_targets = table_rows, asiair_csv, asiair_targets
    ex.plan_card_text = lambda nights, site, s=None, w=None, targets_by_id=None: "PLAN CARD " + " ".join(
        n.date for n in nights)
    ex.plan_card_html = lambda nights, site, s=None, w=None, targets_by_id=None: "<html>card</html>"
    return {"targets": tg, "planner": pl, "weather": wx, "export": ex}, calls


# ---- base ----------------------------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_TK, "needs a display: run under xvfb-run")
class Base(unittest.TestCase):
    use_fakes = True

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched_app_test_")
        self._env = {k: os.environ.get(k) for k in ("SHOBS_SCHED_HOME", "SHOBS_P_SETTINGS")}
        os.environ["SHOBS_SCHED_HOME"] = self.tmp
        os.environ["SHOBS_P_SETTINGS"] = os.path.join(self.tmp, "no_shobs_p.json")
        import sched_app
        self.sa = sched_app
        self._saved_modules = dict(sched_app._MODULES)
        if self.use_fakes:
            fakes, self.calls = make_fakes()
            sched_app._MODULES.clear()
            sched_app._MODULES.update(fakes)
        self.app = None

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass
        self.sa._MODULES.clear()
        self.sa._MODULES.update(self._saved_modules)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def golden_home(self):
        g = golden()
        s = settings.default_settings()
        s["site"].update(g["site"])
        s["optics"].update(g["optics"])
        settings.save_settings(s)
        settings.save_targets([Target.from_dict(t) for t in g["targets"]])
        return g

    def open(self):
        self.app = self.sa.App(testing=True)
        self.app.geometry("1400x900+0+0")
        self.app.update()
        return self.app

    def pump(self):
        self.app.jobs.wait(300)
        for _ in range(3):
            self.app.update()


class TestAppWithFakes(Base):
    def test_standalone_clean_home_every_page(self):
        app = self.open()
        self.assertEqual(app.page_index, 0, "a fresh home opens on Setup")
        setup = app.pages[0]
        self.assertTrue(setup.prov_var.get().startswith("Enter your site and equipment once."))
        self.assertTrue(setup.vars["site.tz"].get(), "a time zone guess is preselected")
        for i in range(4):
            app.show_page(i)
            app.update()
            self.assertEqual(app.title_var.get(), self.sa.STEPS[i])
            self.assertFalse(app.status_var.get().startswith("✖"), app.status_var.get())
        sch = app.pages[2]
        self.assertTrue(sch.placeholder.winfo_ismapped())
        self.assertTrue(sch.msg_var.get().startswith("✖ Enter your site"))
        sch.build()
        self.assertTrue(sch.ind.var.get().startswith("✖ Enter your site"))
        self.assertIn("Tick one or more", app.pages[3].preview.get("1.0", "end"))

    def test_shobs_p_provenance_and_set_here(self):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 35.303, "site_lon": -81.9407, "site_elevation": 280, "aperture_mm": 356,
                       "focal_mm": 2900, "pixel_um": 3.76}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        setup = app.pages[0]
        self.assertTrue(setup.prov_var.get().startswith("✔ Site, optics and camera read from SHOBS-P (settings of"))
        self.assertEqual(setup.vars["site.lat"].get(), "35.303")
        self.assertFalse(setup.tags["site.lat"].winfo_manager())
        setup.vars["site.lat"].set("35.31")
        setup.vars["site.tz"].set("America/New_York")
        self.assertTrue(setup.save(), setup.msg_var.get())
        saved = settings.load_settings()
        self.assertIn("site.lat", saved["set_here"])
        self.assertNotIn("site.lon", saved["set_here"])
        self.assertTrue(setup.tags["site.lat"].winfo_manager(), "the overridden field shows its 'set here' tag")
        with open(p, encoding="utf-8") as fh:
            self.assertNotIn("set_here", fh.read(), "SHOBS-P's file is never written")

    def test_setup_validation_and_units(self):
        app = self.open()
        setup = app.pages[0]
        setup.vars["site.lat"].set("north")
        setup.vars["site.lon"].set("-81.94")
        self.assertFalse(setup.save())
        self.assertTrue(setup.msg_var.get().startswith("✖ Not saved: Latitude must be a number"))
        setup.vars["site.lat"].set("35.3")
        setup.vars["site.tz"].set("Mars/Olympus")
        self.assertFalse(setup.save())
        self.assertIn("Mars/Olympus", setup.msg_var.get())
        setup.vars["site.tz"].set("America/New_York")
        setup.vars["site.elevation_m"].set("280")
        setup.vars["site.length_unit"].set("ft")            # the shown value converts
        self.assertAlmostEqual(float(setup.vars["site.elevation_m"].get()), 918.6, places=1)
        setup.horizon.insert("1.0", "90 25\n180 15\n")
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertAlmostEqual(s["site"]["elevation_m"], 280.0, places=1)
        self.assertEqual(s["site"]["horizon"], [[90.0, 25.0], [180.0, 15.0]])
        self.assertTrue(setup.msg_var.get().startswith("✔"))
        self.assertEqual(self.sa.parse_horizon("10 x")[0], [])

    def test_targets_toggle_priority_add_suggest(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        self.pump()
        dy = next(t for t in app.targets if t.name == "DY Peg")
        self.assertEqual(page.tree.set(dy.id, "plan"), "☑")
        self.assertEqual(page.tree.set(dy.id, "next"), "any time")
        # click the Plan cell
        page.tree.see(dy.id)
        app.update()
        x, y, w, h = page.tree.bbox(dy.id, "plan")
        page.tree.event_generate("<Button-1>", x=x + w // 2, y=y + h // 2)
        app.update()
        self.assertFalse(next(t for t in settings.load_targets() if t.name == "DY Peg").enabled)
        self.assertEqual(page.tree.set(dy.id, "plan"), "☐")
        page.set_priority(dy.id, "High")
        self.assertEqual(next(t for t in settings.load_targets() if t.name == "DY Peg").priority, "High")
        toi = next(t for t in app.targets if t.name == "TOI-2046 b")
        self.assertEqual(page.tree.set(toi.id, "tc"), "⚠ ± 12.5 min")
        hd = next(t for t in app.targets if t.name == "HD 219134")
        self.assertIn("1 of 5 this week", page.tree.set(hd.id, "campaign"))
        self.assertIn("HD 219134 c", page.tree.set(hd.id, "campaign"))
        # add: plain, unknown, needs a mode, several planets
        page.add("RR Lyr")
        self.pump()
        self.assertIn("RR Lyr", [t.name for t in app.targets])
        self.assertTrue(page.add_ind.var.get().startswith("✔ Added RR Lyr"))
        page.add("Nowhere 1")
        self.pump()
        self.assertTrue(page.add_ind.var.get().startswith("✖ SIMBAD does not know"))
        app.auto_answers = ["Discovery (D)"]
        page.add("HD 1")
        self.pump()
        hd1 = next(t for t in app.targets if t.name == "HD 1")
        self.assertEqual((hd1.kind, hd1.mode), ("campaign", "D"))
        app.auto_answers = ["Kepler-9 c"]
        page.add("Kepler-9")
        self.pump()
        self.assertIn("Kepler-9 c", [t.name for t in app.targets])
        page.add("RR Lyr")
        self.pump()
        self.assertTrue(page.add_ind.var.get().startswith("⚠ RR Lyr is already"))
        # suggested transits, tick one, keep it
        page.find_suggested()
        self.pump()
        w2 = next(t for t in app.targets if t.name == "WASP-2 b")
        self.assertTrue(w2.suggested and not w2.enabled)
        self.assertEqual(page.tree.parent(w2.id), "grp:suggested")
        page.toggle(w2.id)
        self.assertTrue(w2.enabled)
        page.tree.selection_set(w2.id)
        page.keep_selected()
        self.assertEqual(page.tree.parent(w2.id), "grp:transit")
        page.remove_ids({w2.id})
        self.assertNotIn("WASP-2 b", [t.name for t in settings.load_targets()])

    def test_edit_dialog(self):
        self.golden_home()
        app = self.open()
        hd = next(t for t in app.targets if t.name == "HD 219134")
        saved = []
        dlg = self.sa.EditTargetDialog(app, hd, on_save=saved.append, modal=False)
        dlg.vars["min_alt"].set("40")
        dlg.vars["window"].set("21:00-02:30")
        dlg.vars["goal_per_week"].set("4")
        dlg.add_no_transit({"name": "HD 219134 d", "period": 46.86, "t0": 2457000.0, "t14": 0.2}, 10)
        self.assertTrue(dlg.save())
        t = saved[0]
        kinds = [r["kind"] for r in t.rules]
        self.assertEqual(kinds.count("no_transit"), 3)
        self.assertIn({"kind": "min_alt", "value": 40.0}, t.rules)
        self.assertEqual(t.goal_per_week, 4)
        dlg = self.sa.EditTargetDialog(app, hd, on_save=saved.append, modal=False)
        dlg.vars["window"].set("late")
        self.assertFalse(dlg.save())
        self.assertTrue(dlg.msg_var.get().startswith("✖ Not saved: Only between"))
        dlg.win.destroy()

    def test_schedule_build_draw_lock_rebuild(self):
        self.golden_home()
        app = self.open()
        app.show_page(2)
        page = app.pages[2]
        page.build(date(2026, 10, 20), 2)
        self.pump()
        self.assertEqual(len(app.schedule.nights), 2, page.ind.var.get())
        self.assertEqual(len(page.tabs), 2)
        self.assertTrue(page.msg_var.get().startswith("✔ Built 2 nights"), page.msg_var.get())
        tab = page.tabs[0]
        app.update()
        tab.draw_bar()
        tab.draw_weather()
        texts = [tab.bar.itemcget(i, "text") for i in tab.bar.find_all() if tab.bar.type(i) == "text"]
        self.assertIn("Site clock", texts)
        self.assertTrue(any("·" in s or s.startswith("X ") for s in texts), "airmass values under the blocks")
        wtexts = [tab.wx.itemcget(i, "text") for i in tab.wx.find_all() if tab.wx.type(i) == "text"]
        self.assertTrue(any(s.startswith("⚠ dew from") for s in wtexts), wtexts)
        self.assertGreater(len(tab.table.get_children()), 3)
        first = tab.table.get_children()[0]
        self.assertTrue(tab.table.tag_has("modeV", first) or tab.table.tag_has("modeT", first))
        # lock the second block, mark the first Done, rebuild: both survive
        iid = tab.table.get_children()[1]
        tab.table.selection_set(iid)
        tab._selected()
        b_before = tab.night().blocks_obj()[int(iid)]
        tab.lock_selected()
        self.assertTrue(tab.night().blocks_obj()[int(iid)].locked)
        tab.select_block(int(first))
        tab.set_status("done")
        self.assertEqual(settings.load_schedule().nights[0]["blocks"][int(first)]["status"], "done")
        page.build(date(2026, 10, 20), 2)
        self.pump()
        kept = [b for b in page.night(0).blocks_obj() if b.target_id == b_before.target_id]
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].locked)
        self.assertAlmostEqual(kept[0].start, b_before.start, places=6)
        # edit times → set by you, rebuilt night keeps it
        tab = page.tabs[0]
        i = next(i for i, b in tab.blocks() if b.target_id == b_before.target_id)
        self.assertTrue(tab.set_times(i, "23:00", "23:40"))
        self.pump()
        b = next(b for b in page.night(0).blocks_obj() if b.target_id == b_before.target_id)
        self.assertTrue(b.set_by_user)
        self.assertEqual(sky.hhmm(b.start, app.site.tz), "23:00")
        k = next(k for k, x in enumerate(page.night(0).blocks_obj()) if x.target_id == b.target_id)
        self.assertIn("set by you", page.tabs[0].table.set(str(k), "target"))
        # Plan this instead → rebuild of that night with prefer
        tid = app.targets[0].id
        page.tabs[0].plan_instead(tid, app.targets[0].name)
        self.pump()
        self.assertEqual(self.calls["build_night"][-1]["prefer"], {tid})
        # move a flexible block to the other night
        tab = page.tabs[0]
        j, blk = next((k, x) for k, x in tab.blocks() if x.kind != "transit" and not x.locked)
        tab.move_block(j, 1)
        self.pump()
        self.assertTrue(any(x.target_id == blk.target_id and x.set_by_user for x in page.night(1).blocks_obj()))
        # re-review from "tonight" drops past nights into the history
        page.rereview()
        self.pump()
        self.assertTrue(app.schedule.history)
        self.assertGreaterEqual(app.schedule.nights[0]["date"], app.tonight().isoformat())

    def test_export_clipboard_and_card(self):
        self.golden_home()
        app = self.open()
        app.pages[2].build(date(2026, 10, 20), 1)
        self.pump()
        app.show_page(3)
        page = app.pages[3]
        self.assertIn("PLAN CARD 2026-10-20", page.preview.get("1.0", "end"))
        page.copy_csv()
        clip = app.clipboard_get()
        self.assertTrue(clip.startswith("Pane, RA, DEC"))
        n = len(clip.splitlines()) - 1
        self.assertEqual(page.msg_var.get().split(".")[0], f"✔ Copied {n} targets")
        path = page.print_card()
        self.assertTrue(path and os.path.exists(path) and path.startswith(settings.cache_dir()))
        out = os.path.join(self.tmp, "x.csv")
        self.assertTrue(page.write_csv(out, clip))
        for v in page.night_vars.values():
            v.set(False)
        page.copy_csv()
        self.assertTrue(page.msg_var.get().startswith("⚠ Tick one or more nights"))

    def test_no_dark_site_and_missing_module(self):
        s = settings.default_settings()
        s["site"].update({"name": "Tromsø", "lat": 69.65, "lon": 18.96, "tz": "Europe/Oslo"})
        settings.save_settings(s)
        settings.save_targets([Target(name="Star", kind="campaign", mode="D", ra=200.0, dec=60.0)])
        self.sa._MODULES.pop("planner")
        real_import = self.sa.importlib.import_module
        self.sa.importlib.import_module = lambda name: (_ for _ in ()).throw(ImportError(name)) \
            if name == "planner" else real_import(name)
        try:
            app = self.open()
            app.show_page(2)
            app.pages[2].build(date(2026, 6, 20), 1)
            self.assertTrue(app.pages[2].ind.var.get().startswith("✖ This copy of SHOBS-SCHED is missing planner"))
        finally:
            self.sa.importlib.import_module = real_import
        fakes, _c = make_fakes()
        self.sa._MODULES["planner"] = fakes["planner"]

        def nodark(d, site, optics, targets, options, **kw):
            return NightPlan(date=d.isoformat(), didnt_fit=[{"target_id": "", "name": "Star",
                                                             "reason": "no dark time tonight"}])
        fakes["planner"].build_night = nodark
        fakes["planner"].build_schedule = lambda start, n, *a, **k: Schedule(nights=[nodark(start, None, None, [],
                                                                                             {}).to_dict()])
        app.pages[2].build(date(2026, 6, 20), 1)
        self.pump()
        tab = app.pages[2].tabs[0]
        self.assertTrue(tab.warn_var.get().startswith("⚠ No dark time tonight"))
        tab.draw_bar()
        tab.draw_weather()


class TestAppWithRealModules(Base):
    """The same flow on the real targets / planner / weather / export, network off."""
    use_fakes = False

    def setUp(self):
        super().setUp()
        try:
            import export  # noqa: F401
            import planner  # noqa: F401
            import targets  # noqa: F401
            import weather
        except Exception as exc:
            self.skipTest(f"a worker module does not import yet: {exc!r}")
        self.sa._MODULES.clear()
        import urllib.request
        self._urlopen = urllib.request.urlopen

        def offline(*_a, **_k):
            raise OSError("network off in tests")
        urllib.request.urlopen = offline
        self._wx_get = weather.http_get_json
        fx = os.path.join(HERE, "fixtures", "weather_openmeteo_shobs_20261022.json")
        reply = None
        if os.path.exists(fx):
            with open(fx, encoding="utf-8") as fh:
                reply = json.load(fh)

        def get(url, timeout=None):
            if reply is None:
                raise weather.WeatherError("offline")
            return reply
        weather.http_get_json = get

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._urlopen
        import weather
        weather.http_get_json = self._wx_get
        super().tearDown()

    def test_golden_build_lock_export(self):
        self.golden_home()
        app = self.open()
        for i in range(4):
            app.show_page(i)
            self.pump()
        page = app.pages[2]
        page.build(date(2026, 10, 22), 3)
        self.pump()
        self.assertEqual(len(app.schedule.nights), 3, page.ind.var.get() + " " + page.msg_var.get())
        app.show_page(2)
        app.update()
        for k, tab in enumerate(page.tabs):
            page.nb.select(k)
            app.update()
            tab.draw_bar()
            tab.draw_weather()
            self.assertTrue(tab.bar.find_all())
        tab = page.tabs[0]
        self.assertTrue(tab.night().weather and tab.night().weather.get("hours"), "weather from the recording")
        i, b = tab.blocks()[0]
        tab.select_block(i)
        tab.lock_selected()
        page.build(date(2026, 10, 22), 3)
        self.pump()
        kept = [x for x in page.night(0).blocks_obj() if x.target_id == b.target_id and x.locked]
        self.assertEqual(len(kept), 1, "the locked block survives the rebuild")
        self.assertAlmostEqual(kept[0].start, b.start, places=6)
        self.assertAlmostEqual(kept[0].end, b.end, places=6)
        # every row's columns filled from export.table_rows
        row = page.tabs[0].table.item(page.tabs[0].table.get_children()[0], "values")
        self.assertIn("(X ", row[4])
        app.show_page(3)
        app.update()
        app.pages[3].copy_csv()
        self.assertTrue(app.pages[3].msg_var.get().startswith("✔ Copied"), app.pages[3].msg_var.get())
        self.assertTrue(app.clipboard_get().startswith("Pane, RA, DEC"))
        self.assertIn("SHOBS-SCHED", app.pages[3].preview.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
