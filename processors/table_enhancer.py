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

import base64
import logging
import re
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


class PPStructureV3Backend:
    """PP-StructureV3 backend (paddleocr 3.x): re-recognize a low-confidence
    table crop with the newer layout+table pipeline.

    Enabled only via config ``backend: "ppstructurev3"`` (never auto-selected by
    the host profiler). PP-StructureV3 needs paddleocr 3.x. Because upgrading
    the main environment to paddle 3.x would swap the text-OCR models for the
    whole pipeline (PP-OCRv4 -> v5) and change existing outputs, this backend
    runs the model in an ISOLATED paddleocr-3.x python via subprocess by
    default (config ``ppstructurev3_python``), keeping the main interpreter on
    2.7.3. If paddleocr 3.x is importable in-process (a future upgraded env),
    it runs in-process instead. Any failure returns None so the enhancer
    degrades safely to the plan-B main output.

    PP-StructureV3 emits markdown with an embedded HTML ``<table>``; we extract
    the first table so downstream scoring/rendering consumes it like the other
    backends.
    """

    name = "ppstructurev3"

    def __init__(self, lang="ch", show_log=False, python_exe=None):
        self.lang = lang
        self.show_log = show_log
        # Path to a paddleocr-3.x python; when set (and paddleocr 3.x is not
        # importable in-process) the backend runs via subprocess isolation.
        self.python_exe = python_exe
        self._engine = None
        self._inproc_ok = None  # tri-state: None=unknown, True/False after probe

    def _can_run_inproc(self):
        if self._inproc_ok is None:
            try:
                import os as _os
                _os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
                from paddleocr import PPStructureV3  # noqa: F401
                self._inproc_ok = True
            except Exception:  # noqa: BLE001
                self._inproc_ok = False
        return self._inproc_ok

    def _get_engine(self):
        if self._engine is None:
            try:
                import os as _os
                _os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
                from paddleocr import PPStructureV3
                self._engine = PPStructureV3(device="cpu")
            except ImportError:
                logger.warning("PP-StructureV3 not available (needs paddleocr 3.x)")
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to init PP-StructureV3 enhancer: %s", exc)
                return None
        return self._engine

    @staticmethod
    def _extract_table_html(results):
        """Pull the first <table>...</table> out of PP-StructureV3 results.

        V3 exposes per-page markdown (``res.markdown`` / ``markdown['markdown_texts']``)
        that embeds HTML tables; we regex the first table out of whatever text
        representation we can reach, falling back to the JSON dump.
        """
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
            except Exception:  # noqa: BLE001
                pass
            for text in texts:
                if not text:
                    continue
                m = re.search(r"<table.*?</table>", text, re.IGNORECASE | re.DOTALL)
                if m:
                    return m.group(0)
        return None

    def _run_inproc(self, crop):
        engine = self._get_engine()
        if engine is None:
            return None
        try:
            results = engine.predict(crop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PP-StructureV3 recognition failed: %s", exc)
            return None
        return self._extract_table_html(results)

    def _run_subprocess(self, crop):
        """Write the crop to a temp PNG and recognize it in an isolated
        paddleocr-3.x python via the committed worker script."""
        import json as _json
        import os as _os
        import subprocess
        import tempfile
        try:
            import cv2
        except Exception as exc:  # noqa: BLE001
            logger.warning("PP-StructureV3 subprocess needs cv2 (%s)", exc)
            return None
        worker = _os.path.join(_os.path.dirname(__file__), "_ppstructurev3_worker.py")
        if not _os.path.exists(worker):
            logger.warning("PP-StructureV3 worker script missing: %s", worker)
            return None
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".png", prefix="ppv3_")
            _os.close(fd)
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                return None
            buf.tofile(tmp)  # handles unicode temp paths
            proc = subprocess.run(
                [self.python_exe, worker, tmp],
                capture_output=True, timeout=900,
            )
            out = (proc.stdout or b"").decode("utf-8", "replace").strip()
            if not out:
                logger.warning("PP-StructureV3 worker: empty output (rc=%s) %s",
                               proc.returncode,
                               (proc.stderr or b"").decode("utf-8", "replace")[-300:])
                return None
            data = _json.loads(out)
            if data.get("error"):
                logger.warning("PP-StructureV3 worker error: %s", data["error"])
            return data.get("html")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PP-StructureV3 subprocess failed: %s", exc)
            return None
        finally:
            if tmp and _os.path.exists(tmp):
                try:
                    _os.remove(tmp)
                except OSError:
                    pass

    def run(self, crop, region, region_blocks, offset_xy):
        if self._can_run_inproc():
            return self._run_inproc(crop)
        if self.python_exe:
            return self._run_subprocess(crop)
        logger.warning(
            "PP-StructureV3 unavailable: paddleocr 3.x not importable and no "
            "ppstructurev3_python configured; skipping (plan-B output kept)."
        )
        return None


