"""Google Docs HTML export -> clean, semantic article HTML.

Shared by the hfh.pw and ai-ownership builds (keep the two copies in sync).
Stdlib only.
"""
import difflib
import html as htmllib
import json
import re
import urllib.parse

# ---------------------------------------------------------------- tabs

TAB_CALL_RE = re.compile(r'"(t\.[a-z0-9]+)"\s*,\s*(\d+)\s*,\s*undefined')
TAB_MAIN_RE = re.compile(r'\{"ty":"mkch","d":\[\[1,("(?:[^"\\]|\\.)*")\]\]\}')
TAB_REC_RE = re.compile(
    r'\{"ty":"ac","d":\["(t\.[a-z0-9]+)",\[1,("(?:[^"\\]|\\.)*")\],\[(\d+)\]\]\}')


def parse_tabs(preview_html):
    """Tabs of a Google Doc, read from its public /preview page.

    Returns (first_tab_id, [(tab_id, title, position), ...] sorted by position).
    The page embeds the original tab (t.0) as an "mkch" record and every
    other tab as an "ac" record carrying its position; the tab displayed
    first is passed to the document loader, which also gets the tab count.
    """
    tabs = [(m.group(1), json.loads(m.group(2)), int(m.group(3)))
            for m in TAB_REC_RE.finditer(preview_html)]
    main = TAB_MAIN_RE.search(preview_html)
    if main:
        taken = {pos for _, _, pos in tabs}
        free = next(i for i in range(len(tabs) + 1) if i not in taken)
        tabs.append(("t.0", json.loads(main.group(1)), free))
    call = TAB_CALL_RE.search(preview_html)
    first = call.group(1) if call else "t.0"
    return first, sorted(tabs, key=lambda t: t[2])


# ---------------------------------------------------------------- comments

COMMENT_ANCHOR_RE = re.compile(
    r'(?:<sup>\s*)?<a href="#cmnt\d+" id="cmnt_ref\d+">\[\w+\]</a>(?:\s*</sup>)?')
COMMENT_BODY_RE = re.compile(
    r'<div[^>]*>\s*<p[^>]*>\s*<a href="#cmnt_ref\d+" id="cmnt\d+">.*?</div>', re.S)


def strip_comments(export_html):
    """Drop comment threads: the export includes comments that doc viewers never see."""
    if not export_html:
        return export_html
    return COMMENT_BODY_RE.sub("", COMMENT_ANCHOR_RE.sub("", export_html))


# ---------------------------------------------------------------- helpers

def plain(fragment):
    """Visible text of an HTML fragment, whitespace-normalised."""
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _class_styles(export_html):
    styles = {}
    m = re.search(r"<style[^>]*>(.*?)</style>", export_html, re.S)
    if not m:
        return styles
    for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", m.group(1)):
        selectors, body = rule.group(1), rule.group(2)
        props = {"bold": "font-weight:700" in body or "font-weight:bold" in body,
                 "italic": "font-style:italic" in body,
                 "sup": "vertical-align:super" in body}
        if any(props.values()):
            for sel in selectors.split(","):
                sel = sel.strip()
                if sel.startswith("."):
                    styles[sel[1:]] = props
    return styles


def _unwrap_google_link(url):
    m = re.match(r"https://www\.google\.com/url\?q=([^&]+)", url)
    return urllib.parse.unquote(m.group(1)) if m else url


def _drop_empty_blocks(body):
    body = re.sub(r"<p([^>]*)>(.*?)</p>",
                  lambda m: "" if not plain(m.group(2)) and "<img" not in m.group(2) else m.group(0),
                  body, flags=re.S)
    return re.sub(r"<div>\s*</div>", "", body)


def _same_title(text, hint):
    a, b = _norm(text), _norm(hint)
    if not a or len(b) < 6:
        return False
    return a.startswith(b) or b.startswith(a) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


# ---------------------------------------------------------------- transform

