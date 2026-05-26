"""
================================================================================
FILE: voice_express.py
PROJECT: Christman Voice Creation Center — Express Service
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    The Express Lane of the Christman Voice Creation Center.

    Two fast tracks:

    TRACK 1 — PRE-RENDERED PHRASE CACHE
        Common, high-frequency phrases pre-rendered per being and stored
        as ready-to-serve audio. Zero synthesis time. Instant delivery.
        
        Examples:
        AlphaVox  → "I love you", "I need help", "I'm hungry"
        AlphaWolf → "You're safe", "Let's go home", "I'm here with you"
        Inferno   → "You're not alone", "Breathe with me", "You are safe"
        Aegis     → "Alert sent", "Help is coming", "You are protected"

    TRACK 2 — PRIORITY SYNTHESIS QUEUE
        Requests that can't use pre-rendered audio but need synthesis
        RIGHT NOW. Safety alerts, crisis responses, urgent AAC communication.
        Jumps to the front of the mill. No waiting.

    The express lane never compromises quality or safety.
    It just removes the wait.

CARDINAL RULE 13: No stubs. Pre-rendered audio is real audio.
================================================================================
"""

import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_express")

# Internal paths — all express assets live inside the center
EXPRESS_ROOT    = Path(__file__).parent.parent / "express"
PHRASE_CACHE    = EXPRESS_ROOT / "phrase_cache"
PRIORITY_QUEUE  = EXPRESS_ROOT / "priority_queue"
EXPRESS_INDEX   = EXPRESS_ROOT / "express_index.json"

# Performance targets
EXPRESS_TARGET_MS  = 50    # Pre-rendered: serve in under 50ms
PRIORITY_TARGET_MS = 500   # Priority synthesis: complete in under 500ms

# Priority levels
class Priority(str, Enum):
    CRITICAL = "critical"   # Aegis safety alert, crisis intervention
    HIGH     = "high"       # AAC urgent communication, medical alert
    STANDARD = "standard"   # Normal express request
    PRELOAD  = "preload"    # Background preloading — lowest priority


@dataclass
class ExpressPhrase:
    """
    A pre-rendered phrase stored in the express cache.
    Ready to serve in milliseconds.
    """
    phrase_id: str           # Hash of being + language + text
    being_name: str
    language: str
    text: str
    audio_path: str          # Internal path to pre-rendered audio file
    duration_seconds: float
    sample_rate: int
    quality_score: float
    serve_count: int = 0
    last_served_at: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass 
class PriorityRequest:
    """
    A synthesis request that needs to jump the queue.
    """
    request_id: str
    being_name: str
    pack_id: str
    text: str
    language: str
    priority: Priority
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    audio_data: Optional[bytes] = None
    error: Optional[str] = None

    def age_ms(self) -> float:
        """How long has this request been waiting, in milliseconds."""
        submitted = datetime.fromisoformat(self.submitted_at)
        now = datetime.now(timezone.utc)
        return (now - submitted).total_seconds() * 1000


@dataclass
class ExpressResult:
    """
    Result from an express service request.
    Always check track and success before using audio.
    """
    success: bool
    track: str               # "pre_rendered" or "priority_synthesis"
    audio_data: Optional[bytes] = None
    audio_path: Optional[str] = None
    duration_seconds: float = 0.0
    serve_time_ms: float = 0.0
    from_cache: bool = False
    phrase_id: Optional[str] = None
    error: Optional[str] = None

    def __repr__(self) -> str:
        if self.success:
            return (
                f"ExpressResult(track={self.track}, "
                f"serve_time={self.serve_time_ms:.1f}ms, "
                f"from_cache={self.from_cache})"
            )
        return f"ExpressResult(success=False, error='{self.error}')"


