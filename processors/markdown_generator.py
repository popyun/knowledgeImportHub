"""
Markdown generator for OCR pipeline.
Assembles final Markdown note with YAML front matter.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

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
        
        # Generate front matter
        front_matter = self._generate_front_matter(
            source_path=source_path,
            confidence=confidence,
            blocks=blocks,
            tables=tables
        )
        
        # Generate body text
        body_text = self._generate_body_text(blocks)
        
        # Generate tables section
        tables_html = self._generate_tables_section(tables)
        
        # Generate link comments
        link_comments = self._generate_link_comments(link_candidates or [])
        
        # Assemble complete note
        note_parts = [front_matter, body_text, tables_html, link_comments]
        
        return "\n\n".join(part for part in note_parts if part)
    
    def _generate_front_matter(
        self,
        source_path: str,
        confidence: float,
        blocks: List[Dict[str, Any]],
        tables: List[Dict[str, Any]]
    ) -> str:
        """
        Generate YAML front matter.
        
        Args:
            source_path: Source image path
            confidence: OCR confidence score
            blocks: OCR blocks
            tables: Generated table dictionaries
            
        Returns:
            YAML front matter string
        """
        # Generate title from first block or filename
        title = self._extract_title(blocks, source_path)
        
        # Get current date
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # Sanitize source path for wiki link
        source_name = source_path.split("/")[-1].split("\\")[-1]
        
        # Determine tags based on content
        tags = ["ocr/pending"]
        if tables:
            tags.append("ocr/table")
        
        front_matter = f"""---
title: "{title}"
date: {date_str}
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
        """
        Extract or generate title.
        
        Args:
            blocks: OCR blocks
            source_path: Source file path
            
        Returns:
            Title string
        """
        # Try to get title from first text block
        for block in blocks:
            if block.get("type") == "text":
                text = block.get("text", "").strip()
                if text and len(text) < 100:
                    # Clean up text for title
                    title = text.replace('"', "'").replace('\n', ' ')
                    return title[:80]  # Limit length
        
        # Fallback to filename
        filename = source_path.split("/")[-1].split("\\")[-1]
        return filename.rsplit(".", 1)[0]
    
    def _generate_body_text(self, blocks: List[Dict[str, Any]]) -> str:
        """
        Generate body text from OCR blocks.
        
        Args:
            blocks: OCR blocks
            
        Returns:
            Body text string
        """
        text_blocks = []
        
        for block in blocks:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    text_blocks.append(text.strip())
        
        return "\n\n".join(text_blocks)
    
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
            table_parts.append(html)
        
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
