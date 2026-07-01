"""
Disambiguator - scores and deduplicates link candidates.
"""

import logging
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict


class Disambiguator:
    """Score and disambiguate link candidates."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize disambiguator.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
    
    def score_candidates(
        self,
        candidates: List[Dict[str, Any]],
        context: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Score candidates based on context and confidence.
        
        Args:
            candidates: Link candidates
            context: Surrounding text context
            
        Returns:
            Scored candidates
        """
        scored = []
        
        for candidate in candidates:
            # Start with base confidence
            base_confidence = candidate.get("confidence", 0)
            
            # Apply bonuses/penalties
            adjusted_confidence = self._adjust_confidence(
                candidate,
                base_confidence,
                context
            )
            
            candidate["adjusted_confidence"] = adjusted_confidence
            scored.append(candidate)
        
        # Sort by adjusted confidence
        scored.sort(key=lambda c: c.get("adjusted_confidence", 0), reverse=True)
        
        return scored
    
    def _adjust_confidence(
        self,
        candidate: Dict[str, Any],
        base_confidence: float,
        context: str
    ) -> float:
        """
        Adjust confidence based on various factors.
        
        Args:
            candidate: Candidate dictionary
            base_confidence: Base confidence score
            context: Surrounding context
            
        Returns:
            Adjusted confidence
        """
        adjusted = base_confidence
        match_type = candidate.get("match_type", "")
        
        # Bonus for exact matches
        if match_type == "exact":
            adjusted = min(adjusted + 0.1, 1.0)
        
        # Check if candidate appears multiple times in context
        candidate_text = candidate.get("text", "")
        if context:
            occurrences = context.lower().count(candidate_text.lower())
            if occurrences > 1:
                # Multiple mentions increase confidence
                adjusted = min(adjusted + 0.05 * (occurrences - 1), 1.0)
        
        # Penalty for very short candidates (likely too generic)
        if len(candidate_text) < 3:
            adjusted *= 0.8
        
        return adjusted
    
    def deduplicate(
        self,
        candidates: List[Dict[str, Any]],
        existing_links: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Deduplicate candidates and remove existing links.
        
        Args:
            candidates: Link candidates
            existing_links: Set of already-linked targets
            
        Returns:
            Deduplicated candidates
        """
        if existing_links is None:
            existing_links = set()
        
        deduped = []
        seen_targets: Set[str] = set(existing_links)
        
        for candidate in candidates:
            target = candidate.get("target", "")
            
            # Skip if already linked
            if target in seen_targets:
                self.logger.debug(f"Skipping duplicate link: {target}")
                continue
            
            seen_targets.add(target)
            deduped.append(candidate)
        
        return deduped
    
    def categorize_by_confidence(
        self,
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Categorize candidates by confidence level.
        
        Args:
            candidates: Link candidates
            
        Returns:
            Dictionary with 'high', 'medium', 'low' categories
        """
        categories = {
            "high": [],      # > 0.85 - auto-link
            "medium": [],    # 0.6 - 0.85 - suggest
            "low": []        # < 0.6 - ignore
        }
        
        for candidate in candidates:
            confidence = candidate.get("adjusted_confidence", candidate.get("confidence", 0))
            
            if confidence > 0.85:
                categories["high"].append(candidate)
            elif confidence >= 0.6:
                categories["medium"].append(candidate)
            else:
                categories["low"].append(candidate)
        
        return categories
