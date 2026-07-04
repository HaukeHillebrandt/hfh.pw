# Handover — new hfh.pw site

Built autonomously on 2026-07-04. Live at **https://haukehillebrandt.github.io/hfh.pw/**.
The old Google Site at www.hfh.pw is untouched; nothing is published outside this repo.

## What you have now

- Every Google Doc in the [Drive folder](https://drive.google.com/drive/folders/1fh8-FbNMdqp6ULXb_QnzhXeauPzVjzls)
  (incl. Productivity + Summaries subfolders) is a post; all 46 old Google-Sites slugs
  (`/ClaudeMaxxing`, `/ai-biases`, …) resolve on the new site, so existing links survive
  the DNS flip. Substack + Bearblog posts are listed and link out.
- Post pages iframe the **live doc** (instant updates), with *Open in Google Docs* and
  *Reader view* buttons. Reader views are exported at build time (fast + indexable,
  images recompressed).
- Daily rebuild at 05:23 UTC + on every push + manual (Actions → Run workflow).
  Drag a doc into the Drive folder and it appears within a day (or trigger a run).
- RSS at `/feed.xml`, per-post social cards, JSON-LD, sitemap, WCAG-AA-checked palette.

## Your decisions / actions

1. **DNS flip** (when happy with the site): follow the checklist in README.md.
   Afterwards, unpublish the Google Site.
2. **Optional — publish docs to the web** for cleaner embeds. 26 of 81 docs are
   published-to-web and get the clean article-style embed; the 55 below fall back to
   the paginated preview embed (same as the old Google Sites behavior, so no
   regression). To upgrade one: open the doc → File → Share → Publish to web →
   tick "Automatically republish when changes are made".
   Current fallback list (also in `dist/build_report.json` after each build):
   AI-claims, AI-progress-2026, China-West-proxy-war, Chinas-Shadow-War, ClaudeMaxxing,
   ITN-biases, ORS, Registered-Reports, Russias-Shadow-War, WaClaude, ai-biases, alc,
   behavioral-therapy…, big-life-decisions, bostroms-future-of-humanity-papers,
   brain-compute, carbon-impact-markets, china-militarization, common-law…, con,
   contra-parfits-against-egoism, covid-recommender, disrupting-housing, econ-goat,
   efficient-everything, feel-good-productivity-summary, free-will, future,
   gates-critique, gov_pay, how-to-speed-up-your-computer, ideas-for-uk-arpa…,
   industrial-revolution, intl-agreements, let-go, list-of-summaries, moravecs-law,
   most-important-century…, non-iron, optimal-non-profit-compensation…,
   potential-priority-areas…, rsp-dilution, rural-to-urban-migration, sadly-podcasts,
   small-brains, spillback-growth, structure-of-a-philosophical-essay…,
   summary-g-polya…, summary-of-you-and-your-research, threat-modelling, vix-hedge,
   why-micracles-are-very-unlikely, world-events, writing, wwiii-risk
3. **Optional — `gws auth login`** (type `! gws auth login` in a Claude session): lets
   Claude read (read-only) each published doc's canonical `2PACX` URL via the Drive
   revisions API, which upgrades embeds for docs that are published but block the
   anonymous ID-based pub endpoint (e.g. ClaudeMaxxing).
4. **Featured posts**: edit `featured` in `config.json` (list of slugs) — currently
   GDP-2050, OpenPhil critique, 69 things, Is China preparing for war.
5. **Docx idea**: Word/PDF files dropped in the Drive folder already work (Drive file
   previewer embed), so swapping a Google Doc for a Drive-synced .docx keeps working —
   it just loses the published-HTML embed and reader view (no HTML export probing for
   docx yet; ask Claude to add it if you adopt that workflow).

## Maintenance

- Slug for a new doc = slugified title; override via `slug_overrides` in `config.json`.
- Feed caches in `data/cache/` self-refresh; Substack blocks GitHub runners, so its
  cache updates only when someone builds locally (`python3 build.py`) — or ask Claude.
- If a build breaks, Pages keeps serving the last good deploy; check the Actions tab.
