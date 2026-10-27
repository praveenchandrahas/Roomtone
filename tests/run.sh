#!/usr/bin/env bash
# Measurement-kit tests: the drift tracker and the microphone calibration. Needs python3 with numpy and scipy. No hardware, no recordings.
set -e
cd "$(dirname "$0")"
python3 test_drift.py
python3 test_miccal.py
for t in test_autocal_ir test_autocal_stimulus test_autocal_eqfit test_autocal_sim test_autocal_analysis test_autocal_cli; do python3 $t.py; done   # the analysis test takes about 2 minutes
bash -n ../scripts/rec.sh ../scripts/tone-test.sh ../tools/bartest/play-bar.sh ../tools/bartest/probe-hdmi.sh ../tools/subtest/phone-to-wav.sh && echo "PASS shell syntax"
python3 test_autocal_web.py
python3 test_ctld.py
python3 test_resource_log.py
if python3 -c "import lupa" 2>/dev/null; then python3 test_theater_status.py; else echo "SKIP overlay tests (pip install lupa)"; fi
bash -n ../gui/install-ctld.sh ../overlay/install-theater-status.sh && echo "PASS shell syntax (gui, overlay)"
