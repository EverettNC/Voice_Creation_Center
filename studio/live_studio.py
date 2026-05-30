"""
================================================================================
FILE: studio/live_studio.py
PROJECT: Christman Voice Creation Center — Live Studio (Phase 1)
AUTHOR: The Christman AI Project | Luma Cognify AI
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    Phase 1 live recording / approval station.

    The Voice Creation Center is the recording and approval station — NOT
    AlphaVox. Raw takes live here, in the Center. Only takes that Everett
    approves are exported out to the AlphaVox phrase pack.

    THE RULE:
        Raw takes stay in the Voice Creation Center.
        Approved takes export to the AlphaVox phrase pack:
            /Users/EverettN/packs/phrases/alphavox_cycled

    This module is intentionally small. It models a recording session and
    drives it through a clear set of workflow states. It does NOT capture
    microphone audio — see capture_take() for the honest stub.

    It reuses what already exists in the Center:
        - voice_validator.validate_text   → validates the transcript
        - voice_path_registrar.register_single_file
                                          → registers the exported wav into
                                            the express index (files don't move
                                            from the export folder; the index
                                            just points at them)

CARDINAL RULE 13: No fake recording. No phantom exports.
    Every exported file must be a real wav that actually exists on disk.
================================================================================
"""

from __future__ import annotations

