---
name: image-to-markdown-lite
description: Portable, self-contained conversion of a document image (screenshot, scanned page, slide, table) into Markdown with inline [[wiki-link]] candidates, using PaddleOCR plus geometry-based layout reconstruction. Use when the user wants image-to-Markdown with structure and links but does NOT have the full Knowledge Import Hub repository/pipeline available, or wants a lightweight distillable version that runs anywhere with paddleocr installed. Produces Markdown only; it does NOT publish, archive, move files, or watch folders.
---

# Image to Markdown (portable)

## Overview

Convert one document image into a single Markdown note with title, headings,
tables, and inline `[[wiki-link]]` candidates - without depending on the
Knowledge Import Hub processors. Layout is reconstructed from OCR line geometry
(row clustering + column alignment + relative font size), so this version is
easy to distill and ship anywhere `paddleocr` runs. Scope stops at Markdown
text: no publishing, archiving, file moves, or folder watching.

## When to use

- The user wants image-to-Markdown with structure and links, but the full Hub
  repo/pipeline is unavailable or overkill.
- A lightweight, portable, single-file version is preferred for distribution.

If the full repository is available and maximum fidelity is required (colored
tables, noise filtering, enhancement gates), prefer the sibling skill
`image-to-markdown-hub` instead.

## Quick start

```bash
python skills/image-to-markdown-lite/scripts/img2md_lite.py <image> --out note.md
```

- Omit `--out` to print Markdown to stdout.
- `--vault PATH` indexes an Obsidian vault so `[[wiki-links]]` prefer real note
  titles; without it, links fall back to repeated multi-character terms.
- `--lang` sets the PaddleOCR language (default `ch`; use `en` for English).

Install dependencies once: `pip install paddleocr opencv-python numpy`.

## How it works

1. PaddleOCR returns text lines with bounding boxes. Unicode image paths are
   read via `cv2.imdecode(np.fromfile(...))` (Windows-safe).
2. Lines are clustered into visual rows by vertical overlap. Rows that align
   into >= 2 stable columns across >= 2 consecutive rows become a Markdown
   table; other rows become paragraphs.
3. The largest-font line in the top third becomes the H1 title. Single-cell
   lines notably larger than the median height become `##` sub-headings.
4. Inline `[[wiki-links]]` are injected once per term: vault note titles found
   in the text take priority, otherwise repeated tokens are linked.

## Guarantees and constraints

- Output is Markdown text only. Never add publishing, archiving, file moves,
  or watcher behavior - that belongs to a separate downstream skill.
- Keep the script self-contained: do not import the Hub `processors/` package.
  The point of this version is portability and independent distribution.
- Heuristics are geometry-based and best-effort; for pixel-faithful colored
  tables and noise filtering, direct the user to `image-to-markdown-hub`.

## Resources

### scripts/
- `scripts/img2md_lite.py` - self-contained CLI (PaddleOCR + geometry). Patch
  it to tune row/column/heading thresholds or link heuristics; keep it free of
  repository-specific imports so it stays portable.
