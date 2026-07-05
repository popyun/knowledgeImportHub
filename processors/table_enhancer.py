"""
Table enhancer (plan A): optional re-recognition of low-confidence table
regions detected by the geometric reconstructor (plan B).

Architecture (pluggable backends + tier auto-selection):
  - The host tier is decided once by host_profiler (cached to
    host_profile.local.json) and maps to a backend:
      * "vision"    -> VisionLocalBackend (offline VLM; strongest)
      * "gridboost" -> GridBoostBackend  (pure-OpenCV preprocessing + PP-Structure)
      * "manual"    -> no enhancement (plan B main output + human review)
  - config.yaml may override the backend via ocr.table_structure.backend.
  - Enhancement stays OFF by default (enhance_on_low_quality: false); the tier
    only decides WHICH backend runs when enhancement is explicitly enabled.

Design constraints (verified on the colored-slide corpus):
  - PP-Structure whole-page layout mis-classifies colored slides as one
    "figure"; we only crop the specific low-confidence region reported by plan B
    and run table-only recognition on that crop.
  - Plan A never replaces the plan B main output. Enhanced results are attached
    as a review-only ("supplementary") block; the plan B rendering and its
    position are unchanged, so enabling plan A can only add information.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ocr_pipeline")


# --------------------------------------------------------------------------- #
# gridboost preprocessing (pure OpenCV: zero new model, zero extra memory).
# --------------------------------------------------------------------------- #

def _binarize_decolor(crop):
    """Grayscale + adaptive threshold: push colored background to white and
    text to black, moving a colored/borderless table toward SLANet's training
    distribution. Returns a BGR image (PP-Structure expects 3 channels)."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=31, C=15,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def _cluster_axis(values, gap):
    """Cluster sorted 1-D coordinates into groups separated by >= gap; return
    the midpoint between adjacent cluster edges as candidate grid lines."""
    if not values:
        return []
    values = sorted(values)
    clusters = [[values[0]]]
    for v in values[1:]:
        if v - clusters[-1][-1] > gap:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    lines = []
    for i in range(len(clusters) - 1):
        edge_a = max(clusters[i])
        edge_b = min(clusters[i + 1])
        lines.append(int((edge_a + edge_b) / 2))
    return lines


