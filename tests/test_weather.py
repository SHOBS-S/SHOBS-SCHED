"""weather.py: Open-Meteo parsing (recorded replies), night slicing, dew onset, daylight-saving night, cache and
offline fallback. No network: weather.http_get_json is replaced in every test.

Fixtures (recorded live 2026-10-22 14:04 UTC with past_days=1, forecast_days=8):
  weather_openmeteo_shobs_20261022.json        John's site 35.303, -81.9407
  weather_openmeteo_sidingspring_20261022.json Siding Spring -31.2733, 149.0617"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
_TMP = tempfile.mkdtemp(prefix="sched_weather_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "no_shobs_p.json")
sys.path.insert(0, ROOT)

import sky  # noqa: E402
import weather  # noqa: E402
from models import Site  # noqa: E402

JOHN = Site(name="SHOBS", lat=35.303, lon=-81.9407, tz="America/New_York")
SIDING = Site(name="Siding Spring", lat=-31.2733, lon=149.0617, tz="Australia/Sydney")
RECORDED = sky.jd_from_datetime(datetime(2026, 10, 22, 14, 4, tzinfo=timezone.utc))
OPTS = {"dew_spread_c": 2.5}


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


def synthetic_reply(t0: datetime, n_hours: int, f) -> dict:
    """An Open-Meteo-shaped reply: f(i, utc datetime) -> (temp, dew, cloud, wind, rh)."""
    h = {k: [] for k in ("time", "temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m",
                         "relative_humidity_2m")}
    for i in range(n_hours):
        t = t0 + timedelta(hours=i)
        temp, dew, cloud, wind, rh = f(i, t)
        h["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        h["temperature_2m"].append(temp)
        h["dew_point_2m"].append(dew)
        h["cloud_cover"].append(cloud)
        h["wind_speed_10m"].append(wind)
        h["relative_humidity_2m"].append(rh)
    return {"latitude": 0, "longitude": 0, "utc_offset_seconds": 0, "timezone": "GMT", "hourly": h}


class FakeNet:
    """Stands in for weather.http_get_json: returns a reply or raises, and counts calls."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls, self.urls = reply, error, 0, []

    def __call__(self, url, timeout=20):
        self.calls += 1
        self.urls.append(url)
        if self.error:
            raise weather.WeatherError(self.error)
        return self.reply


class Base(unittest.TestCase):
    def setUp(self):
        self._orig = weather.http_get_json
        os.environ["SHOBS_SCHED_HOME"] = _TMP          # another test module may have pointed it elsewhere
        os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "no_shobs_p.json")
        import settings
        cache = settings.cache_dir()
        for f in os.listdir(cache):
            if f.startswith("weather_"):
                os.remove(os.path.join(cache, f))

    def tearDown(self):
        weather.http_get_json = self._orig

    def fetch(self, site, start, n, net, now=RECORDED, opts=OPTS):
        weather.http_get_json = net
        return weather.OpenMeteo(now_jd=lambda: now).fetch(site, start, n, opts)


class TestParse(Base):
    def test_john_recorded_reply(self):
        net = FakeNet(load("weather_openmeteo_shobs_20261022.json"))
        res = self.fetch(JOHN, date(2026, 10, 22), 3, net)
        self.assertEqual(list(res), ["2026-10-22", "2026-10-23", "2026-10-24"])
        self.assertEqual(net.calls, 1)
        for iso, nw in res.items():
            d = date.fromisoformat(iso)
            sunset, sunrise = sky.night_window(d, JOHN, sky.SUN_RISE_ALT)
            hours = nw["hours"]
            self.assertTrue(nw["ok"])
            self.assertEqual(nw["source"], "Open-Meteo")
            self.assertGreaterEqual(len(hours), 11)
            self.assertLessEqual(len(hours), 14)
            self.assertGreaterEqual(hours[0]["jd"], sunset - 0.5 / 24 - 1e-9)
            self.assertLessEqual(hours[-1]["jd"], sunrise + 0.5 / 24 + 1e-9)
            self.assertTrue(all(b["jd"] > a["jd"] for a, b in zip(hours, hours[1:])))
            for h in hours:
                self.assertEqual(set(h), {"jd", "cloud", "temp_c", "dew_c", "spread_c", "rh", "wind_kmh"})
                self.assertAlmostEqual(h["spread_c"], h["temp_c"] - h["dew_c"], places=2)
            self.assertTrue(nw["summary"])
        # The recorded 22 Oct evening at John's site: the first hour kept is 19:00 EDT = 23:00 UTC
        first = res["2026-10-22"]["hours"][0]
        self.assertEqual(sky.to_utc(first["jd"]).strftime("%Y-%m-%dT%H:%M"), "2026-10-22T23:00")
        self.assertEqual(sky.hhmm(first["jd"], JOHN.tz), "19:00")
        self.assertEqual(res["2026-10-22"]["summary"], "Clear all night")

    def test_url(self):
        url = weather.OpenMeteo(now_jd=lambda: RECORDED).url(JOHN, date(2026, 10, 22), 7)
        for part in ("latitude=35.3030", "longitude=-81.9407", "timezone=GMT", "forecast_days=8",
                     "hourly=temperature_2m,dew_point_2m,relative_humidity_2m,cloud_cover,wind_speed_10m",
                     "temperature_unit=celsius", "wind_speed_unit=kmh"):
            self.assertIn(part, url)
        far = weather.OpenMeteo(now_jd=lambda: RECORDED).url(JOHN, date(2026, 11, 1), 7)
        self.assertIn("forecast_days=16", far)

    def test_southern_site_and_dew(self):
        net = FakeNet(load("weather_openmeteo_sidingspring_20261022.json"))
        res = self.fetch(SIDING, date(2026, 10, 22), 4, net)
        n23 = res["2026-10-23"]
        dusk, dawn = sky.night_window(date(2026, 10, 23), SIDING)
        self.assertIsNotNone(n23["dew_onset"])
        self.assertGreaterEqual(n23["dew_onset"], dusk)
        self.assertLessEqual(n23["dew_onset"], dawn)
        self.assertIn("dew from", n23["summary"])
        # local evening hours at Sydney (AEDT, UTC+11): the first hour is ~19:00 site clock
        self.assertIn(sky.hhmm(n23["hours"][0]["jd"], SIDING.tz), ("19:00", "20:00"))
        self.assertIn("Clear until 02:00", n23["summary"])

    def test_beyond_forecast_range(self):
        net = FakeNet(load("weather_openmeteo_shobs_20261022.json"))
        res = self.fetch(JOHN, date(2026, 10, 29), 3, net)
        self.assertFalse(res["2026-10-30"]["ok"])
        self.assertEqual(res["2026-10-30"]["hours"], [])
        self.assertIn("No forecast", res["2026-10-30"]["summary"])

    def test_error_reply(self):
        with self.assertRaises(weather.WeatherError):
            weather.parse_open_meteo({"error": True, "reason": "Latitude must be in range"})


