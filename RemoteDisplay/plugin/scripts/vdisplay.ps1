<#
Virtual display helper for the RemoteDisplay plugin (Windows PowerShell 5.1, nothing to install).

  vdisplay.ps1 status                          what Windows sees: real monitors, the virtual display, resolutions
  vdisplay.ps1 install [-Resolution WxH] [-Refresh N]
                                               download + verify + install the Virtual Display Driver, then set the
                                               mode. Asks for admin (UAC) itself. Safe to re-run: if the virtual
                                               display exists it only adds the mode to its list and applies it.
  vdisplay.ps1 set [-Resolution WxH] [-Refresh N]
                                               switch the virtual display to a mode it already offers
  vdisplay.ps1 remove                          remove the virtual display and its driver package (asks for admin)

Driver: VirtualDrivers/Virtual-Display-Driver, pinned to one release and checked against its published SHA-256.
#>
param(
    [Parameter(Position = 0)][ValidateSet('status', 'install', 'set', 'remove')][string]$Action = 'status',
    [string]$Resolution = '3440x1440',
    [int]$Refresh = 60,
    [switch]$Elevated
)
$ErrorActionPreference = 'Stop'

$Tag = '25.7.23'
$Zip = 'VirtualDisplayDriver-x86.Driver.Only.zip'   # the 64-bit Windows build despite the name
$Url = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/$Tag/$Zip"
$Sha = 'e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a'
$Name = 'Virtual Display Driver'
$SettingsDir = 'C:\VirtualDisplayDriver'
$Log = Join-Path $env:TEMP 'remotedisplay.log'

if ($Resolution -notmatch '^(\d{3,5})x(\d{3,5})$') { throw "Resolution must look like 3440x1440, got '$Resolution'" }
$W = [int]$Matches[1]; $H = [int]$Matches[2]

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Vdd { Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -eq $Name } }

