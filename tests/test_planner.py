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
        self.assertTrue(ex["note"].startswith("⚠"))
        self.assertGreater(ex["peak_adu"], self.optics.linear_limit_adu)
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
        self.assertTrue(any("Cloud" in c for c in b.cautions), b.cautions)
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


if __name__ == "__main__":
    unittest.main()
