@echo off
rem Run all automated tests (unit + integration). Add -k NAME to filter, e.g. test.bat -k fast
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pytest tests %*
