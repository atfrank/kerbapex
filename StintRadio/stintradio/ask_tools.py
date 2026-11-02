"""The Claude CLI with StintRadio's MCP data server attached, so a spoken question can be answered from the recorded data
stream and the saved laps: Claude calls the tools itself (query, live, corners, lap_trace ...) and then gives its answer.

Same interface as commentary.ClaudeCliClient (`messages.create`), so ask.answer works with either. Only the StintRadio
tools are allowed; the CLI's own file, shell and web tools stay off."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from .config import data_dir

TOOLS = ["list_sessions", "schema", "query", "live", "corners", "stored_laps", "lap_trace"]

NATIVE_TOOLS_TEXT = (
    "You can call the stintradio tools to look at the recorded data before you answer: live (the latest values), query (a "
    "read-only SQL SELECT on the recorded session, 10 rows a second of everything the game reports), corners (the turn map "
    "of the reference lap), stored_laps and lap_trace (the driver's saved laps). Use them when the facts do not contain the "
    "number you need, with at most three calls, then answer. Never say you lack data without trying them first."
)


def mcp_config(path: Path | None = None) -> Path:
    """The --mcp-config file that starts the server with this install's Python."""
    path = path or (data_dir() / "mcp_ask.json")
    root = Path(__file__).resolve().parents[1]
    cfg = {"mcpServers": {"stintradio": {"command": sys.executable, "args": ["-m", "stintradio.mcp_server"],
                                         "cwd": str(root), "env": {"PYTHONUTF8": "1"}}}}
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


class ClaudeMcpClient:
    def __init__(self, exe: str, timeout: float = 75.0, runner=None, config: Path | None = None):
        self.exe, self.timeout = exe, timeout
        self.config = config or mcp_config()
        self._run = runner or (lambda cmd, text: subprocess.run(
            cmd, input=text, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            creationflags=0x08000000 if os.name == "nt" else 0))
        self.messages = self

    def command(self, model: str = "") -> list:
        cmd = [self.exe, "-p", "--output-format", "text", "--tools", "", "--no-session-persistence", "--strict-mcp-config",
               "--mcp-config", str(self.config), "--allowedTools", ",".join(f"mcp__stintradio__{t}" for t in TOOLS)]
        return cmd + (["--model", model] if model else [])

    def create(self, model: str = "", messages=(), **_):
        text = "\n\n".join(m["content"] for m in messages)
        r = self._run(self.command(model), text)
        if r.returncode and model:
            r = self._run(self.command(), text)
        if r.returncode:
            raise RuntimeError(f"claude CLI failed ({r.returncode}): {(r.stderr or r.stdout or '')[-300:]}")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=r.stdout)])
