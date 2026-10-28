"""
Tests for the voice engine: real renders through Christman-Sound.

The VCC holds no being's voice, so these tests are handed one from outside:

    VCC_TEST_VOICE_PROFILE=<a measured voice profile JSON>
    CHRISTMAN_LEXICON_PATH=<Vosk align_lexicon.txt>

Without them the tests that render are NOT RUN, with that reason. Nothing
here plays sound aloud: audio is rendered to memory or a temp file and
measured.
"""

import io
import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest

import take_listener
from voice_engine import (
    SYNTH_INPUT_FOR,
    Affect,
    ResonanceMode,
    VoiceEngine,
    VoiceParameters,
)

PROFILE = os.environ.get("VCC_TEST_VOICE_PROFILE")
LEXICON = os.environ.get("CHRISTMAN_LEXICON_PATH")

#: A value to request for each control, and a second, different one, so a
#: control the synthesizer takes can be shown to change the sound.
REQUEST_VALUES = {
    "porosity": (0.2, 0.8),
    "timbre": (0.2, 0.8),
    "intonation": (0.2, 0.8),
    "cadence": (0.2, 0.8),
    "prosody_strength": (0.2, 0.8),
    "articulation": (0.2, 0.8),
    "jitter": (0.2, 0.8),
    "shimmer": (0.2, 0.8),
    "resonance": (ResonanceMode.HEAD, ResonanceMode.CHEST),
    "affect": (Affect.CALM, Affect.URGENT),
}


@pytest.fixture(scope="module")
def engine():
    if not PROFILE:
        pytest.skip("NOT RUN: no voice was handed in; set VCC_TEST_VOICE_PROFILE to a measured voice profile")
    if not LEXICON:
        pytest.skip("NOT RUN: no lexicon; set CHRISTMAN_LEXICON_PATH to a Vosk align_lexicon.txt")
    e = VoiceEngine(PROFILE, lexicon_path=LEXICON)
    assert e.is_ready, f"a voice and lexicon were handed in but the engine is not ready: {e.not_ready_reason}"
    return e


def _wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    return x, rate


def _band_energy_db(x: np.ndarray, rate: int, lo: float, hi: float) -> float:
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = spec[(freqs >= lo) & (freqs < hi)].sum()
    return 10.0 * np.log10(band / spec.sum())


# ── No voice of its own ──────────────────────────────────────────────────────

def test_there_is_no_default_voice():
    with pytest.raises(TypeError):
        VoiceEngine()
    with pytest.raises(ValueError):
        VoiceEngine("")


def test_missing_voice_is_not_ready_and_says_why(tmp_path):
    e = VoiceEngine(str(tmp_path / "no_such_profile.json"))
    assert not e.is_ready
    assert "no_such_profile.json" in e.not_ready_reason
    r = e.synthesize("hello")
    assert not r.success and "not ready" in r.error
    status = e.get_engine_status()          # used to raise AttributeError
    assert status["ready"] is False
    assert status["not_ready_reason"] == e.not_ready_reason
    assert all(not c["applied"] for c in status["controls"].values())


def test_parameters_are_validated():
    assert VoiceParameters(sample_rate=44100).validate()
    assert VoiceParameters(output_format="mp3").validate()
    assert VoiceParameters(porosity=2.0).validate()
    assert VoiceParameters(frequency_floor_hz=9000, frequency_cap_hz=8000).validate()
    assert not VoiceParameters().validate()
    assert VoiceParameters().requested_controls() == {}, "nothing is requested unless it is set"


# ── Real renders ─────────────────────────────────────────────────────────────

def test_renders_real_speech_in_the_voice_handed_in(engine, tmp_path):
    out = tmp_path / "out.wav"
    r = engine.synthesize("I love you", output_path=str(out))
    assert r.success, r.error
    assert r.voice == json.loads(Path(PROFILE).read_text())["voice"]
    assert out.read_bytes() == r.audio_data
    x, rate = _wav(r.audio_data)
    assert rate == r.sample_rate == 22050
    report = take_listener.listen(out)
    assert report.passed, report.reasons
    assert report.voiced_seconds >= 0.3


def test_sample_rate_reaches_the_sound(engine):
    for rate in (16000, 22050):
        r = engine.synthesize("Thank you", VoiceParameters(sample_rate=rate))
        assert r.success, r.error
        assert _wav(r.audio_data)[1] == rate


