"""
Color extractor for table cells.
Extracts cell background colors before grayscale conversion.
"""

import cv2
import numpy as np
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseHandler


class ColorExtractor(BaseHandler):
    """Extract cell background colors from table regions."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize color extractor.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.n_clusters = config.get("image", {}).get("color_extraction", {}).get(
            "kmeans_clusters", 8
        )
    
    def process(
        self, 
        image: np.ndarray, 
        table_regions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract colors from table regions.
        
        Args:
            image: Original color image
            table_regions: List of table region dictionaries with bbox
            
        Returns:
            List of color maps for each table region
        """
        color_maps = []
        
        for region in table_regions:
            bbox = region.get("bbox")
            if bbox is None:
                continue
            
            # Crop region
            x, y, w, h = bbox
            region_image = image[y:y+h, x:x+w]
            
            # Extract color grid
            color_grid = self._extract_color_grid(region_image)
            
            color_maps.append({
                "region_bbox": bbox,
                "color_grid": color_grid,
                "rows": len(color_grid),
                "cols": len(color_grid[0]) if color_grid else 0
            })
        
        return color_maps
    
    def _extract_color_grid(
        self, 
        region_image: np.ndarray
    ) -> List[List[str]]:
        """
        Extract a 2D grid of dominant colors from a table region.
        
        Args:
            region_image: Cropped table region image
            
        Returns:
            2D array of hex color strings
        """
        # Convert to LAB color space
        lab = cv2.cvtColor(region_image, cv2.COLOR_BGR2LAB)
        
        # Get image dimensions
        height, width = lab.shape[:2]
        
        # Estimate cell size (assume roughly uniform grid)
        # This is a simplified approach; production would use table structure detection
        cell_height = max(height // 10, 10)  # Assume max 10 rows
        cell_width = max(width // 10, 10)   # Assume max 10 columns
        
        color_grid = []
        
        for row in range(0, height, cell_height):
            row_colors = []
            for col in range(0, width, cell_width):
                # Extract cell region
                cell_y = min(row + cell_height, height)
                cell_x = min(col + cell_width, width)
                cell = lab[row:cell_y, col:cell_x]
                
                # Get dominant color
                dominant_color = self._get_dominant_color(cell)
                row_colors.append(dominant_color)
            
            if row_colors:
                color_grid.append(row_colors)
        
        return color_grid
    
    def _get_dominant_color(self, cell: np.ndarray) -> str:
        """
        Get dominant color from a cell region.
        
        Args:
            cell: Cell region in LAB color space
            
        Returns:
            Hex color string
        """
        # Reshape to pixel list
        pixels = cell.reshape(-1, 3)
        pixels = np.float32(pixels)
        
        # Apply K-Means with small k for single cell
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 5, 1.0)
        flags = cv2.KMEANS_RANDOM_CENTERS
        
        try:
            compactness, labels, centers = cv2.kmeans(
                pixels, min(3, len(pixels)), None, criteria, 10, flags
            )
            
            # Get most common cluster
            unique, counts = np.unique(labels, return_counts=True)
            dominant_idx = unique[np.argmax(counts)]
            dominant_lab = centers[dominant_idx]
            
            # Convert to BGR
            dominant_bgr = cv2.cvtColor(
                np.uint8([[dominant_lab]]), cv2.COLOR_LAB2BGR
            )[0][0]
            
            # Convert to hex
            b, g, r = dominant_bgr
            return "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))
        
        except Exception:
            # Fallback to average color
            avg_color = np.mean(pixels, axis=0)
            avg_bgr = cv2.cvtColor(
                np.uint8([[avg_color]]), cv2.COLOR_LAB2BGR
            )[0][0]
            b, g, r = avg_bgr
            return "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))
