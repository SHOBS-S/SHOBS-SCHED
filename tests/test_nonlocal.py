"""Non-local sites and clocks: southern hemisphere, a site zone different from the computer's, the US daylight-saving
night, a high-latitude summer night without astronomical darkness, and fixed-time collisions."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
_TMP = tempfile.mkdtemp(prefix="sched_nonlocal_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "none.json")


import planner  # noqa: E402
import sky  # noqa: E402
from models import Optics, Site, Target  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "golden_oct19_24.json")
OPTICS = Optics(aperture_mm=356, focal_mm=2900, pixel_um=3.76, color_camera=True, e_per_adu=0.243,
                linear_limit_adu=32000)


def golden():
    with open(FIXTURE, encoding="utf-8") as fh:
        g = json.load(fh)
    return g, Site.from_dict(g["site"])


def campaign(name, ra, dec, block=105, **kw):
    return Target(id=name, name=name, kind="campaign", mode="D", ra=ra, dec=dec, vmag=11.0, block_min=block,
                  min_block_min=45, exposure_s=60, **kw)


def transit(name, ra, dec, tc_jd, t14_h, period, priority="Normal", depth=0.01, vmag=11.0):
    """A synthetic planet whose transit is centred on tc_jd (JD UTC); t0 in BJD_TDB as the Archive gives it."""
    t0 = tc_jd + float(sky.bjd_minus_jd_days(tc_jd, ra, dec))
    pl = {"name": name, "period": period, "period_err": 1e-6, "t0": t0, "t0_err": 0.0005, "t14": t14_h / 24.0,
          "depth": depth}
    return Target(id=name, name=name, kind="transit", mode="T", ra=ra, dec=dec, vmag=vmag, priority=priority,
                  planet=pl, exposure_s=60)


def ra_on_meridian(site, jd):
    """J2000 RA (deg) that is on the meridian at jd (precession ignored: < 0.5° in 2026)."""
    return float(sky.lst_deg(jd, site.lon))


class SouthernSite(unittest.TestCase):
    def setUp(self):
        self.site = Site(name="Siding Spring", lat=-31.27, lon=149.07, elevation_m=1165, tz="Australia/Sydney",
                         min_alt=30, max_airmass=2.0, sun_alt=-18.0, mount="GEM", past_meridian_min=5)
        self.d = date(2026, 10, 20)

    def test_dark_found(self):
        info = sky.night_info(self.d, self.site)
        self.assertTrue(info["dark_ok"])
        dusk, dawn = sky.to_local(info["dusk"], self.site.tz), sky.to_local(info["dawn"], self.site.tz)
        self.assertEqual(dusk.utcoffset().total_seconds(), 11 * 3600)        # AEDT
        self.assertIn(dusk.hour, (20, 21))
        self.assertIn(dawn.hour, (4, 5))

    def test_flips_by_hour_angle(self):
        mer_wanted = sky.local_jd(self.d, 23, 30, self.site.tz)
        ra = ra_on_meridian(self.site, mer_wanted)
        t = campaign("South field", ra, -10.0)              # culminates to the NORTH of the zenith here
        plan = planner.build_night(self.d, self.site, OPTICS, [t], {})
        self.assertEqual(len(plan.blocks), 1, plan.didnt_fit)
        b = plan.blocks_obj()[0]
        self.assertAlmostEqual((b.meridian - mer_wanted) * 1440, 0, delta=3)
        az = float(sky.target_az(ra, -10.0, self.site, b.meridian))
        self.assertTrue(az < 2 or az > 358, az)             # meridian to the north
        alt_m = float(sky.target_alt(ra, -10.0, self.site, b.meridian))
        self.assertAlmostEqual(alt_m, sky.culmination_alt(-10.0, -31.27), delta=0.6)
        # GEM flip rule: start flip_lead before the meridian and let it flip
        self.assertAlmostEqual((b.meridian - b.start) * 1440, 12, delta=1.5)
        self.assertTrue(b.flip_in_block)
        self.assertTrue(any("Start before the meridian and let it flip" in n for n in b.notes), b.notes)
        self.assertRegex(" ".join(b.notes), r"~\d\d:\d\d")
        self.assertIn(sky.hhmm(b.meridian, self.site.tz), ("23:29", "23:30", "23:31"))   # precession ~1 min


class ZoneNotTheComputers(unittest.TestCase):
    """The computer's zone (TZ env) must not change anything: the site's zone rules."""

    def build_all(self):
        g, site = golden()
        out = []
        for n in g["nights"]:
            ts = []
            for td in g["targets"]:
                t = Target.from_dict(dict(td, id=td["name"]))
                t.enabled = t.name in n["enabled"]
                ts.append(t)
            p = planner.build_night(date.fromisoformat(n["date"]), site, Optics.from_dict(g["optics"]), ts, {})
            out.append([(b["name"], round(b["start"], 6), round(b["end"], 6), tuple(b["notes"]))
                        for b in p.blocks] + [round(p.dusk, 6), round(p.dawn, 6)])
        return out

    def test_same_answers_in_tokyo(self):
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            here = self.build_all()
            os.environ["TZ"] = "Asia/Tokyo"
            time.tzset()
            tokyo = self.build_all()
            os.environ["TZ"] = "America/Los_Angeles"
            time.tzset()
            la = self.build_all()
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.assertEqual(here, tokyo)
        self.assertEqual(here, la)

    def test_golden_times_whatever_the_env(self):
        # whatever TZ this process runs under (the suite is also run with TZ=Asia/Tokyo), the clock is EDT
        g, site = golden()
        info = sky.night_info(date(2026, 10, 20), site)
        self.assertEqual(sky.hhmm(info["dusk"], site.tz), "20:11")
        self.assertEqual(sky.hhmm(info["dawn"], site.tz), "06:14")


class DaylightSavingNight(unittest.TestCase):
    """US clocks go back at 02:00 on Sun 1 Nov 2026: the night that starts on Sat 31 Oct lasts 25 h."""

    def test_25_hour_night(self):
        _g, site = golden()
        j0, j1 = sky.night_span(date(2026, 10, 31), site)
        self.assertAlmostEqual((j1 - j0) * 24, 25.0, places=4)
        j0, j1 = sky.night_span(date(2026, 11, 1), site)
        self.assertAlmostEqual((j1 - j0) * 24, 24.0, places=4)

    def test_dawn_still_right(self):
        _g, site = golden()
        before = sky.night_info(date(2026, 10, 30), site)
        dst = sky.night_info(date(2026, 10, 31), site)
        after = sky.night_info(date(2026, 11, 1), site)
        for info in (before, dst, after):
            self.assertAlmostEqual(float(sky.sun_alt_at(site, info["dawn"])), -18.0, delta=0.05)
        b_local, d_local = sky.to_local(before["dawn"], site.tz), sky.to_local(dst["dawn"], site.tz)
        self.assertEqual(b_local.utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(d_local.utcoffset().total_seconds(), -5 * 3600)
        # the clock goes back an hour; dawn itself moves ~1 min later in UT
        clock_shift = (d_local.hour * 60 + d_local.minute) - (b_local.hour * 60 + b_local.minute)
        self.assertAlmostEqual(clock_shift, -59, delta=2)
        self.assertAlmostEqual((dst["dawn"] - before["dawn"] - 1) * 1440, 1.0, delta=1.0)

    def test_plan_across_the_change(self):
        _g, site = golden()
        d = date(2026, 10, 31)
        info = sky.night_info(d, site)
        # a field whose meridian is near dawn: its block runs up to dawn, across the 02:00 change
        ra = ra_on_meridian(site, info["dawn"] + 1 / 24)
        t = campaign("Dawn field", ra, 40.0, block=600)
        plan = planner.build_night(d, site, OPTICS, [t], {})
        b = plan.blocks_obj()[0]
        self.assertLessEqual(b.end, plan.dawn + 1e-6)
        self.assertGreater(b.end, plan.dawn - 3 / 1440)
        self.assertEqual(sky.hhmm(b.end, site.tz), sky.hhmm(plan.dawn, site.tz))
        self.assertTrue(sky.hhmm(plan.dawn, site.tz).startswith("05:"))


class HighLatitude(unittest.TestCase):
    def test_no_dark_in_june(self):
        site = Site(name="Tromsø", lat=69.65, lon=18.96, tz="Europe/Oslo", min_alt=30, sun_alt=-18.0)
        t = campaign("Polaris field", 37.95, 89.26)
        plan = planner.build_night(date(2026, 6, 20), site, OPTICS, [t], {})
        self.assertEqual(plan.blocks, [])
        self.assertIsNone(plan.dusk)
        self.assertTrue(any("no dark time" in n.lower() for n in plan.notes), plan.notes)
        self.assertEqual(len(plan.didnt_fit), 1)
        self.assertIn("no dark time tonight", plan.didnt_fit[0]["reason"])

    def test_brighter_darkness_allowed(self):
        # early September in Tromsø: no astronomical darkness, but nautical darkness exists
        site = Site(name="Tromsø", lat=69.65, lon=18.96, tz="Europe/Oslo", min_alt=30, sun_alt=-18.0)
        d = date(2026, 9, 5)
        astro = planner.build_night(d, site, OPTICS, [campaign("Polaris field", 37.95, 89.26, block=60)], {})
        self.assertEqual(astro.blocks, [])
        self.assertIn("−12", " ".join(astro.notes))
        site.sun_alt = -12.0
        naut = planner.build_night(d, site, OPTICS, [campaign("Polaris field", 37.95, 89.26, block=60)], {})
        self.assertEqual(len(naut.blocks), 1, naut.didnt_fit)


class Collisions(unittest.TestCase):
    """Two transits whose wanted windows overlap (alt-az site: no flips to muddy the arithmetic)."""

    def setUp(self):
        _g, site = golden()
        site.mount = "altaz"
        self.site = site
        self.d = date(2026, 10, 20)
        self.ra = ra_on_meridian(site, sky.local_jd(self.d, 23, 30, site.tz))

    def jd(self, hh, mm):
        day = self.d if hh >= 12 else self.d + timedelta(days=1)
        return sky.local_jd(day, hh, mm, self.site.tz)

    def test_trim_keeps_both(self):
        a = transit("Alpha b", self.ra, 35.0, self.jd(22, 30), 2.0, 3.0)        # 21:30–23:30
        b = transit("Beta b", self.ra + 10, 35.0, self.jd(1, 45), 1.5, 3.7)     # 01:00–02:30
        plan = planner.build_night(self.d, self.site, OPTICS, [a, b], {})
        self.assertEqual(len(plan.blocks), 2, plan.didnt_fit)
        ba, bb = plan.blocks_obj()
        self.assertLessEqual(ba.end + 3 / 1440, bb.start + 1e-6)
        self.assertGreaterEqual((ba.end - ba.t4) * 1440, 20 - 0.1)
        self.assertGreaterEqual((bb.t1 - bb.start) * 1440, 20 - 0.1)
        self.assertTrue(any("Baseline trimmed" in c for c in ba.cautions + bb.cautions))

    def test_priority_then_prefer(self):
        a = transit("Alpha b", self.ra, 35.0, self.jd(22, 30), 2.0, 3.0)                    # 21:30–23:30
        b = transit("Beta b", self.ra + 10, 35.0, self.jd(0, 15), 1.5, 3.7, priority="High")  # 23:30–01:00
        plan = planner.build_night(self.d, self.site, OPTICS, [a, b], {})
        self.assertEqual([x["name"] for x in plan.blocks], ["Beta b"])
        miss = plan.didnt_fit[0]
        self.assertEqual(miss["name"], "Alpha b")
        self.assertIn("collides with Beta b", miss["reason"])
        self.assertIn("next chance", miss["reason"])
        self.assertTrue(miss["next_chance"])
        # "Plan this instead"
        plan2 = planner.build_night(self.d, self.site, OPTICS, [a, b], {}, prefer={"Alpha b"})
        self.assertEqual([x["name"] for x in plan2.blocks], ["Alpha b"])
        self.assertIn("collides with Alpha b", plan2.didnt_fit[0]["reason"])

    def test_rarer_next_chance_wins(self):
        often = transit("Often b", self.ra, 35.0, self.jd(22, 30), 2.0, 3.0, depth=0.03)       # back in 3 nights
        rare = transit("Rare b", self.ra + 10, 35.0, self.jd(0, 15), 1.5, 41.0, depth=0.005)   # 41 nights
        plan = planner.build_night(self.d, self.site, OPTICS, [often, rare], {})
        self.assertEqual([x["name"] for x in plan.blocks], ["Rare b"])
        miss = plan.didnt_fit[0]
        self.assertEqual(miss["name"], "Often b")
        nd = date.fromisoformat(miss["next_chance"])
        self.assertEqual((nd - self.d).days, 3)
        self.assertIn(f"next chance {nd.day} {nd.strftime('%b')}", miss["reason"])

    def test_better_snr_wins_when_equal(self):
        deep = transit("Deep b", self.ra, 35.0, self.jd(22, 30), 2.0, 3.0, depth=0.03)
        shallow = transit("Shallow b", self.ra + 10, 35.0, self.jd(0, 15), 1.5, 3.0, depth=0.004)
        plan = planner.build_night(self.d, self.site, OPTICS, [deep, shallow], {})
        self.assertEqual([x["name"] for x in plan.blocks], ["Deep b"])


if __name__ == "__main__":
    unittest.main()
