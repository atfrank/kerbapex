"""python -m stintradio.sims list | check <id or file.py> [seconds] | new <id> | contract"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import C, registry
from .check import check, report


def _contract() -> str:
    out = [f"KerbApex sim contract, version {C.CONTRACT_VERSION}", "", "Sample fields (unit, meaning):"]
    out += [f"  {k:11} {u:10} {d}" for k, (u, d) in C.SAMPLE_FIELDS.items()]
    out += ["", "session_info() keys:"] + [f"  {k:10} {d}" for k, d in C.SESSION_INFO_KEYS.items()]
    out += ["", "Capabilities and what they switch on:"] + [f"  {k:14} {v}" for k, v in C.FEATURES.items()]
    return "\n".join(out)


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "list"
    if cmd == "list":
        for row in registry.listing():
            print(f"{row['id']:10} {row['name']:30} {'RUNNING' if row['running'] else '       '}  provides: {', '.join(row['capabilities'])}")
        for e in registry.errors:
            print("  problem:", e)
        print(f"Drop your own adapter in {registry.adapters_dir()}")
        return 0
    if cmd == "contract":
        print(_contract())
        return 0
    if cmd == "new" and len(a) > 1:
        dest = registry.adapters_dir() / f"{a[1]}.py"
        if dest.exists():
            print(f"{dest} already exists")
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (Path(__file__).with_name("template.py")).read_text(encoding="utf-8").replace('id = "mysim"', f'id = "{a[1]}"')
        dest.write_text(text, encoding="utf-8")
        print(f"created {dest}; edit it, then: python -m stintradio.sims check {a[1]}")
        return 0
    if cmd == "check" and len(a) > 1:
        target, secs = a[1], float(a[2]) if len(a) > 2 else 3.0
        if target.endswith(".py"):
            cls = registry.load_file(target)[0]
        else:
            registry.discover()
            cls = registry.get(target)
        class _Cfg:
            game = cls.id
        try:
            adapter = cls.open(_Cfg())
        except OSError as e:
            print(f"could not open {cls.id}: {e}")
            return 2
        res = check(adapter, secs)
        print(report(res, f"{cls.name} ({cls.id})"))
        adapter.close()
        return 1 if res["errors"] else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
