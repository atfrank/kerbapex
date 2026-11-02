---
name: virtual-display
description: Fix a PC stuck at 640x480 (or any wrong resolution) over a remote session because the monitor is off or unplugged, by checking what Windows sees and installing or adjusting a virtual display at e.g. 3440x1440. Use when the user says their resolution dropped to 640x480, they are remote and the monitor is off, or games/OBS/remote tools see the wrong screen size.
---

# Virtual display for remote use

Helper: `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/vdisplay.ps1" <command>`
(commands: `status`, `install`, `set`, `remove`; options `-Resolution 3440x1440 -Refresh 60`).

**Why it happens:** with the monitor off or unplugged the GPU gets no EDID, so Windows falls back to a generic
640x480 display. A virtual display always reports a proper EDID.

1. `status`. `virtual_display_installed` true: skip to step 4. `monitors_reporting_edid` empty and `active_modes`
   showing 640x480 confirms the cause. If a real monitor is listed and the resolution is still wrong, this is not
   the problem: check the display settings or driver instead.
2. Ask which resolution (default 3440x1440 at 60 Hz) and, before downloading, tell the user the file, source and size:
   `VirtualDisplayDriver-x86.Driver.Only.zip` (about 132 KB) from the official GitHub release of
   VirtualDrivers/Virtual-Display-Driver, checked against its published SHA-256. It installs a third-party driver and
   adds a display, so get a clear yes first.
3. `install -Resolution WxH -Refresh N`. It asks for admin itself: the user must approve the UAC prompt on the PC (or in
   the remote session); without it the script times out after 3 minutes. It downloads, verifies the hash and the
   signatures, installs, adds the mode, and applies it. Re-running adds a new mode to an existing install.
4. `set -Resolution WxH -Refresh N` switches between modes the virtual display already offers (the stock list plus
   anything added by `install`; refresh rates 30/60/90/120/144/165/244 are offered at each size).
5. Verify with `status`. Windows reports the display scaled (3440x1440 at 150% shows as 2293x960 to many tools); the
   video controller entry in `active_modes` shows the true mode.

Notes for the user: when the real monitor is on again, Windows may extend the desktop onto both; choose
"Show only on 1/2" in Settings > System > Display. `remove` uninstalls it (settings stay in `C:\VirtualDisplayDriver`).
Never edit `C:\VirtualDisplayDriver\vdd_settings.xml` by hand while the display is in use: use `install` to add a mode.
