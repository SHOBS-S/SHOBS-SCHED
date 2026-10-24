"""The app (sched_app.py) under a virtual display:

    cd /home/claude/shobs-sched && xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_app -v

TestAppWithFakes swaps targets / planner / weather / export for the small fakes below (so the app's own behaviour is
tested whatever state those modules are in); TestAppWithRealModules runs the same flow on the real modules with the
network switched off (skipped when one of them does not import). Every test uses a fresh temporary home folder."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
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


def px_tol(app) -> int:
    """A few pixels of slack (padding) at the display's scaling."""
    import style
    return style.px(app, 12)


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
        if name == "WASP-9 b":
            t = Target(name=name, kind="transit", mode="T", ra=1.0, dec=2.0, source="NEA",
                       planet={"name": name, "period": 2.0, "t0": 2455000.0, "t14": 0.1})
            t.lookup_note = "the Archive did not answer (HTTP 503); using the copy from 3 days ago"
            return t
        if name.startswith("Kepler-9 "):
            return Target(name=name, kind="transit", mode="T", ra=285.6, dec=38.4, vmag=13.9, source="NEA",
                          planet={"name": name, "period": 19.2, "t0": 2455073.4, "t14": 0.17, "depth": 0.006})
        return Target(name=name, kind="variable", mode="V", ra=100.0, dec=10.0, vmag=11.0, source="VSX",
                      elements={"vtype": "RRAB", "period": 0.5, "epoch": 2450000.0, "any_time": True})

    def suggest_transits(site, optics, options, nights, exclude_names=frozenset(), catalog=None, progress=None):
        calls["suggest"] += 1
        if progress:
            progress(1, 1)
        return [Target(name="WASP-2 b", kind="transit", mode="T", ra=307.7, dec=6.4, vmag=11.98, source="NEA",
                       suggested=True, enabled=False,
                       planet={"name": "WASP-2 b", "period": 2.15, "t0": 2453991.5, "t14": 0.07, "depth": 0.017})]

    tg.LookupError_, tg.NeedsMode, tg.PlanetChoice = LookupError_, NeedsMode, PlanetChoice
    tg.add_target = add_target
    tg.resolve_simbad = lambda name, refresh=False: {"main_id": name, "ra": 348.32, "dec": 57.17, "vmag": 5.57,
                                                     "otype": "*"}
    tg.load_transit_catalog = lambda refresh=False: ([], calls.get("catalog_note", ""))
    tg.suggest_transits = suggest_transits
    tg.next_event_text = lambda t, site, from_jd, days=30: "any time" if t.kind != "transit" else "Fri 23 Oct (full)"

    def next_events(t, site, nights, from_jd):
        if t.kind == "campaign":
            return {"text": "every night"}
        if t.kind == "transit":
            if not t.planet:
                return {"none": "no ephemeris"}
            tc = 2461337.6                      # Fri 23 Oct 2026, 22:24 EDT
            return {"night": date(2026, 10, 23), "t1": tc - 0.05, "tc": tc, "t4": tc + 0.05, "full": True}
        return {"night": date(2026, 10, 23), "any": True, "t": 2461337.55}
    tg.next_events = next_events
    tg.period_text = lambda p: "—" if not p else (f"{p * 24:.2f} h" if p < 1 else f"{p:.5f} d")
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

    def night_file_text(program, night, targets_by_id, site=None, optics=None):
        rows, _ = asiair_targets([night], targets_by_id, site)
        head = "Pane,RA,DEC" if program == "nina" else "Pane, RA, DEC"
        return "\n".join([head] + [f"{r['name']}, 1, 2" for r in rows]) + "\n"

    def write_night_file(text, name, folder, fallback):
        note = ""
        if not (folder and os.path.isdir(folder)):
            note = f"⚠ The chosen folder {folder} is missing, so the file went to {fallback} instead." if folder else ""
            folder = fallback
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path, note
    ex.night_file_text, ex.write_night_file = night_file_text, write_night_file
    ex.night_file_name = lambda program, d: f"SHOBS-SCHED_{program.upper()}_{d}.csv"
    ex.plan_card_text = lambda nights, site, s=None, w=None, targets_by_id=None: "PLAN CARD " + " ".join(
        n.date for n in nights)
    ex.plan_card_html = lambda nights, site, s=None, w=None, targets_by_id=None, plots=None: \
        "<html>card" + ("".join(f"<img src=\"data:image/png;base64,{len(v)}\">" for v in (plots or {}).values())) + "</html>"
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
            self.fakes = fakes
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

    def settle(self, seconds: float = 0.4):
        """Let the debounced redraws run (they wait 80 ms after the last resize)."""
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.app.update()
            time.sleep(0.02)

    def tag(self, setup, key) -> str:
        """The source tag shown beside a Setup field ("" = none shown)."""
        self.app.update()
        t = setup.tags[key]
        return str(t.cget("text")) if t.winfo_manager() else ""

    def pump(self):
        self.app.jobs.wait(300)
        for _ in range(3):
            self.app.update()


