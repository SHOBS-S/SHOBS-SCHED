#!/usr/bin/env bash
# SHOBS-SCHED on macOS and Linux: finds a Python that can run SHOBS-SCHED, sets up SHOBS-SCHED's own private
# environment, then does one job:  bash shobs_setup.sh run | selftest
# (the Mac's "Start SHOBS-SCHED.command" / "Run self-test.command" and Linux's start_linux.sh call it).
#
# Same rules as setup_python.ps1 on Windows. Pythons are tried in the order 3.13, 3.12, 3.11, then any newer one:
# a brand-new Python often has no ready-made packages yet, and a normal user must never meet a compiler prompt
# (SHOBS-P's Mac test, 27 Oct 2026). When the packages will not install on one Python, the next one is tried.
# For testing: SHOBS_USE_PYTHON=/path/to/python3 tries only that Python.
set -u
ACTION="${1:-run}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="$APP_DIR/shobs_check_python.py"
REQ="$APP_DIR/requirements.txt"
unset PYTHONHOME PYTHONPATH

case "$(uname -s)" in
    Darwin) HOME2="$HOME/Library/Application Support/SHOBS-SCHED" ;;
    *)      HOME2="${XDG_DATA_HOME:-$HOME/.local/share}/SHOBS-SCHED" ;;
esac
mkdir -p "$HOME2"
ENV_DIR="$HOME2/python-env"
ENV_PY="$ENV_DIR/bin/python3"

say() { printf '%s\n' "$*"; }

# One candidate: prints its full path when it can run SHOBS-SCHED (version, SSL, Tkinter), else says why not.
usable() {
    local exe="$1" out
    out="$("$exe" "$CHECK" 2>&1)" || { say "    not usable: $(printf '%s\n' "$out" | tail -n 1)" >&2; return 1; }
    printf '%s\n' "$out" | sed -n 's/^SHOBSPY=//p' | head -n 1
}

candidates() {
    if [ -n "${SHOBS_USE_PYTHON:-}" ]; then
        printf '%s\n' "$SHOBS_USE_PYTHON"
        return
    fi
    local v
    for v in 3.13 3.12 3.11; do
        command -v "python$v" 2>/dev/null
        [ -x "/Library/Frameworks/Python.framework/Versions/$v/bin/python3" ] && \
            printf '%s\n' "/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
        [ -x "/opt/homebrew/bin/python$v" ] && printf '%s\n' "/opt/homebrew/bin/python$v"
        [ -x "/usr/local/bin/python$v" ] && printf '%s\n' "/usr/local/bin/python$v"
    done
    for v in 3.14 3.15 3.16; do
        command -v "python$v" 2>/dev/null
        [ -x "/Library/Frameworks/Python.framework/Versions/$v/bin/python3" ] && \
            printf '%s\n' "/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
    done
    command -v python3 2>/dev/null
}

# pip's words → what to tell the user. A package that has to be compiled is the usual cause on a brand-new Python.
why_failed() {
    local log="$1"
    if grep -qiE "unknown compiler|command line developer tools|xcrun|gcc|clang|meson|building wheel|metadata-generation-failed|python: No such file" "$log"; then
        say "    A package has no ready-made copy for this Python and would have to be compiled. Trying an older Python."
        say "    (If none works: install Python 3.13 from python.org; it has ready-made copies of every package.)"
    elif grep -qiE "could not fetch|connection|timed out|name resolution|SSL" "$log"; then
        say "    The packages could not be downloaded: check the internet connection and run this again."
    else
        say "    The packages did not install on this Python (the last lines above say why). Trying the next one."
    fi
}

install_packages() {     # $1 = the environment's python
    local log="$HOME2/pip_log.txt"
    "$1" -m pip install --disable-pip-version-check --upgrade pip >"$log" 2>&1
    "$1" -m pip install --disable-pip-version-check --only-binary=:all: -r "$REQ" >>"$log" 2>&1 && return 0
    tail -n 5 "$log" | sed 's/^/    /'
    why_failed "$log"
    return 1
}

ready=""
if [ -x "$ENV_PY" ] && "$ENV_PY" "$CHECK" --packages >/dev/null 2>&1; then
    ready="$ENV_PY"
fi
if [ -z "$ready" ]; then
    say "Setting up SHOBS-SCHED's own Python environment (one time; a few minutes)…"
    seen=""
    while IFS= read -r cand; do
        [ -n "$cand" ] || continue
        case " $seen " in *" $cand "*) continue ;; esac
        seen="$seen $cand"
        say "  Trying $cand"
        base="$(usable "$cand")" || continue
        [ -n "$base" ] || continue
        rm -rf "$ENV_DIR"
        if ! "$base" -m venv "$ENV_DIR" >/dev/null 2>&1; then
            say "    could not make an environment with it (venv)"
            continue
        fi
        if install_packages "$ENV_PY" && "$ENV_PY" "$CHECK" --packages >/dev/null 2>&1; then
            ready="$ENV_PY"
            printf '%s\n' "$base" >"$HOME2/python.txt"
            say "  ✔ Using $base"
            break
        fi
    done < <(candidates)
fi
if [ -z "$ready" ]; then
    say ""
    say "✖ No Python on this computer can run SHOBS-SCHED yet."
    say "  Install Python 3.13 from https://www.python.org/downloads/ (on a Mac, then run its"
    say "  'Install Certificates.command'), and start this again."
    exit 2
fi

cd "$APP_DIR" || exit 3
case "$ACTION" in
    run)      exec "$ready" "$APP_DIR/sched_app.py" ;;
    selftest) exec "$ready" "$APP_DIR/selftest.py" ;;
    *)        say "Usage: bash shobs_setup.sh run|selftest"; exit 4 ;;
esac
