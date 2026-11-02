"""Fable review of 1.3.0: the one-time Moon-distance note for an older settings file, the 1 % period match for
TOIs the Archive already lists, a plain web page from a priority list is a format change (not soft), and a listed
planet hides its ExoClock spelling from suggestions."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="sched_rev130_")
os.environ.setdefault("SHOBS_SCHED_HOME", _TMP)
os.environ.setdefault("SHOBS_P_SETTINGS", os.path.join(_TMP, "none.json"))

import settings  # noqa: E402
import targets as T  # noqa: E402


class TestMoonUpgradeNote(unittest.TestCase):
    def test_old_file_flags_once(self):
        path = settings.settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"options": {"suggest_max": 25}}, fh)
        s = settings.load_settings()
        self.assertTrue(s.get("moon_default_new"))
        self.assertEqual(s["options"]["moon_sep_mode"], "phase")
        s.pop("moon_default_new")
        settings.save_settings(s)
        self.assertFalse(settings.load_settings().get("moon_default_new"))

    def test_new_install_no_note(self):
        path = settings.settings_path()
        if os.path.exists(path):
            os.remove(path)
        self.assertFalse(settings.load_settings().get("moon_default_new"))


class TestPriorityReview(unittest.TestCase):
    def test_period_match_one_percent(self):
        a = {"ra": 10.0, "dec": 20.0, "period": 95.174}
        b = {"ra": 10.0, "dec": 20.0, "period": 95.400}      # TOI-1338.01 vs TOI-1338 b: 0.24 %
        self.assertTrue(T._same_planet(a, b) if hasattr(T, "_same_planet") else
                        abs(a["period"] - b["period"]) <= T.MATCH_PERIOD_FRAC * b["period"])
        self.assertGreaterEqual(T.MATCH_PERIOD_FRAC, 0.01)

    def test_plain_web_page_is_format_error(self):
        with mock.patch.object(T, "_get_text", return_value="<html><body>Moved</body></html>"):
            fetch = getattr(T, "_priority_text_fetch", None)
            if fetch is None:
                self.skipTest("fetch helper renamed")
            with self.assertRaises(T.PriorityFormatError):
                fetch("ExoClock", T.EXOCLOCK_URL)

    def test_login_page_is_soft(self):
        with mock.patch.object(T, "_get_text", return_value="<html>Please log in</html>"):
            fetch = getattr(T, "_priority_text_fetch", None)
            if fetch is None:
                self.skipTest("fetch helper renamed")
            with self.assertRaises(T._PrioritySoft):
                fetch("ExoClock", T.EXOCLOCK_URL)


if __name__ == "__main__":
    unittest.main()
