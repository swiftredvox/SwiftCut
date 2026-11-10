# Build a portable, self-contained SwiftCut folder: dist\SwiftCut\SwiftCut.exe (+ bundled FFmpeg).
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-FFmpegBin "C:\path\to\ffmpeg\bin"] [-Zip]
param([string]$FFmpegBin = "", [switch]$Zip)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not $FFmpegBin) {
    $pinned = Join-Path $root "vendor\ffmpeg\bin"
    if (Test-Path (Join-Path $pinned "ffmpeg.exe")) {
        $FFmpegBin = $pinned            # the checksum-verified build from tools\get_ffmpeg.ps1
    } else {
        Write-Warning "Pinned FFmpeg not found (run tools\get_ffmpeg.ps1). Falling back to the system FFmpeg."
        $probe = & $py -c "from swiftcut.core.ffmpeg_tools import find_ffmpeg; import os; print(os.path.dirname(find_ffmpeg().ffmpeg))"
        $FFmpegBin = $probe.Trim()
    }
}
Write-Host "Bundling FFmpeg from $FFmpegBin"
$env:SWIFTCUT_FFMPEG_BIN = $FFmpegBin

# PyInstaller comes from the hash-locked dev requirements (installed by setup.bat).
& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install --quiet --require-hashes -r (Join-Path $root "requirements-dev.txt") }
Push-Location $root
try {
    & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\swiftcut.spec
} finally { Pop-Location }

$out = Join-Path $root "dist\SwiftCut"
Write-Host "Built $out"
if ($Zip) {
    $version = & $py -c "import swiftcut; print(swiftcut.__version__)"
    $zipPath = Join-Path $root "dist\SwiftCut-$($version.Trim())-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path $out -DestinationPath $zipPath
    Write-Host "Zipped $zipPath"
}
