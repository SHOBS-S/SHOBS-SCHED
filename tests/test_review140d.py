"""SHOBS-SCHED 1.4.0, John's first look (8 Nov 2026): the Edit dialog's precision plot shaded 'over linear' and
'saturated' from peak/t at the shortest exposure, which folded the camera's bias into the slope (TOI-6130 b: 502 ADU
bias, 'saturated' drawn from ~83 s against a true ~546 s). The shading now reads the crossing off the curve's own raw
peaks."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snr  # noqa: E402


def pts(rate, bias, ts=(0.75, 1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 300, 600)):
    return [{"t": float(t), "peak": rate * t + bias} for t in ts]


class ExposureAtPeak(unittest.TestCase):

    def test_bias_not_folded_into_the_slope(self):
        rate, bias = 119.1, 502.0            # ~TOI-6130 b: 11,223 ADU at 90 s with 502 ADU bias
        p = pts(rate, bias)
        t_lin = snr.exposure_at_peak(p, 40129)
        t_sat = snr.exposure_at_peak(p, 65535)
        self.assertAlmostEqual(t_lin, (40129 - bias) / rate, places=6)
        self.assertAlmostEqual(t_sat, (65535 - bias) / rate, places=6)
        self.assertGreater(t_sat, 500)       # the old peak(t0)/t0 rule gave ~80 s
        old = 65535 / (p[0]["peak"] / p[0]["t"])
        self.assertLess(old, 100)

    def test_no_bias_matches_the_old_rule(self):
        p = pts(200.0, 0.0)
        self.assertAlmostEqual(snr.exposure_at_peak(p, 32000), 160.0, places=6)

    def test_over_at_the_shortest_and_never_reached(self):
        p = pts(100000.0, 500.0)
        self.assertEqual(snr.exposure_at_peak(p, 32000), 0.75)
        self.assertIsNone(snr.exposure_at_peak(pts(1.0, 500.0), 65535))
        self.assertIsNone(snr.exposure_at_peak([], 32000))
        self.assertIsNone(snr.exposure_at_peak(pts(1.0, 0.0), 0))

    def test_real_curve_with_bias(self):
        inp = snr.Inputs(zp=20.594, gain=0.256, bias_adu=502.0)
        c = snr.curve(inp, 11.21, [0.75, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200])
        t = snr.exposure_at_peak(c, inp.linear_adu)
        self.assertIsNotNone(t)
        self.assertGreater(t, c[0]["t"])
        self.assertAlmostEqual(snr.peak_adu(inp, 11.21, t), inp.linear_adu, delta=1.0)


if __name__ == "__main__":
    unittest.main()


# ---- the window: Save refreshes the Photometry noise box's estimate tags --------------------------------------------

import tempfile  # noqa: E402

import instrument  # noqa: E402

from tests.test_app import HAVE_TK, Base  # noqa: E402


@unittest.skipUnless(HAVE_TK, "needs Tk")
class SaveRefreshesEstimates(Base):
    """1.4.0 first-start reshoot: after typing the telescope and camera on a fresh Setup and pressing Save, the
    estimate tags kept saying a bare 'estimate' until the page was reloaded; they now show the values in use."""
    use_fakes = False

    def test_tags_show_values_after_save(self):
        d = tempfile.mkdtemp(prefix="sched_r140d_shobs_")     # an empty SHOBS data folder: no FLUX profile
        old = os.environ.get("SHOBS_DATA_HOME")
        os.environ["SHOBS_DATA_HOME"] = d
        self.addCleanup(lambda: os.environ.__setitem__("SHOBS_DATA_HOME", old) if old is not None
                        else os.environ.pop("SHOBS_DATA_HOME", None))
        instrument._CACHE.clear()
        app = self.open()
        setup = app.pages[0]
        app.update()
        for key, val in (("site.name", "Example Observatory"), ("site.lat", "40"), ("site.lon", "-100"),
                         ("site.elevation_m", "750"), ("optics.telescope", "Example 10in"),
                         ("optics.aperture_mm", "250"), ("optics.focal_mm", "1600"), ("optics.pixel_um", "3.76"),
                         ("optics.camera", "ZWO ASI533MC Pro"), ("optics.gain", "100"), ("optics.e_per_adu", "1"),
                         ("optics.full_well_e", "50000"), ("optics.linear_limit_adu", "50000")):
            setup.vars[key].set(val)
        app.update()
        self.assertTrue(setup.save())
        app.update()
        zp = setup.snr_tags["snr_zp"].cget("text")
        self.assertRegex(zp, r"^estimate \d")
        for key in setup.snr_entries:
            self.assertTrue(setup.snr_tags[key].cget("text").startswith("estimate"), key)
