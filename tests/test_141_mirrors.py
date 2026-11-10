"""SHOBS-SCHED 1.4.1, the manual-shots afternoon of 10 Nov 2026: with CDS down, Check comps on WASP-12 b got its APASS
cone from the CfA mirror, whose II/336 copy sent V 0.1–2.1 for stars that IDIA gives as V 12–13 (the same star
097.530316 +29.503188: CfA V 0.230, IDIA V 13.082). SCHED took the first mirror with rows and saved them, so the
Edit dialog said "321 brighter field stars … brightest APASS V 0.11" and "none within 1 mag", and the exposure bound
moved. A reply where most stars are brighter than V 6 is now a damaged copy: the next mirror is asked, and a saved
answer like that counts as not checked."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import targets as T  # noqa: E402

from tests.test_review140b import Home  # noqa: E402
from tests.test_review140e import ASU_REPLY, EMPTY_REPLY  # noqa: E402

HEAD = "RAJ2000\tDEJ2000\tVmag\tB-V\ndeg\tdeg\tmag\tmag\n----------\t----------\t------\t------\n"
CFA_DAMAGED = "#\n#   VizieR Astronomical Server vizier.cfa.harvard.edu\n" + HEAD + (
    "097.537268\t+29.503025\t 1.188\t 0.918\n097.504234\t+29.511227\t 0.218\t 0.240\n"
    "097.530316\t+29.503188\t 0.230\t 0.224\n097.485704\t+29.519347\t 0.446\t 0.637\n")
TAP_DAMAGED = b"RAJ2000,DEJ2000,Vmag,B-V\n97.53,29.50,0.23,0.22\n97.50,29.51,0.22,0.24\n97.48,29.52,0.45,0.64\n"


class DamagedMirror(unittest.TestCase):

    def setUp(self):
        Home(self, copy_fixture=False)
        tmp = tempfile.mkdtemp(prefix="sched_r141m_home_")
        self.addCleanup(shutil.rmtree, tmp, True)
        old = os.environ.get("SHOBS_SCHED_HOME")
        os.environ["SHOBS_SCHED_HOME"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("SHOBS_SCHED_HOME", old) if old
                        else os.environ.pop("SHOBS_SCHED_HOME", None))
        self._get = T.http_get
        self.addCleanup(lambda: setattr(T, "http_get", self._get))
        self.urls = []

    def fake(self, tap=None, replies=(None, CFA_DAMAGED, ASU_REPLY)):
        """tap: bytes to answer TAP with (None = timeout); replies: one per ASU mirror (None = down)."""
        def get(url, timeout=T.TIMEOUT_S):
            self.urls.append(url)
            if url.startswith(T.VIZIER_TAP):
                if tap is None:
                    raise T.FetchError(f"no answer within {timeout:.0f} s")
                return tap
            for base, r in zip(T.VIZIER_ASU, replies):
                if url.startswith(base):
                    if r is None:
                        raise T.FetchError("service unavailable")
                    return r.encode("utf-8")
            raise AssertionError(url)
        T.http_get = get

    def test_damaged_mirror_goes_on_to_the_next(self):
        self.fake()
        data, note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(note, "")
        self.assertEqual([round(r["v"], 2) for r in data["rows"]], [11.57, 11.9, 12.3])
        self.assertTrue(self.urls[-1].startswith(T.VIZIER_ASU[2]))

    def test_only_damaged_copies_is_a_lookup_error(self):
        self.fake(replies=(None, CFA_DAMAGED, CFA_DAMAGED))
        with self.assertRaises(T.LookupError_):
            T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2))     # nothing damaged was saved

    def test_damaged_tap_answer_falls_back_to_asu(self):
        self.fake(tap=TAP_DAMAGED, replies=(ASU_REPLY, None, None))
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(len(data["rows"]), 3)
        self.assertTrue(self.urls[-1].startswith(T.VIZIER_ASU[0]))

    def _save_damaged(self):
        rows = [{"ra": 97.53, "dec": 29.50, "v": v, "bv": 0.2} for v in (0.11, 0.23, 0.45, 1.19)]
        T.cache_write("VizieR", T._comp_key(97.6367, 29.6722, 12.2), "APASS", {"rows": rows, "radius_arcmin": 12.2})

    def test_saved_damaged_answer_counts_as_not_checked(self):
        self._save_damaged()
        self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2))
        self.fake()                                       # a fresh (not refresh=True) ask goes to the network again
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57)
        self.assertEqual(len(data["rows"]), 3)
        self.assertIsNotNone(T.saved_comp_stars(97.6367, 29.6722, 12.2))

    def test_saved_damaged_answer_and_vizier_down(self):
        self._save_damaged()
        self.fake(replies=(None, None, None))
        with self.assertRaises(T.LookupError_):
            T.comp_stars(97.6367, 29.6722, 12.2, 11.57)

    def test_real_fields_pass(self):
        # a field next to a bright star: two stars brighter than V 6 among fifty is a real sky
        self.assertTrue(T.apass_vs_plausible([4.5, 5.9] + [11.0 + i * 0.05 for i in range(48)]))
        self.assertTrue(T.apass_vs_plausible([]))
        self.assertTrue(T.apass_vs_plausible([5.57, 5.9]))          # HD 219134-like: a bright target, few rows
        self.assertFalse(T.apass_vs_plausible([0.11, 0.23, 0.45, 1.19, 13.0]))

    # Fable review of dc8ba55 (S2): a damaged copy next to an empty one must not be saved as "no stars"
    def test_damaged_plus_empty_is_not_no_stars(self):
        for replies in ((None, CFA_DAMAGED, EMPTY_REPLY), (None, EMPTY_REPLY, CFA_DAMAGED)):
            self.urls.clear()
            self.fake(replies=replies)
            with self.assertRaises(T.LookupError_):
                T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
            self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57))

    def test_down_plus_empty_is_not_no_stars(self):
        self.fake(replies=(None, EMPTY_REPLY, None))
        with self.assertRaises(T.LookupError_):
            T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57))

    def test_good_saved_copy_kept_when_only_damaged_answer(self):
        self.fake(replies=(ASU_REPLY, None, None))
        T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.fake(replies=(None, CFA_DAMAGED, CFA_DAMAGED))
        data, note = T.comp_stars(97.6367, 29.6722, 12.2, 11.57, refresh=True)
        self.assertEqual(len(data["rows"]), 3)
        self.assertIn("did not answer", note)
        self.assertEqual(len(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57)["rows"]), 3)

    def test_bright_target_not_judged(self):
        # a V 3.6 target asks for stars to V 5.6: every row is bright, and that is the real sky (Fable S3)
        bright = "#\n" + HEAD + "".join(f"97.6{i}\t+29.6{i}\t {4.0 + i * 0.3:.3f}\t 0.5\n" for i in range(4))
        self.fake(replies=(bright, None, None))
        data, _note = T.comp_stars(97.6367, 29.6722, 12.2, 3.6, refresh=True)
        self.assertEqual(len(data["rows"]), 4)
        self.assertIsNotNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 3.6))

    def test_malformed_saved_copy_is_not_checked(self):
        T.cache_write("VizieR", T._comp_key(97.6367, 29.6722, 12.2), "APASS", {"rows": ["x", 3], "radius_arcmin": 1})
        self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57))
        T.cache_write("VizieR", T._comp_key(97.6367, 29.6722, 12.2), "APASS",
                      {"rows": [{"ra": 97.6, "dec": 29.6, "v": "12.1", "bv": None}], "radius_arcmin": 12.2})
        self.assertIsNotNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57))   # "12.1" reads as a number

    def test_comps_prefetch_heals_a_damaged_copy(self):
        import comps
        from models import Target
        self._save_damaged()
        self.fake()
        t = Target(name="WASP-12 b", kind="transit", mode="T", ra=97.6367, dec=29.6722, vmag=11.57)
        self.assertIsNone(T.saved_comp_stars(97.6367, 29.6722, 12.2, 11.57))
        T.comp_stars(97.6367, 29.6722, 12.2, 11.57)
        self.assertEqual(len(T.saved_comp_stars(t.ra, t.dec, 12.2, t.vmag)["rows"]), 3)
        self.assertTrue(hasattr(comps, "prefetch"))


if __name__ == "__main__":
    unittest.main()
