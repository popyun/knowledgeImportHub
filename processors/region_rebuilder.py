"""
Region-wise vision rebuild (direction 3): rebuild scrambled-layout slides by
feeding *per-region* full-resolution crops to the local vision model
(qwen2.5vl:3b via ollama), stitching the region Markdown, and adopting the
result ONLY when a per-cell consistency guard confirms the VLM neither dropped
nor fabricated content.

Why per-region (verified): the small local VLM fails on a whole page (cannot
see small digits + silently drops the lower half). On 121134 the whole-page
pass produced 409 chars (bottom table missing); splitting top/bottom and asking
each at full resolution produced 513 + 1541 chars with the 17-row sensitivity
table fully recovered.

Design principles (from the 7.18 rollback):
  - The earlier whole-page failure was NOT model strength but the lack of a
    reliable adoption guard. A coverage-by-char guard let a tampered table
    through (121131). Here the guard is per-token: adopt only if numeric tokens
    are neither dropped (hit ratio) nor invented (fabrication ratio), and the
    produced structure does not collapse.
  - OFF by default (config region_rebuild=false); when off, the pipeline is
    byte-for-byte identical to HEAD.
  - On any failure the page falls back to review-only (plan B body unchanged).
"""

import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ocr_pipeline")

_NUM_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*%?")

# Cap the longest crop side sent to the VLM. Region crops are taken from
# the scale=2 enhanced image (up to ~3400 px wide); at that size qwen2.5vl
# blows past its vision-token budget and ollama returns HTTP 400. 1400 px
# keeps small digits legible while staying inside the model's context.
_MAX_CROP_SIDE = 1400


def _numeric_tokens(text):
    out = []
    for m in _NUM_RE.findall(text or ""):
        norm = m.replace(",", "")
        if len(norm.rstrip("%")) >= 1:
            out.append(norm)
    return out


def _strip_md(text):
    return re.sub(r"[|\-\s]", "", text or "")


def ocr_numeric_tokens(blocks):
    toks = []
    for b in blocks or []:
        if b.get("type") == "text":
            toks.extend(_numeric_tokens(b.get("text", "")))
    return toks


def consistency_metrics(ocr_blocks, vlm_markdown):
    from collections import Counter
    ocr_toks = Counter(ocr_numeric_tokens(ocr_blocks))
    vlm_toks = Counter(_numeric_tokens(vlm_markdown))
    n_ocr = sum(ocr_toks.values())
    n_vlm = sum(vlm_toks.values())
    if n_ocr == 0:
        return {"hit": 0.0, "fabricate": 1.0 if n_vlm else 0.0, "n_ocr": 0, "n_vlm": n_vlm}
    matched = sum(min(c, vlm_toks.get(t, 0)) for t, c in ocr_toks.items())
    hit = matched / float(n_ocr)
    invented = sum(max(0, c - ocr_toks.get(t, 0)) for t, c in vlm_toks.items())
    fabricate = invented / float(n_vlm) if n_vlm else 0.0
    return {"hit": round(hit, 3), "fabricate": round(fabricate, 3),
            "n_ocr": n_ocr, "n_vlm": n_vlm}


# ---------------------------------------------------------------------- #
# VLM caller (reuses the local ollama /api/generate channel).
# ---------------------------------------------------------------------- #

_REGION_PROMPT = (
    "You are an OCR layout transcriber. This image is a region cropped from a "
    "Chinese financial slide. Transcribe ALL visible content into clean "
    "GitHub-Flavored Markdown, preserving structure: use '## ' for headings, "
    "render tabular data as Markdown tables, keep formulas and labels as text "
    "lines. Do NOT translate; keep Chinese text, numbers and symbols exactly as "
    "shown. Do NOT invent content. Do NOT merge separate columns into one cell. "
    "Output only Markdown."
)


