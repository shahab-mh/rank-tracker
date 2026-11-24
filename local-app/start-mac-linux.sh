#!/bin/bash
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3 not found. Install from https://www.python.org/downloads/"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python app.py
