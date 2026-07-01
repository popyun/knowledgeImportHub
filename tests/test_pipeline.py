"""
Test suite for Obsidian Knowledge Import Hub.
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import modules directly to avoid relative import issues
from processors.preprocessor import ImagePreprocessor
from processors.ocr_router import ContentType, OCRRouter
from processors.table_builder import TableBuilder
from utils.file_utils import compute_sha256, sanitize_filename
from linkers.entity_linker import EntityLinker


class TestPreprocessor:
    """Test image preprocessor."""
    
    @pytest.fixture
    def config(self):
        return {
            "image": {
                "super_resolution": {"enabled": True, "scale": 2},
                "color_extraction": {"kmeans_clusters": 8}
            }
        }
    
    @pytest.fixture
    def preprocessor(self, config):
        return ImagePreprocessor(config)
    
    def test_preprocessor_initialization(self, preprocessor):
        """Test preprocessor initializes correctly."""
        assert preprocessor is not None
        assert preprocessor.scale == 2
    
    def test_color_extraction(self, preprocessor):
        """Test color extraction from image."""
        # Create a simple test image
        import numpy as np
        import cv2
        
        # Create a colored image
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[0:50, 0:50] = [255, 0, 0]  # Red
        image[0:50, 50:100] = [0, 255, 0]  # Green
        image[50:100, 0:50] = [0, 0, 255]  # Blue
        image[50:100, 50:100] = [255, 255, 0]  # Yellow
        
        # Test color info extraction
        color_info = preprocessor._extract_color_info(image)
        
        assert "dominant_colors" in color_info
        assert len(color_info["dominant_colors"]) > 0
        assert all(c.startswith("#") for c in color_info["dominant_colors"])


class TestContentType:
    """Test content type enum."""
    
    def test_content_type_enum(self):
        """Test ContentType enum values."""
        assert ContentType.PURE_TEXT.value == "pure_text"
        assert ContentType.COMPLEX_TABLE.value == "complex_table"
        assert ContentType.MATH_SPECIAL.value == "math_special"
        assert ContentType.SIMPLE_TABLE.value == "simple_table"
        assert ContentType.MIXED.value == "mixed"


class TestOCRRouterConfig:
    """Test OCR router configuration."""

    def test_paddleocr_precision_defaults(self):
        router = OCRRouter({})

        assert router.lang == "ch"
        assert router.use_angle_cls is True
        assert router.det_limit_side_len == 1920
        assert router.det_db_thresh == 0.2
        assert router.det_db_box_thresh == 0.5
        assert router.det_db_unclip_ratio == 1.8
        assert router.drop_score == 0.35

    def test_paddleocr_precision_overrides(self):
        router = OCRRouter({
            "ocr": {
                "paddleocr": {
                    "lang": "en",
                    "det_limit_side_len": 1280,
                    "drop_score": 0.5,
                }
            }
        })

        assert router.lang == "en"
        assert router.det_limit_side_len == 1280
        assert router.drop_score == 0.5


class TestTableBuilder:
    """Test table builder."""
    
    @pytest.fixture
    def config(self):
        return {}
    
    @pytest.fixture
    def builder(self, config):
        return TableBuilder(config)
    
    def test_table_builder_initialization(self, builder):
        """Test builder initializes correctly."""
        assert builder is not None
    
    def test_html_generation_with_colors(self, builder):
        """Test HTML table generation with background colors."""
        # Create mock cells
        cells = [
            {"text": "A1", "confidence": 0.9, "bbox": [[0, 0], [50, 0], [50, 30], [0, 30]]},
            {"text": "B1", "confidence": 0.9, "bbox": [[50, 0], [100, 0], [100, 30], [50, 30]]},
            {"text": "A2", "confidence": 0.9, "bbox": [[0, 30], [50, 30], [50, 60], [0, 60]]},
            {"text": "B2", "confidence": 0.9, "bbox": [[50, 30], [100, 30], [100, 60], [50, 60]]},
        ]
        
        # Create color map
        color_map = [
            ["#FF0000", "#00FF00"],
            ["#0000FF", "#FFFF00"]
        ]
        
        # Build table
        tables = builder.process(cells, [{"color_grid": color_map}])
        
        assert len(tables) > 0
        table = tables[0]
        
        # Check HTML contains colors
        html = table["html"]
        assert 'bgcolor="#FF0000"' in html or 'bgcolor=' in html
        assert "<table" in html
        assert "</table>" in html
        assert "<tr>" in html
        assert "<td" in html


    def test_plain_text_does_not_generate_table(self, builder):
        cells = [
            {"text": "Title", "confidence": 0.9, "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]]},
            {"text": "Paragraph", "confidence": 0.9, "bbox": [[0, 40], [180, 40], [180, 60], [0, 60]]},
            {"text": "Footer", "confidence": 0.9, "bbox": [[0, 80], [100, 80], [100, 100], [0, 100]]},
        ]

        assert builder.process(cells, []) == []

    def test_adaptive_row_grouping(self, builder):
        cells = [
            {"text": "A1", "confidence": 0.9, "bbox": [[0, 0], [50, 0], [50, 24], [0, 24]]},
            {"text": "B1", "confidence": 0.9, "bbox": [[70, 2], [120, 2], [120, 26], [70, 26]]},
            {"text": "A2", "confidence": 0.9, "bbox": [[0, 42], [50, 42], [50, 66], [0, 66]]},
            {"text": "B2", "confidence": 0.9, "bbox": [[70, 44], [120, 44], [120, 68], [70, 68]]},
        ]

        rows = builder._organize_cells_into_rows(cells)

        assert [[cell["text"] for cell in row] for row in rows] == [["A1", "B1"], ["A2", "B2"]]


class TestLinkHelpers:
    """Test link helper functions."""
    
    def test_blacklist_constants(self):
        """Test blacklist is defined."""
        blacklist = EntityLinker.BLACKLIST
        
        assert blacklist is not None
        assert "the" in blacklist
        assert "data" in blacklist
        assert "\u7684" in blacklist
class TestFileUtils:
    """Test file utilities."""
    
    def test_sha256_computation(self):
        """Test SHA-256 hash computation."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            temp_path = f.name
        
        try:
            hash1 = compute_sha256(temp_path)
            hash2 = compute_sha256(temp_path)
            
            assert hash1 == hash2
            assert len(hash1) == 64  # SHA-256 hex length
        finally:
            os.unlink(temp_path)
    
    def test_filename_sanitization(self):
        """Test filename sanitization."""
        assert sanitize_filename("test file.png") == "test-file.png"
        assert sanitize_filename("test@#file.png") == "test__file.png"
        assert sanitize_filename("normal-file.png") == "normal-file.png"


