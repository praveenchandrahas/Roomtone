#!/usr/bin/env bash
# The repository's full test suite, the same command in CI and on your machine (about 5 minutes: the analysis test alone takes about 2).
# Needs python3-numpy python3-scipy python3-yaml; python3-lupa runs the overlay tests too. The tests that apply a run need linux-theater-dsp's
# set-eq.py: set SET_EQ, or keep the checkout next to this one (../linux-theater-dsp), or set DEPS_DIR to a folder that holds it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ -z "${SET_EQ:-}" ]; then
  for c in "${DEPS_DIR:-/nonexistent}/linux-theater-dsp/scripts/set-eq.py" ../linux-theater-dsp/scripts/set-eq.py; do
    [ -f "$c" ] && SET_EQ=$(cd "$(dirname "$c")" && pwd)/set-eq.py && break
  done
fi
if [ -n "${SET_EQ:-}" ]; then export SET_EQ; echo "using SET_EQ=$SET_EQ"; else echo "NOTE: set-eq.py not found; the tests that need it use a stand-in"; fi
bash tests/run.sh
