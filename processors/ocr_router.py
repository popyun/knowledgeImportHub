"""
OCR Engine Router.
Selects and runs the appropriate OCR engine based on content type.
"""

import logging
from typing import Any, Dict, List, Optional
from enum import Enum

from .base import BaseHandler


class ContentType(Enum):
    """Content type classification."""
    PURE_TEXT = "pure_text"
    SIMPLE_TABLE = "simple_table"
    COMPLEX_TABLE = "complex_table"
    MIXED = "mixed"
    MATH_SPECIAL = "math_special"


class OCRResult:
    """Unified OCR result structure."""
    
    def __init__(
        self,
        blocks: List[Dict[str, Any]],
        tables: List[Dict[str, Any]],
        confidence: float,
        engine_used: str,
        content_type: ContentType
    ):
        self.blocks = blocks  # [{type, text, confidence, bbox}]
        self.tables = tables  # [{html, cells, conf}]
        self.confidence = confidence
        self.engine_used = engine_used
        self.content_type = content_type
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "blocks": self.blocks,
            "tables": self.tables,
            "confidence": self.confidence,
            "engine_used": self.engine_used,
            "content_type": self.content_type.value
        }


class OCRRouter(BaseHandler):
    """Route OCR requests to appropriate engines."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize OCR router.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.logger = logging.getLogger("ocr_pipeline")
        
        # Engine configuration
        self.engines = config.get("ocr", {}).get("engines", {})
        self.mathpix_key = config.get("ocr", {}).get("mathpix_api_key", "")
        self.ollama_config = config.get("ocr", {}).get("ollama", {})
        self.paddle_config = config.get("ocr", {}).get("paddleocr", {})
        self.lang = self.paddle_config.get("lang", "ch")
        self.use_angle_cls = self.paddle_config.get("use_angle_cls", True)
        self.det_limit_side_len = self.paddle_config.get("det_limit_side_len", 1920)
        self.det_db_thresh = self.paddle_config.get("det_db_thresh", 0.2)
        self.det_db_box_thresh = self.paddle_config.get("det_db_box_thresh", 0.5)
        self.det_db_unclip_ratio = self.paddle_config.get("det_db_unclip_ratio", 1.8)
        self.drop_score = self.paddle_config.get("drop_score", 0.35)
        self.table_structure_config = config.get("ocr", {}).get("table_structure", {})
        self.table_structure_enabled = self.table_structure_config.get("enabled", True)
        self.table_structure_lang = self.table_structure_config.get("lang", self.lang)
        self.table_structure_show_log = self.table_structure_config.get("show_log", False)
        self.table_structure_recovery = self.table_structure_config.get("recovery", True)
        
        # Initialize PaddleOCR (default engine)
        self._paddleocr = None
        self._ppstructure = None
    
    def _get_paddleocr(self):
        """Lazy initialization of PaddleOCR."""
        if self._paddleocr is None:
            try:
                from paddleocr import PaddleOCR
                self._paddleocr = PaddleOCR(
                    use_angle_cls=self.use_angle_cls,
                    lang=self.lang,
                    show_log=False,
                    det_limit_side_len=self.det_limit_side_len,
                    det_db_thresh=self.det_db_thresh,
                    det_db_box_thresh=self.det_db_box_thresh,
                    det_db_unclip_ratio=self.det_db_unclip_ratio,
                    drop_score=self.drop_score,
                )
            except ImportError:
                self.logger.warning("PaddleOCR not available")
                return None
        return self._paddleocr
    
    def _get_ppstructure(self):
        """Lazy initialization of PaddleOCR PP-Structure table engine."""
        if not self.table_structure_enabled:
            return None
        if self._ppstructure is None:
            try:
                from paddleocr import PPStructure
                self._ppstructure = PPStructure(
                    show_log=self.table_structure_show_log,
                    lang=self.table_structure_lang,
                    recovery=self.table_structure_recovery,
                )
            except ImportError:
                self.logger.warning("PP-Structure not available")
                return None
            except Exception as e:
                self.logger.warning(f"Failed to initialize PP-Structure: {e}")
                return None
        return self._ppstructure


    def classify_content(self, image_path: str) -> ContentType:
        """
        Classify image content type using layout analysis.
        
        Args:
            image_path: Path to image
            
        Returns:
            ContentType enum value
        """
        # Use PaddleOCR layout analysis if available
        ocr = self._get_paddleocr()
        
        if ocr is None:
            # Fallback: assume mixed content
            return ContentType.MIXED
        
        try:
            result = ocr.ocr(image_path, cls=True)
            
            if not result or not result[0]:
                return ContentType.PURE_TEXT
            
            # Analyze structure
            blocks = result[0]
            
            # Simple heuristic: check for grid-like structure
            # In production, use PaddleOCR layout model
            has_table_structure = self._detect_table_structure(blocks)
            
            if has_table_structure:
                # Check complexity
                if self._is_complex_table(blocks):
                    return ContentType.COMPLEX_TABLE
                else:
                    return ContentType.SIMPLE_TABLE
            
            # Check for math/special characters
            if self._has_math_content(blocks):
                return ContentType.MATH_SPECIAL
            
            return ContentType.PURE_TEXT
            
        except Exception as e:
            self.logger.error(f"Content classification failed: {e}")
            return ContentType.MIXED
    
    def _box_center(self, box: List) -> tuple:
        """Return center x/y and width/height for a PaddleOCR box."""
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        return (
            (min(xs) + max(xs)) / 2,
            (min(ys) + max(ys)) / 2,
            max(xs) - min(xs),
            max(ys) - min(ys),
        )

    def _cluster_positions(self, values: List[float], tolerance: float) -> List[List[float]]:
        """Cluster sorted coordinate values by distance tolerance."""
        if not values:
            return []
        groups = [[sorted(values)[0]]]
        for value in sorted(values)[1:]:
            current = groups[-1]
            if abs(value - (sum(current) / len(current))) <= tolerance:
                current.append(value)
            else:
                groups.append([value])
        return groups


    def _detect_table_structure(self, blocks: List) -> bool:
        """Detect table-like layouts using adaptive row/column clustering."""
        if len(blocks) < 4:
            return False

        metrics = [self._box_center(block[0]) for block in blocks]
        heights = [height for _x, _y, _w, height in metrics if height > 0]
        widths = [width for _x, _y, width, _h in metrics if width > 0]
        if not heights or not widths:
            return False

        median_height = sorted(heights)[len(heights) // 2]
        median_width = sorted(widths)[len(widths) // 2]
        row_tolerance = max(median_height * 0.7, 8)
        col_tolerance = max(median_width * 0.5, 12)

        row_groups = self._cluster_positions([y for _x, y, _w, _h in metrics], row_tolerance)
        col_groups = self._cluster_positions([x for x, _y, _w, _h in metrics], col_tolerance)

        rows_with_multiple = sum(1 for group in row_groups if len(group) >= 2)
        columns_with_multiple = sum(1 for group in col_groups if len(group) >= 2)
        rectangular_density = len(blocks) / max(len(row_groups) * len(col_groups), 1)

        return (
            len(row_groups) >= 2
            and len(col_groups) >= 2
            and rows_with_multiple >= 2
            and columns_with_multiple >= 2
            and rectangular_density >= 0.35
        )
    
    def _is_complex_table(self, blocks: List) -> bool:
        """Check if table has complex structure (merged cells, etc.)."""
        # Simplified: check for varying cell sizes
        if len(blocks) < 6:
            return False
        
        areas = []
        for block in blocks:
            box = block[0]
            w = box[1][0] - box[0][0]
            h = box[2][1] - box[0][1]
            areas.append(w * h)
        
        # High variance in cell sizes suggests complex structure
        avg_area = sum(areas) / len(areas)
        variance = sum((a - avg_area) ** 2 for a in areas) / len(areas)
        
        return variance > (avg_area * 0.5)
    
    def _has_math_content(self, blocks: List) -> bool:
        """Check for mathematical/special characters."""
        special_chars = set("+-=*/^_{}[]()<>|" + "\\u2211\\u222b\\u221a\\u2260\\u2248\\u2264\\u2265\\u00b1\\u00d7\\u00f7\\u03c0\\u03b1\\u03b2\\u03b3\\u03b8".encode("ascii").decode("unicode_escape"))
        
        for block in blocks:
            text = block[1][0] if len(block) > 1 else ""
            if any(c in text for c in special_chars):
                return True
        
        return False
    
    def process(self, image_path: str, content_type: Optional[ContentType] = None) -> OCRResult:
        """
        Process image with appropriate OCR engine.
        
        Args:
            image_path: Path to image
            content_type: Pre-determined content type (optional)
            
        Returns:
            OCRResult object
        """
        # Classify content if not provided
        if content_type is None:
            content_type = self.classify_content(image_path)
        
        # Select engine based on content type
        engine = self._select_engine(content_type)
        
        # Run OCR
        try:
            if engine == "paddleocr_vl":
                return self._run_paddleocr(image_path, content_type)
            elif engine == "mineru":
                return self._run_mineru(image_path, content_type)
            elif engine == "mathpix_api":
                return self._run_mathpix(image_path, content_type)
            else:
                # Fallback to PaddleOCR
                return self._run_paddleocr(image_path, content_type)
        except Exception as e:
            self.logger.error(f"OCR failed with {engine}: {e}")
            # Try fallback
            if engine != "paddleocr_vl":
                return self._run_paddleocr(image_path, content_type)
            raise
    
    def _select_engine(self, content_type: ContentType) -> str:
        """Select OCR engine based on content type."""
        engine_map = {
            ContentType.PURE_TEXT: self.engines.get("text_default", "paddleocr_vl"),
            ContentType.SIMPLE_TABLE: self.engines.get("text_default", "paddleocr_vl"),
            ContentType.COMPLEX_TABLE: self.engines.get("table_complex", "mineru"),
            ContentType.MIXED: self.engines.get("text_default", "paddleocr_vl"),
            ContentType.MATH_SPECIAL: self.engines.get("math_special", "mathpix_api")
        }
        
        selected = engine_map.get(content_type, "paddleocr_vl")
        
        # Check Mathpix availability
        if selected == "mathpix_api" and not self.mathpix_key:
            self.logger.warning("Mathpix API key not configured, falling back to PaddleOCR")
            return "paddleocr_vl"
        
        return selected
    
    def _run_table_structure(self, image_path: str) -> List[Dict[str, Any]]:
        """Run PP-Structure and return normalized table dictionaries."""
        engine = self._get_ppstructure()
        if engine is None:
            return []
        try:
            raw_results = engine(image_path)
        except Exception as e:
            self.logger.warning(f"PP-Structure table extraction failed: {e}")
            return []
        return self._parse_ppstructure_tables(raw_results)

    def _parse_ppstructure_tables(self, raw_results: Any) -> List[Dict[str, Any]]:
        """Normalize PP-Structure output to OCRResult table dictionaries."""
        tables = []
        if not raw_results:
            return tables
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"table", "Table"}:
                continue
            res = item.get("res", {}) or {}
            html = res.get("html") or item.get("html") or ""
            if not html:
                continue
            cells = res.get("cell_bbox") or res.get("cells") or []
            confidence = item.get("confidence") or res.get("confidence") or 1.0
            tables.append({
                "html": html,
                "cells": cells,
                "bbox": item.get("bbox"),
                "confidence": float(confidence) if isinstance(confidence, (int, float)) else 1.0,
                "engine": "ppstructure",
            })
        return tables


    def _run_paddleocr(self, image_path: str, content_type: ContentType) -> OCRResult:
        """Run PaddleOCR on image."""
        ocr = self._get_paddleocr()
        
        if ocr is None:
            raise RuntimeError("PaddleOCR not available")
        
        result = ocr.ocr(image_path, cls=True)
        
        blocks = []
        total_confidence = 0
        
        if result and result[0]:
            for block in result[0]:
                box = block[0]
                text = block[1][0] if len(block) > 1 else ""
                conf = block[1][1] if len(block) > 1 and len(block[1]) > 1 else 0.5
                
                blocks.append({
                    "type": "text",
                    "text": text,
                    "confidence": conf,
                    "bbox": box
                })
                total_confidence += conf
        
        avg_confidence = total_confidence / len(blocks) if blocks else 0
        tables = self._run_table_structure(image_path)
        
        return OCRResult(
            blocks=blocks,
            tables=tables,
            confidence=avg_confidence,
            engine_used="paddleocr_vl",
            content_type=content_type
        )
    
    def _run_mineru(self, image_path: str, content_type: ContentType) -> OCRResult:
        """Run MinerU on image (placeholder)."""
        # In production, call magic-pdf via subprocess
        self.logger.info("MinerU engine called (placeholder implementation)")
        
        # Fallback to PaddleOCR for now
        return self._run_paddleocr(image_path, content_type)
    
    def _run_mathpix(self, image_path: str, content_type: ContentType) -> OCRResult:
        """Run Mathpix API on image (placeholder)."""
        # In production, call Mathpix API
        self.logger.info("Mathpix API called (placeholder implementation)")
        
        # Fallback to PaddleOCR for now
        return self._run_paddleocr(image_path, content_type)
