"""How much footage does every script have? Flags subjects that would run short.

    python tools/coverage.py [first_n]
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wild import footage  # noqa: E402
from wild.script import all_scripts  # noqa: E402

logging.basicConfig(level=logging.ERROR)


def main() -> int:
    scripts = all_scripts()
    if len(sys.argv) > 1:
        scripts = scripts[: int(sys.argv[1])]
    seen: dict[str, int] = {}
    weak = 0
    for sc in scripts:
        subs = {sc.subject.key: sc.subject}
        for l in sc.lines:
            if not l.visual.scenery:
                subs.setdefault(l.visual.key, l.visual)
        report = []
        for key, s in subs.items():
            if key not in seen:
                try:
                    seen[key] = len(footage.pool(s))
                except Exception as e:  # noqa: BLE001
                    print(f"  error {s.common}: {e}")
                    seen[key] = 0
            report.append((s.common, seen[key], key == sc.subject.key))
        main_n = next(n for c, n, m in report if m)
        flag = "WEAK" if main_n < 14 else "ok"
        if main_n < 14:
            weak += 1
        extras = ", ".join(f"{c}={n}" for c, n, m in report if not m and n < 3)
        print(f"{flag:4s} {sc.key:40s} main={main_n:3d} {('low extras: ' + extras) if extras else ''}", flush=True)
    print(f"{weak} weak of {len(scripts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
