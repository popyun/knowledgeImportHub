"""
Main entry point for Obsidian Knowledge Import Hub.
"""

import argparse
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List

import yaml

from queue_manager import QueueManager
from watcher import FolderWatcher
from processors.image_handler import ImageHandler
from publishers.obsidian_publisher import ObsidianPublisher
from linkers.entity_linker import EntityLinker
from linkers.disambiguator import Disambiguator
from utils.log_setup import setup_logging, get_logger
from utils.progress import ProgressTracker


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a YAML mapping: {config_path}")

    return config


def validate_config(config: Dict[str, Any]) -> None:
    """Validate required configuration values before starting the pipeline."""
    vault_config = config.get("vault")
    if not isinstance(vault_config, dict):
        raise ValueError("Missing required 'vault' configuration section")

    if not vault_config.get("root"):
        raise ValueError("Missing required 'vault.root' configuration value")

    processing_config = config.get("processing", {})
    max_workers = processing_config.get("max_worker_threads", 1)
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("'processing.max_worker_threads' must be a positive integer")


class PipelineOrchestrator:
    """Orchestrate the complete OCR pipeline."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize orchestrator.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = get_logger()
        
        # Initialize components
        self.queue_manager = QueueManager(config)
        self.image_handler = ImageHandler(config)
        self.publisher = ObsidianPublisher(config)
        self.entity_linker = EntityLinker(config)
        self.disambiguator = Disambiguator(config)
        
        # Progress tracker
        vault_root = config.get("vault", {}).get("root", "")
        audit_folder = config.get("vault", {}).get("audit_folder", "")
        progress_path = os.path.join(vault_root, audit_folder, "progress.json")
        self.progress_tracker = ProgressTracker(progress_path)
        
        # Worker threads
        self.max_workers = config.get("processing", {}).get("max_worker_threads", 2)
        self.workers: List[threading.Thread] = []
        self._stop_workers = False
    
    def initialize(self) -> bool:
        """Initialize all components."""
        try:
            self.image_handler.initialize()
            
            # Build vault index
            vault_root = self.config.get("vault", {}).get("root", "")
            self.entity_linker.build_vault_index(vault_root)
            
            self.logger.info("Pipeline initialized")
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            return False
    
    def process_file(self, file_path: str) -> bool:
        """
        Process a single file through the pipeline.
        
        Args:
            file_path: Path to image file
            
        Returns:
            True if successful
        """
        self.logger.info(f"Processing: {file_path}")
        
        try:
            # Run image through pipeline
            result = self.image_handler.process(file_path)
            
            if not result["success"]:
                raise Exception(result.get("error", "Unknown error"))
            
            # Generate link candidates
            ocr_text = " ".join(
                block.get("text", "") 
                for block in result["ocr_result"].get("blocks", [])
            )
            
            candidates = self.entity_linker.extract_candidates(ocr_text)
            filtered = self.entity_linker.filter_candidates(candidates)
            scored = self.disambiguator.score_candidates(filtered, ocr_text)
            categorized = self.disambiguator.categorize_by_confidence(scored)
            
            # Generate final markdown with links
            all_candidates = (
                categorized["high"] + 
                categorized["medium"]
            )
            
            markdown = self.image_handler.markdown_generator.process(
                result["ocr_result"],
                file_path,
                all_candidates
            )
            
            # Publish note
            note_path = self.publisher.publish(
                markdown,
                file_path,
                all_candidates
            )
            
            if not note_path:
                raise Exception("Failed to publish note")
            
            # Update progress
            self.progress_tracker.update(
                file_path=file_path,
                status="completed"
            )
            
            self.logger.info(f"Successfully processed: {file_path} -> {note_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Processing failed for {file_path}: {e}")
            
            # Update progress
            self.progress_tracker.update(
                file_path=file_path,
                status="failed",
                error=str(e)
            )
            
            return False
    
    def worker_loop(self) -> None:
        """Worker thread loop."""
        while not self._stop_workers:
            # Get next task
            task = self.queue_manager.get_next_pending_task()
            
            if not task:
                # No pending tasks, wait
                time.sleep(1)
                continue
            
            task_id = task["id"]
            file_path = task["file_path"]
            
            # Update status to processing
            self.queue_manager.update_task_status(task_id, "processing")
            
            try:
                # Process file
                success = self.process_file(file_path)
                
                if success:
                    self.queue_manager.update_task_status(task_id, "completed")
                else:
                    self.queue_manager.update_task_status(
                        task_id, 
                        "failed", 
                        "Processing failed"
                    )
            
            except Exception as e:
                self.logger.error(f"Worker error: {e}")
                self.queue_manager.update_task_status(task_id, "failed", str(e))
    
    def start_workers(self) -> None:
        """Start worker threads."""
        self._stop_workers = False
        
        for i in range(self.max_workers):
            worker = threading.Thread(target=self.worker_loop, daemon=True)
            worker.start()
            self.workers.append(worker)
        
        self.logger.info(f"Started {self.max_workers} worker threads")
    
    def stop_workers(self) -> None:
        """Stop worker threads."""
        self._stop_workers = True
        
        for worker in self.workers:
            worker.join(timeout=5)
        
        self.workers.clear()
        self.logger.info("Worker threads stopped")
    
    def start_watcher(self) -> None:
        """Start file watcher."""
        def on_new_file(file_path: str):
            """Callback when new file detected."""
            self.queue_manager.add_task(file_path)
        
        watcher = FolderWatcher(self.config)
        
        if watcher.start(on_new_file):
            # Scan for existing files
            existing_files = watcher.scan_existing_files()
            
            for file_path in existing_files:
                self.queue_manager.add_task(file_path)
            
            # Keep running until stopped
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                watcher.stop()
    
    def run_once(self, file_paths: List[str]) -> None:
        """
        Process files once without watching.
        
        Args:
            file_paths: List of file paths to process
        """
        for file_path in file_paths:
            if os.path.exists(file_path):
                self.process_file(file_path)
            else:
                self.logger.warning(f"File not found: {file_path}")
    
    def print_status(self) -> None:
        """Print queue status."""
        summary = self.queue_manager.get_queue_summary()
        
        print("\n=== Queue Status ===")
        print(f"Total tasks: {summary.get('total', 0)}")
        print(f"  Pending: {summary.get('pending', 0)}")
        print(f"  Processing: {summary.get('processing', 0)}")
        print(f"  Completed: {summary.get('completed', 0)}")
        print(f"  Failed: {summary.get('failed', 0)}")
        
        recent_failures = summary.get("recent_failures", [])
        if recent_failures:
            print("\nRecent failures:")
            for failure in recent_failures[:3]:
                print(f"  - {failure.get('file_path', 'Unknown')}")
                print(f"    Error: {failure.get('last_error', 'Unknown')}")
        
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Obsidian Knowledge Import Hub - OCR Pipeline"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration file"
    )
    
    parser.add_argument(
        "--once",
        type=str,
        nargs="+",
        help="Process specific files once (no watching)"
    )
    
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config_path = args.config
    if not os.path.isabs(config_path):
        # Look for config in current directory and script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(script_dir, config_path)):
            config_path = os.path.join(script_dir, config_path)
    
    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        sys.exit(1)
    
    try:
        config = load_config(config_path)
        validate_config(config)
    except Exception as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    
    # Setup logging
    log_config = config.get("logging", {})
    setup_logging(
        log_file=log_config.get("file", "logs/pipeline.log"),
        level=log_config.get("level", "INFO")
    )
    
    logger = get_logger()
    logger.info("Starting Obsidian Knowledge Import Hub")
    
    # Initialize orchestrator
    orchestrator = PipelineOrchestrator(config)
    
    if not orchestrator.initialize():
        logger.error("Failed to initialize")
        sys.exit(1)
    
    try:
        if args.status:
            # Show status
            orchestrator.print_status()
        
        elif args.once:
            # Process specific files
            logger.info(f"Processing {len(args.once)} files")
            orchestrator.run_once(args.once)
        
        else:
            # Start watcher mode
            logger.info("Starting watcher mode")
            orchestrator.start_workers()
            orchestrator.start_watcher()
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    
    finally:
        orchestrator.stop_workers()
        orchestrator.queue_manager.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
