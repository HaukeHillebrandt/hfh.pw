#!/usr/bin/env python3
"""Static site builder for hfh.pw.

Fetches content from Google Drive (public folder listing), Substack and
Bearblog, then renders a static site into dist/.

Every Google Doc shows its first tab only (other tabs stay reachable in
Google Docs):
  - single-tab docs: /<slug> embeds the live doc (edits show within minutes),
                     /reader/<slug> is a typeset article of it
  - multi-tab docs : /<slug> is a typeset article of the first tab (Google's
                     live views can't be limited to one tab neatly)

Stdlib only; Pillow (optional) for images and social cards.
"""
import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import html as htmllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from docrender import parse_tabs, plain, strip_comments, to_article

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
FONT_DIR = os.path.join(ROOT, "assets", "fonts")
UA = {"User-Agent": "Mozilla/5.0 (compatible; hfh-pw-builder; +https://github.com/HaukeHillebrandt/hfh.pw)"}


def _load(name, default):
    path = os.path.join(ROOT, "data", name)
    return json.load(open(path)) if os.path.exists(path) else default


CONFIG = json.load(open(os.path.join(ROOT, "config.json")))
SLUG_HARVEST = _load("slugs_harvest.json", {})
# Written by tools/discover_published.py (needs Google auth, so run locally):
# canonical 2PACX pub URLs (some published docs 401 on the ID-based endpoint)
# and creation dates for Drive docs with no known publication date.
PUBLISHED_LINKS = _load("published_links.json", {})
DOC_META = _load("doc_meta.json", {})
BASE_URL = os.environ.get("SITE_BASE", CONFIG["site"]["base_url"]).rstrip("/")
AUTHOR = CONFIG["site"]["author"]

report = {"built_at": datetime.now(timezone.utc).isoformat(), "warnings": [], "docs": {}}


def warn(msg):
    report["warnings"].append(msg)
    print(f"  [warn] {msg}", file=sys.stderr)


