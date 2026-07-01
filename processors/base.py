"""
Base handler abstract class for the OCR pipeline.
All processors inherit from this base class.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseHandler(ABC):
    """Abstract base class for all pipeline handlers."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize handler with configuration.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self._initialized = False
    
    @abstractmethod
    def process(self, input_data: Any) -> Any:
        """
        Process input data and return result.
        
        Args:
            input_data: Input to process
            
        Returns:
            Processed output
        """
        pass
    
    def initialize(self) -> bool:
        """
        Initialize handler resources (models, connections, etc.).
        
        Returns:
            True if successful, False otherwise
        """
        self._initialized = True
        return True
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self._initialized = False
    
    @property
    def is_initialized(self) -> bool:
        """Check if handler is initialized."""
        return self._initialized
