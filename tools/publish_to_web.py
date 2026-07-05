#!/usr/bin/env python3
"""Publish all of the site's not-yet-published Google Docs to the web.

FOR HAUKE TO RUN HIMSELF (it changes doc publication state):
    python3 tools/publish_to_web.py           # dry run: lists what it would do
    python3 tools/publish_to_web.py --apply   # actually publish

For each doc that data/published_links.json doesn't already list as published,
this sets published=true + publishAuto=true on its head revision via the
Drive API (the same thing File -> Share -> Publish to web does, with
"automatically republish when changes are made" ticked).

Afterwards run tools/discover_published.py and push, or let Claude's loop
pick it up.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from discover_published import collect_doc_ids  # noqa: E402

APPLY = "--apply" in sys.argv


def gws(args):
    out = subprocess.run(["gws"] + args, capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout[out.stdout.index("{"):])


def head_revision(doc_id):
    data = gws(["drive", "revisions", "list", "--params",
                json.dumps({"fileId": doc_id, "fields": "revisions(id)"})])
    revs = data.get("revisions", [])
    return revs[-1]["id"] if revs else None


def publish(doc_id, rev_id):
    return gws(["drive", "revisions", "update", "--params",
                json.dumps({"fileId": doc_id, "revisionId": rev_id,
                            "requestBody": {"published": True,
                                            "publishAuto": True}})])


def main():
    published = json.load(open(os.path.join(ROOT, "data", "published_links.json")))
    todo = [d for d in collect_doc_ids() if d not in published]
    print(f"{len(todo)} docs not yet published to web"
          + ("" if APPLY else "  (dry run — pass --apply to publish)"))
    for i, did in enumerate(todo, 1):
        if not APPLY:
            print(f"  would publish {did}")
            continue
        try:
            rev = head_revision(did)
            if not rev:
                print(f"  [{i}/{len(todo)}] {did}: no revisions, skipped")
                continue
            r = publish(did, rev)
            ok = r.get("published") or r.get("publishedLink")
            print(f"  [{i}/{len(todo)}] {did}: {'published' if ok else 'FAILED: ' + json.dumps(r)[:120]}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(todo)}] {did}: ERROR {e}")
    if APPLY:
        print("\nNow run: python3 tools/discover_published.py  (or let the loop do it)")


if __name__ == "__main__":
    main()