class TestDew(Base):
    def test_onset_interpolated(self):
        # Spread falls 1 °C per hour from 8 °C at 22:00 UTC: crosses 2.5 °C at 03:30 UTC = 23:30 EDT.
        t0 = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)

        def f(i, t):
            hours_after_22 = (t - datetime(2026, 10, 22, 22, tzinfo=timezone.utc)).total_seconds() / 3600
            spread = max(0.0, 8.0 - max(0.0, hours_after_22))
            return 10.0, 10.0 - spread, 0, 5, 80
        reply = synthetic_reply(t0, 36, f)
        hours = weather.parse_open_meteo(reply)
        nw = weather.night_weather(hours, date(2026, 10, 22), JOHN, OPTS, source="test")
        self.assertEqual(sky.hhmm(nw["dew_onset"], JOHN.tz), "23:30")
        self.assertIn("dew from 23:30", nw["summary"])

    def test_wet_at_dusk_and_never(self):
        dusk, dawn = sky.night_window(date(2026, 10, 22), JOHN)
        wet = [{"jd": dusk - 0.5 / 24 + k / 24, "spread_c": 1.0} for k in range(14)]
        self.assertAlmostEqual(weather.dew_onset(wet, dusk, dawn, 2.5), dusk, places=6)
        dry = [{"jd": dusk - 0.5 / 24 + k / 24, "spread_c": 9.0} for k in range(14)]
        self.assertIsNone(weather.dew_onset(dry, dusk, dawn, 2.5))
        self.assertIsNone(weather.dew_onset(wet, None, None, 2.5))

    def test_threshold_from_options(self):
        net = FakeNet(load("weather_openmeteo_shobs_20261022.json"))
        dry = self.fetch(JOHN, date(2026, 10, 22), 1, net, opts={"dew_spread_c": 0.1})
        self.assertIsNone(dry["2026-10-22"]["dew_onset"])
        os.remove(weather.cache_path(JOHN))
        wet = self.fetch(JOHN, date(2026, 10, 22), 1, net, opts={"dew_spread_c": 30})
        self.assertIsNotNone(wet["2026-10-22"]["dew_onset"])


class TestDST(Base):
    def test_fall_back_night(self):
        # US clocks fall back at 02:00 EDT on 1 Nov 2026: the night that starts 31 Oct is 25 h noon to noon.
        j0, j1 = sky.night_span(date(2026, 10, 31), JOHN)
        self.assertAlmostEqual((j1 - j0) * 24, 25.0, places=6)
        t0 = datetime(2026, 10, 31, 0, tzinfo=timezone.utc)
        reply = synthetic_reply(t0, 72, lambda i, t: (10.0, 0.0, 0 if t.hour < 8 else 90, 5, 50))
        hours = weather.parse_open_meteo(reply)
        nw = weather.night_weather(hours, date(2026, 10, 31), JOHN, OPTS, source="test")
        sunset, sunrise = sky.night_window(date(2026, 10, 31), JOHN, sky.SUN_RISE_ALT)
        # one sample per real hour from sunset to sunrise (the repeated 01:00 hour included)
        expected = sum(1 for h in hours if sunset - 0.5 / 24 <= h["jd"] <= sunrise + 0.5 / 24)
        self.assertEqual(len(nw["hours"]), expected)
        self.assertAlmostEqual(len(nw["hours"]), (sunrise - sunset) * 24 + 1, delta=1.01)
        clock = [sky.hhmm(h["jd"], JOHN.tz) for h in nw["hours"]]
        self.assertEqual(clock.count("01:00"), 2)       # 01:00 EDT and 01:00 EST
        # cloud arrives at 08:00 UTC = 03:00 EST (after the change), not 04:00
        self.assertIn("Clear until 03:00", nw["summary"])


