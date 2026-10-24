"""Find suggested variables (targets.suggest_variables + the Targets page's pop-up and Suggested Variables group), and
the Suggested groups listed by date.

    cd /home/claude/shobs-sched && xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_suggest_vars -v

targets_vizier_varsearch.csv: 44 B/vsx rows recorded live from VizieR TAP on 23 Oct 2026 (Dec ≈ +20…+22°, RA 18–22 h,
V 8–13.5: every family, some ":" types, amplitudes given as "Y" f_min). The site and nights are the golden fixture's
(Oct 19–23 2026)."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_targets import DOWN, NIGHTS, SITE, Base, fx  # noqa: E402  (sets a temporary home first)

import events  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
import targets as T  # noqa: E402
from models import Target, VarElements  # noqa: E402

FIXTURE = "targets_vizier_varsearch.csv"
ALL = ["eclipsing", "rrlyr", "dsct", "long", "cepheid", "other"]


def fixture_rows() -> list[dict]:
    return [T.candidate_from_row(r) for r in csv.DictReader(io.StringIO(fx(FIXTURE).decode("utf-8")))]


def params(**kw) -> dict:
    p = dict(T.VAR_SEARCH_DEFAULTS, vmin=9.0, vmax=13.5, families=list(ALL), max_results=100)
    p.update(kw)
    return p


def search(**kw):
    rows = kw.pop("rows", None)
    return T.suggest_variables(SITE, {}, NIGHTS, params(**kw), rows=fixture_rows() if rows is None else rows)


def asu_tsv() -> bytes:
    """The fixture as a VizieR ASU TSV reply (header, units line, dashes line)."""
    rows = list(csv.DictReader(io.StringIO(fx(FIXTURE).decode("utf-8"))))
    cols = list(rows[0].keys())
    lines = ["#RESOURCE=yCat_102027", "\t".join(cols), "\t".join(" " for _ in cols), "\t".join("-" * 3 for _ in cols)]
    lines += ["\t".join(r[c] for c in cols) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


class TestTypesAndParams(unittest.TestCase):
    def test_variable_family(self):
        f = T.variable_family
        self.assertEqual(f("EA/SD"), ("eclipsing", False))
        self.assertEqual(f("EW|EB"), ("eclipsing", False))
        self.assertEqual(f("EB:"), ("eclipsing", True))
        self.assertEqual(f("RRAB/BL"), ("rrlyr", False))
        self.assertEqual(f("RRC|EC|ESD"), ("rrlyr", False))
        self.assertEqual(f("HADS"), ("dsct", False))
        self.assertEqual(f("DSCTC"), ("dsct", False))
        self.assertEqual(f("DSCT|GDOR|SXPHE"), ("dsct", False))
        self.assertEqual(f("SXPHE"), ("dsct", False))
        self.assertEqual(f("M"), ("long", False))
        self.assertEqual(f("M:"), ("long", True))
        self.assertEqual(f("SRB"), ("long", False))
        self.assertEqual(f("MISC"), ("other", False), "MISC is not a Mira")
        self.assertEqual(f("DCEP-FO"), ("cepheid", False))
        self.assertEqual(f("CWA"), ("cepheid", False))
        self.assertEqual(f("ROT"), ("other", False))
        self.assertEqual(f("L:"), ("other", True))
        self.assertEqual(f("CST"), (None, False))
        self.assertEqual(f(""), (None, False))

    def test_params_defaults_and_saved(self):
        p = T.var_search_params({})
        self.assertEqual(p["families"], ["eclipsing", "rrlyr", "dsct", "long"], "Miras / SR on by default")
        self.assertEqual((p["vmin"], p["vmax"]), (9.0, 13.0), "V range from Setup's Suggested range")
        self.assertEqual((p["min_amp"], p["max_results"]), (0.1, 25))    # default cap 25 (John 24 Oct)
        p = T.var_search_params({"suggest_vmin": 8.5, "suggest_vmax": 12.5,
                                 "var_search": {"families": ["long", "bogus"], "punit": "x", "vmax": 11.0}})
        self.assertEqual(p["families"], ["long"])
        self.assertEqual((p["vmin"], p["vmax"], p["punit"]), (8.5, 11.0, "d"))

    def test_amplitude_from_either_form(self):
        rows = {r["name"]: r for r in fixture_rows()}
        self.assertAlmostEqual(rows["WISE J202815.7+200020"]["amp"], 0.364, places=3, msg="f_min Y: min is the amp")
        self.assertAlmostEqual(rows["PT Del"]["amp"], 1.22, places=3)

    def test_adql(self):
        p = T.var_search_params({})
        q = T.var_search_adql(p, -24.7, 90.0, [(200.0, 360.0), (0.0, 30.0)])
        for part in ("V = 0", "max BETWEEN 9.00 AND 13.00", "n_max IN ('V'", "DEJ2000 BETWEEN -24.700 AND 90.000",
                     "RAJ2000 BETWEEN 200.000 AND 360.000", "RAJ2000 BETWEEN 0.000 AND 30.000", "DEJ2000 >= 60",
                     "f_min = 'Y' AND min >= 0.100", "min - max >= 0.100", "Type LIKE 'EA%'", "Epoch > 0",
                     "Type LIKE 'HADS%'", "ORDER BY OID"):
            self.assertIn(part, q)
        self.assertIn("Type = 'M'", q, "Miras / SR on by default")
        q = T.var_search_adql(dict(p, families=["long", "other"]), 0, 90, None)
        self.assertNotIn("Type LIKE", q, "'other' = every type: no type filter")
        self.assertNotIn("Type =", q)
        q = T.var_search_adql(dict(p, families=["long"], pmin=2, pmax=10, punit="h"), 0, 90, None)
        self.assertIn("Type = 'M'", q)
        self.assertIn("Period >= 0.08333333", q)
        self.assertIn("Period <= 0.41666667", q)
        self.assertNotIn("RAJ2000 BETWEEN", q)

    def test_ra_range_follows_the_dark(self):
        wins = [(d, *sky.night_window(d, SITE)) for d in NIGHTS]
        h = T._max_hour_angle(SITE, 30.0, -24.7, 90.0)
        self.assertTrue(80 < h < 100, h)
        ras = T._ra_ranges(SITE, wins, 30.0)              # a narrow widening: only the RAs up in the dark
        self.assertIsNotNone(ras)
        lst_mid = float(sky.lst_deg((wins[0][1] + wins[0][2]) / 2, SITE.lon))
        self.assertTrue(any(a <= lst_mid <= b for a, b in ras), (ras, lst_mid))
        noon = (lst_mid + 180.0) % 360.0
        self.assertFalse(any(a <= noon <= b for a, b in ras))
        self.assertIsNone(T._ra_ranges(SITE, wins, 120.0), "a long night with a wide margin: every RA")


class TestSuggestVariables(unittest.TestCase):
    def test_families_and_uncertain_types(self):
        out, note, info = search()
        names = {t.name for t in out}
        self.assertTrue(out)
        self.assertEqual(note, "")
        self.assertIn("catalog stars matched", info)
        fams = {t.suggest_info["family"] for t in out}
        self.assertTrue({"eclipsing", "long"} <= fams, fams)
        self.assertFalse(any(T.variable_family(t.elements["vtype"])[1] for t in out), "':' types left out")
        out2, _n, _i = search(uncertain=True, min_amp=0.25)
        self.assertTrue(any(T.variable_family(t.elements["vtype"])[1] for t in out2), "asked for: included")
        only_long, _n, _i = search(families=["long"])
        self.assertTrue(only_long)
        self.assertEqual({t.suggest_info["family"] for t in only_long}, {"long"})
        self.assertEqual({t.suggest_info["kind"] for t in only_long}, {"any"}, "Miras / SR are any-time fillers")
        self.assertTrue(names)

    def test_v_period_amplitude_filters(self):
        out, _n, _i = search(vmax=12.0)
        self.assertTrue(out)
        self.assertTrue(all(t.vmag <= 12.0 for t in out))
        out, _n, _i = search(pmax=10, punit="h")
        self.assertTrue(out)
        self.assertTrue(all(t.elements["period"] * 24 <= 10 for t in out))
        out, _n, _i = search(min_amp=1.0)
        self.assertTrue(out)
        self.assertTrue(all(t.suggest_info["amp"] >= 1.0 for t in out))
        out, _n, _i = search(max_results=3)
        self.assertEqual(len(out), 3)
        scores = [t.suggest_info["score"] for t in out]
        self.assertEqual(scores, sorted(scores, reverse=True), "the best-ranked ones are kept")
        self.assertEqual(search(vmin=5.0, vmax=6.0)[0], [])

    def test_unreachable_and_daytime_stars_left_out(self):
        rows = fixture_rows()
        good = search()[0][0].name                                   # one that is offered
        pt = next(r for r in rows if r["name"] == good)
        south = dict(pt, name="Far South", dec=-70.0)
        day = dict(pt, name="Day Star", ra=(float(sky.lst_deg(sky.local_jd(NIGHTS[2], 12, 0, SITE.tz),
                                                             SITE.lon))) % 360.0, dec=0.0)
        out, _n, _i = search(rows=[pt, south, day])
        names = [t.name for t in out]
        self.assertIn(good, names)
        self.assertNotIn("Far South", names)
        self.assertNotIn("Day Star", names)
        out, _n, _i = search(rows=[pt], max_results=5)
        self.assertEqual(T.suggest_variables(SITE, {}, [], params(), rows=[pt])[0], [])

    def test_timed_events_and_full_cycles(self):
        out, _n, _i = search()
        wins = {d.isoformat(): sky.night_window(d, SITE) for d in NIGHTS}
        timed = [t for t in out if t.suggest_info["kind"] == "timed"]
        self.assertTrue(timed)
        for t in timed:
            info = t.suggest_info
            w0, w1 = wins[info["night"]]
            self.assertTrue(w0 <= info["when"] <= w1, t.name)
            self.assertTrue(bool(sky.up_mask(t.ra, t.dec, SITE, [info["when"]])[0]), t.name)
            el = VarElements(period=t.elements["period"], epoch=t.elements["epoch"], any_time=False,
                             epoch_kind=info["event"])
            evs = events.variable_events(el, t.ra, t.dec, w0, w1)
            self.assertTrue(any(abs(e["t"] - info["when"]) < 1 / 1440 for e in evs), t.name)
            self.assertRegex(t.notes, r"(min|max) \w{3} \d{1,2} \w{3} \d\d:\d\d, Local time \(UTC[−+]\d+\)$")
            self.assertTrue(info["text"].startswith(("min ", "max ")))
        # a short pulsator needs a whole cycle: HADS (P 3.3 h) on a night too short for it is not offered
        hads = [r for r in fixture_rows() if r["vtype"] == "HADS" and r["max"] <= 13.0]
        self.assertTrue(hads)
        out, _n, _i = search(rows=hads)
        for t in out:
            self.assertEqual(t.suggest_info["kind"], "cycle")
            self.assertIn("full cycle", t.suggest_info["text"])
        long_cycle = [dict(hads[0], name="Slow HADS", period=0.6)]   # 14.4 h + 30 min: never in one night
        self.assertEqual(search(rows=long_cycle, families=["dsct"])[0], [])

    def test_target_shape(self):
        out, _n, _i = search(max_results=5)
        t = out[0]
        self.assertEqual((t.kind, t.mode, t.suggested, t.enabled, t.source), ("variable", "V", True, False,
                                                                              "VizieR VSX"))
        self.assertTrue(t.elements["vtype"])
        self.assertTrue(t.notes)
        back = Target.from_dict(json.loads(json.dumps(t.to_dict())))
        self.assertEqual(back.suggest_info["when"], t.suggest_info["when"], "the date survives saving")


class TestFetch(Base):
    WINS = [(d, *sky.night_window(d, SITE)) for d in NIGHTS]

    def test_tap_then_cache(self):
        r = self.route([(["tapvizier", "B/vsx/vsx", "ORDER BY OID"], FIXTURE)])
        rows, note, cut = T.fetch_variable_candidates(T.var_search_params({}), SITE, self.WINS)
        self.assertEqual(len(rows), 44)
        self.assertEqual((note, cut), ("", False))
        n = len(r.calls)
        T.fetch_variable_candidates(T.var_search_params({}), SITE, self.WINS)
        self.assertEqual(len(r.calls), n, "the same search within a day comes from the saved copy")

    def test_tap_down_asu_answers(self):
        self.route([(["tapvizier"], DOWN), (["asu-tsv", "-sort=OID"], asu_tsv())])
        rows, note, _cut = T.fetch_variable_candidates(T.var_search_params({}), SITE, self.WINS)
        self.assertTrue(rows)
        fams = {T.variable_family(r["vtype"])[0] for r in rows}
        self.assertTrue(fams <= {"eclipsing", "rrlyr", "dsct", "long"}, "ASU rows filtered here by family")
        self.assertTrue(all(r["amp"] is not None and r["amp"] >= 0.1 for r in rows))

    def test_everything_down(self):
        self.route([([""], DOWN)])
        with self.assertRaises(T.LookupError_) as cm:
            T.suggest_variables(SITE, {}, NIGHTS, params())
        self.assertIn("did not answer", str(cm.exception))
        # a saved copy is used, with its age, when VizieR is down later
        self.route([(["tapvizier"], FIXTURE)])
        T.fetch_variable_candidates(T.var_search_params({}), SITE, self.WINS)
        old = T.VAR_SEARCH_MAX_AGE_DAYS
        T.VAR_SEARCH_MAX_AGE_DAYS = -1.0
        try:
            self.route([([""], DOWN)])
            rows, note, _c = T.fetch_variable_candidates(T.var_search_params({}), SITE, self.WINS)
        finally:
            T.VAR_SEARCH_MAX_AGE_DAYS = old
        self.assertTrue(rows)
        self.assertIn("using the copy from", note)


# ---- the Targets page ---------------------------------------------------------------------------------------------

from tests.test_app import HAVE_TK, Base as AppBase  # noqa: E402


class VarsPageBase(AppBase):
    """The app with the fake targets module answering the variable search from the recorded field (no tests)."""
    def setUp(self):
        super().setUp()
        tg = self.fakes["targets"]
        tg.var_search_params, tg.VAR_FAMILIES = T.var_search_params, T.VAR_FAMILIES
        self.var_calls = []
        self.var_answer = None

        def suggest_variables(site, options, nights, params=None, exclude_names=frozenset(), rows=None,
                              progress=None):
            self.var_calls.append({"params": params, "nights": nights, "exclude": set(exclude_names)})
            if isinstance(self.var_answer, BaseException):
                raise self.var_answer
            if self.var_answer is not None:
                return self.var_answer
            out, _n, info = T.suggest_variables(SITE, {}, NIGHTS, dict(params, vmax=13.5), rows=fixture_rows())
            return out, "", info
        tg.suggest_variables = suggest_variables

    def page(self):
        self.golden_home()
        app = self.open()
        app.show_page(1)
        return app, app.pages[1]

    def kids(self, page, group):
        """Row names of a Targets box in order; "suggested" / "suggested_var" = only the suggestions in the
        Transits / Variables box (1.1.0: one box per type)."""
        real = {"suggested": "transit", "suggested_var": "variable"}.get(group, group)
        tree = page.tables[real].tree
        by = page.app.targets_by_id()
        return [tree.set(i, "name") for i in tree.get_children()
                if real == group or (i in by and by[i].suggested)]


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSuggestedVariablesPage(VarsPageBase):
    def test_group_names_and_old_files(self):
        old = Target(name="WASP-2 b", kind="transit", mode="T", ra=307.7, dec=6.4, suggested=True, enabled=False,
                     planet={"name": "WASP-2 b", "period": 2.15, "t0": 2453991.5, "t14": 0.07})
        d = old.to_dict()
        d.pop("suggest_info")                         # a v1.1.0 targets file has no suggest_info
        self.golden_home()
        targets = settings.load_targets()
        with open(settings.targets_path(), "w", encoding="utf-8") as fh:
            json.dump({"targets": [t.to_dict() for t in targets] + [d]}, fh)
        app = self.open()
        app.show_page(1)
        page = app.pages[1]
        self.assertTrue(page.tables["transit"].box.cget("text").startswith("Transits  ("))
        self.assertTrue(page.tables["variable"].box.cget("text").startswith("Variables  ("))
        self.assertTrue(page.tables["campaign"].box.cget("text").startswith("Campaigns  ("))
        self.assertEqual(self.kids(page, "suggested"), ["WASP-2 b"])
        self.assertEqual(page.table_of(old.id).group, "transit", "an old Suggested transit lands in Transits")
        self.assertEqual(self.kids(page, "suggested_var"), [])

    def test_popup_remembers_and_results_land_unticked_by_date(self):
        app, page = self.page()
        dlg = page.ask_variable_search()
        self.assertEqual(dlg.vars["vmin"].get(), "9")
        self.assertEqual(dlg.vars["vmax"].get(), "13", "Setup's Suggested V range")
        for k, var in dlg.fam_vars.items():
            var.set(k in ("eclipsing", "long"))
        dlg.vars["pmax"].set("")
        dlg.vars["min_amp"].set("0.15")
        dlg.vars["max_results"].set("8")
        self.assertTrue(dlg.search())
        self.pump()
        saved = settings.load_settings()["options"]["var_search"]
        self.assertEqual(saved["families"], ["eclipsing", "long"])
        self.assertEqual((saved["min_amp"], saved["max_results"]), (0.15, 8))
        self.assertEqual(self.var_calls[-1]["params"]["families"], ["eclipsing", "long"])
        names = self.kids(page, "suggested_var")
        self.assertTrue(names)
        self.assertLessEqual(len(names), 8)
        ts = [t for t in app.targets if t.suggested and t.kind == "variable"]
        self.assertTrue(all(not t.enabled for t in ts), "unticked")
        by_name = {t.name: t for t in ts}
        order = self.sa.row_order("variable", sky.now_jd())          # dates already past count as undated
        keys = [order(by_name[n]) for n in names]
        self.assertEqual(keys, sorted(keys), "night by night: timed events first, then fillers")
        self.assertTrue(page.find_ind.var.get().startswith("✔ "), page.find_ind.var.get())
        self.assertIn("found, soonest first", page.find_ind.var.get())
        self.assertIn("catalog stars matched", page.msg_var.get())
        # the pop-up opens with what was used last
        dlg = page.ask_variable_search()
        self.assertEqual(dlg.vars["min_amp"].get(), "0.15")
        self.assertFalse(dlg.fam_vars["rrlyr"].get())
        dlg.vars["vmin"].set("14")
        self.assertFalse(dlg.search())
        self.assertIn("wrong way", dlg.msg_var.get())
        for var in dlg.fam_vars.values():
            var.set(False)
        dlg.vars["vmin"].set("9")
        self.assertFalse(dlg.search())
        self.assertIn("Tick at least one type", dlg.msg_var.get())
        dlg.win.destroy()

    def test_detail_next_column_keep_remove(self):
        app, page = self.page()
        future = sky.now_jd() + 1.0
        t = Target(name="XZ And", kind="variable", mode="V", ra=29.0, dec=42.0, vmag=9.9, suggested=True,
                   enabled=False, source="VizieR VSX", elements={"vtype": "EA", "period": 1.357, "epoch": 2450000.0},
                   notes="EA, P = 1.357 d", suggest_info={"when": future, "text": "min Sat 24 Oct 04:22",
                                                          "score": 4.34, "good_nights": 3, "nights": 7,
                                                          "kind": "timed", "event": "min"})
        app.targets.append(t)
        page.fill()
        tv = page.tables["variable"].tree
        self.assertEqual(tv.set(t.id, "event"), f"min {sky.hhmm(future, app.site.tz)}")
        self.assertEqual(tv.set(t.id, "period"), "1.35700 d")
        self.assertEqual(tv.set(t.id, "vtype"), "EA")
        self.assertTrue(tv.set(t.id, "source").startswith("suggested"))
        page.select_target(t.id)
        app.update()
        self.assertIn("suggested variable: score 4.34, good on 3 of 7 nights", page.detail_var.get())
        page.keep_selected()
        self.assertEqual(self.kids(page, "variable").count("XZ And"), 1)
        self.assertIn("Kept XZ And", page.msg_var.get())
        self.assertFalse(t.suggested)
        page.select_target(t.id)
        page.remove_selected()
        self.assertNotIn("XZ And", [x.name for x in app.targets])

    def test_transit_search_keeps_suggested_variables_and_vice_versa(self):
        app, page = self.page()
        page.find_variables(T.var_search_params({}))
        self.pump()
        n_var = len([t for t in app.targets if t.suggested and t.kind == "variable"])
        self.assertTrue(n_var)
        page.find_suggested()
        self.pump()
        self.assertEqual(len([t for t in app.targets if t.suggested and t.kind == "variable"]), n_var)
        self.assertEqual(self.kids(page, "suggested"), ["WASP-2 b"])
        page.find_variables(T.var_search_params({}))
        self.pump()
        self.assertEqual(self.kids(page, "suggested"), ["WASP-2 b"], "a variable search keeps the transits")

    def test_offline_message(self):
        app, page = self.page()
        self.var_answer = self.fakes["targets"].LookupError_(
            "VizieR did not answer (service unavailable, HTTP 503) and there is no saved copy for this variable-star "
            "search. Try again later.")
        page.find_variables(T.var_search_params({}))
        self.pump()
        self.assertTrue(page.find_ind.var.get().startswith("✖ No suggested variables: VizieR did not answer"),
                        page.find_ind.var.get())

    def test_suggested_variables_timed_first_then_fillers_each_night(self):
        app, page = self.page()
        now = sky.now_jd()
        d1, d2 = "2026-10-23", "2026-10-24"

        def mk(name, night, kind, when):
            return Target(name=name, kind="variable", mode="V", ra=10.0, dec=20.0, suggested=True, enabled=False,
                          suggest_info={"night": night, "kind": kind, "when": when, "text": name, "score": 1.0})
        app.targets += [mk("Mira A", d1, "any", now + 0.10), mk("EB late", d1, "timed", now + 0.30),
                        mk("RR early", d1, "timed", now + 0.20), mk("HADS", d1, "cycle", now + 0.25),
                        mk("SR B", d1, "any", now + 0.05), mk("EB next", d2, "timed", now + 1.20),
                        mk("Mira next", d2, "any", now + 1.05),
                        Target(name="Undated", kind="variable", mode="V", suggested=True, enabled=False)]
        page.fill()
        self.assertEqual(self.kids(page, "suggested_var"),
                         ["RR early", "HADS", "EB late", "SR B", "Mira A", "EB next", "Mira next", "Undated"])

    def test_suggested_transits_listed_by_date(self):
        app, page = self.page()
        now = sky.now_jd()
        mk = lambda name, when: Target(name=name, kind="transit", mode="T", ra=10.0, dec=20.0, suggested=True,
                                       enabled=False, planet={"name": name, "period": 2.0, "t0": 2460000.5,
                                                              "t14": 0.1},
                                       suggest_info={"when": when, "score": 3.0, "snr": 2.0})
        app.targets += [mk("A-1 b", now + 3), mk("B-2 b", now + 1), mk("C-3 b", now + 2),
                        Target(name="0-old b", kind="transit", mode="T", suggested=True, enabled=False)]
        page.fill()
        self.assertEqual(self.kids(page, "suggested"), ["B-2 b", "C-3 b", "A-1 b", "0-old b"],
                         "soonest first; no date last")
        page.select_target(next(t.id for t in app.targets if t.name == "B-2 b"))
        app.update()
        self.assertIn("suggested transit: score 3.00, S/N 2.0", page.detail_var.get())


# ---- review round 3: timing precision, amplitude bands, Keep keeps the timing, stale, names ------------------------

def well_placed(night_index: int = 1):
    """(RA, mid-dark JD) of a star crossing the meridian in the middle of a golden night's darkness."""
    w0, w1 = sky.night_window(NIGHTS[night_index], SITE)
    mid = (w0 + w1) / 2.0
    return float(sky.lst_deg(mid, SITE.lon)), mid


