"""Golden test: John's hand-made Oct 19–24 2026 plan (tests/fixtures/golden_oct19_24.json) against SCHED.

For each night only that night's targets are enabled. Checked: dusk/dawn within 3 min, transit times within
3 min, HD 219134's 22 Oct block ends before the 04:28 blackout, every listed target gets a block, transit blocks
cover t1−20 … t4+20 min, the HD 219134 blocks the plan started before the meridian (Wed 21, Thu 22) carry a flip
note, and flexible starts are within 30 min of the plan OR the difference is explained below (the plan doc was
made by hand, partly before the 20 Oct flip rule). A side-by-side table is printed for every night."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="sched_golden_")
os.environ["SHOBS_SCHED_HOME"] = _TMP
os.environ["SHOBS_P_SETTINGS"] = os.path.join(_TMP, "none.json")

import planner  # noqa: E402
import sky  # noqa: E402
import events  # noqa: E402
from models import Optics, Site, Target  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "golden_oct19_24.json")

# Why SCHED's flexible start differs from the hand plan by more than 30 min (checked by test_flexible_starts).
# (Every flexible start is currently within 30 min of the hand plan; _why() prints the reasons for smaller ones.)
EXPLAINED: dict = {}


def load():
    with open(FIXTURE, encoding="utf-8") as fh:
        g = json.load(fh)
    site, optics = Site.from_dict(g["site"]), Optics.from_dict(g["optics"])
    return g, site, optics


def targets_for(g, names):
    out = []
    for td in g["targets"]:
        t = Target.from_dict(dict(td, id=td["name"]))
        t.enabled = t.name in names
        out.append(t)
    return out


def clock_jd(d: date, hhmm: str, tz: str) -> float:
    """A plan-doc clock time on the night that starts on d (times before noon are the next morning)."""
    hh, mm = map(int, hhmm.split(":"))
    day = d if hh >= 12 else d + timedelta(days=1)
    return sky.local_jd(day, hh, mm, tz)


def diff_min(a_jd, b_jd):
    return (a_jd - b_jd) * 1440.0


class GoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g, cls.site, cls.optics = load()
        cls.plans = {}
        for n in cls.g["nights"]:
            d = date.fromisoformat(n["date"])
            cls.plans[n["date"]] = planner.build_night(d, cls.site, cls.optics, targets_for(cls.g, n["enabled"]), {})
        cls._print_tables()

    @classmethod
    def _print_tables(cls):
        tz = cls.site.tz
        out = ["", "=" * 118, "GOLDEN: hand plan (Oct 19–24 plan doc) vs SCHED, site clock " + tz, "=" * 118]
        for n in cls.g["nights"]:
            d = date.fromisoformat(n["date"])
            p = cls.plans[n["date"]]
            out.append(f"{d.strftime('%a %d %b')}   dark plan {n['dusk']}–{n['dawn']}   SCHED "
                       f"{sky.hhmm(p.dusk, tz)}–{sky.hhmm(p.dawn, tz)}   Moon {n['moon_pct']}% / "
                       f"{p.moon_illum * 100:.0f}%")
            out.append(f"  {'target':<12} {'plan':<13} {'SCHED':<13} {'Δstart':>7} {'Δend':>6}  "
                       f"{'merid plan/SCHED':<17} notes")
            blocks = {b.name: b for b in p.blocks_obj()}
            for st, en, name, mer in n["plan"]:
                b = blocks.get(name)
                if not b:
                    out.append(f"  {name:<12} {st}–{en}   (no block)")
                    continue
                ds = diff_min(b.start, clock_jd(d, st, tz))
                de = diff_min(b.end, clock_jd(d, en, tz))
                why = EXPLAINED.get((n["date"], name), "")
                note = "; ".join(b.notes[:1] + b.cautions)
                out.append(f"  {name:<12} {st}–{en}   {sky.hhmm(b.start, tz)}–{sky.hhmm(b.end, tz)}   "
                           f"{ds:+6.0f} {de:+6.0f}  {mer} / {sky.hhmm(b.meridian, tz):<8} {note}")
                for line in cls._why(n["date"], name, b, ds, de, d, clock_jd(d, st, tz), clock_jd(d, en, tz), mer):
                    out.append(f"  {'':<12} ↳ {line}")
                if why:
                    out.append(f"  {'':<12} ↳ {why}")
            for x in p.didnt_fit:
                out.append(f"  didn't fit: {x['name']}: {x['reason']}")
            out.append("")
        print("\n".join(out))

    @classmethod
    def _why(cls, iso, name, b, ds, de, d, plan_start=None, plan_end=None, plan_mer=None):
        """Plain reasons for differences over 5 min (the hand plan predates some rules). Each reason is only
        given when it is true for this row."""
        tz = cls.site.tz
        o = planner.DEFAULT_OPTIONS
        lines = []
        mer = b.meridian
        t = next(t for t in cls.g["targets"] if t["name"] == name)
        hand_after = plan_start is not None and plan_mer is not None and plan_start > clock_jd(d, plan_mer, tz)
        if abs(ds) > 5 and b.flip_in_block and b.start < mer and hand_after:
            lines.append(f"flip rule (20 Oct): SCHED starts before the {sky.hhmm(mer, tz)} meridian and lets it "
                         f"flip; the hand plan started just after the meridian (before the rule)")
        if abs(ds) > 5 and not b.fixed and 0 <= (b.start - mer) * 1440 <= 61:
            lines.append(f"flip rule (20 Oct): no start within an hour after the {sky.hhmm(mer, tz)} meridian, "
                         f"and starting before it would be before dark (the hand plan predates the rule)")
        if abs(ds) > 5 and b.fixed:
            if b.t1 - b.start < o["baseline_min"] / 1440 - 1e-6:
                lines.append(f"SCHED's baseline starts at nautical dusk ({sky.hhmm(b.start, tz)}, Sun −12°); the "
                             f"hand plan started earlier, in brighter twilight")
            else:
                lines.append("SCHED's transit block starts 60 min (baseline_min) before ingress; the hand plan "
                             "stretched the baseline back to dusk")
        if abs(de) > 5 and b.fixed:
            lines.append("after-egress baseline: SCHED ends inside the mount's tracking margin when the meridian "
                         "falls in the baseline, else at egress + 60 min")
        if abs(de) > 15 and not b.fixed:
            want = float(t.get("block_min") or 120)
            got = b.minutes()
            early = [n for n in b.notes if n.startswith("Ends ") and "early so" in n]
            if got < want - 1 and early:
                lines.append(f"block cut to {got:.0f} of its {want:.0f} min: {early[0][0].lower()}{early[0][1:]}")
            elif got < want - 1:
                lines.append(f"block cut to {got:.0f} of its {want:.0f} min (free time before the next block)")
            else:
                lines.append(f"SCHED gives a flexible target its block_min ({want:.0f} min); the hand plan "
                             f"stretched this block to fill the night")
        if abs(ds) > 5 and not b.fixed and b.flip_in_block and b.start < mer and not hand_after:
            lines.append(f"both start before the {sky.hhmm(mer, tz)} meridian; SCHED starts "
                         f"{(mer - b.start) * 1440:.0f} min before it (not {o['flip_lead_min']:.0f}) so the block "
                         f"gets its full length before the next block")
        if abs(ds) > 5 and not b.fixed and not lines:
            lines.append("placement differs: SCHED starts flexible blocks near meridian − flip lead, after "
                         "the blocks placed before it (priority order)")
        return lines

    def night(self, iso):
        return next(n for n in self.g["nights"] if n["date"] == iso)

    def test_dark_times(self):
        tz = self.site.tz
        for n in self.g["nights"]:
            d = date.fromisoformat(n["date"])
            p = self.plans[n["date"]]
            self.assertLessEqual(abs(diff_min(p.dusk, clock_jd(d, n["dusk"], tz))), 3.0, n["date"])
            self.assertLessEqual(abs(diff_min(p.dawn, clock_jd(d, n["dawn"], tz))), 3.0, n["date"])

    def test_transit_times(self):
        tz = self.site.tz
        for n in self.g["nights"]:
            d = date.fromisoformat(n["date"])
            for name, (t1, tc, t4) in (n.get("transits") or {}).items():
                b = next(b for b in self.plans[n["date"]].blocks_obj() if b.name == name)
                for got, want in ((b.t1, t1), (b.tc, tc), (b.t4, t4)):
                    self.assertLessEqual(abs(diff_min(got, clock_jd(d, want, tz))), 3.0, f"{name} {want}")

    def test_every_target_gets_a_block(self):
        for n in self.g["nights"]:
            names = {b.name for b in self.plans[n["date"]].blocks_obj()}
            for name in n["enabled"]:
                self.assertIn(name, names, f"{n['date']}: {name} has no block "
                                           f"({self.plans[n['date']].didnt_fit})")

    def test_transit_blocks_cover_baselines(self):
        for n in self.g["nights"]:
            for b in self.plans[n["date"]].blocks_obj():
                if b.kind == "transit":
                    self.assertLessEqual(b.start, b.t1 - 20 / 1440 + 1e-6, b.name)
                    self.assertGreaterEqual(b.end, b.t4 + 20 / 1440 - 1e-6, b.name)

    def test_blackout_oct22(self):
        tz = self.site.tz
        n = self.night("2026-10-22")
        d = date.fromisoformat(n["date"])
        hd = Target.from_dict(dict(next(t for t in self.g["targets"] if t["name"] == "HD 219134"), id="HD"))
        bos = events.blackouts(hd, *sky.night_span(d, self.site))
        start, end = n["blackouts"]["HD 219134"]
        self.assertTrue(any(abs(diff_min(b["start"], clock_jd(d, start, tz))) <= 3 and
                            abs(diff_min(b["end"], clock_jd(d, end, tz))) <= 3 for b in bos), bos)
        b = next(b for b in self.plans["2026-10-22"].blocks_obj() if b.name == "HD 219134")
        self.assertLess(b.end, clock_jd(d, start, tz))
        self.assertTrue(any("No HD 219134" in x and "planet c" in x for x in b.notes), b.notes)

    def test_flip_notes_wed_thu(self):
        # 1.2.0 (fix list 24, John 29 Oct): a flexible block that fits wholly before its meridian runs there (no
        # flip, no settle, no dropped frames) and ends inside the tracking margin; never a mid-run flip
        margin = float(self.site.past_meridian_min or 0.0)
        for iso in ("2026-10-21", "2026-10-22"):
            b = next(b for b in self.plans[iso].blocks_obj() if b.name == "HD 219134")
            self.assertFalse(b.flip_in_block, iso)
            self.assertLess(b.start, b.meridian)
            self.assertLessEqual((b.end - b.meridian) * 1440.0, margin + 0.5, iso)
            self.assertTrue(any("inside your tracking margin" in x for x in b.notes), (iso, b.notes))
        for iso, p in self.plans.items():
            for b in p.blocks_obj():
                if not b.fixed and b.flip_in_block:
                    self.assertTrue(any("let it flip" in x for x in b.notes), (iso, b.name, b.notes))

    def test_k2_29_tracking_margin(self):
        b = next(b for b in self.plans["2026-10-22"].blocks_obj() if b.name == "K2-29 b")
        self.assertFalse(b.flip_in_block)
        self.assertTrue(any("stop by 03:38 at the latest" in x for x in b.notes), b.notes)

    def test_flexible_starts(self):
        tz = self.site.tz
        problems = []
        for n in self.g["nights"]:
            d = date.fromisoformat(n["date"])
            blocks = {b.name: b for b in self.plans[n["date"]].blocks_obj()}
            for st, en, name, mer in n["plan"]:
                b = blocks[name]
                if b.fixed:
                    continue
                ds = diff_min(b.start, clock_jd(d, st, tz))
                if abs(ds) > 30 and (n["date"], name) not in EXPLAINED and not self._why(
                        n["date"], name, b, ds, 0, d, clock_jd(d, st, tz), clock_jd(d, en, tz), mer):
                    problems.append(f"{n['date']} {name}: {ds:+.0f} min, no explanation")
        self.assertEqual(problems, [])

    def test_no_overlaps(self):
        for iso, p in self.plans.items():
            bl = p.blocks_obj()
            for a, b in zip(bl, bl[1:]):
                self.assertLessEqual(a.end + 3 / 1440 - 1e-6, b.start, iso)


class GoldenWeek(unittest.TestCase):
    def test_all_targets_seven_nights(self):
        """All 9 golden targets enabled for 7 nights: the weekly-goal campaign NGC 7789 (goal 1) gets a night, and
        HD 219134 (High, goal 5) gets at least 5."""
        g, site, optics = load()
        targets = [Target.from_dict(dict(td, id=td["name"])) for td in g["targets"]]
        sch = planner.build_schedule(date(2026, 10, 19), 7, site, optics, targets, {})
        nights = {name: 0 for name in ("NGC 7789", "HD 219134")}
        for n in sch.nights_obj():
            names = {b.name for b in n.blocks_obj()}
            for k in nights:
                nights[k] += k in names
            bl = n.blocks_obj()
            for a, b in zip(bl, bl[1:]):
                self.assertLessEqual(a.end, b.start + 1e-9, n.date)
        self.assertGreaterEqual(nights["NGC 7789"], 1)
        # "Shorten for goals only": blocks are shortened only for a campaign behind its goal, so no variable gets a
        # squeezed-in fragment
        kinds = {t.name: t.kind for t in targets}
        for n in sch.nights_obj():
            for b in n.blocks_obj():
                for note in b.notes:
                    if note.startswith("Shortened to make room for "):
                        who = note[len("Shortened to make room for "):]
                        self.assertEqual(kinds[who], "campaign", f"{n.date}: {note}")
        self.assertGreaterEqual(nights["HD 219134"], 5)


if __name__ == "__main__":
    unittest.main()