class TestCache(Base):
    def test_fresh_cache_skips_network(self):
        rep = load("weather_openmeteo_shobs_20261022.json")
        self.fetch(JOHN, date(2026, 10, 22), 3, FakeNet(rep))
        self.assertTrue(os.path.exists(weather.cache_path(JOHN)))
        self.assertTrue(os.path.basename(weather.cache_path(JOHN)).startswith("weather_35.303_-81.941"))
        net = FakeNet(error="should not be called")
        res = self.fetch(JOHN, date(2026, 10, 22), 2, net, now=RECORDED + 2 / 24)
        self.assertEqual(net.calls, 0)
        self.assertTrue(res["2026-10-22"]["ok"])
        self.assertAlmostEqual(res["2026-10-22"]["age_h"], 2.0, places=2)

    def test_stale_cache_refetches(self):
        rep = load("weather_openmeteo_shobs_20261022.json")
        self.fetch(JOHN, date(2026, 10, 22), 3, FakeNet(rep))
        net = FakeNet(rep)
        self.fetch(JOHN, date(2026, 10, 22), 3, net, now=RECORDED + 3.5 / 24)
        self.assertEqual(net.calls, 1)

    def test_offline_uses_cache_with_age(self):
        rep = load("weather_openmeteo_shobs_20261022.json")
        self.fetch(JOHN, date(2026, 10, 22), 3, FakeNet(rep))
        res = self.fetch(JOHN, date(2026, 10, 22), 3, FakeNet(error="timed out"), now=RECORDED + 5 / 24)
        n = res["2026-10-22"]
        self.assertTrue(n["ok"])
        self.assertGreater(len(n["hours"]), 0)
        self.assertIn("did not answer (timed out)", n["note"])
        self.assertIn("5 h ago", n["note"])

    def test_offline_no_cache(self):
        res = self.fetch(JOHN, date(2026, 10, 22), 2, FakeNet(error="no connection (Name or service not known)"))
        self.assertEqual(len(res), 2)
        n = res["2026-10-22"]
        self.assertFalse(n["ok"])
        self.assertEqual(n["hours"], [])
        self.assertIsNone(n["dew_onset"])
        self.assertTrue(n["summary"].startswith("No forecast (Open-Meteo did not answer (no connection"))

    def test_incomplete_site(self):
        res = self.fetch(Site(), date(2026, 10, 22), 1, FakeNet(error="x"))
        self.assertIn("not set", res["2026-10-22"]["summary"])


class TestFormat(unittest.TestCase):
    def test_temp(self):
        self.assertEqual(weather.fmt_temp(12.4, "C"), "12 °C")
        self.assertEqual(weather.fmt_temp(12.4, "F"), "54 °F")
        self.assertEqual(weather.fmt_temp(None, "C"), "—")

    def test_wind(self):
        self.assertEqual(weather.fmt_wind(16.1, "metric"), "16 km/h")
        self.assertEqual(weather.fmt_wind(16.1, "imperial"), "10 mph")
        self.assertEqual(weather.fmt_wind(16.1, "ft"), "10 mph")
        self.assertEqual(weather.fmt_wind(None, "metric"), "—")

    def test_sources(self):
        self.assertIs(weather.SOURCES["Open-Meteo"], weather.OpenMeteo)
        self.assertTrue(issubclass(weather.OpenMeteo, weather.WeatherSource))
        self.assertEqual(weather.OpenMeteo.name, "Open-Meteo")

    def test_summary_shapes(self):
        dusk, dawn = sky.night_window(date(2026, 10, 22), JOHN)
        mk = lambda clouds: [{"jd": dusk + k / 24, "cloud": c} for k, c in enumerate(clouds)]
        self.assertEqual(weather.summary_text(mk([0] * 10), dusk, dawn, None, JOHN.tz), "Clear all night")
        self.assertEqual(weather.summary_text(mk([95] * 10), dusk, dawn, None, JOHN.tz), "Overcast all night")
        s = weather.summary_text(mk([0, 0, 0, 0, 0, 0, 60, 70, 80, 80]), dusk, dawn, dusk + 9 / 24, JOHN.tz)
        self.assertRegex(s, r"^Clear until \d\d:\d\d, then 60–80 % cloud; dew from \d\d:\d\d$")
        # a lone odd hour does not split the night
        self.assertEqual(weather.summary_text(mk([0, 0, 0, 40, 0, 0]), dusk, dawn, None, JOHN.tz),
                         "Clear all night")


if __name__ == "__main__":
    unittest.main()
