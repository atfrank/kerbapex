# Builds the distributable: KerbApex (KerbApex.exe, the control panel, and StintRadio.exe, the engine) with PyInstaller, a zip, and the
# installer when Inno Setup (ISCC.exe) is installed. Run from anywhere:
#   powershell -File packaging\build.ps1
# The output goes OUTSIDE the repository (%LOCALAPPDATA%\KerbApex\build, or $env:KERBAPEX_BUILD): the repository lives in OneDrive, which
# locks and uploads a hundred megabytes of binaries.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$ver = (& $py -c "import stintradio; print(stintradio.__version__)").Trim()
$out = if ($env:KERBAPEX_BUILD) { $env:KERBAPEX_BUILD } else { Join-Path $env:LOCALAPPDATA "KerbApex\build" }
$dist, $work, $rel = (Join-Path $out "dist"), (Join-Path $out "work"), (Join-Path $out "release")
foreach ($d in $dist, $work, $rel) { New-Item -ItemType Directory -Force $d | Out-Null }

& $py -m pytest -q
if ($LASTEXITCODE) { throw "tests failed" }
& $py packaging\gen_settings_doc.py

Push-Location packaging
if (Test-Path (Join-Path $dist "KerbApex")) { Remove-Item (Join-Path $dist "KerbApex") -Recurse -Force }
& $py -m PyInstaller --noconfirm --distpath $dist --workpath $work stintradio.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }
Pop-Location

$zip = Join-Path $rel "KerbApex-$ver-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $dist "KerbApex") -DestinationPath $zip
Write-Host "zip: $zip"

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$ver" "/DDistDir=$dist" "/DOutDir=$rel" packaging\installer.iss
    Write-Host "installer: $(Join-Path $rel "KerbApex-Setup-$ver.exe")"
} else {
    Write-Host "Inno Setup not found: install it from https://jrsoftware.org/isinfo.php to also build the installer."
}
Write-Host "built in $out"
