# -*- coding: utf-8 -*-
"""Direction-3 region-rebuild tests: consistency guard, struct score, stitch,
verdict adopt/fallback, trigger detection, and body replace vs compare block.
Mocks the ollama VLM call; no real model required."""

import os
import sys
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from processors.markdown_generator import MarkdownGenerator
from processors.region_rebuilder import (
    RegionRebuilder,
    consistency_metrics,
    markdown_struct_score,
    stitch_regions,
)

ADOPT_NOTE = "\u5206\u533a\u57df\u89c6\u89c9\u91cd\u5efa"  # body-replaced marker
COMPARE_NOTE = "\u672a\u91c7\u7eb3"  # review-only compare marker


def _cfg(**ts):
    base = {"ocr": {"table_structure": {"region_rebuild": True,
                                        "region_rebuild_adopt": True}}}
    base["ocr"]["table_structure"].update(ts)
    return base


def _gen(cfg=None):
    return MarkdownGenerator(cfg or {})


def _row(text, y0, y1, x0=0, x1=200):
    return {"type": "text", "text": text, "confidence": 0.98,
            "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


class TestConsistencyGuard:
    def test_all_match(self):
        cm = consistency_metrics([{"type": "text", "text": "12.5 300 7%"}],
                                 "val 12.5 and 300 up 7%")
        assert cm["hit"] == 1.0 and cm["fabricate"] == 0.0

    def test_detects_dropped(self):
        cm = consistency_metrics([{"type": "text", "text": "12.5 300 7%"}], "only 12.5")
        assert cm["hit"] < 0.5 and cm["fabricate"] == 0.0

    def test_detects_fabricated(self):
        cm = consistency_metrics([{"type": "text", "text": "12.5"}], "12.5 999 888")
        assert cm["fabricate"] > 0.5


class TestStructScore:
    def test_rewards_tables(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        assert markdown_struct_score(md) > 0.7

    def test_empty_is_zero(self):
        assert markdown_struct_score("") == 0.0

    def test_plain_text_modest(self):
        assert markdown_struct_score("x" * 60) == 0.55


class TestStitch:
    def test_dedups_overlap(self):
        out = stitch_regions(["# H\nrow one\nrow two", "row two\nrow three"])
        assert out.count("row two") == 1 and "row three" in out


class TestJudge:
    def test_adopts_clean_result(self):
        rb = RegionRebuilder(_cfg(), generator=_gen())
        blocks = [{"type": "text", "text": "12.5 300 7% 42 88"}]
        stitched = "| x | y |\n|---|---|\n| 12.5 | 300 |\n| 7% | 42 |\n| 88 | n/a |"
        v = rb._judge(blocks, stitched)
        assert v["adopt"] is True and not v["reasons"]

    def test_rejects_dropped(self):
        rb = RegionRebuilder(_cfg(), generator=_gen())
        blocks = [{"type": "text", "text": "12.5 300 7% 42 88 99 11 22"}]
        v = rb._judge(blocks, "| a |\n|---|\n| 12.5 |")
        assert v["adopt"] is False and any("hit" in r for r in v["reasons"])

    def test_rejects_fabrication(self):
        rb = RegionRebuilder(_cfg(), generator=_gen())
        blocks = [{"type": "text", "text": "12.5"}]
        v = rb._judge(blocks, "| a | b |\n|---|---|\n| 12.5 | 999 |\n| 888 | 777 |")
        assert v["adopt"] is False and any("fabricate" in r for r in v["reasons"])

    def test_adopt_blocked_when_disabled(self):
        rb = RegionRebuilder(_cfg(region_rebuild_adopt=False), generator=_gen())
        blocks = [{"type": "text", "text": "12.5 300 7% 42 88"}]
        stitched = "| x | y |\n|---|---|\n| 12.5 | 300 |\n| 7% | 42 |\n| 88 | n/a |"
        assert rb._judge(blocks, stitched)["adopt"] is False


class TestRebuild:
    def test_disabled_returns_none(self):
        rb = RegionRebuilder({"ocr": {"table_structure": {"region_rebuild": False}}},
                             generator=_gen())
        assert rb.rebuild(np.zeros((10, 10, 3), np.uint8),
                          [{"type": "text", "text": "1"}]) is None

    def test_region_failure_falls_back(self):
        rb = RegionRebuilder(_cfg(), generator=_gen())
        blocks = [_row("alpha 12", 10, 60), _row("beta 34", 200, 260)]
        img = np.full((400, 300, 3), 255, np.uint8)
        with patch("processors.region_rebuilder._vlm_region_markdown", return_value=None):
            assert rb.rebuild(img, blocks) is None

    def test_stitches_and_judges(self):
        rb = RegionRebuilder(_cfg(), generator=_gen())
        blocks = [_row("12.5 300", 10, 60), _row("7% 42", 200, 260)]
        img = np.full((400, 300, 3), 255, np.uint8)
        with patch("processors.region_rebuilder._vlm_region_markdown",
                   return_value="| a | b |\n|---|---|\n| 12.5 | 300 |\n| 7% | 42 |"):
            v = rb.rebuild(img, blocks)
        assert v is not None and v["markdown"] and v["n_regions"] >= 1


class TestGeneratorIntegration:
    def test_needs_rebuild_skips_sparse_page(self):
        ocr = {"blocks": [_row("a", 10, 40), _row("b", 50, 80)]}
        assert _gen().needs_region_rebuild(ocr) is False

    def test_process_adopt_replaces_body(self):
        ocr = {
            "blocks": [_row("orig text one", 10, 40)],
            "region_rebuild": {"markdown": "| a | b |\n|---|---|\n| 1 | 2 |",
                               "adopt": True, "hit": 0.95, "fabricate": 0.0,
                               "reasons": []},
        }
        md = _gen().process(ocr, "x.jpg", link_candidates=[])
        assert ADOPT_NOTE in md and "| a | b |" in md

    def test_process_fallback_appends_compare(self):
        ocr = {
            "blocks": [_row("orig body text", 10, 40)],
            "region_rebuild": {"markdown": "| a |\n|---|\n| 1 |",
                               "adopt": False, "hit": 0.4, "fabricate": 0.0,
                               "reasons": ["hit 0.40 < 0.90 (dropped content)"]},
        }
        md = _gen().process(ocr, "x.jpg", link_candidates=[])
        assert COMPARE_NOTE in md and "orig body text" in md
