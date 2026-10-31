#!/bin/bash
# SHOBS-SCHED on Linux: bash start_linux.sh   (or: bash start_linux.sh selftest)
# The first time it sets up its own Python environment (needs Python 3.11+ with Tkinter: e.g. python3-tk).
cd "$(dirname "$0")" || exit 1
exec bash ./shobs_setup.sh "${1:-run}"
