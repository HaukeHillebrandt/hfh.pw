#!/usr/bin/env python3
"""Static site builder for hfh.pw.

Fetches content from Google Drive (public folder listing), Substack,
Bearblog and the Inkhaven feed, then renders a static site into dist/.

Google Docs are served two ways:
  - live view  : /<slug>          -> iframe of the live doc (pub or preview endpoint)
  - reader view: /reader/<slug>   -> doc HTML exported at build time (fast, indexable)

Stdlib only; no dependencies.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import html as htmllib
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
UA = {"User-Agent": "Mozilla/5.0 (compatible; hfh-pw-builder; +https://github.com/HaukeHillebrandt/hfh.pw)"}

CONFIG = json.load(open(os.path.join(ROOT, "config.json")))
SLUG_HARVEST = json.load(open(os.path.join(ROOT, "data", "slugs_harvest.json")))
BASE_URL = os.environ.get("SITE_BASE", CONFIG["site"]["base_url"]).rstrip("/")

report = {"built_at": datetime.now(timezone.utc).isoformat(), "warnings": [], "docs": {}}


def warn(msg):
    report["warnings"].append(msg)
    print(f"  [warn] {msg}", file=sys.stderr)


def fetch(url, timeout=30, retries=2, binary=False):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            data = urllib.request.urlopen(req, timeout=timeout).read()
            return data if binary else data.decode("utf-8", "replace")
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


# ---------------------------------------------------------------- doc probing

def probe_doc(doc):
    """Decide embed endpoint for a Google Doc and fetch its exported HTML."""
    did = doc["doc_id"]
    result = {"published": False, "export_html": None, "restricted": False}
    try:
        body = fetch(f"https://docs.google.com/document/d/{did}/pub", retries=1)
        if 'id="contents"' in body or "doc-content" in body:
            result["published"] = True
    except Exception:  # noqa: BLE001 - unpublished/restricted docs 401 here
        pass
    try:
        result["export_html"] = fetch(
            f"https://docs.google.com/document/d/{did}/export?format=html", retries=1)
    except Exception as e:  # noqa: BLE001
        warn(f"no HTML export for '{doc['title']}' ({did}): {e}")
    if not result["published"] and not result["export_html"]:
        # Anonymous visitors would hit a Google login wall in the iframe.
        try:
            prev = fetch(f"https://docs.google.com/document/d/{did}/preview", retries=0)
            result["restricted"] = "ServiceLogin" in prev or "accounts.google.com/v3/signin" in prev
        except Exception:  # noqa: BLE001
            result["restricted"] = True
        if result["restricted"]:
            warn(f"RESTRICTED doc (login wall for visitors): '{doc['title']}' ({did}) "
                 f"- share it as 'anyone with the link' to fix")
    return result


def doc_embed_url(doc):
    if doc.get("kind") == "file":
        return f"https://drive.google.com/file/d/{doc['doc_id']}/preview"
    if doc.get("published"):
        return f"https://docs.google.com/document/d/{doc['doc_id']}/pub?embedded=true"
    return f"https://docs.google.com/document/d/{doc['doc_id']}/preview"


def doc_open_url(doc):
    if doc.get("kind") == "file":
        return f"https://drive.google.com/file/d/{doc['doc_id']}/view"
    return f"https://docs.google.com/document/d/{doc['doc_id']}/edit"


def excerpt_from_export(export_html, title):
    if not export_html:
        return ""
    m = re.search(r"<body[^>]*>(.*)</body>", export_html, re.S)
    if not m:
        return ""
    text = re.sub(r"<[^>]+>", " ", m.group(1))
    text = htmllib.unescape(re.sub(r"\s+", " ", text)).strip()
    if text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" :—–-")
    words = text.split(" ")
    return " ".join(words[:36]) + ("…" if len(words) > 36 else "")


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

    # 1. Harvested slugs from the old Google Site (canonical URLs + real dates),
    #    plus manual fixes from config.
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
            "date_exact": True, "doc_id": doc_id, "kind": kind,
            "source": "essay", "inkhaven": info["date"] >= "2025-11-01",
        }

    # 2. Drive-folder docs not already covered by a harvested slug
    for did, e in drive_docs.items():
        if did in used_doc_ids:
            continue
        if e["title"] in CONFIG["exclude_titles"] or did == CONFIG["cv_doc_id"]:
            continue
        kind = "doc" if "/document/" in e["url"] else "file"
        slug = slugify(e["title"])
        while slug in posts:
            slug += "-2"
        posts[slug] = {
            "slug": slug, "title": e["title"],
            "date": parse_mdy(e["modified"]) or "2020-01-01", "date_exact": False,
            "doc_id": did, "kind": kind, "source": "essay",
            "folder": e.get("folder"), "inkhaven": False,
        }

    # 3. External posts
    print("Fetching feeds…")
    external = []
    try:
        for it in cached_json("substack",
                              lambda: parse_rss(fetch(CONFIG["feeds"]["substack"]))):
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

    return posts, external


# ---------------------------------------------------------------- reader pages

def optimize_image_bytes(data, ext):
    """Downscale/recompress one image; returns (bytes, ext).

    Screenshots (opaque PNGs) and animated GIFs recompress to WebP at a
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


