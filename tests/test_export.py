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

    def test_usual_camera_angle(self):
        from models import Optics
        op = Optics.from_dict(OPTICS.to_dict())
        op.camera_angle_deg = 30
        lines = export.asiair_csv([oct20_night(), oct22_night()], BY_ID, SITE, optics=op).split("\n")
        for ln in lines[1:]:
            self.assertEqual(ln.split(", ")[3], "30.00")
        self.assertEqual(export.asiair_angle(op), 30.0)
        for value, want in ((None, 0.0), (210.004, 210.0), (360, 0.0), (359.999, 0.0), ("x", 0.0)):
            op.camera_angle_deg = value
            self.assertAlmostEqual(export.asiair_angle(op), want, places=2)
        self.assertEqual(export.asiair_angle(None), 0.0)

    def test_south_names_and_pane_names(self):
        t = Target(id="s", name="Foo, “Bär” <&>", ra=83.8221, dec=-5.3911)
        n = NightPlan(date="2026-10-20", blocks=[Block(target_id="s", name=t.name, start=2461334.6,
                                                       end=2461334.7).to_dict()])
        line = export.asiair_csv([n], {"s": t}, SITE).split("\n")[1]
        self.assertTrue(line.startswith("Foo Bar <&>, 05hr 35' 17\", -05º 23' 28\", "))
        self.assertEqual(export.ascii_name("Tromsø Æbelø Straße Łódź Þór"), "Tromso AEbelo Strasse Lodz Thor")
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
        self.assertIn("SHOBS-SCHED · all times Local time (UTC−4) <span class=\"zone\">(America/New_York)</span>", page)
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
                  "SHOBS-SCHED · all times Local time (UTC−4) (America/New_York)", "Dark 20:08–06:16 (astronomical)"):
            self.assertIn(s, text)

    def test_no_dark_and_empty(self):
        n = NightPlan(date="2026-06-21")
        page = export.plan_card_html([n], Site(lat=69.65, lon=18.96, tz="Europe/Oslo"), {})
        self.assertIn("No dark time tonight", page)
        self.assertIn("No blocks planned", page)
        self.assertIn("all times Local time (UTC+2)", page)
        self.assertIn("Times: Local time (UTC+2)", page)
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
        self.assertIn("all times UTC ⚠ unknown time zone (Mars/Olympus)",
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


class TestNightFiles(unittest.TestCase):
    """Export page item 7: one night per file for ASIAIR and NINA, folder fallback, NINA's format."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched_files_")
        self.downloads = os.path.join(self.tmp, "Downloads")
        os.makedirs(self.downloads)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_names(self):
        self.assertEqual(export.night_file_name("asiair", "2026-10-20"), "SHOBS-SCHED_ASIAIR_2026-10-20.csv")
        self.assertEqual(export.night_file_name("nina", date(2026, 10, 31)), "SHOBS-SCHED_NINA_2026-10-31.csv")

    def test_asiair_file_is_one_night_and_unchanged_format(self):
        text = export.night_file_text("asiair", oct22_night(), BY_ID, SITE, optics=OPTICS)
        self.assertEqual(text, export.asiair_csv([oct22_night()], BY_ID, SITE, optics=OPTICS))
        self.assertFalse(text.endswith("\n"), "byte-identical to v1.0.0 Save CSV: no trailing newline")
        self.assertNotIn("TOI-2046 b", text, "only that night's targets")
        self.assertEqual([ln.split(", ")[0] for ln in text.splitlines()[1:]], ["HD 219134", "K2-29 b"])
        path, note = export.write_night_file(text, export.night_file_name("asiair", "2026-10-22"), self.downloads,
                                             self.downloads)
        self.assertEqual(note, "")
        self.assertEqual(path, os.path.join(self.downloads, "SHOBS-SCHED_ASIAIR_2026-10-22.csv"))
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "no byte-order mark")
        self.assertEqual(data.decode("utf-8"), text)

    def test_chosen_folder_used(self):
        cloud = os.path.join(self.tmp, "iCloud Drive", "ASIAIR")
        os.makedirs(cloud)
        path, note = export.write_night_file("x\n", "a.csv", cloud, self.downloads)
        self.assertEqual((path, note), (os.path.join(cloud, "a.csv"), ""))
        self.assertEqual(os.listdir(cloud), ["a.csv"], "the write probe leaves nothing behind")

    def test_missing_folder_falls_back(self):
        gone = os.path.join(self.tmp, "gone")
        path, note = export.write_night_file("x\n", "a.csv", gone, self.downloads)
        self.assertEqual(path, os.path.join(self.downloads, "a.csv"))
        self.assertTrue(note.startswith("⚠") and "is missing" in note and self.downloads in note, note)
        path, note = export.write_night_file("x\n", "b.csv", "", self.downloads)
        self.assertEqual((path, note), (os.path.join(self.downloads, "b.csv"), ""), "no folder chosen: Downloads")

    def test_unwritable_folder_falls_back(self):
        locked = os.path.join(self.tmp, "locked")
        os.makedirs(locked)
        real = export.folder_writable
        export.folder_writable = lambda f: f != locked and real(f)
        try:
            path, note = export.write_night_file("x\n", "a.csv", locked, self.downloads)
        finally:
            export.folder_writable = real
        self.assertEqual(path, os.path.join(self.downloads, "a.csv"))
        self.assertIn("cannot be written", note)
        # the probe passes but the write itself fails: still the fallback
        import builtins
        real_open = builtins.open

        def bad_open(p, *a, **k):
            if str(p).startswith(locked + os.sep) and "probe" not in str(p):
                raise PermissionError(13, "Permission denied")
            return real_open(p, *a, **k)
        builtins.open = bad_open
        try:
            path, note = export.write_night_file("x\n", "c.csv", locked, self.downloads)
        finally:
            builtins.open = real_open
        self.assertEqual(path, os.path.join(self.downloads, "c.csv"))
        self.assertIn("cannot be written", note)
        # both fail: the error reaches the caller
        with self.assertRaises(OSError):
            export.write_night_file("x\n", "d.csv", os.path.join(self.tmp, "no1"), os.path.join(self.tmp, "no2"))

    @staticmethod
    def nina_dms(text: str) -> float:
        """AstroUtil.DMSToDegrees as NINA writes it: the numbers by regex, '-' anywhere = negative."""
        nums = [float(x) for x in re.findall(r"[0-9.]+", text)]
        v = nums[0] + (nums[1] / 60 if len(nums) > 1 else 0) + (nums[2] / 3600 if len(nums) > 2 else 0)
        return -v if "-" in text else v

    def test_nina_format(self):
        op = Optics.from_dict(OPTICS.to_dict())
        op.camera_angle_deg = 210.5
        south = Target(id="s", name="Foo, “Bär”", ra=83.8221, dec=-5.3911)
        n = oct20_night()
        n.blocks.append(Block(target_id="s", name=south.name, start=at(date(2026, 10, 20), 3, 0),
                              end=at(date(2026, 10, 20), 4, 0)).to_dict())
        by_id = dict(BY_ID, s=south)
        text = export.night_file_text("nina", n, by_id, SITE, optics=op)
        self.assertEqual(text, export.nina_csv([n], by_id, SITE, optics=op))
        self.assertTrue(text.endswith("\r\n"))
        rows = list(csv.reader(io.StringIO(text, newline="")))
        # CsvHelper matches NINA's header names lower-cased and trimmed
        self.assertEqual([h.lower().strip() for h in rows[0][:4]], ["pane", "ra", "dec", "position angle (east)"])
        self.assertEqual([r[0] for r in rows[1:]], ["TOI-2046 b", "HD 219134", "Foo, “Bär”"])
        for r in rows[1:]:
            self.assertEqual(len(r), 9)
            self.assertEqual(r[3], "210.50")
        hd = rows[2]
        self.assertAlmostEqual(self.nina_dms(hd[1]) * 15, TARGETS["HD 219134"].ra, delta=0.01)
        self.assertAlmostEqual(self.nina_dms(hd[2]), TARGETS["HD 219134"].dec, delta=0.001)
        self.assertAlmostEqual(self.nina_dms(rows[3][2]), -5.3911, delta=0.001)
        self.assertIn('"Foo, “Bär”"', text, "a name with a comma is quoted")
        self.assertNotIn(", ", text.splitlines()[1].split('"')[0], "no space after the separators")
        self.assertEqual(export.nina_name("A\tB\n"), "A B")


class TestLabelsAndPlots(unittest.TestCase):
    """Item 6 labels on the card; the optional night plot."""

    def test_dst_night_new_york(self):
        n = NightPlan(date="2026-10-31", dusk=sky.local_jd(date(2026, 10, 31), 19, 50, TZ),
                      dawn=sky.local_jd(date(2026, 11, 1), 5, 30, TZ))
        self.assertEqual(export.night_clock_label(n, SITE), "Local time (UTC−4 → UTC−5)")
        page = export.plan_card_html([n], SITE, {})
        self.assertIn("Times: Local time (UTC−4 → UTC−5)", page)
        self.assertIn("all times Local time (UTC−4 → UTC−5)", page)
        self.assertNotIn("site clock", page)
        text = export.plan_card_text([n], SITE, {})
        self.assertIn("Times: Local time (UTC−4 → UTC−5)", text)
        self.assertIn("(America/New_York)", text, "the zone name stays in small text")
        # a run across the change: the footer spans it, each night has its own
        nn = [NightPlan(date="2026-10-30"), n, NightPlan(date="2026-11-02")]
        self.assertEqual(export.nights_clock_label(nn, SITE), "Local time (UTC−4 → UTC−5)")
        self.assertEqual(export.night_clock_label(nn[0], SITE), "Local time (UTC−4)")
        self.assertEqual(export.night_clock_label(nn[2], SITE), "Local time (UTC−5)")

    def test_non_us_zones(self):
        berlin = Site(lat=52.5, lon=13.4, tz="Europe/Berlin")
        self.assertEqual(export.night_clock_label(NightPlan(date="2026-10-24"), berlin),
                         "Local time (UTC+2 → UTC+1)")
        self.assertEqual(export.night_clock_label(NightPlan(date="2026-11-10"), berlin), "Local time (UTC+1)")
        tokyo = Site(lat=35.68, lon=139.69, tz="Asia/Tokyo")
        self.assertIn("Times: Local time (UTC+9)", export.plan_card_html([NightPlan(date="2026-10-24")], tokyo, {}))
        india = Site(lat=19.0, lon=72.8, tz="Asia/Kolkata")
        self.assertEqual(export.night_clock_label(NightPlan(date="2026-10-24"), india), "Local time (UTC+5:30)")
        bad = Site(lat=35.0, lon=-80.0, tz="Mars/Olympus")
        self.assertEqual(export.night_clock_label(NightPlan(date="2026-10-24"), bad), "UTC")

    def test_card_sections_match_card(self):
        sec = export.card_sections(oct20_night(), SITE, {}, None, BY_ID)
        self.assertEqual(sec["iso"], "2026-10-20")
        self.assertEqual(sec["clock"], "Local time (UTC−4)")
        self.assertEqual([r["target"] for r in sec["rows"]], ["TOI-2046 b", "HD 219134"])
        self.assertEqual([h for h, _ in sec["lists"]], ["Flats", "Darks", "Didn't fit, and why", "Free time"])

    def test_plots_embedded(self):
        import base64
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
               b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x81\xb5\x00\x00\x00\x00IEND"
               b"\xaeB`\x82")
        nights = [oct20_night(), oct22_night()]
        plain = export.plan_card_html(nights, SITE, {}, None, BY_ID)
        self.assertEqual(export.plan_card_html(nights, SITE, {}, None, BY_ID, plots=None), plain)
        self.assertEqual(export.plan_card_html(nights, SITE, {}, None, BY_ID, plots={}), plain)
        page = export.plan_card_html(nights, SITE, {}, None, BY_ID, plots={"2026-10-20": png})
        self.assertEqual(page.count("<img "), 1, "only the night that has a plot")
        b64 = base64.b64encode(png).decode("ascii")
        self.assertIn(f'<figure class="plot"><img src="data:image/png;base64,{b64}"', page)
        # under the 20th's heading and facts, before its table
        i20, iimg = page.index("<h2>Tue 20 Oct 2026</h2>"), page.index("<figure")
        self.assertLess(i20, iimg)
        self.assertLess(iimg, page.index("<table", i20))
        self.assertLess(iimg, page.index("<h2>Thu 22 Oct 2026</h2>"))
        self.assertIn(".plot { margin: 4pt 0; break-inside: avoid; page-break-inside: avoid; }", page)
        self.assertIsNone(re.search(r"<(script|link)\b", page))
        self.assertNotIn("http", page, "still self-contained")

    def test_plot_page_then_table_page(self):
        """With a plot: page 1 = heading, facts, plot; page 2 = the whole table and the lists (kept together).
        A night without a plot is laid out as before."""
        nights = [oct20_night(), oct22_night()]
        page = export.plan_card_html(nights, SITE, {}, None, BY_ID, plots={"2026-10-20": b"\x89PNG fake"})
        s20 = page[page.index('<section class="night with-plot">'):page.index("</section>") + 10]
        self.assertTrue(s20.index('<div class="plotpage">') < s20.index("<h2>") < s20.index("<figure")
                        < s20.index('</div><div class="tablepage">') < s20.index("<table")
                        < s20.index("<h3>Flats</h3>"))
        self.assertIn('<p class="cont">Tue 20 Oct 2026 · Times: Local time (UTC−4)</p>', s20)
        self.assertTrue(s20.endswith("</div></div></section>"))
        self.assertEqual(page.count('class="night with-plot"'), 1)
        self.assertEqual(page.count('<section class="night">'), 1, "the 22nd has no plot: as before")
        for rule in (".plotpage { break-after: page;", ".tablepage { break-inside: avoid;",
                     ".night.with-plot:not(:first-of-type) { break-before: page;", "tr, .list { break-inside: avoid;"):
            self.assertIn(rule, page)
        plain = export.plan_card_html(nights, SITE, {}, None, BY_ID)
        self.assertNotIn("plotpage", plain.split("</style>")[1])
        self.assertNotIn("tablepage", plain.split("</style>")[1])


if __name__ == "__main__":
    unittest.main()
