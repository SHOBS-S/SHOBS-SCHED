"""SHOBS-SCHED self-test: checks this copy with the Python the shortcuts use. It changes no data: settings, targets
and the schedule are read from and written to a temporary folder only.

The log goes to selftest_log.txt next to this file and a copy to Downloads\\SHOBS-SCHED_selftest_log.txt.
Sections:
  1 environment (Python, numpy, Tkinter, time zones)
  2 sky and events against the Oct 19–24 2026 plan (dark times, meridians, transit times)
  3 planner: the five golden nights
  4 weather and export (recorded forecast; CSV; plan card)
  5 the window (all four pages, a build, the CSV) — needs a display
  6 live services (NASA Exoplanet Archive, SIMBAD, VizieR, AAVSO VSX, Open-Meteo): reported, never a failure
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="shobs_sched_selftest_")
os.environ["SHOBS_SCHED_HOME"] = TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(TMP, "no_shobs_p.json")
sys.path.insert(0, HERE)

OUT = io.StringIO()
FAILS: list[str] = []


def say(line: str = "") -> None:
    print(line)
    OUT.write(line + "\n")


def check(name: str, ok: bool, detail: str = "") -> None:
    say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def section(title: str, fn) -> None:
    say("")
    say(title)
    t0 = time.time()
    try:
        fn()
    except Exception:
        FAILS.append(title)
        say("  FAIL  (unexpected error)")
        for line in traceback.format_exc().splitlines():
            say("        " + line)
    say(f"  ({time.time() - t0:.1f} s)")


def golden():
    with open(os.path.join(HERE, "tests", "fixtures", "golden_oct19_24.json"), encoding="utf-8") as fh:
        return json.load(fh)


def minutes(hhmm_a: str, hhmm_b: str) -> float:
    a = int(hhmm_a[:2]) * 60 + int(hhmm_a[3:])
    b = int(hhmm_b[:2]) * 60 + int(hhmm_b[3:])
    d = (a - b) % 1440
    return min(d, 1440 - d)


def s1_environment():
    import numpy
    say(f"  Python {sys.version.split()[0]} ({sys.executable})")
    say(f"  numpy {numpy.__version__}")
    try:
        import matplotlib
        from matplotlib.backends import backend_agg  # noqa: F401  (the night plot and its PNG)
        ok, ver = True, matplotlib.__version__
    except Exception as exc:
        ok, ver = False, f"does not load ({exc}); run Create shortcuts.bat again"
    check(f"matplotlib {ver} (the night plot)", ok)
    import tkinter
    say(f"  Tk {tkinter.TkVersion}")
    from zoneinfo import ZoneInfo
    for z in ("America/New_York", "Australia/Sydney", "Europe/Oslo", "Asia/Tokyo"):
        ZoneInfo(z)
    check("time zones load (tzdata)", True)
    import settings
    check("settings point at the test folder", settings.settings_path().startswith(TMP))
    check("time-zone guess", bool(settings.guess_tz()), settings.guess_tz())


def s2_sky():
    import events
    import sky
    from models import Site, Target
    g = golden()
    site = Site.from_dict(g["site"])
    tg = {t["name"]: Target.from_dict(t) for t in g["targets"]}
    for n in g["nights"]:
        d = date.fromisoformat(n["date"])
        info = sky.night_info(d, site)
        dusk, dawn = sky.hhmm(info["dusk"], site.tz), sky.hhmm(info["dawn"], site.tz)
        check(f"{n['date']} dark {dusk}–{dawn}", minutes(dusk, n["dusk"]) <= 3 and minutes(dawn, n["dawn"]) <= 3,
              f"plan {n['dusk']}–{n['dawn']}")
        j0, j1 = sky.night_span(d, site)
        for name, exp in n.get("transits", {}).items():
            t = tg[name]
            ev = [e for e in events.transit_events(t.planet_obj(), t.ra, t.dec, j0, j1)]
            got = [sky.hhmm(ev[0][k], site.tz) for k in ("t1", "tc", "t4")] if ev else ["—"] * 3
            ok = bool(ev) and all(minutes(a, b) <= 3 for a, b in zip(got, exp))
            check(f"{n['date']} {name} {'/'.join(got)}", ok, "plan " + "/".join(exp))
        for name, exp in n.get("blackouts", {}).items():
            b = [x for x in events.blackouts(tg[name], j0, j1) if minutes(sky.hhmm(x["start"], site.tz), exp[0]) <= 3]
            check(f"{n['date']} {name} blackout {exp[0]}–{exp[1]}", bool(b))
    hd = tg["HD 219134"]
    mer = sky.hhmm(sky.target_meridian(hd.ra, hd.dec, site, sky.local_jd(date(2026, 10, 21), 23, 0, site.tz)), site.tz)
    check(f"HD 219134 meridian 21 Oct {mer}", minutes(mer, "22:40") <= 2, "plan 22:40")


def s3_planner():
    import planner
    import settings
    import sky
    from models import Optics, Site, Target
    g = golden()
    site, optics = Site.from_dict(g["site"]), Optics.from_dict(g["optics"])
    options = dict(settings.DEFAULT_OPTIONS)
    all_t = [Target.from_dict(t) for t in g["targets"]]
    for n in g["nights"]:
        d = date.fromisoformat(n["date"])
        tg = [t for t in all_t if t.name in n["enabled"]]
        night = planner.build_night(d, site, optics, tg, options)
        blocks = night.blocks_obj()
        names = {b.name for b in blocks}
        missing = [x for x in n["enabled"] if x not in names]
        line = ", ".join(f"{b.name} {sky.hhmm(b.start, site.tz)}–{sky.hhmm(b.end, site.tz)}" for b in blocks)
        check(f"{n['date']} every planned target placed", not missing, line if not missing else f"missing {missing}")
        bs = sorted(blocks, key=lambda b: b.start)
        check(f"{n['date']} no overlapping blocks", all(a.end <= b.start + 1e-6 for a, b in zip(bs, bs[1:])))
    t0 = time.time()
    sch = planner.build_schedule(date(2026, 10, 19), 7, site, optics, all_t, options)
    check("7-night build with every target", len(sch.nights) == 7, f"{time.time() - t0:.1f} s")


def s4_weather_export():
    import export
    import planner
    import settings
    import weather
    from models import Optics, Site, Target
    g = golden()
    site, optics = Site.from_dict(g["site"]), Optics.from_dict(g["optics"])
    fx = os.path.join(HERE, "tests", "fixtures", "weather_openmeteo_shobs_20261022.json")
    with open(fx, encoding="utf-8") as fh:
        reply = json.load(fh)
    old = weather.http_get_json
    weather.http_get_json = lambda *a, **k: reply
    try:
        got = weather.OpenMeteo().fetch(site, date(2026, 10, 22), 2, dict(settings.DEFAULT_OPTIONS))
    finally:
        weather.http_get_json = old
    first = got.get("2026-10-22", {})
    check("recorded Open-Meteo forecast parses", bool(first.get("hours")), first.get("summary", ""))
    tg = [Target.from_dict(t) for t in g["targets"]]
    by_id = {t.id: t for t in tg}
    n = planner.build_night(date(2026, 10, 22), site, optics, [t for t in tg if t.name in ("HD 219134", "K2-29 b")],
                            dict(settings.DEFAULT_OPTIONS))
    rows = export.table_rows(n, site, targets_by_id=by_id)
    check("nightly table rows", len(rows) == len(n.blocks) and all(r.get("time") for r in rows))
    csv = export.asiair_csv([n], by_id, site, optics=optics)
    check("ASIAIR CSV", csv.count("\n") + 1 == 1 + len({b["target_id"] for b in n.blocks}), csv.splitlines()[0][:60])
    html = export.plan_card_html([n], site, settings.default_settings(), targets_by_id=by_id)
    check("plan card HTML", "K2-29 b" in html and "HD 219134" in html)


def s5_window():
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
    except Exception as exc:
        say(f"  SKIP  no display ({exc})")
        return
    import subprocess
    # Its own process: the window test turns the network off and uses its own temporary folder.
    p = subprocess.run([sys.executable, os.path.join(HERE, "sched_app.py"), "--selftest-ui"], cwd=HERE,
                       capture_output=True, text=True, timeout=600)
    for line in (p.stdout + p.stderr).strip().splitlines()[-12:]:
        say("        " + line)
    check("window: all pages, a build, the CSV", p.returncode == 0)


def s6_live():
    import urllib.request
    tests = [
        ("NASA Exoplanet Archive", "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query="
         "select+pl_name+from+pscomppars+where+pl_name+%3D+%27HAT-P-23+b%27&format=csv"),
        ("SIMBAD", "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=HD+219134&output.format=ASCII"),
        ("VizieR (VSX copy)", "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync?REQUEST=doQuery&LANG=ADQL"
         "&FORMAT=csv&QUERY=SELECT+Name+FROM+%22B/vsx/vsx%22+WHERE+Name%3D%27BL+Cam%27"),
        ("AAVSO VSX", "https://www.aavso.org/vsx/index.php?view=api.object&ident=BL+Cam&format=json"),
        ("Open-Meteo", "https://api.open-meteo.com/v1/forecast?latitude=35&longitude=-80&hourly=temperature_2m"
         "&forecast_days=1"),
    ]
    for name, url in tests:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SHOBS-SCHED/1.0 selftest"})
            with urllib.request.urlopen(req, timeout=20) as r:
                say(f"  OK    {name} answered (HTTP {r.status})")
        except Exception as exc:
            say(f"  NOTE  {name} did not answer ({exc}); SCHED falls back to its saved copies")


def main() -> int:
    import settings
    say(f"SHOBS-SCHED self-test, version {settings.APP_VERSION}, {time.strftime('%Y-%m-%d %H:%M')}")
    say(f"Folder: {HERE}")
    section("1. Environment", s1_environment)
    section("2. Sky and events vs the Oct 19-24 plan", s2_sky)
    section("3. Planner: the five golden nights", s3_planner)
    section("4. Weather and export", s4_weather_export)
    section("5. Window", s5_window)
    section("6. Live services (information only)", s6_live)
    say("")
    say("ALL PASSED" if not FAILS else f"FAILED: {len(FAILS)} check(s): " + "; ".join(FAILS))
    text = OUT.getvalue()
    for path in (os.path.join(HERE, "selftest_log.txt"),
                 os.path.join(os.path.expanduser("~"), "Downloads", "SHOBS-SCHED_selftest_log.txt")):
        try:
            if os.path.isdir(os.path.dirname(path)):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
        except OSError:
            pass
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
