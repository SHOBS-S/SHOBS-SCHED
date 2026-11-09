"""SHOBS-SCHED 1.4.0, the reshoot evening of 8 Nov 2026: VizieR's TAP service timed out on every query, so Check
comps (APASS II/336, TAP only) could never answer while the variable search got through on its ASU fallback. The comp
check now falls back to VizieR ASU (CDS, CfA, IDIA) too; an expected lookup failure is logged in one line, not with a
traceback; the comp result line wraps instead of widening the Edit dialog; and a star with nothing to judge says why
in its own words ("VSX gives no range" / "not a VSX variable")."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import planner  # noqa: E402
import targets as T  # noqa: E402
from models import Target  # noqa: E402

from tests.test_review140b import Home, optics  # noqa: E402

ASU_REPLY = """#
#   VizieR Astronomical Server vizier.cds.unistra.fr
#INFO	votable-version=1.99+ (14-Oct-2013)
RAJ2000\tDEJ2000\tVmag\tB-V
deg\tdeg\tmag\tmag
-----------\t-----------\t------\t------
97.636700\t+29.672200\t11.570\t0.570
97.700000\t+29.700000\t11.900\t0.610
97.600000\t+29.650000\t12.300\t
"""

EMPTY_REPLY = """#
#   VizieR Astronomical Server vizier.cfa.harvard.edu
#RESOURCE=yCat_2336
#Table	II_336_apass9:
#Column	RAJ2000	(F10.6)	Right ascension (FK5, Equinox=J2000.0)	[ucd=pos.eq.ra;meta.main]
#Column	DEJ2000	(F10.6)	Declination (FK5, Equinox=J2000.0)	[ucd=pos.eq.dec;meta.main]
"""


class CompsAsuFallback(unittest.TestCase):

    def setUp(self):
        Home(self, copy_fixture=False)
        # the lookup cache lives under SHOBS_SCHED_HOME (Home sets only SHOBS_DATA_HOME): keep fake APASS rows out of
        # the real cache (Fable M1)
        tmp = tempfile.mkdtemp(prefix="sched_r140e_home_")
        old = os.environ.get("SHOBS_SCHED_HOME")
        os.environ["SHOBS_SCHED_HOME"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SHOBS_SCHED_HOME", old) if old
                        else os.environ.pop("SHOBS_SCHED_HOME", None))
        self._get = T.http_get
        self.addCleanup(lambda: setattr(T, "http_get", self._get))
        self.urls = []

    def fake(self, tap_ok=False, asu_ok=(True, True, True), replies=None):
        def get(url, timeout=T.TIMEOUT_S):
            self.urls.append(url)
            if url.startswith(T.VIZIER_TAP):
                if tap_ok:
                    return b"RAJ2000,DEJ2000,Vmag,B-V\n97.6367,29.6722,11.57,0.57\n"
                raise T.FetchError(f"no answer within {timeout:.0f} s")
            for base, ok in zip(T.VIZIER_ASU, asu_ok):
                if url.startswith(base):
                    if ok:
                        r = replies[T.VIZIER_ASU.index(base)] if replies else ASU_REPLY
                        return r.encode("utf-8")
                    raise T.FetchError("service unavailable")
            raise AssertionError(url)
        T.http_get = get

    def test_tap_timeout_falls_back_to_asu(self):
        self.fake(tap_ok=False)
        data, note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(note, "")
        self.assertEqual(len(data["rows"]), 3)
        self.assertAlmostEqual(data["rows"][0]["v"], 11.57)
        self.assertAlmostEqual(data["rows"][0]["bv"], 0.57)
        self.assertIsNone(data["rows"][2]["bv"])
        asu = [u for u in self.urls if u.startswith(T.VIZIER_ASU[0])]
        self.assertEqual(len(asu), 1)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(asu[0]).query)
        self.assertEqual(q["-source"], ["II/336/apass9"])
        self.assertEqual(q["-c.rm"], ["12.20"])
        self.assertTrue(q["Vmag"][0].startswith("<"))
        text = T.comp_check_text(11.57, 97.6367, 29.6722, data)
        self.assertTrue(text.startswith("Comps:"), text)

    def test_mirrors_in_turn(self):
        self.fake(tap_ok=False, asu_ok=(False, False, True))
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(len(data["rows"]), 3)
        self.assertTrue(self.urls[-1].startswith(T.VIZIER_ASU[2]))

    def test_all_down_is_a_lookup_error(self):
        self.fake(tap_ok=False, asu_ok=(False, False, False))
        with self.assertRaises(T.LookupError_):
            T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)

    def test_empty_mirror_goes_on_to_the_next(self):
        # 8 Nov 2026: CfA answered every II/336 query with comments only; IDIA had the stars
        self.fake(tap_ok=False, asu_ok=(False, True, True), replies=(ASU_REPLY, EMPTY_REPLY, ASU_REPLY))
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(len(data["rows"]), 3)
        self.assertTrue(self.urls[-1].startswith(T.VIZIER_ASU[2]))

    def test_all_empty_is_no_comps_not_an_outage(self):
        self.fake(tap_ok=False, asu_ok=(True, True, True), replies=(EMPTY_REPLY,) * 3)
        data, note = T.comp_stars(348.32, 57.17, 12.2, 5.57, refresh=True)
        self.assertEqual((data["rows"], note), ([], ""))

    def test_garbage_is_still_an_error(self):
        self.fake(tap_ok=False, asu_ok=(True, True, True), replies=("#just a comment\n",) * 3)
        with self.assertRaises(T.LookupError_):
            T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)

    def test_out_max_unlimited(self):
        self.fake(tap_ok=False)
        T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.urls[-1]).query)
        self.assertEqual(q["-out.max"], ["unlimited"])

    def test_tap_answer_used_first(self):
        self.fake(tap_ok=True)
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(len(data["rows"]), 1)
        self.assertFalse(any(u.startswith(T.VIZIER_ASU) for u in self.urls))

    def test_parse_numeric_key_skips_units_line(self):
        rows = T.parse_asu_tsv(ASU_REPLY, key="RAJ2000", numeric=True)
        self.assertEqual([r["RAJ2000"].strip() for r in rows], ["97.636700", "97.700000", "97.600000"])
        # the VSX use (key Name) is unchanged
        with self.assertRaises(T.FetchError):
            T.parse_asu_tsv(ASU_REPLY)


class NoVerdictWords(unittest.TestCase):

    def setUp(self):
        Home(self, copy_fixture=False)

    def test_star_without_vsx_entry(self):
        t = Target(name="HD 219134", kind="campaign", ra=348.32, dec=57.17, vmag=11.0, source="SIMBAD")  # not saturating
        p = planner.exposure_plan(t, optics(), {}, 1.05)
        self.assertEqual(p.get("verdict") or "", "")
        self.assertIn("not a VSX variable", p.get("reason", ""))

    def test_vsx_star_without_range(self):
        t = Target(name="X Var", kind="variable", ra=10.0, dec=40.0, vmag=11.0, source="VSX",
                   elements={"vtype": "SR", "mag_max": 11.0, "mag_min": None})
        p = planner.exposure_plan(t, optics(), {}, 1.05)
        self.assertIn("VSX gives no range", p.get("reason", ""))


class ExpectedFailures(unittest.TestCase):

    def test_lookup_failures_are_expected(self):
        try:
            import sched_app
        except Exception as exc:          # no Tk here: the helper is plain Python, read it from the source
            self.skipTest(f"sched_app not importable: {exc}")
        self.assertTrue(sched_app._expected_failure(T.LookupError_("x")))
        self.assertTrue(sched_app._expected_failure(T.NotFound("x")))
        self.assertTrue(sched_app._expected_failure(T.FetchError("x")))
        self.assertFalse(sched_app._expected_failure(ValueError("x")))


if __name__ == "__main__":
    unittest.main()