def _og_font(size):
    from PIL import ImageFont
    for f in ["/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
              "/System/Library/Fonts/Supplemental/Georgia.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return None


def make_og_image(title=None, out_name="og.png"):
    """Branded social card; per-post cards render the post title.

    Returns the card's site-relative path, or None if Pillow/fonts missing.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    import textwrap
    small = _og_font(34)
    if not small:
        return None
    im = Image.new("RGB", (1200, 630), "#1d2b45")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1200, 8], fill="#8db4e8")
    if title:
        lines = textwrap.wrap(title, width=30)[:4]
        font = _og_font(64 if len(lines) <= 3 else 56)
        y = 315 - 42 * len(lines)
        for line in lines:
            d.text((80, y), line, font=font, fill="#faf9f6")
            y += 84
        d.text((84, 520), "Hauke Hillebrandt", font=small, fill="#9fb3d1")
    else:
        d.text((80, 240), "Hauke Hillebrandt", font=_og_font(78), fill="#faf9f6")
        d.text((84, 350), "Essays on AI, economic growth, and global priorities",
               font=small, fill="#9fb3d1")
    path = os.path.join(DIST, out_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, "PNG", optimize=True)
    return out_name


def build_rss(all_items, site):
    items_xml = []
    for it in all_items[:60]:
        if not it["date"] or not it["date_exact"]:
            continue
        url = it["url"] if it["external"] else f"{BASE_URL}/{it['url']}"
        desc = esc(it.get("excerpt") or "")
        items_xml.append(
            f"  <item>\n    <title>{esc(it['title'])}</title>\n"
            f"    <link>{esc(url)}</link>\n    <guid isPermaLink=\"false\">{esc(url)}</guid>\n"
            f"    <pubDate>{datetime.strptime(it['date'], '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 GMT')}</pubDate>\n"
            f"    <description>{desc}</description>\n  </item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0"><channel>\n'
            f"  <title>{esc(site['title'])}</title>\n"
            f"  <link>{BASE_URL}/</link>\n"
            f"  <description>{esc(site['description'])}</description>\n"
            + "\n".join(items_xml) + "\n</channel></rss>\n")


DATA_URI_RE = re.compile(r'src="data:image/(png|jpe?g|gif|webp|svg\+xml);base64,([A-Za-z0-9+/=]+)"')


def externalize_images(html_str):
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
        return f'src="../img/{name}"'

    return DATA_URI_RE.sub(repl, html_str)


READER_STYLE_OVERRIDES = """
<style>
  body { max-width: 760px !important; margin: 0 auto !important;
         padding: 96px 24px 64px !important; }
  img { max-width: 100% !important; height: auto !important; }
  table { max-width: 100%; }
</style>
"""


def build_reader_page(post, export_html):
    """Standalone reader page: Google's exported HTML + injected site header."""
    bar = render(template("readerbar.html"),
                 TITLE=esc(post["title"]),
                 LIVE_URL=f"../{post['slug']}",
                 DOC_URL=doc_open_url(post))
    out = externalize_images(export_html)
    out = out.replace("<img ", '<img loading="lazy" decoding="async" ')
    canonical = f'<link rel="canonical" href="{BASE_URL}/{post["slug"]}">\n'
    if "</head>" in out:
        out = out.replace("</head>", canonical + "</head>", 1)
    if "</head>" in out:
        out = out.replace("</head>", READER_STYLE_OVERRIDES + "</head>", 1)
    else:
        out = READER_STYLE_OVERRIDES + out
    m = re.search(r"<body[^>]*>", out)
    if m:
        out = out[:m.end()] + "\n" + bar + out[m.end():]
    else:
        out = bar + out
    return out


# ---------------------------------------------------------------- rendering

def month_year(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %Y")
    except Exception:  # noqa: BLE001
        return ""


def build():
    posts, external = collect_posts()

    print(f"Probing {len(posts)} Google Docs…")
    with ThreadPoolExecutor(max_workers=12) as ex:
        probes = {slug: f for slug, f in
                  ((s, ex.submit(probe_doc, p)) for s, p in posts.items() if p["kind"] == "doc")}
        for slug, fut in probes.items():
            r = fut.result()
            posts[slug]["published"] = r["published"]
            posts[slug]["_export"] = r["export_html"]
            report["docs"][slug] = {"published": r["published"],
                                    "has_export": bool(r["export_html"]),
                                    "restricted": r["restricted"]}

    # ---- output dir
    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    os.makedirs(os.path.join(DIST, "reader"))
    for f in os.listdir(os.path.join(ROOT, "static")):
        shutil.copy(os.path.join(ROOT, "static", f), DIST)

    site = CONFIG["site"]
    nav = " ".join(
        f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in CONFIG["nav_links"])

    # ---- doc pages (live iframe view) + reader pages
    post_tpl = template("post.html")
    for post in posts.values():
        export_html = post.pop("_export", None)
        post["has_reader"] = bool(export_html)
        if export_html:
            post["excerpt"] = excerpt_from_export(export_html, post["title"])
        reader_link = (f'<a class="btn" href="reader/{post["slug"]}">Reader view</a>'
                       if post["has_reader"] else "")
        og_rel = make_og_image(post["title"], f"og/{post['slug']}.png") or "og.png"
        jsonld = json.dumps({
            "@context": "https://schema.org", "@type": "Article",
            "headline": post["title"], "datePublished": post["date"],
            "author": {"@type": "Person", "name": site["author"]},
            "mainEntityOfPage": f"{BASE_URL}/{post['slug']}",
        })
        page = render(post_tpl,
                      SITE_TITLE=esc(site["title"]),
                      TITLE=esc(post["title"]),
                      DESCRIPTION=esc(post.get("excerpt") or site["description"]),
                      CANONICAL=f"{BASE_URL}/{post['slug']}",
                      DATE=month_year(post["date"]),
                      EMBED_URL=doc_embed_url(post),
                      DOC_URL=doc_open_url(post),
                      READER_LINK=reader_link,
                      OG_IMAGE=f"{BASE_URL}/{og_rel}",
                      JSONLD=f'<script type="application/ld+json">{jsonld}</script>',
                      NAV=nav)
        open(os.path.join(DIST, f"{post['slug']}.html"), "w").write(page)
        if export_html:
            open(os.path.join(DIST, "reader", f"{post['slug']}.html"), "w").write(
                build_reader_page(post, export_html))

    # ---- CV page
    cv = {"slug": "cv", "title": "CV — Hauke Hillebrandt", "doc_id": CONFIG["cv_doc_id"],
          "kind": "doc", "published": False}
    page = render(post_tpl,
                  SITE_TITLE=esc(site["title"]), TITLE="CV",
                  DESCRIPTION=esc(site["description"]),
                  CANONICAL=f"{BASE_URL}/cv", DATE="",
                  EMBED_URL=doc_embed_url(cv), DOC_URL=doc_open_url(cv),
                  READER_LINK="", OG_IMAGE=f"{BASE_URL}/og.png", JSONLD="", NAV=nav)
    open(os.path.join(DIST, "cv.html"), "w").write(page)

    # ---- homepage
    all_items = []
    for p in posts.values():
        all_items.append({
            "title": p["title"], "url": p["slug"], "date": p["date"],
            "date_exact": p["date_exact"], "source": p["source"],
            "excerpt": p.get("excerpt", ""), "inkhaven": p.get("inkhaven", False),
            "folder": p.get("folder"), "external": False,
        })
    for it in external:
        all_items.append({**it, "external": True, "date_exact": True,
                          "inkhaven": False, "folder": None})
    all_items.sort(key=lambda x: x["date"] or "0000", reverse=True)

    rows = []
    for it in all_items:
        badge = {"essay": "essay", "substack": "substack", "note": "note"}[it["source"]]
        extra = ' <span class="badge inkhaven" title="Written during the Inkhaven residency">inkhaven</span>' if it["inkhaven"] else ""
        if it["folder"]:
            extra += f' <span class="badge">{esc(it["folder"].lower())}</span>'
        ext = ' target="_blank" rel="noopener"' if it["external"] else ""
        date_disp = month_year(it["date"]) if it["date"] else ""
        if not it["date_exact"]:
            date_disp = f'<span title="Last modified date">upd. {date_disp}</span>'
        excerpt = f'<p class="excerpt">{esc(it["excerpt"])}</p>' if it.get("excerpt") else ""
        rows.append(
            f'<li class="post" data-source="{badge}" data-title="{esc(it["title"].lower())}">'
            f'<a class="post-link" href="{esc(it["url"])}"{ext}>'
            f'<span class="post-title">{esc(it["title"])}</span>'
            f'<span class="post-meta"><span class="badge {badge}">{badge}</span>{extra}'
            f'<time>{date_disp}</time></span></a>{excerpt}</li>')

    proj_cards = "".join(
        f'<a class="card" href="{esc(p["url"])}" target="_blank" rel="noopener">'
        f'<h3>{esc(p["title"])}</h3><p>{esc(p["description"])}</p></a>'
        for p in CONFIG["projects"])

    counts = {"all": len(all_items),
              "essay": sum(1 for i in all_items if i["source"] == "essay"),
              "substack": sum(1 for i in all_items if i["source"] == "substack"),
              "note": sum(1 for i in all_items if i["source"] == "note")}

    index = render(template("index.html"),
                   SITE_TITLE=esc(site["title"]),
                   DESCRIPTION=esc(site["description"]),
                   CANONICAL=BASE_URL + "/",
                   BIO=CONFIG["bio"],
                   NAV=nav,
                   POSTS="\n".join(rows),
                   PROJECTS=proj_cards,
                   COUNT_ALL=str(counts["all"]), COUNT_ESSAY=str(counts["essay"]),
                   COUNT_SUBSTACK=str(counts["substack"]), COUNT_NOTE=str(counts["note"]),
                   UPDATED=datetime.now(timezone.utc).strftime("%d %b %Y"))
    open(os.path.join(DIST, "index.html"), "w").write(index)

    # ---- 404, blog redirect, robots, sitemap
    open(os.path.join(DIST, "404.html"), "w").write(
        render(template("404.html"), SITE_TITLE=esc(site["title"]), BASE=BASE_URL))
    open(os.path.join(DIST, "blog.html"), "w").write(
        f'<!doctype html><meta http-equiv="refresh" content="0;url={BASE_URL}/">')
    open(os.path.join(DIST, "robots.txt"), "w").write(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    urls = [(f"{BASE_URL}/", None), (f"{BASE_URL}/cv", None)]
    for p in posts.values():
        urls.append((f"{BASE_URL}/{p['slug']}", p["date"]))
        if p.get("has_reader"):
            urls.append((f"{BASE_URL}/reader/{p['slug']}", p["date"]))
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n' \
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
              "\n".join(f"  <url><loc>{esc(u)}</loc>"
                        + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>"
                        for u, d in urls) + "\n</urlset>\n"
    open(os.path.join(DIST, "sitemap.xml"), "w").write(sitemap)

    open(os.path.join(DIST, "feed.xml"), "w").write(build_rss(all_items, site))
    json.dump([{"slug": p["slug"], "title": p["title"]} for p in posts.values()],
              open(os.path.join(DIST, "posts.json"), "w"))
    make_og_image()

    json.dump(report, open(os.path.join(DIST, "build_report.json"), "w"), indent=1)
    pub_count = sum(1 for d in report["docs"].values() if d["published"])
    print(f"Done: {len(all_items)} posts ({pub_count}/{len(report['docs'])} docs published-to-web), "
          f"{len(report['warnings'])} warnings -> dist/")


if __name__ == "__main__":
    build()
