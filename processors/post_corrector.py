"""
Post-correction using local LLM (Ollama).
Corrects OCR errors, especially for special characters and ambiguous text.
"""

import logging
import requests
from typing import Any, Dict, List, Optional

from .base import BaseHandler


class PostCorrector(BaseHandler):
    """Post-process OCR results using LLM correction."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize post-corrector.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.logger = logging.getLogger("ocr_pipeline")
        
        ollama_config = config.get("ocr", {}).get("ollama", {})
        self.endpoint = ollama_config.get("endpoint", "http://localhost:11434")
        self.model = ollama_config.get("model", "qwen2.5:1.5b")
        self.confidence_threshold = config.get("processing", {}).get(
            "confidence_threshold", 0.85
        )
    
    def process(
        self, 
        ocr_result: Dict[str, Any],
        content_type: str
    ) -> Dict[str, Any]:
        """
        Correct OCR results using LLM.
        
        Args:
            ocr_result: OCR result dictionary
            content_type: Type of content (text/table/mixed)
            
        Returns:
            Corrected OCR result
        """
        # Check if correction is needed
        avg_confidence = ocr_result.get("confidence", 1.0)
        
        if avg_confidence >= self.confidence_threshold:
            # High confidence, skip correction
            self.logger.debug(f"Skipping correction: confidence {avg_confidence:.2f} >= threshold")
            return ocr_result
        
        self.logger.info(f"Running LLM correction: confidence {avg_confidence:.2f} < threshold")
        
        # Extract text blocks for correction
        blocks = ocr_result.get("blocks", [])
        
        # Correct text blocks (never modify table numeric cells)
        corrected_blocks = []
        for block in blocks:
            if block.get("type") == "text":
                corrected_block = self._correct_block(block, content_type)
                corrected_blocks.append(corrected_block)
            else:
                # Keep table cells as-is
                corrected_blocks.append(block)
        
        # Update result
        ocr_result["blocks"] = corrected_blocks
        
        # Recalculate confidence (improved)
        if corrected_blocks:
            new_confidence = sum(
                b.get("confidence", 0) for b in corrected_blocks
            ) / len(corrected_blocks)
            ocr_result["confidence"] = min(new_confidence * 1.1, 1.0)  # Boost confidence
        
        return ocr_result
    
    def _correct_block(
        self, 
        block: Dict[str, Any],
        content_type: str
    ) -> Dict[str, Any]:
        """
        Correct a single text block.
        
        Args:
            block: Text block dictionary
            content_type: Content type
            
        Returns:
            Corrected block
        """
        text = block.get("text", "")
        confidence = block.get("confidence", 0)
        
        # Skip high-confidence blocks
        if confidence >= 0.95:
            return block
        
        # Check for common OCR errors
        needs_correction = self._detect_ocr_errors(text)
        
        if not needs_correction:
            return block
        
        # Build prompt for LLM
        prompt = self._build_correction_prompt(text, content_type)
        
        try:
            corrected_text = self._call_ollama(prompt)
            
            if corrected_text and corrected_text != text:
                self.logger.debug(f"Corrected: '{text}' -> '{corrected_text}'")
                block["text"] = corrected_text
                block["corrected"] = True
                block["confidence"] = min(confidence + 0.1, 1.0)
        
        except Exception as e:
            self.logger.warning(f"LLM correction failed: {e}")
            # Keep original text
        
        return block
    
    def _detect_ocr_errors(self, text: str) -> bool:
        """
        Detect common OCR error patterns.
        
        Args:
            text: Text to check
            
        Returns:
            True if errors detected
        """
        error_patterns = [
            '0' in text and 'O' in text,  # 0/O confusion
            '1' in text and 'l' in text,  # 1/l confusion
            '5' in text and 'S' in text,  # 5/S confusion
            '8' in text and 'B' in text,  # 8/B confusion
            any(ord(c) > 127 for c in text),  # Special characters
        ]
        
        return any(error_patterns)
    
    def _build_correction_prompt(
        self, 
        text: str,
        content_type: str
    ) -> str:
        """
        Build prompt for LLM correction.
        
        Args:
            text: OCR text
            content_type: Content type
            
        Returns:
            Prompt string
        """
        if content_type == "table":
            return f"""Correct any OCR errors in this table cell text. 
Preserve numbers exactly. Fix only obvious character recognition errors.
Text: {text}
Corrected:"""
        else:
            return f"""Correct any OCR errors in this text. 
Fix common confusions like 0/O, 1/l, 5/S. Preserve proper nouns and technical terms.
Text: {text}
Corrected:"""
    
    def _call_ollama(self, prompt: str) -> Optional[str]:
        """
        Call Ollama API for correction.
        
        Args:
            prompt: Prompt text
            
        Returns:
            Corrected text or None
        """
        try:
            response = requests.post(
                f"{self.endpoint}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 100
                    }
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("response", "").strip()
        
        except requests.RequestException as e:
            self.logger.warning(f"Ollama API call failed: {e}")
        
        return None
