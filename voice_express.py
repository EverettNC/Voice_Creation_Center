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

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_express")

# Internal paths — all express assets live inside the center
# (2026-10-28) $CHRISTMAN_EXPRESS_ROOT, when set, names the express folder in
# place of the default below; VoiceExpress(root=...) does the same for one
# instance. With neither, the location is exactly what it was before.
EXPRESS_ROOT    = Path(
    os.environ.get("CHRISTMAN_EXPRESS_ROOT") or (Path(__file__).parent.parent / "express")
).expanduser()
PHRASE_CACHE    = EXPRESS_ROOT / "phrase_cache"
PRIORITY_QUEUE  = EXPRESS_ROOT / "priority_queue"
EXPRESS_INDEX   = EXPRESS_ROOT / "express_index.json"

# Where an express phrase's audio came from (ExpressPhrase.source).
# Everything except RENDERED_SOURCE is a recording someone made; the factory
# (vcc_factory.py) never replaces one of those with a rendered file.
RENDERED_SOURCE  = "vcc_factory"      # rendered by vcc_factory.py
RECORDING_SOURCE = "recording"        # a being's own recorded take, registered in place

class RecordingInPlace(RuntimeError):
    """(Added 2026-10-28) register_phrase(replace_recording=False) found a recording there."""