def fetch(url, timeout=30, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - retry any network error
            last = e
    raise last


CACHE_DIR = os.path.join(ROOT, "data", "cache")


def cached_json(name, producer):
    """Network-first data source with a committed JSON fallback cache.

    Some feeds (Substack) block GitHub Actions runner IPs; the cache keeps
    the site complete when that happens and refreshes whenever a fetch works.
    Caching parsed data (not raw responses) means the files only change when
    the content actually changes, so the Action's auto-commit doesn't churn.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name + ".json")
    try:
        data = producer()
        if not data:
            raise ValueError("empty result (blocked?)")
        new = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False)
        old = open(path).read() if os.path.exists(path) else None
        if new != old:
            with open(path, "w") as f:
                f.write(new)
        return data
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):
            warn(f"{name}: live fetch failed ({e}); using committed cache")
            return json.loads(open(path).read())
        raise


def template(name):
    return open(os.path.join(ROOT, "templates", name)).read()


def render(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def esc(s):
    return htmllib.escape(s, quote=True)


def slugify(title):
    s = re.sub(r"[’'\"]", "", title.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60].rstrip("-") or "post"


# ---------------------------------------------------------------- sources

def fetch_drive_folder(fid):
    return cached_json(f"drive_{fid}", lambda: _parse_drive_folder(
        fetch(f"https://drive.google.com/embeddedfolderview?id={fid}#list")))


def _parse_drive_folder(src):
    out = []
    for chunk in src.split('<div class="flip-entry" ')[1:]:
        eid = re.search(r'id="entry-([^"]+)"', chunk)
        href = re.search(r'<a href="([^"]+)"', chunk)
        title = re.search(r'flip-entry-title">([^<]*)</div>', chunk)
        mod = re.search(r'flip-entry-last-modified"><div>([^<]*)</div>', chunk)
        if not (eid and href):
            continue
        out.append({
            "id": eid.group(1),
            "url": htmllib.unescape(href.group(1)),
            "title": htmllib.unescape(title.group(1)) if title else "?",
            "modified": mod.group(1) if mod else None,
        })
    return out


def parse_mdy(s):
    """Drive folder dates: '10/19/23' or 'Feb 11' (this year) -> ISO date."""
    if not s:
        return None
    try:
        m, d, y = s.split("/")
        return f"20{y}-{int(m):02d}-{int(d):02d}"
    except ValueError:
        pass
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    parts = s.split()
    if len(parts) == 2 and parts[0] in months:
        try:
            return f"{datetime.now().year}-{months[parts[0]]:02d}-{int(parts[1]):02d}"
        except ValueError:
            pass
    return None


def parse_rss(xml):
    items = []
    for chunk in re.split(r"<item>", xml)[1:]:
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", chunk, re.S)
        link = re.search(r"<link>([^<]*)</link>", chunk)
        date = re.search(r"<pubDate>([^<]*)</pubDate>", chunk)
        desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", chunk, re.S)
        iso = None
        if date:
            try:
                iso = parsedate_to_datetime(date.group(1)).date().isoformat()
            except Exception:  # noqa: BLE001
                pass
        if t and link:
            d = htmllib.unescape(re.sub(r"<[^>]+>", "", desc.group(1))) if desc else ""
            items.append({
                "title": htmllib.unescape(t.group(1).strip()),
                "url": link.group(1).strip(),
                "date": iso,
                "excerpt": d.strip()[:220],
            })
    return items


def fetch_substack():
    """Substack posts from the RSS feed, or from the archive API when the feed is blocked."""
    feed = CONFIG["feeds"]["substack"]
    try:
        return parse_rss(fetch(feed))
    except Exception as feed_err:  # noqa: BLE001
        api = feed.rsplit("/feed", 1)[0] + "/api/v1/archive?sort=new&offset=0&limit=50"
        try:
            posts = json.loads(fetch(api))
        except Exception as api_err:  # noqa: BLE001
            raise RuntimeError(f"feed: {feed_err}; archive API: {api_err}") from None
        print(f"  substack: feed failed ({feed_err}); used the archive API")
        return [{"title": p["title"].strip(), "url": p["canonical_url"],
                 "date": (p.get("post_date") or "")[:10] or None,
                 "excerpt": (p.get("description") or p.get("subtitle") or "").strip()[:220]}
                for p in posts if p.get("canonical_url")]


# ---------------------------------------------------------------- doc probing

def probe_doc(doc):
    """Publication state, tabs and first-tab HTML export of one Google Doc."""
    did = doc["doc_id"]
    result = {"published": False, "export_html": None, "restricted": False,
              "first_tab": "t.0", "tab_count": 1, "tab_title": None}
    if did not in PUBLISHED_LINKS:
        try:
            body = fetch(f"https://docs.google.com/document/d/{did}/pub", retries=1)
            result["published"] = 'id="contents"' in body or "doc-content" in body
        except Exception:  # noqa: BLE001 - unpublished/restricted docs 401 here
            pass
    try:
        prev = fetch(f"https://docs.google.com/document/d/{did}/preview", retries=1)
        if "ServiceLogin" in prev or "accounts.google.com/v3/signin" in prev:
            result["restricted"] = True
        else:
            first, tabs = parse_tabs(prev)
            count = re.search(r'"%s"\s*,\s*(\d+)\s*,\s*undefined' % re.escape(first), prev)
            result["first_tab"] = first
            result["tab_count"] = int(count.group(1)) if count else max(1, len(tabs))
            result["tab_title"] = next((t for i, t, _ in tabs if i == first), None)
    except Exception:  # noqa: BLE001
        pass
    try:
        result["export_html"] = strip_comments(fetch(
            f"https://docs.google.com/document/d/{did}/export?format=html&tab={result['first_tab']}",
            retries=1))
    except Exception as e:  # noqa: BLE001
        warn(f"no HTML export for '{doc['title']}' ({did}): {e}")
    if result["restricted"]:
        warn(f"RESTRICTED doc (login wall for visitors): '{doc['title']}' ({did}) "
             f"- share it as 'anyone with the link' to fix")
    return result


def doc_embed_url(doc):
    if doc.get("kind") == "file":
        return f"https://drive.google.com/file/d/{doc['doc_id']}/preview"
    pl = PUBLISHED_LINKS.get(doc["doc_id"])
    if pl and pl.get("publishAuto"):
        return pl["url"] + "?embedded=true"
    if doc.get("published"):
        return f"https://docs.google.com/document/d/{doc['doc_id']}/pub?embedded=true"
    return f"https://docs.google.com/document/d/{doc['doc_id']}/preview"


def doc_open_url(doc):
    if doc.get("kind") == "file":
        return f"https://drive.google.com/file/d/{doc['doc_id']}/view"
    url = f"https://docs.google.com/document/d/{doc['doc_id']}/edit"
    return url + f"?tab={doc['first_tab']}" if doc.get("tab_count", 1) > 1 else url


def text_from_export(export_html, limit=3000):
    """Plain text of the doc body (span-aware tag stripping), for search."""
    m = re.search(r"<body[^>]*>(.*)</body>", export_html, re.S)
    if not m:
        return ""
    body = re.sub(r"<style.*?</style>", " ", m.group(1), flags=re.S)
    body = re.sub(r"</(?:p|h[1-6]|li|td|div|br)>", " ", body)
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", body))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def excerpt_from_export(export_html, title):
    """First real paragraph of the doc, skipping title/byline boilerplate."""
    if not export_html:
        return ""
    m = re.search(r"<body[^>]*>(.*)</body>", export_html, re.S)
    if not m:
        return ""
    tnorm = re.sub(r"[^a-z0-9]", "", title.lower())
    for para in re.findall(r"<p[^>]*>(.*?)</p>", m.group(1), re.S):
        text = htmllib.unescape(re.sub(r"<[^>]+>", "", para))
        text = re.sub(r"\[\w{1,3}\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^[a-z](?=[A-Z])", "", text)  # stray footnote superscript
        norm = re.sub(r"[^a-z0-9]", "", text.lower())
        if len(text) < 60:
            continue
        if "@" in text and len(text) < 200:  # byline with email
            continue
        if tnorm and norm.startswith(tnorm[:24]):
            continue
        words = text.split(" ")
        return " ".join(words[:36]) + ("…" if len(words) > 36 else "")
    return ""


# ---------------------------------------------------------------- collect posts

def collect_posts():
    print("Fetching Drive folder…")
    root_entries = fetch_drive_folder(CONFIG["drive_folder_id"])
    drive_docs = {}
    for e in root_entries:
        if "/folders/" in e["url"]:
            print(f"  subfolder: {e['title']}")
            for sub in fetch_drive_folder(e["id"]):
                if "/folders/" in sub["url"]:
                    continue
                sub["folder"] = e["title"]
                drive_docs[sub["id"]] = sub
        else:
            drive_docs[e["id"]] = e

    posts = {}  # slug -> post
    used_doc_ids = set()

    # 1. Posts with a known publication date (harvested from the old Google
    #    Site / Inkhaven feed), plus manual fixes from config.
    harvest = dict(SLUG_HARVEST)
    for slug, info in CONFIG.get("slug_overrides", {}).items():
        harvest[slug] = {**harvest.get(slug, {}), **info}
    for slug, info in harvest.items():
        doc_id = info.get("docId")
        kind = "doc"
        if not doc_id:
            femb = [e for e in info.get("embeds", []) if "/file/d/" in e]
            if femb:
                doc_id = re.search(r"/file/d/([\w-]+)", femb[0]).group(1)
                kind = "file"
            else:
                warn(f"slug '{slug}' has no resolvable embed; skipped")
                continue
        used_doc_ids.add(doc_id)
        posts[slug] = {
            "slug": slug, "title": info["title"], "date": info["date"],
            "date_kind": "published", "doc_id": doc_id, "kind": kind,
            "source": "essay", "inkhaven": info["date"] >= "2025-11-01",
        }

    # 2. Other Drive-folder docs, dated by creation (or last edit if unknown)
    for did, e in drive_docs.items():
        if did in used_doc_ids:
            continue
        if e["title"] in CONFIG["exclude_titles"] or did == CONFIG["cv_doc_id"]:
            continue
        kind = "doc" if "/document/" in e["url"] else "file"
        slug = slugify(e["title"])
        while slug in posts:
            slug += "-2"
        meta = DOC_META.get(did)
        if meta:
            date, date_kind = meta["created"], "created"
        else:
            date, date_kind = parse_mdy(e["modified"]) or "2020-01-01", "updated"
        posts[slug] = {
            "slug": slug, "title": e["title"], "date": date, "date_kind": date_kind,
            "doc_id": did, "kind": kind, "source": "essay",
            "folder": e.get("folder"), "inkhaven": False,
        }

    # 3. External posts
    print("Fetching feeds…")
    external = []
    eaforum = []
    try:
        eaforum = cached_json("eaforum", fetch_eaforum_posts)
    except Exception as e:  # noqa: BLE001
        warn(f"EA Forum lookup failed: {e}")
    try:
        for it in cached_json("substack", fetch_substack):
            if it["url"].rstrip("/") == "https://hauke.substack.com":
                continue
            external.append({**it, "source": "substack"})
    except Exception as e:  # noqa: BLE001
        warn(f"substack feed failed: {e}")
    try:
        for it in cached_json("bearblog",
                              lambda: parse_rss(fetch(CONFIG["feeds"]["bearblog"]))):
            external.append({**it, "source": "note"})
    except Exception as e:  # noqa: BLE001
        warn(f"bearblog feed failed: {e}")

    # Drive docs also published on the EA Forum or Substack take that date.
    published = [(it["title"], it["date"]) for it in eaforum + external if it.get("date")]
    for p in posts.values():
        if p["date_kind"] == "published":
            continue
        match = min((d for t, d in published if same_title(p["title"], t)), default=None)
        if match:
            p["date"], p["date_kind"] = match, "published"

    return posts, external


def fetch_eaforum_posts():
    """Titles and dates of the author's EA Forum posts (public GraphQL API)."""
    query = ('{ posts(input:{terms:{view:"userPosts", userId:"%s", limit:300}}) '
             '{ results { title postedAt } } }' % CONFIG["eaforum_user_id"])
    req = urllib.request.Request(
        "https://forum.effectivealtruism.org/graphql", data=json.dumps({"query": query}).encode(),
        headers={**UA, "Content-Type": "application/json"})
    rows = json.load(urllib.request.urlopen(req, timeout=40))["data"]["posts"]["results"]
    return sorted(({"title": r["title"], "date": r["postedAt"][:10]} for r in rows if r.get("postedAt")),
                  key=lambda r: r["date"])


def same_title(a, b):
    na, nb = (re.sub(r"[^a-z0-9]", "", x.lower()) for x in (a, b))
    if min(len(na), len(nb)) < 10:
        return na == nb
    return na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9


# ---------------------------------------------------------------- images

def optimize_image_bytes(data, ext):
    """Downscale/recompress one image; returns (bytes, ext).

    Screenshots (opaque PNGs) and still images recompress to WebP at a
    fraction of the size. Falls back to the original bytes on any failure
    or when Pillow is unavailable.
    """
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if getattr(im, "is_animated", False):
            return data, ext  # transcoding animations is slow; they lazy-load instead
        buf = io.BytesIO()
        w, h = im.size
        if w > 1400:
            im = im.resize((1400, max(1, round(h * 1400 / w))), Image.LANCZOS)
        if im.mode == "RGBA" and im.getchannel("A").getextrema()[0] >= 250:
            im = im.convert("RGB")
        if im.mode in ("RGBA", "LA", "P"):
            im.save(buf, "PNG", optimize=True)
            out_ext = "png"
            if buf.tell() > 400_000:  # big transparent screenshot: try palette mode
                qbuf = io.BytesIO()
                im.quantize(256).save(qbuf, "PNG", optimize=True)
                if qbuf.tell() < buf.tell() * 0.6:
                    buf = qbuf
        else:
            im.convert("RGB").save(buf, "WEBP", quality=80)
            out_ext = "webp"
        out = buf.getvalue()
        if len(out) < len(data):
            return out, out_ext
    except Exception:  # noqa: BLE001 - keep original on any decode issue
        pass
    return data, ext


DATA_URI_RE = re.compile(r'src="data:image/(png|jpe?g|gif|webp|svg\+xml);base64,([A-Za-z0-9+/=]+)"')


def externalize_images(html_str, prefix):
    """Move base64-inlined images out to dist/img/<hash> files (deduped)."""
    img_dir = os.path.join(DIST, "img")
    os.makedirs(img_dir, exist_ok=True)

    def repl(m):
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(m.group(1), m.group(1))
        try:
            data = base64.b64decode(m.group(2))
        except Exception:  # noqa: BLE001
            return m.group(0)
        stem = hashlib.sha1(data).hexdigest()[:16]
        if ext != "svg":
            data, ext = optimize_image_bytes(data, ext)
        name = f"{stem}.{ext}"
        path = os.path.join(img_dir, name)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
        return f'src="{prefix}img/{name}"'

    return DATA_URI_RE.sub(repl, html_str)


# ---------------------------------------------------------------- social cards

INK, ACCENT, MUTED = "#0f1115", "#1f3fe0", "#9aa3b2"


def _font(size, weight=700):
    from PIL import ImageFont
    path = os.path.join(FONT_DIR, f"Inter-{weight}.ttf")
    return ImageFont.truetype(path, size) if os.path.exists(path) else None


def make_og_image(title=None, out_name="og.png"):
    """1200x630 social card; per-post cards render the post title.

    Returns the card's site-relative path, or None if Pillow/fonts missing.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    import textwrap
    small = _font(30, 500)
    if not small:
        return None
    im = Image.new("RGB", (1200, 630), INK)
    d = ImageDraw.Draw(im)
    d.rectangle([80, 84, 108, 112], fill=ACCENT)
    d.text((126, 80), AUTHOR, font=small, fill=MUTED)
    if title:
        lines = textwrap.wrap(title, width=26)[:4]
        size = 72 if len(lines) <= 2 else (62 if len(lines) == 3 else 54)
        font, y = _font(size), 210
        for line in lines:
            d.text((80, y), line, font=font, fill="#ffffff")
            y += int(size * 1.18)
    else:
        d.text((80, 230), AUTHOR, font=_font(84), fill="#ffffff")
        d.text((82, 350), "AI governance · economic growth · effective philanthropy",
               font=_font(34, 500), fill=MUTED)
    d.text((80, 536), "hfh.pw", font=small, fill=MUTED)
    path = os.path.join(DIST, out_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, "PNG", optimize=True)
    return out_name


def make_touch_icon():
    """180x180 apple-touch-icon matching the favicon. No-op without Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    font = _font(112)
    if not font:
        return
    im = Image.new("RGB", (180, 180), ACCENT)
    ImageDraw.Draw(im).text((90, 88), "H", font=font, fill="#ffffff", anchor="mm")
    im.save(os.path.join(DIST, "apple-touch-icon.png"), "PNG", optimize=True)


# ---------------------------------------------------------------- page parts

def month_year(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %Y")
    except Exception:  # noqa: BLE001
        return ""


def nav_html(root):
    home = root or "./"
    out = []
    for link in CONFIG["nav_links"]:
        if "href" in link:
            href = link["href"]
            href = home + href if href.startswith("#") else root + href
            out.append(f'<a href="{esc(href)}">{esc(link["label"])}</a>')
        else:
            out.append(f'<a href="{esc(link["url"])}" rel="me noopener">{esc(link["label"])}'
                       f'<span class="ext" aria-hidden="true">↗</span></a>')
    return "\n      ".join(out)


def head_common(root):
    return render(template("_head.html"), ROOT=root)


def header_html(root):
    return render(template("_header.html"), ROOT=root, HOME=root or "./", NAV=nav_html(root))


def footer_html(root):
    return render(template("_footer.html"), ROOT=root,
                  UPDATED=datetime.now(timezone.utc).strftime("%-d %B %Y"))


def build_rss(all_items, site):
    items_xml = []
    for it in all_items[:60]:
        if not it["date"] or it.get("date_kind") == "updated":
            continue
        url = it["url"] if it["external"] else f"{BASE_URL}/{it['url']}"
        items_xml.append(
            f"  <item>\n    <title>{esc(it['title'])}</title>\n"
            f"    <link>{esc(url)}</link>\n    <guid isPermaLink=\"false\">{esc(url)}</guid>\n"
            f"    <pubDate>{datetime.strptime(it['date'], '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT')}</pubDate>\n"
            f"    <description>{esc(it.get('excerpt') or '')}</description>\n  </item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0"><channel>\n'
            f"  <title>{esc(site['title'])}</title>\n"
            f"  <link>{BASE_URL}/</link>\n"
            f"  <description>{esc(site['description'])}</description>\n"
            + "\n".join(items_xml) + "\n</channel></rss>\n")


def article_page(post, export_html, root, canonical, live_link):
    art = to_article(export_html, [post["title"], post.get("tab_title")], author=AUTHOR)
    body = externalize_images(art["body"], root)
    meta = [month_year(post["date"])]
    if post.get("inkhaven"):
        meta.append("Inkhaven")
    if post.get("folder"):
        meta.append(post["folder"])
    actions = [f'<a class="btn primary" href="{esc(doc_open_url(post))}" target="_blank" '
               f'rel="noopener">Open in Google Docs <span aria-hidden="true">↗</span></a>']
    if live_link:
        actions.append(f'<a class="btn" href="{esc(live_link)}">Live view</a>')
    title = art["title"] or post["title"]
    subtitle = f'<p class="dek">{esc(art["subtitle"])}</p>' if art["subtitle"] else ""
    return render(template("article.html"),
                  HEAD=head_common(root), HEADER=header_html(root), FOOTER=footer_html(root),
                  ROOT=root, TITLE=esc(title), SITE_TITLE=esc(CONFIG["site"]["title"]),
                  DESCRIPTION=esc(post.get("excerpt") or CONFIG["site"]["description"]),
                  CANONICAL=canonical, OG_IMAGE=f"{BASE_URL}/{post['og']}",
                  JSONLD=post["jsonld"], META=" · ".join(m for m in meta if m),
                  SUBTITLE=subtitle, ACTIONS="\n        ".join(actions), CONTENT=body)


def frame_page(post, reader_href):
    embed = doc_embed_url(post)
    reader = (f'<a class="btn" href="{esc(reader_href)}">Article view</a>' if reader_href else "")
    return render(template("post.html"),
                  HEAD=head_common(""), TITLE=esc(post["title"]),
                  SITE_TITLE=esc(CONFIG["site"]["title"]),
                  DESCRIPTION=esc(post.get("excerpt") or CONFIG["site"]["description"]),
                  CANONICAL=f"{BASE_URL}/{post['slug']}", DATE=month_year(post.get("date", "")),
                  EMBED_URL=embed, FRAME_CLASS="doc-frame pub" if "/pub?" in embed else "doc-frame",
                  DOC_URL=esc(doc_open_url(post)), READER_LINK=reader,
                  OG_IMAGE=f"{BASE_URL}/{post.get('og', 'og.png')}", JSONLD=post.get("jsonld", ""))


# ---------------------------------------------------------------- build

def build():
    posts, external = collect_posts()

    print(f"Probing {len(posts)} Google Docs…")
    with ThreadPoolExecutor(max_workers=12) as ex:
        probes = {s: ex.submit(probe_doc, p) for s, p in posts.items() if p["kind"] == "doc"}
        for slug, fut in probes.items():
            r = fut.result()
            p = posts[slug]
            p.update(published=r["published"], first_tab=r["first_tab"],
                     tab_count=r["tab_count"], tab_title=r["tab_title"])
            p["_export"] = r["export_html"]
            report["docs"][slug] = {"published": r["published"] or p["doc_id"] in PUBLISHED_LINKS,
                                    "has_export": bool(r["export_html"]),
                                    "tabs": r["tab_count"], "restricted": r["restricted"]}

    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(os.path.join(DIST, "reader"))
    for f in os.listdir(os.path.join(ROOT, "static")):
        shutil.copy(os.path.join(ROOT, "static", f), DIST)

    site = CONFIG["site"]

    # ---- post pages
    n_articles = n_frames = 0
    for post in posts.values():
        export_html = post.pop("_export", None)
        post["has_article"] = bool(export_html)
        if export_html:
            post["excerpt"] = excerpt_from_export(export_html, post["title"])
            post["search_text"] = text_from_export(export_html).lower()
        post["og"] = make_og_image(post["title"], f"og/{post['slug']}.png") or "og.png"
        post["jsonld"] = ('<script type="application/ld+json">' + json.dumps({
            "@context": "https://schema.org", "@type": "Article",
            "headline": post["title"], "datePublished": post["date"],
            "author": {"@type": "Person", "name": AUTHOR},
            "mainEntityOfPage": f"{BASE_URL}/{post['slug']}"}) + "</script>")
        canonical = f"{BASE_URL}/{post['slug']}"
        top = os.path.join(DIST, f"{post['slug']}.html")
        reader = os.path.join(DIST, "reader", f"{post['slug']}.html")
        if export_html and post.get("tab_count", 1) > 1:
            open(top, "w").write(article_page(post, export_html, "", canonical, None))
            open(reader, "w").write(
                f'<!doctype html><meta charset="utf-8"><link rel="canonical" href="{canonical}">'
                f'<meta http-equiv="refresh" content="0;url=../{post["slug"]}">')
            n_articles += 1
        else:
            open(top, "w").write(frame_page(post, f"reader/{post['slug']}" if export_html else None))
            n_frames += 1
            if export_html:
                open(reader, "w").write(
                    article_page(post, export_html, "../", canonical, f"../{post['slug']}"))
    print(f"  {n_articles} multi-tab docs rendered as articles, {n_frames} live embeds")

    # ---- CV page (live embed)
    cv = {"slug": "cv", "title": "CV", "doc_id": CONFIG["cv_doc_id"], "kind": "doc",
          "date": "", "og": "og.png"}
    open(os.path.join(DIST, "cv.html"), "w").write(frame_page(cv, None))

    # ---- homepage
    all_items = [{
        "title": p["title"], "url": p["slug"], "date": p["date"], "date_kind": p["date_kind"],
        "source": p["source"], "excerpt": p.get("excerpt", ""), "inkhaven": p.get("inkhaven", False),
        "folder": p.get("folder"), "external": False} for p in posts.values()]

    def norm_title(t):
        return re.sub(r"[^a-z0-9]", "", t.lower())

    essay_titles = {norm_title(p["title"]) for p in posts.values()}
    for it in external:
        if norm_title(it["title"]) in essay_titles:
            continue  # cross-post of an essay; the doc version wins
        all_items.append({**it, "external": True, "date_kind": "published",
                          "inkhaven": False, "folder": None})
    all_items.sort(key=lambda x: x["date"] or "0000", reverse=True)

    selected = []
    for entry in CONFIG.get("featured", []):
        if isinstance(entry, str):  # a post slug
            p = posts.get(entry)
            if not p:
                warn(f"featured slug '{entry}' not found")
                continue
            href, title, ext = entry, p["title"], ""
            note = " · ".join(x for x in [month_year(p["date"]), "Inkhaven" if p.get("inkhaven") else ""] if x)
        else:  # external item: {title, url, excerpt}
            href, title, note = entry["url"], entry["title"], entry.get("excerpt", "")
            ext = ' target="_blank" rel="noopener"'
        arrow = '<span class="ext" aria-hidden="true">↗</span>' if ext else ""
        selected.append(f'<li><a href="{esc(href)}"{ext}>{esc(title)}{arrow}</a>'
                        f'<span class="note">{esc(note)}</span></li>')

    rows, last_year = [], None
    for it in all_items:
        year = (it["date"] or "")[:4]
        if year and year != last_year:
            rows.append(f'<li class="year" aria-hidden="true">{year}</li>')
            last_year = year
        try:
            d = datetime.strptime(it["date"], "%Y-%m-%d")
            when = d.strftime("%b") if it["date_kind"] == "updated" else d.strftime("%b %-d")
        except Exception:  # noqa: BLE001
            when = ""
        tags = []
        if it["external"]:
            label = {"substack": "Substack", "note": "Notes"}[it["source"]]
            tags.append(f'<span class="tag">{label}<span aria-hidden="true"> ↗</span></span>')
        if it["inkhaven"]:
            tags.append('<span class="tag" title="Written during or after the Inkhaven residency">Inkhaven</span>')
        if it["folder"]:
            tags.append(f'<span class="tag">{esc(it["folder"])}</span>')
        if it["date_kind"] == "updated":
            tags.append('<span class="tag" title="Date of last edit">updated</span>')
        ext = ' target="_blank" rel="noopener"' if it["external"] else ""
        slug_attr = f' data-slug="{esc(it["url"])}"' if not it["external"] else ""
        rows.append(
            f'<li class="row" data-source="{it["source"]}" data-title="{esc(it["title"].lower())}"{slug_attr}>'
            f'<time datetime="{it["date"] or ""}">{when}</time>'
            f'<a href="{esc(it["url"])}"{ext}>{esc(it["title"])}</a>'
            f'<span class="tags">{"".join(tags)}</span></li>')

    projects = "".join(
        f'<li><a href="{esc(p["url"])}" target="_blank" rel="noopener">{esc(p["title"])}'
        f'<span class="ext" aria-hidden="true">↗</span></a><p>{esc(p["description"])}</p></li>'
        for p in CONFIG["projects"])

    counts = {k: sum(1 for i in all_items if k == "all" or i["source"] == k)
              for k in ("all", "essay", "substack", "note")}
    index = render(template("index.html"),
                   HEAD=head_common(""), HEADER=header_html(""), FOOTER=footer_html(""),
                   SITE_TITLE=esc(site["title"]), DESCRIPTION=esc(site["description"]),
                   CANONICAL=BASE_URL + "/", OG_IMAGE=f"{BASE_URL}/og.png",
                   NAME=esc(AUTHOR), BIO=CONFIG["bio"], SELECTED="\n".join(selected),
                   POSTS="\n".join(rows), PROJECTS=projects,
                   COUNT_ALL=str(counts["all"]), COUNT_ESSAY=str(counts["essay"]),
                   COUNT_SUBSTACK=str(counts["substack"]), COUNT_NOTE=str(counts["note"]))
    open(os.path.join(DIST, "index.html"), "w").write(index)

    # ---- 404, blog redirect, robots, sitemap, feeds, indexes
    open(os.path.join(DIST, "404.html"), "w").write(render(
        template("404.html"), HEAD=head_common(BASE_URL + "/"),
        HEADER=header_html(BASE_URL + "/"), FOOTER=footer_html(BASE_URL + "/"), BASE=BASE_URL))
    open(os.path.join(DIST, "blog.html"), "w").write(
        f'<!doctype html><meta http-equiv="refresh" content="0;url={BASE_URL}/">')
    open(os.path.join(DIST, "robots.txt"), "w").write(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    urls = [(f"{BASE_URL}/", None), (f"{BASE_URL}/cv", None)]
    for p in posts.values():
        urls.append((f"{BASE_URL}/{p['slug']}", p["date"]))
        if p.get("has_article") and p.get("tab_count", 1) == 1:
            urls.append((f"{BASE_URL}/reader/{p['slug']}", p["date"]))
    open(os.path.join(DIST, "sitemap.xml"), "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"  <url><loc>{esc(u)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "")
                    + "</url>" for u, d in urls) + "\n</urlset>\n")
    open(os.path.join(DIST, "feed.xml"), "w").write(build_rss(all_items, site))
    json.dump([{"slug": p["slug"], "title": p["title"]} for p in posts.values()],
              open(os.path.join(DIST, "posts.json"), "w"))
    json.dump({p["slug"]: p.get("search_text", "") for p in posts.values()},
              open(os.path.join(DIST, "search.json"), "w"))
    make_og_image()
    make_touch_icon()
    json.dump(report, open(os.path.join(DIST, "build_report.json"), "w"), indent=1)

    # Sanity gate: refuse to ship an obviously broken build (Pages then keeps
    # serving the previous deploy).
    essays = sum(1 for i in all_items if i["source"] == "essay")
    articles = sum(1 for p in posts.values() if p.get("has_article"))
    problems = []
    if essays < 40:
        problems.append(f"only {essays} essays (expected >=40)")
    if len(all_items) < 60:
        problems.append(f"only {len(all_items)} posts (expected >=60)")
    if articles < 40:
        problems.append(f"only {articles} docs exported (expected >=40)")
    if os.path.getsize(os.path.join(DIST, "index.html")) < 20_000:
        problems.append("index.html suspiciously small")
    if problems:
        print("BUILD REJECTED: " + "; ".join(problems), file=sys.stderr)
        sys.exit(1)

    pub = sum(1 for d in report["docs"].values() if d["published"])
    print(f"Done: {len(all_items)} posts ({pub}/{len(report['docs'])} docs published-to-web), "
          f"{len(report['warnings'])} warnings -> dist/")


if __name__ == "__main__":
    build()
