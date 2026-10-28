"""
================================================================================
FILE: studio/take_listener.py
PROJECT: Christman Voice Creation Center — Live Studio
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    Listens to a take before the studio will pass it.

    WHAT CHANGED AND WHY (added 2026-10-28)
    Validation used to check only the typed transcript. It never opened the
    WAV, so a file of pure digital silence was validated, approved and
    exported as "I love you". This module reads the take itself and measures
    it: how long it runs, how loud it is, and how much of it is voiced —
    sound that repeats at a voice's pitch, the way vowels and voiced
    consonants do. A take with no voiced sound (silence, hiss, a click) or
    of an unreasonable length is refused, with the measurement that refused
    it.

    What it does NOT do: it does not recognize words. It cannot tell "I love
    you" from "I need help". A passing take has voiced sound of a reasonable
    length; whether the words are the target phrase is still the reviewer's
    judgment (mark_validation's `passed`).

    Every limit below is a chosen starting value with its reason beside it,
    held until measurements from real takes replace it.
================================================================================
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

# ── Limits (chosen starting values, each with its reason) ────────────────────

#: A single phrase shorter than this cannot hold even one short word with
#: its onset and release; a click or a cut-off take is shorter.
MIN_TAKE_SECONDS = 0.3
#: The ingestor's own ceiling for one phrase (voice_ingestor MAX_PHRASE_MS),
#: so the studio and the ingestor agree on what one phrase can be.
MAX_TAKE_SECONDS = 15.0
#: Voiced sound needed for a take to count as spoken. Measured 2026-10-28:
#: "Yes", rendered by christman_synth in a measured voice, carries 0.12 s of
#: voiced sound by this module's count, the least of the phrases tried; 0.10
#: lets a one-syllable word through and still refuses a click or a breath.
MIN_VOICED_SECONDS = 0.10
#: A frame quieter than this (dB below full scale) is not counted as voice,
#: however periodic it looks. It sits 15 dB above the recorder's noise-floor
#: constant (-60 dB), so hum or room tone near that floor is not counted.
VOICED_MIN_DB = -45.0
#: The pitch range a speaking voice sits in, low adult voices to children.
VOICE_PITCH_HZ = (60.0, 400.0)
#: How strongly a frame must repeat at one period (normalized
#: autocorrelation, 0..1) to count as voiced. Steady vowels measure well
#: above this; noise and silence measure well below it.
PERIODICITY_MIN = 0.5
#: 30 ms frames every 10 ms: long enough to hold two periods of a 60 Hz
#: voice, short enough to follow syllables.
FRAME_SECONDS = 0.030
HOP_SECONDS = 0.010


class TakeUnreadable(ValueError):
    """The file is not a PCM WAV this module can read. Nothing was measured."""


@dataclass
class TakeReport:
    """What listening to a take measured, and whether it passed."""

    path: str
    sample_rate: int
    duration_seconds: float
    peak_db: Optional[float]   # None when every sample is zero
    rms_db: Optional[float]
    voiced_seconds: float
    passed: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _read_pcm(path: Path):
    """Mono float samples in [-1, 1] and the sample rate, from a PCM WAV."""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            width = w.getsampwidth()
            channels = w.getnchannels()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise TakeUnreadable(f"cannot read {path} as a WAV: {exc}") from exc
    if rate <= 0:
        raise TakeUnreadable(f"{path} reports a sample rate of {rate}")
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float64) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / float(1 << 31)
    else:
        raise TakeUnreadable(f"{path}: {width}-byte samples are not PCM this module reads")
    if channels > 1:
        x = x[: len(x) - len(x) % channels].reshape(-1, channels).mean(axis=1)
    return x, rate


def _db(value: float):
    """dB below full scale, or None for a value of exactly zero (there is no dB for it)."""
    return round(float(20.0 * np.log10(value)), 2) if value > 0 else None


def voiced_seconds(samples: np.ndarray, rate: int) -> float:
    """Seconds of the signal that are loud enough and periodic at a voice's pitch."""
    frame = int(round(FRAME_SECONDS * rate))
    hop = int(round(HOP_SECONDS * rate))
    if len(samples) < frame or frame < 4:
        return 0.0
    n = 1 + (len(samples) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    frames = samples[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    loud = 20.0 * np.log10(np.maximum(rms, 1e-12)) >= VOICED_MIN_DB

    lo = max(1, int(rate / VOICE_PITCH_HZ[1]))
    hi = min(frame - 1, int(rate / VOICE_PITCH_HZ[0]))
    size = 1 << int(np.ceil(np.log2(2 * frame)))
    spec = np.fft.rfft(frames, n=size, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), n=size, axis=1)[:, :frame]
    energy = acf[:, :1]
    # Divide each lag by the overlap it was summed over, so long lags are not
    # penalized for having fewer samples, then normalize by lag 0.
    overlap = (frame - np.arange(frame)) / frame
    norm = np.where(energy > 0, acf / np.maximum(energy, 1e-30) / overlap[None, :], 0.0)
    periodicity = norm[:, lo:hi + 1].max(axis=1)
    voiced = loud & (periodicity >= PERIODICITY_MIN)
    return float(voiced.sum() * hop / rate)


def listen(path: Union[str, Path]) -> TakeReport:
    """
    Measure the take at `path`. Raises TakeUnreadable when it is not a WAV
    this module can read; otherwise returns a TakeReport whose `passed` is
    False, with every reason, when the take is silent, unvoiced, too short
    or too long.
    """
    p = Path(path)
    samples, rate = _read_pcm(p)
    duration = len(samples) / rate
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    voiced = voiced_seconds(samples, rate)

    reasons: List[str] = []
    if peak == 0.0:
        reasons.append("the take is pure silence: every sample is zero")
    if duration < MIN_TAKE_SECONDS:
        reasons.append(f"the take runs {duration:.3f} s, under {MIN_TAKE_SECONDS} s")
    if duration > MAX_TAKE_SECONDS:
        reasons.append(f"the take runs {duration:.3f} s, over {MAX_TAKE_SECONDS} s for one phrase")
    if peak > 0.0 and voiced < MIN_VOICED_SECONDS:
        reasons.append(
            f"only {voiced:.3f} s of voiced sound (needs {MIN_VOICED_SECONDS} s): "
            f"nothing in it repeats at a speaking voice's pitch "
            f"({VOICE_PITCH_HZ[0]:.0f}-{VOICE_PITCH_HZ[1]:.0f} Hz) above {VOICED_MIN_DB} dB"
        )
    return TakeReport(
        path=str(p.resolve()),
        sample_rate=rate,
        duration_seconds=round(duration, 3),
        peak_db=_db(peak),
        rms_db=_db(rms),
        voiced_seconds=round(voiced, 3),
        passed=not reasons,
        reasons=reasons,
    )
