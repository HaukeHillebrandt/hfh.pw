# hfh.pw

Personal site of Hauke Hillebrandt, built as a static site around **live Google Docs**.

## How it works

- `build.py` (stdlib-only Python) fetches, at build time:
  - the public Drive folder listing (`embeddedfolderview`) — every doc you drag into the
    [Drive folder](https://drive.google.com/drive/folders/1fh8-FbNMdqp6ULXb_QnzhXeauPzVjzls)
    becomes a post automatically
  - Substack RSS, Bearblog RSS (Substack blocks GitHub's servers, so the Action uses
    the committed copy in `data/cache/substack.json`; see "Local refreshes" below)
- **Only a doc's first tab is shown.** Put notes, drafts and appendices in later tabs:
  they stay reachable via "Open in Google Docs" but never appear on the site
  (pages, excerpts or search).
  - `/<slug>` is a typeset article of the first tab, exported at build time, so edits
    show on the next rebuild. `/reader/<slug>` redirects to it.
  - Files without an HTML export (Word/PDF, restricted docs) embed the live file
    instead (`/pub?embedded=true` when published-to-web, else `/preview`).
- Rendering (`docrender.py`) strips private comment threads, Google's inserted tables
  of contents and "Listen to this tab" artefacts, and promotes the doc's own title.
  The same file is copied into the `ai-ownership` repo; keep the two in sync.
- Dates: posts from the old Google Site / Inkhaven feed keep their publication dates;
  other docs use their EA Forum or Substack publication date if a title matches,
  else their creation date (from `data/doc_meta.json`).
- `data/slugs_harvest.json` maps the old Google-Sites URLs (e.g. `/ClaudeMaxxing`,
  `/ai-biases`) to their doc IDs, so **all existing links keep working** after the
  domain points here. Fix or add slugs via `slug_overrides` in `config.json`.
- GitHub Actions rebuilds and deploys every 3 hours, on every push, and on demand
  (Actions → "Build and deploy site" → Run workflow, or
  `gh workflow run deploy.yml -R HaukeHillebrandt/hfh.pw`).

## Local refreshes

Two things only update from your own machine, because Substack returns 403 to GitHub's
servers (both the RSS feed and its JSON API) and the Action has no Google credentials:

- **New Substack posts:** run a local build and push the refreshed feed cache:

  ```sh
  python3 build.py && git add data/cache && git commit -m "Refresh feed cache" && git push
  ```

- **Drive metadata:** after publishing docs to the web or adding new ones, refresh it
  with the authenticated `gws` CLI (read-only), then commit and push:

  ```sh
  python3 tools/discover_published.py   # writes data/published_links.json + data/doc_meta.json
  ```

`tools/publish_to_web.py --apply` publishes every unpublished site doc to the web
(it changes publication state, so only run it deliberately).

## Local build

```sh
python3 build.py          # writes the site to dist/
cd dist && python3 -m http.server 8899
# note: local server needs .html extensions; GitHub Pages resolves /slug -> slug.html
```

## Domains

- The site lives at **https://hauke.uk** (GitHub Pages custom domain, `static/CNAME`,
  `base_url` in `config.json`). DNS is on Cloudflare: the four GitHub Pages A/AAAA
  records on the apex and `www` → CNAME → `haukehillebrandt.github.io`, all DNS only.
- **hfh.pw** and **www.hfh.pw** forward to hauke.uk with a 301 that keeps the path
  (Squarespace domain forwarding), so old links such as `hfh.pw/ClaudeMaxxing` still work.
  The Mailgun email forwarding records on hfh.pw are unchanged.

## Publishing notes

- Docs that are **published to the web** (File → Share → Publish to web, with
  "automatically republish" on) get the cleaner `pub` embed when they fall back to a
  live embed; unpublished but link-shared docs use the paginated `preview` embed.
  `dist/build_report.json` lists each doc's publication state and tab count.
- Word/PDF files in the Drive folder work too (embedded via the Drive file previewer).
- Design: white / near-black, Inter (fonts in `assets/fonts` for the social cards,
  SIL OFL), one cobalt accent (`#1f3fe0`, `#8ea2ff` in dark mode). Tokens live at the
  top of `static/style.css`.
