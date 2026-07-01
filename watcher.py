"""
File watcher - monitors RAW folder for new images.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    WATCHDOG_AVAILABLE = False

    class FileSystemEventHandler:
        """Fallback base class used when watchdog is not installed."""
        pass


class ImageFileHandler(FileSystemEventHandler):
    """Handle file system events for image files."""
    
    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.heic', '.bmp', '.tiff'}
    
    def __init__(
        self,
        callback: Callable[[str], None],
        config: Dict[str, Any]
    ):
        """
        Initialize file handler.
        
        Args:
            callback: Function to call when new file detected
            config: Configuration dictionary
        """
        super().__init__()
        self.callback = callback
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
        
        self.watch_delay = config.get("processing", {}).get("watch_delay_seconds", 5)
        self.skip_patterns = config.get("processing", {}).get("skip_patterns", [])
        
        # Track pending files to avoid duplicates
        self.pending_files: Set[str] = set()
        self.processed_files: Set[str] = set()
    
    def on_created(self, event):
        """Handle file creation event."""
        if event.is_directory:
            return
        
        file_path = event.src_path
        
        # Check if file should be processed
        if not self._should_process(file_path):
            return
        
        # Check if already pending or processed
        if file_path in self.pending_files or file_path in self.processed_files:
            return
        
        # Add to pending
        self.pending_files.add(file_path)
        
        # Schedule processing after delay (anti-shake)
        self.logger.info(f"New file detected: {file_path}")
        
        # Use a simple delay mechanism
        time.sleep(self.watch_delay)
        
        # Verify file still exists
        if os.path.exists(file_path):
            self.callback(file_path)
        
        # Remove from pending
        self.pending_files.discard(file_path)
        self.processed_files.add(file_path)
    
    def _should_process(self, file_path: str) -> bool:
        """
        Check if file should be processed.
        
        Args:
            file_path: File path to check
            
        Returns:
            True if should process
        """
        # Check extension
        ext = Path(file_path).suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            return False
        
        # Check skip patterns
        for pattern in self.skip_patterns:
            if self._matches_pattern(file_path, pattern):
                return False
        
        return True
    
    def _matches_pattern(self, file_path: str, pattern: str) -> bool:
        """Check if file matches skip pattern."""
        # Simple glob-like pattern matching
        if pattern.startswith("**/"):
            return pattern[3:] in file_path
        elif pattern.startswith("*"):
            return file_path.endswith(pattern[1:])
        else:
            return pattern in file_path


class FolderWatcher:
    """Watch folder for new image files."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize folder watcher.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
        
        vault_config = config.get("vault", {})
        self.root = vault_config.get("root", "")
        self.raw_folder = vault_config.get("raw_folder", "00-RAW")
        
        self.watch_path = os.path.join(self.root, self.raw_folder)
        
        self.observer: Optional[Observer] = None
        self.handler: Optional[ImageFileHandler] = None
        self._running = False
    
    def start(self, callback: Callable[[str], None]) -> bool:
        """
        Start watching folder.
        
        Args:
            callback: Function to call when new file detected
            
        Returns:
            True if successful
        """
        if not WATCHDOG_AVAILABLE:
            self.logger.error("watchdog is not installed. Install dependencies with: pip install -r requirements.txt")
            return False

        try:
            # Ensure watch folder exists
            if not os.path.exists(self.watch_path):
                self.logger.info(f"Creating watch folder: {self.watch_path}")
                os.makedirs(self.watch_path, exist_ok=True)
            
            # Create handler and observer
            self.handler = ImageFileHandler(callback, self.config)
            self.observer = Observer()
            self.observer.schedule(self.handler, self.watch_path, recursive=False)
            
            # Start observer
            self.observer.start()
            self._running = True
            
            self.logger.info(f"Watching folder: {self.watch_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start watcher: {e}")
            return False
    
    def stop(self) -> None:
        """Stop watching folder."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self._running = False
            self.logger.info("Watcher stopped")
    
    def scan_existing_files(self) -> List[str]:
        """
        Scan for existing files in watch folder.
        
        Returns:
            List of file paths
        """
        files = []
        
        if not os.path.exists(self.watch_path):
            return files
        
        for filename in os.listdir(self.watch_path):
            file_path = os.path.join(self.watch_path, filename)
            
            if os.path.isfile(file_path):
                ext = Path(filename).suffix.lower()
                if ext in ImageFileHandler.SUPPORTED_EXTENSIONS:
                    files.append(file_path)
        
        return files
    
    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running