class TestAppWithFakes(Base):
    # ---- start, Setup ----
    def test_standalone_clean_home_every_page(self):
        app = self.open()
        self.assertEqual(app.page_index, 0, "a fresh home opens on Setup")
        setup = app.pages[0]
        self.assertTrue(setup.prov_var.get().startswith("Enter your site and equipment once"))
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
        self.assertIn("No schedule yet", app.pages[3].card_text())

    # ---- round-2 review regressions ----
    def test_edit_unchanged_save_keeps_ephemeris(self):
        self.golden_home()
        app = self.open()
        hp = next(t for t in app.targets if t.name == "HAT-P-23 b")
        saved = []
        dlg = self.sa.EditTargetDialog(app, hp, on_save=saved.append, modal=False)
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        pl = saved[0].planet
        self.assertEqual(pl["period"], 1.212886397)
        self.assertEqual(pl["period_err"], 7.4e-08)
        self.assertEqual(pl["t14"], 0.0908)
        self.assertEqual(pl["source"], "NEA (most precise)", "an unchanged Save is not 'typed'")
        dlg = self.sa.EditTargetDialog(app, hp, on_save=saved.append, modal=False)
        dlg.vars["period"].set("1.2128865")
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        pl = saved[1].planet
        self.assertEqual(pl["source"], "typed")
        self.assertIsNone(pl["period_err"])
        self.assertEqual(pl["t0_err"], 7.2e-05, "Tc untouched keeps its error")

    def test_standalone_values_survive_shobs_p_installed_later(self):
        app = self.open()
        setup = app.pages[0]
        setup.vars["site.lat"].set("35.303")
        setup.vars["site.lon"].set("-81.9407")
        setup.vars["site.tz"].set("America/New_York")
        setup.vars["optics.aperture_mm"].set("356")
        self.assertTrue(setup.save(), setup.msg_var.get())
        app.destroy()
        self.app = None
        p = os.path.join(self.tmp, "shobs_p_later.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 0.0, "site_lon": 0.0, "aperture_mm": 200, "focal_mm": 2900}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        self.assertEqual(app.site.lat, 35.303)
        self.assertEqual(app.site.lon, -81.9407)
        self.assertEqual(app.optics.aperture_mm, 356)
        self.assertEqual(app.optics.focal_mm, 2900, "an empty field is filled from SHOBS-P")

    def test_corrupt_settings_kept_as_bad(self):
        path = settings.settings_path() if hasattr(settings, "settings_path") else None
        if path is None:
            self.skipTest("no settings_path()")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        settings.load_settings()
        self.assertTrue(os.path.exists(path + ".bad"))


    def test_shobs_p_provenance_and_set_here(self):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 35.303, "site_lon": -81.9407, "site_elevation": 280, "aperture_mm": 356,
                       "focal_mm": 2900, "pixel_um": 3.76}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        setup = app.pages[0]
        self.assertTrue(setup.prov_var.get().startswith("✔ Site and telescope read from SHOBS-P (settings of"))
        self.assertEqual(setup.vars["site.lat"].get(), "35.303")
        self.assertEqual(self.tag(setup, "site.lat"), "from SHOBS-P")
        self.assertEqual(self.tag(setup, "site.max_airmass"), "", "an untouched default has no tag")
        setup.vars["site.lat"].set("35.31")
        setup.vars["site.tz"].set("America/New_York")
        self.assertTrue(setup.save(), setup.msg_var.get())
        saved = settings.load_settings()
        self.assertIn("site.lat", saved["set_here"])
        self.assertNotIn("site.lon", saved["set_here"])
        self.assertEqual(self.tag(setup, "site.lat"), "set here", "the overridden field shows its 'set here' tag")
        # typing SHOBS-P's own value back follows SHOBS-P again
        setup.vars["site.lat"].set("35.303")
        self.assertTrue(setup.save())
        self.assertNotIn("site.lat", settings.load_settings()["set_here"])
        self.assertEqual(self.tag(setup, "site.lat"), "from SHOBS-P")
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

    def test_setup_cadence_angle_defocus(self):
        app = self.open()
        setup = app.pages[0]
        setup.vars["site.lat"].set("35.3")
        setup.vars["site.lon"].set("-81.94")
        setup.vars["site.tz"].set("America/New_York")
        self.assertEqual(setup.vars["options.defocus_fwhm_factor"].get(), "1.5")
        setup.vars["optics.camera_angle_deg"].set("400")
        self.assertFalse(setup.save())
        self.assertIn("Usual camera angle", setup.msg_var.get())
        setup.vars["optics.camera_angle_deg"].set("30")
        setup.vars["optics.frame_overhead_s"].set("32")
        setup.vars["options.defocus_fwhm_factor"].set("2.5")
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertEqual(s["optics"]["camera_angle_deg"], 30.0)
        self.assertEqual(s["optics"]["frame_overhead_s"], 32.0)
        self.assertEqual(s["options"]["defocus_fwhm_factor"], 2.5)
        self.assertEqual(app.optics.frame_overhead_s, 32.0)
        setup.vars["optics.camera_angle_deg"].set("")           # blank = 0 (as before)
        setup.vars["optics.frame_overhead_s"].set("")
        self.assertTrue(setup.save(), setup.msg_var.get())
        s = settings.load_settings()
        self.assertIsNone(s["optics"]["camera_angle_deg"])
        self.assertIsNone(s["optics"]["frame_overhead_s"])

    def test_setup_unsaved_changes_on_leave_and_close(self):
        self.golden_home()
        app = self.open()
        app.show_page(0)
        setup = app.pages[0]
        setup.vars["site.min_alt"].set("35")
        app.show_page(1)                                   # valid change: saved on the way out, and said so
        self.assertEqual(app.page_index, 1)
        self.assertEqual(settings.load_settings()["site"]["min_alt"], 35.0)
        self.assertIn("kept", app.status_var.get())
        app.show_page(0)
        setup.vars["site.min_alt"].set("high")             # invalid: asked; "no" keeps the page
        app.auto_answers = [False]
        app.show_page(2)
        self.assertEqual(app.page_index, 0)
        self.assertTrue(setup.msg_var.get().startswith("✖ Not saved"))
        app.auto_answers = [True]                          # "drop the changes"
        app.show_page(2)
        self.assertEqual(app.page_index, 2)
        self.assertEqual(setup.vars["site.min_alt"].get(), "35")
        app.show_page(0)
        setup.vars["site.max_airmass"].set("2.5")
        app.on_close()                                     # the window's close button saves too
        self.app = None
        self.assertEqual(settings.load_settings()["site"]["max_airmass"], 2.5)

    # ---- Targets ----
    def test_targets_toggle_priority_add_suggest(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        self.pump()
        tv = page.tables["variable"].tree
        tt = page.tables["transit"].tree
        tc = page.tables["campaign"].tree
        # three boxes, Plan first, no mode square or SHOBS-P mode (John 24 Oct)
        for tree in (tv, tt, tc):
            self.assertEqual(tree["columns"][0], "plan")
            self.assertEqual(tree["columns"][1], "name")
            self.assertNotIn("mode", tree["columns"])
            self.assertNotIn("tree", str(tree.cget("show")), "no tree column (no mode square)")
        self.assertIn("period", tt["columns"])
        self.assertEqual([c for c in tt["columns"] if c in ("ingress", "mid", "egress")], ["ingress", "mid", "egress"])
        self.assertIn("event", tv["columns"])
        self.assertEqual(tv.column("event", "stretch"), True)
        dy = next(t for t in app.targets if t.name == "DY Peg")
        self.assertEqual(page.table_of(dy.id).group, "variable")
        self.assertEqual(tv.set(dy.id, "plan"), "☑")
        self.assertEqual(tv.set(dy.id, "name"), "DY Peg")
        self.assertRegex(tv.set(dy.id, "event"), r"^(any time|max \d\d:\d\d|min \d\d:\d\d|)$")
        # each list scrolls on its own: its own scroll bar, and the wheel over it (John 24 Oct)
        self.assertTrue(str(tv.cget("yscrollcommand")))
        self.assertEqual(tv.bind("<MouseWheel>") != "", True)
        self.assertEqual(page._click.__name__, "_click")
        self.assertEqual(len(page.paned.panes()), 2, "Transits and Variables share a draggable divider")
        tv.see(dy.id)
        app.update()
        x, y, w, h = tv.bbox(dy.id, "plan")
        tv.event_generate("<Button-1>", x=x + w // 2, y=y + h // 2)
        app.update()
        self.assertFalse(next(t for t in settings.load_targets() if t.name == "DY Peg").enabled)
        self.assertEqual(tv.set(dy.id, "plan"), "☐")
        page.set_priority(dy.id, "High")
        self.assertEqual(next(t for t in settings.load_targets() if t.name == "DY Peg").priority, "High")
        toi = next(t for t in app.targets if t.name == "TOI-2046 b")
        self.assertEqual(page.table_of(toi.id).group, "transit")
        self.assertIn("⚠ Tc ± 12.5 min", tt.set(toi.id, "source"))
        self.assertRegex(tt.set(toi.id, "period"), r"^1\.497\d\d d$")
        self.assertEqual(tt.set(toi.id, "night"), "Fri 23 Oct")
        self.assertEqual([tt.set(toi.id, c) for c in ("ingress", "mid", "egress")], ["21:12", "22:24", "23:36"])
        self.assertEqual(page.tables["campaign"].tree.set(
            next(t for t in app.targets if t.name == "HD 219134").id, "next"), "every night")
        page.select_target(toi.id)
        app.update()
        self.assertIn("depth 1.47 %", page.detail_var.get())
        self.assertIn("Tc ⚠ ± 12.5 min", page.detail_var.get())
        hd = next(t for t in app.targets if t.name == "HD 219134")
        self.assertEqual(page.table_of(hd.id).group, "campaign")
        page.select_target(hd.id)
        app.update()
        self.assertEqual(tt.selection(), (), "one box's selection clears the others")
        self.assertIn("1 of 5 this week", page.detail_var.get())
        self.assertIn("HD 219134 c", page.detail_var.get())
        self.assertIn("planned night", page.tables["transit"].box.cget("text"))
        # add: plain, unknown, needs a mode, several planets
        page.add("RR Lyr")
        self.pump()
        self.assertIn("RR Lyr", [t.name for t in app.targets])
        self.assertTrue(page.add_ind.var.get().startswith("✔ Added RR Lyr"))
        page.add("Nowhere 1")
        self.pump()
        self.assertTrue(page.add_ind.var.get().startswith("✖ SIMBAD does not know"))
        app.auto_answers = ["Star field (a campaign)"]
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
        page.find_suggested()
        self.pump()
        w2 = next(t for t in app.targets if t.name == "WASP-2 b")
        self.assertTrue(w2.suggested and not w2.enabled)
        self.assertEqual(page.table_of(w2.id).group, "transit", "suggestions share the Transits box")
        self.assertTrue(page.tables["transit"].tree.set(w2.id, "source").startswith("suggested"))
        page.toggle(w2.id)
        self.assertTrue(w2.enabled)
        page.select_target(w2.id)
        page.keep_selected()
        self.assertFalse(w2.suggested)
        self.assertEqual(page.table_of(w2.id).group, "transit")
        page.remove_ids({w2.id})
        self.assertNotIn("WASP-2 b", [t.name for t in settings.load_targets()])

    def test_edit_dialog(self):
        self.golden_home()
        app = self.open()
        hd = next(t for t in app.targets if t.name == "HD 219134")
        saved = []
        dlg = self.sa.EditTargetDialog(app, hd, on_save=saved.append, modal=False)
        self.assertIsInstance(dlg.sb, self.sa.ScrollBody, "the body scrolls; the buttons stay on screen")
        self.assertLessEqual(int(dlg.sb.canvas.cget("height")), int(app.winfo_screenheight() * 0.70))
        dlg.vars["min_alt"].set("40")
        dlg.vars["window"].set("21:00-02:30")
        dlg.vars["goal_per_week"].set("4")
        dlg.add_no_transit({"name": "HD 219134 d", "period": 46.86, "t0": 2457000.0, "t14": 0.2}, 10)
        dlg._return()                                       # Return = Save
        t = saved[0]
        kinds = [r["kind"] for r in t.rules]
        self.assertEqual(kinds.count("no_transit"), 3)
        self.assertIn({"kind": "min_alt", "value": 40.0}, t.rules)
        self.assertEqual(t.goal_per_week, 4)
        dlg = self.sa.EditTargetDialog(app, hd, on_save=saved.append, modal=False)
        self.assertEqual(dlg.vars["cycle_s"].get(), "")
        dlg.vars["cycle_s"].set("33")
        self.assertTrue(dlg.save() is not False, dlg.msg_var.get())
        self.assertEqual(saved[-1].cycle_s, 33.0)
        dlg = self.sa.EditTargetDialog(app, saved[-1], on_save=saved.append, modal=False)
        self.assertEqual(dlg.vars["cycle_s"].get(), "33")
        dlg.vars["cycle_s"].set("0.5")                          # shorter than the 0.75 s exposure
        self.assertFalse(dlg.save())
        self.assertIn("Seconds between frames", dlg.msg_var.get())
        dlg.win.destroy()
        for key, value, start in (("window", "late", "✖ Not saved: Only between"),
                                  ("goal_per_week", "2.5", "✖ Not saved: Goal must be a whole number"),
                                  ("block_min", "", "✖ Not saved: Block length is empty")):
            dlg = self.sa.EditTargetDialog(app, hd, on_save=saved.append, modal=False)
            dlg.vars[key].set(value)
            self.assertFalse(dlg.save())
            self.assertTrue(dlg.msg_var.get().startswith(start), dlg.msg_var.get())
            dlg.win.focus_force()
            app.update()
            dlg.win.event_generate("<Escape>")              # Escape = Cancel
            app.update()
            self.assertFalse(dlg.win.winfo_exists())

    # ---- Schedule ----
    def build(self, start=date(2026, 10, 20), n=2):
        page = self.app.pages[2]
        self.app.show_page(2)
        page.build(start, n)
        self.pump()
        return page

    def test_schedule_build_draw_lock_rebuild(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        self.assertEqual(len(app.schedule.nights), 2, page.ind.var.get())
        self.assertEqual(len(page.tabs), 2)
        self.assertTrue(page.msg_var.get().startswith("✔ Built 2 nights"), page.msg_var.get())
        self.assertEqual(page.n_var.get(), "2")
        self.assertEqual(app.options["nights"], 2)
        tab = page.tabs[0]
        self.assertTrue(tab.tab_text().startswith("⚠ "), "warning mark first, no padding")
        self.assertFalse(tab.tab_text().endswith(" "))
        app.update()
        tab.redraw()
        self.assertIsNotNone(tab.plot, "the night plot is drawn")
        p = tab.plot
        texts = [t.get_text() for t in p.ax.texts + p.fig.texts]
        self.assertTrue(p.ax.get_xlabel().startswith("Local time (UTC"), p.ax.get_xlabel())
        self.assertTrue(any("Moon " in s and "%" in s for s in texts), texts)
        self.assertTrue(any(s.count(".") == 1 and s[:1] in "12" and len(s) == 4 for s in texts),
                        "airmass at the block ends")
        wtexts = [t.get_text() for t in p.axw.texts]
        self.assertTrue(any("⚠ dew from" in s for s in wtexts), wtexts)
        self.assertGreater(len(tab.table.get_children()), 3)
        first = tab.table.get_children()[0]
        self.assertTrue(tab.table.tag_has("modeV", first) or tab.table.tag_has("modeT", first))
        self.assertFalse(self.sa.scrolls_itself(tab.table), "the wheel over the nightly table scrolls the page")
        for tbl in app.pages[1].tables.values():
            self.assertTrue(self.sa.scrolls_itself(tbl.tree))
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
        self.build()
        kept = [b for b in page.night(0).blocks_obj() if b.target_id == b_before.target_id]
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].locked)
        self.assertAlmostEqual(kept[0].start, b_before.start, places=6)
        # edit times: the last block shortened → set by you, rebuilt night keeps it
        tab = page.tabs[0]
        i, last = tab.blocks()[-1]
        tz = app.site.tz
        new_end = sky.hhmm(last.start + 30 / 1440, tz)
        self.assertTrue(tab.set_times(i, sky.hhmm(last.start, tz), new_end))
        self.pump()
        b = next(b for b in page.night(0).blocks_obj() if b.target_id == last.target_id)
        self.assertTrue(b.set_by_user)
        self.assertEqual(sky.hhmm(b.end, tz), new_end)
        k = next(k for k, x in enumerate(page.night(0).blocks_obj()) if x.target_id == last.target_id)
        self.assertIn("set by you", page.tabs[0].table.set(str(k), "target"))
        self.assertTrue(tab.act_var.get().startswith(("✔", "⚠")), tab.act_var.get())
        # move a flexible block to the other night
        tab = page.tabs[0]
        j, blk = next((k, x) for k, x in tab.blocks() if not x.fixed and not x.locked and not x.set_by_user
                      and x.status == "planned")
        tab.move_block(j, 1)
        self.pump()
        self.assertTrue(any(x.target_id == blk.target_id and x.set_by_user for x in page.night(1).blocks_obj()))
        self.assertFalse(any(x.target_id == blk.target_id and abs(x.start - blk.start) < 1e-6
                             for x in page.night(0).blocks_obj()))

    def test_edit_times_refuses_overlap_and_outside_night(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        tab = page.tabs[0]
        tz = app.site.tz
        i0, b1 = tab.blocks()[0][0], tab.blocks()[1][1]
        tab.select_block(tab.blocks()[1][0])
        tab.lock_selected()                                  # only kept blocks (locked, fixed, marked) block it
        n_calls = len(self.calls["build_night"])
        self.assertFalse(tab.set_times(i0, sky.hhmm(b1.start, tz), sky.hhmm(b1.end, tz)))
        self.assertTrue(tab.act_var.get().startswith("✖"))
        self.assertIn(f"overlaps {b1.name} {sky.hhmm(b1.start, tz)}–{sky.hhmm(b1.end, tz)}", tab.act_var.get())
        self.assertFalse(tab.set_times(i0, "14:00", "15:00"))
        self.assertIn("outside the night", tab.act_var.get())
        self.assertEqual(len(self.calls["build_night"]), n_calls, "nothing rebuilt")

    def test_failed_rebuild_changes_nothing(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        before = json.dumps(app.schedule.to_dict(), sort_keys=True)
        good = self.fakes["planner"].build_night

        def boom(*a, **k):
            raise RuntimeError("planner broke")
        self.fakes["planner"].build_night = boom
        tab = page.tabs[0]
        i, last = tab.blocks()[-1]
        tz = app.site.tz
        self.assertTrue(tab.set_times(i, sky.hhmm(last.start, tz), sky.hhmm(last.start + 20 / 1440, tz)))
        self.pump()
        self.assertTrue(tab.act_var.get().startswith("✖"), tab.act_var.get())
        j, blk = next((k, x) for k, x in tab.blocks() if not x.fixed)
        tab.move_block(j, 1)
        self.pump()
        self.assertEqual(json.dumps(app.schedule.to_dict(), sort_keys=True), before)
        self.assertEqual(json.dumps(settings.load_schedule().to_dict(), sort_keys=True), before)
        self.fakes["planner"].build_night = good
        # a fixed block cannot move
        nd = page.night(0)
        bl = nd.blocks_obj()
        bl[j].fixed = True
        nd.blocks = [x.to_dict() for x in bl]
        page.set_night(0, nd)
        tab.select_block(j)
        self.assertTrue(str(tab.move_btn.state()).find("disabled") >= 0)
        self.assertFalse(tab.move_block(j, 1))
        self.assertTrue(tab.act_var.get().startswith("✖"))

    def test_busy_build_refuses_edits_and_keeps_live_changes(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        tab = page.tabs[0]
        i, b = tab.blocks()[2]
        page.build(date(2026, 10, 20), 2)                 # running now
        self.assertTrue(page.busy)
        self.assertIn("disabled", page.build_btn.state())
        tab.select_block(i)
        tab.lock_selected()
        self.assertEqual(tab.act_var.get(), page.BUSY)
        n_jobs = app.jobs.active
        page.build(date(2026, 10, 20), 2)                 # a second click does not start another build
        self.assertEqual(app.jobs.active, n_jobs)
        # an edit that reached the live schedule during the build (e.g. from another window) is merged
        app.schedule.nights[0]["blocks"][i]["locked"] = True
        app.schedule.nights[0]["blocks"][i]["start"] += 5 / 1440
        app.schedule.nights[0]["blocks"][i]["end"] += 5 / 1440
        moved = dict(app.schedule.nights[0]["blocks"][i])
        self.pump()
        self.assertFalse(page.busy)
        got = [x for x in page.night(0).blocks_obj() if x.target_id == moved["target_id"]]
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].locked)
        self.assertAlmostEqual(got[0].start, moved["start"], places=6)
        self.assertEqual(page.ind.var.get(), "✔ Done")

    def test_plan_this_instead(self):
        self.golden_home()
        app = self.open()
        page = self.build(n=1)
        tab = page.tabs[0]
        ok = tab.plan_instead_ok
        self.assertTrue(ok({"reason": "collides with HAT-P-23 b; next chance 27 Oct"}))
        self.assertTrue(ok({"reason": "no free stretch of 45 min left tonight (longest 9 min)"}))
        self.assertFalse(ok({"reason": "collides with your locked block K2-29 b"}))
        self.assertFalse(ok({"reason": "below your limits (altitude 22° at 21:00)"}))
        tid = app.targets[0].id
        tab.plan_instead(tid, app.targets[0].name)
        self.pump()
        self.assertEqual(self.calls["build_night"][-1]["prefer"], {tid})
        self.assertTrue(tab.act_var.get().startswith("✔ Rebuilt with"), tab.act_var.get())
        off = Target(name="Ghost", kind="variable", mode="V", ra=10.0, dec=10.0, enabled=False)
        app.targets.append(off)
        tab.plan_instead(off.id, "Ghost")
        self.pump()
        self.assertTrue(tab.act_var.get().startswith("✖ Still didn't fit"), tab.act_var.get())

    def test_seven_nights_draw_only_the_visible_tab(self):
        """Regression: a 7-night schedule froze the UI (every hidden tab redrew on every resize)."""
        self.golden_home()
        app = self.open()
        counts = {}
        orig = self.sa.NightTab.draw_plot

        def counting(tab):
            counts[tab.idx] = counts.get(tab.idx, 0) + 1
            return orig(tab)
        self.sa.NightTab.draw_plot = counting
        try:
            page = self.build(n=7)
            self.settle()
            self.assertEqual(len(page.tabs), 7)
            self.assertEqual(set(counts), {0}, f"only the shown tab draws: {counts}")
            self.assertLessEqual(counts[0], 2)
            self.assertEqual(sum(1 for t in page.tabs if t.built), 1, "hidden tabs are built when first shown")
            counts.clear()
            app.geometry("1300x880+0+0")
            self.settle()
            self.assertLessEqual(sum(counts.values()), 2, f"one redraw per resize: {counts}")
            self.assertEqual(set(counts) - {0}, set())
            counts.clear()
            page.nb.select(3)
            self.settle()
            self.assertEqual(set(counts), {3})
            self.assertLessEqual(counts[3], 2)
        finally:
            self.sa.NightTab.draw_plot = orig

    def test_rereview_keeps_future_nights(self):
        self.golden_home()
        app = self.open()
        tonight = app.tonight()
        page = self.build(start=tonight + timedelta(days=2), n=3)
        dates = [n["date"] for n in app.schedule.nights]
        self.assertEqual(page.review_start(), tonight + timedelta(days=2))
        page.rereview()
        self.pump()
        self.assertEqual([n["date"] for n in app.schedule.nights], dates, "no future night dropped")
        # a schedule that started before tonight rolls forward with its length
        self.build(start=tonight - timedelta(days=2), n=3)
        nd = page.night(0)
        bl = nd.blocks_obj()
        bl[0].locked = True
        nd.blocks = [x.to_dict() for x in bl]
        page.set_night(0, nd)
        app.auto_answers = [False]                     # "locked blocks on past nights": cancel
        page.rereview()
        self.assertFalse(page.busy)
        self.assertEqual(app.schedule.nights[0]["date"], (tonight - timedelta(days=2)).isoformat())
        page.rereview()                                 # testing: confirmed
        self.pump()
        self.assertEqual(app.schedule.nights[0]["date"], tonight.isoformat())
        self.assertEqual(len(app.schedule.nights), 3)
        self.assertTrue(app.schedule.history)

    def test_forecast_messages_and_typed_start(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        page.start_var.set("2026-10-25")
        self.assertEqual(page.chosen_start(), date(2026, 10, 25))
        page.start_var.set("25 Oct 2026")
        self.assertEqual(page.chosen_start(), date(2026, 10, 25))
        page.start_var.set("soon")
        self.assertIsNone(page.chosen_start())
        page.build()
        self.assertTrue(page.ind.var.get().startswith("✖ Type the first night"))
        src = self.fakes["weather"].SOURCES["Open-Meteo"]
        real = src.fetch

        def no_hours(self_, site, start, n, options=None):
            return {(start + timedelta(days=k)).isoformat(): {"ok": False, "note": "Open-Meteo did not answer "
                    "(HTTP 503)", "hours": [], "summary": "No forecast"} for k in range(n)}
        src.fetch = no_hours
        self.build(n=1)
        self.assertTrue(page.msg_var.get().startswith("⚠ Built 1 night"))
        self.assertIn("built without weather", page.msg_var.get())

        def partial(self_, site, start, n, options=None):
            out = real(self_, site, start, n, options)
            out[start.isoformat()]["note"] = "using the forecast from 5 h ago"   # a stale copy is still ok=True
            out[start.isoformat()]["ok"] = True
            return out
        src.fetch = partial
        self.build(n=1)
        self.assertIn("Forecast: using the forecast from 5 h ago", page.msg_var.get())
        self.assertNotIn("without weather", page.msg_var.get())
        src.fetch = real

    def test_night_graph_grows_with_window(self):
        """The night plot gets taller with the window (between its min and max: it takes the spare height instead of
        leaving empty space below) and is re-rendered at the new size; at a small window it keeps its minimum."""
        self.golden_home()
        app = self.open()
        page = self.build(n=1)
        tab = page.tabs[0]
        lh = tab.lh
        w = tab.plot_widget()
        self.assertIsNotNone(w, "matplotlib is installed for the tests")
        app.geometry("1100x620+0+0")
        self.settle(0.6)
        small = int(w.cget("height"))
        self.assertGreaterEqual(small, int(lh * tab.GRAPH_LINES[0]))
        app.geometry("1400x1400+0+0")
        self.settle(0.6)
        big = int(w.cget("height"))
        self.assertGreater(big, small, "the plot grows with the window")
        self.assertLessEqual(big, lh * tab.GRAPH_LINES[1] + 1)
        self.assertEqual(tab._plot_size, (int(w.winfo_width()), int(w.winfo_height())), "rendered at the new size")
        fw, fh = tab.mpl_canvas.figure.get_size_inches() * tab.mpl_canvas.figure.dpi
        self.assertLessEqual(abs(fh - w.winfo_height()), 2, "the figure follows the widget")
        # the free height goes to the plot: the tab's contents end near the bottom of the visible area
        view_h = int(tab.body.canvas.winfo_height())
        used = int(tab.body.inner.winfo_reqheight())
        if big < lh * tab.GRAPH_LINES[1]:
            self.assertLessEqual(abs(view_h - used), px_tol(app), (view_h, used))

    def _sandbox_downloads(self):
        downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(downloads, exist_ok=True)
        real = settings.downloads_dir
        settings.downloads_dir = lambda: downloads
        self.addCleanup(lambda: setattr(settings, "downloads_dir", real))
        return downloads

    def test_export_night_files_folder_and_fallback(self):
        self.golden_home()
        app = self.open()
        self.build(n=1)
        app.show_page(3)
        page = app.pages[3]
        downloads = self._sandbox_downloads()
        page.refresh()
        iso = "2026-10-20"
        self.assertIn(iso, page.nights_ui)
        self.assertEqual(set(page.nights_ui[iso]["buttons"]), {"asiair", "nina", "copy"},
                         "per night: Export to ASIAIR, Export to NINA, Copy")
        self.assertIn(downloads, page.folder_var.get(), "no folder chosen: Downloads")
        # ASIAIR: to Downloads, full path in a ✔ line, Open folder / Open in Notepad, contents behind a toggle
        page.nights_ui[iso]["buttons"]["asiair"].invoke()
        path = page.results[(iso, "asiair")]["path"]
        self.assertEqual(path, os.path.join(downloads, "SHOBS-SCHED_ASIAIR_2026-10-20.csv"))
        self.assertTrue(os.path.exists(path))
        msg = page.nights_ui[iso]["asiair"]["label"].cget("text")
        self.assertTrue(msg.startswith("✔ ASIAIR: saved"), msg)
        self.assertIn(path, msg)
        self.assertEqual(page.open_folder(path), downloads)
        self.assertEqual(page.open_in_notepad(path), path)
        self.assertIsNone(page.nights_ui[iso]["asiair"]["contents"], "no raw text box by default")
        self.assertTrue(page.toggle_contents(iso, "asiair"))
        shown = page.nights_ui[iso]["asiair"]["contents"].get("1.0", "end")
        self.assertTrue(shown.startswith("Pane, RA, DEC"), shown)
        self.assertFalse(page.toggle_contents(iso, "asiair"))
        # a chosen folder (e.g. iCloud) is remembered and used
        cloud = os.path.join(self.tmp, "iCloudDrive")
        os.makedirs(cloud)
        real_ask = self.sa.filedialog.askdirectory
        self.sa.filedialog.askdirectory = lambda **kw: cloud
        try:
            self.assertEqual(page.choose_folder(), cloud)
        finally:
            self.sa.filedialog.askdirectory = real_ask
        self.assertEqual(settings.load_settings()["asiair_dir"], cloud)
        self.assertEqual(page.export_night(iso, "asiair"), os.path.join(cloud, "SHOBS-SCHED_ASIAIR_2026-10-20.csv"))
        # NINA goes to Downloads
        self.assertEqual(page.export_night(iso, "nina"), os.path.join(downloads, "SHOBS-SCHED_NINA_2026-10-20.csv"))
        self.assertTrue(page.msg_var.get().startswith("✔ NINA: saved"), page.msg_var.get())
        # the folder vanished: ⚠ and the file goes to Downloads
        shutil.rmtree(cloud)
        page.refresh()
        self.assertIn("not found", page.folder_var.get())
        p2 = page.export_night(iso, "asiair")
        self.assertEqual(p2, os.path.join(downloads, "SHOBS-SCHED_ASIAIR_2026-10-20.csv"))
        self.assertTrue(page.msg_var.get().startswith("⚠ ASIAIR: saved"), page.msg_var.get())
        page.use_downloads()
        self.assertEqual(settings.load_settings()["asiair_dir"], "")

    def test_export_status_dropped_when_night_rebuilt(self):
        self.golden_home()
        app = self.open()
        self.build(n=2)
        self._sandbox_downloads()
        app.show_page(3)
        page = app.pages[3]
        for v in page.night_vars.values():
            v.set(True)
        a, b = "2026-10-20", "2026-10-21"
        page.export_night(a, "asiair")
        page.export_night(b, "nina")
        app.show_page(3)                                       # nothing changed: both kept
        self.assertIn((a, "asiair"), page.results)
        self.assertIn((b, "nina"), page.results)
        # the 20th is rebuilt differently (a block moves): its "✔ saved" goes, the 21st's stays
        app.schedule.nights[0]["blocks"][0]["start"] += 0.01
        app.schedule_changed()
        app.show_page(3)
        self.assertNotIn((a, "asiair"), page.results)
        self.assertIn((b, "nina"), page.results)
        self.assertNotIn("asiair", page.nights_ui[a], "no stale status line on screen")
        self.assertIn("nina", page.nights_ui[b])
        # NINA always to Downloads, even with a remembered Save CSV folder from v1.0.0
        other = os.path.join(self.tmp, "old_save_csv_folder")
        os.makedirs(other)
        app.settings["export_dir"] = other
        self.assertEqual(os.path.dirname(page.export_night(b, "nina")), os.path.join(self.tmp, "Downloads"))

    def test_obsplan_name_stale_bar_and_past_schedule(self):
        self.golden_home()
        app = self.open()
        tz = app.site.tz
        real = sky.now_jd
        sky.now_jd = lambda: sky.local_jd(date(2026, 10, 20), 21, 0, tz)      # the evening of Tue 20 Oct
        app._info_cache.clear()
        try:
            self.assertEqual(self.sa.STEPS[3], "OBSPlan")
            self.build(start=date(2026, 10, 20), n=2)
            self._sandbox_downloads()
            app.show_page(3)
            page = app.pages[3]
            self.assertEqual(app.title_var.get(), "OBSPlan")
            self.assertFalse(page.warn_lbl.winfo_manager(), "a fresh plan: no ⚠ bar")
            self.assertTrue(page.export_night("2026-10-20", "asiair"))
            self.assertTrue(page.msg_var.get().startswith("✔ ASIAIR"))
            # Setup changed since the build: ⚠ bar, Print and Export still run but say ⚠
            app.settings_changed()
            app.show_page(3)
            self.assertTrue(page.warn_lbl.winfo_manager())
            self.assertEqual(page.warn_var.get(), page.STALE)
            self.assertEqual(page.warn_lbl.cget("style"), "Warn.TLabel")
            path = page.export_night("2026-10-20", "nina")
            self.assertTrue(path and os.path.exists(path))
            self.assertTrue(page.msg_var.get().startswith("⚠ Plan out of date"), page.msg_var.get())
            self.assertIn(path, page.msg_var.get())
            self.assertTrue(page.print_card())
            self.assertTrue(page.page_msg_var.get().startswith("⚠ Plan out of date"), page.page_msg_var.get())
            # rebuilt: the bar goes
            self.build(start=date(2026, 10, 20), n=2)
            app.show_page(3)
            self.assertFalse(page.warn_lbl.winfo_manager())
            # three days later every night has passed: Schedule and OBSPlan both say so, tabs marked (past)
            sky.now_jd = lambda: sky.local_jd(date(2026, 10, 23), 21, 0, tz)
            app._info_cache.clear()
            app.show_page(2)
            sch = app.pages[2]
            self.assertEqual(sch.msg_var.get(), "⚠ Last built for Wed 21 Oct (past): press Build schedule.")
            self.assertEqual([sch.nb.tab(t.frame, "text").endswith("(past)") for t in sch.tabs], [True, True])
            self.assertTrue(sch.tabs[0].past_lbl.winfo_manager())
            self.assertIn("Last built for Wed 21 Oct (past)", sch.tabs[0].past_lbl.cget("text"))
            app.show_page(3)
            self.assertIn("Last built for Wed 21 Oct (past)", page.warn_var.get())
            # one night still ahead: the schedule is live; only the past night is marked
            sky.now_jd = lambda: sky.local_jd(date(2026, 10, 21), 21, 0, tz)
            app._info_cache.clear()
            app.show_page(2)
            self.assertFalse(sch.msg_var.get().startswith("⚠ Last built"))
            self.assertEqual([sch.nb.tab(t.frame, "text").endswith("(past)") for t in sch.tabs], [True, False])
            app.show_page(3)
            self.assertFalse(page.warn_lbl.winfo_manager())
        finally:
            sky.now_jd = real

    def test_export_nights_flow_and_plots_tick(self):
        self.golden_home()
        app = self.open()
        self.build(n=7)
        app.geometry("700x900+0+0")
        app.show_page(3)
        self.settle()
        page = app.pages[3]
        self.assertGreater(len(page._check_rows_frames), 1, "seven nights wrap onto more rows when narrow")
        fw = page.checks.winfo_width()
        for w in page.check_widgets:
            self.assertLessEqual(w.winfo_rootx() - page.checks.winfo_rootx() + w.winfo_width(), fw + 1,
                                 w.cget("text"))
        # the notes column wraps to the room left: nothing past the card's right edge
        cw = page.body.canvas.winfo_width()
        for iso, ui in page.nights_ui.items():
            for w in ui["box"].winfo_children():
                self.assertLessEqual(w.winfo_rootx() - page.body.canvas.winfo_rootx() + w.winfo_width(), cw + 1)
        # Include night plots: remembered; off → no plots asked for
        asked = []
        page.card_plots = lambda nights: asked.append(len(nights)) or {n.date: b"png" for n in nights}
        self.assertTrue(page.plots_var.get())
        page.print_card()
        self.assertEqual(len(asked), 1)
        page.plots_var.set(False)
        page._plots_changed()
        self.assertIs(settings.load_settings()["card_plots"], False)
        path = page.print_card()
        self.assertEqual(len(asked), 1, "plots off: not drawn")
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("<img", fh.read())

    def test_downloads_dir_fallbacks(self):
        real = os.path.expanduser
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        os.path.expanduser = lambda p: p.replace("~", home, 1)
        real_win = settings._windows_downloads
        settings._windows_downloads = lambda: None          # on Windows too: test the fallbacks
        try:
            self.assertEqual(settings.downloads_dir(), home, "no Downloads folder: the home folder")
            os.makedirs(os.path.join(home, "Downloads"))
            self.assertEqual(settings.downloads_dir(), os.path.join(home, "Downloads"))
        finally:
            os.path.expanduser = real
            settings._windows_downloads = real_win
        self.assertEqual(settings.default_settings()["export_dir"], "")

    def test_export_clipboard_and_card(self):
        self.golden_home()
        app = self.open()
        self.build(n=1)
        app.show_page(3)
        page = app.pages[3]
        text = page.card_text()
        self.assertIn("Tue 20 Oct", text, "the on-screen card shows the night")
        self.assertIn("Time", text)
        self.assertIn("Notes", text)
        self.assertIn("Times: Local time (UTC−4)", text)
        page.copy_csv()
        clip = app.clipboard_get()
        self.assertTrue(clip.startswith("Pane, RA, DEC"))
        n = len(clip.splitlines()) - 1
        self.assertEqual(page.msg_var.get().split(".")[0], f"✔ Copied {n} targets for Tue 20 Oct")
        self.assertIn(("2026-10-20", "copy"), page.results)
        path = page.print_card()
        self.assertTrue(path, page.page_msg_var.get())
        self.assertTrue(path and os.path.exists(path) and path.startswith(settings.cache_dir()))
        self.assertTrue(page.page_msg_var.get().startswith("✔ The plan card is open"))
        with open(path, encoding="utf-8") as fh:
            self.assertIn("data:image/png;base64,", fh.read(), "the night plot is on the printed card")
        for v in page.night_vars.values():
            v.set(False)
        self.assertIn("Tick one or more nights", page.card_text())
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
        self.sa._MODULES["planner"] = self.fakes["planner"]

        def nodark(d, site, optics, targets, options, **kw):
            return NightPlan(date=d.isoformat(), notes=["✖ No dark time tonight: the Sun stays above −18°"],
                             didnt_fit=[{"target_id": "", "name": "Star", "reason": "no dark time tonight"}])
        self.fakes["planner"].build_night = nodark
        self.fakes["planner"].build_schedule = lambda start, n, *a, **k: Schedule(
            nights=[nodark(start, None, None, [], {}).to_dict()])
        app.pages[2].build(date(2026, 6, 20), 1)
        self.pump()
        tab = app.pages[2].tabs[0]
        lines = [w.cget("text") for w in tab.notes_box.winfo_children()]
        self.assertTrue(lines[0].startswith("✖ No dark time tonight"), lines)
        self.assertEqual(tab.notes_box.winfo_children()[0].cget("style"), "Bad.TLabel")
        tab.redraw()

    # ---- final review (marks, tonight, the star itself, history, icon, notes, stale setup) ----
    def test_marks_survive_rebuild_without_restamping(self):
        self.golden_home()
        app = self.open()
        page = self.build()
        tab = page.tabs[0]
        i, b = tab.blocks()[1]
        tab.select_block(i)
        tab.set_status("clouded")
        self.build()
        n0 = page.night(0).blocks_obj()
        self.assertEqual([x.status for x in n0].count("clouded"), 1, [(x.name, x.status) for x in n0])
        self.assertEqual([x.target_id for x in n0].count(b.target_id), 1)
        page.tabs[0].plan_instead(app.targets[0].id, app.targets[0].name)
        self.pump()
        n0 = page.night(0).blocks_obj()
        self.assertEqual([x.status for x in n0].count("clouded"), 1)
        self.assertEqual([x.target_id for x in n0].count(b.target_id), 1)

    def test_tonight_follows_sunrise(self):
        self.golden_home()
        app = self.open()
        tz = app.site.tz
        real = sky.now_jd
        try:
            for (d, hh), want in (((22, 3), 21), ((22, 9), 22), ((22, 20), 22), ((23, 1), 22)):
                j = sky.local_jd(date(2026, 10, d), hh, 0, tz)
                sky.now_jd = lambda j=j: j
                app._info_cache.clear()
                self.assertEqual(app.tonight(), date(2026, 10, want), f"{d} Oct {hh:02d}:00")
            sky.now_jd = lambda: sky.local_jd(date(2026, 10, 22), 9, 0, tz)
            app._info_cache.clear()
            self.build(start=date(2026, 10, 21), n=2)
            app.show_page(3)
            self.assertEqual({k: v.get() for k, v in app.pages[3].night_vars.items()},
                             {"2026-10-21": False, "2026-10-22": True}, "Export ticks follow tonight")
            self.assertEqual(app.pages[2].review_start(), date(2026, 10, 22))
        finally:
            sky.now_jd = real

    def test_add_the_star_itself_and_change_group(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        app.auto_answers = [self.sa.STAR_ITSELF, "Campaign, reduced in Variables (V)"]
        page.add("Kepler-9")
        self.pump()
        t = next(t for t in app.targets if t.name == "Kepler-9")
        self.assertEqual((t.kind, t.mode, t.source), ("campaign", "V", "SIMBAD"))
        self.assertAlmostEqual(t.ra, 348.32)
        self.assertEqual(page.table_of(t.id).group, "campaign")
        app.auto_answers = ["Variable star (V)"]
        page.add("HD 1")                                  # NeedsMode offers the same kinds
        self.pump()
        self.assertEqual(next(x for x in app.targets if x.name == "HD 1").kind, "variable")
        saved = []
        dlg = self.sa.EditTargetDialog(app, t, on_save=saved.append, modal=False)
        dlg.vars["kind"].set("Variable")
        dlg.vars["mode"].set("Discovery (D)")
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        self.assertEqual((saved[0].kind, saved[0].mode), ("variable", "D"))
        page._edited(saved[0])
        self.assertEqual(page.table_of(t.id).group, "variable")

    def test_build_later_start_rolls_marks_into_history(self):
        import planner as real_planner
        self.fakes["planner"].roll_forward = real_planner.roll_forward
        self.golden_home()
        app = self.open()
        page = self.build(start=date(2026, 10, 20), n=2)
        tab = page.tabs[0]
        i, b = tab.blocks()[0]
        tab.select_block(i)
        tab.set_status("done")
        self.build(start=date(2026, 10, 21), n=2)
        self.assertEqual([n["date"] for n in app.schedule.nights], ["2026-10-21", "2026-10-22"])
        self.assertIn({"date": "2026-10-20", "target_id": b.target_id, "status": "done"},
                      [{k: h[k] for k in ("date", "target_id", "status")} for h in app.schedule.history])

    def test_icon_errors_and_notes(self):
        self.golden_home()
        app = self.open()
        self.assertTrue(hasattr(app, "_icon_image"), "the window has SHOBS-SCHED's icon")
        self.assertEqual(self.sa.APP_USER_MODEL_ID, "ShilohHill.SHOBS-SCHED")
        try:
            raise ValueError("bad value in a callback")
        except ValueError as exc:
            app.report_callback_exception(ValueError, exc, exc.__traceback__)
        self.assertTrue(app.status_var.get().startswith("✖ Something went wrong: bad value in a callback"))
        with open(settings.log_path(), encoding="utf-8") as fh:
            self.assertIn("Traceback", fh.read())
        page = app.pages[1]
        page.add("WASP-9 b")
        self.pump()
        self.assertTrue(page.add_ind.var.get().startswith("⚠ Added WASP-9 b (NEA). The Archive did not answer"),
                        page.add_ind.var.get())
        self.calls["catalog_note"] = "the catalog did not answer; using the saved copy from 9 days ago"
        page.find_suggested()
        self.pump()
        self.assertIn("Catalog: The catalog did not answer; using the saved copy from 9 days ago.",
                      page.find_ind.var.get())
        self.assertTrue(page.find_ind.var.get().startswith("⚠"))

    def test_setup_change_marks_schedule_stale_and_rows_stay_put(self):
        self.golden_home()
        app = self.open()
        page = self.build(n=1)
        app.show_page(0)
        app.pages[0].vars["site.min_alt"].set("33")
        self.assertTrue(app.pages[0].save())
        app.show_page(2)
        self.assertEqual(page.msg_var.get(), page.STALE)
        self.build(n=1)
        self.assertNotEqual(page.msg_var.get(), page.STALE)
        app.show_page(2)
        self.assertNotEqual(page.msg_var.get(), page.STALE)
        tp = app.pages[1]
        app.show_page(1)
        dy = next(t for t in app.targets if t.name == "DY Peg")
        before = tp.table_of(dy.id).tree.index(dy.id)
        tp.toggle(dy.id)
        self.assertEqual(tp.table_of(dy.id).tree.index(dy.id), before, "a ticked/unticked row keeps its place")

    def test_edit_times_over_a_flexible_block_replans_it(self):
        self.golden_home()
        app = self.open()
        page = self.build(n=1)
        tab = page.tabs[0]
        tz = app.site.tz
        (i0, b0), b1 = tab.blocks()[0], tab.blocks()[1][1]
        self.assertTrue(tab.set_times(i0, sky.hhmm(b1.start, tz), sky.hhmm(b1.end, tz)))
        self.pump()
        n0 = page.night(0).blocks_obj()
        mine = next(x for x in n0 if x.target_id == b0.target_id)
        self.assertTrue(mine.set_by_user)
        self.assertFalse(any(x is not mine and x.start < mine.end and mine.start < x.end for x in n0))

    def test_shobs_p_telescope_name_is_not_the_site_name(self):
        p = os.path.join(self.tmp, "shobs_p.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": 35.303, "site_lon": -81.9407, "telescope_name": "14in RC"}, fh)
        os.environ["SHOBS_P_SETTINGS"] = p
        app = self.open()
        self.assertEqual(app.site.name, "")
        self.assertEqual(app.optics.telescope, "14in RC")
        self.assertEqual(app.pages[0].vars["optics.telescope"].get(), "14in RC")


def hd_info(note="", matched="name"):
    """A host_planets answer for HD 219134 (b and c transit, d does not)."""
    def entry(letter, usable):
        name = f"HD 219134 {letter}"
        return {"name": name, "transits": usable, "usable": usable, "why": "" if usable else "does not transit",
                "planet": {"name": name, "period": 3.09 if letter == "b" else 6.76, "t0": 2460599.5, "t14": 0.05,
                           "reference": "Archive"} if usable else None}
    return {"host": "HD 219134", "matched": matched, "planets": [entry("b", True), entry("c", True),
                                                                   entry("d", False)], "note": note}


class TestPlanetHosts(Base):
    """A star's planets at the Archive: listed in Edit, offered when the star is added, bare letters; moving a target
    between groups in Edit; the Edit window sized to its contents."""

    def setUp(self):
        super().setUp()
        tg = self.fakes["targets"]
        self.host_calls = []
        self.host_answer = hd_info()

        def host_planets(name, ra=None, dec=None, vmag=None, refresh=False):
            self.host_calls.append((name, ra, dec))
            if isinstance(self.host_answer, Exception):
                raise self.host_answer
            return self.host_answer
        tg.host_planets = host_planets
        self.lookups = []
        real_lookup = tg.lookup_planet

        def lookup_planet(name, when_jd=None):
            self.lookups.append(name)
            return real_lookup(name)
        tg.lookup_planet = lookup_planet
        add = tg.add_target

        def add_target(name, when_jd=None):
            if name == "HD219134":
                raise tg.NeedsMode(name, 348.3208, 57.1683, 5.57, "HD 219134")
            if name in ("HD 219134 b", "HD 219134 c"):
                return Target(name=name, kind="transit", mode="T", ra=348.34, dec=57.17, source="NEA",
                              planet={"name": name, "period": 3.09, "t0": 2457126.7, "t14": 0.04})
            return add(name, when_jd)
        tg.add_target = add_target
        self.asked = []

    def record_questions(self, answers):
        real = self.sa.ask_choice
        answers = list(answers)

        def ask(app, title, message, choices, ok_text="OK"):
            self.asked.append((message, list(choices)))
            return answers.pop(0)
        self.sa.ask_choice = ask
        self.addCleanup(setattr, self.sa, "ask_choice", real)

    def dialog(self, t):
        saved = []
        dlg = self.sa.EditTargetDialog(self.app, t, on_save=saved.append, modal=False)
        self.pump()
        return dlg, saved

    @staticmethod
    def nt_names(rules):
        return [r["planet"]["name"] for r in rules if r.get("kind") == "no_transit"]

    def test_target_edit_marks_schedule_stale(self):
        """John 22 Oct: after editing a target the Schedule page showed the old plan with no hint to rebuild."""
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        page.build(n=1)
        app.jobs.wait()
        self.pump()
        self.assertFalse(page.setup_stale)
        app.targets_changed()
        app.show_page(2)
        self.pump()
        self.assertTrue(page.setup_stale)
        self.assertIn("targets changed", page.msg_var.get())
        page.build(n=1)
        app.jobs.wait()
        self.pump()
        self.assertFalse(page.setup_stale, "a build clears it")

    def test_removed_windows_stay_removed(self):
        """Review fix: a campaign whose planet windows were all removed on purpose is not re-ticked next time."""
        self.golden_home()
        self.open()
        star = Target(name="HD219134", kind="campaign", mode="V", ra=348.3208, dec=57.1683, vmag=5.57)
        dlg, saved = self.dialog(star)
        self.app.jobs.wait()
        self.pump()
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 b", "HD 219134 c"], "first time: pre-ticked")
        dlg.nt_list.selection_set(0, tk.END)
        dlg._nt_remove()
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        t = saved[-1]
        self.assertTrue(t.planets_checked)
        self.assertEqual(self.nt_names(t.rules), [])
        dlg2, saved2 = self.dialog(t)
        self.app.jobs.wait()
        self.pump()
        self.assertEqual(self.nt_names(dlg2.no_transit), [], "not re-added on the next Edit")
        self.assertTrue(dlg2.save(), dlg2.msg_var.get())
        self.assertEqual(self.nt_names(saved2[-1].rules), [])

    def test_planet_moved_out_of_transits_becomes_the_star(self):
        """Review fix: "HD 219134 b" moved to Campaigns is named for its star (planet letter dropped)."""
        self.golden_home()
        self.open()
        t = Target(name="HD 219134 b", kind="transit", mode="T", ra=348.34, dec=57.17, vmag=5.57,
                   planet={"name": "HD 219134 b", "period": 3.09, "t0": 2457126.7, "t14": 0.04})
        dlg, saved = self.dialog(t)
        dlg.vars["mode"].set(dict((v, k) for k, v in self.sa.MODE_CHOICES)["V"])
        self.pump()
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        self.assertEqual(saved[-1].kind, "campaign")
        self.assertEqual(saved[-1].name, "HD 219134")

    def test_edit_lists_planets_ticks_and_letters(self):
        self.golden_home()
        app = self.open()
        star = Target(name="HD219134", kind="campaign", mode="V", ra=348.3208, dec=57.1683, vmag=5.57)
        dlg, saved = self.dialog(star)
        self.assertEqual(self.host_calls, [("HD219134", 348.3208, 57.1683)], "by name and position, in a job")
        self.assertEqual({k: v.get() for k, v in dlg.pl_vars.items()},
                         {"HD 219134 b": True, "HD 219134 c": True, "HD 219134 d": False})
        boxes = {str(cb.cget("text")).split("  ")[0]: cb for cb in dlg.pl_checks.winfo_children()}
        self.assertTrue(boxes["HD 219134 d"].instate(["disabled"]), "non-transiting planets are greyed out")
        self.assertIn("does not transit", str(boxes["HD 219134 d"].cget("text")))
        self.assertTrue(dlg.pl_ind.var.get().startswith("✔ 3 known planets"), dlg.pl_ind.var.get())
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 b", "HD 219134 c"], "transiting: pre-ticked")
        boxes["HD 219134 c"].invoke()                       # untick c
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 b"])
        dlg.nt_name.set("c")                                # a bare letter = this star's planet c
        dlg._nt_add()
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 b", "HD 219134 c"])
        self.assertTrue(dlg.pl_vars["HD 219134 c"].get(), "the tick follows")
        self.assertEqual(self.lookups, [], "a looked-up planet needs no second lookup")
        dlg.nt_name.set("d")
        dlg._nt_add()
        self.assertTrue(dlg.nt_ind.var.get().startswith("✖ HD 219134 d: does not transit"), dlg.nt_ind.var.get())
        dlg.nt_name.set("HD219134 b")
        dlg._nt_add()
        self.assertTrue(dlg.nt_ind.var.get().startswith("⚠ HD219134 b is already"), dlg.nt_ind.var.get())
        dlg.nt_list.selection_set(0)
        dlg._nt_remove()
        self.assertFalse(dlg.pl_vars["HD 219134 b"].get())
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        self.assertEqual(self.nt_names(saved[0].rules), ["HD 219134 c"])

    def test_edit_keeps_typed_planets_no_planets_offline(self):
        self.golden_home()
        app = self.open()
        hd = next(t for t in app.targets if t.name == "HD 219134")
        dlg, saved = self.dialog(hd)
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 c", "HD 219134 b"], "nothing added twice")
        self.assertTrue(dlg.pl_vars["HD 219134 b"].get() and dlg.pl_vars["HD 219134 c"].get())
        self.assertTrue(dlg.save())
        c = next(r for r in saved[0].rules if r.get("kind") == "no_transit" and r["planet"]["name"].endswith("c"))
        self.assertEqual(c["planet"]["reference"], "ExoFOP", "the existing window keeps its own ephemeris")
        # a star the user unticked everything for keeps it that way: windows for other planets only
        star = Target(name="HD 1", kind="variable", mode="V", ra=10.0, dec=20.0)
        self.host_answer = {"host": "", "matched": "", "planets": [], "note": ""}
        dlg, saved = self.dialog(star)
        self.assertEqual(dlg.pl_ind.var.get(), "✔ No known planets at the NASA Exoplanet Archive.")
        dlg.nt_name.set("b")                                # no looked-up planets: the star's name + the letter
        dlg._nt_add()
        self.pump()
        self.assertEqual(self.lookups, ["HD 1 b"])
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 1 b"])
        self.host_answer = self.fakes["targets"].LookupError_(
            "The NASA Exoplanet Archive did not answer (HTTP 503) and there is no saved copy for HD 2. Try again later.")
        dlg, saved = self.dialog(Target(name="HD 2", kind="campaign", mode="V", ra=1.0, dec=2.0))
        self.assertTrue(dlg.pl_ind.var.get().startswith("✖ Couldn't reach the NASA Exoplanet Archive"),
                        dlg.pl_ind.var.get())
        self.assertIn("HTTP 503", dlg.pl_ind.var.get())
        self.assertEqual(dlg.pl_vars, {})
        self.assertTrue(dlg.save(), "a failed lookup never blocks saving")

    def test_edit_moves_between_groups(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        hd = next(t for t in app.targets if t.name == "HD 219134")
        dlg, saved = self.dialog(hd)
        self.assertFalse(dlg.eph_widgets[0].winfo_manager(), "no ephemeris rows for a campaign")
        dlg.vars["mode"].set("Transits (T)")
        self.assertEqual(dlg.vars["kind"].get(), "Transit", "the mode moves the group")
        self.assertEqual(dlg.eph_widgets[0].winfo_manager(), "grid")
        self.assertIn("add its planet HD 219134 b and HD 219134 c", dlg.msg_var.get())
        self.assertFalse(dlg.save(), "never an empty transit target")
        self.assertTrue(dlg.msg_var.get().startswith("✖ Not saved: A transit target needs its period and Tc"))
        dlg.vars["period"].set("3.5")
        dlg.vars["t0"].set("2460000.5")
        dlg.vars["t14"].set("2")
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        t = saved[0]
        self.assertEqual((t.kind, t.mode, t.elements), ("transit", "T", None))
        self.assertEqual((t.planet["period"], t.planet["name"]), (3.5, "HD 219134"))
        page._edited(t)
        self.assertEqual(page.table_of(t.id).group, "transit")
        # and back: a transit moved to Campaigns loses its ephemeris and is offered its star's planets
        tr = next(x for x in app.targets if x.id == t.id)
        n = len(self.host_calls)
        dlg, saved = self.dialog(tr)
        self.assertEqual(len(self.host_calls), n, "transit targets look up nothing until they become stars")
        dlg.vars["kind"].set("Campaign")
        self.pump()
        self.assertEqual(dlg.vars["mode"].get(), "Variables (V)", "the group moves the mode")
        self.assertEqual(self.host_calls[n:], [("HD 219134", tr.ra, tr.dec)], "a star now: its planets offered")
        self.assertEqual(self.nt_names(dlg.no_transit), ["HD 219134 c", "HD 219134 b"])
        self.assertIn("out of Transits", dlg.msg_var.get())
        self.assertTrue(dlg.save(), dlg.msg_var.get())
        self.assertEqual((saved[0].kind, saved[0].mode, saved[0].planet), ("campaign", "V", None))
        page._edited(saved[0])
        self.assertEqual(page.table_of(t.id).group, "campaign")
        # a mode change alone (as if the traces had not run) still moves the group
        dlg, saved = self.dialog(saved[0])
        dlg._syncing = True
        dlg.vars["mode"].set("Discovery (D)")
        dlg.vars["kind"].set("Transit")
        self.assertFalse(dlg.save())
        self.assertIn("Transits (T) goes with the Transit group", dlg.msg_var.get())
        dlg.vars["kind"].set("Campaign")
        self.assertTrue(dlg.save())
        self.assertEqual((saved[0].kind, saved[0].mode), ("campaign", "D"))

    def test_edit_transit_planet_lookup_is_not_done_for_transits(self):
        self.golden_home()
        app = self.open()
        k2 = next(t for t in app.targets if t.kind == "transit")
        self.dialog(k2)
        self.assertEqual(self.host_calls, [])

    def test_add_star_with_planets_offers_its_transiting_planets(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        app.show_page(1)
        sa = self.sa
        self.record_questions([sa.planet_pick("HD 219134 b"), "Campaign, reduced in Variables (V)"])
        page.add("HD219134")
        self.pump()
        msg, choices = self.asked[0]
        star = [c[0] for c in sa.KIND_CHOICES[:3]]
        self.assertEqual(choices, [sa.planet_pick("HD 219134 b"), sa.planet_pick("HD 219134 c")] + star)
        self.assertNotIn(sa.KIND_CHOICES[3][0], choices, "no empty transit target for a planet host")
        self.assertIn("HD 219134 b and HD 219134 c transit", msg)
        b = next(t for t in app.targets if t.name == "HD 219134 b")
        self.assertEqual(b.kind, "transit")
        page.add("HD219134")
        self.pump()
        t = next(t for t in app.targets if t.name == "HD219134")
        self.assertEqual((t.kind, t.mode), ("campaign", "V"))
        self.assertEqual(self.nt_names(t.rules), ["HD 219134 b", "HD 219134 c"])
        self.assertIn("no data while HD 219134 b and HD 219134 c transit", page.msg_var.get())
        self.assertEqual(self.host_calls[0], ("HD 219134", 348.3208, 57.1683))

    def test_add_star_when_planets_cannot_be_checked(self):
        self.golden_home()
        app = self.open()
        page = app.pages[1]
        self.host_answer = self.fakes["targets"].LookupError_("The NASA Exoplanet Archive did not answer (HTTP 503)")
        self.record_questions(["Variable star (V)"])
        page.add("HD219134")
        self.pump()
        msg, choices = self.asked[0]
        self.assertEqual(choices, [c[0] for c in self.sa.KIND_CHOICES])
        self.assertIn("could not be checked", msg)
        self.assertEqual(next(t for t in app.targets if t.name == "HD219134").kind, "variable")

    def test_planet_choice_offers_only_transiting_planets(self):
        self.golden_home()
        app = self.open()
        app.targets = [t for t in app.targets if t.name != "HD 219134"]
        page = app.pages[1]
        tg = self.fakes["targets"]
        exc = tg.PlanetChoice("HD 219134", ["HD 219134 b", "HD 219134 c", "HD 219134 d"])
        exc.transiting = {"HD 219134 b", "HD 219134 c"}
        self.record_questions([self.sa.STAR_ITSELF, "Discovery field (D)"])
        page._added("HD 219134", None, exc)
        self.pump()
        msg, choices = self.asked[0]
        self.assertEqual(choices, ["HD 219134 b", "HD 219134 c", self.sa.STAR_ITSELF])
        self.assertIn("HD 219134 d does not transit", msg)
        self.assertEqual(self.asked[1][1], [c[0] for c in self.sa.KIND_CHOICES[:3]], "the star itself: its kinds")
        self.assertIn("No data while HD 219134 b and HD 219134 c transit", self.asked[1][0])
        t = next(t for t in app.targets if t.name == "HD 219134")
        self.assertEqual((t.kind, t.mode), ("campaign", "D"))
        self.assertEqual(len(self.nt_names(t.rules)), 2)

    def test_non_transiting_planet_steers_to_its_star(self):
        self.golden_home()
        app = self.open()
        app.targets = [t for t in app.targets if t.name != "HD 219134"]
        page = app.pages[1]
        tg = self.fakes["targets"]
        exc = tg.NeedsMode("HD 219134 d", 348.3372, 57.1696, 5.57, "HD 219134 d")
        exc.source, exc.otype = "the NASA Exoplanet Archive", "a planet that does not transit"
        self.record_questions(["Variable star (V)"])
        page._added("HD 219134 d", None, exc)
        self.pump()
        msg, choices = self.asked[0]
        self.assertTrue(msg.startswith("HD 219134 d is a planet that does not transit, so it cannot be a transit"))
        self.assertNotIn(self.sa.KIND_CHOICES[3][0], choices)
        self.assertEqual(self.host_calls[0][0], "HD 219134")
        t = next(t for t in app.targets if t.name == "HD 219134" and t.kind == "variable")
        self.assertEqual(self.nt_names(t.rules), ["HD 219134 b", "HD 219134 c"])

    def test_edit_window_fits_its_contents_scaled(self):
        self.golden_home()
        app = self.open()
        for scaling in (1.3333 * 1.25, 1.3333 * 1.5):           # Windows 125 % and 150 % (the app is closed after)
            app.tk.call("tk", "scaling", scaling)
            hd = next(t for t in app.targets if t.name == "HD 219134")
            dlg, _saved = self.dialog(hd)
            self.settle(0.2)
            body = dlg.sb.inner
            self.assertGreaterEqual(dlg.sb.canvas.winfo_width(), body.winfo_reqwidth(),
                                    f"nothing cut off on the right at scaling {scaling:.2f}")
            self.assertGreaterEqual(body.winfo_width(), body.winfo_reqwidth())
            self.assertGreaterEqual(int(dlg.win.minsize()[0]), body.winfo_reqwidth(), "cannot be squeezed")
            self.assertLessEqual(dlg.win.winfo_height(), int(app.winfo_screenheight() * 0.90) + 1)
            # wider contents later (the moved-in ephemeris, a long Archive line) widen the window
            dlg.vars["kind"].set("Transit")
            dlg.show_planets(hd_info(note="the NASA Exoplanet Archive did not answer (HTTP 503); using the copy "
                                          "from 3 days ago", matched="position"))
            self.settle(0.2)
            self.assertGreaterEqual(dlg.sb.canvas.winfo_width(), body.winfo_reqwidth())
            self.assertTrue(dlg.pl_ind.var.get().startswith("⚠ 3 known planets"))
            self.assertIn("found by position as HD 219134", dlg.pl_ind.var.get())
            dlg.win.destroy()


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
            tab.redraw()
            self.assertIsNotNone(tab.plot)
            self.assertTrue(tab.plot.groups, "the blocks' targets are drawn")
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
        self.assertIn("(X ", row[3])                        # no mode column (1.1.0)
        # a Clouded-out mark: a rebuild keeps it once, and does not plan the target again that night
        tab = page.tabs[0]
        i, b = next((i, b) for i, b in tab.blocks() if not b.fixed and not b.locked)
        tab.select_block(i)
        tab.set_status("clouded")
        page.build(date(2026, 10, 22), 3)
        self.pump()
        n0 = page.night(0).blocks_obj()
        self.assertEqual([x.target_id for x in n0].count(b.target_id), 1, [(x.name, x.status) for x in n0])
        self.assertEqual([x.status for x in n0].count("clouded"), 1)
        # rebuilding one night alone gives what the full build gave (same history + earlier nights)
        before = [(x.target_id, round(x.start, 6), round(x.end, 6)) for x in page.night(2).blocks_obj()]
        self.assertTrue(page.rebuild_night(2))
        self.pump()
        self.assertEqual([(x.target_id, round(x.start, 6), round(x.end, 6)) for x in page.night(2).blocks_obj()],
                         before)
        app.show_page(3)
        app.update()
        app.pages[3].copy_csv()
        self.assertTrue(app.pages[3].msg_var.get().startswith("✔ Copied"), app.pages[3].msg_var.get())
        self.assertTrue(app.clipboard_get().startswith("Pane, RA, DEC"))
        self.assertIn("All times Local time (UTC−4)", app.pages[3].card_text())
        # per night, real files: ASIAIR (format unchanged) and NINA (CSV for NINA's Import targets)
        downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(downloads)
        real_dl = settings.downloads_dir
        settings.downloads_dir = lambda: downloads
        try:
            iso = app.pages[3].chosen()[0].date
            pa = app.pages[3].export_night(iso, "asiair")
            pn = app.pages[3].export_night(iso, "nina")
        finally:
            settings.downloads_dir = real_dl
        with open(pa, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("Pane, RA, DEC, Position Angle (East)"))
        with open(pn, encoding="utf-8", newline="") as fh:
            self.assertTrue(fh.read().startswith("Pane,RA,DEC,Position Angle (East)"))
        self.assertTrue(os.path.basename(pn).startswith("SHOBS-SCHED_NINA_"))
        # the usual camera angle goes into the list, and the page says so
        app.settings["optics"]["camera_angle_deg"] = 30.0
        app.pages[3].update_preview()
        self.assertIn("position angle 30.00°", app.pages[3].angle_var.get())
        app.pages[3].copy_csv()
        self.assertIn("Position angle 30.00°", app.pages[3].msg_var.get())
        self.assertEqual(app.clipboard_get().split("\n")[1].split(", ")[3], "30.00")

    def test_clouded_mark_not_copied_to_replanned_block(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        page.build(date(2026, 10, 22), 1)
        self.pump()
        night = app.schedule.nights[0]
        b = night["blocks"][0]
        b["status"] = "clouded"
        tid, start = b["target_id"], b["start"]
        page.build(date(2026, 10, 22), 1)
        self.pump()
        same = [x for x in app.schedule.nights[0]["blocks"] if x["target_id"] == tid]
        self.assertTrue(any(abs(x["start"] - start) < 1e-6 and x["status"] == "clouded" for x in same))
        for x in same:
            if abs(x["start"] - start) >= 1e-6:
                self.assertEqual(x.get("status", "planned"), "planned", "the re-planned block is plannable")

    def test_build_from_later_night_keeps_past_marks(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        page.build(date(2026, 10, 21), 2)
        self.pump()
        b = app.schedule.nights[0]["blocks"][0]
        b["status"] = "done"
        page.build(date(2026, 10, 22), 2)
        self.pump()
        self.assertEqual(app.schedule.nights[0]["date"], "2026-10-22")
        self.assertTrue(any(h.get("date") == "2026-10-21" and h.get("target_id") == b["target_id"]
                            and h.get("status") == "done" for h in app.schedule.history),
                        "the Done mark of the dropped night went into the history")


if __name__ == "__main__":
    unittest.main()
