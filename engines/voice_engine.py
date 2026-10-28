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

WHAT CHANGED AND WHY (2026-10-28)
    The engine could never speak. It imported four names from
    christman_sound.synthesis, a package that does not exist (the modules are
    in christman_voice_sdk.synthesis); two class names were wrong, one class
    does not exist, the orchestrator call used keyword arguments and a
    render() method the SDK does not have; and even with every name fixed,
    the classes it chose raise "sovereign synthesizer not wired yet" or only
    analyze audio. get_engine_status raised AttributeError, and the
    frequency safety filter imported a module that exists in no repo, so
    audio went out unfiltered.

    Now:
    - Sound is made by christman_sound.synth.synthesize — the sovereign
      synthesizer, christman_synth, the same call the SDK's
      SpeechSynthesisEngine._render makes — with the voice the engine was
      given: a measured voice profile file passed in by the being that owns
      it (christman_sound.voice_profile.load_voice_profile). The VCC holds
      no being's voice and has no default being.
    - Every control in VoiceParameters is either passed to the synthesizer
      or reported as not applied, with the reason. The engine reads the
      synthesizer's own signature at render time, so a control is applied
      the moment Christman-Sound accepts it, and never before. A request for
      a control that cannot be applied is refused unless the caller passes
      allow_unapplied=True; then the audio is made and the report still says
      what was not applied.
    - The frequency floor and cap are applied by this engine to the samples
      (a band filter, below), and reported like every other control.
    - Phoneme labels come from the SDK's labeler
      (christman_voice_sdk.synthesis.phoneme_labeler), the one copy.
