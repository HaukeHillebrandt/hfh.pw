#!/usr/bin/env python3
"""Discover published-to-web links for all site docs via the Drive API.

Read-only: queries each doc's revisions for `publishedLink` (the canonical
2PACX pub URL) using the locally-authenticated `gws` CLI. Never changes any
doc's sharing or publication state.

Run locally when docs' publication status changes, then commit the output:
    python3 tools/discover_published.py
Output: data/published_links.json  {docId: {"url": ..., "publishAuto": bool}}
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


def query(doc_id):
    params = json.dumps({"fileId": doc_id,
                         "fields": "revisions(published,publishAuto,publishedLink)"})
    try:
        out = subprocess.run(
            ["gws", "drive", "revisions", "list", "--params", params],
            capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout[out.stdout.index("{"):])
    except Exception as e:  # noqa: BLE001
        print(f"  {doc_id}: ERROR {e}", file=sys.stderr)
        return None
    for rev in reversed(data.get("revisions", [])):
        if rev.get("published") and rev.get("publishedLink"):
            return {"url": rev["publishedLink"],
                    "publishAuto": bool(rev.get("publishAuto"))}
    return None


def main():
    ids = collect_doc_ids()
    print(f"querying {len(ids)} docs…")
    links = {}
    for i, did in enumerate(ids, 1):
        r = query(did)
        if r:
            links[did] = r
        print(f"  [{i}/{len(ids)}] {did[:12]}… {'published' if r else '-'}")
    path = os.path.join(ROOT, "data", "published_links.json")
    json.dump(links, open(path, "w"), indent=1, sort_keys=True)
    auto = sum(1 for v in links.values() if v["publishAuto"])
    print(f"done: {len(links)}/{len(ids)} published ({auto} auto-republish) -> {path}")


if __name__ == "__main__":
    main()
