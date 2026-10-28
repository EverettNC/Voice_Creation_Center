"""
Tests for the live studio workflow.

Proves the workflow end to end with REAL wav files:
    create session -> register raw take -> listen + validate -> approve -> export

Export targets a temp dir and the express-index registration is pointed at a
temp index file, so these tests never touch a real export folder or the
Center's real express_index.json. No test opens a microphone or plays sound.

Run directly:   python3 studio/test_live_studio.py
Or with pytest:  pytest studio/test_live_studio.py

A test that cannot run is reported as NOT RUN with its reason, never as a pass.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "engines")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import live_studio as ls
from signals_for_tests import write_noise_wav, write_silent_wav, write_voiced_wav

SPEAKER = "studio test"
BEING = "being under test"


def test_create_attach_approve_export(tmp_path):
    raw_take = write_voiced_wav(tmp_path / "raw_take.wav")

    export_dir = tmp_path / "approved_exports"
    studio = ls.LiveStudio(export_dir=export_dir, base_dir=tmp_path)

    # create
    session = studio.create_session("I love you", speaker=SPEAKER, being=BEING)
    assert session.state == ls.SessionState.CREATED
    assert session.exported_to is None

    # register raw take
    studio.register_raw_take(session.id, str(raw_take))
    assert session.state == ls.SessionState.TAKE_REGISTERED
    assert Path(session.raw_path) == raw_take.resolve()

    # validate: the take is listened to, and it holds voiced sound
    studio.mark_validation(session.id, transcript="I love you")
    assert session.validation_status == ls.ValidationStatus.PASSED, session.validation_reasons
    assert session.take_report["voiced_seconds"] > 0.5

    # approve
    studio.approve(session.id)
    assert session.approved is True
    assert session.state == ls.SessionState.APPROVED

    # export (register=False keeps it off the real index)
    studio.export_approved(session.id, register=False)
    assert session.state == ls.SessionState.EXPORTED
    exported = Path(session.exported_to)
    assert exported.exists(), "exported wav must actually exist on disk"
    assert exported.parent == export_dir.resolve()

    # the raw take must NOT have moved — raw stays in the Center
    assert raw_take.exists(), "raw take must stay where it was"


def test_silent_take_is_refused(tmp_path):
    """A WAV of pure silence must never validate, approve or export, whatever the transcript says."""
    raw_take = write_silent_wav(tmp_path / "silence.wav", seconds=1.0)
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    session = studio.create_session("I love you", speaker=SPEAKER, being=BEING)
    studio.register_raw_take(session.id, str(raw_take))

    studio.mark_validation(session.id, transcript="I love you", passed=True)
    assert session.validation_status == ls.ValidationStatus.FAILED
    assert any("silence" in r for r in session.validation_reasons), session.validation_reasons
    assert session.take_report["voiced_seconds"] == 0.0

    try:
        studio.approve(session.id)
        assert False, "approve must refuse a silent take"
    except ValueError:
        pass
    try:
        studio.export_approved(session.id, register=False)
        assert False, "export must refuse a silent take"
    except ValueError:
        pass
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_unvoiced_noise_take_is_refused(tmp_path):
    """Loud noise with no voice in it is refused too: loudness alone is not speech."""
    raw_take = write_noise_wav(tmp_path / "noise.wav")
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    session = studio.create_session("Help is coming", speaker=SPEAKER, being=BEING)
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="Help is coming")
    assert session.validation_status == ls.ValidationStatus.FAILED
    assert any("voiced sound" in r for r in session.validation_reasons), session.validation_reasons


def test_too_short_take_is_refused(tmp_path):
    raw_take = write_voiced_wav(tmp_path / "short.wav", seconds=0.2)
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    session = studio.create_session("Yes", speaker=SPEAKER, being=BEING)
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="Yes")
    assert session.validation_status == ls.ValidationStatus.FAILED
    assert any("under" in r for r in session.validation_reasons), session.validation_reasons


def test_export_registers_into_index(tmp_path):
    """Export with register=True writes into the express index under the studio's base_dir (a temp folder)."""
    raw_take = write_voiced_wav(tmp_path / "raw.wav")

    temp_index = tmp_path / "express" / "express_index.json"

    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    session = studio.create_session("You are safe", speaker=SPEAKER, being=BEING)
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="You are safe")
    studio.approve(session.id)
    studio.export_approved(session.id, register=True)

    import json
    data = json.loads(temp_index.read_text())
    texts = [p["text"] for p in data.get("phrases", [])]
    assert "You are safe" in texts


def test_cannot_approve_without_passing_validation(tmp_path):
    raw_take = write_voiced_wav(tmp_path / "r.wav")
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    session = studio.create_session("hi", speaker=SPEAKER, being=BEING)
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="kill yourself")  # blocked by validator
    assert session.validation_status == ls.ValidationStatus.FAILED
    try:
        studio.approve(session.id)
        assert False, "approve should have raised"
    except ValueError:
        pass


def test_capture_without_an_input_device_refuses_and_records_nothing(tmp_path):
    """
    Phase 2 made capture_take real: it records through studio/recorder.py.
    With no audio input available it must refuse loudly (RuntimeError),
    put the session back to CREATED, and leave no take behind. The recorder's
    audio library is taken away for this test, so no microphone can open
    even on a machine that has one.
    """
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    studio._recorder._sd = None
    session = studio.create_session("hello", speaker=SPEAKER, being=BEING)
    for duration in (None, 1.0):
        try:
            studio.capture_take(session.id, duration_seconds=duration, countdown=False)
            assert False, "capture_take must not fake a recording"
        except RuntimeError as e:
            assert "microphone" in str(e)
        assert session.state == ls.SessionState.CREATED
        assert session.raw_path is None


def test_there_is_no_default_being_or_export_folder(tmp_path):
    """The VCC names no being: every studio is handed its export folder, every take its being."""
    try:
        ls.LiveStudio(export_dir=None, base_dir=tmp_path)
        assert False, "a studio without an export folder must be refused"
    except ValueError:
        pass
    studio = ls.LiveStudio(export_dir=tmp_path / "out", base_dir=tmp_path)
    try:
        studio.create_session("hello", speaker=SPEAKER)
        assert False, "a take without its being must be refused"
    except ValueError:
        pass


# ── Standalone runner ────────────────────────────────────────────────────────

def _run_all():
    """Runs every test without pytest. A skipped test is NOT RUN, never a pass."""
    import tempfile

    try:
        import pytest
        skip_exc = pytest.skip.Exception
    except ImportError:  # no test here calls skip, but a pytest-less run must still load
        skip_exc = ()

    class _MP:
        def __init__(self): self._saved = []
        def setattr(self, obj, name, val):
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._saved):
                setattr(obj, name, val)

    passed, failed, not_run = 0, 0, 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            mp = _MP()
            try:
                if "monkeypatch" in fn.__code__.co_varnames:
                    fn(tmp, mp)
                else:
                    fn(tmp)
                print(f"PASS     {name}")
                passed += 1
            except skip_exc as e:
                print(f"NOT RUN  {name}: {e}")
                not_run += 1
            except Exception as e:
                print(f"FAIL     {name}: {type(e).__name__}: {e}")
                failed += 1
            finally:
                mp.undo()
    print(f"\n{passed} passed, {failed} failed, {not_run} not run.")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
