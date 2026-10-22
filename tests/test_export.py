"""export.py: nightly table rows, ASIAIR CSV, plan card HTML/text. NightPlans are made by hand in the shape of the
golden plan's Tue 20 Oct night (planner.py is not used)."""
import csv
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_TMP = tempfile.mkdtemp(prefix="sched_export_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "no_shobs_p.json")
sys.path.insert(0, ROOT)

import export  # noqa: E402
import sky  # noqa: E402
from models import Block, NightPlan, Optics, Site, Target  # noqa: E402

with open(os.path.join(HERE, "fixtures", "golden_oct19_24.json"), encoding="utf-8") as _fh:
    GOLD = json.load(_fh)
SITE = Site.from_dict(GOLD["site"])
OPTICS = Optics.from_dict(GOLD["optics"])
TARGETS = {t["name"]: Target.from_dict(dict(t, id=t["name"].replace(" ", "_"))) for t in GOLD["targets"]}
BY_ID = {t.id: t for t in TARGETS.values()}
TZ = SITE.tz


def at(d, hh, mm):
    """JD of a site-clock time on the night that starts on date d (after-midnight times are the next day)."""
    day = d if hh >= 12 else date.fromordinal(d.toordinal() + 1)
    return sky.local_jd(day, hh, mm, TZ)


def oct20_night() -> NightPlan:
    d = date(2026, 10, 20)
    toi, hd = TARGETS["TOI-2046 b"], TARGETS["HD 219134"]
    b1 = Block(target_id=toi.id, name=toi.name, mode="T", kind="transit", start=at(d, 20, 15), end=at(d, 0, 30),
               fixed=True, t1=at(d, 21, 32), tc=at(d, 22, 44), t4=at(d, 23, 56), tc_sigma_min=1.0,
               meridian=at(d, 0, 35), alt_start=52.4, alt_end=71.6, alt_min=52.4, alt_max=71.6,
               airmass_start=1.26, airmass_best=1.054, airmass_end=1.054, exposure_s=60, exposure_is_estimate=False,
               frames=240, frames_in_transit=135,
               notes=["Ingress 21:32, mid 22:44, egress 23:56 (±1 min)"])
    b2 = Block(target_id=hd.id, name=hd.name, mode="V", kind="campaign", start=at(d, 0, 40), end=at(d, 2, 10),
               meridian=at(d, 22, 44), alt_start=66.0, alt_end=48.0, alt_min=48.0, alt_max=66.0,
               airmass_best=1.095, exposure_s=0.75, exposure_is_estimate=False, frames=164,
               notes=["Past the meridian since 22:44; no flip needed"],
               cautions=["⚠ dew expected from 04:05"])
    return NightPlan(date="2026-10-20", dusk=at(d, 20, 11), dawn=at(d, 6, 14), moon_illum=0.74,
                     moonset=at(d, 2, 47), blocks=[b2.to_dict(), b1.to_dict()],       # out of order on purpose
                     didnt_fit=[{"target_id": "x", "name": "WASP-12 b", "reason": "collides with TOI-2046 b",
                                 "next_chance": "2026-10-27"}],
                     free=[(at(d, 2, 10), at(d, 3, 0))],
                     flats=["Flats: one set at the night's camera angle; a meridian flip's 180° turn needs no new flats"],
                     darks=["Darks to shoot: 0.75 s × 20"])


def oct22_night() -> NightPlan:
    d = date(2026, 10, 22)
    hd, k2 = TARGETS["HD 219134"], TARGETS["K2-29 b"]
    blocks = [Block(target_id=hd.id, name=hd.name, mode="V", kind="campaign", start=at(d, 22, 25), end=at(d, 0, 0),
                    exposure_s=0.75, frames=150),
              Block(target_id=k2.id, name=k2.name, mode="T", kind="transit", start=at(d, 0, 5), end=at(d, 3, 36),
                    fixed=True, exposure_s=120, frames=170, frames_in_transit=98),
              Block(target_id=hd.id, name=hd.name, mode="V", kind="campaign", start=at(d, 3, 40), end=at(d, 4, 20),
                    exposure_s=0.75, frames=60)]
    return NightPlan(date="2026-10-22", dusk=at(d, 20, 8), dawn=at(d, 6, 16), moon_illum=0.89,
                     moonset=at(d, 4, 51), blocks=[b.to_dict() for b in blocks])


class TestTableRows(unittest.TestCase):
    def test_oct20_rows(self):
        rows = export.table_rows(oct20_night(), SITE, BY_ID)
        self.assertEqual([r["target"] for r in rows], ["TOI-2046 b", "HD 219134"])
        toi, hd = rows
        self.assertEqual(toi["time"], "20:15–00:30")
        self.assertEqual(toi["mode"], "Transits")
        self.assertEqual(toi["mode_code"], "T")
        self.assertEqual(toi["alt"], "52–72°")
        self.assertEqual(toi["alt_x"], "72° (X 1.05)")
        self.assertEqual(toi["meridian"], "00:35")
        self.assertEqual(toi["exposure"], "60 s")
        self.assertEqual(toi["frames"], "~240 (~135 in transit)")
        self.assertEqual(toi["notes"], "Ingress 21:32, mid 22:44, egress 23:56 (±1 min)")
        self.assertEqual(toi["radec"], f"{sky.fmt_ra(16.185, 0)} {sky.fmt_dec(74.331389)}")
        self.assertEqual(toi["radec"], "01:04:44 +74:19:53")
        self.assertEqual(hd["time"], "00:40–02:10")
        self.assertEqual(hd["mode"], "Variables")
        self.assertEqual(hd["exposure"], "0.75 s")
        self.assertEqual(hd["frames"], "~164")
        self.assertEqual(hd["meridian"], "22:44")
        self.assertEqual(hd["notes"], "Past the meridian since 22:44; no flip needed; ⚠ dew expected from 04:05")
        self.assertEqual(hd["cautions"], ["⚠ dew expected from 04:05"])
        for r in rows:
            self.assertEqual(set(export.TABLE_KEYS) - set(r), set())

    def test_no_targets_and_missing_values(self):
        n = NightPlan(date="2026-10-20", blocks=[Block(name="X", start=at(date(2026, 10, 20), 21, 0),
                                                       end=at(date(2026, 10, 20), 22, 0)).to_dict()])
        r = export.table_rows(n, SITE)[0]
        self.assertEqual((r["radec"], r["alt"], r["alt_x"], r["meridian"], r["exposure"], r["frames"]),
                         ("—", "—", "—", "—", "—", "—"))

    def test_formats(self):
        self.assertEqual(export.fmt_exposure(60.0), "60 s")
        self.assertEqual(export.fmt_exposure(0.75), "0.75 s")
        self.assertEqual(export.fmt_frames(240, 135), "~240 (~135 in transit)")


class TestAsiairCsv(unittest.TestCase):
    def test_header_and_rows(self):
        text = export.asiair_csv([oct20_night(), oct22_night()], BY_ID, SITE, optics=OPTICS)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), "
                                   "Pane height (arcmins), Overlap, Row, Column")
        self.assertFalse(text.endswith("\n"))
        # time order across nights, each target once: TOI (20th), HD (21st 00:40), K2-29 b (23rd 00:05)
        self.assertEqual([ln.split(", ")[0] for ln in lines[1:]], ["TOI-2046 b", "HD 219134", "K2-29 b"])
        hd = lines[2].split(", ")
        self.assertEqual(hd[1], "23hr 13' 17\"")
        self.assertEqual(hd[2], "57º 10' 06\"")
        self.assertEqual(hd[3:], ["0.00", hd[4], hd[5], "0%", "-", "-"])
        # 14" f/8 (2900 mm) + ASI2600: 0.267"/px × 6248 × 4176 px ≈ 27.8' × 18.6'
        self.assertAlmostEqual(float(hd[4]), 27.8, delta=0.2)
        self.assertAlmostEqual(float(hd[5]), 18.6, delta=0.2)
        for ln in lines[1:]:
            self.assertEqual(len(ln.split(", ")), 9)
            self.assertRegex(ln, r"^[^,]+, \d\dhr \d\d' \d\d\", -?\d\dº \d\d' \d\d\", ")

    def test_south_names_and_pane_names(self):
        t = Target(id="s", name="Foo, “Bär” <&>", ra=83.8221, dec=-5.3911)
        n = NightPlan(date="2026-10-20", blocks=[Block(target_id="s", name=t.name, start=2461334.6,
                                                       end=2461334.7).to_dict()])
        line = export.asiair_csv([n], {"s": t}, SITE).split("\n")[1]
        self.assertTrue(line.startswith("Foo Bar <&>, 05hr 35' 17\", -05º 23' 28\", "))
        self.assertTrue(all(ord(c) < 128 for c in line.split(",")[0]))
        line2 = export.asiair_csv([n], {"s": t}, SITE, pane_names=False).split("\n")[1]
        self.assertTrue(line2.startswith("Pane 1, "))

    def test_rounding_carry(self):
        self.assertEqual(export.ra_telescopius(359.99999), "00hr 00' 00\"")
        self.assertEqual(export.dec_telescopius(-0.0000001), "00º 00' 00\"")
        self.assertEqual(export.dec_telescopius(45.999999), "46º 00' 00\"")

    def test_skips_targets_without_coordinates(self):
        n = NightPlan(date="2026-10-20", blocks=[Block(target_id="nope", name="Nowhere", start=1, end=2).to_dict()])
        rows, skipped = export.asiair_targets([n], {}, SITE)
        self.assertEqual((rows, skipped), ([], ["Nowhere"]))
        self.assertEqual(export.asiair_csv([n], {}, SITE), export.ASIAIR_HEADER)

    def test_observing_list_style(self):
        text = export.asiair_csv([oct20_night()], BY_ID, SITE, style="observing_list")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], export.OBSERVING_LIST_HEADER)
        self.assertEqual(rows[2][0], "HD 219134")
        self.assertEqual(rows[2][5], "23h 13' 17\"")
        self.assertEqual(rows[2][6], "57º 10' 06\"")
        self.assertEqual(rows[2][7], "5.57")
        self.assertIn('"23h 13\' 17"""', text)            # quoted as in the Telescopius file


