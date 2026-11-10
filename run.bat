@echo off
rem Launch SwiftCut (no console window). Pass a video path to open it directly:
rem     run.bat "C:\path\to\video.mp4"
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "%~dp0SwiftCut.pyw" %*
