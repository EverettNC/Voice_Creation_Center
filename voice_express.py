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
        An AAC being        → "I love you", "I need help", "I'm hungry"
        A dementia-care being → "You're safe", "Let's go home", "I'm here with you"
        A PTSD being        → "You're not alone", "Breathe with me", "You are safe"
        A safety being      → "Alert sent", "Help is coming", "You are protected"

    TRACK 2 — PRIORITY SYNTHESIS QUEUE
        Requests that can't use pre-rendered audio but need synthesis
        RIGHT NOW. Safety alerts, crisis responses, urgent AAC communication.
        Jumps to the front of the queue. No waiting.

    The express lane never compromises quality or safety.
    It just removes the wait.

CARDINAL RULE 13: No stubs. Pre-rendered audio is real audio.

WHAT CHANGED AND WHY (2026-10-28)
    - The index could not be loaded. Every entry the registrar and the
      ingestor wrote carried "source" and "original_filename", which
      ExpressPhrase did not accept, so load() failed on the committed index
      ("unexpected keyword argument 'source'") and served nothing. There is
      now one entry format, written by one function (register_phrase), which
      the registrar, the ingestor and the studio all call.
    - Nothing in an entry is assumed. Duration and sample rate are read from
      the audio file itself when it is registered, and checked again when the
      index is loaded; an unreadable or empty file is refused. The fixed
      sample_rate 22050 and quality_score 1.0 written into every entry are
      gone: nothing ever measured a quality, so no quality is recorded.
    - Track 2 had no consumer: requests were queued and nothing rendered
      them. process_next() and run_queue() now render each request through
      the requesting being's VoiceEngine, register the result, and
      get_result() returns it.
    - HIGH requests ran last in, first out (each new HIGH was inserted ahead
      of earlier HIGHs). Every priority level is now first in, first out:
      CRITICAL, then HIGH, then STANDARD, then PRELOAD, oldest first in each.
    - No being is written into the VCC. The default phrase table keyed by
      six being names is gone: each being hands in its own phrase source
      (a file), the same way it hands the engine its own voice.
================================================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import wave
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

# Internal paths — all express assets live inside the center. They come from
# vcc_paths: the base_dir passed to VoiceExpress, else $VCC_HOME, else this
# repo. (They used to be Path(__file__).parent.parent / "express", which from
# the repo root is the folder ABOVE the repo.)
from vcc_paths import VCCPaths

logger = logging.getLogger("christman.voice_express")

# Performance targets
EXPRESS_TARGET_MS  = 50    # Pre-rendered: serve in under 50ms
PRIORITY_TARGET_MS = 500   # Priority synthesis: complete in under 500ms

#: Where a phrase's audio came from. Every entry names one.
SOURCES = (
    "recording",       # a recording of the being's voice, registered as it is
    "rendered",        # rendered by the voice engine (Track 2 or a preload)
    "studio_export",   # an approved take exported by the live studio
    "voice_ingestor",  # a phrase cut from a longer file by the ingestor
    "path_registrar",  # a WAV registered in place by the path registrar
)

#: Where a phrase's text came from: given by a person or a phrase map, or
#: read from the file's name (the registrar's folder scan, when no text map
#: is given). A filename is not proof of what is said in the file; this field
#: keeps that visible.
TEXT_FROM = ("given", "filename")

INDEX_VERSION = "2.0.0"


# Priority levels
class Priority(str, Enum):
    CRITICAL = "critical"   # Safety alert, crisis intervention
    HIGH     = "high"       # AAC urgent communication, medical alert
    STANDARD = "standard"   # Normal express request
    PRELOAD  = "preload"    # Background preloading — lowest priority


#: Queue order: lower runs first. Within one level, oldest first.
_RANK = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.STANDARD: 2, Priority.PRELOAD: 3}


class UnmeasurableAudio(ValueError):
    """The file cannot be read as a WAV, or holds no audio. Nothing was registered."""


class IndexUnreadable(ValueError):
    """The express index is not JSON in the format this module writes. Nothing was loaded."""