import logging
import shutil
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# ── Make the Center's existing helpers importable ───────────────────────────
# voice_validator lives in engines/, voice_path_registrar in engines/,
# Voice_registry at the repo root.
_CENTER_ROOT = Path(__file__).resolve().parent.parent
_ENGINES_DIR = _CENTER_ROOT / "engines"
for _p in (str(_CENTER_ROOT), str(_ENGINES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voice_validator  # noqa: E402  (engines/voice_validator.py)
import voice_path_registrar  # noqa: E402  (engines/voice_path_registrar.py)

logger = logging.getLogger("christman.live_studio")

# ── The one constant that matters most ──────────────────────────────────────
# Approved takes export here. Raw takes never leave the Center.
ALPHAVOX_APPROVED_EXPORT_DIR = Path("/Users/EverettN/packs/phrases/alphavox_cycled")

# Default being for Phase 1. The station records for AlphaVox.
DEFAULT_BEING = "AlphaVox"
DEFAULT_LANGUAGE = "en-US"


# ── Workflow states ─────────────────────────────────────────────────────────

class ValidationStatus(str, Enum):
    """Where a take sits in validation."""
    PENDING = "pending"      # not yet validated
    PASSED = "passed"        # validator accepted it
    FAILED = "failed"        # validator rejected it


class SessionState(str, Enum):
    """The lifecycle of a recording session."""
    CREATED = "created"          # session exists, no take attached yet
    TAKE_REGISTERED = "take_registered"  # a raw wav path is attached
    VALIDATED = "validated"      # validation result recorded
    APPROVED = "approved"        # Everett approved
    REJECTED = "rejected"        # Everett rejected
    EXPORTED = "exported"        # approved take copied to AlphaVox pack


# ── The session model ─────────────────────────────────────────────────────────

@dataclass
class StudioSession:
    """
    A single recording session for one target phrase.

    Lightweight on purpose. This is the unit Everett works with at the
    station: pick a phrase, record a take, validate, approve or reject,
    and (if approved) export to AlphaVox.
    """
    speaker: str
    target_phrase: str
    phrase_key: str
    take_number: int = 1
    raw_path: Optional[str] = None
    processed_path: Optional[str] = None
    transcript: Optional[str] = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    approved_by_everett: bool = False
    exported_to: Optional[str] = None
    being: str = DEFAULT_BEING
    language: str = DEFAULT_LANGUAGE
    state: SessionState = SessionState.CREATED
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        """Serializable view. Enums become their string values."""
        d = asdict(self)
        d["validation_status"] = self.validation_status.value
        d["state"] = self.state.value
        return d


# ── Helpers ─────────────────────────────────────────────────────────────────

def _slugify_phrase(phrase: str) -> str:
    """Make a filesystem-safe stem from a phrase. 'I love you' → 'i_love_you'."""
    safe = "".join(c.lower() if c.isalnum() else "_" for c in phrase.strip())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")[:60] or "phrase"


def make_phrase_key(being: str, phrase: str, language: str = DEFAULT_LANGUAGE) -> str:
    """
    Stable key for a target phrase. Mirrors the express index's id scheme
    so a phrase keeps the same key across the Center.
    """
    return voice_path_registrar._make_phrase_id(phrase, being, language)


# ── The service ─────────────────────────────────────────────────────────────

class LiveStudio:
    """
    Phase 1 live studio service.

    Drives a StudioSession through its states. Holds sessions in memory —
    Phase 1 is a single-station workflow, not a persisted multi-user system.
    Persistence can be layered on later without changing this surface.
    """

    def __init__(self, export_dir: Optional[Path] = None):
        # export_dir is overridable so tests never touch the real /Users path.
        self.export_dir = Path(export_dir) if export_dir else ALPHAVOX_APPROVED_EXPORT_DIR
        self._sessions: dict[str, StudioSession] = {}

    # -- create -------------------------------------------------------------
    def create_session(
        self,
        target_phrase: str,
        speaker: str,
        take_number: int = 1,
        being: str = DEFAULT_BEING,
        language: str = DEFAULT_LANGUAGE,
    ) -> StudioSession:
        """Open a new recording session for one target phrase."""
        session = StudioSession(
            speaker=speaker,
            target_phrase=target_phrase,
            phrase_key=make_phrase_key(being, target_phrase, language),
            take_number=take_number,
            being=being,
            language=language,
        )
        self._sessions[session.id] = session
        logger.info(
            "Session %s created — '%s' take %d (speaker: %s)",
            session.id, target_phrase, take_number, speaker,
        )
        return session

    def get_session(self, session_id: str) -> StudioSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"No studio session with id {session_id!r}")
        return session

    def list_sessions(self) -> list[StudioSession]:
        return list(self._sessions.values())

    # -- register a raw take ------------------------------------------------
    def register_raw_take(self, session_id: str, raw_path: str) -> StudioSession:
        """
        Attach a raw take wav to a session. The file must already exist —
        Cardinal Rule 13. The raw take stays exactly where it is; it never
        leaves the Center on registration.
        """
        session = self.get_session(session_id)
        wav = Path(raw_path)
        if not wav.exists():
            raise FileNotFoundError(f"Raw take not found: {raw_path}")

        session.raw_path = str(wav.resolve())
        session.state = SessionState.TAKE_REGISTERED
        logger.info("Session %s — raw take registered: %s", session_id, session.raw_path)
        return session

    # -- validation ---------------------------------------------------------
    def mark_validation(
        self,
        session_id: str,
        transcript: str,
        passed: Optional[bool] = None,
    ) -> StudioSession:
        """
        Record a validation result for the take.

        Reuses voice_validator.validate_text on the transcript. If `passed`
        is given explicitly (e.g. a human listened and judged), it is honored
        and combined with the text validator (both must agree to pass).
        """
        session = self.get_session(session_id)
        if session.raw_path is None:
            raise ValueError("Cannot validate a session with no raw take registered.")

        session.transcript = transcript
        text_ok, text_err = voice_validator.validate_text(transcript)

        if passed is None:
            ok = text_ok
        else:
            ok = bool(passed) and text_ok

        session.validation_status = (
            ValidationStatus.PASSED if ok else ValidationStatus.FAILED
        )
        session.state = SessionState.VALIDATED
        logger.info(
            "Session %s — validation %s%s",
            session_id,
            session.validation_status.value,
            "" if ok else f" ({text_err or 'human-rejected'})",
        )
        return session

    # -- approve / reject ---------------------------------------------------
    def approve(self, session_id: str) -> StudioSession:
        """
        Everett approves the take. Only validated, passing takes can be
        approved — no approving something that failed or was never validated.
        """
        session = self.get_session(session_id)
        if session.validation_status != ValidationStatus.PASSED:
            raise ValueError(
                "Cannot approve: validation has not passed "
                f"(status: {session.validation_status.value})."
            )
        session.approved_by_everett = True
        session.state = SessionState.APPROVED
        logger.info("Session %s — APPROVED by Everett.", session_id)
        return session

    def reject(self, session_id: str) -> StudioSession:
        """Everett rejects the take. It will not export."""
        session = self.get_session(session_id)
        session.approved_by_everett = False
        session.state = SessionState.REJECTED
        logger.info("Session %s — rejected.", session_id)
        return session

    # -- export -------------------------------------------------------------
    def export_approved(self, session_id: str, register: bool = True) -> StudioSession:
        """
        Export an approved take's wav to the AlphaVox phrase pack.

        Copies the wav into self.export_dir (the AlphaVox cycled phrase
        folder) under a phrase-named filename, then optionally registers it
        into the express index via voice_path_registrar.register_single_file.

        Raw takes stay in the Center. Only this copy leaves.
        """
        session = self.get_session(session_id)

        if not session.approved_by_everett or session.state != SessionState.APPROVED:
            raise ValueError("Cannot export: session is not approved.")
        if not session.raw_path:
            raise ValueError("Cannot export: no raw take registered.")

        source = Path(session.processed_path or session.raw_path)
        if not source.exists():
            raise FileNotFoundError(f"Source wav no longer exists: {source}")

        self.export_dir.mkdir(parents=True, exist_ok=True)
        stem = _slugify_phrase(session.target_phrase)
        dest = self.export_dir / f"{session.being.lower()}_{stem}_take{session.take_number}.wav"
        shutil.copy2(source, dest)
        session.exported_to = str(dest.resolve())
        session.state = SessionState.EXPORTED
        logger.info("Session %s — exported to %s", session_id, session.exported_to)

        if register:
            try:
                result = voice_path_registrar.register_single_file(
                    wav_path=str(dest),
                    being_name=session.being,
                    language=session.language,
                    text=session.target_phrase,
                )
                logger.info("Session %s — express index registration: %s", session_id, result)
            except Exception as e:  # registration is best-effort; export already done
                logger.warning("Session %s — express registration failed: %s", session_id, e)

        return session

    # -- future mic capture (honest stub) -----------------------------------
    def capture_take(self, session_id: str, *args, **kwargs):
        """
        FUTURE: live microphone capture.

        Phase 1 does NOT capture audio. This repo has no microphone capture
        support, so this is an honest stub — it does not fake a recording.
        Wire real capture in here in a later phase, then have it call
        register_raw_take() with the captured wav path.
        """
        raise NotImplementedError(
            "Live microphone capture is not implemented in Phase 1. "
            "Record the take with your own tool, then call "
            "register_raw_take(session_id, raw_path) with the resulting wav."
        )
