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
            {"text": "C1", "confidence": 0.9, "bbox": [[100, 0], [150, 0], [150, 30], [100, 30]]},
            {"text": "A2", "confidence": 0.9, "bbox": [[0, 30], [50, 30], [50, 60], [0, 60]]},
            {"text": "B2", "confidence": 0.9, "bbox": [[50, 30], [100, 30], [100, 60], [50, 60]]},
            {"text": "C2", "confidence": 0.9, "bbox": [[100, 30], [150, 30], [150, 60], [100, 60]]},
            {"text": "A3", "confidence": 0.9, "bbox": [[0, 60], [50, 60], [50, 90], [0, 90]]},
            {"text": "B3", "confidence": 0.9, "bbox": [[50, 60], [100, 60], [100, 90], [50, 90]]},
            {"text": "C3", "confidence": 0.9, "bbox": [[100, 60], [150, 60], [150, 90], [100, 90]]},
        ]
        
        # Create color map
        color_map = [
            ["#FF0000", "#00FF00", "#FFFFFF"],
            ["#0000FF", "#FFFF00", "#FFFFFF"],
            ["#FFFFFF", "#FFFFFF", "#FFFFFF"]
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


class TestTitleExtraction:
    """Title extraction: real heading vs summary fallback."""

    def _gen(self):
        from processors.markdown_generator import MarkdownGenerator
        return MarkdownGenerator({})

    def _row(self, text, y0, y1, x1=200):
        return {"type": "text", "text": text, "confidence": 0.98,
                "bbox": [[0, y0], [x1, y0], [x1, y1], [0, y1]]}

    def test_real_heading_is_kept(self):
        gen = self._gen()
        blocks = [
            self._row("\u654f\u611f\u5ea6\u8d44\u672c\u8ba1\u91cf \u8ba1\u91cf\u8303\u56f4", 10, 60, x1=300),
            self._row("\u5177\u6709\u8fdc\u671f\u6027\u8d28\u7684\u4ea7\u54c1\u90fd\u8981\u8ba1\u91cf\u4e00\u822c\u5229\u7387\u98ce\u9669\u5e76\u4e14\u8fd8\u8981\u8003\u8651\u5176\u4ed6", 90, 120),
        ]
        title, meta = gen._extract_title(blocks, "x.jpg")
        assert meta["source"] == "heading"
        assert title.startswith("\u654f\u611f\u5ea6\u8d44\u672c\u8ba1\u91cf")

    def test_long_body_only_falls_back_to_summary(self):
        gen = self._gen()
        long_line = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf\u8861\u91cf\u4e86\u5728\u7a81\u53d1\u6781\u7aef\u60c5\u51b5\u4e0b\uff0c\u4f01\u4e1a\u7ecf\u8425\u60c5\u51b5\u6076\u5316\uff0c\u9020\u6210\u4f01\u4e1a\u6240\u53d1\u884c\u7684\u80a1\u7968\u3001\u503a\u5238\u4ef7\u683c\u5728\u77ed\u65f6\u95f4\u5185\u5267\u70c8\u6ce2\u52a8\u7684"
        blocks = [
            self._row(long_line, 10, 90, x1=3300),
            self._row("\u98ce\u9669\u3002\u76d1\u7ba1\u89c4\u5b9a\u7684\u8ba1\u91cf\u671f\u9650\u4e3a1\u5e74", 100, 160),
        ]
        title, meta = gen._extract_title(blocks, "x.jpg")
        assert meta["source"] == "summary"
        assert len(title) <= gen._SUMMARY_TITLE_MAX
        assert long_line != title
        note = gen._generate_review_note(title, meta)
        assert "[!todo]" in note
        assert title in note

    def test_heading_meta_has_no_review_note(self):
        gen = self._gen()
        note = gen._generate_review_note("A Title", {"source": "heading"})
        assert note == ""

    def test_summary_complete_when_short(self):
        gen = self._gen()
        s = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf\u7684\u8303\u56f4\u4e0e\u671f\u9650\u8bf4\u660e"
        blocks = [self._row(s, 10, 90, x1=3300)]
        title, mode = gen._summarize_blocks(blocks)
        assert mode == "complete"
        assert title == s

    def test_summary_tolerated_small_overflow_keeps_whole(self):
        gen = self._gen()
        # 36 chars: overflow (36-30)/30 = 20% < 30% -> keep whole, mode tolerated
        s = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf\u8861\u91cf\u4e86\u5728\u7a81\u53d1\u6781\u7aef\u60c5\u51b5\u4e0b\u4f01\u4e1a\u7ecf\u8425\u6076\u5316\u7684\u60c5\u5f62\u5171\u4e09\u5341\u4e94\u5b57\u7b26\u6d4b\u8bd5"
        assert len(s) == 36
        blocks = [self._row(s, 10, 90, x1=3300)]
        title, mode = gen._summarize_blocks(blocks)
        assert mode == "tolerated"
        assert title == s
        assert len(title) > gen._SUMMARY_TITLE_MAX
        note = gen._generate_review_note(title, {"source": "summary", "summary_mode": mode})
        assert "[!todo]" in note and "30%" in note

    def test_summary_condensed_large_overflow(self):
        gen = self._gen()
        s = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf\u8861\u91cf\u4e86\u5728\u7a81\u53d1\u6781\u7aef\u60c5\u51b5\u4e0b\uff0c\u4f01\u4e1a\u7ecf\u8425\u60c5\u51b5\u6076\u5316\uff0c\u9020\u6210\u4f01\u4e1a\u6240\u53d1\u884c\u7684\u80a1\u7968\u3001\u503a\u5238\u4ef7\u683c\u5728\u77ed\u65f6\u95f4\u5185\u5267\u70c8\u6ce2\u52a8\u7684\u98ce\u9669"
        blocks = [self._row(s, 10, 90, x1=3300)]
        title, mode = gen._summarize_blocks(blocks)
        assert mode == "condensed"
        assert len(title) <= gen._SUMMARY_TITLE_MAX
        assert title != s
        note = gen._generate_review_note(title, {"source": "summary", "summary_mode": mode})
        assert "[!todo]" in note and title in note

    def test_title_cleanup_dedup_and_dash(self):
        gen = self._gen()
        # "计量 量一一计算步骤" -> dup "量" removed, "一一" -> "一"
        s = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf \u91cf\u4e00\u4e00\u8ba1\u7b97\u6b65\u9aa4"
        out = gen._clean_title(s)
        assert out == "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf \u4e00\u8ba1\u7b97\u6b65\u9aa4"

    def test_title_cleanup_dash_fragment_with_emdash(self):
        gen = self._gen()
        # "量一—一计算步骤（续）" fragment -> "一计算步骤（续）"
        s = "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf \u91cf\u4e00\u2014\u4e00\u8ba1\u7b97\u6b65\u9aa4\uff08\u7eed\uff09"
        out = gen._clean_title(s)
        assert out == "\u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u91cf \u4e00\u8ba1\u7b97\u6b65\u9aa4\uff08\u7eed\uff09"

    def test_title_cleanup_bracket_spacing(self):
        gen = self._gen()
        s = "GIRR Delta\u8ba1\u7b97\u793a\u4f8b \uff08\u516d\uff09"
        out = gen._clean_title(s)
        assert out == "GIRR Delta\u8ba1\u7b97\u793a\u4f8b\uff08\u516d\uff09"

    def test_title_cleanup_leaves_normal_title(self):
        gen = self._gen()
        s = "\u503a\u5238\u7c7b\u4ea7\u54c1FRTB\u8d44\u672c\u8ba1\u91cf \u8fdd\u7ea6\u98ce\u9669\u8d44\u672c\u8ba1\u7b97\u793a\u4f8b\uff08\u4e00\uff09"
        assert gen._clean_title(s) == s

    def test_noise_mangled_toolbar_run_in_margin(self):
        gen = self._gen()
        # OCR-mangled toolbar strip with a repeated-char run, in the margin.
        assert gen._noise_kind("\u4e09\u4e09\u4e09\u4e09\u680f\u680f\u4e09\u55b5\u8f6c\u667a\u80fd\u518c\u5f62", in_margin=True) == "toolbar"
        assert gen._noise_kind("IAAAE\u00b7\u6c47\u533a", in_margin=True) == "toolbar"

    def test_noise_repeated_run_outside_margin_is_content(self):
        gen = self._gen()
        # Same repeated-char pattern OUTSIDE the margin (e.g. table sample cell)
        # must remain content.
        assert gen._noise_kind("AAA", in_margin=False) is None
        assert gen._noise_kind("BBB", in_margin=False) is None

    def test_noise_numeric_run_in_margin_not_toolbar(self):
        gen = self._gen()
        # Numeric/percent tokens in the margin are table residue, not toolbar.
        assert gen._noise_kind("111", in_margin=True) != "toolbar"
        assert gen._noise_kind("100.0%", in_margin=True) != "toolbar"

    def test_noise_mangled_presentation_tool_label(self):
        gen = self._gen()
        # "\u6f14\u793a\u4e0a\u5177" (OCR typo of "\u6f14\u793a\u5de5\u5177") short + margin -> toolbar.
        assert gen._noise_kind("\u6f14\u793a\u4e0a\u5177\u00b7", in_margin=True) == "toolbar"

class TestTableQuality:
    """Plan-B geometric table reconstruction: quality gate + gutter split."""

    def _gen(self):
        from processors.markdown_generator import MarkdownGenerator
        return MarkdownGenerator({})

    def _cell(self, cx, cy, w=30, h=18):
        x0, x1 = cx - w / 2, cx + w / 2
        y0, y1 = cy - h / 2, cy + h / 2
        return {"type": "text", "text": "x", "confidence": 0.9,
                "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

    def test_clean_grid_scores_high(self):
        gen = self._gen()
        cols = [50, 150, 250]
        rows = [[self._cell(c, y) for c in cols] for y in (10, 40, 70, 100)]
        q = gen._table_quality(rows, cols)
        assert q["score"] >= gen._TABLE_QUALITY_MIN
        assert q["score"] > 0.9

    def test_wide_garbled_matrix_scores_low(self):
        gen = self._gen()
        cols = [10 + i * 20 for i in range(14)]
        rows = []
        for y in (10, 40, 70):
            row = [self._cell(c, y) for c in cols]
            row.append(self._cell(12, y))  # forces a same-column collision
            rows.append(row)
        q = gen._table_quality(rows, cols)
        assert q["n_cols"] == 14
        assert q["collision"] > 0.0
        assert q["score"] < gen._TABLE_QUALITY_MIN

    def test_gutter_split_separates_side_by_side(self):
        gen = self._gen()
        cols = [10, 30, 50, 70, 200, 220, 240, 260]
        rows = [[self._cell(c, y) for c in cols] for y in (10, 40, 70)]
        result = gen._split_columns_by_gutter(rows, cols)
        assert result is not None
        left_rows, right_rows, split_x = result
        assert 70 < split_x < 200
        assert all(len(r) == 4 for r in left_rows)
        assert all(len(r) == 4 for r in right_rows)

    def test_small_table_is_not_split(self):
        gen = self._gen()
        cols = [10, 60, 110, 160]
        rows = [[self._cell(c, y) for c in cols] for y in (10, 40)]
        assert gen._split_columns_by_gutter(rows, cols) is None

    def test_low_quality_table_gets_warning_annotation(self):
        gen = self._gen()
        anchors = [10 + i * 60 for i in range(14)]
        rows = []
        for y in (10, 40, 70, 100):
            row = [self._cell(a, y) for a in anchors]
            row.append(self._cell(18, y))   # duplicate near col 0 -> collision
            row.append(self._cell(78, y))   # duplicate near col 1 -> collision
            rows.append(row)
        md = gen._render_markdown_table(rows)
        assert "[!warning]" in md
        assert "\u589e\u5f3a\u8bc6\u522b" in md


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