def measure_wav(path) -> Tuple[float, int]:
    """
    (duration in seconds, sample rate in Hz), read from the WAV file itself.
    Raises UnmeasurableAudio for a file that is missing, is not a WAV, or
    holds no audio frames.
    """
    p = Path(path)
    try:
        with wave.open(str(p), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
    except (wave.Error, EOFError, OSError) as e:
        raise UnmeasurableAudio(f"cannot read {p} as a WAV: {e}") from e
    if rate <= 0:
        raise UnmeasurableAudio(f"{p} reports a sample rate of {rate} Hz")
    if frames <= 0:
        raise UnmeasurableAudio(f"{p} holds no audio frames")
    return round(frames / float(rate), 3), rate


def make_phrase_id(text: str, being_name: str, language: str) -> str:
    """
    A stable, unique ID for a phrase.
    Same text + being + language always produces the same ID.
    """
    raw = f"{being_name.lower()}::{language.lower()}::{text.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_phrase_source(path) -> List[str]:
    """
    The phrases a being wants ready in the express cache, from the file it
    hands in: a JSON list of strings, a JSON object with a "phrases" list,
    or a text file with one phrase per line (blank lines and lines starting
    with # are skipped). Raises ValueError naming what is wrong.
    """
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        phrases = data.get("phrases") if isinstance(data, dict) else data
        if not isinstance(phrases, list) or not all(isinstance(x, str) for x in phrases):
            raise ValueError(f"{p}: expected a list of phrases, or {{\"phrases\": [...]}}")
    else:
        phrases = [ln.strip() for ln in text.splitlines()]
        phrases = [ln for ln in phrases if ln and not ln.startswith("#")]
    out, seen = [], set()
    for ph in (x.strip() for x in phrases):
        if ph and ph.lower() not in seen:
            seen.add(ph.lower())
            out.append(ph)
    if not out:
        raise ValueError(f"{p}: holds no phrases")
    return out


def _slug(name: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "_" for c in name.strip())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "being"


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
    audio_path: str          # Absolute path to the audio file
    duration_seconds: float  # Read from the file
    sample_rate: int         # Read from the file
    source: str              # One of SOURCES
    text_from: str = "given" # One of TEXT_FROM
    original_filename: Optional[str] = None
    engine: Optional[str] = None   # For a render: the renderer that made it
    voice: Optional[str] = None    # For a render: the voice it was made in
    serve_count: int = 0
    last_served_at: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def is_rendered(self) -> bool:
        return self.source == "rendered"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExpressPhrase":
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(d) - known)
        if unknown:
            raise IndexUnreadable(f"entry {d.get('phrase_id')!r} has fields this format does not have: {unknown}")
        phrase = cls(**d)
        if phrase.source not in SOURCES:
            raise IndexUnreadable(f"entry {phrase.phrase_id!r}: source {phrase.source!r} is not one of {SOURCES}")
        if phrase.text_from not in TEXT_FROM:
            raise IndexUnreadable(f"entry {phrase.phrase_id!r}: text_from {phrase.text_from!r} is not one of {TEXT_FROM}")
        return phrase


