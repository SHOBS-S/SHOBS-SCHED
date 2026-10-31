"""planner.py unit tests (golden-site based; network never used)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
_TMP = tempfile.mkdtemp(prefix="sched_planner_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "none.json")

import planner  # noqa: E402
import sky  # noqa: E402
from models import Block, NightPlan, Optics, Site, Target  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "golden_oct19_24.json")
MIN = 1 / 1440


def golden():
    with open(FIXTURE, encoding="utf-8") as fh:
        g = json.load(fh)
    return g, Site.from_dict(g["site"]), Optics.from_dict(g["optics"])


def gtarget(g, name, **over):
    td = next(t for t in g["targets"] if t["name"] == name)
    t = Target.from_dict(dict(td, id=name))
    for k, v in over.items():
        setattr(t, k, v)
    return t


def field_(name, ra, dec, block=105, **kw):
    kw.setdefault("vmag", 11.0)
    kw.setdefault("exposure_s", 60)
    return Target(id=name, name=name, kind="campaign", mode="D", ra=ra, dec=dec, block_min=block,
                  min_block_min=45, **kw)


def ra_on_meridian(site, jd):
    return float(sky.lst_deg(jd, site.lon))


class Exposure(unittest.TestCase):
    def setUp(self):
        _g, self.site, self.optics = golden()

    def test_anchor(self):
        t = Target(name="anchor", vmag=11.4, exposure_s=60)
        ex = planner.estimate_exposure(t, self.optics, {}, airmass=1.2)
        self.assertFalse(ex["is_estimate"])
        self.assertGreaterEqual(ex["peak_adu"], 10000)
        self.assertLessEqual(ex["peak_adu"], 17000)
        self.assertNotIn("⚠", ex["note"])

    def test_estimate_picks_standard_exposure(self):
        t = Target(name="x", vmag=11.4)
        ex = planner.estimate_exposure(t, self.optics, {}, airmass=1.2)
        self.assertTrue(ex["is_estimate"])
        self.assertIn(ex["exposure_s"], planner.STANDARD_EXPOSURES)
        self.assertIn("estimate", ex["note"])
        aim = 0.8 * self.optics.linear_limit_adu
        self.assertLessEqual(ex["peak_adu"], aim)
        i = planner.STANDARD_EXPOSURES.index(ex["exposure_s"])
        if i + 1 < len(planner.STANDARD_EXPOSURES):
            nxt = planner.predict_peak_adu(11.4, planner.STANDARD_EXPOSURES[i + 1], self.optics, {}, 1.2)
            self.assertGreater(nxt, aim)
        faint = planner.estimate_exposure(Target(vmag=15.5), self.optics, {})
        self.assertEqual(faint["exposure_s"], 300)

    def test_user_exposure_over_limit(self):
        ex = planner.estimate_exposure(Target(vmag=6.0, exposure_s=30), self.optics, {})
        self.assertFalse(ex["is_estimate"])
        self.assertTrue(ex["note"].startswith("✖"))                 # ≥ 2× the limit
        self.assertGreater(ex["peak_adu"], 2 * self.optics.linear_limit_adu)
        too_bright = planner.estimate_exposure(Target(vmag=2.0), self.optics, {})
        self.assertEqual(too_bright["exposure_s"], 0.75)
        self.assertTrue(too_bright["note"].startswith("⚠"))

    def test_placeholders(self):
        self.assertEqual(planner.estimate_exposure(Target(vmag=None), self.optics, {})["exposure_s"], 60)
        self.assertIn("not set up", planner.estimate_exposure(Target(vmag=10), Optics(), {})["note"])

    def test_brighter_sky_and_seeing(self):
        a = planner.predict_peak_adu(11.4, 60, self.optics, {}, 1.2, 0.0)
        b = planner.predict_peak_adu(11.4, 60, self.optics, {}, 1.2, 3.0)
        c = planner.predict_peak_adu(11.4, 60, self.optics, {"fwhm_arcsec": 2.0}, 1.2, 0.0)
        self.assertGreater(b, a)
        self.assertGreater(c, a)


class NightBasics(unittest.TestCase):
    def setUp(self):
        self.g, self.site, self.optics = golden()
        self.d = date(2026, 10, 21)
        self.info = sky.night_info(self.d, self.site)

    def build(self, targets, **kw):
        return planner.build_night(self.d, self.site, self.optics, targets, kw.pop("options", {}), **kw)

    def test_round_trip_and_fields(self):
        p = self.build([gtarget(self.g, "HD 219134"), gtarget(self.g, "NGC 7789")])
        p2 = NightPlan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(len(p2.blocks_obj()), 2)
        for b in p2.blocks_obj():
            for f in ("alt_start", "alt_end", "alt_min", "alt_max", "airmass_start", "airmass_best", "airmass_end",
                      "meridian", "moon_sep", "exposure_s", "frames", "ra", "dec"):
                self.assertIsNotNone(getattr(b, f), f)
            self.assertLessEqual(b.airmass_best, min(b.airmass_start, b.airmass_end) + 1e-9)
        hd = next(b for b in p2.blocks_obj() if b.name == "HD 219134")
        usable = hd.minutes() - (4.0 if hd.flip_in_block else 0.0)
        self.assertEqual(hd.frames, int(usable * 60 / (0.75 + 32.25)))
        self.assertTrue(p.flats and p.flats[0].startswith("Flats: one set at the night's camera angle"))
        self.assertIn("0.75 s", p.darks[0])

    def test_overheads(self):
        p = self.build([gtarget(self.g, "HD 219134"), gtarget(self.g, "NGC 7789"),
                        gtarget(self.g, "DY Peg"), gtarget(self.g, "XX Cyg")])
        bl = p.blocks_obj()
        self.assertGreaterEqual(bl[0].start, p.dusk + 5 * MIN - 1e-6)        # focus at the start of the night
        for a, b in zip(bl, bl[1:]):
            self.assertGreaterEqual(b.start - a.end, 3 * MIN - 1e-6)          # slew between blocks
        self.assertEqual(len({b.target_id for b in bl}), len(bl))             # each target at most once

    def test_defocus_refocus_and_flats(self):
        hd = gtarget(self.g, "HD 219134", notes="defocus to ~30 px")
        ngc = gtarget(self.g, "NGC 7789")
        p = self.build([hd, ngc])
        bl = p.blocks_obj()
        i = [b.name for b in bl].index("HD 219134")
        if i + 1 < len(bl):
            self.assertGreaterEqual(bl[i + 1].start - bl[i].end, (3 + 5) * MIN - 1e-6)
        self.assertIn("flats at the defocus position too", p.flats[0])

    def test_darks_on_hand(self):
        p = self.build([gtarget(self.g, "NGC 7789")], options={"darks_on_hand": [60]})
        self.assertTrue(p.darks[0].startswith("Darks: all on hand"))

    def test_flip_rule_moves_start(self):
        # a field whose meridian is at 21:00: the block starts flip_lead before it, never just after
        mer = sky.local_jd(self.d, 21, 0, self.site.tz)
        t = field_("F", ra_on_meridian(self.site, mer), 40.0)
        b = self.build([t]).blocks_obj()[0]
        self.assertAlmostEqual((b.meridian - b.start) * 1440, 12, delta=1.5)
        self.assertTrue(b.flip_in_block)
        self.assertIn("Start before the meridian and let it flip (~21:0", " ".join(b.notes))
        self.assertTrue(any("drop the pre-flip frames" in n for n in b.notes))

    def test_never_start_within_an_hour_after_meridian(self):
        # meridian 20:00 (before dark): starting before it is impossible, so the start waits until 21:00
        mer = sky.local_jd(self.d, 20, 0, self.site.tz)
        t = field_("Early", ra_on_meridian(self.site, mer), 40.0)
        b = self.build([t]).blocks_obj()[0]
        self.assertGreaterEqual((b.start - b.meridian) * 1440, 60 - 0.5)
        self.assertFalse(b.flip_in_block)

    def test_tracking_margin_note(self):
        # a field that sets before dawn's end ... use the K2-29 b night: transit block ends inside the margin
        g = self.g
        p = planner.build_night(date(2026, 10, 22), self.site, self.optics, [gtarget(g, "K2-29 b")], {})
        b = p.blocks_obj()[0]
        self.assertFalse(b.flip_in_block)
        self.assertTrue(any("inside your tracking margin; stop by" in n for n in b.notes), b.notes)
        self.assertIn("Ingress", b.notes[0])
        self.assertIsNotNone(b.frames_in_transit)
        self.assertIsNotNone(b.tc_sigma_min)

    def test_altaz_no_flip_keyhole(self):
        site = Site.from_dict(self.site.to_dict())
        site.mount = "altaz"
        mer = sky.local_jd(self.d, 23, 0, site.tz)
        t = field_("Zenith", ra_on_meridian(site, mer), site.lat + 1.0)
        b = planner.build_night(self.d, site, self.optics, [t], {}).blocks_obj()[0]
        self.assertFalse(b.flip_in_block)
        self.assertFalse(any("flip" in n.lower() for n in b.notes))
        self.assertTrue(any("Zenith keyhole" in c for c in b.cautions))
        self.assertAlmostEqual((b.start + b.end) / 2, b.meridian, delta=3 * MIN)   # centred

    def test_meridian_flip_during_transit(self):
        mer = sky.local_jd(self.d, 23, 0, self.site.tz)
        ra = ra_on_meridian(self.site, mer)
        tc = mer
        t0 = tc + float(sky.bjd_minus_jd_days(tc, ra, 30.0))
        t = Target(id="P", name="P b", kind="transit", mode="T", ra=ra, dec=30.0, vmag=11,
                   planet={"name": "P b", "period": 2.5, "t0": t0, "t14": 2 / 24, "depth": 0.01,
                           "t0_err": 0.02, "period_err": 0.0})
        b = self.build([t]).blocks_obj()[0]
        self.assertTrue(any("Meridian flip during the transit" in c for c in b.cautions), b.cautions)
        self.assertTrue(any("Ephemeris uncertain" in c for c in b.cautions), b.cautions)   # 0.02 d = 29 min

    def test_baseline_into_nautical_twilight(self):
        g = self.g
        p = planner.build_night(date(2026, 10, 23), self.site, self.optics, [gtarget(g, "HAT-P-23 b")], {})
        b = p.blocks_obj()[0]
        self.assertLess(b.start, p.dusk)
        self.assertGreaterEqual(b.start, p.nautical_dusk - 1e-6)

    def test_didnt_fit_reasons(self):
        g = self.g
        south = field_("Too south", 0.0, -60.0)
        nopos = Target(id="np", name="No pos", kind="campaign")
        noeph = Target(id="ne", name="No eph", kind="transit", ra=10.0, dec=20.0)
        toi = gtarget(g, "TOI-2046 b")                                          # no transit on 21 Oct
        moon = field_("Moon field", 0.0, 0.0, rules=[{"kind": "min_moon_sep", "value": 179}])
        p = self.build([south, nopos, noeph, toi, moon])
        why = {x["name"]: x["reason"] for x in p.didnt_fit}
        self.assertIn("below your limits all night", why["Too south"])
        self.assertIn("no position", why["No pos"])
        self.assertIn("no ephemeris", why["No eph"])
        self.assertIn("no transit tonight", why["TOI-2046 b"])
        self.assertIn("next chance", why["TOI-2046 b"])
        nc = next(x for x in p.didnt_fit if x["name"] == "TOI-2046 b")["next_chance"]
        self.assertEqual(nc, "2026-10-23")                                      # P = 1.497 d
        self.assertIn("Moon too close", why["Moon field"])
        self.assertEqual(p.blocks, [])

    def test_disabled_ignored(self):
        t = gtarget(self.g, "NGC 7789", enabled=False)
        p = self.build([t])
        self.assertEqual(p.blocks, [])
        self.assertEqual(p.didnt_fit, [])
        p = self.build([t], prefer={"NGC 7789"})
        self.assertEqual(len(p.blocks), 1)

    def test_free_gaps(self):
        p = self.build([gtarget(self.g, "HD 219134")])
        self.assertTrue(p.free)
        for a, b in p.free:
            self.assertGreaterEqual((b - a) * 1440, 15 - 1e-6)
        total = sum(b - a for a, b in p.free) + sum(b["end"] - b["start"] for b in p.blocks)
        self.assertAlmostEqual(total, p.dawn - p.dusk, delta=1e-6)

    def test_window_rule(self):
        t = gtarget(self.g, "NGC 7789", rules=[{"kind": "window", "value": "01:00-03:30"}], block_min=600)
        b = self.build([t]).blocks_obj()[0]
        self.assertGreaterEqual(b.start, sky.local_jd(self.d + timedelta(days=1), 1, 0, self.site.tz) - 1e-6)
        self.assertLessEqual(b.end, sky.local_jd(self.d + timedelta(days=1), 3, 30, self.site.tz) + 1e-6)

    def test_timed_variable_event(self):
        # an eclipsing binary with a minimum at 23:30 tonight
        mer = sky.local_jd(self.d, 23, 30, self.site.tz)
        ra = ra_on_meridian(self.site, mer)
        epoch = mer + float(sky.bjd_minus_jd_days(mer, ra, 40.0))
        t = Target(id="EB", name="EB Test", kind="variable", mode="V", ra=ra, dec=40.0, vmag=11,
                   elements={"vtype": "EA", "period": 2.7, "epoch": epoch, "epoch_kind": "min",
                             "any_time": False, "event_halfwidth_min": 60, "mag_max": 11, "mag_min": 11.8})
        b = self.build([t]).blocks_obj()[0]
        self.assertTrue(b.fixed)
        self.assertIn("Minimum at 23:30", b.notes[0])
        self.assertLessEqual(b.start, mer - 30 * MIN)
        self.assertGreaterEqual(b.end, mer + 30 * MIN)


class Blackouts(unittest.TestCase):
    def test_hd219134_oct22(self):
        g, site, optics = golden()
        d = date(2026, 10, 22)
        # alone, with a long block: it must stop before planet c's 04:28 transit
        hd = gtarget(g, "HD 219134", block_min=600)
        p = planner.build_night(d, site, optics, [hd], {})
        b = p.blocks_obj()[0]
        black = sky.local_jd(d + timedelta(days=1), 4, 28, site.tz)
        self.assertLessEqual(b.end, black + 2 * MIN)
        self.assertTrue(any(n.startswith("No HD 219134") and "(planet c transit)" in n for n in b.notes), b.notes)


class LockedAndWeather(unittest.TestCase):
    def setUp(self):
        self.g, self.site, self.optics = golden()
        self.d = date(2026, 10, 21)

    def test_locked_kept_exactly(self):
        s = sky.local_jd(self.d, 23, 0, self.site.tz)
        lb = Block(target_id="NGC 7789", name="NGC 7789", mode="D", kind="campaign", start=s, end=s + 2 / 24,
                   locked=True, status="planned")
        hd, ngc = gtarget(self.g, "HD 219134"), gtarget(self.g, "NGC 7789")
        p = planner.build_night(self.d, self.site, self.optics, [hd, ngc], {}, locked=[lb])
        bl = {b.name: b for b in p.blocks_obj()}
        self.assertEqual((bl["NGC 7789"].start, bl["NGC 7789"].end), (lb.start, lb.end))
        self.assertTrue(bl["NGC 7789"].locked)
        self.assertEqual(sum(1 for b in p.blocks if b["name"] == "NGC 7789"), 1)
        h = bl["HD 219134"]
        self.assertTrue(h.end + 3 * MIN <= lb.start + 1e-6 or h.start >= lb.end + 3 * MIN - 1e-6)

    def test_locked_impossible_warns(self):
        s = sky.local_jd(self.d, 23, 0, self.site.tz)
        lb = Block(target_id="S", name="Too south", start=s, end=s + 1 / 24, locked=True)
        p = planner.build_night(self.d, self.site, self.optics, [field_("Too south", 0.0, -60.0).__class__(
            id="S", name="Too south", kind="campaign", ra=0.0, dec=-60.0)], {}, locked=[lb])
        b = p.blocks_obj()[0]
        self.assertTrue(any("Below your limits" in c for c in b.cautions), b.cautions)

    def test_dew_and_cloud(self):
        dew = sky.local_jd(self.d + timedelta(days=1), 1, 0, self.site.tz)
        hours = [{"jd": sky.local_jd(self.d, 20, 0, self.site.tz) + k / 24, "cloud": 90 if k >= 2 else 0}
                 for k in range(11)]
        ngc = gtarget(self.g, "NGC 7789", block_min=600)
        p = planner.build_night(self.d, self.site, self.optics, [ngc], {},
                                weather={"hours": hours, "dew_onset": dew})
        b = p.blocks_obj()[0]
        self.assertLessEqual(b.end, dew + 1e-6)
        self.assertTrue(any("Dew expected from 01:00" in c for c in b.cautions), b.cautions)
        self.assertFalse(any("Cloud" in c for c in b.cautions), "clouds are a plain note (fix list 25)")
        self.assertTrue(any(n.startswith("Cloud") for n in b.notes), b.notes)
        self.assertTrue(any("Dew expected" in n for n in p.notes))
        # a fixed block crossing the dew onset only gets the caution
        p2 = planner.build_night(date(2026, 10, 22), self.site, self.optics, [gtarget(self.g, "K2-29 b")], {},
                                 weather={"hours": [], "dew_onset": sky.local_jd(date(2026, 10, 23), 2, 0,
                                                                                 self.site.tz)})
        k = p2.blocks_obj()[0]
        self.assertTrue(any("Dew expected from 02:00" in c for c in k.cautions), k.cautions)

    def test_no_site(self):
        p = planner.build_night(self.d, Site(), self.optics, [gtarget(self.g, "NGC 7789")], {})
        self.assertEqual(p.blocks, [])
        self.assertIn("site not set up", p.didnt_fit[0]["reason"])


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.g, self.site, self.optics = golden()
        self.targets = [gtarget(self.g, n) for n in ("HD 219134", "NGC 7789", "DY Peg", "K2-29 b")]

    def test_build_schedule_keeps_locked_and_status(self):
        start = date(2026, 10, 19)
        sch = planner.build_schedule(start, 3, self.site, self.optics, self.targets, {})
        self.assertEqual([n["date"] for n in sch.nights], ["2026-10-19", "2026-10-20", "2026-10-21"])
        n1 = sch.nights_obj()[1]
        b = n1.blocks_obj()[0]
        b.locked = True
        b.start += 7 * MIN
        b.end += 7 * MIN
        n1.blocks[0] = b.to_dict()
        n0 = sch.nights_obj()[0]
        b0 = n0.blocks_obj()[0]
        b0.status = "done"
        n0.blocks[0] = b0.to_dict()
        sch.nights[0], sch.nights[1] = n0.to_dict(), n1.to_dict()
        sch2 = planner.build_schedule(start, 3, self.site, self.optics, self.targets, {}, prior=sch)
        kept = [x for x in sch2.nights_obj()[1].blocks_obj() if x.locked]
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(kept[0].start, b.start, places=9)
        done = [x for x in sch2.nights_obj()[0].blocks_obj() if x.status == "done"]
        self.assertEqual(len(done), 1)
        self.assertAlmostEqual(done[0].start, b0.start, places=9)

    def test_rereview(self):
        start = date(2026, 10, 19)
        sch = planner.build_schedule(start, 7, self.site, self.optics, self.targets, {})
        n0 = sch.nights_obj()[0]
        bl = n0.blocks_obj()
        bl[0].status = "clouded"
        n0.blocks = [b.to_dict() for b in bl]
        sch.nights[0] = n0.to_dict()
        new = planner.rereview(sch, date(2026, 10, 21), self.site, self.optics, self.targets, {})
        self.assertEqual(len(new.nights), 7)
        self.assertEqual(new.nights[0]["date"], "2026-10-21")
        self.assertEqual(new.nights[-1]["date"], "2026-10-27")
        dates = {h["date"] for h in new.history}
        self.assertEqual(dates, {"2026-10-19", "2026-10-20"})
        self.assertTrue(any(h["status"] == "clouded" for h in new.history))
        self.assertTrue(all("minutes" in h for h in new.history))

    def test_campaign_behind_goal_moves_up(self):
        # Thursday 22 Oct: a goal of 5 a week with nothing done → behind (pace 2 by Thursday) → Normal → High
        d = date(2026, 10, 22)
        ngc = gtarget(self.g, "NGC 7789", goal_per_week=5, block_min=200)
        mer = sky.target_meridian(ngc.ra, ngc.dec, self.site, sky.local_jd(d, 23, 0, self.site.tz))
        rival = field_("Rival", ngc.ra, ngc.dec, block=200, priority="High")
        rival.ra = ngc.ra
        p = planner.build_night(d, self.site, self.optics, [rival, ngc], {}, history=[])
        first = min(p.blocks_obj(), key=lambda b: abs(b.start - (mer - 12 * MIN)))
        self.assertEqual(first.name, "NGC 7789")     # behind its goal: same level as High, goes first (name order)
        self.assertTrue(any("Behind its weekly goal" in n for n in first.notes))
        hist = [{"date": (d - timedelta(days=k)).isoformat(), "target_id": "NGC 7789", "status": "done"}
                for k in (1, 2, 3)]
        p2 = planner.build_night(d, self.site, self.optics, [rival, ngc], {}, history=hist)
        first2 = min(p2.blocks_obj(), key=lambda b: abs(b.start - (mer - 12 * MIN)))
        self.assertEqual(first2.name, "Rival")


def _transit_at(name, ra, dec, tc, t14_h=2.0, period=3.0):
    t0 = tc + float(sky.bjd_minus_jd_days(tc, ra, dec))
    return Target(id=name, name=name, kind="transit", mode="T", ra=ra, dec=dec, vmag=11, exposure_s=60,
                  planet={"name": name, "period": period, "t0": t0, "t14": t14_h / 24, "depth": 0.01,
                          "t0_err": 0.0003, "period_err": 1e-6})


class WeeklyGoal(unittest.TestCase):
    """Pace-aware 'behind its weekly goal' (Mon–Sun weeks; 19 Oct 2026 is a Monday)."""

    def setUp(self):
        self.g, self.site, self.optics = golden()

    def behind(self, d, goal, done_days):
        ngc = gtarget(self.g, "NGC 7789", goal_per_week=goal, block_min=120)
        hist = [{"date": (d - timedelta(days=k)).isoformat(), "target_id": "NGC 7789", "status": "done"}
                for k in done_days]
        p = planner.build_night(d, self.site, self.optics, [ngc], {}, history=hist)
        b = p.blocks_obj()[0]
        return any("Behind its weekly goal" in n for n in b.notes)

    def test_goal_1(self):
        mon, tue = date(2026, 10, 19), date(2026, 10, 20)
        self.assertTrue(self.behind(mon, 1, []))
        self.assertTrue(self.behind(date(2026, 10, 23), 1, []))
        self.assertFalse(self.behind(tue, 1, [1]))              # done on Monday: goal met

    def test_goal_2(self):
        self.assertTrue(self.behind(date(2026, 10, 19), 2, []))          # Mon: ceil(2/7) = 1
        self.assertFalse(self.behind(date(2026, 10, 20), 2, [1]))        # Tue with Monday done: on pace
        self.assertFalse(self.behind(date(2026, 10, 21), 2, [2]))        # Wed: ceil(6/7) = 1
        self.assertTrue(self.behind(date(2026, 10, 22), 2, [3]))         # Thu: ceil(8/7) = 2 > 1
        self.assertTrue(self.behind(date(2026, 10, 24), 2, []))          # Sat: 2 nights left, 2 needed
        self.assertFalse(self.behind(date(2026, 10, 25), 2, [1, 2]))     # met

    def test_unmet_goal_before_goalless_same_priority(self):
        d = date(2026, 10, 21)
        ngc = gtarget(self.g, "NGC 7789", goal_per_week=1, block_min=200)
        # a goal-less any-time variable at the same spot of the sky, same priority, would otherwise go first
        twin = Target(id="Twin", name="AA Twin", kind="variable", mode="V", ra=ngc.ra, dec=ngc.dec, vmag=11,
                      block_min=200, min_block_min=45, exposure_s=60,
                      elements={"vtype": "DSCT", "period": 0.1, "epoch": 2460000.5, "any_time": True})
        hist = [{"date": "2026-10-19", "target_id": "NGC 7789", "status": "clouded"}]
        p = planner.build_night(d, self.site, self.optics, [twin, ngc], {}, history=hist)
        mer = sky.target_meridian(ngc.ra, ngc.dec, self.site, sky.local_jd(d, 23, 30, self.site.tz))
        first = min(p.blocks_obj(), key=lambda b: abs(b.start - (mer - 12 * MIN)))
        self.assertEqual(first.name, "NGC 7789")


class ReviewFixes(unittest.TestCase):
    def setUp(self):
        self.g, self.site, self.optics = golden()
        self.d = date(2026, 10, 21)

    def jd(self, hh, mm):
        day = self.d if hh >= 12 else self.d + timedelta(days=1)
        return sky.local_jd(day, hh, mm, self.site.tz)

    def test_fixed_start_just_before_meridian_moves_earlier(self):
        # ingress 23:00, wanted start 22:00, meridian 22:03: the start goes to 21:51 (12 min before)
        ra = ra_on_meridian(self.site, self.jd(22, 3))
        t = _transit_at("Close b", ra, 45.0, self.jd(0, 0))
        b = planner.build_night(self.d, self.site, self.optics, [t], {}).blocks_obj()[0]
        self.assertAlmostEqual((b.meridian - b.start) * 1440, 12, delta=1.5)
        self.assertIn("(after the flip)", b.notes[1])

    def test_tracking_margin_with_meridian_in_transit(self):
        site = Site.from_dict(self.site.to_dict())
        site.past_meridian_min = 60
        mer = self.jd(23, 40)
        t = _transit_at("Wide b", ra_on_meridian(site, mer), 45.0, self.jd(23, 0))   # t4 = 00:00
        b = planner.build_night(self.d, site, self.optics, [t], {}).blocks_obj()[0]
        self.assertFalse(b.flip_in_block)
        self.assertLessEqual(b.end, b.meridian + 60 * MIN + 1e-6)
        self.assertGreaterEqual(b.end, b.t4 + 20 * MIN - 1e-6)
        self.assertTrue(any("inside your tracking margin" in n for n in b.notes), b.notes)

    def test_exposure_wording(self):
        lim = self.optics.linear_limit_adu
        hd = gtarget(self.g, "HD 219134")
        self.assertIn("defocus", hd.notes.lower())
        ex = planner.estimate_exposure(hd, self.optics, {})
        self.assertTrue(ex["note"].startswith("Your exposure; defocused (stars ~1.5× wider): predicted peak ~"),
                        ex["note"])
        ex = planner.estimate_exposure(Target(vmag=11.4, exposure_s=60), self.optics, {})
        self.assertTrue(ex["note"].startswith("Your exposure; predicted peak"))
        found = {}
        for v10 in range(60, 120):
            v = v10 / 10
            ex = planner.estimate_exposure(Target(vmag=v, exposure_s=60), self.optics, {})
            r = ex["peak_adu"] / lim
            found.setdefault("ok" if r <= 1.5 else "warn" if r < 2 else "hard", ex["note"])
        self.assertTrue(found["warn"].startswith("⚠ estimate: peak ~"))
        self.assertIn("near your 32k limit — check a frame", found["warn"])
        self.assertTrue(found["hard"].startswith("✖"))
        # a 1.2× peak is not flagged at all
        for v10 in range(80, 130):
            ex = planner.estimate_exposure(Target(vmag=v10 / 10, exposure_s=60), self.optics, {})
            if 1.0 < ex["peak_adu"] / lim < 1.5:
                self.assertFalse(ex["note"].startswith(("⚠", "✖")))

    def test_golden_hd_has_no_exposure_caution(self):
        p = planner.build_night(self.d, self.site, self.optics, [gtarget(self.g, "HD 219134")], {})
        b = p.blocks_obj()[0]
        self.assertFalse([c for c in b.cautions if "limit" in c], b.cautions)
        self.assertIn("Refocus after this block", b.notes)

    def _crowded(self, b_kind):
        """Two targets that both want the 22:00–01:00 window (Wed 21 Oct); A (alphabetically first) fills it."""
        win = [{"kind": "window", "value": "22:00-01:00"}]
        ra = ra_on_meridian(self.site, self.jd(23, 30))
        a = field_("A big", ra, 45.0, block=180, rules=win)
        if b_kind == "variable":
            b = Target(id="B small", name="B small", kind="variable", mode="V", ra=ra + 5, dec=45.0, vmag=11,
                       block_min=120, min_block_min=45, exposure_s=60, rules=win,
                       elements={"vtype": "DSCT", "period": 0.08, "epoch": 2460000.5, "any_time": True})
        else:
            b = field_("B small", ra + 5, 45.0, block=120, rules=win)
            if b_kind == "behind":
                # both behind their goal (nothing done this week, Wednesday): same level, A goes first by name,
                # so B gets nothing in the first pass and A shrinks for it
                a.goal_per_week = b.goal_per_week = 1
            elif b_kind == "on_pace":
                b.goal_per_week = 1
        hist = ([{"date": "2026-10-19", "target_id": "B small", "status": "done"}] if b_kind == "on_pace" else [])
        return planner.build_night(self.d, self.site, self.optics, [a, b], {}, history=hist)

    def test_second_pass_for_campaign_behind_goal(self):
        p = self._crowded("behind")
        names = {x.name: x for x in p.blocks_obj()}
        self.assertEqual(set(names), {"A big", "B small"}, p.didnt_fit)
        self.assertTrue(any("Shortened to make room for B small" in n for n in names["A big"].notes))
        self.assertGreaterEqual(names["A big"].minutes(), 45 - 1e-6)
        self.assertGreaterEqual(names["B small"].minutes(), 45 - 1e-6)

    def test_no_shrinking_for_others(self):
        # "Shorten for goals only": an any-time variable, a goal-less campaign and a campaign on pace get no room
        for kind in ("variable", "plain", "on_pace"):
            p = self._crowded(kind)
            self.assertEqual([x.name for x in p.blocks_obj()], ["A big"], kind)
            self.assertFalse(any("Shortened" in n for n in p.blocks_obj()[0].notes), kind)
            miss = p.didnt_fit[0]
            self.assertEqual(miss["name"], "B small")
            self.assertIn("no free stretch", miss["reason"])
            self.assertIn("next chance", miss["reason"])

    def test_counterweight_up(self):
        # dec 86°: always above 30° here; on a night when it is below the pole, |HA| > 6 h all night
        ra = ra_on_meridian(self.site, self.jd(13, 0))
        t = field_("Polar", ra, 86.0, block=60)
        b = planner.build_night(self.d, self.site, self.optics, [t], {}).blocks_obj()[0]
        self.assertTrue(any(c.startswith("⚠ Counterweight up beyond HA ±6 h (") for c in b.cautions), b.cautions)
        ok = planner.build_night(self.d, self.site, self.optics, [gtarget(self.g, "NGC 7789")], {})
        self.assertFalse(any("Counterweight" in c for c in ok.blocks_obj()[0].cautions))

    def test_bad_zone(self):
        site = Site.from_dict(self.site.to_dict())
        site.tz = "Mars/Olympus_Mons"
        p = planner.build_night(self.d, site, self.optics, [gtarget(self.g, "NGC 7789")], {})
        self.assertIn("✖ Time zone 'Mars/Olympus_Mons' not available; times shown in UTC", p.notes)

    def test_running_records_and_roll_forward(self):
        """rebuild of one night gets the same pacing records build_schedule passed; Build/Re-review roll past
        nights (with their marks) into the history."""
        from models import Schedule
        mk = lambda d, st: NightPlan(date=d, blocks=[Block(target_id="X", name="X", start=1.0, end=1.0 + 1 / 24,
                                                           status=st).to_dict()]).to_dict()
        sch = Schedule(nights=[mk("2026-10-19", "done"), mk("2026-10-20", "planned"), mk("2026-10-21", "planned")],
                       history=[{"date": "2026-10-12", "target_id": "X", "status": "done"}])
        recs = planner.history_for_night(sch, "2026-10-21")
        self.assertEqual([r["date"] for r in recs], ["2026-10-12", "2026-10-19", "2026-10-20"])
        self.assertTrue(all(r.get("scheduled") for r in recs[1:]))
        self.assertEqual(recs[1]["status"], "done")
        rolled = planner.roll_forward(sch, date(2026, 10, 20))
        self.assertEqual([n["date"] for n in rolled.nights], ["2026-10-20", "2026-10-21"])
        self.assertEqual(rolled.history[-1]["status"], "done")
        self.assertEqual(rolled.history[-1]["date"], "2026-10-19")

    def test_clouded_block_not_replanned_same_night(self):
        """A Clouded-out / Partial mark stays as the night's record; the target is not planned AGAIN that night
        (it returns to the pool for later nights). Re-planning it duplicated the target on every rebuild."""
        s = self.jd(20, 30)
        for st in ("clouded", "partial"):
            cl = Block(target_id="NGC 7789", name="NGC 7789", mode="D", kind="campaign", start=s, end=s + 1 / 24,
                       status=st)
            p = planner.build_night(self.d, self.site, self.optics, [gtarget(self.g, "NGC 7789")], {}, locked=[cl])
            bl = [b for b in p.blocks_obj() if b.name == "NGC 7789"]
            self.assertEqual([b.status for b in bl], [st])
            # rebuilding the rebuilt night again changes nothing (no duplicates)
            again = planner.build_night(self.d, self.site, self.optics, [gtarget(self.g, "NGC 7789")], {},
                                        locked=[b for b in p.blocks_obj() if b.status != "planned"])
            self.assertEqual(len([b for b in again.blocks_obj() if b.name == "NGC 7789"]), 1)
        done = Block.from_dict(dict(cl.to_dict(), status="done"))
        p = planner.build_night(self.d, self.site, self.optics, [gtarget(self.g, "NGC 7789")], {}, locked=[done])
        self.assertEqual(len(p.blocks), 1)

    def test_reversed_user_block_and_bad_history(self):
        s = self.jd(23, 0)
        rev = Block(target_id="NGC 7789", name="NGC 7789", start=s, end=s - 1 / 24, set_by_user=True)
        hist = [{"date": None, "target_id": "NGC 7789", "status": "done"}, {"target_id": "NGC 7789"}]
        ngc = gtarget(self.g, "NGC 7789", goal_per_week=2)
        p = planner.build_night(self.d, self.site, self.optics, [ngc], {}, locked=[rev], history=hist)
        b = p.blocks_obj()[0]
        self.assertTrue(any("Ends before it starts" in c for c in b.cautions), b.cautions)

    def test_same_star_needs_no_slew(self):
        site = Site.from_dict(self.site.to_dict())
        site.mount = "altaz"
        ra = ra_on_meridian(site, self.jd(23, 30))
        pb = _transit_at("Star b", ra, 45.0, self.jd(22, 0), 1.5, 3.1)     # 21:15–22:45
        pc = _transit_at("Star c", ra, 45.0, self.jd(1, 0), 1.5, 7.3)      # 00:15–01:45
        bl = planner.build_night(self.d, site, self.optics, [pb, pc], {}).blocks_obj()
        self.assertEqual(len(bl), 2)
        self.assertLess((bl[1].start - bl[0].end) * 1440, 1.0)
        self.assertGreaterEqual(bl[1].start, bl[0].end - 1e-9)

    def test_moon_rule_only_while_moon_up(self):
        # 3 Nov: the Moon rises at ~02:12. A field ~51° from it with a 60° rule is fine until moonrise.
        d = date(2026, 11, 3)
        info = sky.night_info(d, self.site)
        mra, mdec, _ = sky.moon_radec(info["moonrise"])
        t = field_("Near Moon", float(mra) - 45.0, float(mdec) + 30.0, block=120,
                   rules=[{"kind": "min_moon_sep", "value": 60}])
        self.assertLess(float(sky.target_moon_sep(t.ra, t.dec, info["moonrise"] + 0.05)), 60)
        p = planner.build_night(d, self.site, self.optics, [t], {})
        self.assertTrue(p.blocks, p.didnt_fit)
        self.assertLessEqual(p.blocks_obj()[0].end, info["moonrise"] + 2 * MIN)

    def test_polar_sunset_none(self):
        site = Site(name="Tromsø", lat=69.65, lon=18.96, tz="Europe/Oslo")
        info = sky.night_info(date(2026, 6, 20), site)
        self.assertIsNone(info["sunset"])
        self.assertIsNone(info["sunrise"])
        info = sky.night_info(date(2026, 12, 20), site)                 # polar night
        self.assertIsNone(info["sunset"])
        self.assertIsNone(info["sunrise"])
        self.assertIsNotNone(info["dusk"])

    def test_limit_value_in_reason(self):
        p = planner.build_night(self.d, self.site, self.optics, [field_("Too south", 0.0, -60.0)], {})
        self.assertIn("limit 30°", p.didnt_fit[0]["reason"])

    def test_variable_events_per_event_correction(self):
        import events
        from models import VarElements
        el = VarElements(vtype="EA", period=1.37, epoch=2460000.123, epoch_kind="min", any_time=False)
        ra, dec = 90.0, 20.0
        j0 = 2461300.5
        evs = events.variable_events(el, ra, dec, j0, j0 + 200)
        self.assertTrue(evs)
        for e in evs:
            t_b = el.epoch + e["cycle"] * el.period
            self.assertAlmostEqual(e["t"], t_b - float(sky.bjd_minus_jd_days(t_b, ra, dec)), places=7)
            self.assertTrue(j0 <= e["t"] <= j0 + 200)



class CadenceDefocus(unittest.TestCase):
    """22 Oct testing: frames from the real ASIAIR cycle; defocused peaks; (camera angle is in test_export)."""

    def setUp(self):
        self.g, self.site, self.optics = golden()
        self.d = date(2026, 10, 21)

    def test_frame_cycle_precedence(self):
        op = Optics.from_dict(self.optics.to_dict())
        t = Target(exposure_s=0.75)                              # old default 3 s, no Setup value: as before
        self.assertEqual(planner.frame_cycle_s(t, 0.75, op), (3.75, "default"))
        op.frame_overhead_s = 32.0                               # Setup's overhead replaces the old default
        self.assertEqual(planner.frame_cycle_s(t, 0.75, op), (32.75, "setup"))
        t.cadence_overhead_s = 10.0                              # a target's own old value still applies
        self.assertEqual(planner.frame_cycle_s(t, 0.75, op), (10.75, "target"))
        t.cycle_s = 33.0                                         # seconds between frames wins
        self.assertEqual(planner.frame_cycle_s(t, 0.75, op), (33.0, "cycle"))
        self.assertEqual(planner.frame_cycle_s(t, 60.0, op)[0], 60.0, "never shorter than the exposure")
        t2 = Target.from_dict({"name": "old", "exposure_s": 5})  # an old file without the new fields
        self.assertIsNone(t2.cycle_s)
        self.assertIsNone(Optics.from_dict({}).frame_overhead_s)

    def test_hd219134_frames_with_cycle(self):
        hd = gtarget(self.g, "HD 219134", cycle_s=33.0, rules=[])
        b = planner.build_night(self.d, self.site, self.optics, [hd], {}).blocks_obj()[0]
        mins = (b.end - b.start) * 1440
        self.assertEqual(b.cycle_s, 33.0)
        self.assertLessEqual(b.frames, int(mins * 60 / 33) + 1)
        self.assertGreater(b.frames, 150)
        self.assertLess(b.frames, 200, "about 190 frames for 105 min, not ~1,600")
        self.assertIn("Frames every 33 s (your seconds between frames)", b.notes)
        # Setup's overhead with no per-target cycle
        op = Optics.from_dict(self.optics.to_dict())
        op.frame_overhead_s = 32.0
        hd2 = gtarget(self.g, "HD 219134", cadence_overhead_s=3.0, rules=[])
        b2 = planner.build_night(self.d, self.site, op, [hd2], {}).blocks_obj()[0]
        self.assertAlmostEqual(b2.cycle_s, 32.75)
        self.assertFalse(any("Frames every" in n for n in b2.notes))

    def test_frames_in_transit_use_cycle(self):
        t = gtarget(self.g, "K2-29 b")
        d = date(2026, 10, 22)
        base = planner.build_night(d, self.site, self.optics, [t], {}).blocks_obj()
        t.cycle_s = float(t.exposure_s or 60) * 4
        slow = planner.build_night(d, self.site, self.optics, [t], {}).blocks_obj()
        self.assertTrue(base and slow and base[0].frames_in_transit)
        self.assertLess(slow[0].frames_in_transit, base[0].frames_in_transit)

    def test_defocused_peak(self):
        hd = gtarget(self.g, "HD 219134", exposure_s=None)
        infocus = planner.predict_peak_adu(hd.vmag, 0.75, self.optics, {})
        defoc = planner.predict_peak_adu(hd.vmag, 0.75, self.optics, {}, defocused=True)
        self.assertGreater(infocus, 35000)
        self.assertLess(defoc, infocus / 1.8, "1.5x wider stars: under half the peak")
        self.assertTrue(15000 < defoc < 23000, f"default matches HD 219134 at +125 steps (~19k): {defoc:,.0f}")
        two = planner.predict_peak_adu(hd.vmag, 0.75, self.optics, {"defocus_fwhm_factor": 2.0}, defocused=True)
        self.assertLess(two, infocus / 3, "2x wider stars: about a quarter of the peak")
        wider = planner.predict_peak_adu(hd.vmag, 0.75, self.optics, {"defocus_fwhm_factor": 3.0}, defocused=True)
        self.assertLess(wider, defoc)
        ex = planner.estimate_exposure(hd, self.optics, {})
        self.assertIn("defocused", ex["note"])
        self.assertFalse(ex["note"].startswith("⚠"), ex["note"])
        self.assertLess(ex["peak_adu"], 0.8 * self.optics.linear_limit_adu + 1)
        # a brighter defocused star that still saturates says so, with "defocused"
        bright = Target(vmag=1.0, notes="defocus", exposure_s=None)
        ex = planner.estimate_exposure(bright, self.optics, {})
        self.assertTrue(ex["note"].startswith("⚠ estimate: even 0.75 s may saturate defocused"), ex["note"])
        # the user's exposure on a defocused target gets a peak estimate
        ex = planner.estimate_exposure(Target(vmag=5.57, notes="Defocus +125 steps", exposure_s=0.75),
                                       self.optics, {})
        self.assertIsNotNone(ex["peak_adu"])
        self.assertIn("defocused", ex["note"])
        self.assertNotIn("not valid", ex["note"])


if __name__ == "__main__":
    unittest.main()
