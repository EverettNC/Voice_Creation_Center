"""
================================================================================
FILE: studio/live_studio.py
PROJECT: Christman Voice Creation Center — Live Studio Phase 2
AUTHOR: The Christman AI Project | Luma Cognify AI
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    Recording and approval station for the Christman Voice Creation Center.

    THE RULE:
        Raw takes stay in the Voice Creation Center.
        Approved takes export to the phrase folder the being supplies
        (LiveStudio's export_dir). The VCC names no being and no folder of
        its own: every take says which being it belongs to.

    PHASE 2 ADDITIONS:
        capture_take() is now real. It calls studio/recorder.py which uses
        sounddevice to capture actual microphone audio. No more stub.
        No more NotImplementedError.

        Modes:
            Fixed duration — capture_take(session_id, duration_seconds=3.0)
            Open-ended     — capture_take(session_id)  → then  finish_capture()

    All other Phase 1 workflow (validate, approve, reject, export) unchanged.

CARDINAL RULE 13: No fake recording. Every WAV written is real microphone audio.
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
_CENTER_ROOT = Path(__file__).resolve().parent.parent
_ENGINES_DIR = _CENTER_ROOT / "engines"
_STUDIO_DIR  = Path(__file__).resolve().parent
for _p in (str(_CENTER_ROOT), str(_ENGINES_DIR), str(_STUDIO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voice_validator         # engines/voice_validator.py
import take_listener         # studio/take_listener.py — listens to the take
from vcc_paths import VCCPaths  # one base directory for the whole VCC
import voice_path_registrar    # engines/voice_path_registrar.py
from recorder import Recorder, RecordingStats  # studio/recorder.py  (Phase 2)

logger = logging.getLogger("christman.live_studio")

# Approved takes export to the folder the being supplies (export_dir); there
# is no default export folder and no default being.

# Raw takes live in the Center — never in a being's folder. Their folder is
# studio/raw under the VCC's one base directory (vcc_paths).

DEFAULT_LANGUAGE = "en-US"


# ── Workflow states ─────────────────────────────────────────────────────────

class ValidationStatus(str, Enum):
    PENDING = "pending"
    PASSED  = "passed"
    FAILED  = "failed"


class SessionState(str, Enum):
    CREATED         = "created"
    RECORDING       = "recording"       # Phase 2: capture in progress
    TAKE_REGISTERED = "take_registered"
    VALIDATED       = "validated"
    APPROVED        = "approved"
    REJECTED        = "rejected"
    EXPORTED        = "exported"


# ── Session model ─────────────────────────────────────────────────────────────

@dataclass
class StudioSession:
    speaker:           str
    target_phrase:     str
    phrase_key:        str
    being:             str            # the being this take belongs to — always given
    take_number:       int = 1
    raw_path:          Optional[str] = None
    processed_path:    Optional[str] = None
    transcript:        Optional[str] = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    approved:          bool = False
    exported_to:       Optional[str] = None
    language:          str = DEFAULT_LANGUAGE
    state:             SessionState = SessionState.CREATED

    # Phase 2 additions — recording metrics for quality review
    recording_stats:   Optional[dict] = None   # RecordingStats.to_dict()

    # What listening to the take measured (take_listener.TakeReport.to_dict())
    # and every reason validation refused it, if it did.
    take_report:        Optional[dict] = None
    validation_reasons: list = field(default_factory=list)

    id:         str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["validation_status"] = self.validation_status.value
        d["state"]             = self.state.value
        return d


# ── Helpers ─────────────────────────────────────────────────────────────────

def _slugify_phrase(phrase: str) -> str:
    safe = "".join(c.lower() if c.isalnum() else "_" for c in phrase.strip())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")[:60] or "phrase"


def make_phrase_key(being: str, phrase: str, language: str = DEFAULT_LANGUAGE) -> str:
    return voice_path_registrar._make_phrase_id(phrase, being, language)


# ── Live Studio ──────────────────────────────────────────────────────────────

class LiveStudio:
    """
    Phase 2 live studio service.

    Now supports real microphone capture via studio/recorder.py.
    Everything else (validate, approve, reject, export) unchanged from Phase 1.
    """

    def __init__(
        self,
        export_dir: Path,
        device_index: Optional[int] = None,
        base_dir: Optional[Path] = None,
    ):
        """
        Args:
            export_dir:   Where approved takes are exported: the phrase
                          folder the being supplies. Required; there is no
                          default. Tests pass a temp dir here.
            device_index: Input device index for recording.
                          None = system default microphone.
            base_dir:     The VCC's base directory (vcc_paths): raw takes
                          go to studio/raw under it, and exports register
                          into its express index. None = $VCC_HOME, else
                          this repo.
        """
        self.paths = VCCPaths(base_dir)
        if not export_dir:
            raise ValueError("export_dir is required: the being supplies the folder its approved takes go to")
        self.export_dir = Path(export_dir)
        self._sessions: dict[str, StudioSession] = {}
        self._recorder  = Recorder(device_index=device_index)
        self._active_recording_session_id: Optional[str] = None

        # Ensure raw takes directory exists
        self.paths.studio_raw.mkdir(parents=True, exist_ok=True)

    # ── Device helpers ────────────────────────────────────────────────────────

    def list_input_devices(self):
        """Print available input devices to help choose the microphone."""
        self._recorder.print_devices()
        return self._recorder.list_input_devices()

    def set_device(self, device_index: int) -> bool:
        """Switch to a specific input device."""
        return self._recorder.set_device(device_index)

    # ── Session management ────────────────────────────────────────────────────

    def create_session(
        self,
        target_phrase: str,
        speaker: str,
        take_number: int = 1,
        being: Optional[str] = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> StudioSession:
        if not being:
            raise ValueError("being is required: say which being this take belongs to; there is no default being")
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

    # ── Phase 1: register a pre-existing raw take ─────────────────────────────

    def register_raw_take(self, session_id: str, raw_path: str) -> StudioSession:
        """
        Attach a raw take wav that already exists on disk.
        Used when a take was recorded outside the Center and is being brought in.
        """
        session = self.get_session(session_id)
        wav = Path(raw_path)
        if not wav.exists():
            raise FileNotFoundError(f"Raw take not found: {raw_path}")
        session.raw_path = str(wav.resolve())
        session.state    = SessionState.TAKE_REGISTERED
        logger.info("Session %s — raw take registered: %s", session_id, session.raw_path)
        return session

    # ── Phase 2: REAL microphone capture ──────────────────────────────────────

    def capture_take(
        self,
        session_id: str,
        duration_seconds: Optional[float] = None,
        countdown: bool = True,
    ) -> StudioSession:
        """
        Record a take directly from the microphone.

        TWO MODES:
            Fixed duration:
                session = studio.capture_take(session_id, duration_seconds=3.0)
                Blocks until recording completes. Returns the session.

            Open-ended:
                session = studio.capture_take(session_id)
                Returns immediately while recording runs in background.
                Call finish_capture(session_id) when done speaking.

        Args:
            session_id:       ID of the session to attach the take to.
            duration_seconds: Record for this many seconds. None = open-ended.
            countdown:        If True, print 3-2-1 countdown before recording.

        Returns:
            The updated session (state = RECORDING if open-ended, or
            TAKE_REGISTERED if fixed-duration and recording completed).
        """
        session = self.get_session(session_id)

        # Build output path — raw takes live inside the Center
        slug     = _slugify_phrase(session.target_phrase)
        filename = (
            f"{session.being.lower()}_{slug}"
            f"_take{session.take_number}"
            f"_{session.id[:6]}.wav"
        )
        output_path = self.paths.studio_raw / filename

        session.state = SessionState.RECORDING
        self._active_recording_session_id = session_id

        if duration_seconds is not None:
            # Fixed-duration mode — blocks until done
            stats = self._recorder.record_fixed(
                output_path=str(output_path),
                duration_seconds=duration_seconds,
                countdown=countdown,
            )

            if stats is None:
                session.state = SessionState.CREATED
                self._active_recording_session_id = None
                raise RuntimeError(
                    f"Recording failed for session {session_id}. "
                    f"Check microphone connection and device index."
                )

            session.raw_path       = str(output_path.resolve())
            session.recording_stats = stats.to_dict()
            session.state          = SessionState.TAKE_REGISTERED
            self._active_recording_session_id = None

            logger.info(
                "Session %s — take captured: %s (%.2fs, peak %.1fdB%s)",
                session_id,
                filename,
                stats.duration_seconds,
                stats.peak_db,
                " ⚠️ CLIPPED" if stats.clipped else "",
            )

        else:
            # Open-ended mode — returns immediately, recording runs in background
            started = self._recorder.record_open(
                output_path=str(output_path),
                countdown=countdown,
            )
            if not started:
                session.state = SessionState.CREATED
                self._active_recording_session_id = None
                raise RuntimeError(
                    f"Failed to start open-ended recording for session {session_id}. "
                    f"Check microphone connection and device index."
                )

            # Store the intended output path for finish_capture()
            session.raw_path = str(output_path.resolve())
            logger.info(
                "Session %s — open-ended recording started → %s",
                session_id, filename,
            )

        return session

    def finish_capture(self, session_id: str) -> StudioSession:
        """
        Stop an open-ended recording and finalize the take.

        Call this after capture_take(session_id) with no duration_seconds,
        when the speaker is done speaking.

        Returns the updated session (state = TAKE_REGISTERED).
        """
        session = self.get_session(session_id)

        if session.state != SessionState.RECORDING:
            raise ValueError(
                f"Session {session_id} is not in RECORDING state "
                f"(current: {session.state.value}). "
                f"Call capture_take() first."
            )

        stats = self._recorder.stop()
        self._active_recording_session_id = None

        if stats is None:
            session.state = SessionState.CREATED
            raise RuntimeError(
                f"Recording stop failed for session {session_id}. "
                f"No audio frames captured — check microphone."
            )

        session.recording_stats = stats.to_dict()
        session.state           = SessionState.TAKE_REGISTERED

        logger.info(
            "Session %s — open-ended take finished: %.2fs, peak %.1fdB%s",
            session_id,
            stats.duration_seconds,
            stats.peak_db,
            " ⚠️ CLIPPED" if stats.clipped else "",
        )

        return session

    def preview(self, session_id: str) -> bool:
        """
        Play back the raw take for a session.
        Returns True if playback started, False on error.
        """
        session = self.get_session(session_id)
        if not session.raw_path:
            logger.error("Session %s has no raw take to preview.", session_id)
            return False
        return self._recorder.play(session.raw_path)

    def stop_preview(self) -> None:
        """Stop active playback."""
        self._recorder.stop_playback()

    def wait_for_preview(self) -> None:
        """Block until playback finishes naturally."""
        self._recorder.wait_for_playback()

    # ── Validation ────────────────────────────────────────────────────────────

    def mark_validation(
        self,
        session_id: str,
        transcript: str,
        passed: Optional[bool] = None,
    ) -> StudioSession:
        """
        Listen to the take, check the transcript, and record the result.

        The take itself is measured first (studio/take_listener.py): a take
        that is silent, holds no voiced sound, or is too short or too long
        for one phrase fails, whatever the transcript says. The transcript is
        then checked with voice_validator.validate_text. `passed` is the
        reviewer's own judgment of the words; when given, it must also be
        True for the take to pass. The listener does not recognize words.
        """
        session = self.get_session(session_id)
        if session.raw_path is None:
            raise ValueError("Cannot validate a session with no raw take registered.")

        session.transcript = transcript
        reasons: list = []

        try:
            report = take_listener.listen(session.raw_path)
            session.take_report = report.to_dict()
            reasons.extend(report.reasons)
        except take_listener.TakeUnreadable as e:
            session.take_report = None
            reasons.append(str(e))

        text_ok, text_err = voice_validator.validate_text(transcript)
        if not text_ok:
            reasons.append(text_err or "transcript refused by voice_validator")
        if passed is False:
            reasons.append("the reviewer did not pass the take")

        ok = not reasons
        session.validation_reasons = reasons
        session.validation_status = (
            ValidationStatus.PASSED if ok else ValidationStatus.FAILED
        )
        session.state = SessionState.VALIDATED
        logger.info(
            "Session %s — validation %s%s",
            session_id,
            session.validation_status.value,
            "" if ok else f" ({'; '.join(reasons)})",
        )
        return session

    # ── Approve / Reject ──────────────────────────────────────────────────────

    def approve(self, session_id: str) -> StudioSession:
        """
        The reviewer approves the take.
        Only validated, passing takes can be approved.
        """
        session = self.get_session(session_id)
        if session.validation_status != ValidationStatus.PASSED:
            raise ValueError(
                "Cannot approve: validation has not passed "
                f"(status: {session.validation_status.value})."
            )
        session.approved = True
        session.state               = SessionState.APPROVED
        logger.info("Session %s — APPROVED.", session_id)
        return session

    def reject(self, session_id: str) -> StudioSession:
        """The reviewer rejects the take."""
        session = self.get_session(session_id)
        session.approved = False
        session.state               = SessionState.REJECTED
        logger.info("Session %s — rejected.", session_id)
        return session

    # ── Export ────────────────────────────────────────────────────────────────

    def export_approved(self, session_id: str, register: bool = True) -> StudioSession:
        """
        Export an approved take to the being's phrase folder (export_dir).
        Raw take stays in the Center. Only a copy leaves.
        """
        session = self.get_session(session_id)

        if not session.approved or session.state != SessionState.APPROVED:
            raise ValueError("Cannot export: session is not approved.")
        if not session.raw_path:
            raise ValueError("Cannot export: no raw take registered.")

        source = Path(session.processed_path or session.raw_path)
        if not source.exists():
            raise FileNotFoundError(f"Source wav no longer exists: {source}")

        self.export_dir.mkdir(parents=True, exist_ok=True)
        stem = _slugify_phrase(session.target_phrase)
        dest = self.export_dir / (
            f"{session.being.lower()}_{stem}_take{session.take_number}.wav"
        )
        shutil.copy2(source, dest)
        session.exported_to = str(dest.resolve())
        session.state       = SessionState.EXPORTED
        logger.info("Session %s — exported to %s", session_id, session.exported_to)

        if register:
            try:
                result = voice_path_registrar.register_single_file(
                    wav_path=str(dest),
                    being_name=session.being,
                    language=session.language,
                    text=session.target_phrase,
                    base_dir=str(self.paths.base),
                    source="studio_export",
                )
                logger.info(
                    "Session %s — express index registration: %s", session_id, result
                )
            except Exception as e:
                logger.warning(
                    "Session %s — express registration failed (export succeeded): %s",
                    session_id, e,
                )

        return session


# ==============================================================================
# © 2026 Everett Nathaniel Christman & The Christman AI Project
# Luma Cognify AI — "How can we help you love yourself more?"
# Patent Pending TCAP-2026-001 | TCAP-2026-002
# Cardinal Rule 1: It has to actually work.
# Cardinal Rule 13: No fake recording. Every WAV is real microphone audio.
# ==============================================================================
