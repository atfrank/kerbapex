#!/usr/bin/env python3
"""Start StintRadio's MCP server (the data tools for Claude) with the project's own Python, found the same way sr.py
finds the install. Standard library only: this file just hands over to the install's virtual environment."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sr  # noqa: E402  (same folder)

home = sr.find_home()
if home is None:
    sys.stderr.write("StintRadio is not installed where I can find it; run: sr.py home <path to StintRadio>\n")
    sys.exit(1)
py = home / ".venv" / "Scripts" / "python.exe"
if not py.is_file():
    py = Path(sys.executable)
sys.exit(subprocess.call([str(py), "-m", "stintradio.mcp_server"], cwd=str(home), env={**os.environ, "PYTHONUTF8": "1"}))
