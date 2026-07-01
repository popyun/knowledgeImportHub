"""
Obsidian publisher - writes notes to Obsidian vault.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.file_utils import sanitize_filename, ensure_directory


class ObsidianPublisher:
    """Publish processed notes to Obsidian vault."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize publisher.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
        
        vault_config = config.get("vault", {})
        self.root = vault_config.get("root", "")
        self.audit_folder = vault_config.get("audit_folder", "99-Audit/OCR-Pending")
        self.raw_folder = vault_config.get("raw_folder", "00-RAW")
        self.wiki_base = vault_config.get("wiki_base", "10-WIKI")
        self.archive_folder = vault_config.get("archive_folder", "99-Archive")
    
    def publish(
        self,
        markdown_content: str,
        source_path: str,
        link_candidates: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
        """
        Publish a note to the Obsidian vault.
        
        Args:
            markdown_content: Complete Markdown content
            source_path: Path to source image
            link_candidates: Optional link candidates
            
        Returns:
            Path to published note, or None on failure
        """
        try:
            # Generate filename
            filename = self._generate_filename(source_path)
            
            # Build full path
            audit_path = os.path.join(self.root, self.audit_folder)
            ensure_directory(audit_path)
            
            note_path = os.path.join(audit_path, filename)
            
            # Write note
            with open(note_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            self.logger.info(f"Published note: {note_path}")
            return note_path
            
        except Exception as e:
            self.logger.error(f"Failed to publish note: {e}")
            return None
    
    def _generate_filename(self, source_path: str) -> str:
        """
        Generate sanitized filename for note.
        
        Args:
            source_path: Source image path
            
        Returns:
            Filename string
        """
        # Get date
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # Extract and sanitize base name
        basename = os.path.basename(source_path)
        name_without_ext = os.path.splitext(basename)[0]
        sanitized = sanitize_filename(name_without_ext)
        
        return f"{date_str}_{sanitized}.md"
    
    def move_to_archive(self, source_path: str) -> bool:
        """
        Move processed file to archive.
        
        Args:
            source_path: Path to source file
            
        Returns:
            True if successful
        """
        try:
            archive_path = os.path.join(self.root, self.archive_folder)
            ensure_directory(archive_path)
            
            filename = os.path.basename(source_path)
            dest_path = os.path.join(archive_path, filename)
            
            # Move file
            os.rename(source_path, dest_path)
            
            self.logger.info(f"Archived: {source_path} -> {dest_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to archive file: {e}")
            return False
    
    def get_vault_index(self) -> List[str]:
        """
        Get index of all note titles in vault.
        
        Returns:
            List of note titles
        """
        titles = []
        
        try:
            wiki_path = os.path.join(self.root, self.wiki_base)
            
            if os.path.exists(wiki_path):
                for root, dirs, files in os.walk(wiki_path):
                    for file in files:
                        if file.endswith('.md'):
                            # Extract title from filename or frontmatter
                            title = os.path.splitext(file)[0]
                            titles.append(title)
        
        except Exception as e:
            self.logger.error(f"Failed to build vault index: {e}")
        
        return titles
