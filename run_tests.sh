#!/usr/bin/env bash
# Runs the full automated test suite. Intended to gate deployment: a non-zero
# exit code here means the app must not be shipped.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
