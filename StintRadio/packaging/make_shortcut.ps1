# Makes the "KerbApex" shortcuts (desktop and Start menu) that open the KerbApex control panel with its icon, no console window.
#   powershell -ExecutionPolicy Bypass -File packaging\make_shortcut.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent                       # the StintRadio folder
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { throw "No .venv found in $root - set it up first (see docs\BUILDING.md)." }
$icon = Join-Path $root "assets\kerbapex.ico"
if (-not (Test-Path $icon)) { & (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "make_icon.py") }

$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
foreach ($dir in $places) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "KerbApex.lnk"))
    $lnk.TargetPath = $py
    $lnk.Arguments = "-m stintradio gui"
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = "KerbApex control panel: start and stop StintRadio, the coach and the overlay"
    $lnk.WindowStyle = 1
    $lnk.Save()
    Write-Host "shortcut: $(Join-Path $dir 'KerbApex.lnk')"
}
