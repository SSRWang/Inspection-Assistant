#!/usr/bin/env bash
set -euo pipefail

# Python version check
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if [[ $(echo -e "3.9\n$PYTHON_VERSION" | sort -V | head -n1) != "3.9" ]]; then
    echo "Python >= 3.9 is required, found $PYTHON_VERSION"
    exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n config.example.yaml config.yaml || true
echo "Edit config.yaml and run: python -m main"