class TestPlanCard(unittest.TestCase):
    def setUp(self):
        self.n20, self.n22 = oct20_night(), oct22_night()
        evil = Target(id="evil", name="Evil <script>alert(1)</script> & Co", ra=10, dec=20)
        self.by_id = dict(BY_ID, evil=evil)
        d = date(2026, 10, 22)
        b = Block(target_id="evil", name=evil.name, mode="D", kind="campaign", start=at(d, 4, 25),
                  end=at(d, 5, 30), notes=["a < b & c"])
        self.n22.blocks.append(b.to_dict())
        self.weather = {"2026-10-20": {"source": "Open-Meteo", "ok": True, "note": "",
                                       "summary": "Clear until 02:00, then 60–80 % cloud; dew from 04:05",
                                       "dew_onset": at(date(2026, 10, 20), 4, 5),
                                       "hours": [{"jd": at(date(2026, 10, 20), 20, 0), "temp_c": 12.0,
                                                  "dew_c": 5.0, "wind_kmh": 10.0, "cloud": 0}]}}

    def test_darks_heading_reads_well(self):
        self.assertEqual(export._items("Darks", ["Darks to shoot: 40 s", "Darks: all on hand (60 s, 120 s)"]),
                         ["To shoot: 40 s", "All on hand (60 s, 120 s)"])
        self.assertEqual(export._items("Flats", ["Flats: one set"]), ["One set"])

    def test_html(self):
        page = export.plan_card_html([self.n20, self.n22], SITE, {"observer_code": "JABA"}, self.weather,
                                     self.by_id)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        for name in ("TOI-2046 b", "HD 219134", "K2-29 b"):
            self.assertIn(name, page)
        self.assertIn("Evil &lt;script&gt;alert(1)&lt;/script&gt; &amp; Co", page)
        self.assertNotIn("<script", page)
        self.assertIn("a &lt; b &amp; c", page)
        for t in ("20:15–00:30", "00:40–02:10", "22:25–00:00", "00:05–03:36", "03:40–04:20"):
            self.assertIn(t, page)
        self.assertEqual(page.count("<tr>"), 2 + 2 + 4)       # header row per night + blocks
        self.assertIn("Dark 20:11–06:14 (astronomical)", page)
        self.assertIn("Moon 74 %, sets 02:47", page)
        self.assertIn("Clear until 02:00, then 60–80 % cloud", page)
        self.assertIn("Dew from 04:05", page)
        self.assertIn("High 12 °C, low 12 °C, wind up to 10 km/h", page)
        self.assertIn("WASP-12 b: collides with TOI-2046 b (next chance Tue 27 Oct)", page)
        self.assertIn("Free 02:10–03:00", page)
        self.assertIn("<h3>Darks</h3><ul><li>To shoot: 0.75 s × 20</li>", page)
        self.assertIn("<li>One set at the night", page)              # "Flats:" prefix not repeated
        self.assertIn("a meridian flip&#x27;s 180° turn", page)
        self.assertIn("SHOBS-SCHED · site clock (America/New_York)", page)
        self.assertIn("observer JABA", page)
        self.assertIn("Tue 20 Oct 2026", page)
        # self-contained: no external scripts, stylesheets or images
        self.assertIsNone(re.search(r"<(script|link|img)\b", page))
        self.assertNotIn("http", page)
        self.assertIn("color: #000", page)

    def test_text(self):
        text = export.plan_card_text([self.n20, self.n22], SITE, {}, self.weather, self.by_id)
        for s in ("TOI-2046 b", "HD 219134", "K2-29 b", "Evil <script>alert(1)</script> & Co",
                  "20:15–00:30", "~240 (~135 in transit)", "Free 02:10–03:00", "Didn't fit, and why:",
                  "SHOBS-SCHED · site clock (America/New_York)", "Dark 20:08–06:16 (astronomical)"):
            self.assertIn(s, text)

    def test_no_dark_and_empty(self):
        n = NightPlan(date="2026-06-21")
        page = export.plan_card_html([n], Site(lat=69.65, lon=18.96, tz="Europe/Oslo"), {})
        self.assertIn("No dark time tonight", page)
        self.assertIn("No blocks planned", page)
        self.assertIn("site clock (Europe/Oslo)", page)
        self.assertIn("No nights selected", export.plan_card_text([], SITE, {}))

    def test_offline_weather_line(self):
        w = {"2026-10-20": {"source": "Open-Meteo", "ok": False, "hours": [], "dew_onset": None,
                            "note": "Open-Meteo did not answer (timed out)",
                            "summary": "No forecast (Open-Meteo did not answer (timed out))"}}
        text = export.plan_card_text([self.n20], SITE, {}, w, self.by_id)
        self.assertIn("No forecast (Open-Meteo did not answer (timed out))", text)
        self.assertEqual(text.count("did not answer"), 1)



