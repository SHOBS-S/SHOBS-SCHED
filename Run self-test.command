#!/bin/bash
# SHOBS-SCHED self-test on a Mac: checks this copy with the same Python the app uses. It changes no data.
# The log goes to selftest_log.txt here and to your Downloads folder.
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh selftest
code=$?
echo
if [ $code -eq 0 ]; then echo "Done. The log is selftest_log.txt in this folder."; else
    echo "The self-test did not pass or could not run. Copy the message above."; fi
read -r -p "Press Return to close."
exit $code
