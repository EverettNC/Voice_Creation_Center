"""
================================================================================
FILE: voice_validator.py
PROJECT: Christman Voice Creation Center — Helper Suite
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    Validates all inputs before they reach the engine.
    Checks text content, parameters, and pack integrity.
    The engine never sees dirty data.
================================================================================
"""

import logging
import re
from typing import Optional

logger = logging.getLogger("christman.voice_validator")

# Hard limits
MAX_TEXT_LENGTH = 5000
MIN_TEXT_LENGTH = 1
ALLOWED_OUTPUT_FORMATS = {"wav", "mp3", "flac"}
ALLOWED_SAMPLE_RATES = {8000, 16000, 22050, 44100, 48000}

# Content safety — phrases that should never be synthesized
# by any Christman AI family member
BLOCKED_PATTERNS = [
    r"\b(kill yourself)\b",
    r"\b(you are worthless)\b",
    r"\b(nobody loves you)\b",
    # Extend this list — the safety_gates module handles deeper content scanning
]


def validate_text(text: str) -> tuple[bool, Optional[str]]:
    """
    Validate synthesis text input.
    Returns (is_valid, error_message).
    """
    if not text or not isinstance(text, str):
        return False, "Text must be a non-empty string."

    if len(text.strip()) < MIN_TEXT_LENGTH:
        return False, "Text is empty after stripping whitespace."

    if len(text) > MAX_TEXT_LENGTH:
        return False, f"Text exceeds maximum length of {MAX_TEXT_LENGTH} characters."

    # Content safety check
    text_lower = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text_lower):
            logger.warning(f"Blocked content pattern detected in synthesis request.")
            return False, "Content blocked by safety validator. Rule 14 — dignity always."

    return True, None


def validate_pack_manifest(manifest: dict) -> tuple[bool, list[str]]:
    """
    Validate a loaded pack manifest has required fields and sane values.
    Returns (is_valid, list_of_errors).
    """
    errors = []
    required = ["pack_id", "language", "being_name", "version", "offline_capable"]

    for field in required:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    quality = manifest.get("quality_score", None)
    if quality is not None and not (0.0 <= quality <= 1.0):
        errors.append(f"quality_score out of range: {quality}")

    freq_floor = manifest.get("frequency_floor_hz", 80)
    freq_cap = manifest.get("frequency_cap_hz", 8000)
    if freq_floor >= freq_cap:
        errors.append(f"frequency_floor_hz must be less than frequency_cap_hz.")

    return len(errors) == 0, errors


def validate_output_format(fmt: str) -> tuple[bool, Optional[str]]:
    """Validate output format string."""
    if fmt not in ALLOWED_OUTPUT_FORMATS:
        return False, f"Output format '{fmt}' not supported. Use: {ALLOWED_OUTPUT_FORMATS}"
    return True, None


def validate_sample_rate(rate: int) -> tuple[bool, Optional[str]]:
    """Validate audio sample rate."""
    if rate not in ALLOWED_SAMPLE_RATES:
        return False, f"Sample rate {rate} not supported. Use: {ALLOWED_SAMPLE_RATES}"
    return True, None


def validate_float_param(name: str, value: float) -> tuple[bool, Optional[str]]:
    """Validate a normalized float parameter (0.0 to 1.0)."""
    if not isinstance(value, (int, float)):
        return False, f"{name} must be a number."
    if not (0.0 <= float(value) <= 1.0):
        return False, f"{name} must be between 0.0 and 1.0, got {value}."
    return True, None
