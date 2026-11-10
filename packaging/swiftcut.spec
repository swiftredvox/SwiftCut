# PyInstaller spec for a self-contained, windowed, one-folder SwiftCut build.
# Build with packaging\build.ps1 (it sets SWIFTCUT_FFMPEG_BIN so FFmpeg gets bundled).
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ffmpeg_bin = os.environ.get("SWIFTCUT_FFMPEG_BIN", "")
binaries = []
datas_extra = []
if ffmpeg_bin:
    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        binaries.append((str(Path(ffmpeg_bin) / exe), "ffmpeg"))
    # Ship FFmpeg's licence and version stamp alongside it (GPL requirement / provenance).
    for extra in ("LICENSE", "VERSION.txt", "README.txt"):
        p = Path(ffmpeg_bin).parent / extra
        if p.is_file():
            datas_extra.append((str(p), "ffmpeg"))

# Qt modules SwiftCut never uses - excluding them keeps the bundle far smaller.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtQuick3D",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtHttpServer", "PySide6.QtSpatialAudio",
    "tkinter", "unittest", "pytest",
]

a = Analysis(
    [str(ROOT / "SwiftCut.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=[(str(ROOT / "swiftcut" / "resources"), "swiftcut/resources"),
           (str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
           (str(ROOT / "licenses"), "licenses"), *datas_extra],
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg", "PySide6.QtNetwork"],
    excludes=EXCLUDES,
    noarchive=False,
)
# SwiftCut is English-only: drop Qt's translation files (about 7 MB).
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/").split("/")[:2] or not d[0].startswith("PySide6")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SwiftCut",
    icon=str(ROOT / "swiftcut" / "resources" / "swiftcut.ico"),
    console=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SwiftCut")