Add-Type @'
using System;
using System.Runtime.InteropServices;
public class VD {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct DEV { public int cb; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string Name;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string Str; public int Flags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string ID;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string Key; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
  public struct DM {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName; public short dmLogPixels;
    public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency, dmICMMethod,
      dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight; }
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern bool EnumDisplayDevicesW(string d, uint i, ref DEV dd, uint f);
  [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern bool EnumDisplaySettings(string n, int m, ref DM d);
  [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern int ChangeDisplaySettingsExA(string n, ref DM d, IntPtr h, int f, IntPtr l);
}
'@

# The desktop name (\\.\DISPLAYn) of the virtual display, or $null if it is not attached.
function Get-VddScreen {
    for ($i = 0; $i -lt 32; $i++) {
        $d = New-Object VD+DEV; $d.cb = [Runtime.InteropServices.Marshal]::SizeOf($d)
        # [NullString]: a plain $null reaches the API as "", which lists nothing
        if (-not [VD]::EnumDisplayDevicesW([NullString]::Value, [uint32]$i, [ref]$d, 0)) { break }
        if ($d.Str -eq $Name -and ($d.Flags -band 1)) { return $d.Name }
    }
    $null
}

function Set-VddMode {
    $screen = $null
    for ($t = 0; $t -lt 10 -and -not $screen; $t++) { $screen = Get-VddScreen; if (-not $screen) { Start-Sleep 1 } }
    if (-not $screen) { throw 'The virtual display is not attached to the desktop (check Settings > System > Display).' }
    $d = New-Object VD+DM; $d.dmSize = [Runtime.InteropServices.Marshal]::SizeOf($d)
    $offered = $false; $i = 0
    while ([VD]::EnumDisplaySettings($screen, $i, [ref]$d)) {
        if ($d.dmPelsWidth -eq $W -and $d.dmPelsHeight -eq $H -and $d.dmDisplayFrequency -eq $Refresh) { $offered = $true; break }
        $i++
    }
    if (-not $offered) { throw "The virtual display offers no ${W}x${H} at $Refresh Hz. Run 'install' with that mode to add it." }
    $d = New-Object VD+DM; $d.dmSize = [Runtime.InteropServices.Marshal]::SizeOf($d)
    [void][VD]::EnumDisplaySettings($screen, -1, [ref]$d)
    $d.dmPelsWidth = $W; $d.dmPelsHeight = $H; $d.dmDisplayFrequency = $Refresh
    $d.dmFields = 0x80000 + 0x100000 + 0x400000
    $r = [VD]::ChangeDisplaySettingsExA($screen, [ref]$d, [IntPtr]::Zero, 1, [IntPtr]::Zero)
    if ($r -ne 0) { throw "ChangeDisplaySettingsEx returned $r" }
    "Set $screen to ${W}x${H} at $Refresh Hz"
}

function Show-Status {
    $vdd = Get-Vdd
    $real = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue |
        Where-Object { $_.Active } | ForEach-Object {
            $n = ($_.UserFriendlyName | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) -join ''
            if ($n) { $n } else { $_.InstanceName } })
    $cur = Get-CimInstance Win32_VideoController | Where-Object { $_.CurrentHorizontalResolution } |
        ForEach-Object { '{0}: {1}x{2} @ {3} Hz' -f $_.Name, $_.CurrentHorizontalResolution, $_.CurrentVerticalResolution, $_.CurrentRefreshRate }
    [pscustomobject]@{
        virtual_display_installed = [bool]$vdd
        virtual_display_status    = if ($vdd) { "$($vdd.Status)" } else { $null }
        virtual_display_screen    = Get-VddScreen
        monitors_reporting_edid   = $real
        active_modes              = @($cur)
        settings_file             = if (Test-Path "$SettingsDir\vdd_settings.xml") { "$SettingsDir\vdd_settings.xml" } else { $null }
    } | ConvertTo-Json
}

# Add the requested mode to a driver settings file if it is missing; returns $true when the file changed.
function Update-Settings([string]$from, [string]$to) {
    [xml]$xml = Get-Content $from
    $res = $xml.vdd_settings.resolutions
    $have = $res.resolution | Where-Object { $_.width -eq "$W" -and $_.height -eq "$H" }
    if (-not $have) {
        $r = $xml.CreateElement('resolution')
        foreach ($p in @(@('width', "$W"), @('height', "$H"), @('refresh_rate', "$Refresh"))) {
            $e = $xml.CreateElement($p[0]); $e.InnerText = $p[1]; [void]$r.AppendChild($e)
        }
        [void]$res.AppendChild($r)
    }
    New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
    $xml.Save($to)
    -not $have
}

function Get-Driver {
    $dir = Join-Path $env:TEMP "remotedisplay-$Tag"
    $drv = Join-Path $dir 'x\VirtualDisplayDriver'
    if (-not (Test-Path (Join-Path $drv 'MttVDD.inf'))) {
        New-Item -ItemType Directory -Force $dir | Out-Null
        $zip = Join-Path $dir $Zip
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $Sha) { Remove-Item $zip -Force; throw "SHA-256 mismatch for $Zip (got $got); not installing." }
        Expand-Archive $zip -DestinationPath (Join-Path $dir 'x') -Force
    }
    foreach ($f in 'MttVDD.dll', 'mttvdd.cat') {
        $s = Get-AuthenticodeSignature (Join-Path $drv $f)
        if ($s.Status -ne 'Valid') { throw "$f signature is $($s.Status); not installing." }
    }
    $drv
}

function Install-Vdd {
    $existing = Get-Vdd
    if ($existing) {
        $f = "$SettingsDir\vdd_settings.xml"
        if ((Test-Path $f) -and (Update-Settings $f $f)) {
            pnputil /restart-device $existing.InstanceId | Out-Null   # the driver rereads its settings on restart
            Start-Sleep 3
            return 'Added the mode to the settings and restarted the virtual display.'
        }
        return 'The virtual display is already installed.'
    }
    $drv = Get-Driver
    [void](Update-Settings (Join-Path $drv 'vdd_settings.xml') "$SettingsDir\vdd_settings.xml")
    pnputil /add-driver (Join-Path $drv 'MttVDD.inf') /install | Out-Null
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Dev {
  [StructLayout(LayoutKind.Sequential)] public struct SP_DEVINFO_DATA { public int cbSize; public Guid ClassGuid; public int DevInst; public IntPtr Reserved; }
  [DllImport("setupapi.dll", SetLastError=true)] public static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid g, IntPtr h);
  [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool SetupDiCreateDeviceInfoW(IntPtr s, string name, ref Guid g, string desc, IntPtr h, int flags, ref SP_DEVINFO_DATA d);
  [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr s, ref SP_DEVINFO_DATA d, int prop, byte[] buf, int len);
  [DllImport("setupapi.dll", SetLastError=true)] public static extern bool SetupDiCallClassInstaller(int fn, IntPtr s, ref SP_DEVINFO_DATA d);
  [DllImport("setupapi.dll", SetLastError=true)] public static extern bool SetupDiDestroyDeviceInfoList(IntPtr s);
  [DllImport("newdev.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr h, string hwid, string inf, int flags, out bool reboot);
}
'@
    $cls = [Guid]'4D36E968-E325-11CE-BFC1-08002BE10318'
    $set = [Dev]::SetupDiCreateDeviceInfoList([ref]$cls, [IntPtr]::Zero)
    $d = New-Object Dev+SP_DEVINFO_DATA; $d.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($d)
    if (-not [Dev]::SetupDiCreateDeviceInfoW($set, 'Display', [ref]$cls, [NullString]::Value, [IntPtr]::Zero, 1, [ref]$d)) { throw "CreateDeviceInfo failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    $hw = [Text.Encoding]::Unicode.GetBytes("Root\MttVDD`0`0")
    if (-not [Dev]::SetupDiSetDeviceRegistryPropertyW($set, [ref]$d, 1, $hw, $hw.Length)) { throw "SetHardwareId failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    if (-not [Dev]::SetupDiCallClassInstaller(0x19, $set, [ref]$d)) { throw "RegisterDevice failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    [void][Dev]::SetupDiDestroyDeviceInfoList($set)
    $reboot = $false
    if (-not [Dev]::UpdateDriverForPlugAndPlayDevicesW([IntPtr]::Zero, 'Root\MttVDD', (Join-Path $drv 'MttVDD.inf'), 1, [ref]$reboot)) { throw "UpdateDriver failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
    "Installed $Name $Tag (reboot needed: $reboot)"
}

function Remove-Vdd {
    $vdd = Get-Vdd
    if (-not $vdd) { return 'The virtual display is not installed.' }
    pnputil /remove-device $vdd.InstanceId | Out-Null
    $blocks = (pnputil /enum-drivers | Out-String) -split '(?m)^\s*\r?$' | Where-Object { $_ -match 'mttvdd\.inf' }
    foreach ($b in $blocks) {
        if ($b -match 'Published Name:\s+(oem\d+\.inf)') { pnputil /delete-driver $Matches[1] /uninstall /force | Out-Null }
    }
    "Removed the virtual display and its driver package. Settings stay in $SettingsDir."
}

# Driver changes need admin: relaunch once elevated (the user approves the UAC prompt), relay its log, then apply the
# mode from this unelevated process, which owns the user's desktop.
if (($Action -in 'install', 'remove') -and -not (Test-Admin) -and -not $Elevated) {
    Remove-Item $Log -ErrorAction SilentlyContinue
    $child = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", $Action,
        '-Resolution', $Resolution, '-Refresh', $Refresh, '-Elevated')
    $p = Start-Process powershell -Verb RunAs -ArgumentList $child -WindowStyle Hidden -PassThru
    if (-not $p) { throw 'Could not start the elevated step (UAC declined or unavailable).' }
    if (-not $p.WaitForExit(180000)) { throw 'Timed out waiting for the UAC prompt: approve it on the PC or in the remote session.' }
    if (Test-Path $Log) { Get-Content $Log }
    if ($p.ExitCode -ne 0) { exit $p.ExitCode }
    if ($Action -eq 'install') { Set-VddMode }
    return
}

if ($Elevated) {
    try {
        $r = if ($Action -eq 'install') { Install-Vdd } else { Remove-Vdd }
        $r | Out-File $Log -Encoding utf8
    } catch {
        "ERROR: $_" | Out-File $Log -Encoding utf8
        exit 1
    }
    return
}

switch ($Action) {
    'status'  { Show-Status }
    'set'     { Set-VddMode }
    'install' { Install-Vdd; Set-VddMode }
    'remove'  { Remove-Vdd }
}
