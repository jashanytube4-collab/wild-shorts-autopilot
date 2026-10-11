"""Set the channel description, keywords and banner through the YouTube API.
(The channel name and profile picture can only be changed in YouTube Studio.)

    python tools/apply_branding.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from googleapiclient.http import MediaFileUpload  # noqa: E402

from wild.youtube import YouTube  # noqa: E402

DESCRIPTION = """Untamed Facts: one wild animal fact every day, in under a minute.

The deadliest, weirdest and most surprising animals on Earth. Venomous snakes and jellyfish, record-breaking predators, bizarre ocean creatures, genius birds, and the science that explains them. Every Short is researched and fact-checked, so you learn something real about nature in 60 seconds.

New Short every day. Subscribe so you never miss a wild fact.

Footage and photos come from creators who share their work under free licenses (Pixabay, iNaturalist, Wikimedia Commons). Every video credits them in its description. Music by Kevin MacLeod (incompetech.com), CC BY 4.0."""

KEYWORDS = ('"animal facts" "wild animals" wildlife "dangerous animals" "weird animals" nature "animal shorts" '
            '"ocean animals" "venomous animals" predators "amazing animals" "nature facts" "did you know"')


def main() -> int:
    d = json.loads((ROOT / ".secrets" / "youtube.json").read_text(encoding="utf-8"))
    yt = YouTube(d["client_id"], d["client_secret"], d["refresh_token"])
    ch = yt.api.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    bs = ch.get("brandingSettings", {})
    bs.setdefault("channel", {})
    bs["channel"]["description"] = DESCRIPTION
    bs["channel"]["keywords"] = KEYWORDS
    bs["channel"]["defaultLanguage"] = "en"
    bs["channel"]["country"] = bs["channel"].get("country") or "IN"
    banner = ROOT / "channel_art" / "banner.jpg"
    if banner.exists():
        up = yt.api.channelBanners().insert(media_body=MediaFileUpload(str(banner), mimetype="image/jpeg")).execute()
        bs.setdefault("image", {})["bannerExternalUrl"] = up["url"]
    yt.api.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": bs}).execute()
    print("description, keywords and banner set for", yt.title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
