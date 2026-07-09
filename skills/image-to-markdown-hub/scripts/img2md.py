#!/usr/bin/env python3
"""Convert a document image to Obsidian-flavored Markdown with inline wiki-links.

Thin CLI over the Knowledge Import Hub pipeline. It runs the full quality
pipeline (layout reconstruction, colored tables, noise filtering, optional
low-quality table enhancement) and inline [[wiki-link]] candidate injection,
then prints/writes Markdown ONLY. No publishing, archiving, or watching.

Usage:
    python img2md.py <image> [--config CONFIG] [--out OUT.md] [--vault VAULT]

Args:
    <image>       Path to the input document image (png/jpg/...).
    --config      Path to a Hub config.yaml. Defaults to the repo config.yaml.
    --out         Write Markdown to this file. If omitted, prints to stdout.
    --vault       Optional vault root to index for [[wiki-link]] targets. If
                  omitted, uses vault.root from config (links still generated
                  for detected entities; existing-note matching is best-effort).

Exit codes: 0 ok, 2 bad args / file missing, 1 processing failure.
"""

import argparse
import os
import sys


def _repo_root() -> str:
    # skills/image-to-markdown-hub/scripts/img2md.py -> repo root is 4 up.
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def build_markdown(image_path: str, config_path: str, vault_root: str = "") -> str:
    """Run the Hub pipeline and return Markdown with inline wiki-links."""
    import yaml

    from processors.image_handler import ImageHandler
    from linkers.entity_linker import EntityLinker
    from linkers.disambiguator import Disambiguator

    with open(config_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    handler = ImageHandler(config)
    if not handler.initialize():
        raise RuntimeError("ImageHandler failed to initialize")

    result = handler.process(image_path)
    if not result.get("success"):
        raise RuntimeError(result.get("error", "unknown processing error"))

    ocr_result = result["ocr_result"]

    # Assemble inline link candidates exactly like main.py::process_file.
    linker = EntityLinker(config)
    index_root = vault_root or config.get("vault", {}).get("root", "")
    if index_root and os.path.isdir(index_root):
        linker.build_vault_index(index_root)

    ocr_text = " ".join(
        block.get("text", "") for block in ocr_result.get("blocks", [])
    )
    disambiguator = Disambiguator(config)
    candidates = linker.extract_candidates(ocr_text)
    filtered = linker.filter_candidates(candidates)
    scored = disambiguator.score_candidates(filtered, ocr_text)
    categorized = disambiguator.categorize_by_confidence(scored)
    link_candidates = categorized["high"] + categorized["medium"]

    return handler.markdown_generator.process(
        ocr_result, image_path, link_candidates
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Image -> Obsidian Markdown (Hub pipeline)")
    parser.add_argument("image")
    parser.add_argument("--config", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--vault", default="")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if not os.path.isfile(args.image):
        print("Image not found: %s" % args.image, file=sys.stderr)
        return 2

    config_path = args.config or os.path.join(repo_root, "config.yaml")
    if not os.path.isfile(config_path):
        print("Config not found: %s" % config_path, file=sys.stderr)
        return 2

    try:
        markdown = build_markdown(args.image, config_path, args.vault)
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
