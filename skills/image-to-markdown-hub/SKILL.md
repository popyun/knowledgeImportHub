---
name: image-to-markdown-hub
description: Convert a document image (screenshot, scanned page, slide, table) into Obsidian-flavored Markdown with inline [[wiki-link]] candidates, using the Knowledge Import Hub's full quality pipeline (layout reconstruction, colored tables, page-number capture, noise filtering, optional low-quality table enhancement). Use when the user wants faithful image-to-Markdown with structure and links preserved and has this repository (or its dependencies) available. Produces Markdown only; it does NOT publish, archive, move files, or watch folders.
---

# Image to Markdown (Hub pipeline)

## Overview

Turn one document image into a single Markdown note whose layout, tables, and
emphasis mirror the source, with inline `[[wiki-link]]` candidates already
injected. This is the repo-backed version: it drives the mature Knowledge
Import Hub processors, so it inherits the project's quality-safety behavior
(never silently degrades output, review-only table enhancement, noise
filtering with audit notes). Scope stops at Markdown text: no publishing,
archiving, file moves, or folder watching.

## When to use

- The user wants high-fidelity conversion of a screenshot / scanned page /
  slide / table image to Markdown, keeping structure and links.
- This repository is checked out and its Python dependencies are installed.

If the repository or its heavy OCR dependencies are not available, prefer the
portable sibling skill `image-to-markdown-lite` instead.

## Quick start

Run the bundled script with the image path:

```bash
python skills/image-to-markdown-hub/scripts/img2md.py <image> --out note.md
```

- Omit `--out` to print Markdown to stdout.
- `--config PATH` overrides the Hub `config.yaml` (defaults to the repo one).
- `--vault PATH` indexes an existing Obsidian vault so `[[wiki-links]]` prefer
  real note titles; omit to use `vault.root` from config.

The script must run from the repository root (or any cwd) - it locates the repo
root relative to its own path and adds it to `sys.path`.

## How it works

The script reproduces `main.py::process_file` minus the publisher:

1. `ImageHandler.process(image)` runs preprocessing, OCR routing, post
   correction, table building, layout-aware Markdown generation, and any
   configured enhancement gate. It returns a corrected OCR result.
2. Inline links are assembled exactly as the pipeline does: `EntityLinker`
   extracts and filters candidates, `Disambiguator` scores and categorizes
   them, and the high + medium buckets become `link_candidates`.
3. `MarkdownGenerator.process(ocr_result, image_path, link_candidates)`
   renders the final Markdown. This is the single layout-authority; do not
   post-process its output for layout.

## Guarantees and constraints

- Output is Markdown text only. Never add publishing, archiving, file moves,
  or watcher behavior here - that belongs to a separate downstream skill.
- Do not reimplement layout logic. All structure/table/link rendering is owned
  by `MarkdownGenerator`; the script only wires inputs and returns its output.
- Enhancement backends stay at their config defaults (review-only, off unless
  enabled). Do not force-adopt enhanced tables from this skill.
- Front matter includes the detected title and page number; keep them intact.

## Resources

### scripts/
- `scripts/img2md.py` - CLI wrapper over the Hub pipeline. Reuse it directly;
  patch it only to adjust wiring (config/vault handling, output format), never
  to duplicate the generator's layout logic.