@pytest.mark.parametrize("control", sorted(SYNTH_INPUT_FOR))
def test_every_control_is_applied_to_the_sound_or_refused(engine, tmp_path, control):
    """
    A control the synthesizer takes must change the sound. A control it
    does not take must be refused (nothing rendered, nothing written) unless
    allowed, and reported as not applied, with the reason, either way.
    """
    a, b = REQUEST_VALUES[control]
    keyword = SYNTH_INPUT_FOR[control]
    if engine._synth_accepts(keyword):
        ra = engine.synthesize("I am here with you", VoiceParameters(**{control: a}))
        rb = engine.synthesize("I am here with you", VoiceParameters(**{control: b}))
        assert ra.success and rb.success, (ra.error, rb.error)
        assert [c for c in ra.controls if c.control == control][0].applied
        assert ra.audio_data != rb.audio_data, f"{control} is passed to the synthesizer but did not change the sound"
    else:
        out = tmp_path / "refused.wav"
        r = engine.synthesize("I am here with you", VoiceParameters(**{control: a}), output_path=str(out))
        assert not r.success
        assert control in r.error and keyword in r.error
        assert not out.exists(), "a refused request must write nothing"
        allowed = engine.synthesize("I am here with you", VoiceParameters(**{control: a}), allow_unapplied=True)
        assert allowed.success, allowed.error
        rep = [c for c in allowed.controls if c.control == control][0]
        assert rep.applied is False and keyword in rep.reason


def test_frequency_floor_reaches_the_sound(engine):
    open_band = engine.synthesize("Good morning", VoiceParameters(frequency_floor_hz=None, frequency_cap_hz=None))
    floored = engine.synthesize("Good morning", VoiceParameters(frequency_floor_hz=400, frequency_cap_hz=None))
    assert open_band.success and floored.success
    x0, rate = _wav(open_band.audio_data)
    x1, _ = _wav(floored.audio_data)
    before = _band_energy_db(x0, rate, 0, 200)
    after = _band_energy_db(x1, rate, 0, 200)
    assert after < before - 20, f"energy below 200 Hz: {before:.1f} dB before, {after:.1f} dB after a 400 Hz floor"


def test_frequency_cap_reaches_the_sound(engine):
    open_band = engine.synthesize("Good morning", VoiceParameters(frequency_floor_hz=None, frequency_cap_hz=None))
    capped = engine.synthesize("Good morning", VoiceParameters(frequency_floor_hz=None, frequency_cap_hz=2000))
    x0, rate = _wav(open_band.audio_data)
    x1, _ = _wav(capped.audio_data)
    before = _band_energy_db(x0, rate, 4000, rate / 2)
    after = _band_energy_db(x1, rate, 4000, rate / 2)
    assert after < before - 20, f"energy above 4 kHz: {before:.1f} dB before, {after:.1f} dB after a 2 kHz cap"


def test_another_language_is_refused(engine):
    r = engine.synthesize("Bonjour", VoiceParameters(language="fr-FR"))
    assert not r.success and "fr-FR" in r.error


def test_blocked_text_is_refused_before_any_sound(engine):
    r = engine.synthesize("kill yourself")
    assert not r.success and r.audio_data is None and "voice_validator" in r.error


def test_word_not_in_lexicon_is_refused_not_spelled(engine):
    r = engine.synthesize("I love zqxwvy")
    assert not r.success and "zqxwvy" in r.error


def test_phoneme_labels_come_from_the_sdk(engine, tmp_path):
    out = tmp_path / "labels.wav"
    assert engine.synthesize("I love you", output_path=str(out)).success
    labels = engine.label_phonemes(str(out), "I love you")
    assert labels, "the SDK labeler returned no phonemes"
    assert type(labels[0]).__module__ == "christman_voice_sdk.synthesis.phoneme_labeler"


def test_status_names_the_voice_and_every_control(engine):
    s = engine.get_engine_status()
    assert s["ready"] and s["voice"] == engine.voice_name
    assert set(s["controls"]) == set(SYNTH_INPUT_FOR)
    for name, c in s["controls"].items():
        assert c["applied"] == engine._synth_accepts(SYNTH_INPUT_FOR[name])
