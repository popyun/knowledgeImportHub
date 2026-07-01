"""
Queue manager - SQLite-based persistent task queue.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.file_utils import compute_sha256, ensure_directory


class QueueManager:
    """Manage persistent task queue with SQLite."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize queue manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
        
        vault_root = config.get("vault", {}).get("root", "")
        db_path = os.path.join(vault_root, ".obsidian", "ocr_queue.db")
        
        # Ensure directory exists
        ensure_directory(os.path.dirname(db_path))
        
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                file_hash TEXT,
                status TEXT DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)
        
        # Create index on status for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
        """)
        
        self.conn.commit()
        self.logger.info(f"Queue database initialized: {self.db_path}")
    
    def add_task(self, file_path: str) -> Optional[int]:
        """
        Add a new task to the queue.
        
        Args:
            file_path: Path to file to process
            
        Returns:
            Task ID, or None if duplicate
        """
        try:
            # Compute hash to prevent duplicates
            file_hash = compute_sha256(file_path)
            
            # Check if already exists
            existing = self.get_task_by_hash(file_hash)
            if existing:
                self.logger.debug(f"Duplicate file detected: {file_path}")
                return None
            
            # Insert new task
            cursor = self.conn.cursor()
            now = datetime.utcnow().isoformat()
            
            cursor.execute("""
                INSERT INTO tasks (file_path, file_hash, status, attempts, created_at, updated_at)
                VALUES (?, ?, 'pending', 0, ?, ?)
            """, (file_path, file_hash, now, now))
            
            self.conn.commit()
            
            task_id = cursor.lastrowid
            self.logger.info(f"Added task {task_id}: {file_path}")
            
            return task_id
            
        except Exception as e:
            self.logger.error(f"Failed to add task: {e}")
            return None
    
    def get_next_pending_task(self) -> Optional[Dict[str, Any]]:
        """
        Get the next pending task.
        
        Returns:
            Task dictionary or None
        """
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("""
                SELECT * FROM tasks 
                WHERE status = 'pending' AND attempts < 3
                ORDER BY created_at ASC
                LIMIT 1
            """)
            
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get next task: {e}")
            return None
    
    def update_task_status(
        self,
        task_id: int,
        status: str,
        error: Optional[str] = None
    ) -> None:
        """
        Update task status.
        
        Args:
            task_id: Task ID
            status: New status (pending/processing/completed/failed)
            error: Error message if failed
        """
        try:
            cursor = self.conn.cursor()
            now = datetime.utcnow().isoformat()
            
            if status == "failed":
                # Increment attempts on failure
                cursor.execute("""
                    UPDATE tasks 
                    SET status = ?, last_error = ?, attempts = attempts + 1, updated_at = ?
                    WHERE id = ?
                """, (status, error, now, task_id))
            else:
                cursor.execute("""
                    UPDATE tasks 
                    SET status = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                """, (status, error, now, task_id))
            
            self.conn.commit()
            
            self.logger.debug(f"Task {task_id} -> {status}")
            
        except Exception as e:
            self.logger.error(f"Failed to update task: {e}")
    
    def get_task_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get task by file hash.
        
        Args:
            file_hash: SHA-256 hash
            
        Returns:
            Task dictionary or None
        """
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("""
                SELECT * FROM tasks WHERE file_hash = ?
            """, (file_hash,))
            
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get task by hash: {e}")
            return None
    
    def get_queue_summary(self) -> Dict[str, Any]:
        """
        Get queue status summary.
        
        Returns:
            Summary dictionary
        """
        try:
            cursor = self.conn.cursor()
            
            # Count by status
            cursor.execute("""
                SELECT status, COUNT(*) as count 
                FROM tasks 
                GROUP BY status
            """)
            
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}
            
            # Get total
            total = sum(status_counts.values())
            
            # Get recent failures
            cursor.execute("""
                SELECT file_path, last_error, updated_at 
                FROM tasks 
                WHERE status = 'failed' 
                ORDER BY updated_at DESC 
                LIMIT 5
            """)
            
            recent_failures = [dict(row) for row in cursor.fetchall()]
            
            return {
                "total": total,
                "pending": status_counts.get("pending", 0),
                "processing": status_counts.get("processing", 0),
                "completed": status_counts.get("completed", 0),
                "failed": status_counts.get("failed", 0),
                "recent_failures": recent_failures
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get queue summary: {e}")
            return {}
    
    def resume_pending_tasks(self) -> List[Dict[str, Any]]:
        """
        Get all pending or retryable failed tasks.
        
        Returns:
            List of task dictionaries
        """
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("""
                SELECT * FROM tasks 
                WHERE status = 'pending' 
                   OR (status = 'failed' AND attempts < 3)
                ORDER BY created_at ASC
            """)
            
            return [dict(row) for row in cursor.fetchall()]
            
        except Exception as e:
            self.logger.error(f"Failed to resume tasks: {e}")
            return []
    
    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
