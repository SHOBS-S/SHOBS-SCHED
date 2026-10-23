"""The night plot (nightplot.py) and its place on the Schedule tab:

    cd /home/claude/shobs-sched && xvfb-run -a /usr/bin/python3.12 -m unittest tests.test_nightplot -v

Nights are built with the real planner from the golden fixture (site, targets) and the recorded Open-Meteo forecast,
so nothing goes to the network. The Tk tests (two-way selection, no matplotlib) skip without a display."""
from __future__ import annotations

import json
import os
import struct
import sys
import time
import types
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import nightplot  # noqa: E402
import settings  # noqa: E402
import sky  # noqa: E402
from models import Block, NightPlan, Optics, Site, Target  # noqa: E402

HAVE_MPL = nightplot.available()


def load(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


GOLDEN = load("golden_oct19_24.json")


def golden_setup():
    s = settings.default_settings()
    s["site"].update(GOLDEN["site"])
    s["optics"].update(GOLDEN["optics"])
    return Site.from_dict(s["site"]), Optics.from_dict(s["optics"]), dict(s["options"]), \
        [Target.from_dict(t) for t in GOLDEN["targets"]]


_NIGHTS: dict = {}


def golden_night(d: date, with_weather: bool = True) -> tuple:
    """(night, site, targets_by_id, options) for the golden site and targets on night d (real planner)."""
    key = (d, with_weather)
    if key not in _NIGHTS:
        import planner
        import weather
        site, optics, opts, targets = golden_setup()
        wx = None
        if with_weather:
            hours = weather.parse_open_meteo(load("weather_openmeteo_shobs_20261022.json"))
            wx = weather.night_weather(hours, d, site, opts, source="Open-Meteo")
        night = planner.build_night(d, site, optics, targets, opts, weather=wx)
        _NIGHTS[key] = (night, site, {t.id: t for t in targets}, opts)
    return _NIGHTS[key]


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def by_name(targets_by_id: dict, name: str) -> Target:
    return next(t for t in targets_by_id.values() if t.name == name)


# ---- no matplotlib needed ---------------------------------------------------------------------------------------------

class TestSpanAndBlackouts(unittest.TestCase):
    def test_span_rule_nautical_plus_minus_90_min_whole_hours(self):
        site, _o, _p, _t = golden_setup()
        for d in (date(2026, 10, 22), date(2026, 6, 21), date(2026, 12, 21)):
            naut = sky.night_window(d, site, sky.NAUTICAL)
            j0, j1 = nightplot.x_span(d, site)
            a, b = naut[0] - 1.5 / 24, naut[1] + 1.5 / 24
            for j in (j0, j1):
                t = sky.to_local(j + 0.5 / 86400, site.tz)
                self.assertEqual((t.minute, t.second), (0, 0), f"{d}: whole hours ({t})")
            self.assertLessEqual(j0, a + 1e-9)
            self.assertGreater(j0, a - 1 / 24 - 1e-9, "truncated to the hour before, no further")
            self.assertGreaterEqual(j1, b - 1e-9)
            self.assertLess(j1, b + 1 / 24 + 1e-9)
        # 22 Oct 2026 (EDT): nautical 19:39–06:45 → 18:00–09:00, as in John's mockup
        d = date(2026, 10, 22)
        j0, j1 = nightplot.x_span(d, site)
        self.assertEqual((sky.hhmm(j0, site.tz), sky.hhmm(j1, site.tz)), ("18:00", "09:00"))

    def test_span_widens_for_a_block_outside(self):
        site, _o, _p, _t = golden_setup()
        d = date(2026, 10, 22)
        early = Block(start=sky.local_jd(d, 16, 10, site.tz), end=sky.local_jd(d, 17, 0, site.tz))
        j0, _j1 = nightplot.x_span(d, site, [early])
        self.assertEqual(sky.hhmm(j0, site.tz), "16:00")

    def test_hour_ticks_one_or_two_hours(self):
        site, _o, _p, _t = golden_setup()
        j0, j1 = nightplot.x_span(date(2026, 10, 22), site)
        one = nightplot.hour_ticks(j0, j1, site.tz, 1)
        two = nightplot.hour_ticks(j0, j1, site.tz, 2)
        self.assertEqual(len(one), 16)
        self.assertTrue(all(sky.to_local(j, site.tz).hour % 2 == 0 for j in two))
        self.assertEqual(len(two), 8)

    def test_blackout_only_when_it_changed_the_plan(self):
        night, site, by_id, _o = golden_night(date(2026, 10, 22), with_weather=False)
        hd = by_name(by_id, "HD 219134")
        # the real plan: HD 219134 22:10–23:55, its planet-c window 04:28–05:53 never touched it → not drawn
        self.assertTrue(any(b.target_id == hd.id for b in night.blocks_obj()))
        self.assertTrue(any("No HD 219134" in n for b in night.blocks_obj() if b.target_id == hd.id
                            for n in b.notes), "the window is in the Notes instead")
        self.assertEqual(nightplot.plan_blackouts(night, by_id), [])
        import events
        j0, j1 = nightplot.x_span(date(2026, 10, 22), site)
        bo = [x for x in events.blackouts(hd, j0, j1) if x["start"] > night.dusk][0]
        # a block that ends where the window starts (the planner cut it there) → drawn
        cut = NightPlan.from_dict(night.to_dict())
        cut.blocks = [Block(target_id=hd.id, name=hd.name, start=bo["start"] - 1.5 / 24, end=bo["start"]).to_dict()]
        got = nightplot.plan_blackouts(cut, by_id)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["why"], "trimmed or moved")
        self.assertAlmostEqual(got[0]["start"], bo["start"], places=6)
        # starts where it ends (moved after it) → drawn
        cut.blocks = [Block(target_id=hd.id, name=hd.name, start=bo["end"] + 1 / 1440, end=bo["end"] + 1.5 / 24
                            ).to_dict()]
        self.assertEqual(len(nightplot.plan_blackouts(cut, by_id)), 1)
        # a block well away from it → not drawn
        cut.blocks = [Block(target_id=hd.id, name=hd.name, start=bo["start"] - 4 / 24, end=bo["start"] - 2 / 24
                            ).to_dict()]
        self.assertEqual(nightplot.plan_blackouts(cut, by_id), [])
        # Didn't fit because blacked out → every window that night is drawn
        cut.blocks = []
        cut.didnt_fit = [{"target_id": hd.id, "name": hd.name, "reason": "blacked out (c transit 04:28–05:53)"}]
        got = nightplot.plan_blackouts(cut, by_id)
        self.assertTrue(got and all(g["why"] == "didn't fit" for g in got))
        cut.didnt_fit = [{"target_id": hd.id, "name": hd.name, "reason": "no free stretch of 45 min left tonight"}]
        self.assertEqual(nightplot.plan_blackouts(cut, by_id), [])


