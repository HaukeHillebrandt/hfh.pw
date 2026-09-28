#!/usr/bin/env python3
"""Refresh Drive metadata for all site docs via the Drive API (read-only).

Uses the locally-authenticated `gws` CLI; never changes any doc's sharing or
publication state. The GitHub Action has no Google credentials, so run this
locally and commit the outputs:

    python3 tools/discover_published.py

Outputs:
  data/published_links.json  {docId: {"url": 2PACX pub URL, "publishAuto": bool}}
      Canonical published-to-web links (some published docs 401 on the
      anonymous ID-based pub endpoint).
  data/doc_meta.json         {docId: {"created": ISO date, "name": str}}
      Creation dates, used to date Drive docs that have no known publication
      date. Docs added later fall back to the folder's last-modified date
      until the next run.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect_doc_ids():
    ids = set()
    harvest = json.load(open(os.path.join(ROOT, "data", "slugs_harvest.json")))
    for info in harvest.values():
        if info.get("docId"):
            ids.add(info["docId"])
    config = json.load(open(os.path.join(ROOT, "config.json")))
    for info in config.get("slug_overrides", {}).values():
        if info.get("docId"):
            ids.add(info["docId"])
    ids.add(config["cv_doc_id"])
    cache_dir = os.path.join(ROOT, "data", "cache")
    for f in os.listdir(cache_dir):
        if f.startswith("drive_") and f.endswith(".json"):
            for e in json.load(open(os.path.join(cache_dir, f))):
                if "/document/" in (e.get("url") or ""):
                    ids.add(e["id"])
    return sorted(ids)


def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout[out.stdout.index("{"):])


def query_published(doc_id):
    try:
        data = gws(["drive", "revisions", "list", "--params", json.dumps(
            {"fileId": doc_id, "fields": "revisions(published,publishAuto,publishedLink)"})])
    except Exception as e:  # noqa: BLE001
        print(f"  {doc_id}: revisions ERROR {e}", file=sys.stderr)
        return None
    for rev in reversed(data.get("revisions", [])):
        if rev.get("published") and rev.get("publishedLink"):
            return {"url": rev["publishedLink"],
                    "publishAuto": bool(rev.get("publishAuto"))}
    return None


def query_meta(doc_id):
    try:
        data = gws(["drive", "files", "get", "--params", json.dumps(
            {"fileId": doc_id, "fields": "name,createdTime"})])
        return {"created": data["createdTime"][:10], "name": data["name"]}
    except Exception as e:  # noqa: BLE001
        print(f"  {doc_id}: meta ERROR {e}", file=sys.stderr)
        return None


def main():
    ids = collect_doc_ids()
    print(f"querying {len(ids)} docs…")
    links, meta = {}, {}
    for i, did in enumerate(ids, 1):
        r = query_published(did)
        if r:
            links[did] = r
        m = query_meta(did)
        if m:
            meta[did] = m
        print(f"  [{i}/{len(ids)}] {did[:12]}… {'published' if r else '-':9s} "
              f"{m['created'] if m else '?'}")
    json.dump(links, open(os.path.join(ROOT, "data", "published_links.json"), "w"),
              indent=1, sort_keys=True)
    json.dump(meta, open(os.path.join(ROOT, "data", "doc_meta.json"), "w"),
              indent=1, sort_keys=True, ensure_ascii=False)
    auto = sum(1 for v in links.values() if v["publishAuto"])
    print(f"done: {len(links)}/{len(ids)} published ({auto} auto-republish), "
          f"{len(meta)} creation dates")


if __name__ == "__main__":
    main()