class VisionLocalBackend:
    """Offline local vision model backend (vision tier).

    Sends the cropped low-confidence region to a locally hosted vision model
    (via the ollama HTTP API, e.g. ``qwen2.5vl:3b``) and asks it to transcribe
    the region as a single HTML ``<table>``. Used only on hosts the profiler
    tags as vision-capable. If the model / server is unavailable or the reply
    is not a usable table, run() returns None so the enhancer degrades safely
    (plan B main output is untouched; enabling this can only add information).
    """

    name = "vision_local"

    # Ask the model for HTML only so downstream _html_table_to_markdown can
    # consume it exactly like the PP-Structure backends.
    _PROMPT = (
        "You are an OCR table transcriber. The image is a cropped region from a "
        "Chinese financial slide that contains ONE table (it may be borderless "
        "or colored). Transcribe it faithfully into a single HTML <table>.\n"
        "Rules:\n"
        "- Output ONLY the HTML <table>...</table>. No prose, no markdown, no code fences.\n"
        "- Preserve the original row/column layout; one <tr> per visual row, "
        "one <td> per cell. Keep empty cells as <td></td>.\n"
        "- Keep numbers, Chinese text and symbols exactly as shown; do not "
        "translate, summarize, reorder, or invent cells.\n"
        "- Do not merge separate columns into one cell."
    )

    def __init__(self, config=None):
        self.config = config or {}
        ocr_cfg = (self.config.get("ocr", {}) or {})
        ollama_cfg = (ocr_cfg.get("ollama", {}) or {})
        ts_cfg = (ocr_cfg.get("table_structure", {}) or {})
        self.endpoint = ollama_cfg.get("endpoint", "http://localhost:11434")
        # vision_model may live under table_structure (backend-specific) or
        # ollama; fall back to the common local VLM tag.
        self.model = (
            ts_cfg.get("vision_model")
            or ollama_cfg.get("vision_model")
            or "qwen2.5vl:3b"
        )
        self.timeout = int(ts_cfg.get("vision_timeout", 180))

    def _encode_png(self, crop):
        """Return base64 PNG of the BGR crop, or None on failure."""
        try:
            import cv2
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                return None
            return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision_local: failed to encode crop (%s)", exc)
            return None

    @staticmethod
    def _extract_table_html(text):
        """Pull the first <table>...</table> out of a model reply."""
        if not text:
            return None
        # Strip code fences if the model wrapped the answer.
        text = text.replace("```html", "").replace("```", "")
        m = re.search(r"<table.*?</table>", text, re.IGNORECASE | re.DOTALL)
        return m.group(0) if m else None

    def run(self, crop, region, region_blocks, offset_xy):
        img_b64 = self._encode_png(crop)
        if img_b64 is None:
            return None
        try:
            import requests
            resp = requests.post(
                self.endpoint.rstrip("/") + "/api/generate",
                json={
                    "model": self.model,
                    "prompt": self._PROMPT,
                    "images": [img_b64],
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 2048},
                },
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision_local: ollama request failed (%s); skipping", exc)
            return None
        if resp.status_code != 200:
            logger.warning("vision_local: ollama status %s; skipping", resp.status_code)
            return None
        try:
            reply = resp.json().get("response", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision_local: bad ollama JSON (%s); skipping", exc)
            return None
        html = self._extract_table_html(reply)
        if not html:
            logger.info("vision_local: no <table> in model reply; skipping")
            return None
        return html


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
        # Path to a paddleocr-3.x python for the isolated PP-StructureV3 backend
        # (empty => run in-process if paddleocr 3.x is importable, else skip).
        self.ppstructurev3_python = ts.get("ppstructurev3_python", "") or None
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
        elif tier == "ppstructurev3":
            self._backend = PPStructureV3Backend(
                lang=self.lang, show_log=self.show_log,
                python_exe=self.ppstructurev3_python,
            )
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
            # Applicability pre-check: PP-StructureV3 is slow (minutes on CPU),
            # so only pay the cost when the region looks like a regular ruled
            # table. Flowcharts / ultra-dense matrices are skipped, keeping the
            # plan-B "> [!warning] human review" fallback. Other backends are
            # unaffected by this gate.
            if self.tier == "ppstructurev3":
                fit = self._v3_applicable(region, region_blocks)
                if not fit.get("applicable"):
                    self.logger.debug(
                        "PP-StructureV3 skipped region (%s)", fit.get("reason")
                    )
                    continue
            html = backend.run(crop, region, region_blocks, (cx0, cy0))
            if not html:
                continue
            se = enhanced_quality(html)
            sb = float(region.get("quality", 0.0))
            item = {
                "html": html,
                "region": region,
                "engine": backend.name,
                "backend": self.tier,
                "score_enhanced": round(se["score"], 3),
                "score_base": round(sb, 3),
                "verdict": "compare",  # review-only this round; adopt gate reserved
            }
            if self.tier == "ppstructurev3":
                item["applicable"] = self._v3_applicable(region, region_blocks)
            results.append(item)
        return results

    # Dense-matrix / flowchart guard rails for PP-StructureV3 applicability.
    _V3_MIN_ROWS = 3
    _V3_MIN_COLS = 2
    _V3_MAX_COLS = 12          # beyond this it is an ultra-dense matrix
    _V3_ROW_STAB_MIN = 0.55    # per-row column-count consistency

    def _v3_applicable(self, region, region_blocks):
        """Structure-cue pre-check: is this low-confidence region a regular
        ruled table (worth a slow PP-StructureV3 pass), or a flowchart /
        ultra-dense matrix (skip -> human review)?

        Uses only geometry already available from OCR blocks: cluster word-box
        centers into rows/columns and require enough rows, a sane column count,
        and stable per-row column counts. Returns a dict with the decision plus
        its component metrics for auditability.
        """
        blocks = [b for b in (region_blocks or []) if b.get("bbox")]
        if len(blocks) < self._V3_MIN_ROWS:
            return {"applicable": False, "reason": "too few text boxes",
                    "n_rows": 0, "n_cols": 0, "row_stab": 0.0}

        ys, xs, heights = [], [], []
        for b in blocks:
            bb = b["bbox"]
            yy = [p[1] for p in bb]
            xx = [p[0] for p in bb]
            ys.append((min(yy) + max(yy)) / 2.0)
            xs.append((min(xx) + max(xx)) / 2.0)
            heights.append(max(yy) - min(yy))
        med_h = sorted(heights)[len(heights) // 2] or 1.0

        rows = self._cluster_1d(ys, gap=max(med_h * 0.8, 8.0))
        cols = self._cluster_1d(xs, gap=max(med_h * 1.2, 12.0))
        n_rows = len(rows)
        n_cols = len(cols)

        # Assign each block to its nearest column center to measure per-row
        # column-count stability (regular tables fill columns consistently).
        col_centers = [sum(c) / len(c) for c in cols] if cols else []
        row_members = self._assign_rows(blocks, ys, rows)
        per_row_cols = []
        for members in row_members:
            used = set()
            for idx in members:
                cx = xs[idx]
                if col_centers:
                    j = min(range(len(col_centers)), key=lambda k: abs(col_centers[k] - cx))
                    used.add(j)
            per_row_cols.append(len(used))
        if per_row_cols:
            mean_c = sum(per_row_cols) / len(per_row_cols)
            var = sum((c - mean_c) ** 2 for c in per_row_cols) / len(per_row_cols)
            row_stab = max(0.0, 1.0 - (var ** 0.5) / max(mean_c, 1.0))
        else:
            row_stab = 0.0

        metrics = {"n_rows": n_rows, "n_cols": n_cols, "row_stab": round(row_stab, 3)}
        if n_rows < self._V3_MIN_ROWS:
            return {"applicable": False, "reason": "too few rows", **metrics}
        if n_cols < self._V3_MIN_COLS:
            return {"applicable": False, "reason": "too few columns (not tabular)", **metrics}
        if n_cols > self._V3_MAX_COLS:
            return {"applicable": False, "reason": "ultra-dense matrix", **metrics}
        if row_stab < self._V3_ROW_STAB_MIN:
            return {"applicable": False, "reason": "irregular layout (flowchart?)", **metrics}
        return {"applicable": True, "reason": "regular ruled table", **metrics}

    @staticmethod
    def _cluster_1d(values, gap):
        """Cluster 1-D coordinates into groups separated by >= gap."""
        if not values:
            return []
        vals = sorted(values)
        clusters = [[vals[0]]]
        for v in vals[1:]:
            if v - clusters[-1][-1] > gap:
                clusters.append([v])
            else:
                clusters[-1].append(v)
        return clusters

    @staticmethod
    def _assign_rows(blocks, ys, rows):
        """Return, for each row cluster, the indices of blocks nearest to it."""
        row_centers = [sum(r) / len(r) for r in rows] if rows else []
        members = [[] for _ in row_centers]
        if not row_centers:
            return members
        for i in range(len(blocks)):
            j = min(range(len(row_centers)), key=lambda k: abs(row_centers[k] - ys[i]))
            members[j].append(i)
        return members

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
