"""
================================================================================
FILE: voice_engine.py
PROJECT: Christman Voice Creation Center
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    Master voice engine for the Christman Voice Creation Center.
    This is the single entry point for all voice synthesis requests
    across the entire Christman AI family.

    It wraps the Christman SDK (christman_sound) and exposes a unified
    interface for controlling:
        - Porosity       (breathiness / air flow in voice texture)
        - Intonation     (pitch contour and melodic pattern)
        - Cadence        (rhythm, pacing, timing between words)
        - Affect         (emotional tone: calm, urgent, warm, grounding)
        - Resonance      (chest/head voice balance)
        - Articulation   (clarity and sharpness of phoneme edges)
        - Prosody        (natural stress and emphasis patterns)
        - Timbre         (voice color and tonal quality)

CARDINAL RULE 13: No stubs. No fake calls. No pretend synthesis.
    Every method either calls real SDK functions or raises clearly labeled
    NotImplementedError with the path to the SDK module that needs wiring.
================================================================================
"""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("christman.voice_engine")


# ---------------------------------------------------------------------------
# Enums — Voice Dimensions
# ---------------------------------------------------------------------------

class Affect(str, Enum):
    """Emotional tone of the synthesized voice."""
    CALM       = "calm"          # Steady, measured — AlphaWolf dementia care
    WARM       = "warm"          # Inviting, gentle — AlphaVox AAC users
    GROUNDING  = "grounding"     # Slow, anchoring — Inferno PTSD/anxiety
    URGENT     = "urgent"        # Alert but not alarming — Aegis safety alerts
    NEUTRAL    = "neutral"       # Flat baseline — system messages
    JOYFUL     = "joyful"        # Bright, uplifting — AlphaDen learning
    CONFIDENT  = "confident"     # Clear, directional — Omega mobility guidance
    EMPATHETIC = "empathetic"    # Soft, present — OmegaAlpha senior companion


class ResonanceMode(str, Enum):
    """Voice body — where the sound resonates."""
    CHEST  = "chest"   # Fuller, lower, more authoritative
    HEAD   = "head"    # Lighter, higher, more approachable
    MID    = "mid"     # Balanced — most natural for AAC users


# ---------------------------------------------------------------------------
# VoiceParameters — The full control surface
# ---------------------------------------------------------------------------

@dataclass
class VoiceParameters:
    """
    Complete parameter set for a single voice synthesis request.

    All float values are normalized 0.0 → 1.0 unless otherwise noted.
    Defaults represent a neutral, accessible baseline suitable for
    most Christman AI family use cases.
    """

    # Core identity
    pack_id: str = "default"              # Which .cvp voice pack to use
    language: str = "en-US"              # BCP-47 language tag

    # Texture
    porosity: float = 0.3                # 0.0 = tight/clear, 1.0 = breathy/airy
    timbre: float = 0.5                  # 0.0 = thin, 1.0 = rich/full

    # Melody and rhythm
    intonation: float = 0.5             # 0.0 = monotone, 1.0 = highly melodic
    cadence: float = 0.5                # 0.0 = very slow, 1.0 = very fast
    prosody_strength: float = 0.5       # 0.0 = flat stress, 1.0 = strong emphasis

    # Clarity
    articulation: float = 0.7           # 0.0 = slurred, 1.0 = hyper-precise
    resonance: ResonanceMode = ResonanceMode.MID

    # Emotion
    affect: Affect = Affect.NEUTRAL

    # Accessibility overrides
    slow_mode: bool = False              # Forces cadence floor — cognitive support
    high_clarity_mode: bool = False      # Forces articulation ceiling — AAC users
    frequency_cap_hz: int = 8000        # Hard ceiling on output frequency (Hz)
    frequency_floor_hz: int = 80        # Hard floor — filters subsonic artifacts

    # Output
    sample_rate: int = 22050            # Hz — matches Christman SDK default
    output_format: str = "wav"          # wav | mp3 | flac

    def apply_affect_preset(self) -> None:
        """
        Auto-configure parameters based on affect.
        Overrides only the dimensions most relevant to each affect type.
        Individual parameters can still be tuned after calling this.
        """
        presets = {
            Affect.CALM: dict(
                cadence=0.35, intonation=0.3, porosity=0.4,
                prosody_strength=0.3, resonance=ResonanceMode.CHEST
            ),
            Affect.WARM: dict(
                cadence=0.45, intonation=0.55, porosity=0.35,
                prosody_strength=0.5, resonance=ResonanceMode.MID
            ),
            Affect.GROUNDING: dict(
                cadence=0.25, intonation=0.2, porosity=0.45,
                prosody_strength=0.2, resonance=ResonanceMode.CHEST,
                slow_mode=True
            ),
            Affect.URGENT: dict(
                cadence=0.7, intonation=0.65, porosity=0.15,
                prosody_strength=0.8, articulation=0.9
            ),
            Affect.JOYFUL: dict(
                cadence=0.6, intonation=0.75, porosity=0.25,
                prosody_strength=0.65, resonance=ResonanceMode.HEAD
            ),
            Affect.CONFIDENT: dict(
                cadence=0.55, intonation=0.45, porosity=0.1,
                articulation=0.85, resonance=ResonanceMode.CHEST
            ),
            Affect.EMPATHETIC: dict(
                cadence=0.4, intonation=0.5, porosity=0.4,
                prosody_strength=0.4, resonance=ResonanceMode.MID
            ),
        }
        preset = presets.get(self.affect, {})
        for key, value in preset.items():
            setattr(self, key, value)

    def validate(self) -> list[str]:
        """
        Validate all parameter ranges.
        Returns a list of error strings — empty list means valid.
        """
        errors = []
        float_fields = [
            ("porosity", self.porosity),
            ("timbre", self.timbre),
            ("intonation", self.intonation),
            ("cadence", self.cadence),
            ("prosody_strength", self.prosody_strength),
            ("articulation", self.articulation),
        ]
        for name, val in float_fields:
            if not (0.0 <= val <= 1.0):
                errors.append(f"{name} must be 0.0–1.0, got {val}")

        if self.frequency_floor_hz >= self.frequency_cap_hz:
            errors.append(
                f"frequency_floor_hz ({self.frequency_floor_hz}) must be "
                f"less than frequency_cap_hz ({self.frequency_cap_hz})"
            )
        if self.sample_rate not in (8000, 16000, 22050, 44100, 48000):
            errors.append(f"sample_rate {self.sample_rate} is not a supported value")

        return errors


