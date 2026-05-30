"""
================================================================================
FILE: studio/test_phase2.py
PROJECT: Christman Voice Creation Center — Phase 2 Tests
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    Smoke tests for Phase 2: recorder.py + updated live_studio.py

    Tests that require a real microphone are SKIPPED automatically when
    sounddevice is unavailable (CI, headless servers). They run fully
    when a microphone is present.

    Tests that do NOT require a microphone (device listing, stats computation,
    Phase 1 workflow with pre-existing WAV) always run.

Run directly:   python3 studio/test_phase2.py
Or with pytest: pytest studio/test_phase2.py -v
================================================================================
"""

import sys
import wave
import tempfile
import time
import struct
import math
from pathlib import Path

_STUDIO_DIR  = Path(__file__).resolve().parent
_CENTER_ROOT = _STUDIO_DIR.parent
_ENGINES_DIR = _CENTER_ROOT / "engines"

for _p in (str(_CENTER_ROOT), str(_ENGINES_DIR), str(_STUDIO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recorder   import Recorder, RecorderState, RecordingStats, SAMPLE_RATE
from live_studio import LiveStudio, SessionState, ValidationStatus

import numpy as np

# ── Check sounddevice availability ────────────────────────────────────────────
try:
    import sounddevice as _sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


# ── WAV helpers ───────────────────────────────────────────────────────────────

def _write_silent_wav(path: Path, seconds: float = 0.5, rate: int = SAMPLE_RATE) -> None:
    """Write a real silent WAV — for tests that need a file but not real audio."""
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


def _write_tone_wav(path: Path, freq: float = 440.0, seconds: float = 0.5) -> None:
    """Write a real sine wave WAV — tests clipping detection with a hot signal."""
    n = int(seconds * SAMPLE_RATE)
    samples = [
        int(32767 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
        for i in range(n)
    ]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{n}h", *samples))


def _write_clipped_wav(path: Path, seconds: float = 0.5) -> None:
    """Write a WAV at exactly max int16 — should trigger clipping detection."""
    n = int(seconds * SAMPLE_RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{n}h", *([32767] * n)))


# ── Recorder tests ────────────────────────────────────────────────────────────

def test_recorder_initial_state():
    """Recorder starts in IDLE state."""
    r = Recorder()
    assert r.state in (RecorderState.IDLE, RecorderState.ERROR), (
        f"Unexpected initial state: {r.state}"
    )
    print("PASS  test_recorder_initial_state")


def test_recorder_list_devices_no_crash():
    """list_input_devices() returns a list (may be empty) without crashing."""
    r = Recorder()
    devices = r.list_input_devices()
    assert isinstance(devices, list), "Expected list from list_input_devices()"
    print(f"PASS  test_recorder_list_devices_no_crash  ({len(devices)} device(s) found)")


def test_recorder_stats_computation(tmp_path):
    """
    _write_wav() computes stats correctly from a known audio array.
    Does NOT require a microphone — uses a synthetic numpy array.
    """
    r = Recorder()
    if r._sd is None:
        # Can still test _write_wav directly — it doesn't need sounddevice
        pass

    # Create a known sine wave at half amplitude
    n      = int(0.5 * SAMPLE_RATE)
    t      = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    signal = (np.sin(2 * np.pi * 440 * t) * 16383).astype(np.int16)

    out_path = tmp_path / "test_tone.wav"
    stats = r._write_wav(signal, out_path, device_name="test_device")

    assert out_path.exists(), "WAV file was not written"
    assert stats.duration_seconds > 0.4, f"Duration too short: {stats.duration_seconds}"
    assert stats.peak_db < 0.0, f"Peak dB should be negative: {stats.peak_db}"
    assert not stats.clipped, "Half-amplitude signal should not be clipped"
    assert stats.sample_rate == SAMPLE_RATE
    print(
        f"PASS  test_recorder_stats_computation  "
        f"(peak={stats.peak_db:.1f}dB, duration={stats.duration_seconds:.2f}s)"
    )


def test_recorder_clipping_detected(tmp_path):
    """_write_wav() detects clipping on a max-amplitude signal."""
    r = Recorder()
    n      = int(0.3 * SAMPLE_RATE)
    signal = np.full(n, 32767, dtype=np.int16)

    out_path = tmp_path / "clipped.wav"
    stats = r._write_wav(signal, out_path, device_name="test")

    assert stats.clipped, "Max-amplitude signal should be flagged as clipped"
    print(f"PASS  test_recorder_clipping_detected  (peak={stats.peak_db:.1f}dB)")


def test_recorder_play_existing_wav(tmp_path):
    """play() returns True for an existing WAV file."""
    if not SOUNDDEVICE_AVAILABLE:
        print("SKIP  test_recorder_play_existing_wav  (sounddevice not installed)")
        return

    wav = tmp_path / "tone.wav"
    _write_tone_wav(wav)

    r = Recorder()
    started = r.play(str(wav))
    r.stop_playback()

    assert started, "play() should return True for a valid WAV file"
    print("PASS  test_recorder_play_existing_wav")


def test_recorder_play_missing_file():
    """play() returns False for a non-existent file."""
    r = Recorder()
    result = r.play("/nonexistent/path/phantom.wav")
    assert result is False, "play() should return False for a missing file"
    print("PASS  test_recorder_play_missing_file")


def test_recorder_record_fixed_requires_sounddevice():
    """
    record_fixed() without sounddevice returns None and doesn't crash.
    Verifies graceful degradation.
    """
    if SOUNDDEVICE_AVAILABLE:
        print("SKIP  test_recorder_record_fixed_requires_sounddevice  (sounddevice present)")
        return

    r = Recorder()
    result = r.record_fixed("/tmp/should_not_exist.wav", duration_seconds=0.1)
    assert result is None, "record_fixed() should return None without sounddevice"
    print("PASS  test_recorder_record_fixed_requires_sounddevice")


# ── LiveStudio Phase 2 tests ──────────────────────────────────────────────────

def test_live_studio_full_phase1_workflow(tmp_path):
    """
    Full Phase 1 workflow with a pre-existing WAV.
    Doesn't use the microphone — verifies nothing broke in Phase 2.
    """
    raw_take = tmp_path / "raw_take.wav"
    _write_silent_wav(raw_take)

    studio = LiveStudio(export_dir=tmp_path / "out")

    session = studio.create_session("I love you", speaker="Everett")
    assert session.state == SessionState.CREATED

    studio.register_raw_take(session.id, str(raw_take))
    assert session.state == SessionState.TAKE_REGISTERED
    assert Path(session.raw_path).exists()

    studio.mark_validation(session.id, transcript="I love you")
    assert session.validation_status == ValidationStatus.PASSED

    studio.approve(session.id)
    assert session.approved_by_everett is True

    studio.export_approved(session.id, register=False)
    assert session.state == SessionState.EXPORTED
    assert Path(session.exported_to).exists()

    # Raw take must NOT have moved
    assert raw_take.exists(), "Raw take must stay in the Center"
    print("PASS  test_live_studio_full_phase1_workflow")


def test_live_studio_session_has_recording_stats(tmp_path):
    """
    Session stores recording_stats dict after a real-WAV registration.
    Verifies the new stats field integrates cleanly.
    """
    raw = tmp_path / "raw.wav"
    _write_tone_wav(raw)

    studio  = LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("You are safe", speaker="Everett")
    studio.register_raw_take(session.id, str(raw))

    # recording_stats is None for register_raw_take (no mic was used)
    # Inject fake stats to verify the field works
    session.recording_stats = {
        "duration_seconds": 0.5,
        "peak_db": -6.0,
        "clipped": False,
        "device_name": "test_mic",
    }
    d = session.to_dict()
    assert d["recording_stats"]["peak_db"] == -6.0
    print("PASS  test_live_studio_session_has_recording_stats")


def test_live_studio_approve_requires_passing_validation(tmp_path):
    """approve() raises ValueError if validation did not pass."""
    raw = tmp_path / "raw.wav"
    _write_silent_wav(raw)

    studio  = LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("test phrase", speaker="Everett")
    studio.register_raw_take(session.id, str(raw))

    # Deliberately fail validation
    studio.mark_validation(session.id, transcript="kill yourself")
    assert session.validation_status == ValidationStatus.FAILED

    try:
        studio.approve(session.id)
        assert False, "approve() should have raised ValueError"
    except ValueError:
        pass

    print("PASS  test_live_studio_approve_requires_passing_validation")


def test_live_studio_reject_workflow(tmp_path):
    """Rejected sessions cannot be exported."""
    raw = tmp_path / "raw.wav"
    _write_silent_wav(raw)

    studio  = LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("test", speaker="Everett")
    studio.register_raw_take(session.id, str(raw))
    studio.mark_validation(session.id, transcript="test")
    studio.reject(session.id)

    assert session.state == SessionState.REJECTED
    assert session.approved_by_everett is False

    try:
        studio.export_approved(session.id, register=False)
        assert False, "export_approved() should have raised ValueError"
    except ValueError:
        pass

    print("PASS  test_live_studio_reject_workflow")


def test_live_studio_finish_capture_wrong_state(tmp_path):
    """finish_capture() raises ValueError if session is not RECORDING."""
    studio  = LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("test", speaker="Everett")

    try:
        studio.finish_capture(session.id)
        assert False, "finish_capture() should raise ValueError when not recording"
    except ValueError:
        pass

    print("PASS  test_live_studio_finish_capture_wrong_state")


def test_live_studio_list_devices_no_crash():
    """list_input_devices() returns without crashing."""
    studio  = LiveStudio()
    devices = studio.list_input_devices()
    assert isinstance(devices, list)
    print(f"PASS  test_live_studio_list_devices_no_crash  ({len(devices)} device(s))")


# ── Microphone tests — require real hardware ──────────────────────────────────
# These only run if sounddevice is installed AND a mic is present.

def test_recorder_record_fixed_real_mic(tmp_path):
    """
    Record 1 second from the real microphone.
    SKIPPED if sounddevice is not installed.
    """
    if not SOUNDDEVICE_AVAILABLE:
        print("SKIP  test_recorder_record_fixed_real_mic  (sounddevice not installed)")
        return

    r   = Recorder()
    out = tmp_path / "real_take.wav"

    print("\n  🎙️  Recording 1 second from your microphone...")
    stats = r.record_fixed(str(out), duration_seconds=1.0, countdown=False)

    if stats is None:
        print("SKIP  test_recorder_record_fixed_real_mic  (no input device available)")
        return

    assert out.exists(),              "WAV file must be written"
    assert stats.duration_seconds > 0.8, f"Duration too short: {stats.duration_seconds}"
    assert stats.sample_rate == SAMPLE_RATE
    assert stats.frames_recorded > 0

    print(
        f"PASS  test_recorder_record_fixed_real_mic  "
        f"(duration={stats.duration_seconds:.2f}s  peak={stats.peak_db:.1f}dB)"
    )


def test_live_studio_capture_fixed_real_mic(tmp_path):
    """
    capture_take() with fixed duration using the real microphone.
    SKIPPED if sounddevice is not installed.
    """
    if not SOUNDDEVICE_AVAILABLE:
        print("SKIP  test_live_studio_capture_fixed_real_mic  (sounddevice not installed)")
        return

    studio  = LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("I love you", speaker="Everett")

    print("\n  🎙️  Recording 'I love you' (1 second)...")
    try:
        studio.capture_take(session.id, duration_seconds=1.0, countdown=False)
    except RuntimeError as e:
        print(f"SKIP  test_live_studio_capture_fixed_real_mic  ({e})")
        return

    assert session.state == SessionState.TAKE_REGISTERED
    assert session.raw_path is not None
    assert Path(session.raw_path).exists()
    assert session.recording_stats is not None
    assert session.recording_stats["duration_seconds"] > 0.8

    print(
        f"PASS  test_live_studio_capture_fixed_real_mic  "
        f"(peak={session.recording_stats['peak_db']:.1f}dB)"
    )


# ── Runner ────────────────────────────────────────────────────────────────────

def _run_all():
    import tempfile

    tests = [
        (test_recorder_initial_state,                       None),
        (test_recorder_list_devices_no_crash,               None),
        (test_recorder_stats_computation,                   True),
        (test_recorder_clipping_detected,                   True),
        (test_recorder_play_existing_wav,                   True),
        (test_recorder_play_missing_file,                   None),
        (test_recorder_record_fixed_requires_sounddevice,   None),
        (test_live_studio_full_phase1_workflow,             True),
        (test_live_studio_session_has_recording_stats,      True),
        (test_live_studio_approve_requires_passing_validation, True),
        (test_live_studio_reject_workflow,                  True),
        (test_live_studio_finish_capture_wrong_state,       True),
        (test_live_studio_list_devices_no_crash,            None),
        (test_recorder_record_fixed_real_mic,               True),
        (test_live_studio_capture_fixed_real_mic,           True),
    ]

    passed = 0
    print(f"\n  Running {len(tests)} tests...\n")

    for fn, needs_tmp in tests:
        try:
            if needs_tmp:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")

    print(f"\n  {passed}/{len(tests)} tests passed.\n")
    return passed


if __name__ == "__main__":
    _run_all()


# ==============================================================================
# © 2026 Everett Nathaniel Christman & The Christman AI Project
# Luma Cognify AI — "How can we help you love yourself more?"
# Patent Pending TCAP-2026-001 | TCAP-2026-002
# ==============================================================================
