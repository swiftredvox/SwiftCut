@echo off
rem One-time development setup: creates .venv and installs the exact, checksum-verified
rem dependencies from requirements-dev.txt (which includes requirements.txt).
cd /d "%~dp0"
if not exist .venv (
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
echo.
echo Setup complete. Optional: fetch the pinned FFmpeg with
echo     powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1
echo and create shortcuts with
echo     powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
