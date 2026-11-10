# Create "SwiftCut" shortcuts (with the app icon) that launch the development build.
# Usage:  powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1 [-Here] [-StartMenu] [-Desktop] [-Remove]
# With no switches, creates the project-folder and Start menu shortcuts.
param([switch]$Here, [switch]$StartMenu, [switch]$Desktop, [switch]$Remove)
if (-not ($Here -or $StartMenu -or $Desktop)) { $Here = $true; $StartMenu = $true }
$root = Split-Path -Parent $PSScriptRoot
$targets = @()
if ($Here)      { $targets += Join-Path $root "SwiftCut.lnk" }
if ($StartMenu) { $targets += Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SwiftCut.lnk" }
if ($Desktop)   { $targets += Join-Path ([Environment]::GetFolderPath("Desktop")) "SwiftCut.lnk" }

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $targets) {
    if ($Remove) {
        if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed $lnk" }
        continue
    }
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
    $s.Arguments = "`"$(Join-Path $root 'SwiftCut.pyw')`""
    $s.WorkingDirectory = $root
    $s.IconLocation = (Join-Path $root "swiftcut\resources\swiftcut.ico") + ",0"
    $s.Description = "SwiftCut - trim videos quickly"
    $s.Save()
    Write-Host "Created $lnk"
}
