"""Writes docs/SETTINGS.md from the settings classes in stintradio/config.py (names, defaults and the comments there)."""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from stintradio import config  # noqa: E402

src = (root / "stintradio" / "config.py").read_text(encoding="utf-8").splitlines()


def comments(cls_name: str) -> dict:
    out, inside, last = {}, False, None
    for line in src:
        if line.startswith(f"class {cls_name}"):
            inside = True
            continue
        if inside and line.startswith("class "):
            break
        if not inside:
            continue
        m = re.match(r"\s{4}(\w+):\s*[^=#]+=\s*[^#]+?(?:#\s*(.*))?$", line)
        if m:
            last = m.group(1)
            out[last] = (m.group(2) or "").strip()
        elif last and re.match(r"\s{20,}#", line):
            out[last] += " " + line.split("#", 1)[1].strip()
    return out


lines = ["# Settings reference", "",
         "Generated from `stintradio/config.py` by `packaging/gen_settings_doc.py`. Settings live in "
         r"`%APPDATA%\StintRadio\settings.json`; restart StintRadio after editing.", ""]
for name, cls in (("live", config.LiveSettings), ("voice", config.VoiceSettings)):
    lines += [f"## `{name}`", "", "| Option | Default | Meaning |", "|---|---|---|"]
    notes = comments(cls.__name__)
    for f, info in cls.model_fields.items():
        d = info.default
        d = '""' if d == "" else d
        lines.append(f"| `{f}` | `{d}` | {notes.get(f, '').replace('|', '/')} |")
    lines.append("")
lines += ["## Top level", "", "| Option | Default | Meaning |", "|---|---|---|", "| `port` | `6420` | The port of the dashboard and API. |", ""]
(root / "docs" / "SETTINGS.md").write_text("\n".join(lines), encoding="utf-8")
print("docs/SETTINGS.md written")