class TestReviewFixes(unittest.TestCase):
    """Fixes from the independent review of 22 Oct."""

    def weather(self, d):
        return {d: {"source": "Open-Meteo", "ok": True, "note": "", "summary": "Clear all night", "dew_onset": None,
                    "hours": [{"jd": 2461336.5, "temp_c": 12.0, "wind_kmh": 16.1},
                              {"jd": 2461336.6, "temp_c": 5.0, "wind_kmh": 8.0}]}}

    def test_temps_and_wind_units(self):
        n = NightPlan(date="2026-10-20")
        metric = Site.from_dict(dict(GOLD["site"], temp_unit="F", length_unit="m"))
        text = export.plan_card_text([n], metric, {}, self.weather("2026-10-20"))
        self.assertIn("High 54 °F, low 41 °F, wind up to 16 km/h", text)
        imperial = Site.from_dict(dict(GOLD["site"], temp_unit="C", length_unit="ft"))
        text = export.plan_card_text([n], imperial, {}, self.weather("2026-10-20"))
        self.assertIn("High 12 °C, low 5 °C, wind up to 10 mph", text)
        self.assertNotIn("→", text)

    def test_bad_block_times(self):
        n = NightPlan(date="2026-10-20", dusk=0, dawn=None, moon_illum=0.5, moonrise=float("nan"),
                      blocks=[Block(name="Zero", start=0, end=0, meridian=12.0).to_dict(),
                              Block(name="Small", start=1.5, end=2.5).to_dict(),
                              Block(name="Good", start=at(date(2026, 10, 20), 21, 0),
                                    end=at(date(2026, 10, 20), 22, 0)).to_dict()],
                      free=[(0, 1)])
        rows = export.table_rows(n, SITE)
        self.assertEqual([r["target"] for r in rows], ["Good", "Zero", "Small"])
        self.assertEqual(rows[1]["time"], "—–—")
        self.assertEqual(rows[1]["meridian"], "—")
        page = export.plan_card_html([n], SITE, {})
        self.assertIn("Zero", page)
        self.assertIn("No dark time tonight", page)
        self.assertIn("Free —–—", page)
        export.plan_card_text([n], SITE, {})
        export.asiair_csv([n], {}, SITE)

    def test_footer_unknown_zone(self):
        bad = Site(lat=35.0, lon=-80.0, tz="Mars/Olympus")
        self.assertIn("site clock (Mars/Olympus) ⚠ unknown time zone, times in UTC",
                      export.plan_card_text([], bad, {}))
        self.assertNotIn("unknown time zone", export.plan_card_text([], SITE, {}))

    def test_southern_and_tokyo_sites(self):
        # Run under TZ=Asia/Tokyo too: the rows must follow the SITE's clock, never the computer's.
        for site, d, ra, dec in ((Site(name="Siding Spring", lat=-31.2733, lon=149.0617, tz="Australia/Sydney"),
                                  date(2026, 10, 22), 83.8221, -5.3911),
                                 (Site(name="Tokyo", lat=35.68, lon=139.69, tz="Asia/Tokyo"),
                                  date(2026, 10, 22), 10.6847, 41.2690)):
            t = Target(id="t", name="T", ra=ra, dec=dec)
            b = Block(target_id="t", name="T", start=sky.local_jd(d, 21, 0, site.tz),
                      end=sky.local_jd(date(2026, 10, 23), 0, 30, site.tz),
                      meridian=sky.local_jd(date(2026, 10, 23), 0, 5, site.tz))
            n = NightPlan(date=d.isoformat(), dusk=sky.local_jd(d, 20, 5, site.tz),
                          dawn=sky.local_jd(date(2026, 10, 23), 4, 40, site.tz), blocks=[b.to_dict()])
            r = export.table_rows(n, site, {"t": t})[0]
            self.assertEqual(r["time"], "21:00–00:30", site.tz)
            self.assertEqual(r["meridian"], "00:05", site.tz)
            self.assertIn("Dark 20:05–04:40", export.plan_card_text([n], site, {}, None, {"t": t}))
        self.assertTrue(r["radec"].startswith("00:42:44 +41:16"))
        line = export.asiair_csv([n], {"t": t}, site).split("\n")[1]
        self.assertIn("00hr 42' 44\", 41º 16' 08\"", line)

    def test_csv_utf8_no_bom(self):
        text = export.asiair_csv([oct20_night()], BY_ID, SITE)
        data = text.encode("utf-8")
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("º".encode("utf-8"), data)


if __name__ == "__main__":
    unittest.main()
