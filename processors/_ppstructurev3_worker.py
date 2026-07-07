# -*- coding: utf-8 -*-
"""Isolated PP-StructureV3 worker.

Run inside a paddleocr-3.x environment (a separate venv) so the main pipeline
can keep paddleocr 2.7.3 for text OCR while still using PP-StructureV3 as a
review-only table-enhancement backend. Reads an image path from argv[1],
recognizes it with PP-StructureV3, and prints a JSON object to stdout:

    {"html": "<table>...</table>"}   on success
    {"html": null, "error": "..."}   on failure

The parent process (processors/table_enhancer.py: PPStructureV3Backend) invokes
this via subprocess and never imports paddleocr 3.x into the main interpreter.
"""
import io
import json
import os
import re
import sys


def _extract_table_html(results):
    """Pull the first <table>...</table> out of PP-StructureV3 results."""
    if not results:
        return None
    for res in results:
        texts = []
        md = getattr(res, "markdown", None)
        if isinstance(md, dict):
            mt = md.get("markdown_texts")
            if mt:
                texts.append(mt if isinstance(mt, str) else str(mt))
        elif isinstance(md, str):
            texts.append(md)
        try:
            j = getattr(res, "json", None)
            if j is not None:
                texts.append(j if isinstance(j, str) else str(j))
        except Exception:
            pass
        for text in texts:
            if not text:
                continue
            m = re.search(r"<table.*?</table>", text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(0)
    return None


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"html": None, "error": "no image path"}))
        return 0
    img_path = sys.argv[1]
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    try:
        from paddleocr import PPStructureV3
        pipe = PPStructureV3(device="cpu")
        results = pipe.predict(img_path)
        html = _extract_table_html(results)
        sys.stdout.write(json.dumps({"html": html}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"html": None, "error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
