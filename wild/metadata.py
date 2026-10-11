"""Title, description (with every credit the licenses ask for) and tags."""
from __future__ import annotations

import re

from .footage import Asset
from .script import Script
from .sound import music_credits

BASE_TAGS = ["animal facts", "animals", "wildlife", "nature", "shorts", "wild animals", "animal kingdom"]


def _hashtag(s: str) -> str:
    return "#" + re.sub(r"[^A-Za-z0-9]", "", s.title())


def title(sc: Script) -> str:
    return sc.title[:100]


def description(sc: Script, credits: list[Asset], tracks: list[str]) -> str:
    hook = sc.lines[0].text
    tags = " ".join(dict.fromkeys(["#animals", "#wildlife", "#animalfacts", "#nature", _hashtag(sc.subject.common)]))
    out = [hook, "", tags, "", "Photos and footage (thank you to these creators):"]
    for a in credits:
        who = a.author or "unknown author"
        out.append(f"• {who}, {a.origin} ({a.license}) {a.page}".rstrip())
    mc = music_credits()
    for t in tracks:
        c = mc.get(t)
        if c:
            out.append(f'Music: "{c["title"]}" by {c["artist"]} (incompetech.com), licensed under Creative Commons: '
                       f"By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/")
    out.append("")
    out.append("CC BY-SA material is used under the same license; no endorsement by the creators is implied.")
    text = "\n".join(out)
    if len(text) > 4900:                       # keep every credit; shorten the URLs first
        text = re.sub(r" https?://\S+", "", text)
    return text[:4900]


def tags(sc: Script) -> list[str]:
    seen, out, size = set(), [], 0
    for t in [sc.subject.common, *sc.tags, *BASE_TAGS]:
        t = t.strip()
        if t and t.lower() not in seen and size + len(t) + 3 < 480:
            seen.add(t.lower())
            out.append(t)
            size += len(t) + 3
    return out
