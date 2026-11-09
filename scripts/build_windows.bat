@echo off
setlocal
py -3 -m venv .build-venv
call .build-venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --onefile --name LANWhiteboardServer server.py
pyinstaller --noconfirm --clean --onefile --windowed --name LANWhiteboardClient --collect-all PySide6 client.py
echo Build complete. See dist folder.
pause
