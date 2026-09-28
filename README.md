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
  - **Single-tab docs:** `/<slug>` embeds the live doc (`/pub?embedded=true` when
    published-to-web, else `/preview`), so edits show within minutes;
    `/reader/<slug>` is a typeset article of the same doc.
  - **Multi-tab docs:** `/<slug>` is a typeset article of the first tab (Google's live
    views can't be limited to one tab neatly), so edits show on the next rebuild.
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

## Pointing www.hfh.pw here (when ready)

1. In this repo: add a file `static/CNAME` containing exactly `www.hfh.pw`, commit, push.
2. On GitHub: repo → Settings → Pages → Custom domain → `www.hfh.pw`, save.
   Enable "Enforce HTTPS" once the certificate is issued.
3. At your DNS provider for `hfh.pw`:
   - `www` → CNAME → `haukehillebrandt.github.io`
   - apex `hfh.pw` → A records → `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153` (or ALIAS to `haukehillebrandt.github.io`)
4. Update `base_url` in `config.json` to `https://www.hfh.pw` and push.
5. Unpublish the Google Site once the new site is live on the domain.

## Publishing notes

- Single-tab docs that are **published to the web** (File → Share → Publish to web,
  with "automatically republish" on) get the cleaner `pub` embed; unpublished but
  link-shared docs fall back to the paginated `preview` embed.
  `dist/build_report.json` lists each doc's publication state and tab count.
- Word/PDF files in the Drive folder work too (embedded via the Drive file previewer).
- Design: white / near-black, Inter (fonts in `assets/fonts` for the social cards,
  SIL OFL), one cobalt accent (`#1f3fe0`, `#8ea2ff` in dark mode). Tokens live at the
  top of `static/style.css`.
