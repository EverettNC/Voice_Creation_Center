"""
Smoke test for the Phase 1 live studio.

Proves the core workflow end to end with a REAL wav file:
    create session -> register raw take -> validate -> approve -> export

Export targets a temp dir (NEVER the real /Users/EverettN path) and the
express-index registration is pointed at a temp index file so this test
never mutates the Center's real express_index.json.

Run directly:   python3 studio/test_live_studio.py
Or with pytest:  pytest studio/test_live_studio.py
"""

import sys
import wave
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import live_studio as ls
import voice_path_registrar


def _write_silent_wav(path: Path, seconds: float = 0.25, rate: int = 22050) -> None:
    """Write a tiny real mono wav (stdlib only — no external deps)."""
    n_frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n_frames)


def test_create_attach_approve_export(tmp_path):
    raw_take = tmp_path / "raw_take.wav"
    _write_silent_wav(raw_take)

    export_dir = tmp_path / "alphavox_cycled"
    studio = ls.LiveStudio(export_dir=export_dir)

    # create
    session = studio.create_session("I love you", speaker="Everett")
    assert session.state == ls.SessionState.CREATED
    assert session.exported_to is None

    # register raw take
    studio.register_raw_take(session.id, str(raw_take))
    assert session.state == ls.SessionState.TAKE_REGISTERED
    assert Path(session.raw_path) == raw_take.resolve()

    # validate
    studio.mark_validation(session.id, transcript="I love you")
    assert session.validation_status == ls.ValidationStatus.PASSED

    # approve
    studio.approve(session.id)
    assert session.approved_by_everett is True
    assert session.state == ls.SessionState.APPROVED

    # export (register=False keeps it off the real index)
    studio.export_approved(session.id, register=False)
    assert session.state == ls.SessionState.EXPORTED
    exported = Path(session.exported_to)
    assert exported.exists(), "exported wav must actually exist on disk"
    assert exported.parent == export_dir.resolve()

    # the raw take must NOT have moved — raw stays in the Center
    assert raw_take.exists(), "raw take must stay where it was"


def test_export_registers_into_index(tmp_path, monkeypatch):
    """Export with register=True writes into the (temp) express index."""
    raw_take = tmp_path / "raw.wav"
    _write_silent_wav(raw_take)

    temp_index = tmp_path / "express_index.json"
    monkeypatch.setattr(voice_path_registrar, "EXPRESS_INDEX", temp_index)
    monkeypatch.setattr(voice_path_registrar, "EXPRESS_ROOT", tmp_path)

    studio = ls.LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("You are safe", speaker="Everett")
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="You are safe")
    studio.approve(session.id)
    studio.export_approved(session.id, register=True)

    import json
    data = json.loads(temp_index.read_text())
    texts = [p["text"] for p in data.get("phrases", [])]
    assert "You are safe" in texts


def test_cannot_approve_without_passing_validation(tmp_path):
    raw_take = tmp_path / "r.wav"
    _write_silent_wav(raw_take)
    studio = ls.LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("hi", speaker="Everett")
    studio.register_raw_take(session.id, str(raw_take))
    studio.mark_validation(session.id, transcript="kill yourself")  # blocked by validator
    assert session.validation_status == ls.ValidationStatus.FAILED
    try:
        studio.approve(session.id)
        assert False, "approve should have raised"
    except ValueError:
        pass


def test_mic_capture_is_honest_stub(tmp_path):
    studio = ls.LiveStudio(export_dir=tmp_path / "out")
    session = studio.create_session("hello", speaker="Everett")
    try:
        studio.capture_take(session.id)
        assert False, "capture_take must not fake a recording"
    except NotImplementedError:
        pass


def _run_all():
    """Tiny runner so the file works without pytest installed."""
    import tempfile
    import contextlib

    class _MP:
        def __init__(self): self._saved = []
        def setattr(self, obj, name, val):
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._saved):
                setattr(obj, name, val)

    passed = 0
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
                print(f"PASS  {name}")
                passed += 1
            finally:
                mp.undo()
    print(f"\n{passed} test(s) passed.")
    return passed


if __name__ == "__main__":
    _run_all()