def to_article(export_html, title_hints=(), author=None):
    """Convert one exported tab into article parts.

    Returns {"title", "subtitle", "body", "toc"}: the doc's own title (from
    Google's Title style or a leading title-like block matching a hint),
    its subtitle, the cleaned body HTML and [(level, id, text)] for h1/h2.
    """
    export_html = strip_comments(export_html)
    styles = _class_styles(export_html)
    m = re.search(r"<body[^>]*>(.*)</body>", export_html, re.S)
    body = m.group(1) if m else export_html

    # Google's Title / Subtitle paragraph styles near the top of the tab
    doc_title = subtitle = None
    for style in ("title", "subtitle"):
        sm = re.search(r'<p class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</p>' % style, body, re.S)
        if sm and sm.start() < 4000 and plain(sm.group(1)):
            if style == "title":
                doc_title = plain(sm.group(1))
            else:
                subtitle = plain(sm.group(1))
            body = body[:sm.start()] + body[sm.end():]

    body = re.sub(r'href="(https://www\.google\.com/url\?q=[^"]+)"',
                  lambda mm: 'href="' + htmllib.escape(
                      _unwrap_google_link(htmllib.unescape(mm.group(1))), quote=True) + '"',
                  body)

    def classes(attrs):
        cm = re.search(r'class="([^"]*)"', attrs)
        return cm.group(1).split() if cm else []

    def span_repl(mm):
        attrs, inner = mm.group(1), mm.group(2)
        bold = italic = sup = False
        for c in classes(attrs):
            p = styles.get(c)
            if p:
                bold |= p["bold"]
                italic |= p["italic"]
                sup |= p["sup"]
        if sup:
            return f"<sup>{inner}</sup>"
        if bold and italic:
            return f"<strong><em>{inner}</em></strong>"
        if bold:
            return f"<strong>{inner}</strong>"
        if italic:
            return f"<em>{inner}</em>"
        return inner

    prev = None
    while prev != body:  # spans nest
        prev = body
        body = re.sub(r"<span([^>]*)>((?:(?!</?span).)*)</span>", span_repl, body, flags=re.S)

    def clean_tag(mm):
        tag, attrs = mm.group(1), mm.group(2)
        keep = ""
        for attr in ("id", "href", "src", "alt", "colspan", "rowspan", "start"):
            am = re.search(rf'\b{attr}="([^"]*)"', attrs)
            if am:
                keep += f' {attr}="{am.group(1)}"'
        if tag in ("ul", "ol"):  # keep Google's list nesting level
            lm = re.search(r"lst-kix_[\w]+-(\d)", attrs)
            if lm and lm.group(1) != "0":
                keep += f' class="l{lm.group(1)}"'
        return f"<{tag}{keep}>"

    body = re.sub(r"<(h[1-6]|p|ul|ol|li|a|td|tr|table|img|div|sup)\b([^>]*)>", clean_tag, body)
    body = _drop_empty_blocks(body)

    # headings: readable ids, drop Google's "Listen to this tab" artefacts
    used, id_map, dropped = set(), {}, set()

    def heading(mm):
        level, attrs, inner = mm.group(1), mm.group(2), mm.group(3)
        text = plain(inner)
        old = re.search(r'id="([^"]+)"', attrs)
        if text.lower() in ("", "listen to this tab"):
            if old:
                dropped.add(old.group(1))
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "s"
        base, i = slug, 2
        while slug in used:
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        if old:
            id_map[old.group(1)] = slug
        return f'<h{level} id="{slug}">{inner}</h{level}>'

    body = re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", heading, body, flags=re.S)
    for did in dropped:
        body = re.sub(rf'<a href="#{re.escape(did)}">.*?</a>', "", body, flags=re.S)
    body = re.sub(r'href="#([^"]+)"', lambda mm: f'href="#{id_map.get(mm.group(1), mm.group(1))}"', body)

    # Google's inserted table of contents: paragraphs that are one internal link
    body = re.sub(r'<p>\s*<a href="#[^"]+">[^<]*</a>\s*(?:\d+)?\s*</p>', "", body)
    body = _drop_empty_blocks(body)

    # a leading title-like block that repeats the post title
    fm = re.match(r"\s*(?:<hr[^>]*>\s*)*<(h[1-4]|p)\b[^>]*>(.*?)</\1>", body, re.S)
    if fm:
        text = plain(fm.group(2))
        if len(text) <= 150 and len(text.split()) <= 20 and any(
                _same_title(text, h) for h in title_hints if h):
            doc_title = doc_title or text
            body = body[fm.end():]

    # a leading rule and byline (the page header already names the author)
    if author:
        bm = re.match(r"\s*(?:<hr[^>]*>\s*)*<p>(.*?)</p>", body, re.S)
        if bm and _norm(author) in _norm(plain(bm.group(1))) and len(plain(bm.group(1)).split()) <= 8:
            body = body[bm.end():]
    body = re.sub(r"^\s*(?:<hr[^>]*>\s*)+", "", body)

    # footnotes
    fnm = re.search(r'<div><p><a id="ftnt1"', body)
    if fnm:
        body = (body[:fnm.start()] + '<section class="footnotes"><h2 id="notes">Notes</h2>'
                + body[fnm.start():] + "</section>")

    body = body.replace("<img ", '<img loading="lazy" decoding="async" ')
    toc = [(int(t.group(1)), t.group(2), plain(t.group(3)))
           for t in re.finditer(r'<h([12]) id="([^"]+)">(.*?)</h\1>', body, re.S)]
    return {"title": doc_title, "subtitle": subtitle, "body": body.strip(), "toc": toc}