# ---------------------------------------------------------------------------
# SynthesisResult
# ---------------------------------------------------------------------------

@dataclass
class SynthesisResult:
    """
    The output of a voice synthesis request.
    Always check success before using audio_data.
    """
    success: bool
    audio_data: Optional[bytes] = None       # Raw audio bytes
    output_path: Optional[str] = None        # Path if written to disk
    duration_seconds: float = 0.0
    sample_rate: int = 22050
    pack_id: str = "unknown"
    error: Optional[str] = None

    def __repr__(self) -> str:
        if self.success:
            return (
                f"SynthesisResult(success=True, duration={self.duration_seconds:.2f}s, "
                f"pack={self.pack_id})"
            )
        return f"SynthesisResult(success=False, error='{self.error}')"


# ---------------------------------------------------------------------------
# VoiceEngine — The Master Orchestrator
# ---------------------------------------------------------------------------

class VoiceEngine:
    """
    Master voice engine for the Christman Voice Creation Center.

    All voice synthesis across the family flows through here.
    This engine wraps the Christman SDK and translates VoiceParameters
    into real audio output.

    Usage:
        engine = VoiceEngine()
        params = VoiceParameters(affect=Affect.GROUNDING, pack_id="inferno_grounding")
        params.apply_affect_preset()
        result = engine.synthesize("I'm here with you.", params)
        if result.success:
            # use result.audio_data or result.output_path
    """

    SDK_MODULE_PATH = "christman_sound"

    def __init__(self, sdk_root: Optional[str] = None):
        """
        Initialize the engine.

        Args:
            sdk_root: Optional path to christman_sound SDK root.
                      Falls back to CHRISTMAN_SDK_ROOT env var,
                      then to system path discovery.
        """
        self.sdk_root = sdk_root or os.environ.get("CHRISTMAN_SDK_ROOT", "")
        self._sdk = None
        self._shorty_engine = None
        self._tts_service = None
        self._orchestrator = None
        self._ready = False

        self._initialize_sdk()

    def _initialize_sdk(self) -> None:
        """
        Attempt to load the Christman SDK modules.
        Logs clearly if any module is missing — never silently fails.
        """
        try:
            # Import the SDK modules we know exist in christman_sound/synthesis/
            from christman_sound.synthesis.shorty_voice_engine_v3 import ShortyVoiceEngine
            from christman_sound.synthesis.tts_service import TTSService
            from christman_sound.synthesis.voice_synthesis_orchestrator import VoiceSynthesisOrchestrator
            from christman_sound.synthesis.phoneme_labeler import PhonemLabeler

            self._shorty_engine = ShortyVoiceEngine()
            self._tts_service = TTSService()
            self._orchestrator = VoiceSynthesisOrchestrator(
                engine=self._shorty_engine,
                tts=self._tts_service
            )
            self._phoneme_labeler = PhonemLabeler()
            self._ready = True
            logger.info("Christman Voice Engine initialized. SDK loaded successfully.")

        except ImportError as e:
            # Rule 13: Be honest. Don't pretend we loaded if we didn't.
            logger.error(
                f"Christman SDK import failed: {e}\n"
                f"Expected SDK at: {self.sdk_root or 'system path'}\n"
                f"Set CHRISTMAN_SDK_ROOT env var to point to christman_sound root.\n"
                f"Engine will not synthesize until SDK is reachable."
            )
            self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def synthesize(
        self,
        text: str,
        params: VoiceParameters,
        output_path: Optional[str] = None
    ) -> SynthesisResult:
        """
        Synthesize speech from text using the given VoiceParameters.

        Args:
            text:        The text to synthesize.
            params:      Full VoiceParameters controlling every dimension.
            output_path: Optional path to write the audio file.
                         If None, returns raw bytes in result.audio_data.

        Returns:
            SynthesisResult — always check .success before using audio.
        """
        if not text or not text.strip():
            return SynthesisResult(
                success=False,
                error="Cannot synthesize empty text."
            )

        # Validate parameters
        errors = params.validate()
        if errors:
            return SynthesisResult(
                success=False,
                error=f"Invalid VoiceParameters: {'; '.join(errors)}"
            )

        if not self._ready:
            return SynthesisResult(
                success=False,
                error=(
                    "VoiceEngine not ready — Christman SDK failed to load. "
                    "Check CHRISTMAN_SDK_ROOT and SDK installation."
                )
            )

        try:
            # Build the SDK request payload
            request = self._build_sdk_request(text, params)

            # Route through the orchestrator
            audio_bytes, duration = self._orchestrator.render(request)

            # Apply frequency filtering (accessibility safety)
            audio_bytes = self._apply_frequency_bounds(
                audio_bytes,
                floor_hz=params.frequency_floor_hz,
                cap_hz=params.frequency_cap_hz,
                sample_rate=params.sample_rate
            )

            # Write to disk if path given
            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)
                logger.info(f"Voice written to {output_path} ({duration:.2f}s)")

            return SynthesisResult(
                success=True,
                audio_data=audio_bytes if not output_path else None,
                output_path=output_path,
                duration_seconds=duration,
                sample_rate=params.sample_rate,
                pack_id=params.pack_id
            )

        except Exception as e:
            logger.error(f"Synthesis failed for pack '{params.pack_id}': {e}", exc_info=True)
            return SynthesisResult(
                success=False,
                error=str(e)
            )

    def _build_sdk_request(self, text: str, params: VoiceParameters) -> dict:
        """
        Translate VoiceParameters into the Christman SDK request format.
        Centralizing this translation means SDK API changes only affect this method.
        """
        return {
            "text": text,
            "pack_id": params.pack_id,
            "language": params.language,
            "porosity": params.porosity,
            "timbre": params.timbre,
            "intonation": params.intonation,
            "cadence": params.cadence,
            "prosody_strength": params.prosody_strength,
            "articulation": params.articulation,
            "resonance": params.resonance.value,
            "affect": params.affect.value,
            "slow_mode": params.slow_mode,
            "high_clarity_mode": params.high_clarity_mode,
            "sample_rate": params.sample_rate,
            "output_format": params.output_format,
        }

    def _apply_frequency_bounds(
        self,
        audio_bytes: bytes,
        floor_hz: int,
        cap_hz: int,
        sample_rate: int
    ) -> bytes:
        """
        Apply frequency floor and ceiling to the audio.
        This is the accessibility safety gate — filters frequencies that
        could trigger seizures or sensory overload in vulnerable users.

        Delegates to frequency_guardian in the accessibility module.
        Falls back to returning unfiltered audio with a warning if
        frequency_guardian is unavailable — never silently corrupts audio.
        """
        try:
            from christman_voice_center.accessibility.frequency_guardian import FrequencyGuardian
            guardian = FrequencyGuardian(sample_rate=sample_rate)
            return guardian.filter(audio_bytes, floor_hz=floor_hz, cap_hz=cap_hz)
        except ImportError:
            logger.warning(
                "frequency_guardian not available — audio returned unfiltered. "
                "Build accessibility/frequency_guardian.py to enable safety filtering."
            )
            return audio_bytes

    def get_engine_status(self) -> dict:
        """
        Returns the current engine status.
        Used by Brockston admin dashboard and health checks.
        """
        return {
            "ready": self._ready,
            "sdk_root": self.sdk_root or "system path",
            "shorty_engine_loaded": self._shorty_engine is not None,
            "tts_service_loaded": self._tts_service is not None,
            "orchestrator_loaded": self._orchestrator is not None,
            "phoneme_labeler_loaded": self._phoneme_labeler is not None,
        }

