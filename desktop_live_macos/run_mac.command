#!/bin/bash
set -u
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10 이상을 설치하세요: https://www.python.org/downloads/macos/"
  read -r -p 'Enter to close...'
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo 'Python virtual environment creation failed'; read -r -p 'Enter to close...'; exit 1; }
fi
. .venv/bin/activate
python -m pip install -r requirements.txt || { echo 'Dependency installation failed'; read -r -p 'Enter to close...'; exit 1; }
python desktop_monitor.py --window
status=$?
echo "Exit status: $status"
read -r -p 'Enter to close...'
