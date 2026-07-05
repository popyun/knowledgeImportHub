"""
Image handler - orchestrates the complete image processing pipeline.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import cv2

from .base import BaseHandler
from .preprocessor import ImagePreprocessor
from .color_extractor import ColorExtractor
from .ocr_router import OCRRouter, ContentType
from .post_corrector import PostCorrector
from .table_builder import TableBuilder
from .markdown_generator import MarkdownGenerator
from .table_enhancer import TableEnhancer
from .host_profiler import load_or_create_profile, missing_vision_requirements
from utils.file_utils import get_temp_file

class ImageHandler(BaseHandler):
    """Orchestrate complete image processing pipeline."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize image handler.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.logger = logging.getLogger("ocr_pipeline")
        
        # Initialize processors
        self.preprocessor = ImagePreprocessor(config)
        self.color_extractor = ColorExtractor(config)
        self.ocr_router = OCRRouter(config)
        self.post_corrector = PostCorrector(config)
        self.table_builder = TableBuilder(config)
        self.markdown_generator = MarkdownGenerator(config)
        # Plan A tiered enhancement: detect host capability once (cached),
        # map to an enhancement tier, and build the matching backend.
        self.host_profile = load_or_create_profile(base_dir=".")
        tier = self.host_profile.get("tier")
        self._maybe_prompt_vision_install(self.host_profile)
        self.table_enhancer = TableEnhancer(config, tier=tier)
        self.logger.info(
            "Enhancement tier=%s (%s); plan A %s",
            self.table_enhancer.tier,
            self.host_profile.get("reason", ""),
            "ON" if self.table_enhancer.enabled else "OFF",
        )
    
    def initialize(self) -> bool:
        """Initialize all processors."""
        try:
            self.preprocessor.initialize()
            self.ocr_router.initialize()
            self._initialized = True
            self.logger.info("ImageHandler initialized")
            return True
        except Exception as e:
            self.logger.error(f"ImageHandler initialization failed: {e}")
            return False
    
    def process(self, image_path: str) -> Dict[str, Any]:
        """
        Process a single image through the complete pipeline.
        
        Args:
            image_path: Path to input image
            
        Returns:
            Processing result dictionary
        """
        self.logger.info(f"Processing image: {image_path}")
        
        result = {
            "success": False,
            "image_path": image_path,
            "output_markdown": None,
            "ocr_result": None,
            "error": None
        }
        
        temp_ocr_path = None

        try:
            # Step 1: Preprocess image
            self.logger.debug("Step 1: Preprocessing")
            prep_result = self.preprocessor.process(image_path)
            enhanced_image = prep_result["final_image"]
            corrected_image = prep_result["corrected_image"]
            color_info = prep_result["color_info"]

            temp_ocr_path = get_temp_file(suffix=".png", prefix="ocr_enhanced_")
            if not cv2.imwrite(temp_ocr_path, enhanced_image):
                raise RuntimeError(f"Failed to write enhanced OCR image: {temp_ocr_path}")
            
            # Step 2: Classify content and run OCR
            self.logger.debug("Step 2: OCR processing")
            content_type = self.ocr_router.classify_content(temp_ocr_path)
            ocr_result = self.ocr_router.process(temp_ocr_path, content_type)
            
            # Step 3: Post-correction
            self.logger.debug("Step 3: Post-correction")
            corrected_result = self.post_corrector.process(
                ocr_result.to_dict(),
                content_type.value
            )
            
            # Step 4: Build tables with colors
            self.logger.debug("Step 4: Table building")
            blocks = corrected_result.get("blocks", [])
            tables = corrected_result.get("tables", [])
            if tables:
                self.logger.debug("Using PP-Structure table output")
            else:
                table_regions = self._infer_table_regions(blocks, content_type)
                color_maps = self.color_extractor.process(corrected_image, table_regions) if table_regions else []
                tables = self.table_builder.process(
                    blocks,
                    color_maps
                )
            corrected_result["tables"] = tables
            
            # Step 5: Generate Markdown
            self.logger.debug("Step 5: Markdown generation")
            markdown = self.markdown_generator.process(
                corrected_result,
                image_path,
                link_candidates=[]  # Would come from entity linker
            )

            # Step 5b (plan A): re-recognize low-confidence table regions with
            # PP-Structure when enabled. Enhanced results are persisted on the
            # OCR result (so cached re-runs render them) and attached as a
            # review-only supplement; the plan-B main output is never replaced.
            if self.table_enhancer.enabled:
                regions = list(self.markdown_generator._low_quality_regions)
                if regions:
                    self.logger.debug("Step 5b: enhancing %d low-confidence region(s)", len(regions))
                    enhanced_tables = self.table_enhancer.enhance_regions(
                        enhanced_image, regions, blocks=corrected_result.get("blocks", [])
                    )
                    if enhanced_tables:
                        corrected_result["enhanced_tables"] = enhanced_tables
                        markdown = self.markdown_generator.process(
                            corrected_result,
                            image_path,
                            link_candidates=[]
                        )
            
            # Populate result
            result["success"] = True
            result["output_markdown"] = markdown
            result["ocr_result"] = corrected_result
            result["confidence"] = corrected_result.get("confidence", 0)
            result["content_type"] = content_type.value
            
            self.logger.info(f"Successfully processed: {image_path}")
            
        except Exception as e:
            self.logger.error(f"Processing failed for {image_path}: {e}")
            result["error"] = str(e)
        finally:
            if temp_ocr_path and os.path.exists(temp_ocr_path):
                try:
                    os.remove(temp_ocr_path)
                except OSError as cleanup_error:
                    self.logger.debug(f"Failed to remove temp OCR image {temp_ocr_path}: {cleanup_error}")
        
        return result
    
    def _maybe_prompt_vision_install(self, profile):
        """If the host has vision-model potential but is missing software/
        model weights, ask the user whether to install (interactive only).

        Non-interactive runs (batch/CI, no TTY) never block: they just log
        the missing pieces and stay on the degraded tier that the profiler
        already selected. This method never changes the tier by itself; it
        only surfaces an install hint.
        """
        try:
            if not profile.get("vision_potential"):
                return
            missing = profile.get("missing_vision_requirements") or []
            if not missing:
                return
            hint = (
                "Host can run an offline vision model but is missing: "
                + "; ".join(missing)
                + ". Install e.g. `ollama pull qwen2.5vl` (several GB) to enable "
                + "the 'vision' enhancement tier."
            )
            import sys
            interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
            if not interactive:
                self.logger.info("%s (non-interactive: staying on '%s' tier)",
                                 hint, profile.get("tier"))
                return
            self.logger.info(hint)
            try:
                answer = input(hint + " Install now? [y/N]: ").strip().lower()
            except (EOFError, OSError):
                return
            if answer in ("y", "yes"):
                self.logger.info(
                    "Run the install command above, then re-run with --rescan "
                    "to pick up the 'vision' tier."
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("vision install prompt skipped: %s", exc)

    def _infer_table_regions(
        self,
        blocks: List[Dict[str, Any]],
        content_type: ContentType
    ) -> List[Dict[str, Any]]:
        """Infer table regions only for grid-like OCR block groups."""
        if content_type not in {ContentType.SIMPLE_TABLE, ContentType.COMPLEX_TABLE, ContentType.MIXED}:
            return []
        table_groups = self.table_builder._group_blocks_into_tables(blocks)
        regions = []
        for group in table_groups:
            boxes = [block.get("bbox") for block in group if block.get("bbox")]
            if not boxes:
                continue
            xs = [point[0] for box in boxes for point in box]
            ys = [point[1] for box in boxes for point in box]
            min_x, max_x = int(max(min(xs), 0)), int(max(xs))
            min_y, max_y = int(max(min(ys), 0)), int(max(ys))
            padding = 8
            regions.append({
                "bbox": (
                    max(min_x - padding, 0),
                    max(min_y - padding, 0),
                    max(max_x - min_x, 1) + padding * 2,
                    max(max_y - min_y, 1) + padding * 2,
                )
            })
        return regions



    def cleanup(self) -> None:
        """Clean up all processors."""
        self.preprocessor.cleanup()
        self.ocr_router.cleanup()
        self.post_corrector.cleanup()
        self._initialized = False
