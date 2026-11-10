# Build a SwiftCut release: portable ZIP, installer, FFmpeg source archive and SHA-256 checksums.
# Output: dist\release\
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\release.ps1 [-SkipTests]
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$version = (& $py -c "import sys; sys.path.insert(0, r'$root'); import swiftcut; print(swiftcut.__version__)").Trim()
$out = Join-Path $root "dist\release"
Write-Host "== SwiftCut $version release"

if (-not $SkipTests) {
    Write-Host "== Running tests"
    & $py -m pytest (Join-Path $root "tests") -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Tests failed - release stopped." }
}

Write-Host "== Pinned FFmpeg"
& powershell -ExecutionPolicy Bypass -File (Join-Path $root "tools\get_ffmpeg.ps1")
if ($LASTEXITCODE -ne 0) { throw "Could not get the pinned FFmpeg." }

Write-Host "== Building the app folder"
& powershell -ExecutionPolicy Bypass -File (Join-Path $root "packaging\build.ps1")
if ($LASTEXITCODE -ne 0) { throw "Build failed." }
$exe = Join-Path $root "dist\SwiftCut\SwiftCut.exe"
$built = (& $exe --version | Out-String).Trim()
if ($built -ne $version) { throw "Built app reports version '$built', expected '$version'." }

if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null

Write-Host "== Portable ZIP"
$zip = Join-Path $out "SwiftCut-$version-portable.zip"
Compress-Archive -Path (Join-Path $root "dist\SwiftCut") -DestinationPath $zip -CompressionLevel Optimal

Write-Host "== Installer"
$iscc = Get-ChildItem "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)." }
& $iscc.FullName "/DAppVersion=$version" "/Q" (Join-Path $root "packaging\swiftcut.iss")
if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }

Write-Host "== FFmpeg source (GPL: distributed alongside the binaries)"
$lock = @{}
Get-Content (Join-Path $root "packaging\ffmpeg.lock") | Where-Object { $_ -match "^\s*(\w+)=(.+)$" } | ForEach-Object { $lock[$Matches[1]] = $Matches[2].Trim() }
$srcName = "ffmpeg-$($lock.version).tar.xz"
$cache = Join-Path $root "vendor\$srcName"
if (-not (Test-Path $cache)) {
    & (Join-Path $env:SystemRoot "System32\curl.exe") -L --fail -sS --retry 3 --retry-delay 5 -o $cache "https://ffmpeg.org/releases/$srcName"
    if ($LASTEXITCODE -ne 0) { throw "Could not download $srcName" }
}
Copy-Item $cache (Join-Path $out $srcName)

Write-Host "== Checksums"
$lines = Get-ChildItem $out -File | Sort-Object Name | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
}
Set-Content -Path (Join-Path $out "SHA256SUMS.txt") -Value $lines -Encoding ascii
Get-ChildItem $out -File | ForEach-Object { "{0,-40} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host "== Done: $out"
