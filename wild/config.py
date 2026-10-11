"""Channel settings. Safe to edit; the next run picks changes up."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ASSETS = ROOT / "assets"
WORK = ROOT / "work"
OUT = ROOT / "out"
STATE = ROOT / "state"

W, H, FPS = 1080, 1920, 30

# Shown faintly at the top of every frame. Change it after renaming the channel.
WATERMARK = os.environ.get("WILD_WATERMARK", "@Jashan-z6b")

VOICE = {
    "edge_voice": "en-US-AndrewMultilingualNeural",
    "rate": "+10%",
    "pitch": "+0Hz",
}
TARGET_SECONDS = (44.0, 58.5)      # spoken length window; the rate is nudged to land inside it

SHOT_SECONDS = 2.7                 # average time between cuts (top animal-fact Shorts cut every 2.5-3.5 s)

MUSIC = {"level_db": -5.0, "duck_db": -8.0}

SCHEDULE = {
    "timezone": "America/New_York",
    "window": ("11:00", "11:59"),  # random minute each day
    "days_ahead": 2,               # keep this many future days uploaded and scheduled
    "start_date": "2026-10-12",
}

_ENV = ROOT / ".secrets" / "keys.env"          # local runs; GitHub passes the same names as secrets
if _ENV.exists():
    for _ln in _ENV.read_text(encoding="utf-8").splitlines():
        if "=" in _ln and not _ln.startswith("#"):
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Optional free API keys. With them most shots are real video; without them the edit is mostly moving photos.
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")

USER_AGENT = "WildShortsBot/1.0 (https://github.com/jashanytube4-collab; animal-facts Shorts with credited CC media)"