def _vlm_region_markdown(crop, endpoint, model, timeout):
    # Return the VLM Markdown transcription of one region crop, or None.
    try:
        import cv2
        h, w = crop.shape[0], crop.shape[1]
        longest = max(h, w)
        if longest > _MAX_CROP_SIDE:
            scale = _MAX_CROP_SIDE / float(longest)
            crop = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))),
                              interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("region_rebuild: encode failed (%s)", exc)
        return None
    try:
        import requests
        resp = requests.post(
            endpoint.rstrip("/") + "/api/generate",
            json={
                "model": model,
                "prompt": _REGION_PROMPT,
                "images": [b64],
                "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.0, "num_predict": 2048},
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("region_rebuild: ollama request failed (%s)", exc)
        return None
    if resp.status_code != 200:
        logger.warning("region_rebuild: ollama status %s", resp.status_code)
        return None
    try:
        reply = resp.json().get("response", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("region_rebuild: bad ollama JSON (%s)", exc)
        return None
    return _clean_md(reply)


def _clean_md(text):
    # Strip code fences the model may wrap around the answer.
    if not text:
        return ""
    return text.replace("```markdown", "").replace("```", "").strip()



# ---------------------------------------------------------------------- #
# Stitch + overlap dedup.
# ---------------------------------------------------------------------- #

def _norm_line(ln):
    # Normalize a Markdown line for near-duplicate comparison.
    return re.sub(r"[|\-\s]", "", ln or "").strip().lower()


def stitch_regions(region_mds):
    # Join region Markdown in reading order, dropping lines whose normalized
    # form already appeared (removes overlap-band duplicates). Structural table
    # separator lines (---) and blank lines are never deduped.
    seen = set()
    out = []
    for md in region_mds:
        if not md:
            continue
        for ln in md.splitlines():
            key = _norm_line(ln)
            if key and len(key) >= 4:
                if key in seen:
                    continue
                seen.add(key)
            out.append(ln)
        out.append("")
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def markdown_struct_score(md):
    # Structure score in [0,1] for stitched Markdown, mirroring
    # table_enhancer.enhanced_quality (fill + column stability + size), but
    # parsing Markdown pipe tables instead of HTML. When no pipe table is
    # present, fall back to a modest text score so plain-text regions are
    # not auto-failed on structure alone.
    rows = []
    for ln in (md or '').splitlines():
        st = ln.strip()
        if not st.startswith('|'):
            continue
        cells = [c.strip() for c in st.strip('|').split('|')]
        # Skip GFM separator rows like |---|---|.
        if cells and all(set(c) <= set('-: ') and c for c in cells):
            continue
        rows.append(cells)
    if not rows:
        # No table: score by whether there is substantive text content.
        text = re.sub(r'\s', '', md or '')
        return 0.55 if len(text) >= 40 else 0.0
    counts = [len(r) for r in rows]
    n_rows = len(rows)
    n_cols = max(counts) if counts else 0
    if n_cols < 1:
        return 0.0
    non_empty = sum(1 for r in rows for c in r if c.strip())
    total = sum(counts)
    fill = non_empty / float(total) if total else 0.0
    mean_c = sum(counts) / len(counts)
    var = sum((c - mean_c) ** 2 for c in counts) / len(counts)
    stab = max(0.0, 1.0 - (var ** 0.5) / max(mean_c, 1.0))
    size_bonus = min(1.0, (n_rows * n_cols) / 12.0)
    score = 0.45 * fill + 0.35 * stab + 0.20 * size_bonus
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------- #
# RegionRebuilder: split -> crop -> per-region VLM -> stitch -> guard.
# ---------------------------------------------------------------------- #

class RegionRebuilder:
    # Rebuild a scrambled page region-by-region via the local VLM, gated by a
    # per-token consistency guard. Enabled only via config; off by default.

    def __init__(self, config=None, generator=None):
        config = config or {}
        self.config = config
        self.generator = generator
        ocr_cfg = config.get("ocr", {}) or {}
        ts = ocr_cfg.get("table_structure", {}) or {}
        ollama = ocr_cfg.get("ollama", {}) or {}
        self.enabled = bool(ts.get("region_rebuild", False))
        self.allow_adopt = bool(ts.get("region_rebuild_adopt", True))
        self.endpoint = ollama.get("endpoint", "http://localhost:11434")
        self.model = (ts.get("vision_model") or ollama.get("vision_model") or "qwen2.5vl:3b")
        self.timeout = int(ts.get("vision_timeout", 600))
        self.overlap = float(ts.get("region_overlap", 0.08))
        self.pad = int(ts.get("crop_pad", 14))
        self.hit_min = float(ts.get("rebuild_hit_min", 0.9))
        self.fabricate_max = float(ts.get("rebuild_fabricate_max", 0.05))
        self.struct_min = float(ts.get("rebuild_struct_min", 0.5))
        self.max_regions = int(ts.get("rebuild_max_regions", 6))

    def _region_boxes(self, blocks):
        # Use the generator's layout split to get region block-groups, then
        # convert each to an OCR-space pixel box via _region_metrics.
        gen = self.generator
        if gen is None:
            return []
        text_blocks = [b for b in blocks if b.get("type") == "text" and b.get("text", "").strip()]
        if not text_blocks:
            return []
        boxes = []
        for band in gen._split_into_vertical_regions(text_blocks):
            for region in gen._split_region_columns_if_needed(band):
                if not region:
                    continue
                m = gen._region_metrics(region)
                boxes.append((m, region))
        return boxes

    def rebuild(self, enhanced_image, blocks):
        # Return a verdict dict or None. Never raises into the pipeline.
        if not self.enabled or enhanced_image is None:
            return None
        blocks = blocks or []
        boxes = self._region_boxes(blocks)
        if not boxes or len(boxes) > self.max_regions:
            # Too fragmented or nothing to do: skip (fragmentation cap avoids
            # dozens of slow VLM calls on a pathological page).
            if not boxes:
                return None
        h, w = enhanced_image.shape[0], enhanced_image.shape[1]
        oy = int(h * self.overlap)
        ox = int(w * self.overlap)
        region_mds = []
        for m, _region in boxes[: self.max_regions]:
            x0 = max(int(m["min_x"]) - self.pad - ox, 0)
            y0 = max(int(m["min_y"]) - self.pad - oy, 0)
            x1 = min(int(m["max_x"]) + self.pad + ox, w)
            y1 = min(int(m["max_y"]) + self.pad + oy, h)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            crop = enhanced_image[y0:y1, x0:x1]
            md = _vlm_region_markdown(crop, self.endpoint, self.model, self.timeout)
            if md is None:
                # A region failed -> abandon adoption for the whole page.
                logger.info("region_rebuild: region VLM failed; page falls back")
                return None
            region_mds.append(md)
        if not region_mds:
            return None
        stitched = stitch_regions(region_mds)
        if not stitched.strip():
            return None
        verdict = self._judge(blocks, stitched)
        verdict["markdown"] = stitched
        verdict["n_regions"] = len(region_mds)
        return verdict

    def _judge(self, blocks, stitched):
        # Apply the per-token consistency guard + structure score, decide adopt.
        cm = consistency_metrics(blocks, stitched)
        # Structure score from the stitched Markdown (pipe tables), not HTML.
        se = markdown_struct_score(stitched)
        reasons = []
        if cm["hit"] < self.hit_min:
            reasons.append("hit %.2f < %.2f (dropped content)" % (cm["hit"], self.hit_min))
        if cm["fabricate"] > self.fabricate_max:
            reasons.append("fabricate %.2f > %.2f (invented numbers)" % (cm["fabricate"], self.fabricate_max))
        if se < self.struct_min:
            reasons.append("structure %.2f < %.2f" % (se, self.struct_min))
        adopt = self.allow_adopt and not reasons
        return {
            "adopt": bool(adopt),
            "hit": cm["hit"],
            "fabricate": cm["fabricate"],
            "struct": round(float(se), 3),
            "n_ocr": cm["n_ocr"],
            "n_vlm": cm["n_vlm"],
            "reasons": reasons,
        }

