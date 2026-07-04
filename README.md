# hfh.pw

Personal site of Hauke Hillebrandt, built as a static site around **live Google Docs**.

## How it works

- `build.py` (stdlib-only Python) fetches, at build time:
  - the public Drive folder listing (`embeddedfolderview`) — every doc you drag into the
    [Drive folder](https://drive.google.com/drive/folders/1fh8-FbNMdqp6ULXb_QnzhXeauPzVjzls)
    becomes a post automatically
  - Substack RSS, Bearblog RSS
- Every Google Doc gets two pages:
  - **`/<slug>`** — live view: an iframe of the doc itself
    (`/pub?embedded=true` when the doc is published-to-web, else `/preview`).
    Edits to the doc show up immediately (published docs republish within ~5 min).
    An **Open in Google Docs** button sits top-right, like on Google Sites.
  - **`/reader/<slug>`** — reader view: the doc's HTML exported at build time.
    Fast, indexable by search engines, refreshed on every rebuild.
- `data/slugs_harvest.json` maps the old Google-Sites URLs (e.g. `/ClaudeMaxxing`,
  `/ai-biases`) to their doc IDs, so **all existing links keep working** after the
  domain points here. Fix or add slugs via `slug_overrides` in `config.json`.
- GitHub Actions rebuilds and deploys daily (05:23 UTC), on every push, and on demand
  (Actions → "Build and deploy site" → Run workflow).

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

- Docs that are **published to the web** (File → Share → Publish to web, with
  "automatically republish" on) get the cleaner `pub` embed; unpublished but
  link-shared docs fall back to the paginated `preview` embed.
  `dist/build_report.json` lists which docs are which.
- Word/PDF files in the Drive folder work too (embedded via the Drive file previewer).
