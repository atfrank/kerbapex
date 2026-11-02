"""Push-to-talk: hold a key (or a wheel button) and speak; the audio is recorded with ffmpeg from the microphone and handed
on when the button is let go. Nothing is recorded while the button is up, and nothing is kept: the clip is deleted once it
has been transcribed.

The button is read by polling (GetAsyncKeyState, or winmm for a wheel button), so it works over a fullscreen game and
needs no hook or extra package."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

FLAGS = 0x08000000 if sys.platform == "win32" else 0
MIN_S, MAX_S = 0.4, 20.0

KEYS = {"rctrl": 0xA3, "lctrl": 0xA2, "rshift": 0xA1, "lshift": 0xA0, "ralt": 0xA5, "lalt": 0xA4, "space": 0x20,
        "capslock": 0x14, "scrolllock": 0x91, "pause": 0x13, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pagedown": 0x22, "tab": 0x09, "`": 0xC0}
KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})
KEYS.update({f"num{n}": 0x60 + n for n in range(10)})
KEYS.update({str(n): 0x30 + n for n in range(10)})
KEYS.update({chr(c + 32): c for c in range(ord("A"), ord("Z") + 1)})


class Button:
    """Is the push-to-talk button down? `spec` is a key name ("f9", "rctrl") or a wheel/joystick button ("joy1:5" =
    device 1, button 5, as numbered in Windows' game controller panel)."""

    def __init__(self, spec: str):
        spec = spec.strip().lower().replace(" ", "")
        m = re.fullmatch(r"joy(\d+):(\d+)", spec)
        self.joy = (int(m.group(1)) - 1, int(m.group(2)) - 1) if m else None
        self.vk = None if self.joy else KEYS.get(spec)
        if self.joy is None and self.vk is None:
            raise ValueError(f"unknown push-to-talk button '{spec}' (use a key like f9 or rctrl, or joy1:5)")

    def down(self) -> bool:
        import ctypes
        if self.vk is not None:
            return bool(ctypes.windll.user32.GetAsyncKeyState(self.vk) & 0x8000)
        info = (ctypes.c_uint32 * 13)(52, 0xFF)                    # JOYINFOEX: dwSize, dwFlags = JOY_RETURNALL
        if ctypes.windll.winmm.joyGetPosEx(self.joy[0], ctypes.byref(info)) != 0:
            return False
        return bool(info[8] >> self.joy[1] & 1)                     # dwButtons


def microphones(ff: str, run=subprocess.run) -> list[str]:
    """DirectShow audio capture devices, as ffmpeg lists them."""
    r = run([ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=FLAGS)
    return re.findall(r'"([^"]+)"\s*\(audio\)', r.stderr or "")


class Recorder:
    """ffmpeg capturing the microphone to a 16 kHz mono wav while it runs."""

    def __init__(self, ff: str, mic: str = "", popen=subprocess.Popen, run=subprocess.run):
        self.ff, self.mic, self.popen, self.run = ff, mic, popen, run
        self.proc = None
        self.path: Optional[Path] = None

    def device(self) -> str:
        if not self.mic:
            found = microphones(self.ff, self.run)
            if not found:
                raise OSError("no microphone found (ffmpeg lists no DirectShow audio devices)")
            self.mic = found[0]
        return self.mic

    def start(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="stintradio_")) / "ask.wav"
        self.proc = self.popen([self.ff, "-loglevel", "error", "-y", "-f", "dshow", "-audio_buffer_size", "50", "-i",
                                f"audio={self.device()}", "-ac", "1", "-ar", "16000", str(self.path)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=FLAGS)

    def stop(self) -> Optional[Path]:
        """Finish the file (ffmpeg writes the header on 'q') and return it."""
        p, self.proc = self.proc, None
        if p is None:
            return None
        try:
            p.stdin.write(b"q")
            p.stdin.flush()
            p.wait(timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            p.kill()
        return self.path if self.path and self.path.is_file() else None


class PushToTalk:
    """Polls the button and records. mode "hold": recording lasts while the button is held. mode "toggle": one press starts,
    the next press (or `max_s` seconds) stops, so the hands can stay on the wheel. On stopping, the clip goes to
    `on_clip(path)` on its own thread. `beep(high)` is called when recording starts (True) and stops (False)."""

    DEBOUNCE_S = 0.35        # a toggle press counts once, however much the button chatters

    def __init__(self, button: Button, recorder: Recorder, on_clip: Callable[[Path], None], poll: float = 0.03,
                 clock=time.monotonic, mode: str = "hold", max_s: float = MAX_S,
                 beep: Optional[Callable[[bool], None]] = None, on_start: Optional[Callable[[], None]] = None):
        self.button, self.rec, self.on_clip, self.poll, self.clock = button, recorder, on_clip, poll, clock
        self.on_start = on_start        # called when a press begins a question (the engineer is cut off)
        self.mode, self.max_s, self.beep = mode, max_s, beep
        self.stop_flag = False
        self.error = ""
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.stop_flag = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_flag = True

    def _begin(self) -> float:
        if self.on_start:
            self.on_start()
        self.rec.start()
        if self.beep:
            self.beep(True)
        return self.clock()

    def _finish(self, began: float) -> None:
        length = self.clock() - began
        path = self.rec.stop()
        if self.beep:
            self.beep(False)
        if path is not None and length >= MIN_S:
            threading.Thread(target=self.on_clip, args=(path,), daemon=True).start()

    def run(self) -> None:
        began = None
        was_down, last_press = False, -1e9
        while not self.stop_flag:
            try:
                down = self.button.down()
                if self.mode == "toggle":
                    press = down and not was_down and self.clock() - last_press >= self.DEBOUNCE_S
                    if press:
                        last_press = self.clock()
                        if began is None:
                            began = self._begin()
                        else:
                            began, b = None, began
                            self._finish(b)
                    elif began is not None and self.clock() - began > self.max_s:
                        began, b = None, began
                        self._finish(b)
                    was_down = down
                else:
                    if down and began is None:
                        began = self._begin()
                    elif began is not None and (not down or self.clock() - began > self.max_s):
                        began, b = None, began
                        self._finish(b)
            except Exception as e:                      # a missing microphone must not end the coach
                self.error = f"{type(e).__name__}: {e}"
                began = None
                time.sleep(2)
            time.sleep(self.poll)
        if began is not None:
            self.rec.stop()


def beeper(high: bool) -> None:
    """A short tone: higher when recording starts, lower when it stops (Windows only; silent elsewhere)."""
    try:
        import winsound
        winsound.Beep(1100 if high else 650, 70)
    except Exception:
        pass