class VoiceExpress:
    """
    The Express Lane of the Christman Voice Creation Center.

    Usage:
        express = VoiceExpress()
        express.load()

        # Serve a pre-rendered phrase instantly
        result = express.serve("I love you", being_name="AlphaVox", language="en-US")

        # Jump the queue for urgent synthesis
        result = express.priority_synthesize(
            text="Help is on the way.",
            being_name="Aegis",
            pack_id="aegis_alert_en_us",
            priority=Priority.CRITICAL
        )
    """

    def __init__(self):
        self._phrases: dict[str, ExpressPhrase] = {}
        self._priority_queue: list[PriorityRequest] = []
        self._stats = defaultdict(int)
        self._loaded = False

        # Ensure internal directories exist
        PHRASE_CACHE.mkdir(parents=True, exist_ok=True)
        PRIORITY_QUEUE.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------
    # Load & Save
    # -------------------------------------------------------------------

    def load(self) -> bool:
        """Load the express index from disk."""
        if not EXPRESS_INDEX.exists():
            logger.info("No express index found — starting fresh.")
            self._loaded = True
            self._save()
            return True

        try:
            with open(EXPRESS_INDEX, "r", encoding="utf-8") as f:
                data = json.load(f)

            for phrase_data in data.get("phrases", []):
                phrase = ExpressPhrase(**phrase_data)
                self._phrases[phrase.phrase_id] = phrase

            self._loaded = True
            logger.info(
                f"Express service loaded — "
                f"{len(self._phrases)} pre-rendered phrases ready."
            )
            return True

        except Exception as e:
            logger.error(f"Express load failed: {e}", exc_info=True)
            return False

    def _save(self) -> None:
        """Persist the express index."""
        try:
            data = {
                "meta": {
                    "version": "1.0.0",
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "total_phrases": len(self._phrases),
                    "express_target_ms": EXPRESS_TARGET_MS,
                    "priority_target_ms": PRIORITY_TARGET_MS,
                },
                "phrases": [p.to_dict() for p in self._phrases.values()],
            }
            with open(EXPRESS_INDEX, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Express save failed: {e}", exc_info=True)

    # -------------------------------------------------------------------
    # Track 1 — Pre-Rendered Phrase Cache
    # -------------------------------------------------------------------

    def serve(
        self,
        text: str,
        being_name: str,
        language: str = "en-US"
    ) -> ExpressResult:
        """
        Serve a pre-rendered phrase from the express cache.
        Target: under 50ms.

        If the phrase isn't pre-rendered, returns a miss result.
        Caller should fall back to priority_synthesize() on miss.
        """
        self._require_loaded()
        start = time.perf_counter()

        phrase_id = self._make_phrase_id(text, being_name, language)
        phrase = self._phrases.get(phrase_id)

        if phrase is None:
            elapsed = (time.perf_counter() - start) * 1000
            self._stats["cache_misses"] += 1
            logger.debug(f"Express cache miss: '{text[:30]}' for {being_name}")
            return ExpressResult(
                success=False,
                track="pre_rendered",
                from_cache=False,
                serve_time_ms=elapsed,
                error="Phrase not in express cache. Use priority_synthesize() for dynamic text."
            )

        # Verify the audio file still exists internally
        audio_path = Path(phrase.audio_path)
        if not audio_path.exists():
            logger.error(
                f"Express audio file missing for phrase {phrase_id}. "
                f"Re-render required."
            )
            del self._phrases[phrase_id]
            self._save()
            return ExpressResult(
                success=False,
                track="pre_rendered",
                error=f"Pre-rendered audio file missing. Phrase removed from cache."
            )

        # Serve it
        try:
            with open(audio_path, "rb") as f:
                audio_data = f.read()

            # Update usage stats
            phrase.serve_count += 1
            phrase.last_served_at = datetime.now(timezone.utc).isoformat()
            self._stats["cache_hits"] += 1

            elapsed = (time.perf_counter() - start) * 1000

            if elapsed > EXPRESS_TARGET_MS:
                logger.warning(
                    f"Express target missed: {elapsed:.1f}ms "
                    f"(target {EXPRESS_TARGET_MS}ms) for phrase {phrase_id}"
                )

            logger.debug(f"Express served in {elapsed:.1f}ms: '{text[:30]}' for {being_name}")

            return ExpressResult(
                success=True,
                track="pre_rendered",
                audio_data=audio_data,
                audio_path=str(audio_path),
                duration_seconds=phrase.duration_seconds,
                serve_time_ms=elapsed,
                from_cache=True,
                phrase_id=phrase_id
            )

        except Exception as e:
            logger.error(f"Express serve failed for {phrase_id}: {e}")
            return ExpressResult(
                success=False,
                track="pre_rendered",
                error=str(e)
            )

    def register_phrase(
        self,
        text: str,
        being_name: str,
        language: str,
        audio_path: str,
        duration_seconds: float,
        sample_rate: int = 22050,
        quality_score: float = 1.0
    ) -> ExpressPhrase:
        """
        Register a pre-rendered phrase into the express cache.
        Called after the factory pre-renders a phrase set.

        The audio file must already exist at audio_path before calling this.
        Rule 13 — we don't register what doesn't exist.
        """
        self._require_loaded()

        if not Path(audio_path).exists():
            raise FileNotFoundError(
                f"Cannot register phrase — audio file not found: {audio_path}"
            )

        phrase_id = self._make_phrase_id(text, being_name, language)
        phrase = ExpressPhrase(
            phrase_id=phrase_id,
            being_name=being_name,
            language=language,
            text=text,
            audio_path=audio_path,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            quality_score=quality_score
        )

        self._phrases[phrase_id] = phrase
        self._save()

        logger.info(
            f"Express phrase registered: '{text[:40]}' "
            f"for {being_name} [{language}]"
        )
        return phrase

    def get_phrases_for_being(self, being_name: str) -> list[ExpressPhrase]:
        """Get all pre-rendered phrases for a specific being."""
        self._require_loaded()
        return [
            p for p in self._phrases.values()
            if p.being_name.lower() == being_name.lower()
        ]

    def is_cached(self, text: str, being_name: str, language: str = "en-US") -> bool:
        """Quick check — is this phrase already in the express cache?"""
        phrase_id = self._make_phrase_id(text, being_name, language)
        return phrase_id in self._phrases

    # -------------------------------------------------------------------
    # Track 2 — Priority Synthesis Queue
    # -------------------------------------------------------------------

    def priority_synthesize(
        self,
        text: str,
        being_name: str,
        pack_id: str,
        language: str = "en-US",
        priority: Priority = Priority.HIGH
    ) -> PriorityRequest:
        """
        Submit a request to the priority synthesis queue.
        Jumps to the front of the mill based on priority level.

        CRITICAL priority → processed immediately before anything else.
        HIGH priority     → next in queue after CRITICAL.
        STANDARD priority → standard express, still faster than main queue.

        Returns a PriorityRequest. The caller polls get_result() or
        registers a callback via the voice_engine orchestrator.
        """
        self._require_loaded()

        request_id = (
            f"express_{being_name.lower()}_{priority.value}"
            f"_{int(time.time() * 1000)}"
        )

        request = PriorityRequest(
            request_id=request_id,
            being_name=being_name,
            pack_id=pack_id,
            text=text,
            language=language,
            priority=priority
        )

        # Insert by priority — CRITICAL goes to front
        if priority == Priority.CRITICAL:
            self._priority_queue.insert(0, request)
            logger.warning(
                f"CRITICAL express request: {being_name} | "
                f"'{text[:40]}' | request_id={request_id}"
            )
        elif priority == Priority.HIGH:
            # Insert after any CRITICAL requests
            insert_at = next(
                (i for i, r in enumerate(self._priority_queue)
                 if r.priority != Priority.CRITICAL),
                len(self._priority_queue)
            )
            self._priority_queue.insert(insert_at, request)
            logger.info(f"HIGH priority express queued: {request_id}")
        else:
            self._priority_queue.append(request)
            logger.info(f"STANDARD priority express queued: {request_id}")

        self._stats["priority_requests"] += 1
        return request

    def get_next_priority_request(self) -> Optional[PriorityRequest]:
        """
        Get the next request from the priority queue.
        Called by the voice_engine synthesis loop.
        """
        self._require_loaded()
        if not self._priority_queue:
            return None
        return self._priority_queue.pop(0)

    def get_queue_depth(self) -> dict:
        """Current priority queue depth by priority level."""
        depth = {p.value: 0 for p in Priority}
        for req in self._priority_queue:
            depth[req.priority.value] += 1
        return depth

    # -------------------------------------------------------------------
    # Pre-Loading — Default Phrase Sets Per Being
    # -------------------------------------------------------------------

    DEFAULT_PHRASES = {
        "AlphaVox": [
            "I love you",
            "I need help",
            "I am hungry",
            "I am tired",
            "I need water",
            "I am in pain",
            "Please stop",
            "Yes",
            "No",
            "Thank you",
            "I am happy",
            "I am scared",
        ],
        "AlphaWolf": [
            "You are safe",
            "Let's go home",
            "I am here with you",
            "Everything is okay",
            "Time for your medicine",
            "Someone is coming to help",
            "You are loved",
            "Let's sit down together",
        ],
        "Inferno": [
            "You are not alone",
            "Breathe with me",
            "You are safe right now",
            "This feeling will pass",
            "I am here",
            "Take your time",
            "You are doing so well",
            "Let's breathe together",
        ],
        "Aegis": [
            "Alert sent",
            "Help is on the way",
            "You are protected",
            "Stay where you are",
            "Emergency services notified",
            "You are safe",
        ],
        "OmegaAlpha": [
            "Good morning",
            "Time for your medication",
            "How are you feeling today",
            "Your family has been notified",
            "Let me help you with that",
            "You look wonderful today",
        ],
        "Omega": [
            "Turn left",
            "Turn right",
            "Stop",
            "Destination reached",
            "Obstacle ahead",
            "Rerouting",
        ],
    }

    def get_preload_list(self, being_name: str) -> list[str]:
        """
        Get the default phrases that should be pre-rendered
        for a given being. Called during initial setup or
        after a pack refresh.
        """
        return self.DEFAULT_PHRASES.get(being_name, [])

    def get_unrendered_phrases(self, being_name: str, language: str) -> list[str]:
        """
        Returns phrases from the default set that haven't been
        pre-rendered yet. Used by the factory to know what to render.
        """
        defaults = self.get_preload_list(being_name)
        return [
            phrase for phrase in defaults
            if not self.is_cached(phrase, being_name, language)
        ]

    # -------------------------------------------------------------------
    # Stats & Status
    # -------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Express service statistics for Brockston dashboard."""
        self._require_loaded()
        total = self._stats["cache_hits"] + self._stats["cache_misses"]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_phrases_cached": len(self._phrases),
            "cache_hits": self._stats["cache_hits"],
            "cache_misses": self._stats["cache_misses"],
            "hit_rate": round(
                self._stats["cache_hits"] / total, 3
            ) if total > 0 else 0.0,
            "priority_requests_served": self._stats["priority_requests"],
            "priority_queue_depth": self.get_queue_depth(),
            "express_target_ms": EXPRESS_TARGET_MS,
            "priority_target_ms": PRIORITY_TARGET_MS,
        }

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _make_phrase_id(self, text: str, being_name: str, language: str) -> str:
        """
        Generate a stable, unique ID for a phrase.
        Same text + being + language always produces the same ID.
        """
        raw = f"{being_name.lower()}::{language.lower()}::{text.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError(
                "VoiceExpress not loaded. Call express.load() before any operation."
            )