# Priority order for the on-disk queue: lower runs first.
_PRIORITY_RANK = {"critical": 0, "high": 1, "standard": 2, "preload": 3}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON so a reader in another process never sees half a file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

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
    # Added 2026-10-28. voice_path_registrar.py and voice_ingestor.py have
    # always written "source" and "original_filename" into the index; without
    # these two fields load() could not read an index they had touched.
    # source: RECORDING_SOURCE, "path_registrar", "manual_registration",
    #   "voice_ingestor" (all recordings), RENDERED_SOURCE (the factory), or
    #   None for an entry written before the field existed.
    source: Optional[str] = None
    original_filename: Optional[str] = None
    # For rendered audio only: the synthesizer, the measured voice it spoke
    # in, and the checks the file passed before it was registered.
    engine: Optional[str] = None
    voice: Optional[str] = None
    validation: Optional[dict] = None

    @property
    def is_rendered(self) -> bool:
        """True only for audio the factory rendered. Anything else is a recording."""
        return self.source == RENDERED_SOURCE

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

    # Added 2026-10-28: the queue lives on disk, one file per request.
    def to_dict(self) -> dict:
        """The request as stored on disk. audio_data is not stored."""
        return {
            "request_id": self.request_id,
            "being_name": self.being_name,
            "pack_id": self.pack_id,
            "text": self.text,
            "language": self.language,
            "priority": self.priority.value,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PriorityRequest":
        return cls(
            request_id=data["request_id"],
            being_name=data["being_name"],
            pack_id=data["pack_id"],
            text=data["text"],
            language=data["language"],
            priority=Priority(data["priority"]),
            submitted_at=data["submitted_at"],
            completed_at=data.get("completed_at"),
            error=data.get("error"),
        )


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

    def __init__(self, root: Optional[Path] = None):
        """
        root  (added 2026-10-28) the express folder for this instance. The
              default is EXPRESS_ROOT, so existing callers are unchanged.
              The factory and AlphaVox must name the same folder.
        """
        self.root = Path(root).expanduser() if root is not None else EXPRESS_ROOT
        self.phrase_cache = self.root / "phrase_cache"
        self.priority_queue_dir = self.root / "priority_queue"
        self.index_path = self.root / "express_index.json"

        self._phrases: dict[str, ExpressPhrase] = {}
        self._priority_queue: list[PriorityRequest] = []
        # Requests handed out by get_next_priority_request() and not yet
        # completed. Their files stay on disk until complete_priority_request().
        self._in_flight: dict[str, PriorityRequest] = {}
        self._index_mtime_ns: Optional[int] = None
        self._stats = defaultdict(int)
        self._loaded = False

        # Ensure internal directories exist
        self.phrase_cache.mkdir(parents=True, exist_ok=True)
        self.priority_queue_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------
    # Load & Save
    # -------------------------------------------------------------------

    def load(self) -> bool:
        """Load the express index and the waiting priority requests from disk."""
        if not self.index_path.exists():
            logger.info("No express index found — starting fresh.")
            self._loaded = True
            with self._index_lock():
                # Another process may have made it while this one waited.
                if self.index_path.exists():
                    self._phrases = self._read_index()
                else:
                    self._save()
            self._load_priority_queue()
            return True

        try:
            self._phrases = self._read_index()
            self._loaded = True
            self._load_priority_queue()
            logger.info(
                f"Express service loaded — "
                f"{len(self._phrases)} pre-rendered phrases ready, "
                f"{len(self._priority_queue)} priority requests waiting."
            )
            return True

        except Exception as e:
            logger.error(f"Express load failed: {e}", exc_info=True)
            return False

    def _read_index(self) -> dict:
        """Read the index file. Raises on a file that cannot be read."""
        st = self.index_path.stat()
        with open(self.index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        phrases = {}
        for phrase_data in data.get("phrases", []):
            phrase = ExpressPhrase(**phrase_data)
            phrases[phrase.phrase_id] = phrase
        self._index_mtime_ns = st.st_mtime_ns
        return phrases

    @contextlib.contextmanager
    def _index_lock(self):
        """
        (Added 2026-10-28) Hold express_index.lock while changing the index.
        The factory and AlphaVox are two processes on one index; each change
        re-reads the file under this lock first, so neither writes over the
        other's phrase.
        """
        with open(self.root / "express_index.lock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def refresh_index(self) -> bool:
        """
        (Added 2026-10-28) Re-read the index if another process has written it
        since this instance last read or wrote it. Returns True if it re-read.
        The factory calls this before every registration, so it never writes
        over a phrase the ingestor, the registrar or another process added.
        """
        self._require_loaded()
        try:
            mtime = self.index_path.stat().st_mtime_ns
        except FileNotFoundError:
            return False
        if mtime == self._index_mtime_ns:
            return False
        self._phrases = self._read_index()
        return True

    def _save(self) -> None:
        """Persist the express index (atomically: write a temp file, then rename)."""
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
            _write_json_atomic(self.index_path, data)
            self._index_mtime_ns = self.index_path.stat().st_mtime_ns
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
            with self._index_lock():
                if self.index_path.exists():
                    self._phrases = self._read_index()
                on_disk = self._phrases.get(phrase_id)
                # Remove it only if the index still points at the missing file:
                # the factory may have re-rendered it a moment ago.
                if on_disk is not None and on_disk.audio_path == phrase.audio_path:
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
        quality_score: float = 1.0,
        source: Optional[str] = None,
        original_filename: Optional[str] = None,
        engine: Optional[str] = None,
        voice: Optional[str] = None,
        validation: Optional[dict] = None,
        replace_recording: bool = True,
    ) -> ExpressPhrase:
        """
        Register a pre-rendered phrase into the express cache.
        Called after the factory pre-renders a phrase set.

        The audio file must already exist at audio_path before calling this.
        Rule 13 — we don't register what doesn't exist.

        source, original_filename, engine, voice, validation (added
        2026-10-28) say where the audio came from; see ExpressPhrase.

        replace_recording (added 2026-10-28) False refuses, with
        RecordingInPlace, to replace an entry that is a recording whose file
        is still there. The factory passes False for every render, so a
        rendered phrase never takes the place of a being's own recording.
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
            quality_score=quality_score,
            source=source,
            original_filename=original_filename,
            engine=engine,
            voice=voice,
            validation=validation,
        )

        with self._index_lock():
            if self.index_path.exists():
                self._phrases = self._read_index()
            existing = self._phrases.get(phrase_id)
            if (
                not replace_recording
                and existing is not None
                and not existing.is_rendered
                and Path(existing.audio_path).exists()
            ):
                raise RecordingInPlace(
                    f"'{text[:40]}' for {being_name} is a recording "
                    f"({existing.audio_path}); not replaced"
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

    def get_phrase(
        self, text: str, being_name: str, language: str = "en-US"
    ) -> Optional[ExpressPhrase]:
        """(Added 2026-10-28) The index entry for this phrase, or None."""
        return self._phrases.get(self._make_phrase_id(text, being_name, language))

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
        priority = Priority(priority)

        # The short random tail (added 2026-10-28) keeps two requests made in
        # the same millisecond from sharing an id, and so a file on disk.
        request_id = (
            f"express_{being_name.lower()}_{priority.value}"
            f"_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        )

        request = PriorityRequest(
            request_id=request_id,
            being_name=being_name,
            pack_id=pack_id,
            text=text,
            language=language,
            priority=priority
        )

        # On disk first (added 2026-10-28): a request survives a restart of
        # this process or of the factory, and the factory — a separate
        # process — sees it. If the write fails, the request is not queued.
        self._write_request(request)
        self._insert_by_priority(request)
        self._stats["priority_requests"] += 1
        return request

    def _insert_by_priority(self, request: PriorityRequest) -> None:
        """Insert into the in-memory queue. Unchanged rule: CRITICAL goes to front."""
        priority = request.priority
        request_id = request.request_id
        being_name = request.being_name
        text = request.text
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
        elif priority == Priority.STANDARD:
            # (2026-10-28) Ahead of any PRELOAD request: PRELOAD is the lowest
            # level, and appending let a STANDARD request wait behind one.
            insert_at = next(
                (i for i, r in enumerate(self._priority_queue)
                 if r.priority == Priority.PRELOAD),
                len(self._priority_queue)
            )
            self._priority_queue.insert(insert_at, request)
            logger.info(f"STANDARD priority express queued: {request_id}")
        else:
            self._priority_queue.append(request)
            logger.info(f"PRELOAD priority express queued: {request_id}")

    def get_next_priority_request(
        self, being_name: Optional[str] = None
    ) -> Optional[PriorityRequest]:
        """
        Get the next request from the priority queue.
        Called by the voice_engine synthesis loop.

        being_name (added 2026-10-28) takes the first request for that being
        only; other beings' requests stay queued for their own factory.

        The request's file stays on disk until complete_priority_request() is
        called, so a request handed out and then lost to a crash or a stop is
        picked up again on the next load().
        """
        self._require_loaded()
        for i, req in enumerate(self._priority_queue):
            if being_name is None or req.being_name.lower() == being_name.lower():
                self._priority_queue.pop(i)
                self._in_flight[req.request_id] = req
                return req
        return None

    def complete_priority_request(
        self,
        request: PriorityRequest,
        phrase_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Path:
        """
        (Added 2026-10-28) Close a request handed out by
        get_next_priority_request(). Its file moves to priority_queue/done/
        (served from the cache as phrase_id) or priority_queue/failed/ (with
        the error), so the outcome stays on disk to read. Returns that path.
        """
        self._require_loaded()
        request.completed_at = datetime.now(timezone.utc).isoformat()
        request.error = error
        record = request.to_dict()
        record["phrase_id"] = phrase_id
        record["status"] = "failed" if error else "done"
        dest_dir = self.priority_queue_dir / record["status"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{request.request_id}.json"
        _write_json_atomic(dest, record)
        src = self.priority_queue_dir / f"{request.request_id}.json"
        if src.exists():
            src.unlink()
        self._in_flight.pop(request.request_id, None)
        return dest

    def refresh_priority_queue(self) -> int:
        """
        (Added 2026-10-28) Pick up requests another process wrote to the
        queue folder since this instance last looked (AlphaVox queues them;
        the factory takes them). Returns how many were added.
        """
        self._require_loaded()
        known = {r.request_id for r in self._priority_queue} | set(self._in_flight)
        new = [r for r in self._read_queue_dir() if r.request_id not in known]
        for req in new:
            self._insert_by_priority(req)
        return len(new)

    def _write_request(self, request: PriorityRequest) -> Path:
        path = self.priority_queue_dir / f"{request.request_id}.json"
        _write_json_atomic(path, request.to_dict())
        return path

    def _read_queue_dir(self) -> list[PriorityRequest]:
        """
        Every waiting request on disk, oldest first within each priority.
        A file that cannot be read is moved to priority_queue/unreadable/ and
        logged — never silently dropped, never guessed at.
        """
        out = []
        for path in sorted(self.priority_queue_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    out.append(PriorityRequest.from_dict(json.load(f)))
            except Exception as e:
                bad_dir = self.priority_queue_dir / "unreadable"
                bad_dir.mkdir(parents=True, exist_ok=True)
                os.replace(path, bad_dir / path.name)
                logger.error(f"Unreadable priority request {path.name} moved to {bad_dir}: {e}")
        out.sort(key=lambda r: (_PRIORITY_RANK[r.priority.value], r.submitted_at))
        return out

    def _load_priority_queue(self) -> None:
        """Rebuild the in-memory queue from disk, CRITICAL first, oldest first within a level."""
        self._priority_queue = self._read_queue_dir()
        self._in_flight = {}

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
