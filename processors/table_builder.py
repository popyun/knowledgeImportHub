"""
Table builder for OCR pipeline.
Reconstructs HTML tables with colors, rowspan, and colspan.
"""

import logging
import html
from typing import Any, Dict, List, Optional

from .base import BaseHandler


class TableBuilder(BaseHandler):
    """Build HTML tables from OCR results and color maps."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize table builder.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.logger = logging.getLogger("ocr_pipeline")
    
    def process(
        self,
        ocr_blocks: List[Dict[str, Any]],
        color_maps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Build HTML tables from OCR blocks and color information.
        
        Args:
            ocr_blocks: OCR result blocks
            color_maps: Color maps from color extractor
            
        Returns:
            List of table dictionaries with HTML
        """
        tables = []
        
        # Group blocks into tables based on spatial arrangement
        table_groups = self._group_blocks_into_tables(ocr_blocks)
        
        for idx, table_group in enumerate(table_groups):
            # Get color map for this table region
            color_map = None
            if idx < len(color_maps):
                color_map = color_maps[idx].get("color_grid", [])
            
            # Build HTML table
            html_table = self._build_html_table(table_group, color_map)
            
            tables.append({
                "html": html_table,
                "cells": table_group,
                "color_map": color_map,
                "confidence": self._calculate_table_confidence(table_group)
            })
        
        return tables
    
    def _bbox_metrics(self, block: Dict[str, Any]) -> Dict[str, float]:
        """Return normalized bbox metrics for an OCR block."""
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

    def _row_tolerance(self, blocks: List[Dict[str, Any]]) -> float:
        """Calculate row grouping tolerance from OCR box heights."""
        heights = sorted(self._bbox_metrics(block)["height"] for block in blocks)
        if not heights:
            return 10
        median_height = heights[len(heights) // 2]
        return max(median_height * 0.7, 8)


    def _group_blocks_into_tables(
        self,
        blocks: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """Group OCR blocks into table structures when layout is grid-like."""
        if len(blocks) < 4:
            return []

        rows = self._organize_cells_into_rows(blocks)
        rows_with_multiple = [row for row in rows if len(row) >= 2]
        max_cols = max((len(row) for row in rows), default=0)

        if len(rows_with_multiple) < 2 or max_cols < 2:
            return []

        sorted_blocks = [cell for row in rows for cell in row]
        return [sorted_blocks]


    
    def _build_html_table(
        self,
        cells: List[Dict[str, Any]],
        color_map: Optional[List[List[str]]]
    ) -> str:
        """
        Build HTML table string from cells and colors.
        
        Args:
            cells: Table cells
            color_map: 2D color grid
            
        Returns:
            HTML table string
        """
        if not cells:
            return "<table></table>"
        
        html_parts = ['<table border="1" style="border-collapse: collapse;">']
        
        # Group cells into rows
        rows = self._organize_cells_into_rows(cells)
        
        for row_idx, row in enumerate(rows):
            html_parts.append("<tr>")
            
            for col_idx, cell in enumerate(row):
                # Get background color
                bgcolor = None
                if color_map and row_idx < len(color_map):
                    if col_idx < len(color_map[row_idx]):
                        bgcolor = color_map[row_idx][col_idx]
                
                # Build cell
                text = html.escape(str(cell.get("text", "")))
                
                if bgcolor:
                    html_parts.append(f'<td bgcolor="{bgcolor}">{text}</td>')
                else:
                    html_parts.append(f'<td>{text}</td>')
            
            html_parts.append("</tr>")
        
        html_parts.append("</table>")
        
        return "".join(html_parts)
    
    def _organize_cells_into_rows(
        self,
        cells: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """Organize cells into rows using adaptive center-y clustering."""
        if not cells:
            return []

        tolerance = self._row_tolerance(cells)
        sorted_cells = sorted(cells, key=lambda c: self._bbox_metrics(c)["center_y"])
        rows = []

        for cell in sorted_cells:
            metrics = self._bbox_metrics(cell)
            if not rows:
                rows.append([cell])
                continue

            current_row = rows[-1]
            row_center = sum(self._bbox_metrics(item)["center_y"] for item in current_row) / len(current_row)
            if abs(metrics["center_y"] - row_center) <= tolerance:
                current_row.append(cell)
            else:
                rows.append([cell])

        for row in rows:
            row.sort(key=lambda c: self._bbox_metrics(c)["center_x"])

        return rows


    
    def _calculate_table_confidence(
        self, 
        cells: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate average confidence for table cells.
        
        Args:
            cells: Table cells
            
        Returns:
            Average confidence score
        """
        if not cells:
            return 0.0
        
        confidences = [
            cell.get("confidence", 0.5) 
            for cell in cells
        ]
        
        return sum(confidences) / len(confidences)
