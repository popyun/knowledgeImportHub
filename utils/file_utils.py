"""
File utilities for the OCR pipeline.
Provides SHA-256 hashing, safe file operations, and path normalization.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional


def compute_sha256(file_path: str) -> str:
    """
    Compute SHA-256 hash of a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Hexadecimal hash string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def safe_delete(file_path: str) -> bool:
    """
    Safely delete a file, moving to trash if possible.
    
    Args:
        file_path: Path to the file to delete
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Try to move to trash first (platform-dependent)
        if os.name == 'nt':  # Windows
            import subprocess
            subprocess.run(['powershell', '-Command', f'Recycle-Bin -Path "{file_path}"'], 
                         capture_output=True)
        else:
            os.remove(file_path)
        return True
    except Exception:
        # Fallback to permanent delete
        try:
            os.remove(file_path)
            return True
        except Exception:
            return False


def normalize_path(path: str) -> str:
    """
    Normalize a file path (resolve .., ., etc.).
    
    Args:
        path: Input path string
        
    Returns:
        Normalized absolute path
    """
    return os.path.normpath(os.path.abspath(path))


def ensure_directory(dir_path: str) -> bool:
    """
    Ensure a directory exists, create if necessary.
    
    Args:
        dir_path: Directory path to ensure
        
    Returns:
        True if directory exists or was created, False on error
    """
    try:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def move_file(src: str, dst: str) -> bool:
    """
    Move a file from src to dst, creating directories as needed.
    
    Args:
        src: Source file path
        dst: Destination file path
        
    Returns:
        True if successful, False otherwise
    """
    try:
        ensure_directory(os.path.dirname(dst))
        shutil.move(src, dst)
        return True
    except Exception:
        return False


def get_temp_file(suffix: str = ".png", prefix: str = "ocr_") -> str:
    """
    Create a temporary file path.
    
    Args:
        suffix: File extension
        prefix: Filename prefix
        
    Returns:
        Path to temporary file
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(fd)
    return path


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename for safe use (ASCII only, hyphens for spaces).
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    # Remove or replace problematic characters
    safe_chars = []
    for c in filename:
        if c.isalnum() or c in ' -_.':
            safe_chars.append(c)
        else:
            safe_chars.append('_')
    
    sanitized = ''.join(safe_chars)
    # Replace spaces with hyphens
    sanitized = sanitized.replace(' ', '-')
    # Remove consecutive hyphens
    while '--' in sanitized:
        sanitized = sanitized.replace('--', '-')
    
    return sanitized
