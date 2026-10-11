"""Footage and photos that are free to use commercially, with the credit each license asks for.

Sources (all searched by the animal's scientific name where possible, so the right species is on screen):
- iNaturalist research-grade observations, photo license CC0 / CC BY / CC BY-SA, most-faved first.
- Wikimedia Commons photos and videos (CC0, public domain, CC BY, CC BY-SA). File titles must name the animal.
- Pexels and Pixabay videos, only when PEXELS_API_KEY / PIXABAY_API_KEY are set. The result's own title or tags
  must name the animal, because a stock search for "electric eel" happily returns any fish.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from . import config
from .script import Subject

log = logging.getLogger("wild.footage")
CACHE = config.WORK / "cache"
S = requests.Session()
S.headers["User-Agent"] = config.USER_AGENT


@dataclass
class Asset:
    id: str
    kind: str            # "photo" | "video"
    url: str
    w: int
    h: int
    author: str
    license: str
    origin: str
    page: str
    duration: float = 0.0
    score: float = 0.0

    def credit(self) -> str:
        who = self.author or "unknown"
        return f"{who} / {self.origin}, {self.license}"


# ---- http with cache + polite retries ----------------------------------------------------------------------
_last_call: dict[str, float] = {}
_GAP = {"api.inaturalist.org": 1.0, "commons.wikimedia.org": 0.5}


def _get_json(url: str, params: dict, headers: dict | None = None, ttl_days: float = 30) -> dict:
    sig = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
    p = CACHE / "search" / f"{sig}.json"
    if p.exists() and time.time() - p.stat().st_mtime < ttl_days * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    host = url.split("/")[2]
    for attempt in range(6):
        wait = _GAP.get(host, 0.2) - (time.time() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = S.get(url, params=params, headers=headers or {}, timeout=40)
        except requests.RequestException as e:
            log.warning("%s: %s", host, e)
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(r.headers.get("Retry-After") or 0) or 4 * (attempt + 1))
            continue
        r.raise_for_status()
        data = r.json()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
        return data
    raise RuntimeError(f"{host} kept failing")


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


_QUALIFIERS = set("""pygmy dwarf lesser greater giant little african asian american red black white grey gray
brown golden northern southern eastern western false sea river mountain desert spotted striped banded blue green
yellow indian chinese japanese european australian bornean sumatran malayan arctic pacific atlantic""".split())


def _names_in(text: str, subject: Subject) -> bool:
    """Does a title/slug/tag string name this subject? "Pygmy hippopotamus" does not name the hippopotamus."""
    t = " ".join(_words(text))
    sci = " ".join(_words(subject.scientific)) if subject.scientific else ""
    if sci and re.search(rf"(^| ){re.escape(sci)}( |$)", t):
        return True
    own = set(_words(subject.common))
    for c in _common_forms(subject):
        for m in re.finditer(rf"(^| ){re.escape(c)}( |$)", t):
            before = t[: m.start()].split()
            if before and before[-1] in _QUALIFIERS and before[-1] not in own:
                continue
            return True
    return False


def _common_forms(subject: Subject) -> list[str]:
    cw = _words(subject.common)
    if not cw:
        return []
    alt = cw[:-1] + [cw[-1][:-1]] if cw[-1].endswith("s") else cw[:-1] + [cw[-1] + "s"]
    return [" ".join(cw), " ".join(alt)]


def _names_in_old(text: str, subject: Subject) -> bool:
    t = " ".join(_words(text))
    cands = []
    if subject.scientific:
        cands.append(" ".join(_words(subject.scientific)))
    if subject.common:
        cw = _words(subject.common)
        cands.append(" ".join(cw))
        if cw and cw[-1].endswith("s"):
            cands.append(" ".join(cw[:-1] + [cw[-1][:-1]]))
        else:
            cands.append(" ".join(cw[:-1] + [cw[-1] + "s"]) if cw else "")
    return any(c and re.search(rf"(^| ){re.escape(c)}( |$)", t) for c in cands)


# ---- iNaturalist --------------------------------------------------------------------------------------------
_INAT_LIC = {"cc0": "CC0", "cc-by": "CC BY 4.0", "cc-by-sa": "CC BY-SA 4.0"}


def inat_taxon(subject: Subject) -> int | None:
    q = subject.scientific or subject.common
    res = _get_json("https://api.inaturalist.org/v1/taxa", {"q": q, "per_page": 10, "is_active": "true"})["results"]
    if not res:
        return None
    exact = [t for t in res if t["name"].lower() == q.lower()]
    best = exact[0] if exact else max(res, key=lambda t: t.get("observations_count", 0))
    return best["id"]


def inat_photos(subject: Subject, limit: int = 90) -> list[Asset]:
    tid = inat_taxon(subject)
    if not tid:
        return []
    data = _get_json("https://api.inaturalist.org/v1/observations", {
        "taxon_id": tid, "photo_license": "cc0,cc-by,cc-by-sa", "quality_grade": "research",
        "order_by": "votes", "per_page": 100, "photos": "true", "captive": "false"})
    out = []
    for rank, ob in enumerate(data.get("results", [])):
        ann = {(a.get("controlled_attribute_id"), a.get("controlled_value_id")) for a in ob.get("annotations", [])}
        if (17, 19) in ann or any(t == 22 and v not in (23, None) for t, v in ann):
            continue                       # dead, or only a track / bone / feather / scat
        for k, ph in enumerate(ob.get("photos", [])[:2]):
            lic = ph.get("license_code")
            if lic not in _INAT_LIC:
                continue
            dims = ph.get("original_dimensions") or {}
            w, h = dims.get("width", 0), dims.get("height", 0)
            if min(w, h) < 700:
                continue
            m = re.search(r"\(c\)\s*([^,]+)", ph.get("attribution", ""))
            author = m.group(1).strip() if m else (ob.get("user") or {}).get("login", "")
            score = 1.0 - rank / 100 - 0.25 * k + (0.25 if h > w else 0) + (0.1 if min(w, h) >= 1500 else 0)
            out.append(Asset(f"inat:{ph['id']}", "photo", ph["url"].replace("/square.", "/original."), w, h, author,
                             _INAT_LIC[lic], "iNaturalist", f"https://www.inaturalist.org/photos/{ph['id']}",
                             score=score))
        if len(out) >= limit:
            break
    return out


# ---- Wikimedia Commons --------------------------------------------------------------------------------------
_BAD_TITLE = re.compile(
    r"\b(map|range|distribution|skeleton|skull|bones?|stamp|drawing|illustration|diagram|logo|chart|coin|plate|"
    r"sketch|painting|lithograph|engraving|cladogram|specimens?|museum|x-ray|eggs?|herbarium|book|page|label|"
    r"poster|figure|fig|taxidermy|stuffed|mounted|preserved|fossil|scan|cartoon|art|statue|sculpture|toy|"
    r"plush|icon|emblem|flag|postage|banknote|tattoo|meat|market|dish|food|sushi|cooked|dead|carcass|roadkill|"
    r"satellite|esa|nasa|iss|landsat|sentinel|astronaut|from space|false colou?r|radar|topograph\w*|aerial view of)\b",
    re.I)
_BAD_CATS = re.compile(r"paintings|drawings|engravings|lithograph|illustrations|maps|watercolou?r|prints|"
                       r"specimens|taxiderm|museum|skeletons|fossils|stamps|art\b|sculptures|historical images|"
                       r"\b1[5-9]\d\d\b|scans|satellite|landsat|sentinel|copernicus|\besa\b|nasa|iss expedition|expedition \d+|"
                       r"from space|international space station|astronaut|alexander gerst|false.colou?r|zoos?|tierpark|"
                       r"zoological gardens?|in captivity|animals in zoos", re.I)
_OK_LIC = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? \d|cc-by(-sa)?-\d|attribution)", re.I)


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def commons(subject: Subject, kind: str, limit: int = 50) -> list[Asset]:
    ft = "video" if kind == "video" else "bitmap"
    if subject.scenery:     # scenery: rated photos first (Commons "Quality images" are real, sharp photographs)
        queries = [f'{subject.common} incategory:"Quality_images"', subject.common]
    else:
        queries = [f'"{n}"' for n in (subject.scientific, subject.common) if n]
    seen, out = set(), []
    for q in queries:
        data = _get_json("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"filetype:{ft} {q}", "gsrlimit": limit,
            "prop": "imageinfo|videoinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 2000,
            "viprop": "derivatives|size"})
        pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
        for p in pages:
            title = p["title"]
            if title in seen:
                continue
            seen.add(title)
            name = title.split(":", 1)[-1].rsplit(".", 1)[0]
            if _BAD_TITLE.search(name.replace("_", " ")):
                continue
            ii = (p.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata", {})
            if not subject.scenery and not _names_in(name, subject):
                meta = _clean(em.get("Categories", {}).get("value", "")) + " " +                     _clean(em.get("ImageDescription", {}).get("value", ""))[:600]
                sci = subject.scientific.lower()
                if not (len(sci.split()) >= 2 and sci in meta.lower().replace("_", " ")):
                    continue               # the species must be named in the title, or by its scientific name
            if subject.scenery and not all(w in _words(name) for w in _words(subject.common)[:2]):
                continue
            lic = _clean(em.get("LicenseShortName", {}).get("value", ""))
            if not _OK_LIC.search(lic):
                continue
            cats = em.get("Categories", {}).get("value", "")
            if _BAD_CATS.search(cats):
                continue
            date = _clean(em.get("DateTimeOriginal", {}).get("value", ""))
            yr = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", date)
            if yr and int(yr.group(1)) < 1985:
                continue
            author = _clean(em.get("Artist", {}).get("value", ""))[:80]
            w, h = ii.get("width", 0), ii.get("height", 0)
            if kind == "photo":
                if ii.get("mime") != "image/jpeg" or min(w, h) < 900 or max(w, h) / max(1, min(w, h)) > 2.1:
                    continue
                url = ii.get("thumburl") or ii.get("url")
                tw, th = ii.get("thumbwidth", w), ii.get("thumbheight", h)
                out.append(Asset(f"commons:{name}", "photo", url, tw, th, author, lic, "Wikimedia Commons",
                                 ii.get("descriptionurl", ""), score=0.75 - len(out) * 0.01 + (0.2 if h > w else 0)))
            else:
                vi = (p.get("videoinfo") or [{}])[0]
                dur = float(vi.get("duration") or ii.get("duration") or 0)
                if dur < 3:
                    continue
                ders = [d for d in vi.get("derivatives", []) if "webm" in d.get("type", "") or "mp4" in d.get("type", "")]
                ders = [d for d in ders if d.get("height", 0) and min(d["width"], d["height"]) >= 480]
                if not ders:
                    continue
                best = min(ders, key=lambda d: abs(min(d["width"], d["height"]) - 1080))
                out.append(Asset(f"commons:{name}", "video", best["src"], best["width"], best["height"], author, lic,
                                 "Wikimedia Commons", ii.get("descriptionurl", ""), dur,
                                 score=1.1 - len(out) * 0.02 + (0.3 if best["height"] > best["width"] else 0)
                                 - (0.9 if min(best["width"], best["height"]) < 720 else 0)))
    return out


# ---- stock video (optional keys) ----------------------------------------------------------------------------
def pexels(subject: Subject) -> list[Asset]:
    if not config.PEXELS_KEY:
        return []
    out = []
    for orient in ("portrait", "landscape"):
        data = _get_json("https://api.pexels.com/videos/search",
                         {"query": subject.common, "per_page": 40, "orientation": orient},
                         {"Authorization": config.PEXELS_KEY}, ttl_days=7)
        for v in data.get("videos", []):
            slug = v.get("url", "").rstrip("/").rsplit("/", 1)[-1]
            if not subject.scenery and not _names_in(slug.replace("-", " "), subject):
                continue
            files = [f for f in v.get("video_files", []) if f.get("height") and f.get("file_type") == "video/mp4"]
            files = [f for f in files if 720 <= min(f["width"], f["height"]) <= 2160]
            if not files or v.get("duration", 0) < 3:
                continue
            f = min(files, key=lambda f: abs(min(f["width"], f["height"]) - 1080))
            out.append(Asset(f"pexels:{v['id']}", "video", f["link"], f["width"], f["height"],
                             (v.get("user") or {}).get("name", ""), "Pexels License", "Pexels", v.get("url", ""),
                             float(v.get("duration", 0)), score=1.5 + (0.3 if f["height"] > f["width"] else 0)
                             - len(out) * 0.02))
    return out


_FAKE = re.compile(r"(ai generated|ai|generated|cgi|render|rendering|3d|animation|animated|cartoon|illustration|"
                   r"toy|plush|statue|sculpture|drawing|painting|model)", re.I)


def pixabay(subject: Subject) -> list[Asset]:
    """Real footage only: AI-generated, CGI and animated clips are dropped by their tags."""
    if not config.PIXABAY_KEY:
        return []
    out, seen = [], set()
    queries = [subject.common]
    cw = _words(subject.common)
    if len(cw) > 1 and not subject.scenery:
        queries.append(cw[-1])            # "honey badger" -> "badger"; the tag check below keeps it on-species
    for q in queries:
        data = _get_json("https://pixabay.com/api/videos/",
                         {"key": config.PIXABAY_KEY, "q": q, "per_page": 100, "safesearch": "true",
                          "video_type": "film", "order": "popular"}, ttl_days=7)
        for v in data.get("hits", []):
            tags = v.get("tags", "")
            if v["id"] in seen or _FAKE.search(tags):
                continue
            if not subject.scenery and not _names_in(tags.replace(",", " , "), subject):
                continue
            vids = v.get("videos", {})
            # 1080p is all a 1080x1920 Short needs; 4K only costs decode time
            f = next((vids[k] for k in ("medium", "large") if vids.get(k, {}).get("url") and vids[k].get("height")
                      and min(vids[k]["width"], vids[k]["height"]) >= 1000), None) or                 next((vids[k] for k in ("large", "medium") if vids.get(k, {}).get("url") and vids[k].get("height")), None)
            if not f or v.get("duration", 0) < 3 or min(f["width"], f["height"]) < 720:
                continue
            seen.add(v["id"])
            zoo = bool(re.search(r"(zoo|aquarium|captive|enclosure)", tags, re.I))
            out.append(Asset(f"pixabay:{v['id']}", "video", f["url"], f["width"], f["height"], v.get("user", ""),
                             "Pixabay Content License", "Pixabay", v.get("pageURL", ""), float(v.get("duration", 0)),
                             score=1.6 - len(out) * 0.01 - (0.3 if zoo else 0) + (0.2 if f["height"] >= 2160 else 0)))
    return out


# ---- pool ---------------------------------------------------------------------------------------------------
def pool(subject: Subject) -> list[Asset]:
    """Candidates for one subject: videos and photos interleaved, best first."""
    vids, photos = [], []
    for fn, args in ((pexels, ()), (pixabay, ()), (commons, ("video",))):
        try:
            vids += fn(subject, *args)
        except Exception as e:  # noqa: BLE001 — one source down never stops the video
            log.warning("%s failed for %s: %s", fn.__name__, subject.common, e)
    photo_src = [] if subject.scenery else [(inat_photos, ()), (commons, ("photo",))]
    if subject.scenery:            # Commons scenery search is too loose (ruins, towns, maps); stock video only
        vids = [v for v in vids if v.origin in ("Pexels", "Pixabay")]
    for fn, args in photo_src:
        try:
            photos += fn(subject, *args)
        except Exception as e:  # noqa: BLE001
            log.warning("%s failed for %s: %s", fn.__name__, subject.common, e)
    if not subject.scenery and len(vids) + len(photos) < 14 and len(subject.scientific.split()) == 2:
        genus = Subject(subject.common, subject.scientific.split()[0])     # rare species: borrow from its genus
        try:
            seen = {a.id for a in photos}
            photos += [a for a in inat_photos(genus) if a.id not in seen]
        except Exception as e:  # noqa: BLE001
            log.warning("genus fallback failed for %s: %s", subject.common, e)
    for a in photos:
        if a.origin == "iNaturalist":   # research grade, wild (not captive): the most natural-looking photos
            a.score += 0.35
    vids.sort(key=lambda a: -a.score)
    photos.sort(key=lambda a: -a.score)
    good_v = [v for v in vids if v.score > 0.6]
    weak_v = [v for v in vids if v.score <= 0.6]      # small clips: a few, spread out, after the strong material
    out = []
    while good_v or photos:        # roughly 3 videos for every photo when videos exist
        for _ in range(3):
            if good_v:
                out.append(good_v.pop(0))
        if photos:
            out.append(photos.pop(0))
        if weak_v and len(out) % 4 == 2:
            out.append(weak_v.pop(0))
    return out + weak_v


# ---- download -----------------------------------------------------------------------------------------------
def fetch(a: Asset) -> Path | None:
    ext = ".jpg" if a.kind == "photo" else (".webm" if "webm" in a.url else ".mp4")
    p = CACHE / "media" / (hashlib.sha1(a.id.encode()).hexdigest()[:16] + ext)
    if p.exists() and p.stat().st_size > 10_000:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    for attempt in range(3):
        try:
            with S.get(a.url, stream=True, timeout=60) as r:
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                size = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        size += len(chunk)
                        if size > 150 * (1 << 20):
                            raise ValueError("file too large")
                        f.write(chunk)
            tmp.replace(p)
            return p
        except Exception as e:  # noqa: BLE001
            log.warning("download failed %s: %s", a.id, e)
            time.sleep(2)
    tmp.unlink(missing_ok=True)
    return None


def probe_video(p: Path) -> tuple[float, int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(p)],
                         capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
        s = d["streams"][0]
        return float(d["format"]["duration"]), int(s["width"]), int(s["height"])
    except (ValueError, KeyError, IndexError):
        return 0.0, 0, 0


def as_dict(a: Asset) -> dict:
    return asdict(a)
