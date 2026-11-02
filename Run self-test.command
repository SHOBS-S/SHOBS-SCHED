#!/bin/bash
# SHOBS-SCHED self-test on a Mac: checks this copy with the same Python the app uses. It changes no data.
# selftest.py writes its log to selftest_log.txt in this folder and a copy to SHOBS-SCHED_selftest_log.txt in your
# Downloads folder (only once the test has run: when no Python could be set up, there is no log).
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh selftest
code=$?
echo
if [ $code -eq 0 ]; then
    echo "Done. The log is selftest_log.txt in this folder (a copy, SHOBS-SCHED_selftest_log.txt, is in your"
    echo "Downloads folder)."
else
    echo "The self-test did not pass or could not run. Leave this window open and copy the message above."
    echo "If the test ran, its log is selftest_log.txt in this folder (and SHOBS-SCHED_selftest_log.txt in Downloads)."
fi
read -r -p "Press Return to close."
exit $code
