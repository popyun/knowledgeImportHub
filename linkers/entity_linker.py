"""
Entity linker - generates bidirectional wiki-link candidates.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False



class EntityLinker:
    """Generate wiki-link candidates from text."""
    
    # Common words to avoid linking
    BLACKLIST = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'data', 'report', 'file', 'note', 'page',
        '\u7684', '\u4e86', '\u662f', '\u5728', '\u6211', '\u6709', '\u548c', '\u5c31', '\u4e0d', '\u4eba', '\u90fd', '\u4e00'
    }
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize entity linker.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger("ocr_pipeline")
        self.vault_index: Set[str] = set()
    
    def build_vault_index(self, vault_root: str) -> int:
        """
        Build index of existing note titles in vault.
        
        Args:
            vault_root: Root path of Obsidian vault
            
        Returns:
            Number of titles indexed
        """
        self.vault_index.clear()
        
        try:
            for root, dirs, files in os.walk(vault_root):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    if file.endswith('.md'):
                        # Extract title from filename
                        title = os.path.splitext(file)[0]
                        self.vault_index.add(title)
                        
                        # Also try to extract from frontmatter
                        file_path = os.path.join(root, file)
                        title_from_fm = self._extract_title_from_file(file_path)
                        if title_from_fm:
                            self.vault_index.add(title_from_fm)
        
        except Exception as e:
            self.logger.error(f"Failed to build vault index: {e}")
        
        return len(self.vault_index)
    
    def _extract_title_from_file(self, file_path: str) -> Optional[str]:
        """Extract title from Markdown frontmatter."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(500)  # Read first 500 chars
                
                # Look for title in frontmatter
                match = re.search(r'title:\s*["\']?([^"\']+)["\']?', content)
                if match:
                    return match.group(1).strip()
        
        except Exception:
            pass
        
        return None
    
    def extract_candidates(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract link candidates from text.
        
        Args:
            text: Text to analyze
            
        Returns:
            List of candidate dictionaries
        """
        candidates = []
        
        # Tokenize text
        tokens = self._tokenize(text)
        
        # Generate n-grams (1-5 words)
        ngrams = self._generate_ngrams(tokens, max_n=5)
        
        # Score each n-gram against vault index
        for ngram in ngrams:
            ngram_text = ' '.join(ngram) if isinstance(ngram, tuple) else ngram
            
            # Skip blacklist words
            if ngram_text.lower() in self.BLACKLIST:
                continue
            
            # Find matches in vault index
            matches = self._find_matches(ngram_text)
            
            for target, confidence in matches:
                candidates.append({
                    "text": ngram_text,
                    "target": target,
                    "confidence": confidence,
                    "match_type": self._get_match_type(ngram_text, target)
                })
        
        return candidates
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text (Chinese or English).
        
        Args:
            text: Input text
            
        Returns:
            List of tokens
        """
        # Check if text contains Chinese
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
        
        if has_chinese and JIEBA_AVAILABLE:
            # Use jieba for Chinese
            return list(jieba.cut(text))
        else:
            # Simple whitespace tokenization for English
            return re.findall(r'\b\w+\b', text)
    
    def _generate_ngrams(
        self, 
        tokens: List[str], 
        max_n: int = 5
    ) -> List[Tuple[str, ...]]:
        """
        Generate n-grams from tokens.
        
        Args:
            tokens: List of tokens
            max_n: Maximum n-gram size
            
        Returns:
            List of n-grams
        """
        ngrams = []
        
        for n in range(1, min(max_n + 1, len(tokens) + 1)):
            for i in range(len(tokens) - n + 1):
                ngram = tuple(tokens[i:i + n])
                ngrams.append(ngram)
        
        return ngrams
    
    def _find_matches(
        self, 
        candidate: str
    ) -> List[Tuple[str, float]]:
        """
        Find matches for candidate in vault index.
        
        Args:
            candidate: Candidate text
            
        Returns:
            List of (target, confidence) tuples
        """
        matches = []
        candidate_lower = candidate.lower()
        
        for title in self.vault_index:
            title_lower = title.lower()
            
            # Exact match
            if candidate_lower == title_lower:
                matches.append((title, 1.0))
                continue
            
            # Normalized match (ignore punctuation)
            candidate_norm = re.sub(r'[^\w\s]', '', candidate_lower)
            title_norm = re.sub(r'[^\w\s]', '', title_lower)
            
            if candidate_norm == title_norm:
                matches.append((title, 0.9))
                continue
            
            # Partial match (substring)
            if candidate_norm in title_norm or title_norm in candidate_norm:
                # Calculate similarity score
                overlap = len(set(candidate_norm) & set(title_norm))
                total = max(len(candidate_norm), len(title_norm))
                confidence = overlap / total if total > 0 else 0
                
                if confidence > 0.5:
                    matches.append((title, confidence * 0.8))
        
        return matches
    
    def _get_match_type(self, candidate: str, target: str) -> str:
        """Determine match type."""
        if candidate.lower() == target.lower():
            return "exact"
        elif re.sub(r'[^\w\s]', '', candidate.lower()) == re.sub(r'[^\w\s]', '', target.lower()):
            return "normalized"
        else:
            return "partial"
    
    def filter_candidates(
        self,
        candidates: List[Dict[str, Any]],
        min_confidence: float = 0.6
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by confidence and deduplicate.
        
        Args:
            candidates: Raw candidates
            min_confidence: Minimum confidence threshold
            
        Returns:
            Filtered candidates
        """
        filtered = []
        seen_targets: Set[str] = set()
        
        # Sort by confidence (highest first)
        sorted_candidates = sorted(
            candidates, 
            key=lambda c: c.get("confidence", 0),
            reverse=True
        )
        
        for candidate in sorted_candidates:
            confidence = candidate.get("confidence", 0)
            target = candidate.get("target", "")
            
            # Skip low confidence
            if confidence < min_confidence:
                continue
            
            # Skip duplicates
            if target in seen_targets:
                continue
            
            seen_targets.add(target)
            filtered.append(candidate)
        
        return filtered