def row(name, vtype, period, epoch, ra, dec=40.0, vmax="11.0", vmin="12.0", n_min="V", **extra):
    """A B/vsx row as VizieR sends it (strings), for one synthetic star."""
    r = {"Name": name, "V": "0", "RAJ2000": f"{ra:.5f}", "DEJ2000": f"{dec:.5f}", "Type": vtype, "max": vmax,
         "n_max": "V", "f_min": "", "min": vmin, "n_min": n_min, "Epoch": epoch, "u_Epoch": "", "l_Period": "",
         "Period": period, "u_Period": ""}
    r.update(extra)
    return T.candidate_from_row(r)


class TestTimingPrecision(unittest.TestCase):
    def test_sigma_from_published_decimals(self):
        dm = row("DM Cyg-like", "RRAB", "0.41986", "2442582.406", 10.0)
        s = T.ephemeris_sigma_min(dm, 2461337.0)
        self.assertGreater(s, 300, "44 670 cycles × 0.5e-5 d ≈ ±5 h")
        zp = row("Z Per-like", "EA/SD", "3.0563691", "2448500.6056", 10.0)
        self.assertLess(T.ephemeris_sigma_min(zp, 2461337.0), 1.0)
        padded = row("ASU padded", "EA", "0.520759600000", "2457000.123400", 10.0)
        self.assertAlmostEqual(T.ephemeris_sigma_min(padded, 2457000.1234 + 0.5207596 * 100),
                               (100 * 0.5e-7 + 0.5e-4) * 1440, places=6, msg="ASU's zero padding is not precision")
        self.assertIsNone(T.ephemeris_sigma_min(row("limit", "EA", "1.2", "2457000.1", 10.0, l_Period=">"), 2461337))

    def test_precise_ephemeris_is_timed_and_kept_timed(self):
        ra, mid = well_placed(1)
        zp = row("Z Per-like", "EA/SD", "3.0563691", f"{mid:.4f}", ra)
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["eclipsing"]), rows=[zp])
        self.assertEqual(len(out), 1)
        t = out[0]
        info = t.suggest_info
        self.assertEqual(info["kind"], "timed")
        self.assertFalse(info["time_uncertain"])
        self.assertLess(info["timing_sigma_min"], 1.0)
        self.assertLess(abs(info["when"] - mid) * 1440, 15)
        self.assertEqual((t.elements["any_time"], t.elements["epoch_kind"]), (False, "min"))
        # kept and planned: the planner puts the block on the minimum it was suggested for
        import planner
        from tests.test_targets import OPTICS
        t.suggested, t.enabled = False, True
        night = planner.build_night(NIGHTS[1], SITE, OPTICS, [t], dict(settings.DEFAULT_OPTIONS))
        blocks = [b for b in night.blocks_obj() if b.target_id == t.id]
        self.assertTrue(blocks, night.didnt_fit)
        self.assertTrue(any(b.start <= info["when"] <= b.end for b in blocks))

    def test_rr_lyrae_maximum_kept_as_timed_max(self):
        ra, mid = well_placed(2)
        rr = row("RR-like", "RRAB", "0.5667148", f"{mid:.4f}", ra, vmax="11.2", vmin="12.1")
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["rrlyr"]), rows=[rr])
        self.assertEqual(out[0].suggest_info["kind"], "timed")
        self.assertEqual((out[0].elements["any_time"], out[0].elements["epoch_kind"]), (False, "max"),
                         "a kept RR Lyrae keeps its timed maxima (VSX elements alone would make it any-time)")

    def test_imprecise_ephemeris_is_not_promised(self):
        ra, mid = well_placed(1)
        dm = row("DM Cyg-like", "RRC", "0.29", "2442582.406", ra)       # P to 2 decimals since 1975: hours off
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["rrlyr"]), rows=[dm])
        self.assertEqual(len(out), 1)
        info = out[0].suggest_info
        self.assertEqual(info["kind"], "cycle", "a whole cycle instead of a promised maximum")
        self.assertTrue(info["time_uncertain"])
        self.assertIn("⚠ time uncertain ±", out[0].notes)
        self.assertIn("⚠ time uncertain", info["text"])
        self.assertTrue(out[0].elements["any_time"])
        long_p = row("Long EA", "EA", "2.1", "2442582.406", ra)            # no timing, too long for a cycle
        self.assertEqual(T.suggest_variables(SITE, {}, NIGHTS, params(families=["eclipsing"]), rows=[long_p])[0],
                         [])
        limit = row("Limit EA", "EA", "0.3", f"{mid:.4f}", ra, l_Period=">")
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["eclipsing"]), rows=[limit])
        self.assertTrue(all(t.suggest_info["kind"] != "timed" for t in out))
        unsure = row("Unsure P", "EA", "0.3000", f"{mid:.4f}", ra, u_Period=":")
        self.assertEqual(T.suggest_variables(SITE, {}, NIGHTS, params(families=["eclipsing"]), rows=[unsure])[0], [],
                         "an uncertain period is an uncertain star: left out unless asked for")

    def test_amplitude_needs_one_band(self):
        ra, _mid = well_placed(1)
        ck = row("CK Aur-like", "SR", "100", "2457000.1", ra, vmax="11.57", vmin="0.27", n_min="TESS", f_min="Y")
        self.assertIsNone(ck["amp"])
        mixed = row("Mixed", "SR", "100", "2457000.1", ra, vmax="11.0", vmin="12.5", n_min="R")
        self.assertIsNone(mixed["amp"])
        ok = row("Same band", "SR", "100", "2457000.1", ra, vmax="11.0", vmin="12.5")
        self.assertAlmostEqual(ok["amp"], 1.5)
        rows_ = [ck, mixed, ok]
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["long"]), rows=rows_)
        self.assertEqual([t.name for t in out], ["Same band"])
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["long"], min_amp=0), rows=rows_)
        self.assertEqual(len(out), 3, "no amplitude filter: unknown amplitudes pass")

    def test_name_key_and_exclusion(self):
        self.assertEqual(T.variable_name_key("V523 Cas"), T.variable_name_key("V0523 Cas"))
        self.assertEqual(T.variable_name_key("v 523 cas"), "v0523 cas")
        ra, mid = well_placed(1)
        r = row("V0523 Cas", "EA", "3.0563691", f"{mid:.4f}", ra)
        out, _n, _i = T.suggest_variables(SITE, {}, NIGHTS, params(families=["eclipsing"]), rows=[r],
                                          exclude_names={"V523 Cas"})
        self.assertEqual(out, [], "already in the list under the other spelling")

    def test_whole_search_is_quick(self):
        """12 000 candidates over 5 nights stays a few seconds of computing (VizieR's own time aside)."""
        import time
        base = fixture_rows()
        many = []
        for k in range(12000 // len(base) + 1):
            for r in base:
                many.append(dict(r, name=f"{r['name']} #{k}", ra=(r["ra"] + 7.3 * k) % 360.0))
        t0 = time.time()
        T.suggest_variables(SITE, {}, NIGHTS, params(), rows=many[:12000])
        self.assertLess(time.time() - t0, 30.0)


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSuggestionsLeavePlanFresh(VarsPageBase):
    def test_search_and_keep_do_not_make_the_plan_stale(self):
        app, page = self.page()
        app.show_page(2)
        app.pages[2].build()
        self.pump()
        self.assertTrue(app.schedule.nights)
        app.pages[2].setup_stale = False
        app.show_page(1)
        page.find_variables(T.var_search_params({}))
        self.pump()
        self.assertFalse(app.pages[2].setup_stale, "only unticked suggestions came: the plan is still current")
        page.find_suggested()
        self.pump()
        self.assertFalse(app.pages[2].setup_stale)
        by = app.targets_by_id()
        kid = next(i for i in page.tables["variable"].tree.get_children() if by[i].suggested)
        page.select_target(kid)
        page.keep_selected()
        self.assertFalse(app.pages[2].setup_stale, "kept unticked: nothing planned changed")
        page.toggle(kid)                                    # ticking it does change the plan
        self.assertTrue(app.pages[2].setup_stale)

    def test_past_dates_sort_as_undated(self):
        app, page = self.page()
        now = sky.now_jd()

        def mk(name, when):
            return Target(name=name, kind="variable", mode="V", ra=10.0, dec=20.0, suggested=True, enabled=False,
                          suggest_info={"night": "2026-10-23", "kind": "timed", "when": when, "text": name})
        app.targets += [mk("Past", now - 0.5), mk("Soon", now + 0.2), mk("Later", now + 0.4)]
        page.fill()
        self.assertEqual(self.kids(page, "suggested_var"), ["Soon", "Later", "Past"])


if __name__ == "__main__":
    unittest.main()
