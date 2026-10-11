"""Make one Short from a script, or run the daily schedule (render + upload + schedule the next days).

    python -m wild.pipeline make scripts/001-electric-eel.md      # render only -> out/001-electric-eel.mp4
    python -m wild.pipeline daily [--dry-run]                     # what GitHub Actions runs every day
    python -m wild.pipeline check                                 # validate every script
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, metadata, render, sound, voice
from .script import Script, all_scripts, load, problems

log = logging.getLogger("wild")


def seed_of(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def make(sc: Script) -> dict:
    t0 = time.time()
    work = config.WORK / sc.key
    work.mkdir(parents=True, exist_ok=True)
    seed = seed_of(sc.key)
    sp = voice.narrate(sc.text, work / "voice.wav", config.VOICE, config.TARGET_SECONDS)
    log.info("%s: narration %.1f s, %d words", sc.key, sp.duration, sc.word_count)
    speech_end = max(w["end"] for w in sp.words)
    edit = render.build_edit(sc, sp.words, speech_end, seed)
    rng = random.Random(seed)
    sfx = [(0.0, sound.effect("deep", seed), -7.0, 0.0)]
    for i, sh in enumerate(edit.shots[1:], 1):
        if i % 2 == 1 or sh.subject.key != edit.shots[i - 1].subject.key:
            sfx.append((sh.start - 0.5, sound.effect("whoosh", seed + i, length=0.75), -19.0, rng.uniform(-0.3, 0.3)))
    mix, tracks = sound.mix(work, edit.total, [(0.0, sp.wav)], sfx, [(0.0, edit.total + 1, sc.mood)],
                            {"music": config.MUSIC}, seed)
    out = config.OUT / f"{sc.key}.mp4"
    render.encode(edit, mix, out)
    info = {
        "key": sc.key, "file": str(out), "duration": round(edit.total, 2), "shots": len(edit.shots),
        "videos": sum(s.asset.kind == "video" for s in edit.shots),
        "title": metadata.title(sc), "description": metadata.description(sc, edit.credits, tracks),
        "tags": metadata.tags(sc), "render_seconds": round(time.time() - t0),
    }
    out.with_suffix(".json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    log.info("%s: %.1f s, %d shots (%d video) in %ds -> %s", sc.key, edit.total, info["shots"], info["videos"],
             info["render_seconds"], out)
    return info


# ---- daily schedule -------------------------------------------------------------------------------------------
STATE_FILE = config.STATE / "posted.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posted": {}, "failed": {}}


def save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf-8")


def slot_time(day: date, key: str) -> datetime:
    tz = ZoneInfo(config.SCHEDULE["timezone"])
    a, b = config.SCHEDULE["window"]
    ha, ma = map(int, a.split(":"))
    hb, mb = map(int, b.split(":"))
    lo, hi = ha * 60 + ma, hb * 60 + mb
    m = random.Random(f"{day}{key}").randint(lo, hi)
    return datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=tz)


def commit_state(msg: str) -> None:
    if os.environ.get("WILD_GIT_PUSH") != "1":
        return
    subprocess.run(["git", "add", "state"], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", msg], check=False)
    for _ in range(3):
        if subprocess.run(["git", "push", "-q"]).returncode == 0:
            return
        subprocess.run(["git", "pull", "-q", "--rebase"], check=False)


def daily(dry_run: bool = False) -> int:
    st = load_state()
    scripts = [s for s in all_scripts() if not problems(s)]
    tz = ZoneInfo(config.SCHEDULE["timezone"])
    now = datetime.now(tz)
    start = max(date.fromisoformat(config.SCHEDULE["start_date"]), now.date())
    taken_days = {v["day"] for v in st["posted"].values()}
    a = config.SCHEDULE["window"][0]
    days = []
    for k in range(config.SCHEDULE["days_ahead"] + 1):
        d = start + timedelta(days=k)
        h, m = map(int, a.split(":"))
        if d.isoformat() in taken_days or datetime(d.year, d.month, d.day, h, m, tzinfo=tz) < now + timedelta(minutes=75):
            continue
        days.append(d)
    order_file = config.SCRIPTS / "order.txt"      # posting order (clip-rich animals first); file names never change
    if order_file.exists():
        rank = {k.strip(): i for i, k in enumerate(order_file.read_text(encoding="utf-8").split()) if k.strip()}
        scripts.sort(key=lambda s: rank.get(s.key, 10_000 + int(s.key.split("-")[0])))
    queue = [s for s in scripts if s.key not in st["posted"] and st["failed"].get(s.key, 0) < 2]
    if not days and not st.get("redo") and not st.get("swap"):
        log.info("schedule is full (%d posted)", len(st["posted"]))
        return 0
    yt = None
    if not dry_run:
        from .youtube import YouTube, key_tag
        yt = YouTube(os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"], os.environ["YT_REFRESH_TOKEN"])
        log.info("channel: %s", yt.title)
        remote = {t for v in yt.recent() for t in v["tags"]}
        from .youtube import QuotaError
        try:
            swap([list(x) for x in st.get("swap", [])], yt, st)
            redo(list(st.get("redo", [])), yt, st)
        except QuotaError as e:
            log.warning("quota reached while replacing (%s); the rest waits for tomorrow", e)
            return 0
    made = 0
    for day in days:
        while queue:
            sc = queue.pop(0)
            if yt and key_tag(sc.key) in remote:
                log.warning("%s already on the channel; marking posted", sc.key)
                st["posted"][sc.key] = {"day": "unknown", "video_id": "?"}
                continue
            try:
                info = make(sc)
            except Exception as e:  # noqa: BLE001
                log.exception("render failed for %s", sc.key)
                st["failed"][sc.key] = st["failed"].get(sc.key, 0) + 1
                save_state(st)
                continue
            if dry_run:
                log.info("dry run: would publish %s on %s", sc.key, day)
                return 0
            when = slot_time(day, sc.key)
            vid = yt.upload(Path(info["file"]), title=info["title"], description=info["description"],
                            tags=info["tags"] + [key_tag(sc.key)], publish_at=when, category="15")
            st["posted"][sc.key] = {"day": day.isoformat(), "video_id": vid, "publish_at": when.isoformat(),
                                    "title": info["title"]}
            save_state(st)
            commit_state(f"Scheduled {sc.key} for {day}")
            log.info("uploaded %s -> https://youtu.be/%s, publishes %s", sc.key, vid, when)
            made += 1
            break
    left = len([s for s in scripts if s.key not in st["posted"]])
    log.info("done: %d uploaded, %d scripts left in the queue", made, left)
    return 0


def redo(keys: list[str], yt=None, st: dict | None = None) -> int:
    """Re-render already-scheduled Shorts and swap them in: upload the new one for the same slot, then delete the old."""
    from .youtube import YouTube, key_tag
    st = st if st is not None else load_state()
    yt = yt or YouTube(os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"], os.environ["YT_REFRESH_TOKEN"])
    for key in keys:
        old = st["posted"].get(key)
        if not old or not old.get("publish_at"):
            log.warning("%s is not scheduled; skipping", key)
            continue
        when = datetime.fromisoformat(old["publish_at"])
        if when < datetime.now(when.tzinfo) + timedelta(minutes=30):
            log.warning("%s publishes too soon to replace", key)
            continue
        try:
            info = make(load(next(config.SCRIPTS.glob(f"{key}.md"))))
        except Exception:  # noqa: BLE001 — keep the old upload if the new render fails
            log.exception("re-render failed for %s; keeping the scheduled version", key)
            continue
        vid = yt.upload(Path(info["file"]), title=info["title"], description=info["description"],
                        tags=info["tags"] + [key_tag(key)], publish_at=when, category="15")
        try:
            yt.api.videos().delete(id=old["video_id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("could not delete old upload %s: %s", old["video_id"], e)
        st["posted"][key] = dict(old, video_id=vid, replaced=old["video_id"])
        if key in st.get("redo", []):
            st["redo"].remove(key)
        save_state(st)
        commit_state(f"Replaced {key} with a new render")
        log.info("replaced %s: %s -> https://youtu.be/%s (%d of %d shots are clips)", key, old["video_id"], vid,
                 info["videos"], info["shots"])
    return 0


def swap(pairs: list[list[str]], yt=None, st: dict | None = None) -> int:
    """Give a scheduled slot to a different script: upload `new` for the slot of `old`, delete `old`'s upload,
    and put `old` back in the queue."""
    from .youtube import YouTube, key_tag
    st = st if st is not None else load_state()
    yt = yt or YouTube(os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"], os.environ["YT_REFRESH_TOKEN"])
    for old_key, new_key in pairs:
        old = st["posted"].get(old_key)
        if not old or new_key in st["posted"]:
            st["swap"] = [x for x in st.get("swap", []) if x != [old_key, new_key]]
            continue
        when = datetime.fromisoformat(old["publish_at"])
        if when < datetime.now(when.tzinfo) + timedelta(minutes=40):
            log.warning("%s publishes too soon to swap", old_key)
            continue
        try:
            info = make(load(next(config.SCRIPTS.glob(f"{new_key}.md"))))
        except Exception:  # noqa: BLE001
            log.exception("render failed for %s; keeping %s", new_key, old_key)
            continue
        vid = yt.upload(Path(info["file"]), title=info["title"], description=info["description"],
                        tags=info["tags"] + [key_tag(new_key)], publish_at=when, category="15")
        try:
            yt.api.videos().delete(id=old["video_id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("could not delete old upload %s: %s", old["video_id"], e)
        del st["posted"][old_key]
        st["posted"][new_key] = {"day": old["day"], "video_id": vid, "publish_at": old["publish_at"],
                                 "title": info["title"]}
        st["swap"] = [x for x in st.get("swap", []) if x != [old_key, new_key]]
        save_state(st)
        commit_state(f"Swapped {old_key} -> {new_key} for {old['day']}")
        log.info("swapped %s -> %s (https://youtu.be/%s, %d of %d shots are clips)", old_key, new_key, vid,
                 info["videos"], info["shots"])
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("scripts", nargs="+")
    dl = sub.add_parser("daily")
    dl.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    sw = sub.add_parser("swap")
    sw.add_argument("pairs", nargs="+", help="old:new")
    rd = sub.add_parser("redo")
    rd.add_argument("keys", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "redo":
        return redo(a.keys)
    if a.cmd == "swap":
        return swap([x.split(":", 1) for x in a.pairs])
    if a.cmd == "make":
        for p in a.scripts:
            sc = load(Path(p))
            bad = problems(sc)
            if bad:
                log.warning("%s: %s", sc.key, "; ".join(bad))
            make(sc)
        return 0
    if a.cmd == "check":
        n = 0
        for sc in all_scripts():
            bad = problems(sc)
            if bad:
                n += 1
                print(f"{sc.key}: {'; '.join(bad)}")
        print(f"{len(all_scripts())} scripts, {n} with problems")
        return 1 if n else 0
    return daily(a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
