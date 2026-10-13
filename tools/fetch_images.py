"""Download and compress every image the timeline uses, so the page can ship them itself.

Reads tools/images.json and writes:
  img/ai/<key>.jpg     AI-generated era and event art
  img/wiki/<key>.jpg   the freely licensed lead image of each event's Wikipedia article
  img/credits.json     author, licence and source page for each Wikimedia image
"""
import html
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "img"
MAX_W = 760
QUALITY = 78
API = "https://en.wikipedia.org/w/api.php"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = (
    "RiversOfTime/1.0 (https://github.com/SHNVTeach/Ancient-Civilizations-Timeline)"
)


def save_jpeg(raw: bytes, dest: Path) -> int:
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (16, 14, 11))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img = img.convert("RGB")
    if img.width > MAX_W:
        img = img.resize((MAX_W, round(img.height * MAX_W / img.width)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    return dest.stat().st_size


def get(url: str, **params) -> requests.Response:
    for attempt in range(4):
        r = SESSION.get(url, params=params or None, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt * 3)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch_ai(items, report):
    for it in items:
        dest = OUT / "ai" / f"{it['key']}.jpg"
        try:
            size = save_jpeg(get(it["url"]).content, dest)
            report["ai_ok"] += 1
            report["bytes"] += size
        except Exception as exc:  # keep going; the page falls back to other art
            report["failed"].append(f"ai {it['key']}: {exc}")


def fetch_wiki(items, report, credits):
    for i in range(0, len(items), 20):
        batch = items[i : i + 20]
        data = get(
            API,
            action="query",
            format="json",
            formatversion="2",
            redirects="1",
            prop="pageimages",
            piprop="thumbnail|name",
            pithumbsize=str(MAX_W),
            pilicense="free",
            titles="|".join(it["title"] for it in batch),
        ).json()
        q = data.get("query", {})
        # map each requested title through normalisation and redirects to its page
        alias = {}
        for step in q.get("normalized", []) + q.get("redirects", []):
            alias[step["from"]] = step["to"]
        pages = {p["title"]: p for p in q.get("pages", [])}
        for it in batch:
            title = it["title"].replace("_", " ")
            while title in alias:
                title = alias[title]
            page = pages.get(title)
            if not page or "thumbnail" not in page:
                report["no_image"].append(it["title"])
                continue
            try:
                size = save_jpeg(get(page["thumbnail"]["source"]).content,
                                 OUT / "wiki" / f"{it['key']}.jpg")
                report["wiki_ok"] += 1
                report["bytes"] += size
                credits[it["key"]] = {
                    "page": page["title"],
                    "file": page.get("pageimage", ""),
                }
            except Exception as exc:
                report["failed"].append(f"wiki {it['title']}: {exc}")
        time.sleep(0.5)


def fetch_credits(credits):
    """Author and licence for each file, from Commons metadata."""
    keys = [k for k, v in credits.items() if v["file"]]
    for i in range(0, len(keys), 25):
        batch = keys[i : i + 25]
        files = {"File:" + credits[k]["file"].replace("_", " "): k for k in batch}
        data = get(
            "https://commons.wikimedia.org/w/api.php",
            action="query",
            format="json",
            formatversion="2",
            prop="imageinfo",
            iiprop="extmetadata",
            titles="|".join(files),
        ).json()
        q = data.get("query", {})
        alias = {n["from"]: n["to"] for n in q.get("normalized", [])}
        back = {alias.get(t, t): k for t, k in files.items()}
        for p in q.get("pages", []):
            k = back.get(p["title"])
            info = (p.get("imageinfo") or [{}])[0].get("extmetadata", {})
            if k:
                credits[k]["author"] = plain(info.get("Artist", {}).get("value", ""))[:120]
                credits[k]["license"] = plain(info.get("LicenseShortName", {}).get("value", ""))
        time.sleep(0.5)


def main():
    manifest = json.loads((ROOT / "tools" / "images.json").read_text())
    report = {"ai_ok": 0, "wiki_ok": 0, "bytes": 0, "no_image": [], "failed": []}
    credits = {}
    fetch_ai(manifest["ai"], report)
    fetch_wiki(manifest["wiki"], report, credits)
    try:
        fetch_credits(credits)
    except Exception as exc:
        report["failed"].append(f"credits: {exc}")
    (OUT / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=1))
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in report.items()}))
    for line in report["failed"]:
        print("FAILED", line)
    if report["ai_ok"] + report["wiki_ok"] == 0:
        sys.exit("no images downloaded")


if __name__ == "__main__":
    main()
