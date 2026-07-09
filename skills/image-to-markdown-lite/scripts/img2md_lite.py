#!/usr/bin/env python3
"""Portable image -> Markdown with inline [[wiki-links]] (self-contained).

No dependency on the Knowledge Import Hub processors. Uses PaddleOCR for text
detection/recognition and reconstructs Markdown from line geometry:

- Lines are clustered into rows by vertical overlap.
- Rows whose cells align into >= 2 stable columns become a Markdown table.
- The largest-font line(s) near the top become the H1 title; other notably
  large lines become sub-headings.
- Inline [[wiki-links]] are injected for vault note titles (if --vault given)
  and, failing that, for repeated multi-character terms.

Produces Markdown text only: no publishing, archiving, file moves, or watching.

Usage:
    python img2md_lite.py <image> [--out OUT.md] [--vault VAULT] [--lang ch]

Dependencies: paddleocr, opencv-python, numpy.
Exit codes: 0 ok, 2 bad args / file missing, 1 processing failure.
"""

import argparse
import os
import re
import sys
from collections import Counter

import numpy as np


def _imread_unicode(path):
    import cv2
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _run_ocr(image_path, lang):
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    img = _imread_unicode(image_path)
    if img is None:
        raise RuntimeError("Could not read image: %s" % image_path)
    raw = ocr.ocr(img, cls=True)
    lines = []
    for page in raw or []:
        for box, (text, conf) in page or []:
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append({
                "text": text,
                "conf": float(conf),
                "x0": min(xs), "x1": max(xs),
                "y0": min(ys), "y1": max(ys),
                "cx": (min(xs) + max(xs)) / 2.0,
                "h": max(ys) - min(ys),
            })
    lines.sort(key=lambda l: (round(l["y0"] / 10.0), l["x0"]))
    return lines


def _cluster_rows(lines):
    """Group lines into visual rows by vertical overlap."""
    rows = []
    for line in sorted(lines, key=lambda l: l["y0"]):
        placed = False
        for row in rows:
            ry0, ry1 = row["y0"], row["y1"]
            overlap = min(line["y1"], ry1) - max(line["y0"], ry0)
            if overlap > 0.4 * min(line["h"], ry1 - ry0):
                row["cells"].append(line)
                row["y0"] = min(ry0, line["y0"])
                row["y1"] = max(ry1, line["y1"])
                placed = True
                break
        if not placed:
            rows.append({"y0": line["y0"], "y1": line["y1"], "cells": [line]})
    for row in rows:
        row["cells"].sort(key=lambda c: c["x0"])
    rows.sort(key=lambda r: r["y0"])
    return rows


def _detect_table_runs(rows):
    """Return list of (start, end) index ranges of rows forming a table."""
    multi = [i for i, r in enumerate(rows) if len(r["cells"]) >= 2]
    runs = []
    if not multi:
        return runs
    start = prev = multi[0]
    for idx in multi[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        if prev - start + 1 >= 2:
            runs.append((start, prev))
        start = prev = idx
    if prev - start + 1 >= 2:
        runs.append((start, prev))
    return runs


def _table_to_md(rows):
    ncols = max(len(r["cells"]) for r in rows)
    def fmt(row):
        cells = [c["text"].replace("|", "\\|") for c in row["cells"]]
        cells += [""] * (ncols - len(cells))
        return "| " + " | ".join(cells) + " |"
    out = [fmt(rows[0]), "| " + " | ".join(["---"] * ncols) + " |"]
    out += [fmt(r) for r in rows[1:]]
    return "\n".join(out)


def _link_terms(lines, vault_titles):
    """Pick inline-link terms: vault titles present in text, else repeats."""
    text_all = " ".join(l["text"] for l in lines)
    terms = set()
    for title in vault_titles:
        if len(title) >= 2 and title in text_all:
            terms.add(title)
    if not terms:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9]{3,}|[\u4e00-\u9fff]{2,6}", text_all)
        for tok, cnt in Counter(tokens).items():
            if cnt >= 2:
                terms.add(tok)
    return sorted(terms, key=len, reverse=True)


def _inject_links(text, terms):
    for term in terms:
        if "[[" in text and term in re.findall(r"\[\[(.*?)\]\]", text):
            continue
        pattern = re.compile(re.escape(term))
        text = pattern.sub("[[%s]]" % term, text, count=1)
    return text


def _load_vault_titles(vault_root):
    titles = set()
    if not vault_root or not os.path.isdir(vault_root):
        return titles
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(".md"):
                titles.add(os.path.splitext(name)[0])
    return titles


def build_markdown(image_path, vault_root="", lang="ch"):
    lines = _run_ocr(image_path, lang)
    if not lines:
        return "# (no text detected)\n"

    heights = sorted(l["h"] for l in lines)
    median_h = heights[len(heights) // 2]
    rows = _cluster_rows(lines)
    table_runs = _detect_table_runs(rows)
    in_table = set()
    for s, e in table_runs:
        in_table.update(range(s, e + 1))

    # Title: largest line within the top third.
    top_cut = min(l["y0"] for l in lines) + 0.33 * (
        max(l["y1"] for l in lines) - min(l["y0"] for l in lines))
    top_lines = [l for l in lines if l["y0"] <= top_cut] or lines
    title = max(top_lines, key=lambda l: l["h"])["text"]

    vault_titles = _load_vault_titles(vault_root)
    terms = _link_terms(lines, vault_titles)

    parts = ["---", "title: \"%s\"" % title.replace('"', "'"), "---", "", "# %s" % title, ""]
    idx = 0
    while idx < len(rows):
        if idx in in_table:
            run = next((r for r in table_runs if r[0] == idx), None)
            if run:
                s, e = run
                parts.append(_table_to_md(rows[s:e + 1]))
                parts.append("")
                idx = e + 1
                continue
        row = rows[idx]
        line_text = " ".join(c["text"] for c in row["cells"])
        max_h = max(c["h"] for c in row["cells"])
        if line_text == title:
            idx += 1
            continue
        if max_h >= 1.35 * median_h and len(row["cells"]) == 1:
            parts.append("## %s" % _inject_links(line_text, terms))
        else:
            parts.append(_inject_links(line_text, terms))
        parts.append("")
        idx += 1

    return "\n".join(parts).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Image -> Markdown (portable)")
    parser.add_argument("image")
    parser.add_argument("--out", default="")
    parser.add_argument("--vault", default="")
    parser.add_argument("--lang", default="ch")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.image):
        print("Image not found: %s" % args.image, file=sys.stderr)
        return 2
    try:
        markdown = build_markdown(args.image, args.vault, args.lang)
    except Exception as exc:  # noqa: BLE001
        print("Processing failed: %s" % exc, file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(markdown)
        print("Wrote %s" % args.out)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
