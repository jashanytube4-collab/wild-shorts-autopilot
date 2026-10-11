"""Script files: scripts/NNN-slug.md

    title: Why You Should Never Touch An Electric Eel
    subject: electric eel | Electrophorus electricus
    mood: danger                      (danger | curious | epic: picks the music)
    tags: electric eel, amazon, electric fish
    ---
    This is why you should *never touch* this creature.
    It can fire *860 volts* in a single shock.
    That is enough to knock a *caiman* off its feet. [caiman | Caiman crocodilus]
    It hunts in the dark waters of the Amazon. [~amazon river]

One sentence per line. *Words* are highlighted yellow (numbers are highlighted automatically).
A line may end with a visual override in brackets: another organism as "common | Scientific name",
or "~words" for scenery with no particular animal. Lines without one show the subject.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import SCRIPTS

MOODS = ("danger", "curious", "epic")


@dataclass
class Subject:
    common: str
    scientific: str = ""          # empty for scenery
    scenery: bool = False

    @property
    def key(self) -> str:
        return ("~" if self.scenery else "") + (self.scientific or self.common).lower()


@dataclass
class Line:
    text: str                     # spoken text, no markup
    words: list[str]              # written words as displayed
    hi: list[bool]                # highlight flag per word
    visual: Subject


@dataclass
class Script:
    path: Path
    key: str
    title: str
    subject: Subject
    mood: str
    tags: list[str]
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(l.text for l in self.lines)

    @property
    def word_count(self) -> int:
        return sum(len(l.words) for l in self.lines)


def parse_subject(s: str) -> Subject:
    s = s.strip()
    if s.startswith("~"):
        return Subject(s[1:].strip(), "", True)
    if "|" in s:
        a, b = (x.strip() for x in s.split("|", 1))
        return Subject(a, b)
    return Subject(s, "")


_NUM = re.compile(r"\d")


def parse_line(raw: str, default: Subject) -> Line:
    visual = default
    m = re.search(r"\[([^\]]+)\]\s*$", raw)
    if m:
        visual = parse_subject(m.group(1))
        raw = raw[: m.start()].rstrip()
    words, hi = [], []
    inside = False
    for tok in raw.split():
        start = tok.startswith("*") or (tok[:1] in "\"'(" and tok[1:2] == "*")
        if start:
            inside = True
        w = tok.replace("*", "")
        words.append(w)
        hi.append(inside or bool(_NUM.search(w)))
        if tok.rstrip(".,!?;:\"')").endswith("*"):
            inside = False
    return Line(" ".join(words), words, hi, visual)


def load(path: Path) -> Script:
    text = path.read_text(encoding="utf-8")
    head, _, body = text.partition("\n---")
    meta = {}
    for ln in head.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    subject = parse_subject(meta["subject"])
    sc = Script(path, path.stem, meta["title"], subject, meta.get("mood", "curious"),
                [t.strip() for t in meta.get("tags", "").split(",") if t.strip()])
    for ln in body.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            sc.lines.append(parse_line(ln, subject))
    return sc


def all_scripts() -> list[Script]:
    return [load(p) for p in sorted(SCRIPTS.glob("[0-9]*.md"))]


def problems(sc: Script) -> list[str]:
    out = []
    n = sc.word_count
    if not 118 <= n <= 160:
        out.append(f"{n} words (want 118-160)")
    if len(sc.title) > 70:
        out.append(f"title too long ({len(sc.title)})")
    if sc.mood not in MOODS:
        out.append(f"mood {sc.mood!r}")
    if len(sc.lines[0].words) > 15:
        out.append("hook line over 15 words")
    if not sc.subject.scientific:
        out.append("subject has no scientific name")
    for l in sc.lines:
        if "*" in l.text or "[" in l.text:
            out.append(f"markup left in: {l.text[:40]}")
        if re.search(r"(hmm|wait|actually, it was|todo|fixme)", l.text, re.I):
            out.append(f"draft note left in: {l.text[:40]}")
        if len(l.words) > 26:
            out.append(f"long line ({len(l.words)} words): {l.text[:40]}")
    return out