def _draw_virtual_grid(binary_bgr, region_blocks, offset_xy):
    """Draw thin black grid lines between OCR word boxes so a borderless table
    becomes a ruled table. region_blocks are OCR blocks (OCR-space bbox); the
    crop offset (x0, y0) maps them into crop-local coordinates."""
    import cv2
    if not region_blocks:
        return binary_bgr
    ox, oy = offset_xy
    h, w = binary_bgr.shape[0], binary_bgr.shape[1]
    xs_center, ys_center, heights = [], [], []
    for b in region_blocks:
        bb = b.get("bbox")
        if not bb:
            continue
        xx = [p[0] - ox for p in bb]
        yy = [p[1] - oy for p in bb]
        xs_center.append((min(xx) + max(xx)) / 2)
        ys_center.append((min(yy) + max(yy)) / 2)
        heights.append(max(yy) - min(yy))
    if not heights:
        return binary_bgr
    med_h = sorted(heights)[len(heights) // 2]
    row_lines = _cluster_axis(ys_center, gap=max(med_h * 0.8, 8))
    col_lines = _cluster_axis(xs_center, gap=max(med_h * 1.2, 12))
    for y in row_lines:
        if 0 < y < h:
            cv2.line(binary_bgr, (0, y), (w, y), (0, 0, 0), 1)
    for x in col_lines:
        if 0 < x < w:
            cv2.line(binary_bgr, (x, 0), (x, h), (0, 0, 0), 1)
    return binary_bgr


def gridboost_preprocess(crop, region_blocks, offset_xy):
    """Full gridboost transform: decolor/binarize then add virtual grid lines."""
    binary = _binarize_decolor(crop)
    return _draw_virtual_grid(binary, region_blocks, offset_xy)

# --------------------------------------------------------------------------- #
# Enhanced-result quality score S_e (comparable-ish to plan B's S_b).
# --------------------------------------------------------------------------- #

def _parse_html_rows(html):
    """Return a list of rows (each a list of cell texts) from a simple table."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self._row = None
            self._cell = None

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []

        def handle_data(self, data):
            if self._cell is not None:
                self._cell.append(data)

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in ("td", "th") and self._cell is not None:
                if self._row is not None:
                    self._row.append("".join(self._cell).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self.rows.append(self._row)
                self._row = None

    parser = _P()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        return []
    return parser.rows


def enhanced_quality(html):
    """Score an enhanced HTML table in [0, 1] using structure-only signals:
    fill (non-empty cells), column-count stability, and a small-table penalty.
    Mirrors the spirit of plan B's _table_quality so scores are comparable."""
    rows = _parse_html_rows(html)
    if not rows:
        return {"score": 0.0, "n_rows": 0, "n_cols": 0, "fill": 0.0, "stab": 0.0}
    counts = [len(r) for r in rows]
    n_rows = len(rows)
    n_cols = max(counts) if counts else 0
    if n_cols < 1:
        return {"score": 0.0, "n_rows": n_rows, "n_cols": 0, "fill": 0.0, "stab": 0.0}
    non_empty = sum(1 for r in rows for c in r if c.strip())
    total_cells = sum(counts)
    fill = non_empty / float(total_cells) if total_cells else 0.0
    mean_c = sum(counts) / len(counts)
    var = sum((c - mean_c) ** 2 for c in counts) / len(counts)
    stab = max(0.0, 1.0 - (var ** 0.5) / max(mean_c, 1.0))
    size_bonus = min(1.0, (n_rows * n_cols) / 12.0)
    score = 0.45 * fill + 0.35 * stab + 0.20 * size_bonus
    score = max(0.0, min(1.0, score))
    return {"score": score, "n_rows": n_rows, "n_cols": n_cols,
            "fill": fill, "stab": stab}


def _first_table_html(raw):
    """Extract the first table HTML from a PP-Structure result list."""
    if not raw:
        return None
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("table", "Table"):
            continue
        res = item.get("res", {}) or {}
        html = ""
        if isinstance(res, dict):
            html = res.get("html", "") or ""
        html = html or item.get("html", "") or ""
        if html:
            return html
    return None


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

class _PPStructureBackendBase:
    """Shared PP-Structure engine lifecycle for ppstructure/gridboost backends."""

    name = "ppstructure"

    def __init__(self, lang="ch", show_log=False):
        self.lang = lang
        self.show_log = show_log
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from paddleocr import PPStructure
                self._engine = PPStructure(
                    show_log=self.show_log, lang=self.lang,
                    layout=False, table=True, ocr=True,
                )
            except ImportError:
                logger.warning("PP-Structure not available for table enhancement")
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to init PP-Structure enhancer: %s", exc)
                return None
        return self._engine

    def _recognize(self, image):
        engine = self._get_engine()
        if engine is None:
            return None
        try:
            raw = engine(image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PP-Structure recognition failed: %s", exc)
            return None
        return _first_table_html(raw)


class PPStructureBackend(_PPStructureBackendBase):
    """Baseline: run PP-Structure directly on the raw crop (plan-A v1 behavior)."""

    name = "ppstructure"

    def run(self, crop, region, region_blocks, offset_xy):
        return self._recognize(crop)


class GridBoostBackend(_PPStructureBackendBase):
    """gridboost: decolor/binarize + virtual grid lines, then PP-Structure."""

    name = "gridboost"

    def run(self, crop, region, region_blocks, offset_xy):
        try:
            processed = gridboost_preprocess(crop, region_blocks, offset_xy)
        except Exception as exc:  # noqa: BLE001
            logger.warning("gridboost preprocess failed (%s); using raw crop", exc)
            processed = crop
        return self._recognize(processed)


class VisionLocalBackend:
    """Offline local vision model backend (vision tier).

    Placeholder for high-capability hosts: on the current CPU-only host this
    tier is never selected. run() returns None (graceful no-op) until a local
    VLM integration is wired in, so selecting this backend degrades safely.
    """

    name = "vision_local"

    def __init__(self, config=None):
        self.config = config or {}

    def run(self, crop, region, region_blocks, offset_xy):
        logger.info("vision_local backend not implemented on this host; skipping")
        return None


# --------------------------------------------------------------------------- #
# TableEnhancer: crops regions, runs the selected backend, scores + gates.
# --------------------------------------------------------------------------- #

class TableEnhancer:
    """Crop low-confidence table regions, re-recognize via the tier's backend,
    score the result (S_e), and return review-only enhanced tables."""

    def __init__(self, config: Dict[str, Any], tier: Optional[str] = None):
        self.logger = logging.getLogger("ocr_pipeline")
        self.config = config
        ocr_cfg = config.get("ocr", {}) or {}
        ts = ocr_cfg.get("table_structure", {}) or {}
        self.enabled = bool(ts.get("enhance_on_low_quality", False))
        self.lang = ts.get("lang", (ocr_cfg.get("paddleocr", {}) or {}).get("lang", "ch"))
        self.show_log = bool(ts.get("show_log", False))
        self.scale = int(((config.get("image", {}) or {}).get("super_resolution", {}) or {}).get("scale", 2)) or 1
        self.pad = int(ts.get("crop_pad", 14))
        self.max_regions = int(ts.get("max_enhance_regions", 4))
        # Tier resolution order: explicit config override > passed tier > gridboost.
        self.tier = ts.get("backend") or tier or "gridboost"
        self.adopt_margin = float(ts.get("enhance_adopt_margin", 0.1))
        self._backend = None

    def _get_backend(self):
        if self._backend is not None:
            return self._backend
        tier = self.tier
        if tier == "manual":
            self._backend = None
        elif tier == "vision" or tier == "vision_local":
            self._backend = VisionLocalBackend(self.config)
        elif tier == "ppstructure":
            self._backend = PPStructureBackend(lang=self.lang, show_log=self.show_log)
        else:  # gridboost (default) and any unknown tier fall back to gridboost
            self._backend = GridBoostBackend(lang=self.lang, show_log=self.show_log)
        return self._backend

    def enhance_regions(
        self, enhanced_image, regions: List[Dict[str, Any]],
        blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Return enhanced-table dicts for the given low-confidence regions.

        Args:
            enhanced_image: OCR-space (super-resolved) numpy BGR image.
            regions: [{"bbox": [x0,y0,x1,y1], "quality": float, "n_cols": int}, ...]
            blocks: full OCR blocks (used to draw virtual grid lines in gridboost).
        Returns:
            [{"html", "region", "engine", "backend", "score_enhanced",
              "score_base", "verdict"}]  (verdict is always "compare" this round)
        """
        if self.tier == "manual" or not self.enabled or enhanced_image is None or not regions:
            return []
        backend = self._get_backend()
        if backend is None:
            return []
        height, width = enhanced_image.shape[0], enhanced_image.shape[1]
        blocks = blocks or []
        results: List[Dict[str, Any]] = []
        for region in regions[: self.max_regions]:
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x0, y0, x1, y1 = [int(round(v)) for v in bbox]
            cx0 = max(x0 - self.pad, 0)
            cy0 = max(y0 - self.pad, 0)
            cx1 = min(x1 + self.pad, width)
            cy1 = min(y1 + self.pad, height)
            if cx1 - cx0 < 8 or cy1 - cy0 < 8:
                continue
            crop = enhanced_image[cy0:cy1, cx0:cx1]
            region_blocks = self._blocks_in_bbox(blocks, [cx0, cy0, cx1, cy1])
            html = backend.run(crop, region, region_blocks, (cx0, cy0))
            if not html:
                continue
            se = enhanced_quality(html)
            sb = float(region.get("quality", 0.0))
            results.append({
                "html": html,
                "region": region,
                "engine": backend.name,
                "backend": self.tier,
                "score_enhanced": round(se["score"], 3),
                "score_base": round(sb, 3),
                "verdict": "compare",  # review-only this round; adopt gate reserved
            })
        return results

    @staticmethod
    def _blocks_in_bbox(blocks, bbox):
        """Return OCR text blocks whose center falls inside bbox (OCR space)."""
        x0, y0, x1, y1 = bbox
        inside = []
        for b in blocks:
            if b.get("type") != "text":
                continue
            bb = b.get("bbox")
            if not bb:
                continue
            xs = [p[0] for p in bb]
            ys = [p[1] for p in bb]
            cx = (min(xs) + max(xs)) / 2
            cy = (min(ys) + max(ys)) / 2
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                inside.append(b)
        return inside

    # Backwards-compatible static helper retained for existing tests/imports.
    @staticmethod
    def _first_table_html(raw: Any) -> Optional[str]:
        return _first_table_html(raw)