================================================================================
"""

from __future__ import annotations

import inspect
import io
import logging
import sys
import wave
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("christman.voice_engine")

_ENGINES_DIR = str(Path(__file__).resolve().parent)
if _ENGINES_DIR not in sys.path:
    sys.path.insert(0, _ENGINES_DIR)
import voice_validator  # engines/voice_validator.py — the content check before any sound


# ---------------------------------------------------------------------------
# Enums — Voice Dimensions
# ---------------------------------------------------------------------------

class Affect(str, Enum):
    """Emotional tone of the synthesized voice."""
    CALM       = "calm"          # Steady, measured — dementia care
    WARM       = "warm"          # Inviting, gentle — AAC clients
    GROUNDING  = "grounding"     # Slow, anchoring — PTSD/anxiety
    URGENT     = "urgent"        # Alert but not alarming — safety alerts
    NEUTRAL    = "neutral"       # Flat baseline — system messages
    JOYFUL     = "joyful"        # Bright, uplifting — learning
    CONFIDENT  = "confident"     # Clear, directional — mobility guidance
    EMPATHETIC = "empathetic"    # Soft, present — senior companion


class ResonanceMode(str, Enum):
    """Voice body — where the sound resonates."""
    CHEST  = "chest"   # Fuller, lower, more authoritative
    HEAD   = "head"    # Lighter, higher, more approachable
    MID    = "mid"     # Balanced — most natural for AAC clients


# ---------------------------------------------------------------------------
# Where each control goes in the synthesizer
# ---------------------------------------------------------------------------

#: VoiceParameters field -> the keyword christman_sound.synth.synthesize must
#: accept for that control to be applied. Agreed with the Christman-Sound
#: build (2026-10-28). A keyword the synthesizer does not have means the
#: control is not applied, and the report says so.
SYNTH_INPUT_FOR: Dict[str, str] = {
    "porosity": "breath",
    "timbre": "timbre",
    "intonation": "intonation",
    "cadence": "cadence",
    "prosody_strength": "prosody",
    "articulation": "articulation",
    "resonance": "resonance",
    "affect": "affect",
    "jitter": "jitter",
    "shimmer": "shimmer",
}

#: Sample rates christman_synth renders at (christman_sound/synth.py:
#: CODECWAR_SAMPLE_RATE and CHRISTMAN_SOUND_SAMPLE_RATE).
SYNTH_SAMPLE_RATES = (16000, 22050)

#: The frequency filter: a 4th-order Butterworth, run forward and backward
#: (zero phase, so it shifts no timing). 4th order cuts 24 dB per octave
#: beyond the edge; a chosen starting value: steep enough that the floor and
#: cap mean something, gentle enough not to ring audibly on speech.
FILTER_ORDER = 4


# ---------------------------------------------------------------------------
# VoiceParameters — The full control surface
# ---------------------------------------------------------------------------

@dataclass
class VoiceParameters:
    """
    Complete parameter set for a single voice synthesis request.

    All float values are normalized 0.0 → 1.0 unless otherwise noted.

    A control left at None is not requested: the voice is the being's own
    measured voice for that dimension. A control that is set is a request,
    and the engine either applies it or reports why it could not.
    (Until 2026-10-28 every control carried a default value — porosity 0.3,
    articulation 0.7 and so on — that no measurement stood behind and that
    reached no sound.)
    """

    # Core identity
    language: str = "en-US"              # BCP-47 language tag

    # Texture
    porosity: Optional[float] = None     # 0.0 = tight/clear, 1.0 = breathy/airy
    timbre: Optional[float] = None       # 0.0 = thin, 1.0 = rich/full
    jitter: Optional[float] = None       # 0.0 = steady pitch, 1.0 = most cycle-to-cycle pitch variation
    shimmer: Optional[float] = None      # 0.0 = steady loudness, 1.0 = most cycle-to-cycle loudness variation

    # Melody and rhythm
    intonation: Optional[float] = None       # 0.0 = monotone, 1.0 = highly melodic
    cadence: Optional[float] = None          # 0.0 = very slow, 1.0 = very fast
    prosody_strength: Optional[float] = None # 0.0 = flat stress, 1.0 = strong emphasis

    # Clarity
    articulation: Optional[float] = None     # 0.0 = slurred, 1.0 = hyper-precise
    resonance: Optional[ResonanceMode] = None

    # Emotion
    affect: Optional[Affect] = None

    # Accessibility overrides
    slow_mode: bool = False              # Forces cadence floor — cognitive support
    high_clarity_mode: bool = False      # Forces articulation ceiling — AAC clients
    frequency_cap_hz: Optional[int] = 8000   # Hard ceiling on output frequency (Hz)
    frequency_floor_hz: Optional[int] = 80   # Hard floor — filters subsonic artifacts

    # Output
    sample_rate: int = 22050            # Hz — christman_sound's standard; christman_synth also renders 16000
    output_format: str = "wav"          # wav only: nothing in the VCC encodes mp3 or flac

    FLOAT_CONTROLS = (
        "porosity", "timbre", "jitter", "shimmer", "intonation",
        "cadence", "prosody_strength", "articulation",
    )

    def apply_affect_preset(self) -> None:
        """
        Auto-configure parameters based on affect.
        Overrides only the dimensions most relevant to each affect type.
        Individual parameters can still be tuned after calling this.

        The numbers in this table are chosen starting values; no recording
        was measured to make them. Every value a preset sets is a request
        the engine must apply or report as not applied.
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
        for name in self.FLOAT_CONTROLS:
            val = getattr(self, name)
            if val is not None and not (0.0 <= val <= 1.0):
                errors.append(f"{name} must be 0.0–1.0, got {val}")
        if self.resonance is not None and not isinstance(self.resonance, ResonanceMode):
            errors.append(f"resonance must be a ResonanceMode, got {self.resonance!r}")
        if self.affect is not None and not isinstance(self.affect, Affect):
            errors.append(f"affect must be an Affect, got {self.affect!r}")

        for name in ("frequency_floor_hz", "frequency_cap_hz"):
            val = getattr(self, name)
            if val is not None and val <= 0:
                errors.append(f"{name} must be a positive number of Hz, got {val}")
        if (
            self.frequency_floor_hz is not None
            and self.frequency_cap_hz is not None
            and self.frequency_floor_hz >= self.frequency_cap_hz
        ):
            errors.append(
                f"frequency_floor_hz ({self.frequency_floor_hz}) must be "
                f"less than frequency_cap_hz ({self.frequency_cap_hz})"
            )
        if self.frequency_floor_hz is not None and self.frequency_floor_hz >= self.sample_rate / 2:
            errors.append(
                f"frequency_floor_hz ({self.frequency_floor_hz}) is at or above the "
                f"{self.sample_rate / 2:.0f} Hz limit of {self.sample_rate} Hz audio"
            )
        if self.sample_rate not in SYNTH_SAMPLE_RATES:
            errors.append(
                f"sample_rate {self.sample_rate} is not one christman_synth renders "
                f"({', '.join(map(str, SYNTH_SAMPLE_RATES))})"
            )
        if self.output_format != "wav":
            errors.append(f"output_format {self.output_format!r}: nothing in the VCC encodes it; wav only")

        return errors

    def requested_controls(self) -> Dict[str, object]:
        """The controls this request sets, with their values (None means not requested)."""
        out: Dict[str, object] = {}
        for name in SYNTH_INPUT_FOR:
            val = getattr(self, name)
            if val is not None:
                out[name] = val.value if isinstance(val, Enum) else val
        if self.slow_mode:
            out["slow_mode"] = True
        if self.high_clarity_mode:
            out["high_clarity_mode"] = True
        return out


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class ControlReport:
    """One control: what was asked, whether it reached the sound, and why not if it did not."""
    control: str
    requested: object
    applied: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SynthesisResult:
    """
    The output of a voice synthesis request.
    Always check success before using audio_data.
    """
    success: bool
    audio_data: Optional[bytes] = None       # A complete WAV file (16-bit mono PCM)
    output_path: Optional[str] = None        # Path if written to disk
    duration_seconds: float = 0.0
    sample_rate: Optional[int] = None        # The rate the audio was actually rendered at
    voice: Optional[str] = None              # The measured voice it was made in (from the profile)
    renderer: Optional[str] = None           # christman_synth's renderer, as it reported it
    controls: List[ControlReport] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def not_applied(self) -> List[ControlReport]:
        return [c for c in self.controls if not c.applied]

    def __repr__(self) -> str:
        if self.success:
            return (
                f"SynthesisResult(success=True, duration={self.duration_seconds:.2f}s, "
                f"voice={self.voice}, not_applied={[c.control for c in self.not_applied]})"
            )
        return f"SynthesisResult(success=False, error='{self.error}')"


