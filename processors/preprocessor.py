"""
Image preprocessor for OCR pipeline.
Handles document detection, perspective correction, super-resolution, and enhancement.
"""

import cv2
import numpy as np
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseHandler


class ImagePreprocessor(BaseHandler):
    """Preprocess images for OCR processing."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize preprocessor.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.scale = config.get("image", {}).get("super_resolution", {}).get("scale", 2)
    
    def process(self, image_path: str) -> Dict[str, Any]:
        """
        Process an image through the preprocessing pipeline.
        
        Args:
            image_path: Path to input image
            
        Returns:
            Dictionary with processed image and metadata
        """
        # Read image
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Failed to read image: {image_path}")
        
        original_shape = image.shape
        
        # Step 1: Document detection and perspective correction
        corrected_image, corners = self._detect_and_correct(image)
        
        # Step 2: Extract color information before grayscale
        color_info = self._extract_color_info(corrected_image)
        
        # Step 3: Apply super-resolution (if enabled)
        if self.config.get("image", {}).get("super_resolution", {}).get("enabled", True):
            enhanced_image = self._apply_super_resolution(corrected_image)
        else:
            enhanced_image = corrected_image
        
        # Step 4: Grayscale + CLAHE + denoising
        final_image = self._enhance_for_ocr(enhanced_image)
        
        return {
            "original_image": image,
            "corrected_image": corrected_image,
            "enhanced_image": enhanced_image,
            "final_image": final_image,
            "corners": corners,
            "original_shape": original_shape,
            "final_shape": final_image.shape,
            "color_info": color_info
        }
    
    def _detect_and_correct(
        self, 
        image: np.ndarray
    ) -> Tuple[np.ndarray, Optional[List[np.ndarray]]]:
        """
        Detect document corners and apply perspective correction.
        
        Args:
            image: Input image
            
        Returns:
            Tuple of (corrected image, corner points)
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply adaptive thresholding
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        
        # Find contours
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        
        # Find largest quadrilateral contour
        max_area = 0
        best_contour = None
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > 1000:  # Minimum area threshold
                approx = cv2.approxPolyDP(
                    contour, 0.02 * cv2.arcLength(contour, True), True
                )
                
                if len(approx) == 4 and area > max_area:
                    max_area = area
                    best_contour = approx
        
        # If no quadrilateral found, return original image
        if best_contour is None:
            return image, None
        
        # Order corners: top-left, top-right, bottom-right, bottom-left
        corners = self._order_points(best_contour.reshape(4, 2)).astype(np.float32)
        
        # Calculate new image dimensions
        width = int(
            np.linalg.norm(corners[0] - corners[1])
        )
        height = int(
            np.linalg.norm(corners[0] - corners[3])
        )
        
        # Create destination points
        dst_points = np.array([
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1]
        ], dtype=np.float32)
        
        # Apply perspective transform
        matrix = cv2.getPerspectiveTransform(corners, dst_points)
        corrected = cv2.warpPerspective(image, matrix, (width, height))
        
        return corrected, corners.tolist()
    
    def _order_points(self, points: np.ndarray) -> np.ndarray:
        """
        Order points in clockwise direction starting from top-left.
        
        Args:
            points: Array of 4 points
            
        Returns:
            Ordered points
        """
        # Sort by y-coordinate
        sorted_by_y = points[np.argsort(points[:, 1])]
        
        # Top two points
        top_two = sorted_by_y[:2]
        top_left = top_two[np.argmin(top_two[:, 0])]
        top_right = top_two[np.argmax(top_two[:, 0])]
        
        # Bottom two points
        bottom_two = sorted_by_y[2:]
        bottom_left = bottom_two[np.argmin(bottom_two[:, 0])]
        bottom_right = bottom_two[np.argmax(bottom_two[:, 0])]
        
        return np.array([top_left, top_right, bottom_right, bottom_left])
    
    def _extract_color_info(self, image: np.ndarray) -> Dict[str, Any]:
        """
        Extract color information from image before grayscale.
        
        Args:
            image: Color image
            
        Returns:
            Color information dictionary
        """
        # Convert to LAB color space
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        
        # Extract dominant colors using K-Means
        n_clusters = self.config.get("image", {}).get("color_extraction", {}).get(
            "kmeans_clusters", 8
        )
        
        pixels = lab.reshape(-1, 3)
        pixels = np.float32(pixels)
        
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        flags = cv2.KMEANS_RANDOM_CENTERS
        
        compactness, labels, centers = cv2.kmeans(
            pixels, n_clusters, None, criteria, 10, flags
        )
        
        # Convert centers back to BGR
        centers_bgr = cv2.cvtColor(
            centers.reshape(1, -1, 3).astype(np.uint8), 
            cv2.COLOR_LAB2BGR
        ).reshape(-1, 3)
        
        # Convert to hex colors
        hex_colors = [
            "#{:02x}{:02x}{:02x}".format(int(b), int(g), int(r))
            for r, g, b in centers_bgr
        ]
        
        return {
            "dominant_colors": hex_colors,
            "cluster_centers": centers.tolist(),
            "n_clusters": n_clusters
        }
    
    def _apply_super_resolution(self, image: np.ndarray) -> np.ndarray:
        """
        Apply super-resolution to image.
        
        Note: Full Real-ESRGAN implementation requires external model.
        This is a placeholder using OpenCV's upscaling.
        
        Args:
            image: Input image
            
        Returns:
            Upscaled image
        """
        # Placeholder: Use bicubic interpolation
        # In production, integrate Real-ESRGAN or TextZoom
        height, width = image.shape[:2]
        new_width = width * self.scale
        new_height = height * self.scale
        
        upscaled = cv2.resize(
            image, (new_width, new_height), interpolation=cv2.INTER_CUBIC
        )
        
        return upscaled
    
    def _enhance_for_ocr(self, image: np.ndarray) -> np.ndarray:
        """
        Enhance image for OCR: grayscale + CLAHE + denoising.
        
        Args:
            image: Input image
            
        Returns:
            Enhanced grayscale image
        """
        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        # Apply non-local means denoising
        denoised = cv2.fastNlMeansDenoising(enhanced, None, 10, 7, 21)
        
        return denoised