@dataclass
class PriorityRequest:
    """
    A synthesis request that needs to jump the queue.
    """
    request_id: str
    being_name: str
    text: str
    language: str
    priority: Priority
    params: Optional[object] = None      # voice_engine.VoiceParameters; None = the voice as it is
    allow_unapplied: bool = False        # passed to VoiceEngine.synthesize
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    phrase_id: Optional[str] = None
    audio_path: Optional[str] = None
    controls: List[dict] = field(default_factory=list)   # the engine's report, per control
    error: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.completed_at is not None

    @property
    def succeeded(self) -> bool:
        return self.done and self.error is None

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
        express = VoiceExpress()           # or VoiceExpress(base_dir=...)
        express.load()

        # Serve a pre-rendered phrase instantly
        result = express.serve("I love you", being_name=name, language="en-US")

        # Jump the queue for urgent synthesis, then render it in the being's voice
        req = express.priority_synthesize("Help is on the way.", being_name=name,
                                          priority=Priority.CRITICAL)
        express.run_queue({name: VoiceEngine(profile_path_the_being_supplies)})
        express.get_result(req.request_id)
    """

    def __init__(self, base_dir: Optional[str] = None):
        self.paths = VCCPaths(base_dir)
        self._phrases: Dict[str, ExpressPhrase] = {}
        self._priority_queue: List[PriorityRequest] = []
        self._results: Dict[str, PriorityRequest] = {}
        self._stats = defaultdict(int)
        self._loaded = False
        #: Entries left out by the last load(), each with its reason.
        self.load_problems: List[str] = []

        # Ensure internal directories exist
        self.paths.phrase_cache.mkdir(parents=True, exist_ok=True)
        self.paths.priority_queue.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.paths.express_index

    # -------------------------------------------------------------------
    # Load & Save
    # -------------------------------------------------------------------

    def load(self) -> bool:
        """
        Load the express index from disk. Each entry's file is measured
        again: an entry whose file is missing, unreadable, or no longer
        matches its recorded duration and sample rate is left out, with the
        reason in self.load_problems, and leaves the index on the next save.
        An index in another format is not loaded at all (returns False), so
        nothing written by another version is overwritten.
        """
        self.load_problems = []
        if not self.index_path.exists():
            logger.info("No express index found — starting fresh.")
            self._phrases = {}
            self._loaded = True
            self._save()
            return True

        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("phrases", [])
            parsed = [ExpressPhrase.from_dict(dict(e)) for e in entries]
        except (json.JSONDecodeError, OSError, TypeError, AttributeError, IndexUnreadable) as e:
            logger.error("Express load failed, nothing loaded: %s", e)
            return False

        self._phrases = {}
        for phrase in parsed:
            try:
                duration, rate = measure_wav(phrase.audio_path)
            except UnmeasurableAudio as e:
                self.load_problems.append(f"{phrase.phrase_id} {phrase.text!r}: {e}")
                continue
            if rate != phrase.sample_rate or abs(duration - phrase.duration_seconds) > 0.001:
                self.load_problems.append(
                    f"{phrase.phrase_id} {phrase.text!r}: the file now measures {duration} s at {rate} Hz, "
                    f"the entry says {phrase.duration_seconds} s at {phrase.sample_rate} Hz"
                )
                continue
            self._phrases[phrase.phrase_id] = phrase

        for problem in self.load_problems:
            logger.error("Express entry left out: %s", problem)
        self._loaded = True
        logger.info(
            "Express service loaded — %d pre-rendered phrases ready, %d left out.",
            len(self._phrases), len(self.load_problems),
        )
        return True

    def _save(self) -> None:
        """Persist the express index. Raises if it cannot be written: a lost write is not hidden."""
        self.paths.express.mkdir(parents=True, exist_ok=True)
        data = {
            "meta": {
                "version": INDEX_VERSION,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_phrases": len(self._phrases),
                "express_target_ms": EXPRESS_TARGET_MS,
                "priority_target_ms": PRIORITY_TARGET_MS,
                "format": (
                    "One entry per phrase, written only by voice_express.register_phrase. "
                    "duration_seconds and sample_rate are read from the audio file; "
                    "source says where the audio came from, text_from where the text came from."
                ),
            },
            "phrases": [p.to_dict() for p in self._phrases.values()],
        }
        tmp = self.index_path.with_suffix(".json.writing")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.index_path)

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

        phrase_id = make_phrase_id(text, being_name, language)
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
                error="Pre-rendered audio file missing. Phrase removed from cache."
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
        source: str,
        text_from: str = "given",
        original_filename: Optional[str] = None,
        engine: Optional[str] = None,
        voice: Optional[str] = None,
        replace: bool = False,
    ) -> ExpressPhrase:
        """
        Register a phrase's audio into the express cache. The one writer of
        index entries: the registrar, the ingestor, the studio and Track 2
        all come through here.

        The audio file must already exist. Its duration and sample rate are
        read from it; a file that cannot be read or holds no audio is
        refused (UnmeasurableAudio). Rule 13 — we don't register what
        doesn't exist.

        If the phrase is already registered, the existing entry is returned
        unchanged unless replace=True.
        """
        self._require_loaded()
        if source not in SOURCES:
            raise ValueError(f"source {source!r} is not one of {SOURCES}")
        if text_from not in TEXT_FROM:
            raise ValueError(f"text_from {text_from!r} is not one of {TEXT_FROM}")
        if not text or not text.strip():
            raise ValueError("a phrase needs its text")

        path = Path(audio_path).expanduser().resolve()
        duration, rate = measure_wav(path)

        phrase_id = make_phrase_id(text, being_name, language)
        existing = self._phrases.get(phrase_id)
        if existing is not None and not replace:
            logger.info("Express phrase already registered: '%s' for %s — kept as it is.", text[:40], being_name)
            return existing

        phrase = ExpressPhrase(
            phrase_id=phrase_id,
            being_name=being_name,
            language=language,
            text=text.strip(),
            audio_path=str(path),
            duration_seconds=duration,
            sample_rate=rate,
            source=source,
            text_from=text_from,
            original_filename=original_filename,
            engine=engine,
            voice=voice,
        )

        self._phrases[phrase_id] = phrase
        self._save()

        logger.info(
            f"Express phrase registered: '{text[:40]}' "
            f"for {being_name} [{language}] ({duration}s, {rate} Hz, {source})"
        )
        return phrase

    def get_phrase(self, text: str, being_name: str, language: str = "en-US") -> Optional[ExpressPhrase]:
        """The entry for this phrase, or None."""
        self._require_loaded()
        return self._phrases.get(make_phrase_id(text, being_name, language))

    def get_phrases_for_being(self, being_name: str) -> list[ExpressPhrase]:
        """Get all pre-rendered phrases for a specific being."""
        self._require_loaded()
        return [
            p for p in self._phrases.values()
            if p.being_name.lower() == being_name.lower()
        ]

    def is_cached(self, text: str, being_name: str, language: str = "en-US") -> bool:
        """Quick check — is this phrase already in the express cache?"""
        phrase_id = make_phrase_id(text, being_name, language)
        return phrase_id in self._phrases

    # -------------------------------------------------------------------
    # Track 2 — Priority Synthesis Queue
    # -------------------------------------------------------------------

    def priority_synthesize(
        self,
        text: str,
        being_name: str,
        language: str = "en-US",
        priority: Priority = Priority.HIGH,
        params: Optional[object] = None,
        allow_unapplied: bool = False,
    ) -> PriorityRequest:
        """
        Submit a request to the priority synthesis queue.
        Jumps to the front of the queue based on priority level.

        CRITICAL priority → processed immediately before anything else.
        HIGH priority     → next in queue after CRITICAL.
        STANDARD priority → standard express, still faster than main queue.
        PRELOAD           → last.
        Within one level, first in, first out.

        Returns a PriorityRequest. process_next() / run_queue() render it;
        get_result(request_id) returns it, done or still waiting.
        """
        self._require_loaded()
        priority = Priority(priority)

        self._stats["request_counter"] += 1
        request_id = (
            f"express_{_slug(being_name)}_{priority.value}"
            f"_{int(time.time() * 1000)}_{self._stats['request_counter']}"
        )

        request = PriorityRequest(
            request_id=request_id,
            being_name=being_name,
            text=text,
            language=language,
            priority=priority,
            params=params,
            allow_unapplied=allow_unapplied,
        )

        # Insert after every request of the same or a more urgent level:
        # first in, first out within a level.
        rank = _RANK[priority]
        insert_at = next(
            (i for i, r in enumerate(self._priority_queue) if _RANK[r.priority] > rank),
            len(self._priority_queue),
        )
        self._priority_queue.insert(insert_at, request)
        self._results[request_id] = request

        if priority == Priority.CRITICAL:
            logger.warning(
                f"CRITICAL express request: {being_name} | "
                f"'{text[:40]}' | request_id={request_id}"
            )
        else:
            logger.info(f"{priority.value.upper()} priority express queued: {request_id}")

        self._stats["priority_requests_queued"] += 1
        return request

    def get_next_priority_request(self) -> Optional[PriorityRequest]:
        """Take the next request from the priority queue (process_next renders it)."""
        self._require_loaded()
        if not self._priority_queue:
            return None
        return self._priority_queue.pop(0)

    def get_result(self, request_id: str) -> Optional[PriorityRequest]:
        """The request with this id, done (completed_at set) or still waiting; None if unknown."""
        return self._results.get(request_id)

    def process_next(self, engines: Mapping[str, object]) -> Optional[PriorityRequest]:
        """
        Render the most urgent waiting request, in the requesting being's
        voice. `engines` maps a being's name to its voice_engine.VoiceEngine
        (built from the voice profile that being supplies). The render is
        written into the phrase cache, registered, and the request completed
        with the phrase, the engine's control report, or the error.

        Returns the request it worked on, or None when nothing was waiting.
        """
        req = self.get_next_priority_request()
        if req is None:
            return None

        engine = next((e for name, e in engines.items() if name.lower() == req.being_name.lower()), None)
        try:
            if engine is None:
                raise LookupError(f"no voice engine was handed in for {req.being_name!r}")
            cached = self.get_phrase(req.text, req.being_name, req.language)
            if cached is not None and req.params is None:
                req.phrase_id, req.audio_path = cached.phrase_id, cached.audio_path
            else:
                phrase_id = make_phrase_id(req.text, req.being_name, req.language)
                folder = self.paths.phrase_cache / _slug(req.being_name)
                final = folder / f"{phrase_id}.wav"
                partial = folder / f"{phrase_id}.rendering.wav"
                result = engine.synthesize(
                    req.text, req.params, output_path=str(partial), allow_unapplied=req.allow_unapplied
                )
                req.controls = [c.to_dict() for c in result.controls]
                if not result.success:
                    if partial.exists():
                        partial.unlink()
                    raise RuntimeError(result.error)
                partial.replace(final)
                phrase = self.register_phrase(
                    req.text, req.being_name, req.language, str(final),
                    source="rendered", engine=result.renderer, voice=result.voice,
                    replace=True,
                )
                req.phrase_id, req.audio_path = phrase.phrase_id, phrase.audio_path
            self._stats["priority_requests_completed"] += 1
        except Exception as e:
            req.error = f"{type(e).__name__}: {e}"
            self._stats["priority_requests_failed"] += 1
            logger.error("Express request %s not rendered: %s", req.request_id, req.error)
        req.completed_at = datetime.now(timezone.utc).isoformat()
        return req

    def run_queue(self, engines: Mapping[str, object], max_requests: Optional[int] = None) -> List[PriorityRequest]:
        """Render waiting requests in priority order until the queue is empty (or max_requests)."""
        done: List[PriorityRequest] = []
        while max_requests is None or len(done) < max_requests:
            req = self.process_next(engines)
            if req is None:
                break
            done.append(req)
        return done

    def get_queue_depth(self) -> dict:
        """Current priority queue depth by priority level."""
        depth = {p.value: 0 for p in Priority}
        for req in self._priority_queue:
            depth[req.priority.value] += 1
        return depth

    # -------------------------------------------------------------------
    # Pre-Loading — each being's own phrase set
    # -------------------------------------------------------------------

    def get_preload_list(self, phrase_source) -> list[str]:
        """
        The phrases a being wants pre-rendered, read from the phrase source
        it hands in (see load_phrase_source). The VCC keeps no phrase list
        of its own for any being.
        """
        return load_phrase_source(phrase_source)

    def get_unrendered_phrases(self, phrase_source, being_name: str, language: str) -> list[str]:
        """
        Returns phrases from the being's phrase source that aren't in the
        express cache yet.
        """
        return [
            phrase for phrase in self.get_preload_list(phrase_source)
            if not self.is_cached(phrase, being_name, language)
        ]

    def queue_preload(self, phrase_source, being_name: str, language: str = "en-US") -> List[PriorityRequest]:
        """Queue every phrase of the being's source not yet cached, at PRELOAD priority."""
        return [
            self.priority_synthesize(text, being_name, language, Priority.PRELOAD)
            for text in self.get_unrendered_phrases(phrase_source, being_name, language)
        ]

    # -------------------------------------------------------------------
    # Stats & Status
    # -------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Express service statistics for the admin dashboard."""
        self._require_loaded()
        total = self._stats["cache_hits"] + self._stats["cache_misses"]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_phrases_cached": len(self._phrases),
            "entries_left_out_at_load": len(self.load_problems),
            "cache_hits": self._stats["cache_hits"],
            "cache_misses": self._stats["cache_misses"],
            "hit_rate": round(
                self._stats["cache_hits"] / total, 3
            ) if total > 0 else 0.0,
            "priority_requests_queued": self._stats["priority_requests_queued"],
            "priority_requests_completed": self._stats["priority_requests_completed"],
            "priority_requests_failed": self._stats["priority_requests_failed"],
            "priority_queue_depth": self.get_queue_depth(),
            "express_target_ms": EXPRESS_TARGET_MS,
            "priority_target_ms": PRIORITY_TARGET_MS,
        }

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _make_phrase_id(self, text: str, being_name: str, language: str) -> str:
        return make_phrase_id(text, being_name, language)

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError(
                "VoiceExpress not loaded. Call express.load() before any operation."
            )
