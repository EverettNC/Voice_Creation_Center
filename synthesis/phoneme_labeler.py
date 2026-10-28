"""
Phoneme Labeling Module - Stage 1: Phoneme Extraction

Extracts phoneme-level timing and labels from audio.
Uses Montreal Forced Aligner for precise alignment with a 
native energy-based fallback to ensure system stability.

Patent Pending TCAP-2026-001 / TCAP-2026-002
© 2026 Everett Nathaniel Christman & Misty Gail Christman
The Christman AI Project — Luma Cognify AI
Truth. Dignity. Protection. Transparency. No Erasure.
"""

import subprocess
import tempfile
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List, Dict, Optional
from logger import get_logger
from audio.config import get_config

logger = get_logger(__name__)

# Optional dependencies for MFA alignment
try:
    import textgrid
    TEXTGRID_AVAILABLE = True
except ImportError:
    TEXTGRID_AVAILABLE = False
    logger.warning("textgrid library not found. MFA alignment will be unavailable.")

class Phoneme:
    """Represents a phoneme with timing information."""
    
    def __init__(self, label: str, start_time: float, end_time: float, confidence: float = 1.0):
        self.label = label
        self.start_time = start_time
        self.end_time = end_time
        self.duration = end_time - start_time
        self.confidence = confidence
    
    def to_dict(self) -> Dict:
        return {
            "label": self.label,
            "start": self.start_time,
            "end": self.end_time,
            "duration": self.duration,
            "confidence": self.confidence
        }
    
    """
Phoneme Labeling Module - Stage 1: Phoneme Extraction

Extracts phoneme-level timing and labels from audio.
Uses Montreal Forced Aligner for precise alignment with a 
native energy-based fallback to ensure system stability.

Patent Pending TCAP-2026-001 / TCAP-2026-002
© 2026 Everett Nathaniel Christman & Misty 
