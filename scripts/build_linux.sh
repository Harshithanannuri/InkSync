#!/bin/bash
set -euo pipefail
python3 -m venv .build-venv
source .build-venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --onefile --name LANWhiteboardServer server.py
pyinstaller --noconfirm --clean --onefile --name LANWhiteboardClient --collect-all PySide6 client.py
echo "Build complete. See dist folder."
