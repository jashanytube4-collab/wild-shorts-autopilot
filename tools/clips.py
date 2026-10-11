"""Count real video clips available for every script (exact species, plus same-kind animals).

    python tools/clips.py            -> work/clips.json and a summary
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wild import config, footage  # noqa: E402
from wild.script import all_scripts  # noqa: E402

logging.basicConfig(level=logging.ERROR)


def group_clips(word: str) -> int:
    data = footage._get_json("https://pixabay.com/api/videos/",
                             {"key": config.PIXABAY_KEY, "q": word, "per_page": 100, "safesearch": "true",
                              "video_type": "film", "order": "popular"}, ttl_days=7)
    n = 0
    for v in data.get("hits", []):
        tags = v.get("tags", "")
        if footage._FAKE.search(tags):
            continue
        if re.search(rf"(^|, ){re.escape(word)}s?(,|$)", tags.lower()):
            n += 1
    return n


def main() -> int:
    out = {}
    for sc in all_scripts():
        s = sc.subject
        try:
            exact = len(footage.pixabay(s)) + len(footage.commons(s, "video"))
            grp = group_clips(footage._words(s.common)[-1])
        except Exception as e:  # noqa: BLE001
            print(f"{sc.key}: error {e}")
            exact, grp = 0, 0
        out[sc.key] = {"exact": exact, "group": grp}
        print(f"{sc.key:40s} exact={exact:3d} group={grp:3d}", flush=True)
    (config.WORK / "clips.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    rich = sum(1 for v in out.values() if v["exact"] >= 8)
    print(f"{rich} of {len(out)} scripts have 8+ exact clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
