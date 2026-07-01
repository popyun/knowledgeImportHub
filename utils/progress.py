"""
Progress tracking for the OCR pipeline.
Handles progress.json for resume capability.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProgressTracker:
    """Track processing progress for resume capability."""
    
    def __init__(self, progress_file: str):
        """
        Initialize progress tracker.
        
        Args:
            progress_file: Path to progress.json file
        """
        self.progress_file = progress_file
        self.data: Dict[str, Any] = self._load()
    
    def _load(self) -> Dict[str, Any]:
        """Load progress data from file."""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            "last_processed_id": None,
            "last_processed_file": None,
            "last_updated": None,
            "total_processed": 0,
            "total_failed": 0,
            "history": []
        }
    
    def _save(self) -> None:
        """Save progress data to file."""
        # Ensure directory exists
        Path(self.progress_file).parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def update(
        self,
        task_id: Optional[int] = None,
        file_path: Optional[str] = None,
        status: str = "completed",
        error: Optional[str] = None
    ) -> None:
        """
        Update progress after processing a file.
        
        Args:
            task_id: Task ID from queue
            file_path: Path to processed file
            status: Processing status (completed/failed)
            error: Error message if failed
        """
        self.data["last_processed_id"] = task_id
        self.data["last_processed_file"] = file_path
        self.data["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        if status == "completed":
            self.data["total_processed"] = self.data.get("total_processed", 0) + 1
        elif status == "failed":
            self.data["total_failed"] = self.data.get("total_failed", 0) + 1
        
        # Add to history (keep last 100)
        history_entry = {
            "task_id": task_id,
            "file_path": file_path,
            "status": status,
            "error": error,
            "timestamp": self.data["last_updated"]
        }
        self.data["history"].append(history_entry)
        self.data["history"] = self.data["history"][-100:]
        
        self._save()
    
    def get_last_processed(self) -> Optional[str]:
        """Get the last processed file path."""
        return self.data.get("last_processed_file")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics."""
        return {
            "total_processed": self.data.get("total_processed", 0),
            "total_failed": self.data.get("total_failed", 0),
            "last_updated": self.data.get("last_updated"),
            "last_file": self.data.get("last_processed_file")
        }
