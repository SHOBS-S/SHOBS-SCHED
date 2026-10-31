#!/bin/bash
# SHOBS-SCHED on a Mac: double-click to start (the first time it sets up its own Python environment).
# If macOS says it cannot be opened: right-click (or Control-click) it, choose Open, then Open again.
cd "$(dirname "$0")" || exit 1
bash ./shobs_setup.sh run
code=$?
if [ $code -ne 0 ]; then
    echo
    echo "SHOBS-SCHED did not start. Leave this window open and copy the message above."
    read -r -p "Press Return to close."
fi
exit $code