# ---- the plot -----------------------------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_MPL, "matplotlib is not installed")
class TestPlot(unittest.TestCase):
    def plot(self, d=date(2026, 10, 22), with_weather=True, print_style=False, night=None, size=(14, 8.4)):
        n, site, by_id, opts = golden_night(d, with_weather)
        n = night or n
        mpl = nightplot._load()
        fig = mpl["Figure"](figsize=size, dpi=100)
        mpl["Agg"](fig)
        p = nightplot.NightPlot(n, site, by_id, n.weather if with_weather else None, opts, print_style=print_style,
                                figure=fig).draw()
        fig.canvas.draw()
        return p

    def test_twilight_bands_match_night_window(self):
        p = self.plot()
        site, d = p.site, p.d
        for key, lvl in (("sunset", sky.SUN_RISE_ALT), ("civil_dusk", sky.CIVIL), ("nautical_dusk", sky.NAUTICAL),
                         ("astro_dusk", sky.ASTRONOMICAL)):
            w = sky.night_window(d, site, lvl)
            self.assertAlmostEqual(p.times[key], w[0], places=9)
            dawn = key.replace("dusk", "dawn").replace("sunset", "sunrise")
            self.assertAlmostEqual(p.times[dawn], w[1], places=9)
        # every band edge inside the span is one of those times exactly (no seams, no drift)
        inner = p.sky_edges[1:-1]
        self.assertEqual(len(inner), 8)
        for e in inner:
            self.assertTrue(any(abs(e - v) < 1e-9 for v in p.times.values() if v), e)
        # the band between two edges has the Sun's level of its middle
        for k in range(len(p.sky_levels)):
            mid = 0.5 * (p.sky_edges[k] + p.sky_edges[k + 1])
            self.assertEqual(p.sky_levels[k], int(nightplot.sun_level(sky.sun_alt_at(site, mid))))
        self.assertEqual(p.sky_levels, [0, 1, 2, 3, 4, 3, 2, 1, 0])
        labels = [t.get_text().split() for t, _j in p.twilight_texts]
        self.assertEqual([x[0] for x in labels], ["Sunset", "Civil", "Naut.", "Astro", "Astro", "Naut.", "Civil",
                                                   "Sunrise"])
        self.assertEqual(labels[0][1], sky.hhmm(p.times["sunset"], site.tz))

    def test_axes_and_contents(self):
        p = self.plot()
        self.assertEqual(p.ax.get_ylim(), (2.4, 1.0), "airmass downward, 1.0 at the top")
        self.assertEqual(p.ax.get_xlabel(), "Local time (UTC−4)")
        self.assertEqual(p.ax2.get_yticklabels()[0].get_text(), "90°")
        self.assertEqual(p.ax2.get_ylim(), p.ax.get_ylim(), "elevation aligned with the airmass")
        self.assertTrue(any(abs(line.get_ydata()[0] - 2.0) < 1e-9 for line in p.ax.lines
                            if len(line.get_ydata()) == 2), "max-airmass line")
        names = {b.name for _i, b in p.blocks}
        boxes = [lab.get_text().split("\n") for lab, *_r in p.labels]
        self.assertEqual({b[0] for b in boxes}, names)
        for b in boxes:
            self.assertRegex(b[1], r"^\d\d:\d\d–\d\d:\d\d$")
            self.assertRegex(b[2], r"^Moon \d+°$")
        # Moon: dashed, width 1.5·(1 + illumination)
        self.assertAlmostEqual(p.moon_line.get_linewidth(), 1.5 * (1 + p.illum))
        self.assertNotEqual(p.moon_line.get_linestyle(), "-")
        # transit band and baselines for the T block (K2-29 b)
        k2 = next(b for _i, b in p.blocks if b.name == "K2-29 b")
        texts = [t.get_text() for t in p.ax.texts]
        self.assertIn(f" Transit {sky.hhmm(k2.t1, p.tz)}–{sky.hhmm(k2.t4, p.tz)}", texts)
        # weather strip: cloud bars, temperature, dew point, spread numbers
        self.assertTrue(p.has_wx)
        self.assertEqual(len(p.axw.patches), len(p.hours))
        self.assertEqual(len(p.axt.lines), 2)
        self.assertTrue(p.spread_texts)

    def test_flip_mark_only_inside_the_block(self):
        p = self.plot()
        flips = [(line, tid) for tid, arts in p.groups.items() for line in arts
                 if hasattr(line, "get_marker") and line.get_marker() == "v"]
        inside = {b.target_id for _i, b in p.blocks
                  if b.start <= sky.target_meridian(*p._coords(b), p.site, 0.5 * (b.start + b.end)) <= b.end}
        self.assertEqual({tid for _l, tid in flips}, inside)
        self.assertEqual({p.by_id[t].name for t in inside}, {"HD 219134", "K2-29 b"})
        for line, tid in flips:
            b = next(b for _i, b in p.blocks if b.target_id == tid)
            x = float(line.get_xdata()[0])
            self.assertTrue(p.x(b.start) <= x <= p.x(b.end))
        # a block moved away from its meridian loses the mark
        night = NightPlan.from_dict(p.night.to_dict())
        bl = night.blocks_obj()
        hd = next(b for b in bl if b.name == "HD 219134")
        hd.start, hd.end, hd.meridian = hd.start + 1.5 / 24, hd.end + 1.5 / 24, None
        night.blocks = [b.to_dict() for b in bl if b.name in ("HD 219134",)]
        p2 = self.plot(night=night)
        self.assertFalse([a for arts in p2.groups.values() for a in arts
                          if hasattr(a, "get_marker") and a.get_marker() == "v"])

    def test_highlight_restores_every_artist_exactly(self):
        p = self.plot()
        arts = [a for g in p.groups.values() for a in g]
        before = {id(a): p.look_of(a) for a in arts}
        moon = (p.moon_line.get_alpha(), p.moon_line.get_linewidth(), p.moon_line.get_zorder())
        tids = list(p.groups)
        self.assertGreaterEqual(len(tids), 4)
        sel = tids[2]
        self.assertTrue(p.set_highlight(sel))
        self.assertFalse(p.set_highlight(sel), "the same selection again changes nothing")
        for tid, g in p.groups.items():
            for a in g:
                o, now, role = before[id(a)], p.look_of(a), p.roles[id(a)]
                if tid == sel:
                    self.assertEqual(now["zorder"], o["zorder"] + 10)
                    if role != "span":
                        self.assertEqual(now["alpha"], 1.0)
                    if "lw" in o and role != "span":
                        self.assertAlmostEqual(now["lw"], o["lw"] * 1.6)
                else:
                    self.assertLessEqual(now["alpha"], 0.15 + 1e-9)
                    if role == "path":
                        self.assertLessEqual(now["alpha"], 0.06 + 1e-9)
        self.assertEqual((p.moon_line.get_alpha(), p.moon_line.get_linewidth(), p.moon_line.get_zorder()), moon,
                         "the Moon is never touched")
        self.assertIn("Selected:", p.sel_text.get_text())
        for k in range(60):                         # many clicks, in every order
            p.set_highlight(tids[k % len(tids)] if k % 3 else None)
            p.set_highlight(tids[(k * 7) % len(tids)])
        p.set_highlight(None)
        for a in arts:
            self.assertEqual(p.look_of(a), before[id(a)], f"{a} drifted")
        self.assertEqual(p.sel_text.get_text(), "")
        p.set_highlight("no such target")           # unknown → all normal
        self.assertIsNone(p.selected)

    def test_hover_values(self):
        p = self.plot()
        hd = next(b for _i, b in p.blocks if b.name == "HD 219134")
        j = 0.5 * (hd.start + hd.end)
        x = float(p.x(j))
        xa = float(sky.target_airmass(*p._coords(hd), p.site, j))
        text = p.hover_text(x, xa)
        self.assertTrue(text.startswith(sky.hhmm(j, p.tz)), text)
        self.assertIn(f"HD 219134: X {xa:.2f}", text)
        self.assertIn("block HD 219134", text)
        self.assertIn("cloud", text)
        self.assertIn("°C", text)
        k = int(round((j - p.j0) * 1440 / p.GRID_MIN))
        if p.moon_x[k] == p.moon_x[k]:               # the Moon is up then
            self.assertIn("Moon: X", p.hover_text(x, float(p.moon_x[k])))
        self.assertEqual(p.hover_text(-1, 1.2), "")
        # click mapping
        i = p.block_at(x)
        self.assertEqual(p.night.blocks_obj()[i].name, "HD 219134")
        self.assertIsNone(p.block_at(0.2))

    def test_no_weather_strip_shrinks(self):
        with_wx = self.plot()
        no_wx = self.plot(with_weather=False)
        self.assertFalse(no_wx.has_wx)
        self.assertTrue(any(t.get_text().startswith("No forecast") for t in no_wx.axw.texts))
        self.assertLess(no_wx.axw.get_position().height, with_wx.axw.get_position().height * 0.6)
        self.assertGreater(no_wx.ax.get_position().height, with_wx.ax.get_position().height)

    def test_dst_night_label_and_ticks(self):
        p = self.plot(d=date(2026, 10, 31))           # US clocks go back at 02:00 on 1 Nov 2026
        self.assertEqual(p.ax.get_xlabel(), "Local time (UTC−4 → UTC−5)")
        labels = [t.get_text() for t in p.ax.get_xticklabels()]
        self.assertEqual(labels.count("01:00"), 2, "the repeated hour shows twice, on its real time")
        for j in (p.j0, p.j1):
            self.assertEqual(sky.to_local(j + 0.5 / 86400, p.tz).minute, 0)

    def test_southern_hemisphere_site(self):
        import planner
        import weather
        site = Site(name="Siding Spring", lat=-31.2733, lon=149.0617, tz="Australia/Sydney", max_airmass=2.0)
        _s, optics, opts, _t = golden_setup()
        tuc = Target(name="47 Tuc", kind="campaign", mode="D", ra=6.0236, dec=-72.0813, vmag=4.1)
        cen = Target(name="Fomalhaut", kind="variable", mode="V", ra=344.4127, dec=-29.6222, vmag=1.2)
        d = date(2026, 10, 22)
        hours = weather.parse_open_meteo(load("weather_openmeteo_sidingspring_20261022.json"))
        wx = weather.night_weather(hours, d, site, opts, source="Open-Meteo")
        night = planner.build_night(d, site, optics, [tuc, cen], opts)    # (that night's forecast: dew early)
        self.assertTrue(night.blocks, night.didnt_fit)
        mpl = nightplot._load()
        fig = mpl["Figure"](figsize=(12, 7), dpi=100)
        mpl["Agg"](fig)
        p = nightplot.NightPlot(night, site, {t.id: t for t in (tuc, cen)}, wx, opts, figure=fig).draw()
        fig.canvas.draw()
        self.assertEqual(p.ax.get_xlabel(), "Local time (UTC+11)")
        self.assertEqual(p.sky_levels[0], 0)
        self.assertEqual(max(p.sky_levels), 4)
        self.assertTrue(p.times["sunset"] < p.times["astro_dusk"] < p.times["astro_dawn"] < p.times["sunrise"])
        self.assertEqual(len(p.labels), len(night.blocks))
        self.assertTrue(p.has_wx)
        png = nightplot.png_bytes(night, site, {t.id: t for t in (tuc, cen)}, wx, opts)
        self.assertEqual(png_size(png)[0], 1500)

    def test_png_bytes_both_styles(self):
        n, site, by_id, opts = golden_night(date(2026, 10, 22))
        for style_ in (True, False):
            t0 = time.time()
            png = nightplot.png_bytes(n, site, by_id, n.weather, opts, print_style=style_)
            self.assertIsNotNone(png)
            w, h = png_size(png)
            self.assertEqual(w, 1500, "10 in at 150 dpi")
            self.assertGreater(w, h, "landscape")
            self.assertLess(time.time() - t0, 5.0)
        self.assertIsNone(nightplot.png_bytes(n, Site(), by_id, n.weather, opts), "site not set: None")
        self.assertIsNotNone(nightplot.png_bytes(n.to_dict(), site, {k: v.to_dict() for k, v in by_id.items()},
                                                 None, opts), "dicts and no weather work too")

    def test_print_style_is_light(self):
        p = self.plot(print_style=True)
        self.assertEqual(p.look["sky"][0], "#ffffff")
        self.assertEqual(p.fig.get_facecolor()[:3], (1.0, 1.0, 1.0))

    def test_narrow_plot_two_hour_ticks(self):
        wide = self.plot(size=(14, 8))
        narrow = self.plot(size=(5, 5))
        self.assertEqual(wide.every, 1)
        self.assertEqual(narrow.every, 2)

    def test_draw_is_fast(self):
        t0 = time.time()
        for _ in range(3):
            self.plot()
        self.assertLess((time.time() - t0) / 3, 1.5, "one night plot (build + render) well under 1.5 s")


