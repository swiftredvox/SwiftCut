# Download the FFmpeg build pinned in packaging\ffmpeg.lock into vendor\ffmpeg\bin, verifying its
# SHA-256 checksum first. The download is refused if the checksum doesn't match.
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$lock = @{}
Get-Content (Join-Path $root "packaging\ffmpeg.lock") | Where-Object { $_ -match "^\s*(\w+)=(.+)$" } |
    ForEach-Object { $lock[$Matches[1]] = $Matches[2].Trim() }

$dest = Join-Path $root "vendor\ffmpeg"
$stamp = Join-Path $dest "VERSION.txt"
if ((Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq "$($lock.version) $($lock.sha256)")) {
    Write-Host "FFmpeg $($lock.version) already present in $dest"
    exit 0
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("swiftcut-ffmpeg-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work "ffmpeg.zip"
    Write-Host "Downloading FFmpeg $($lock.version) (about 110 MB)..."
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path $curl) {
        # Windows' built-in curl is far faster than Invoke-WebRequest and shows a compact progress bar.
        & $curl -L --fail --progress-bar -o $zip $lock.url
        if ($LASTEXITCODE -ne 0) { throw "Download failed (curl exit code $LASTEXITCODE)." }
    } else {
        $ProgressPreference = "SilentlyContinue"   # PowerShell 5's progress display slows downloads ~10x
        Invoke-WebRequest -Uri $lock.url -OutFile $zip -UseBasicParsing
    }
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $lock.sha256.ToLower()) {
        throw "Checksum mismatch! expected $($lock.sha256), got $actual. Download discarded."
    }
    Write-Host "Checksum verified."
    $unpacked = Join-Path $work "unpacked"
    Expand-Archive -Path $zip -DestinationPath $unpacked
    $bin = Get-ChildItem $unpacked -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $bin) { throw "ffmpeg.exe not found in the archive" }
    $srcDir = Split-Path $bin.FullName -Parent
    $pkgRoot = Split-Path $srcDir -Parent
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Path (Join-Path $dest "bin") | Out-Null
    Copy-Item (Join-Path $srcDir "ffmpeg.exe"), (Join-Path $srcDir "ffprobe.exe") (Join-Path $dest "bin")
    foreach ($f in "LICENSE", "README.txt") {
        $p = Join-Path $pkgRoot $f
        if (Test-Path $p) { Copy-Item $p $dest }
    }
    Set-Content -Path $stamp -Value "$($lock.version) $($lock.sha256)"
    Write-Host "Installed FFmpeg $($lock.version) to $dest"
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