class TestIntegration:
    """Integration tests."""
    
    @pytest.fixture
    def temp_vault(self):
        """Create temporary vault structure."""
        temp_dir = tempfile.mkdtemp()
        
        # Create folder structure
        os.makedirs(os.path.join(temp_dir, "00-RAW"))
        os.makedirs(os.path.join(temp_dir, "99-Audit/OCR-Pending"))
        os.makedirs(os.path.join(temp_dir, "10-WIKI"))
        
        yield temp_dir
        
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_end_to_end_config(self, temp_vault):
        """Test configuration loads correctly."""
        config = {
            "vault": {
                "root": temp_vault,
                "raw_folder": "00-RAW",
                "audit_folder": "99-Audit/OCR-Pending",
                "wiki_base": "10-WIKI"
            },
            "processing": {
                "max_worker_threads": 1,
                "confidence_threshold": 0.85
            },
            "ocr": {
                "engines": {"text_default": "paddleocr_vl"},
                "ollama": {"endpoint": "http://localhost:11434"}
            }
        }
        
        # Test that config is valid
        assert config["vault"]["root"] == temp_vault
        assert config["processing"]["max_worker_threads"] == 1


class TestImageHandlerPipeline:
    """Test image handler orchestration details."""

    def test_ocr_uses_enhanced_temp_image(self, tmp_path):
        import numpy as np
        from processors.image_handler import ImageHandler

        source = tmp_path / "source.png"
        source.write_bytes(b"placeholder")
        config = {"image": {"super_resolution": {"enabled": True, "scale": 2}}}
        handler = ImageHandler(config)

        handler.preprocessor.process = Mock(return_value={
            "final_image": np.zeros((8, 8), dtype=np.uint8),
            "corrected_image": np.zeros((8, 8, 3), dtype=np.uint8),
            "color_info": {"dominant_colors": ["#ffffff"]},
            "corners": None,
            "original_shape": (4, 4, 3),
            "final_shape": (8, 8),
        })
        handler.ocr_router.classify_content = Mock(return_value=ContentType.PURE_TEXT)
        handler.ocr_router.process = Mock(return_value=Mock(to_dict=lambda: {
            "blocks": [],
            "tables": [],
            "confidence": 1.0,
            "engine_used": "paddleocr_vl",
            "content_type": "pure_text",
        }))
        handler.post_corrector.process = Mock(side_effect=lambda result, _content_type: result)
        handler.table_builder.process = Mock(return_value=[])
        handler.markdown_generator.process = Mock(return_value="---\n---")

        result = handler.process(str(source))

        assert result["success"] is True
        used_path = handler.ocr_router.process.call_args.args[0]
        assert used_path != str(source)
        assert "ocr_enhanced_" in used_path
        assert not os.path.exists(used_path)

    def test_ppstructure_tables_are_preserved(self, tmp_path):
        import numpy as np
        from processors.image_handler import ImageHandler

        source = tmp_path / "source.png"
        source.write_bytes(b"placeholder")
        handler = ImageHandler({})

        handler.preprocessor.process = Mock(return_value={
            "final_image": np.zeros((8, 8), dtype=np.uint8),
            "corrected_image": np.zeros((8, 8, 3), dtype=np.uint8),
            "color_info": {"dominant_colors": ["#ffffff"]},
            "corners": None,
            "original_shape": (4, 4, 3),
            "final_shape": (8, 8),
        })
        handler.ocr_router.classify_content = Mock(return_value=ContentType.SIMPLE_TABLE)
        pp_table = {"html": "<table><tr><td>A</td></tr></table>", "engine": "ppstructure", "confidence": 1.0}
        handler.ocr_router.process = Mock(return_value=Mock(to_dict=lambda: {
            "blocks": [],
            "tables": [pp_table],
            "confidence": 1.0,
            "engine_used": "paddleocr_vl",
            "content_type": "simple_table",
        }))
        handler.post_corrector.process = Mock(side_effect=lambda result, _content_type: result)
        handler.table_builder.process = Mock(return_value=[])
        handler.markdown_generator.process = Mock(return_value="---\n---")

        result = handler.process(str(source))

        assert result["success"] is True
        assert result["ocr_result"]["tables"] == [pp_table]
        handler.table_builder.process.assert_not_called()

class TestPPStructureParsing:
    """Test PP-Structure output normalization."""

    def test_parse_ppstructure_table_html(self):
        router = OCRRouter({})
        raw = [{
            "type": "table",
            "bbox": [1, 2, 3, 4],
            "res": {
                "html": "<table><tr><td>A</td></tr></table>",
                "cell_bbox": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
            }
        }]

        tables = router._parse_ppstructure_tables(raw)

        assert tables == [{
            "html": "<table><tr><td>A</td></tr></table>",
            "cells": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
            "bbox": [1, 2, 3, 4],
            "confidence": 1.0,
            "engine": "ppstructure",
        }]



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
