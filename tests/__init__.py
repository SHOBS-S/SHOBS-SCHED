# The tests never read the computer's own SHOBS instrument profiles (~/.shobs): an empty data folder unless a test
# points SHOBS_DATA_HOME at its own (1.4.0).
import os as _os
import tempfile as _tempfile

_os.environ["SHOBS_DATA_HOME"] = _tempfile.mkdtemp(prefix="sched_tests_shobs_")
