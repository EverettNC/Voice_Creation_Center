"""
Test signals for the studio tests, written as real 16-bit mono WAV files.

These are signals with known properties, made so a test knows what the
listener should measure. None of them is anyone's voice and none is used
outside the tests.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np


def _write(path: Path, samples: np.ndarray, rate: int) -> Path:
    pcm = np.clip(np.round(samples * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return Path(path)


def write_silent_wav(path: Path, seconds: float = 0.5, rate: int = 22050) -> Path:
    """Every sample zero."""
    return _write(path, np.zeros(int(seconds * rate)), rate)


def write_noise_wav(path: Path, seconds: float = 1.0, rate: int = 22050, level: float = 0.2) -> Path:
    """White noise at `level` peak: loud, but repeats at no pitch (fixed seed)."""
    rng = np.random.default_rng(20261028)
    return _write(path, rng.uniform(-level, level, int(seconds * rate)), rate)


def write_voiced_wav(path: Path, seconds: float = 1.0, rate: int = 22050, f0: float = 150.0) -> Path:
    """
    A periodic, voice-like signal: a 150 Hz fundamental with its first ten
    harmonics falling off as 1/k, a slow pitch drift of +/-3 %, and a
    syllable-shaped loudness envelope. Periodic at a speaking voice's pitch,
    the way a sustained vowel is.
    """
    n = int(seconds * rate)
    t = np.arange(n) / rate
    inst_f0 = f0 * (1.0 + 0.03 * np.sin(2 * math.pi * 3.0 * t))
    phase = 2 * math.pi * np.cumsum(inst_f0) / rate
    x = sum(np.sin(k * phase) / k for k in range(1, 11))
    env = np.sin(math.pi * np.clip(t / seconds, 0, 1)) ** 0.5
    x = x * env
    return _write(path, 0.3 * x / np.max(np.abs(x)), rate)


def write_tone_wav(path: Path, freq: float = 440.0, seconds: float = 0.5, rate: int = 22050) -> Path:
    """A full-scale sine."""
    n = int(seconds * rate)
    samples = [int(32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{n}h", *samples))
    return Path(path)


def write_phrases_with_gaps_wav(path: Path, count: int = 2, rate: int = 44100,
                                phrase_seconds: float = 0.8, gap_seconds: float = 0.8) -> Path:
    """`count` voice-like phrases (as write_voiced_wav) separated by digital silence."""
    n = int(phrase_seconds * rate)
    t = np.arange(n) / rate
    phase = 2 * math.pi * np.cumsum(150.0 * (1.0 + 0.03 * np.sin(2 * math.pi * 3.0 * t))) / rate
    x = sum(np.sin(k * phase) / k for k in range(1, 11)) * np.sin(math.pi * t / phrase_seconds) ** 0.5
    x = 0.3 * x / np.max(np.abs(x))
    gap = np.zeros(int(gap_seconds * rate))
    parts = [gap]
    for _ in range(count):
        parts += [x, gap]
    return _write(path, np.concatenate(parts), rate)
