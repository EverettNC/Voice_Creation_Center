"""
================================================================================
FILE: studio/recorder.py
PROJECT: Christman Voice Creation Center — Live Studio Phase 2
AUTHOR: The Christman AI Project | Luma Cognify AI
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    Real microphone capture for the Christman Voice Creation Center.

    Phase 1 had an honest stub for capture_take() — it raised
    NotImplementedError because we had no audio capture.
    Phase 2 fixes that. This module is the real thing.

    THREE MODES:
        record_fixed(output_path, seconds)
            Record for an exact number of seconds. Good for phrase capture
            when you know how long the phrase runs.

        record_open(output_path)  +  stop()
            Start recording. Stop whenever you're ready.
            Good for album work, longer takes, or variable phrases.

        play(wav_path)  +  stop_playback()
            Immediate playback preview of any WAV file.
            Hear it before you approve it.

    STANDARD:
        All audio is normalized to 22050Hz mono — the Christman SDK standard.
        Raw takes are written as WAV only. No lossy formats enter the Center.

CARDINAL RULE 13: No fake recording. No phantom files.
    Every WAV written by this module is real audio from the microphone.
    If the device is unavailable, this module says so clearly and stops.
    It does NOT write silence and pretend it recorded.

CARDINAL RULE 6: Fail loud.
    Device errors, clipping, buffer overruns — all logged with context.
    The caller always knows what happened.
================================================================================
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("christman.recorder")

# ── Christman SDK audio standard ─────────────────────────────────────────────
SAMPLE_RATE   = 22050   # Hz — matches christman_sound standard
CHANNELS      = 1       # Mono — all beings speak in mono
BIT_DEPTH     = 16      # 16-bit PCM
DTYPE         = "int16"

# Safety thresholds
CLIPPING_THRESHOLD = 0.98    # Ratio of max int16 — flag at 98%
NOISE_FLOOR_DB     = -60.0   # dB — silence below this is noise floor


# ── Recorder state ────────────────────────────────────────────────────────────

class RecorderState(str, Enum):
    IDLE       = "idle"
    RECORDING  = "recording"
    STOPPING   = "stopping"
    STOPPED    = "stopped"
    PLAYING    = "playing"
    ERROR      = "error"


@dataclass
class RecordingStats:
    """
    Metrics from a completed recording.
    Used by the validation engine and the admin dashboard.
    """
    duration_seconds:  float = 0.0
    sample_rate:       int   = SAMPLE_RATE
    channels:          int   = CHANNELS
    peak_db:           float = -96.0
    rms_db:            float = -96.0
    clipped:           bool  = False
    noise_floor_db:    float = -96.0
    frames_recorded:   int   = 0
    device_name:       str   = ""
    recorded_at:       str   = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "duration_seconds":  self.duration_seconds,
            "sample_rate":       self.sample_rate,
            "channels":          self.channels,
            "peak_db":           round(self.peak_db, 2),
            "rms_db":            round(self.rms_db, 2),
            "clipped":           self.clipped,
            "noise_floor_db":    round(self.noise_floor_db, 2),
            "frames_recorded":   self.frames_recorded,
            "device_name":       self.device_name,
            "recorded_at":       self.recorded_at,
        }


@dataclass
class DeviceInfo:
    """Clean summary of an audio input device."""
    index:         int
    name:          str
    max_channels:  int
    default_rate:  float
    is_default:    bool = False

    def __str__(self) -> str:
        default_tag = " [DEFAULT]" if self.is_default else ""
        return (
            f"[{self.index}] {self.name}"
            f"  {self.max_channels}ch  {int(self.default_rate)}Hz"
            f"{default_tag}"
        )


# ── Core recorder ─────────────────────────────────────────────────────────────

class Recorder:
    """
    Real microphone recorder for the Christman Voice Creation Center.

    Usage:
        recorder = Recorder()

        # List available input devices
        devices = recorder.list_input_devices()

        # Fixed-duration recording
        stats = recorder.record_fixed("raw/i_love_you.wav", duration_seconds=3.0)

        # Open-ended recording — stop when ready
        recorder.record_open("raw/take_001.wav")
        # ... the speaker speaks ...
        stats = recorder.stop()

        # Playback preview
        recorder.play("raw/take_001.wav")
        time.sleep(3)
        recorder.stop_playback()
    """

    def __init__(self, device_index: Optional[int] = None):
        """
        Args:
            device_index: Input device index from list_input_devices().
                          None uses the system default input device.
        """
        self._device_index = device_index
        self._state        = RecorderState.IDLE
        self._stop_event   = threading.Event()
        self._frames:  list = []
        self._thread:  Optional[threading.Thread] = None
        self._stats:   Optional[RecordingStats] = None
        self._sd       = None   # sounddevice — loaded lazily

        self._load_sounddevice()

    # ── Device management ─────────────────────────────────────────────────────

    def _load_sounddevice(self) -> bool:
        """Load sounddevice. Fail loud if unavailable."""
        try:
            import sounddevice as sd
            self._sd = sd
            return True
        except ImportError:
            logger.error(
                "sounddevice not installed.\n"
                "Install it with:  pip install sounddevice\n"
                "On Mac you may also need:  brew install portaudio"
            )
            self._state = RecorderState.ERROR
            return False

    def list_input_devices(self) -> list[DeviceInfo]:
        """
        Return all available audio input devices.
        Useful for choosing the microphone before recording.
        """
        if self._sd is None:
            logger.error("sounddevice not available — cannot list devices.")
            return []

        try:
            devices = self._sd.query_devices()
            default_input = self._sd.query_devices(kind="input")
            default_name  = default_input.get("name", "") if default_input else ""

            result = []
            for i, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) > 0:
                    result.append(DeviceInfo(
                        index=i,
                        name=dev["name"],
                        max_channels=dev["max_input_channels"],
                        default_rate=dev.get("default_samplerate", SAMPLE_RATE),
                        is_default=(dev["name"] == default_name),
                    ))

            if not result:
                logger.warning("No input devices found. Check mic connection.")

            return result

        except Exception as e:
            logger.error(f"Failed to list input devices: {e}")
            return []

    def print_devices(self) -> None:
        """Print a human-readable device list."""
        devices = self.list_input_devices()
        if not devices:
            print("  No input devices found.")
            return
        print("\n  Available input devices:")
        print("  " + "─" * 60)
        for dev in devices:
            print(f"  {dev}")
        print("  " + "─" * 60 + "\n")

    def set_device(self, device_index: int) -> bool:
        """
        Switch to a specific input device.
        Returns True if the device exists and supports input.
        """
        devices = self.list_input_devices()
        match = next((d for d in devices if d.index == device_index), None)
        if match is None:
            logger.error(
                f"Device index {device_index} not found or has no input channels. "
                f"Run list_input_devices() to see available devices."
            )
            return False
        self._device_index = device_index
        logger.info(f"Input device set: {match.name}")
        return True

    # ── Fixed-duration recording ──────────────────────────────────────────────

    def record_fixed(
        self,
        output_path: str,
        duration_seconds: float,
        countdown: bool = True
    ) -> Optional[RecordingStats]:
        """
        Record for an exact number of seconds, then stop automatically.

        Args:
            output_path:      Where to write the WAV file.
            duration_seconds: How long to record.
            countdown:        If True, print a 3-2-1 countdown before recording.

        Returns:
            RecordingStats on success, None on failure.
        """
        if not self._assert_ready("record_fixed"):
            return None

        if duration_seconds <= 0:
            logger.error("duration_seconds must be greater than 0.")
            return None

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if countdown:
            self._countdown()

        try:
            n_frames = int(duration_seconds * SAMPLE_RATE)
            device_name = self._get_device_name()

            logger.info(
                f"Recording {duration_seconds:.1f}s → {output.name} "
                f"[{device_name}]"
            )
            self._state = RecorderState.RECORDING

            audio = self._sd.rec(
                n_frames,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                device=self._device_index,
            )
            self._sd.wait()  # Block until recording completes

            self._state = RecorderState.STOPPED
            stats = self._write_wav(audio, output, device_name)
            self._stats = stats
            return stats

        except Exception as e:
            self._state = RecorderState.ERROR
            logger.error(f"record_fixed failed: {e}", exc_info=True)
            return None

    # ── Open-ended recording ─────────────────────────────────────────────────

    def record_open(
        self,
        output_path: str,
        countdown: bool = True
    ) -> bool:
        """
        Start recording. Runs until stop() is called.

        Args:
            output_path: Where to write the WAV file when stopped.
            countdown:   If True, print a 3-2-1 countdown before recording.

        Returns:
            True if recording started, False if it could not start.
        """
        if not self._assert_ready("record_open"):
            return False

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        self._frames = []
        self._stop_event.clear()
        self._output_path = output

        if countdown:
            self._countdown()

        device_name = self._get_device_name()
        logger.info(f"Recording open-ended → {output.name} [{device_name}]")
        self._state = RecorderState.RECORDING

        def _capture_loop():
            try:
                with self._sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    device=self._device_index,
                    callback=self._audio_callback,
                ):
                    self._stop_event.wait()  # Hold until stop() is called

            except Exception as e:
                self._state = RecorderState.ERROR
                logger.error(f"Recording stream failed: {e}", exc_info=True)

        self._thread = threading.Thread(target=_capture_loop, daemon=True)
        self._thread.start()
        return True

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for InputStream — accumulates frames."""
        if status:
            logger.warning(f"Audio stream status: {status}")
        self._frames.append(indata.copy())

    def stop(self) -> Optional[RecordingStats]:
        """
        Stop an open-ended recording and write the WAV file.

        Returns:
            RecordingStats on success, None if not recording or on error.
        """
        if self._state != RecorderState.RECORDING:
            logger.warning(
                f"stop() called but recorder is in state '{self._state.value}'. "
                f"Call record_open() first."
            )
            return None

        self._state = RecorderState.STOPPING
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=3.0)

        if not self._frames:
            logger.error("No audio frames captured. Check mic connection.")
            self._state = RecorderState.IDLE
            return None

        try:
            audio = np.concatenate(self._frames, axis=0)
            device_name = self._get_device_name()
            stats = self._write_wav(audio, self._output_path, device_name)
            self._stats = stats
            self._state = RecorderState.STOPPED
            self._frames = []
            return stats

        except Exception as e:
            self._state = RecorderState.ERROR
            logger.error(f"stop() failed while writing WAV: {e}", exc_info=True)
            return None

    # ── Playback preview ──────────────────────────────────────────────────────

    def play(self, wav_path: str) -> bool:
        """
        Play a WAV file for preview.
        Non-blocking — returns immediately while audio plays in background.
        Call stop_playback() to interrupt.

        Returns True if playback started, False on error.
        """
        if self._sd is None:
            logger.error("sounddevice not available — cannot play audio.")
            return False

        path = Path(wav_path)
        if not path.exists():
            logger.error(f"Playback failed — file not found: {wav_path}")
            return False

        try:
            import soundfile as sf
            audio, rate = sf.read(str(path), dtype=DTYPE)
            self._state = RecorderState.PLAYING
            self._sd.play(audio, samplerate=rate)
            logger.info(f"Playing: {path.name}")
            return True

        except ImportError:
            # soundfile unavailable — fall back to wave + numpy
            try:
                with wave.open(str(path), "rb") as wf:
                    rate   = wf.getframerate()
                    frames = wf.readframes(wf.getnframes())
                    audio  = np.frombuffer(frames, dtype=DTYPE)

                self._state = RecorderState.PLAYING
                self._sd.play(audio, samplerate=rate)
                logger.info(f"Playing (wave fallback): {path.name}")
                return True

            except Exception as e:
                logger.error(f"Playback failed for {path.name}: {e}", exc_info=True)
                self._state = RecorderState.IDLE
                return False

        except Exception as e:
            logger.error(f"Playback failed for {path.name}: {e}", exc_info=True)
            self._state = RecorderState.IDLE
            return False

    def stop_playback(self) -> None:
        """Stop active playback immediately."""
        if self._sd is not None:
            self._sd.stop()
        if self._state == RecorderState.PLAYING:
            self._state = RecorderState.IDLE
        logger.debug("Playback stopped.")

    def wait_for_playback(self) -> None:
        """Block until playback finishes naturally."""
        if self._sd is not None:
            self._sd.wait()
        self._state = RecorderState.IDLE

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _write_wav(
        self,
        audio: np.ndarray,
        output_path: Path,
        device_name: str
    ) -> RecordingStats:
        """Write audio array to a WAV file and compute recording stats."""
        # Ensure correct shape (n_frames, 1) → (n_frames,)
        if audio.ndim > 1:
            audio = audio[:, 0]

        # Write WAV
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())

        # Compute stats
        duration   = len(audio) / SAMPLE_RATE
        peak_abs   = np.max(np.abs(audio.astype(np.float32)))
        max_int16  = float(np.iinfo(np.int16).max)
        peak_ratio = peak_abs / max_int16
        clipped    = peak_ratio >= CLIPPING_THRESHOLD

        # dB calculations (avoid log(0))
        if peak_abs > 0:
            peak_db = 20.0 * np.log10(peak_ratio)
        else:
            peak_db = -96.0

        rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
        if rms > 0:
            rms_db = 20.0 * np.log10(rms / max_int16)
        else:
            rms_db = -96.0

        # Noise floor: estimate from first 0.1s (assumed silence before speech)
        noise_samples = audio[:int(SAMPLE_RATE * 0.1)]
        if len(noise_samples) > 0:
            noise_rms = np.sqrt(np.mean(noise_samples.astype(np.float32) ** 2))
            noise_floor_db = 20.0 * np.log10(noise_rms / max_int16) if noise_rms > 0 else -96.0
        else:
            noise_floor_db = -96.0

        stats = RecordingStats(
            duration_seconds=round(duration, 3),
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            peak_db=float(peak_db),
            rms_db=float(rms_db),
            clipped=bool(clipped),
            noise_floor_db=float(noise_floor_db),
            frames_recorded=len(audio),
            device_name=device_name,
        )

        if clipped:
            logger.warning(
                f"CLIPPING detected in {output_path.name} — "
                f"peak at {peak_db:.1f}dB ({peak_ratio:.1%} of max). "
                f"Reduce mic gain and re-record."
            )
        else:
            logger.info(
                f"Recorded: {output_path.name} — "
                f"{duration:.2f}s  peak={peak_db:.1f}dB  rms={rms_db:.1f}dB"
            )

        return stats

    def _get_device_name(self) -> str:
        """Get the name of the current input device."""
        if self._sd is None:
            return "unknown"
        try:
            if self._device_index is not None:
                dev = self._sd.query_devices(self._device_index)
                return dev.get("name", f"device_{self._device_index}")
            else:
                dev = self._sd.query_devices(kind="input")
                return dev.get("name", "default") if dev else "default"
        except Exception:
            return "unknown"

    def _assert_ready(self, caller: str) -> bool:
        """Assert recorder is in a state that allows recording."""
        if self._sd is None:
            logger.error(
                f"{caller}: sounddevice not available. "
                f"Install it with: pip install sounddevice"
            )
            return False
        if self._state == RecorderState.RECORDING:
            logger.error(
                f"{caller}: already recording. "
                f"Call stop() before starting a new recording."
            )
            return False
        if self._state == RecorderState.ERROR:
            logger.error(
                f"{caller}: recorder is in error state. "
                f"Check device connection and restart."
            )
            return False
        return True

    def _countdown(self, seconds: int = 3) -> None:
        """Print a countdown before recording starts."""
        print()
        for i in range(seconds, 0, -1):
            print(f"  Recording in {i}...", end="\r", flush=True)
            time.sleep(1)
        print("  🎙️  Recording...        ", flush=True)

    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def last_stats(self) -> Optional[RecordingStats]:
        """Stats from the most recent completed recording."""
        return self._stats

    def is_recording(self) -> bool:
        return self._state == RecorderState.RECORDING

    def reset(self) -> None:
        """Reset the recorder to IDLE state. Does not delete any files."""
        if self._state == RecorderState.RECORDING:
            self.stop()
        self._state  = RecorderState.IDLE
        self._frames = []
        self._stats  = None


# ==============================================================================
# © 2026 Everett Nathaniel Christman & The Christman AI Project
# Luma Cognify AI — "How can we help you love yourself more?"
# Patent Pending TCAP-2026-001 | TCAP-2026-002
# Cardinal Rule 1: It has to actually work.
# Cardinal Rule 6: Fail loud.
# Cardinal Rule 13: No fake recording. No phantom files.
# ==============================================================================
