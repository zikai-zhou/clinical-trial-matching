#!/usr/bin/env python3
"""Compute word-level diff between original_chart and each rewrite's cf_chart.

Annotates each rewrite in clinician_review.json with:
  - diff_segments: [{type: 'equal'|'add'|'del', text: str}, ...]
  - diff_summary:  {n_added, n_deleted, n_changed_lines, edit_distance_chars}

This is consumed by:
  - the UI for highlighted side-by-side rendering
  - the gpt-5 simclin re-validator (passed as a structured "what changed" list
    so the judge focuses on the actual edit, not re-reading two full charts)
"""
from __future__ import annotations
import argparse, difflib, json, pathlib, re


def word_tokens(text: str) -> list[str]:
    # Tokenize on whitespace + punctuation boundaries, keeping the separators
    return re.findall(r"\S+|\s+", text)


def diff_segments(a: str, b: str) -> list[dict]:
    ta, tb = word_tokens(a), word_tokens(b)
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    segs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            segs.append({"type": "equal", "text": "".join(ta[i1:i2])})
        elif tag == "delete":
            segs.append({"type": "del", "text": "".join(ta[i1:i2])})
        elif tag == "insert":
            segs.append({"type": "add", "text": "".join(tb[j1:j2])})
        elif tag == "replace":
            segs.append({"type": "del", "text": "".join(ta[i1:i2])})
            segs.append({"type": "add", "text": "".join(tb[j1:j2])})
    return segs


def summarize(segs: list[dict], orig: str, cf: str) -> dict:
    n_add = sum(1 for s in segs if s["type"] == "add")
    n_del = sum(1 for s in segs if s["type"] == "del")
    added_chars = sum(len(s["text"]) for s in segs if s["type"] == "add")
    deleted_chars = sum(len(s["text"]) for s in segs if s["type"] == "del")
    # Line-level changed count (heuristic)
    orig_lines = set(l.strip() for l in orig.splitlines() if l.strip())
    cf_lines = set(l.strip() for l in cf.splitlines() if l.strip())
    changed_lines = len(orig_lines.symmetric_difference(cf_lines))
    return {
        "n_added_segs": n_add,
        "n_deleted_segs": n_del,
        "added_chars": added_chars,
        "deleted_chars": deleted_chars,
        "changed_lines": changed_lines,
        "orig_chars": len(orig),
        "cf_chars": len(cf),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--in-place", action="store_true")
    args = ap.parse_args()

    p = pathlib.Path(args.review)
    d = json.load(p.open())
    topics = d.get("topics", d) if isinstance(d, dict) else d

    n_processed = 0
    for t in topics:
        if t.get("sheet") != "cf_rewrite_review":
            continue
        orig = t.get("original_chart") or ""
        for rw in t.get("rewrites", []):
            cf = rw.get("cf_chart") or ""
            if not orig or not cf:
                continue
            segs = diff_segments(orig, cf)
            rw["diff_segments"] = segs
            rw["diff_summary"] = summarize(segs, orig, cf)
            n_processed += 1

    out = p if args.in_place else p.with_suffix(".diffs.json")
    json.dump(d, out.open("w"), indent=2)
    print(f"processed {n_processed} rewrites; wrote {out}")


if __name__ == "__main__":
    main()