class TestWithoutMatplotlib(unittest.TestCase):
    BLOCK = ("matplotlib", "matplotlib.figure", "matplotlib.backends", "matplotlib.backends.backend_agg",
             "matplotlib.backends.backend_tkagg", "matplotlib.colors", "matplotlib.ticker")

    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in self.BLOCK}
        for k in self.BLOCK:
            sys.modules[k] = None                     # "import matplotlib" now raises ImportError
        nightplot._load(force=True)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        nightplot._load(force=True)

    def test_module_without_matplotlib(self):
        self.assertFalse(nightplot.available())
        n = NightPlan(date="2026-10-22")
        site, _o, opts, _t = golden_setup()
        self.assertIsNone(nightplot.png_bytes(n, site, {}, None, opts))
        self.assertIsNone(nightplot.tk_canvas(None))
        self.assertEqual(nightplot.plan_blackouts(n, {}), [], "the plain helpers still work")


# ---- the Schedule tab (Tk) ------------------------------------------------------------------------------------------------

try:
    from tests.test_app import HAVE_TK, Base
except Exception:      # run from another folder
    HAVE_TK, Base = False, unittest.TestCase


@unittest.skipUnless(HAVE_TK and HAVE_MPL, "needs a display (xvfb-run) and matplotlib")
class TestScheduleTabPlot(Base):
    use_fakes = False

    def setUp(self):
        super().setUp()
        import urllib.request
        import weather
        self._urlopen = urllib.request.urlopen
        urllib.request.urlopen = lambda *_a, **_k: (_ for _ in ()).throw(OSError("network off in tests"))
        self._wx_get = weather.http_get_json
        reply = load("weather_openmeteo_shobs_20261022.json")
        weather.http_get_json = lambda url, timeout=None: reply
        self.sa._MODULES.clear()

    def tearDown(self):
        import urllib.request
        import weather
        urllib.request.urlopen = self._urlopen
        weather.http_get_json = self._wx_get
        super().tearDown()

    def built_tab(self):
        self.golden_home()
        app = self.open()
        page = app.pages[2]
        app.show_page(2)
        page.build(date(2026, 10, 22), 2)
        self.pump()
        self.settle(0.5)
        tab = page.tabs[0]
        self.assertIsNotNone(tab.plot, "the plot is drawn when the tab shows")
        return app, page, tab

    def click_at(self, tab, x_hours, airmass=1.5):
        p = tab.plot
        dx, dy = p.ax.transData.transform((x_hours, airmass))
        ev = types.SimpleNamespace(x=float(dx), y=float(dy), button=1)
        tab._plot_click(ev)

    def test_two_way_selection(self):
        app, page, tab = self.built_tab()
        p = tab.plot
        rows = tab.table.get_children()
        bl = tab.night().blocks_obj()
        # table → plot
        tab.table.selection_set(rows[1])
        app.update()
        self.assertEqual(p.selected, bl[int(rows[1])].target_id)
        # plot → table: click inside another block
        other = bl[int(rows[3])]
        calls = []
        real = p.set_highlight
        p.set_highlight = lambda tid: calls.append(tid) or real(tid)
        self.click_at(tab, float(p.x(0.5 * (other.start + other.end))))
        app.update()
        self.assertEqual(tab.table.selection(), (rows[3],))
        self.assertEqual(p.selected, other.target_id)
        self.assertLessEqual(len(calls), 3, f"no feedback loop: {calls}")
        self.assertFalse(tab._syncing)
        self.assertTrue(tab.detail_var.get().startswith(other.name), "the row's notes and actions follow")
        # a click beside the blocks clears both
        self.click_at(tab, 0.2)
        app.update()
        self.assertEqual(tab.table.selection(), ())
        self.assertIsNone(p.selected)
        # hover writes the info line
        hd = next(b for b in bl if b.name == "HD 219134")
        j = 0.5 * (hd.start + hd.end)
        x = float(p.x(j))
        dx, dy = p.ax.transData.transform((x, float(sky.target_airmass(hd.ra, hd.dec, app.site, j))))
        tab._plot_motion(types.SimpleNamespace(x=float(dx), y=float(dy)))
        self.assertIn("HD 219134: X", tab.hover_var.get())
        # the same night is not rebuilt by a selection or a re-show; a lock (the night changes) rebuilds it
        same = tab.plot
        tab.select_block(int(rows[0]))
        page._tab_changed()
        self.settle(0.2)
        self.assertIs(tab.plot, same)
        tab.lock_selected()
        self.settle(0.2)
        self.assertIsNot(tab.plot, same)
        self.assertEqual(tab.plot.selected, tab.night().blocks_obj()[int(rows[0])].target_id,
                         "the selection survives the rebuild")
        from matplotlib.colors import to_rgb
        box = next(lab for lab, _m, _n, _t, i in tab.plot.labels if i == int(rows[0]))
        self.assertEqual(tuple(box.get_bbox_patch().get_edgecolor()[:3]), to_rgb(nightplot.LOCK),
                         "locked: gold box edge")

    def test_schedule_tab_without_matplotlib(self):
        saved = {k: sys.modules.get(k) for k in TestWithoutMatplotlib.BLOCK}
        try:
            for k in saved:
                sys.modules[k] = None
            nightplot._load(force=True)
            self.golden_home()
            app = self.open()
            page = app.pages[2]
            app.show_page(2)
            page.build(date(2026, 10, 22), 1)
            self.pump()
            self.settle(0.3)
            tab = page.tabs[0]
            self.assertIsNone(tab.mpl_canvas)
            self.assertEqual(tab.plot_missing.cget("text"),
                             "⚠ Night plot needs matplotlib — run Create shortcuts.bat again")
            self.assertEqual(str(tab.plot_missing.cget("style")), "Warn.TLabel")
            rows = tab.table.get_children()
            self.assertGreater(len(rows), 3, "the table still works")
            tab.table.selection_set(rows[0])
            app.update()
            tab.lock_selected()
            self.assertTrue(tab.night().blocks_obj()[int(rows[0])].locked)
            self.assertFalse(tab.fit_height())
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            nightplot._load(force=True)


if __name__ == "__main__":
    unittest.main()
