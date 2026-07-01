"""
Structured logging setup for the OCR pipeline.
Provides JSON-formatted logging with consistent fields.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        
        # Add file field if available
        if hasattr(record, 'file_path'):
            log_data["file"] = record.file_path
        
        # Add extra fields
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(
    log_file: str = "logs/pipeline.log",
    level: str = "INFO",
    console_output: bool = True
) -> logging.Logger:
    """
    Set up structured logging for the pipeline.
    
    Args:
        log_file: Path to log file
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_output: Whether to also log to console
        
    Returns:
        Configured logger instance
    """
    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    # Create logger
    logger = logging.getLogger("ocr_pipeline")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create JSON formatter
    json_formatter = JSONFormatter()
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(file_handler)
    
    # Console handler (optional)
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(module)s: %(message)s'
        ))
        console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        logger.addHandler(console_handler)
    
    return logger


def get_logger() -> logging.Logger:
    """Get the pipeline logger, creating it if necessary."""
    logger = logging.getLogger("ocr_pipeline")
    if not logger.handlers:
        return setup_logging()
    return logger
