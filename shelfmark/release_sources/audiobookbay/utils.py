"""Utility functions for AudiobookBay integration."""

import re
from typing import Optional


def sanitize_title(title: str) -> str:
    """Remove characters that are invalid in filenames.
    
    Args:
        title: Book title
        
    Returns:
        Sanitized title
    """
    return re.sub(r'[<>:"/\\|?*]', '', title).strip()


def parse_size(size_str: Optional[str]) -> Optional[int]:
    """Parse size string to bytes.
    
    Args:
        size_str: Size string (e.g., "1.5 GB", "500 MB", "11.68 GBs")
        
    Returns:
        Size in bytes, or None if parsing fails
    """
    if not size_str:
        return None
    
    # Match number and unit, handling "GBs" as well as "GB" (case-insensitive)
    match = re.search(r'([\d.]+)\s*([BKMGT]B?)S?', size_str.upper())
    if not match:
        return None
    
    value = float(match.group(1))
    unit = match.group(2)
    
    multipliers = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 ** 2,
        'GB': 1024 ** 3,
        'TB': 1024 ** 4,
    }
    
    return int(value * multipliers.get(unit, 1))
