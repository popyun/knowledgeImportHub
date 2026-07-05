"""
Table enhancer (plan A): optional PP-Structure re-recognition for
low-confidence table regions detected by the geometric reconstructor (plan B).

Design constraints (verified on the slide corpus):
  - PP-Structure whole-page layout mis-classifies colored slides as a single
    "figure", so we never run it on the full page. We only crop the specific
    low-confidence table region reported by plan B and run PP-Structure in
    table-only mode on that crop.
  - On this corpus PP-Structure does NOT reliably beat plan B, so the enhanced
    result is attached as a review-only ("supplementary") block under the
    low-confidence warning; it never replaces the plan B main output.
  - Disabled by default (each call costs several seconds). Enable via config
    ocr.table_structure.enhance_on_low_quality: true.
"""

import logging
from typing import Any, Dict, List, Optional


class TableEnhancer:
    """Crop low-confidence table regions and re-recognize them via PP-Structure."""

    def __init__(self, config: Dict[str, Any]):
        self.logger = logging.getLogger("ocr_pipeline")
        ts = (config.get("ocr", {}) or {}).get("table_structure", {}) or {}
        self.enabled = bool(ts.get("enhance_on_low_quality", False))
        self.lang = ts.get("lang", (config.get("ocr", {}) or {}).get("paddleocr", {}).get("lang", "ch"))
        self.show_log = bool(ts.get("show_log", False))
        self.scale = int(((config.get("image", {}) or {}).get("super_resolution", {}) or {}).get("scale", 2)) or 1
        self.pad = int(ts.get("crop_pad", 14))
        self.max_regions = int(ts.get("max_enhance_regions", 4))
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from paddleocr import PPStructure
                self._engine = PPStructure(
                    show_log=self.show_log,
                    lang=self.lang,
                    layout=False,
                    table=True,
                    ocr=True,
                )
            except ImportError:
                self.logger.warning("PP-Structure not available for table enhancement")
                return None
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Failed to init PP-Structure enhancer: %s", exc)
                return None
        return self._engine

    def enhance_regions(
        self, enhanced_image, regions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Return enhanced-table dicts for the given low-confidence regions.

        Args:
            enhanced_image: the OCR-space (super-resolved) image as a numpy BGR array.
            regions: list of {"bbox": [x0, y0, x1, y1], "quality": float, "n_cols": int}
                     in OCR/enhanced coordinate space.
        Returns:
            list of {"html", "markdown_hint": None, "region": {...}, "engine": "ppstructure"}
        """
        if not self.enabled or enhanced_image is None or not regions:
            return []
        engine = self._get_engine()
        if engine is None:
            return []
        height, width = enhanced_image.shape[0], enhanced_image.shape[1]
        results: List[Dict[str, Any]] = []
        for region in regions[: self.max_regions]:
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x0, y0, x1, y1 = [int(round(v)) for v in bbox]
            x0 = max(x0 - self.pad, 0)
            y0 = max(y0 - self.pad, 0)
            x1 = min(x1 + self.pad, width)
            y1 = min(y1 + self.pad, height)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            crop = enhanced_image[y0:y1, x0:x1]
            try:
                raw = engine(crop)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("PP-Structure enhance failed on region %s: %s", bbox, exc)
                continue
            html = self._first_table_html(raw)
            if not html:
                continue
            results.append({
                "html": html,
                "region": region,
                "engine": "ppstructure",
            })
        return results

    @staticmethod
    def _first_table_html(raw: Any) -> Optional[str]:
        if not raw:
            return None
        for item in raw:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"table", "Table"}:
                continue
            res = item.get("res", {}) or {}
            html = ""
            if isinstance(res, dict):
                html = res.get("html", "") or ""
            html = html or item.get("html", "") or ""
            if html:
                return html
        return None