#!/usr/bin/env bash
# Measurement-kit tests: the drift tracker and the microphone calibration. Needs python3 with numpy and scipy. No hardware, no recordings.
set -e
cd "$(dirname "$0")"
python3 test_drift.py
python3 test_miccal.py
bash -n ../scripts/rec.sh ../scripts/tone-test.sh ../tools/bartest/play-bar.sh ../tools/bartest/probe-hdmi.sh ../tools/subtest/phone-to-wav.sh && echo "PASS shell syntax"
