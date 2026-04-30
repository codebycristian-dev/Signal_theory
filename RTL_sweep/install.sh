#!/usr/bin/env bash
# RTL Sweep Pro — convenience installer for Linux / macOS.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "==> Creating virtual environment in .venv"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrading pip"
pip install --upgrade pip

echo "==> Installing requirements"
pip install -r requirements.txt

echo
echo "RTL Sweep Pro is installed."
echo "Activate the venv with:  source .venv/bin/activate"
echo "Then run:                python main.py"
echo "Or, without hardware:    python main.py --mock"
