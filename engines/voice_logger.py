"""
================================================================================
FILE: voice_logger.py
PROJECT: Christman Voice Creation Center — Helper Suite
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    Audit logging and observability for the Voice Creation Center.
    Tracks every synthesis request, pack usage, refresh cycle,
    and safety event. All logs stay internal.
    
    The engine logs through here. Every event has a trail.
================================================================================
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_logger")

LOG_DIR = Path(__file__).parent.parent / "logs"
AUDIT_LOG = LOG_DIR / "voice_audit.jsonl"
SAFETY_LOG = LOG_DIR / "voice_safety.jsonl"
HEALTH_LOG = LOG_DIR / "voice_health.jsonl"


def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _write_event(log_file: Path, event: dict) -> None:
    """Append a single event to a JSONL log file."""
    _ensure_log_dir()
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        logger.error(f"Failed to write to {log_file}: {e}")


def log_synthesis(
    pack_id: str,
    being_name: str,
    language: str,
    text_length: int,
    duration_seconds: float,
    success: bool,
    error: Optional[str] = None
) -> None:
    """
    Log a synthesis request.
    Called by voice_engine.py after every render.
    Never logs the actual text — privacy first.
    """
    _write_event(AUDIT_LOG, {
        "event": "synthesis",
        "pack_id": pack_id,
        "being_name": being_name,
        "language": language,
        "text_length": text_length,
        "duration_seconds": round(duration_seconds, 3),
        "success": success,
        "error": error,
    })


def log_pack_refresh(
    pack_id: str,
    old_quality: float,
    new_quality: float,
    refresh_count: int
) -> None:
    """Log a pack refresh event — called by the registry."""
    _write_event(HEALTH_LOG, {
        "event": "pack_refresh",
        "pack_id": pack_id,
        "old_quality": old_quality,
        "new_quality": new_quality,
        "refresh_count": refresh_count,
    })


def log_degradation_detected(
    pack_id: str,
    quality_score: float,
    threshold: float
) -> None:
    """Log when a pack drops below the degradation threshold."""
    _write_event(HEALTH_LOG, {
        "event": "degradation_detected",
        "pack_id": pack_id,
        "quality_score": quality_score,
        "threshold": threshold,
    })


def log_language_learn(
    being_name: str,
    source_pack_id: str,
    target_language: str,
    new_pack_id: str,
    success: bool
) -> None:
    """Log a language learning event."""
    _write_event(AUDIT_LOG, {
        "event": "language_learn",
        "being_name": being_name,
        "source_pack_id": source_pack_id,
        "target_language": target_language,
        "new_pack_id": new_pack_id,
        "success": success,
    })


def log_safety_event(
    event_type: str,
    pack_id: str,
    being_name: str,
    reason: str
) -> None:
    """
    Log a safety gate trigger.
    Called by voice_safety_gates.py when content is blocked.
    Safety events always get their own dedicated log.
    """
    _write_event(SAFETY_LOG, {
        "event": "safety_block",
        "event_type": event_type,
        "pack_id": pack_id,
        "being_name": being_name,
        "reason": reason,
    })
    logger.warning(
        f"SAFETY EVENT [{event_type}] | being={being_name} | pack={pack_id} | reason={reason}"
    )


def get_recent_events(log_file: Path, n: int = 50) -> list[dict]:
    """
    Read the last N events from a log file.
    Used by the Brockston admin dashboard.
    """
    if not log_file.exists():
        return []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        recent = lines[-n:]
        return [json.loads(line) for line in recent if line.strip()]
    except Exception as e:
        logger.error(f"Failed to read log {log_file}: {e}")
        return []


def get_recent_synthesis_events(n: int = 50) -> list[dict]:
    return get_recent_events(AUDIT_LOG, n)


def get_recent_safety_events(n: int = 50) -> list[dict]:
    return get_recent_events(SAFETY_LOG, n)


def get_recent_health_events(n: int = 50) -> list[dict]:
    return get_recent_events(HEALTH_LOG, n)
