"""Split a batch file into numbered scripts.

    python tools/add_scripts.py batch.txt

The batch holds several scripts, each starting with a line "@@ slug". They are numbered after the last existing
script, in order. A slug that already exists is skipped (so re-running a batch is safe).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wild.script import load, problems  # noqa: E402

SCRIPTS = ROOT / "scripts"


def main(path: str) -> int:
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^@@\s*(\S+)\s*$", text, flags=re.M)[1:]
    existing = {p.stem.split("-", 1)[1] for p in SCRIPTS.glob("[0-9]*.md")}
    n = max([int(p.stem.split("-")[0]) for p in SCRIPTS.glob("[0-9]*.md")] or [0])
    bad = 0
    for slug, body in zip(parts[::2], parts[1::2]):
        if slug in existing:
            print(f"skip {slug} (exists)")
            continue
        n += 1
        out = SCRIPTS / f"{n:03d}-{slug}.md"
        out.write_text(body.strip() + "\n", encoding="utf-8")
        issues = problems(load(out))
        if issues:
            bad += 1
            print(f"{out.name}: {'; '.join(issues)}")
    print(f"now {n} scripts; {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
