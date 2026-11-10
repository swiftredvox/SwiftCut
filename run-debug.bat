@echo off
rem Launch SwiftCut with a console showing live debug logging.
cd /d "%~dp0"
set SWIFTCUT_DEBUG=1
".venv\Scripts\python.exe" -m swiftcut %*
