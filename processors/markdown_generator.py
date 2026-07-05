"""
Markdown generator for OCR pipeline.
Assembles final Markdown note with YAML front matter.
"""

import logging
import re
from html.parser import HTMLParser
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseHandler


class MarkdownGenerator(BaseHandler):
    """Generate Markdown notes from OCR results."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize markdown generator.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.logger = logging.getLogger("ocr_pipeline")
    
    def process(
        self,
        ocr_result: Dict[str, Any],
        source_path: str,
        link_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generate complete Markdown note.
        
        Args:
            ocr_result: OCR result dictionary
            source_path: Path to source image
            link_candidates: Optional link candidates from entity linker
            
        Returns:
            Complete Markdown string
        """
        # Extract components
        blocks = ocr_result.get("blocks", [])
        tables = ocr_result.get("tables", [])
        confidence = ocr_result.get("confidence", 0)

        # Split page blocks into core content vs filtered noise (nav bars, headers/footers)
        content_blocks, noise_blocks = self._partition_blocks(blocks)
        page_number = self._extract_page_number(noise_blocks, blocks)
        title = self._extract_title(content_blocks, source_path)

        # Generate front matter (page number recorded for sequential archiving)
        front_matter = self._generate_front_matter(
            source_path=source_path,
            confidence=confidence,
            tables=tables,
            title=title,
            page_number=page_number,
        )

        # Keep the title visible in the body as a heading, then the reconstructed content
        title_heading = f"# {title}" if title else ""
        body_text = self._generate_body_text(content_blocks, title)

        # Drop external tables whose cell text is already rendered in the body
        # (fallback table_builder sometimes wraps a multi-column *text* slide as
        # one table, duplicating the reconstructed body content).
        tables = self._drop_body_duplicate_tables(tables, content_blocks)

        # Generate tables section
        tables_html = self._generate_tables_section(tables)

        # Generate link comments
        link_comments = self._generate_link_comments(link_candidates or [])

        # Note for human review: what was filtered out
        filtered_note = self._generate_filtered_note(noise_blocks)

        # Assemble complete note
        note_parts = [front_matter, title_heading, body_text, tables_html, filtered_note, link_comments]

        return "\n\n".join(part for part in note_parts if part)
    
    def _generate_front_matter(
        self,
        source_path: str,
        confidence: float,
        tables: List[Dict[str, Any]],
        title: str,
        page_number: Optional[str] = None,
    ) -> str:
        """
        Generate YAML front matter.
        
        Args:
            source_path: Source image path
            confidence: OCR confidence score
            tables: Generated table dictionaries
            title: Extracted document title
            page_number: Page number parsed from header/footer, if any

        Returns:
            YAML front matter string
        """
        # Get current date
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # Sanitize source path for wiki link
        source_name = source_path.split("/")[-1].split("\\")[-1]
        
        # Determine tags based on content
        tags = ["ocr/pending"]
        if tables:
            tags.append("ocr/table")

        page_line = f"\npage: {page_number}" if page_number else ""
        front_matter = f"""---
title: "{title}"
date: {date_str}{page_line}
tags: [{', '.join(f'"{tag}"' for tag in tags)}]
status: pending
source: "[[00-RAW/{source_name}]]"
ocr_confidence: {confidence:.2f}
---"""
        
        return front_matter
    
    def _extract_title(
        self,
        blocks: List[Dict[str, Any]],
        source_path: str
    ) -> str:
        """Extract a document title from page content, not app toolbar text."""
        text_blocks = [block for block in blocks if block.get("type") == "text" and block.get("text", "").strip()]
        if not text_blocks:
            filename = source_path.split("/")[-1].split("\\")[-1]
            return filename.rsplit(".", 1)[0]

        rows = self._group_blocks_into_rows(text_blocks)
        page_top = min(self._block_metrics(block)["min_y"] for block in text_blocks)
        page_bottom = max(self._block_metrics(block)["max_y"] for block in text_blocks)
        page_height = max(page_bottom - page_top, 1)
        # Title lives in the upper portion of the (already denoised) content.
        content_bottom = page_top + page_height * 0.5
        median_height = sorted(self._block_metrics(block)["height"] for block in text_blocks)
        median_height = median_height[len(median_height) // 2]

        candidates = []
        for row in rows:
            row_text = self._row_text(row)
            if not row_text or self._is_toolbar_noise(row_text):
                continue
            row_metrics = [self._block_metrics(block) for block in row]
            row_y = min(metric["min_y"] for metric in row_metrics)
            if row_y > content_bottom:
                continue
            row_height = max(metric["height"] for metric in row_metrics)
            row_width = max(metric["max_x"] for metric in row_metrics) - min(metric["min_x"] for metric in row_metrics)
            row_center_y = sum(metric["center_y"] for metric in row_metrics) / len(row_metrics)
            relative_y = (row_center_y - page_top) / page_height
            # Prefer larger-than-body font near the top; ignore tiny/very long lines.
            # Cap the font ratio so a single outlier-tall block (e.g. a merged
            # multi-line cell) cannot dominate scoring.
            height_ratio = min(row_height / max(median_height, 1), 3.0)
            score = height_ratio * 60
            score += max(0, 40 - relative_y * 80)
            if 6 <= len(row_text) <= 40:
                score += 15
            if row_height < median_height * 1.1:
                score -= 40
            # A title is a few large blocks, not a wide band of many cells.
            # Penalize table-header-like rows (many blocks / long merged text).
            if len(row) >= 4:
                score -= 25 * (len(row) - 3)
            if len(row_text) > 45:
                score -= (len(row_text) - 45) * 1.5
            candidates.append((score, row_y, row_text))

        if candidates:
            _score, _row_y, title = max(candidates, key=lambda item: (item[0], -item[1]))
            return title.replace('"', "'")[:120]

        filename = source_path.split("/")[-1].split("\\")[-1]
        return filename.rsplit(".", 1)[0]

    def _is_toolbar_noise(self, text: str) -> bool:
        """Return True for UI toolbar or footer OCR noise."""
        return self._noise_kind(text) is not None

    def _noise_kind(self, text: str, in_margin: bool = True) -> Optional[str]:
        """Classify OCR text as a kind of noise, or None when it is content."""
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return "empty"
        # Unambiguous UI/menu tokens: treat as toolbar noise anywhere on the page.
        strong_terms = (
            "智能图形", "另存为", "幻灯片", "放映", "批注", "缩放",
        )
        # Ambiguous menu labels that also appear inside ordinary prose
        # (e.g. "工具" inside "金融工具"). A single weak term only counts as
        # toolbar noise when the text is short AND sits in the page margin, so
        # long body sentences are never dropped. But when TWO OR MORE distinct
        # weak terms co-occur (e.g. an OCR-mangled toolbar strip such as
        # "文本框形状多排列口轮廓替换"), it is a toolbar regardless of length or
        # position, because ordinary prose almost never packs several UI labels.
        weak_terms = (
            "填充", "查找", "替换", "搜索", "菜单", "工具", "视图", "帮助",
            "开始", "插入", "设计", "切换", "动画", "审阅",
            "文件", "编辑", "格式", "共享", "登录", "选择", "打印", "导出",
            "文本框", "形状", "轮廓", "对齐", "旋转",
            "艺术字", "绘图", "演示工具",
        )
        if any(term in compact for term in strong_terms):
            return "toolbar"
        weak_hits = sum(1 for term in weak_terms if term in compact)
        if weak_hits >= 2:
            return "toolbar"
        if in_margin and len(compact) <= 8 and weak_hits >= 1:
            return "toolbar"
        if re.fullmatch(r"[-_—=+*/\\|.·,:;!?()\[\]{}<>]+", compact or ""):
            return "symbol"
        if re.fullmatch(r"第?\d+页|\d+/\d+", compact or ""):
            return "page_number"
        return None

    def _partition_blocks(
        self, blocks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split OCR blocks into core content and filtered noise.

        Noise = editor/navigation toolbars plus PPT-style headers and footers
        located in the top/bottom page margins. Everything else is core content.
        """
        text_blocks = [
            block for block in blocks
            if block.get("type") == "text" and block.get("text", "").strip()
        ]
        if not text_blocks:
            return [], []
        page_top = min(self._block_metrics(block)["min_y"] for block in text_blocks)
        page_bottom = max(self._block_metrics(block)["max_y"] for block in text_blocks)
        page_height = max(page_bottom - page_top, 1)
        header_limit = page_top + page_height * 0.07
        footer_limit = page_bottom - page_height * 0.07

        content: List[Dict[str, Any]] = []
        noise: List[Dict[str, Any]] = []
        for block in blocks:
            if block.get("type") != "text" or not block.get("text", "").strip():
                content.append(block)
                continue
            text = block.get("text", "").strip()
            metrics = self._block_metrics(block)
            in_margin = metrics["max_y"] <= header_limit or metrics["min_y"] >= footer_limit
            kind = self._noise_kind(text, in_margin=in_margin)
            reason = None
            compact_text = re.sub(r"\s+", "", text)
            is_page_number = kind == "page_number" or (
                in_margin and re.fullmatch(r"\d{1,4}", compact_text) is not None
            )
            if is_page_number:
                reason = "page_number"
            elif kind == "toolbar":
                reason = "toolbar"
            elif kind == "symbol":
                reason = "symbol"
            elif kind == "empty":
                reason = "empty"
            elif in_margin and self._is_margin_noise(text, metrics, page_height):
                reason = "footer" if metrics["min_y"] >= footer_limit else "header"
            if reason is not None:
                # Annotate the block with a human-readable filter reason so the
                # review block can explain WHY each item was removed.
                block = dict(block)
                block["_filter_reason"] = reason
                noise.append(block)
            else:
                content.append(block)
        return content, noise

    def _is_margin_noise(self, text: str, metrics: Dict[str, float], page_height: float) -> bool:
        """Header/footer heuristic: short or boilerplate text in page margins."""
        compact = re.sub(r"\s+", "", text)
        if len(compact) <= 6:
            return True
        footer_terms = ("版权", "保留", "所有权利", "confidential", "版权所有", "有限公司", "咨询")
        lowered = compact.lower()
        if any(term.lower() in lowered for term in footer_terms):
            return True
        return False

    def _extract_page_number(
        self, noise_blocks: List[Dict[str, Any]], all_blocks: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Pull a page number from footer/header noise for sequential archiving."""
        for block in noise_blocks:
            text = re.sub(r"\s+", "", block.get("text", ""))
            match = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if match:
                return f"{match.group(1)}/{match.group(2)}"
            match = re.fullmatch(r"第?(\d+)页", text)
            if match:
                return match.group(1)
            if re.fullmatch(r"\d{1,4}", text):
                return text
        return None

    _FILTER_REASON_LABELS = {
        "toolbar": "编辑器/导航栏按钮",
        "symbol": "纯符号噪音",
        "empty": "空白内容",
        "page_number": "页号",
        "header": "页眉",
        "footer": "页脚/版权信息",
    }

    def _generate_filtered_note(self, noise_blocks: List[Dict[str, Any]]) -> str:
        """Emit a review note listing filtered-out non-content text with reasons."""
        items = []
        for block in noise_blocks:
            text = re.sub(r"\s+", " ", block.get("text", "")).strip()
            if not text:
                continue
            reason = block.get("_filter_reason", "")
            label = self._FILTER_REASON_LABELS.get(reason, "其他无关内容")
            items.append((label, text))
        if not items:
            return ""
        lines = [
            "<!-- Filtered non-content (nav bars / headers / footers) - review before archiving -->",
            "> [!note] 已过滤无关内容，请人工审核后归档。每条已标注过滤原因（页号仅供连续归档参考）。",
        ]
        for label, text in items:
            lines.append(f"> - [{label}] {text}")
        return "\n".join(lines)

    def _block_metrics(self, block: Dict[str, Any]) -> Dict[str, float]:
        """Return bbox metrics for reading-order reconstruction."""
        bbox = block.get("bbox") or [[0, 0], [0, 0], [0, 0], [0, 0]]
        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "center_x": (min_x + max_x) / 2,
            "center_y": (min_y + max_y) / 2,
            "width": max(max_x - min_x, 1),
            "height": max(max_y - min_y, 1),
        }

    def _region_metrics(self, blocks: List[Dict[str, Any]]) -> Dict[str, float]:
        """Return bounding metrics for a group of OCR blocks."""
        metrics = [self._block_metrics(block) for block in blocks]
        min_x = min(metric["min_x"] for metric in metrics)
        max_x = max(metric["max_x"] for metric in metrics)
        min_y = min(metric["min_y"] for metric in metrics)
        max_y = max(metric["max_y"] for metric in metrics)
        return {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "center_x": (min_x + max_x) / 2,
            "center_y": (min_y + max_y) / 2,
            "width": max(max_x - min_x, 1),
            "height": max(max_y - min_y, 1),
        }

    def _generate_body_text(self, blocks: List[Dict[str, Any]], title: str = "") -> str:
        """Generate body text by visual regions before handling columns."""
        text_blocks = [
            block for block in blocks
            if block.get("type") == "text" and block.get("text", "").strip()
        ]
        if not text_blocks:
            return ""
        text_blocks = self._drop_title_row(text_blocks, title)
        sections = []
        for band in self._split_into_vertical_regions(text_blocks):
            for region in self._split_region_columns_if_needed(band):
                rendered = self._render_region(region)
                if rendered:
                    sections.append(rendered)
        return "\n\n".join(sections)

    def _drop_title_row(self, blocks: List[Dict[str, Any]], title: str) -> List[Dict[str, Any]]:
        """Remove the OCR row that matches the extracted title."""
        if not title:
            return blocks
        for row in self._group_blocks_into_rows(blocks):
            if self._is_same_text(self._row_text(row), title):
                row_ids = {id(block) for block in row}
                return [block for block in blocks if id(block) not in row_ids]
        return blocks

    def _render_rows(self, rows: List[List[Dict[str, Any]]]) -> str:
        """Render visual rows with paragraph gaps."""
        if not rows:
            return ""
        heights = [self._block_metrics(block)["height"] for row in rows for block in row]
        median_height = sorted(heights)[len(heights) // 2]
        paragraph_gap = max(median_height * 1.8, 18)
        lines = []
        previous_bottom = None
        for row in rows:
            row_text = self._row_text(row)
            if not row_text:
                continue
            row_top = min(self._block_metrics(block)["min_y"] for block in row)
            if previous_bottom is not None and row_top - previous_bottom > paragraph_gap:
                lines.append("")
            lines.append(row_text)
            previous_bottom = max(self._block_metrics(block)["max_y"] for block in row)
        return "\n".join(lines)

    def _split_into_vertical_regions(self, blocks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Split the page into top-to-bottom visual bands before column handling."""
        rows = self._group_blocks_into_rows(blocks)
        if not rows:
            return []
        heights = [self._block_metrics(block)["height"] for block in blocks]
        median_height = sorted(heights)[len(heights) // 2]
        split_gap = max(median_height * 2.8, 28)
        bands: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        previous_bottom: Optional[float] = None
        for row in rows:
            row_top = min(self._block_metrics(block)["min_y"] for block in row)
            row_bottom = max(self._block_metrics(block)["max_y"] for block in row)
            if current and previous_bottom is not None and row_top - previous_bottom > split_gap:
                bands.append(current)
                current = []
            current.extend(row)
            previous_bottom = row_bottom
        if current:
            bands.append(current)
        return bands

    def _split_region_columns_if_needed(self, blocks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Split one visual band into columns only when it looks table-like."""
        if len(blocks) < 8:
            return [blocks]
        metrics = [(block, self._block_metrics(block)) for block in blocks]
        min_x = min(metric["min_x"] for _block, metric in metrics)
        max_x = max(metric["max_x"] for _block, metric in metrics)
        page_width = max(max_x - min_x, 1)
        midpoint = min_x + page_width * 0.5
        left = [block for block, metric in metrics if metric["center_x"] <= midpoint]
        right = [block for block, metric in metrics if metric["center_x"] > midpoint]
        if self._is_balanced_column_split(left, right):
            return [left, right]

        centers = sorted(metric["center_x"] for _block, metric in metrics)
        gaps = [(centers[i + 1] - centers[i], (centers[i + 1] + centers[i]) / 2) for i in range(len(centers) - 1)]
        large_gaps = [(gap, split) for gap, split in gaps if gap > page_width * 0.14]
        if not large_gaps:
            return [blocks]
        _gap, split_x = max(large_gaps, key=lambda item: item[0])
        left = [block for block, metric in metrics if metric["center_x"] <= split_x]
        right = [block for block, metric in metrics if metric["center_x"] > split_x]
        if not self._is_balanced_column_split(left, right):
            return [blocks]
        return [left, right]

    def _is_balanced_column_split(self, left: List[Dict[str, Any]], right: List[Dict[str, Any]]) -> bool:
        """Return True when both sides look like independent visual regions."""
        if len(left) < 4 or len(right) < 4:
            return False
        left_rows = self._group_blocks_into_rows(left)
        right_rows = self._group_blocks_into_rows(right)
        if len(left_rows) < 3 or len(right_rows) < 3:
            return False
        left_box = self._region_metrics(left)
        right_box = self._region_metrics(right)
        y_overlap = min(left_box["max_y"], right_box["max_y"]) - max(left_box["min_y"], right_box["min_y"])
        min_height = min(left_box["height"], right_box["height"])
        return y_overlap / max(min_height, 1) >= 0.35

    def _split_into_reading_regions(self, blocks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Backward-compatible wrapper for region-aware reading order."""
        regions: List[List[Dict[str, Any]]] = []
        for band in self._split_into_vertical_regions(blocks):
            regions.extend(self._split_region_columns_if_needed(band))
        return regions

    def _group_blocks_into_rows(self, blocks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Group blocks into visual rows."""
        if not blocks:
            return []
        heights = sorted(self._block_metrics(block)["height"] for block in blocks)
        median_height = heights[len(heights) // 2]
        row_tolerance = max(median_height * 0.7, 8)
        rows = []
        for block in sorted(blocks, key=lambda b: self._block_metrics(b)["center_y"]):
            metrics = self._block_metrics(block)
            if not rows:
                rows.append([block])
                continue
            current_row = rows[-1]
            row_center = sum(self._block_metrics(item)["center_y"] for item in current_row) / len(current_row)
            if abs(metrics["center_y"] - row_center) <= row_tolerance:
                current_row.append(block)
            else:
                rows.append([block])
        for row in rows:
            row.sort(key=lambda b: self._block_metrics(b)["min_x"])
        return rows

    def _looks_like_table_region(self, rows: List[List[Dict[str, Any]]]) -> bool:
        """Detect grid-like regions without relying on fixed document text."""
        rows_with_multiple = [row for row in rows if len(row) >= 2]
        if len(rows) < 2 or len(rows_with_multiple) < 2:
            return False
        if len(rows_with_multiple) / len(rows) < 0.6:
            return False
        columns = self._estimate_columns(rows)
        if len(columns) < 2:
            return False
        aligned_rows = 0
        for row in rows:
            filled = self._columns_covered(row, columns)
            if filled >= 2:
                aligned_rows += 1
        return aligned_rows >= 2 and aligned_rows / len(rows) >= 0.6

    def _estimate_columns(self, rows: List[List[Dict[str, Any]]]) -> List[float]:
        """Cluster block center-x positions into column anchors (centroids)."""
        centers = sorted(
            self._block_metrics(block)["center_x"]
            for row in rows for block in row
        )
        if not centers:
            return []
        heights = [self._block_metrics(block)["height"] for row in rows for block in row]
        gap_threshold = max(sorted(heights)[len(heights) // 2] * 1.5, 20)
        # Group adjacent centers into clusters, then use each cluster centroid
        # so a single misaligned block cannot spawn a spurious column anchor.
        clusters: List[List[float]] = [[centers[0]]]
        for center in centers[1:]:
            if center - clusters[-1][-1] > gap_threshold:
                clusters.append([center])
            else:
                clusters[-1].append(center)
        row_count = max(len(rows), 1)
        columns: List[float] = []
        for cluster in clusters:
            # Drop weak clusters that only a small fraction of rows populate;
            # these are usually stray blocks, not real table columns.
            if len(cluster) < 2 and row_count >= 3:
                continue
            columns.append(sum(cluster) / len(cluster))
        if not columns:
            columns = [sum(cluster) / len(cluster) for cluster in clusters]
        return columns

    def _columns_covered(self, row: List[Dict[str, Any]], columns: List[float]) -> int:
        """Count how many distinct columns a row's blocks occupy."""
        used = set()
        for block in row:
            center_x = self._block_metrics(block)["center_x"]
            nearest = min(range(len(columns)), key=lambda i: abs(columns[i] - center_x))
            used.add(nearest)
        return len(used)

    def _is_region_heading(self, row: List[Dict[str, Any]], rows: List[List[Dict[str, Any]]]) -> bool:
        """Infer highlighted box headings from top-row geometry."""
        if len(rows) < 2:
            return False
        text = self._row_text(row)
        if not text or len(text) > 24:
            return False
        all_heights = [self._block_metrics(block)["height"] for item in rows for block in item]
        median_height = sorted(all_heights)[len(all_heights) // 2]
        row_height = max(self._block_metrics(block)["height"] for block in row)
        if re.match(r"^([一二三四五六七八九十]+、|\d+[.、)]|\(?[A-Za-z]\))", text):
            return True
        if text.endswith(("：", ":")):
            return True
        return row_height >= median_height * 1.35

    def _render_markdown_table(self, rows: List[List[Dict[str, Any]]]) -> str:
        """Render OCR rows as a Markdown table."""
        rows, side_notes = self._separate_side_notes(rows)
        columns = self._estimate_columns(rows)
        if len(columns) < 2:
            body = self._render_rows(rows)
            return "\n\n".join(part for part in [body, side_notes] if part)
        normalized_rows = []
        for row in rows:
            cells = [""] * len(columns)
            for block in sorted(row, key=lambda b: self._block_metrics(b)["center_x"]):
                text = self._escape_table_cell(block.get("text", "").strip())
                if not text:
                    continue
                center_x = self._block_metrics(block)["center_x"]
                idx = min(range(len(columns)), key=lambda i: abs(columns[i] - center_x))
                cells[idx] = (cells[idx] + " " + text).strip() if cells[idx] else text
            if any(cells):
                normalized_rows.append(cells)
        if len(normalized_rows) < 2:
            body = self._render_rows(rows)
            return "\n\n".join(part for part in [body, side_notes] if part)
        col_count = len(columns)
        padded = [row + [""] * (col_count - len(row)) for row in normalized_rows]
        lines = [
            "| " + " | ".join(padded[0]) + " |",
            "| " + " | ".join(["---"] * col_count) + " |",
        ]
        for row in padded[1:]:
            lines.append("| " + " | ".join(row) + " |")
        table_md = "\n".join(lines)
        return "\n\n".join(part for part in [table_md, side_notes] if part)

    def _separate_side_notes(
        self, rows: List[List[Dict[str, Any]]]
    ) -> Tuple[List[List[Dict[str, Any]]], str]:
        """Split off wide paragraph blocks that sit beside a grid.

        A side note is an OCR block whose width is much larger than the table's
        typical cell width and which sits to the right of the main grid body.
        These are explanatory captions, not table cells.
        """
        widths = [
            self._block_metrics(block)["width"]
            for row in rows for block in row
        ]
        if len(widths) < 4:
            return rows, ""
        sorted_widths = sorted(widths)
        median_width = sorted_widths[len(sorted_widths) // 2]

        # Right edge of the grid body, measured only from cell-sized blocks so a
        # wide caption cannot inflate it.
        cell_right_edges = sorted(
            self._block_metrics(block)["max_x"]
            for row in rows for block in row
            if self._block_metrics(block)["width"] <= median_width * 1.6
        )
        if not cell_right_edges:
            return rows, ""
        grid_right = cell_right_edges[int(len(cell_right_edges) * 0.9)]

        kept_rows: List[List[Dict[str, Any]]] = []
        note_blocks: List[Dict[str, Any]] = []
        for row in rows:
            kept = []
            for block in row:
                metrics = self._block_metrics(block)
                # A caption starts to the right of the grid body and is wider
                # than a normal cell; both conditions guard against false hits.
                starts_right_of_grid = metrics["min_x"] >= grid_right + median_width * 0.3
                is_wide = metrics["width"] >= median_width * 2.0
                if starts_right_of_grid and is_wide:
                    note_blocks.append(block)
                else:
                    kept.append(block)
            if kept:
                kept_rows.append(kept)
        if not note_blocks:
            return rows, ""
        note_rows = self._group_blocks_into_rows(note_blocks)
        side_text = self._render_rows(note_rows)
        return kept_rows, side_text

    def _escape_table_cell(self, text: str) -> str:
        """Escape Markdown table separators inside a cell."""
        return text.replace("|", "\\|").replace("\n", " ").strip()

    def _is_same_text(self, left: str, right: str) -> bool:
        """Compare OCR text after removing spacing and punctuation noise."""
        if not left or not right:
            return False
        normalize = lambda value: re.sub(r"\W+", "", value, flags=re.UNICODE).lower()
        return normalize(left) == normalize(right)

    def _html_table_to_markdown(self, html: str) -> str:
        """Convert simple HTML table output to Markdown table."""
        if not html or "<table" not in html.lower():
            return ""
        parser = _HTMLTableParser()
        parser.feed(html)
        rows = [[self._escape_table_cell(cell) for cell in row] for row in parser.rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            return ""
        col_count = max(len(row) for row in rows)
        if col_count < 2:
            return "\n".join(" ".join(row).strip() for row in rows)
        padded = [row + [""] * (col_count - len(row)) for row in rows]
        lines = [
            "| " + " | ".join(padded[0]) + " |",
            "| " + " | ".join(["---"] * col_count) + " |",
        ]
        for row in padded[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    def _render_region(self, blocks: List[Dict[str, Any]]) -> str:
        """Render one visual region as text, heading text, or a table."""
        rows = self._group_blocks_into_rows(blocks)
        if not rows:
            return ""
        parts: List[str] = []
        if self._is_region_heading(rows[0], rows):
            parts.append(f"> {self._row_text(rows[0])}")
            rows = rows[1:]
        body = self._render_mixed_rows(rows)
        if body:
            parts.append(body)
        return "\n\n".join(part for part in parts if part)

    def _render_mixed_rows(self, rows: List[List[Dict[str, Any]]]) -> str:
        """Render rows as text, converting only contiguous grid runs to tables."""
        if not rows:
            return ""
        heights = [self._block_metrics(block)["height"] for row in rows for block in row]
        median_height = sorted(heights)[len(heights) // 2] if heights else 12
        # Table-boundary gap must adapt to the region's own row spacing. Some
        # valid tables are sparse (large but *uniform* row gaps); a fixed small
        # threshold would wrongly split every such row into its own 1-row buffer
        # (which then fails table detection and degrades to loose text). We take
        # the median gap between consecutive multi-column rows and only treat a
        # gap as a stacked-table boundary when it clearly exceeds that spacing.
        # The value is clamped to be >= the original threshold, so denser tables
        # keep their previous behaviour and splitting can only become rarer.
        row_bounds = [
            (
                min(self._block_metrics(block)["min_y"] for block in row),
                max(self._block_metrics(block)["max_y"] for block in row),
            )
            for row in rows
        ]
        multi_rows = [row for row in rows if len(row) >= 2]
        multi_gaps = [
            row_bounds[i][0] - row_bounds[i - 1][1]
            for i in range(1, len(rows))
            if len(rows[i]) >= 2 and len(rows[i - 1]) >= 2
        ]
        # A real (grid) table has SHORT cell labels; a two-column *prose* layout
        # has long sentence cells. Only genuine tables may relax the gap, so
        # long-text multi-column regions never get merged into a table.
        cell_lengths = [
            len(block.get("text", "").strip())
            for row in multi_rows for block in row
            if block.get("text", "").strip()
        ]
        median_cell_len = (
            sorted(cell_lengths)[len(cell_lengths) // 2] if cell_lengths else 999
        )
        # A regular sparse table has a STABLE column count across rows; a messy
        # matrix region has wildly varying block counts per row. Only stable
        # regions may relax the gap, so chaotic matrix layouts keep their
        # original (pre-change) splitting behaviour.
        block_counts = sorted(len(row) for row in multi_rows)
        if block_counts:
            median_blocks = block_counts[len(block_counts) // 2]
            max_blocks = block_counts[-1]
            stable_columns = max_blocks <= median_blocks + 1
        else:
            stable_columns = False
        base_gap = max(median_height * 0.7, 10)
        table_gap = base_gap
        if multi_gaps and median_cell_len <= 12 and stable_columns:
            positive_gaps = sorted(g for g in multi_gaps if g > 0)
            if positive_gaps:
                median_gap = positive_gaps[len(positive_gaps) // 2]
                # Only sparse tables (row gaps that are consistently and
                # SIGNIFICANTLY larger than the base threshold) relax the gap.
                # Dense/stacked layouts keep base_gap unchanged, so their
                # splitting behaviour is byte-for-byte identical to before.
                if median_gap > base_gap * 1.5:
                    table_gap = median_gap * 1.4
                # else: leave table_gap == base_gap (no regression).
        chunks: List[str] = []
        buffer: List[List[Dict[str, Any]]] = []

        def flush_buffer() -> None:
            if not buffer:
                return
            if self._looks_like_table_region(buffer):
                chunks.append(self._render_markdown_table(buffer))
            else:
                chunks.append(self._render_rows(buffer))
            buffer.clear()

        previous_bottom: Optional[float] = None
        for row in rows:
            row_top = min(self._block_metrics(block)["min_y"] for block in row)
            row_bottom = max(self._block_metrics(block)["max_y"] for block in row)
            if len(row) >= 2:
                # A wide vertical gap marks the boundary between stacked tables.
                if buffer and previous_bottom is not None and row_top - previous_bottom > table_gap:
                    flush_buffer()
                buffer.append(row)
            else:
                flush_buffer()
                chunks.append(self._render_rows([row]))
            previous_bottom = row_bottom
        flush_buffer()
        return "\n\n".join(chunk for chunk in chunks if chunk)

    def _row_text(self, row: List[Dict[str, Any]]) -> str:
        """Render one row as text."""
        return " ".join(block.get("text", "").strip() for block in row if block.get("text", "").strip())

    def _drop_body_duplicate_tables(
        self, tables: List[Dict[str, Any]], content_blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove tables whose text mostly repeats already-rendered body content."""
        if not tables:
            return tables
        body_texts = {
            re.sub(r"\s+", "", block.get("text", ""))
            for block in content_blocks
            if block.get("type") == "text" and block.get("text", "").strip()
        }
        body_texts.discard("")
        if not body_texts:
            return tables
        kept: List[Dict[str, Any]] = []
        for table in tables:
            cell_texts = self._table_cell_texts(table)
            if len(cell_texts) < 3:
                kept.append(table)
                continue
            matched = sum(1 for cell in cell_texts if cell in body_texts)
            if matched / len(cell_texts) >= 0.6:
                # Content already present in body; skip to avoid duplication.
                continue
            kept.append(table)
        return kept

    def _table_cell_texts(self, table: Dict[str, Any]) -> List[str]:
        """Extract compacted cell strings from a table's cells or HTML."""
        texts: List[str] = []
        cells = table.get("cells")
        if isinstance(cells, list):
            for cell in cells:
                if isinstance(cell, dict):
                    value = cell.get("text", "")
                else:
                    value = str(cell)
                compact = re.sub(r"\s+", "", value or "")
                if compact:
                    texts.append(compact)
        if not texts:
            html = table.get("html", "") or ""
            for chunk in re.findall(r"<td[^>]*>(.*?)</td>", html, flags=re.S):
                compact = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", chunk))
                if compact:
                    texts.append(compact)
        return texts

    def _generate_tables_section(self, tables: List[Dict[str, Any]]) -> str:
        """
        Generate HTML tables section.
        
        Args:
            tables: Table dictionaries
            
        Returns:
            HTML tables string
        """
        if not tables:
            return ""
        
        table_parts = ["## Tables\n"]
        
        for idx, table in enumerate(tables, 1):
            html = table.get("html", "")
            conf = table.get("confidence", 0)
            
            engine = table.get("engine", "table_builder")
            table_parts.append(f"### Table {idx} ({engine}, confidence: {conf:.2f})\n")
            markdown_table = self._html_table_to_markdown(html)
            table_parts.append(markdown_table or html)
        
        return "\n\n".join(table_parts)
    
    def _generate_link_comments(
        self, 
        link_candidates: List[Dict[str, Any]]
    ) -> str:
        """
        Generate link candidate comments.
        
        Args:
            link_candidates: Link candidates from entity linker
            
        Returns:
            HTML comments string
        """
        if not link_candidates:
            return ""
        
        comments = ["<!-- Link Candidates (for review) -->\n"]
        
        for candidate in link_candidates:
            text = candidate.get("text", "")
            target = candidate.get("target", "")
            confidence = candidate.get("confidence", 0)
            
            if confidence > 0.85:
                # High confidence: direct link
                comments.append(f"<!-- LINK: {text} -> [[{target}]] (conf: {confidence:.2f}) -->")
            elif confidence > 0.6:
                # Medium confidence: comment for review
                comments.append(f"<!-- LINK: {text} -> [[{target}]] (conf: {confidence:.2f}) -->")
        
        return "\n".join(comments)


class _HTMLTableParser(HTMLParser):
    """Small HTML table parser for OCR table output."""

    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._current_row: Optional[List[str]] = None
        self._current_cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None:
            cell_text = re.sub(r"\s+", " ", "".join(self._current_cell)).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell.strip() for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None