# ---------------------------------------------------------------------------
# VoiceEngine — The Master Orchestrator
# ---------------------------------------------------------------------------

class VoiceEngine:
    """
    Master voice engine for the Christman Voice Creation Center.

    One engine speaks in one voice: the measured voice profile its being
    hands it. The VCC holds no being's voice.

    Usage:
        engine = VoiceEngine("/path/the/being/supplies/voice_profile.json")
        params = VoiceParameters(affect=Affect.GROUNDING)
        params.apply_affect_preset()
        result = engine.synthesize("I'm here with you.", params, allow_unapplied=True)
        if result.success:
            # result.audio_data is a WAV; result.controls says what was applied
    """

    def __init__(
        self,
        voice_profile_path: str,
        lexicon_path: Optional[str] = None,
        language: str = "en-US",
    ):
        """
        Args:
            voice_profile_path: The being's measured voice profile (JSON, the
                      format christman_sound.voice_profile reads). Required:
                      there is no default voice.
            lexicon_path: The pronunciation lexicon (Vosk align_lexicon.txt).
                      None: $CHRISTMAN_LEXICON_PATH.
            language: The language of that lexicon. A request in another
                      language is refused.
        """
        if not voice_profile_path:
            raise ValueError("voice_profile_path is required: the being supplies its own voice; there is no default")
        self.voice_profile_path = str(Path(voice_profile_path).expanduser())
        self.lexicon_path = lexicon_path
        self.language = language
        self._synthesize = None
        self._voice = None
        self._lexicon = None
        self._dsp = None
        self._synth_inputs: tuple = ()
        self._phoneme_labeler = None
        self._ready = False
        self._not_ready_reason: Optional[str] = None

        self._initialize_sdk()

    def _initialize_sdk(self) -> None:
        """
        Load the synthesizer, the being's voice, the lexicon and the C
        library. Any piece that fails leaves the engine not ready, with the
        real reason kept and logged — never silently.
        """
        try:
            from christman_sound.dsp import get_dsp
            from christman_sound.lexicon import load_lexicon
            from christman_sound.synth import synthesize
            from christman_sound.voice_profile import load_voice_profile
        except ImportError as e:
            self._fail(f"Christman-Sound is not importable: {e} (pip install -r requirements.txt)")
            return
        try:
            self._voice = load_voice_profile(self.voice_profile_path)
        except Exception as e:
            self._fail(f"voice profile {self.voice_profile_path}: {e}")
            return
        try:
            self._lexicon = load_lexicon(self.lexicon_path)
        except Exception as e:
            self._fail(f"lexicon: {e}")
            return
        dsp = get_dsp()
        if not getattr(dsp, "available", False):
            self._fail(f"christman_dsp not loaded: {getattr(dsp, 'error', None) or 'unknown'}")
            return
        if not getattr(dsp, "synth_available", False):
            self._fail(f"christman_synth not bound: {getattr(dsp, 'synth_error', None) or 'unknown'}")
            return
        self._dsp = dsp
        self._synthesize = synthesize
        self._synth_inputs = tuple(inspect.signature(synthesize).parameters)
        self._ready = True
        self._not_ready_reason = None
        logger.info(
            "Christman Voice Engine ready: voice %r (%s), synthesizer inputs %s",
            self._voice.name, self.voice_profile_path, ", ".join(self._synth_inputs),
        )

    def _fail(self, reason: str) -> None:
        self._ready = False
        self._not_ready_reason = reason
        logger.error("Voice engine not ready — %s", reason)

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def not_ready_reason(self) -> Optional[str]:
        return self._not_ready_reason

    @property
    def voice_name(self) -> Optional[str]:
        return self._voice.name if self._voice is not None else None

    # -- controls ------------------------------------------------------------

    def _synth_accepts(self, keyword: str) -> bool:
        if self._synthesize is not None:
            # Read now, not at start-up only: the synthesizer's inputs are the truth.
            self._synth_inputs = tuple(inspect.signature(self._synthesize).parameters)
        return keyword in self._synth_inputs

    def plan_controls(self, params: VoiceParameters) -> tuple:
        """
        (synth keyword arguments, [ControlReport]) for this request. A
        control is applied only when the synthesizer takes it; everything
        else is reported with the reason it is not.
        """
        kwargs: Dict[str, object] = {}
        reports: List[ControlReport] = []
        requested = params.requested_controls()

        for name, keyword in SYNTH_INPUT_FOR.items():
            if name not in requested:
                continue
            value = requested[name]
            if self._synth_accepts(keyword):
                kwargs[keyword] = value
                reports.append(ControlReport(name, value, True, f"passed to christman_sound.synth.synthesize as {keyword}={value!r}"))
            else:
                reason = f"christman_sound.synth.synthesize has no {keyword!r} input"
                if name == "cadence":
                    reason += "; its rate input is refused alongside a voice profile, which carries its own measured rate"
                reports.append(ControlReport(name, value, False, reason))

        if params.slow_mode:
            reports.append(ControlReport(
                "slow_mode", True, False,
                "no cadence floor is defined for it anywhere in the VCC or Christman-Sound, so nothing enforces it",
            ))
        if params.high_clarity_mode:
            reports.append(ControlReport(
                "high_clarity_mode", True, False,
                "no articulation ceiling is defined for it anywhere in the VCC or Christman-Sound, so nothing enforces it",
            ))

        if self._synth_accepts("sample_rate"):
            kwargs["sample_rate"] = params.sample_rate
            reports.append(ControlReport("sample_rate", params.sample_rate, True, "passed to synthesize as sample_rate"))
        else:
            reports.append(ControlReport("sample_rate", params.sample_rate, False, "synthesize has no sample_rate input"))

        for name in ("frequency_floor_hz", "frequency_cap_hz"):
            val = getattr(params, name)
            if val is None:
                continue
            nyquist = params.sample_rate / 2
            if name == "frequency_cap_hz" and val >= nyquist:
                reports.append(ControlReport(
                    name, val, True,
                    f"nothing above {val} Hz exists in {params.sample_rate} Hz audio (its limit is {nyquist:.0f} Hz); no cut needed",
                ))
            else:
                kind = "high-pass" if name == "frequency_floor_hz" else "low-pass"
                reports.append(ControlReport(
                    name, val, True,
                    f"applied by the VCC to the samples: order-{FILTER_ORDER} Butterworth {kind}, zero phase",
                ))
        return kwargs, reports

    # -- synthesis ------------------------------------------------------------

    def synthesize(
        self,
        text: str,
        params: Optional[VoiceParameters] = None,
        output_path: Optional[str] = None,
        allow_unapplied: bool = False,
    ) -> SynthesisResult:
        """
        Synthesize speech from text in this engine's voice.

        Args:
            text:        The text to synthesize.
            params:      VoiceParameters. None: no control requested, the
                         being's measured voice as it is.
            output_path: Optional path to write the WAV file to.
            allow_unapplied: When False (the default) a request for a
                         control that cannot be applied is refused and
                         nothing is rendered. When True the audio is made
                         and result.controls says what was not applied.

        Returns:
            SynthesisResult — always check .success before using audio.
            result.controls lists every requested control: applied or not,
            with the reason.
        """
        params = params if params is not None else VoiceParameters()
        if not text or not text.strip():
            return SynthesisResult(success=False, error="Cannot synthesize empty text.")

        errors = params.validate()
        if errors:
            return SynthesisResult(success=False, error=f"Invalid VoiceParameters: {'; '.join(errors)}")

        ok, why = voice_validator.validate_text(text)
        if not ok:
            return SynthesisResult(success=False, error=f"Refused by voice_validator: {why}")

        if not self._ready:
            return SynthesisResult(
                success=False,
                error=f"VoiceEngine not ready — {self._not_ready_reason}",
            )

        if params.language.lower().split("-")[0] != self.language.lower().split("-")[0]:
            return SynthesisResult(
                success=False,
                error=f"language {params.language!r}: this engine's lexicon is {self.language!r}; there is none for that language",
            )

        kwargs, reports = self.plan_controls(params)
        missing = [r for r in reports if not r.applied]
        if missing and not allow_unapplied:
            detail = "; ".join(f"{r.control}={r.requested!r}: {r.reason}" for r in missing)
            return SynthesisResult(
                success=False,
                controls=reports,
                error=f"Refused, nothing rendered: requested controls cannot be applied ({detail}). "
                      f"Pass allow_unapplied=True to render without them.",
            )

        try:
            made = self._synthesize(
                text, voice=self._voice, lexicon=self._lexicon, engine=self._dsp, **kwargs
            )
        except Exception as e:
            logger.error("Synthesis failed in voice %r: %s", self.voice_name, e)
            return SynthesisResult(success=False, controls=reports, error=f"{type(e).__name__}: {e}")

        if getattr(made, "voice", None) != self._voice.name:
            return SynthesisResult(
                success=False, controls=reports,
                error=f"the synthesizer reports voice {getattr(made, 'voice', None)!r}, not {self._voice.name!r}",
            )

        try:
            samples = self._apply_frequency_bounds(
                made.samples,
                floor_hz=params.frequency_floor_hz,
                cap_hz=params.frequency_cap_hz,
                sample_rate=made.sample_rate,
            )
            wav_bytes = _wav_bytes(samples, made.sample_rate)
        except ValueError as e:
            return SynthesisResult(success=False, controls=reports, error=str(e))

        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(wav_bytes)
            logger.info("Voice written to %s (%.2fs)", out, len(samples) / made.sample_rate)

        for r in missing:
            logger.warning("Rendered without %s=%r: %s", r.control, r.requested, r.reason)

        return SynthesisResult(
            success=True,
            audio_data=wav_bytes,
            output_path=str(output_path) if output_path else None,
            duration_seconds=round(len(samples) / made.sample_rate, 3),
            sample_rate=made.sample_rate,
            voice=made.voice,
            renderer=getattr(made, "renderer", None),
            controls=reports,
        )

    def _apply_frequency_bounds(
        self,
        samples,
        floor_hz: Optional[int],
        cap_hz: Optional[int],
        sample_rate: int,
    ):
        """
        Apply the frequency floor and ceiling to the samples: the
        accessibility safety gate. A high-pass at the floor; a low-pass at
        the cap when the cap is below the audio's own limit (half the sample
        rate). Raises ValueError rather than return audio it could not
        filter, or audio that left full scale.
        """
        import numpy as np
        x = np.asarray(samples, dtype=np.float64)
        if not np.any(x):
            raise ValueError("the synthesizer returned silence: every sample is zero")
        if floor_hz is None and (cap_hz is None or cap_hz >= sample_rate / 2):
            return x
        from scipy.signal import butter, sosfiltfilt
        if floor_hz is not None:
            sos = butter(FILTER_ORDER, floor_hz, btype="highpass", fs=sample_rate, output="sos")
            x = sosfiltfilt(sos, x)
        if cap_hz is not None and cap_hz < sample_rate / 2:
            sos = butter(FILTER_ORDER, cap_hz, btype="lowpass", fs=sample_rate, output="sos")
            x = sosfiltfilt(sos, x)
        peak = float(np.max(np.abs(x)))
        if peak > 1.0:
            raise ValueError(f"after the frequency filter the peak is {peak:.3f}, past full scale; refusing to clip")
        return x

    # -- phonemes (the SDK's labeler, the one copy) ---------------------------

    def label_phonemes(self, audio_path: str, transcript: Optional[str] = None) -> list:
        """
        Phoneme timing for a recording, from Christman-Sound's labeler
        (christman_voice_sdk.synthesis.phoneme_labeler). Analysis only: it
        makes no sound.
        """
        if self._phoneme_labeler is None:
            from christman_voice_sdk.synthesis.phoneme_labeler import PhonemeLabeler
            self._phoneme_labeler = PhonemeLabeler()
        return self._phoneme_labeler.label_audio(Path(audio_path), transcript)

    # -- status ----------------------------------------------------------------

    def get_engine_status(self) -> dict:
        """
        Returns the current engine status.
        Used by the admin dashboard and health checks. Never raises.
        """
        support = {}
        for name, keyword in SYNTH_INPUT_FOR.items():
            support[name] = (
                {"applied": True, "synth_input": keyword}
                if self._synthesize is not None and self._synth_accepts(keyword)
                else {"applied": False, "synth_input": keyword,
                      "reason": f"christman_sound.synth.synthesize has no {keyword!r} input"
                      if self._synthesize is not None else "the synthesizer is not loaded"}
            )
        return {
            "ready": self._ready,
            "not_ready_reason": self._not_ready_reason,
            "voice_profile_path": self.voice_profile_path,
            "voice": self.voice_name,
            "lexicon": str(getattr(self._lexicon, "path", None)) if self._lexicon is not None else None,
            "language": self.language,
            "synthesizer": "christman_sound.synth.synthesize" if self._synthesize is not None else None,
            "synthesizer_inputs": list(self._synth_inputs),
            "controls": support,
            "phoneme_labeler": "christman_voice_sdk.synthesis.phoneme_labeler.PhonemeLabeler",
            "phoneme_labeler_loaded": self._phoneme_labeler is not None,
        }


def _wav_bytes(samples, sample_rate: int) -> bytes:
    """A complete mono 16-bit PCM WAV file. A sample outside [-1, 1] is refused, not clipped."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float64)
    if np.any(np.abs(x) > 1.0):
        raise ValueError("a sample is outside [-1, 1]; refusing to clip")
    pcm = np.round(x * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
