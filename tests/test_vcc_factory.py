"""
Tests for vcc_factory.py — real runs against the real synthesizer.

Every test renders with the installed Christman-Sound (christman_synth with
AlphaVox's measured voice profile), reads Everett's Vosk lexicon, and reads
AlphaVox's own recordings. Nothing is mocked. When one of those is not on
this machine, the tests report NOT RUN with the reason.

    CHRISTMAN_LEXICON_PATH=~/models/vosk-model-en-us-0.22/graph/phones/align_lexicon.txt \\
    VCC_ALPHAVOX_RECORDINGS=~/AlphaVox/data/phrases/alphavox_verified \\
    ~/claude-work/vcc-factory-venv/bin/python -m pytest tests/ -ra

Each test works in its own temporary express folder. The recordings are only
ever read; test_recordings_never_written checks that byte for byte.
"""

import array
import hashlib
import json
import math
import os
import socket
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "engines")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vcc_factory as vf  # noqa: E402
import voice_express as vx  # noqa: E402

BEING = "AlphaVox"
PRELOAD = vx.VoiceExpress.DEFAULT_PHRASES[BEING]


# ── what the tests need on this machine ──────────────────────────────────────

def _missing() -> list:
    missing = []
    lex = os.environ.get("CHRISTMAN_LEXICON_PATH")
    if not lex or not Path(lex).expanduser().is_file():
        missing.append(f"CHRISTMAN_LEXICON_PATH is not a lexicon file ({lex!r})")
    rec = os.environ.get("VCC_ALPHAVOX_RECORDINGS")
    if not rec or not Path(rec).expanduser().is_dir():
        missing.append(f"VCC_ALPHAVOX_RECORDINGS is not AlphaVox's recordings folder ({rec!r})")
    try:
        from christman_sound.dsp import get_dsp
        get_dsp()
    except Exception as exc:  # reported as the NOT RUN reason
        missing.append(f"christman_synth not loadable: {type(exc).__name__}: {exc}")
    try:
        vf.load_being_voice(BEING)
    except Exception as exc:  # reported as the NOT RUN reason
        missing.append(f"AlphaVox's voice not loadable: {exc}")
    return missing


@pytest.fixture(scope="session")
def env():
    missing = _missing()
    if missing:
        pytest.skip("NOT RUN — " + "; ".join(missing))
    return {
        "lexicon": str(Path(os.environ["CHRISTMAN_LEXICON_PATH"]).expanduser()),
        "recordings": Path(os.environ["VCC_ALPHAVOX_RECORDINGS"]).expanduser().resolve(),
    }


@pytest.fixture
def root(tmp_path):
    return tmp_path / "express"


@pytest.fixture
def make_factory(env, root):
    made = []

    def _make(express_root=None):
        f = vf.Factory(BEING, env["lexicon"], str(env["recordings"]),
                       express_root=str(express_root or root), poll_seconds=0.05)
        f.start()
        made.append(f)
        return f

    yield _make
    for f in made:
        f.close()


def _alphavox_side(root):
    """A second VoiceExpress on the same folder: AlphaVox's side of the cache."""
    e = vx.VoiceExpress(root=root)
    assert e.load()
    return e


def _recorded_texts(env):
    f = vf.Factory.__new__(vf.Factory)
    texts = set()
    for wav in env["recordings"].glob("*.wav"):
        stem = wav.stem[len("alphavox_"):] if wav.stem.startswith("alphavox_") else wav.stem
        texts.add(stem.replace("_", " ").lower())
    return texts


def _fingerprint(folder: Path) -> dict:
    out = {}
    for p in sorted(folder.iterdir()):
        st = p.stat()
        out[p.name] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
    return out


# ── render, validate, register ───────────────────────────────────────────────

def test_render_validate_register(make_factory):
    f = make_factory()
    o = f.produce("I need help", origin="test")
    assert o.status == "rendered", o.error
    path = Path(o.audio_path)
    assert path.parent == f.cache_dir and path.exists()

    checks = vf.validate_wav(path)
    assert all(c["passed"] for c in checks.values()), checks
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == vf.EXPECTED_SAMPLE_RATE
        assert w.getnchannels() == 1 and w.getsampwidth() == 2

    entry = f.express.get_phrase("I need help", BEING)
    assert entry.source == vx.RENDERED_SOURCE
    assert entry.voice == "alphavox"
    assert entry.engine == "christman"
    assert entry.is_rendered and entry.validation["sample_rate"]["passed"]
    assert entry.sample_rate == vf.EXPECTED_SAMPLE_RATE
    on_disk = json.loads(f.express.index_path.read_text())["phrases"]
    assert any(p["phrase_id"] == entry.phrase_id and p["audio_path"] == str(path) for p in on_disk)


def test_serve_from_cache(make_factory, root):
    f = make_factory()
    o = f.produce("I am hungry", origin="test")
    assert o.status == "rendered", o.error

    served = _alphavox_side(root).serve("I am hungry", being_name=BEING)
    assert served.success and served.from_cache, served.error
    assert served.audio_data == Path(o.audio_path).read_bytes()
    assert served.audio_data[:4] == b"RIFF"


# ── recordings ────────────────────────────────────────────────────────────────

def test_recording_outranks_render(make_factory, env):
    f = make_factory()
    o = f.produce("Thank you", origin="test")
    assert o.status == "recording", o.error
    assert Path(o.audio_path) == env["recordings"] / "alphavox_thank_you.wav"

    # A render of "Yes" registered earlier is replaced by AlphaVox's recording.
    yes_path = f.cache_dir / "earlier_yes.wav"
    result, checks = f.render("Yes", yes_path)
    f.express.register_phrase(
        text="Yes", being_name=BEING, language="en-US", audio_path=str(yes_path),
        duration_seconds=result.duration, sample_rate=result.sample_rate,
        source=vx.RENDERED_SOURCE, engine=result.engine, voice=result.voice, validation=checks,
    )
    assert f.express.get_phrase("Yes", BEING).is_rendered
    f.run(once=True)
    entry = f.express.get_phrase("Yes", BEING)
    assert not entry.is_rendered
    assert Path(entry.audio_path) == env["recordings"] / "alphavox_yes.wav"


def test_recording_facts_read_from_float_wav():
    # Needs only the recordings, not the synthesizer.
    rec = os.environ.get("VCC_ALPHAVOX_RECORDINGS")
    if not rec or not Path(rec).expanduser().is_dir():
        pytest.skip(f"NOT RUN — VCC_ALPHAVOX_RECORDINGS is not AlphaVox's recordings folder ({rec!r})")
    # AlphaVox's recordings are 32-bit float WAV, which the stdlib wave module cannot open.
    dur, rate = vf._wav_facts(Path(rec).expanduser() / "alphavox_thank_you.wav")
    assert rate == 24000 and 0.3 < dur < 1.0


def test_render_never_replaces_a_recording_entry(make_factory, env):
    f = make_factory()
    assert f.produce("I love you", origin="test").status == "recording"
    render_path = f.cache_dir / "would_be_love.wav"
    result, checks = f.render("I love you", render_path)
    with pytest.raises(vx.RecordingInPlace):
        f._register_render("I love you", render_path, result.duration, result.sample_rate, checks)
    entry = f.express.get_phrase("I love you", BEING)
    assert not entry.is_rendered
    assert Path(entry.audio_path) == env["recordings"] / "alphavox_i_love_you.wav"


def test_other_pack_id_is_refused(make_factory, root):
    req = _alphavox_side(root).priority_synthesize(
        "I am tired", BEING, "some_other_pack", priority=vx.Priority.HIGH)
    f = make_factory()
    assert f.work_once()
    failed = root / "priority_queue" / "failed" / f"{req.request_id}.json"
    assert "pack_id" in json.loads(failed.read_text())["error"]
    assert not f.express.is_cached("I am tired", BEING)


def test_recordings_never_written(make_factory, env, root):
    before = _fingerprint(env["recordings"])
    assert len(before) == 26
    av = _alphavox_side(root)
    for text in ("I love you", "No", "I am scared"):  # recorded phrases, queued as well
        av.priority_synthesize(text, BEING, pack_id="alphavox_default_en_us",
                               priority=vx.Priority.CRITICAL)
    f = make_factory()
    f.run(once=True)
    assert _fingerprint(env["recordings"]) == before


# ── the priority queue ────────────────────────────────────────────────────────

def test_queue_critical_first(make_factory, root):
    av = _alphavox_side(root)
    av.priority_synthesize("I am tired", BEING, "alphavox_default_en_us", priority=vx.Priority.STANDARD)
    av.priority_synthesize("I need water", BEING, "alphavox_default_en_us", priority=vx.Priority.HIGH)
    av.priority_synthesize("Please stop", BEING, "alphavox_default_en_us", priority=vx.Priority.CRITICAL)
    av.priority_synthesize("I am in pain", BEING, "alphavox_default_en_us", priority=vx.Priority.CRITICAL)
    assert len(list((root / "priority_queue").glob("*.json"))) == 4

    f = make_factory()
    for _ in range(4):
        assert f.work_once()
    origins = [o.origin for o in f.outcomes]
    assert origins == ["queue:critical", "queue:critical", "queue:high", "queue:standard"], origins
    assert [o.text for o in f.outcomes[:2]] == ["Please stop", "I am in pain"]  # oldest first within a level
    assert all(o.status == "rendered" for o in f.outcomes), [o.error for o in f.outcomes]
    assert not list((root / "priority_queue").glob("*.json"))
    done = list((root / "priority_queue" / "done").glob("*.json"))
    assert len(done) == 4
    assert all(json.loads(p.read_text())["phrase_id"] for p in done)


def test_picks_up_what_alphavox_queues_while_running(make_factory, root):
    f = make_factory()
    t = threading.Thread(target=f.run, daemon=True)
    t.start()
    try:
        time.sleep(0.5)
        req = _alphavox_side(root).priority_synthesize(
            "I want to go to the park", BEING, "alphavox_default_en_us", priority=vx.Priority.HIGH)
        done = root / "priority_queue" / "done" / f"{req.request_id}.json"
        deadline = time.time() + 120
        while not done.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert done.exists(), "the running factory did not pick up the queued phrase"
    finally:
        f.stop()
        t.join(timeout=60)
    assert not t.is_alive()
    served = _alphavox_side(root).serve("I want to go to the park", being_name=BEING)
    assert served.success and served.from_cache, served.error


def test_restart_resumes_and_loses_nothing(make_factory, root, env):
    av = _alphavox_side(root)
    for text in ("I am hungry", "I am tired", "I need water"):
        av.priority_synthesize(text, BEING, "alphavox_default_en_us", priority=vx.Priority.HIGH)

    f1 = make_factory()
    assert f1.work_once()
    first = f1.outcomes[0]
    assert first.status == "rendered", first.error
    first_mtime = Path(first.audio_path).stat().st_mtime_ns
    lost = f1.express.get_next_priority_request(being_name=BEING)  # handed out, then a "crash"
    assert lost is not None
    f1.close()

    f2 = make_factory()
    f2.run(once=True)
    rendered = {o.text for o in f2.outcomes if o.status == "rendered"}
    assert lost.text in rendered                      # the in-flight request came back
    assert first.text not in rendered                 # done work is not done twice
    assert Path(first.audio_path).stat().st_mtime_ns == first_mtime
    assert not list((root / "priority_queue").glob("*.json"))

    recorded = _recorded_texts(env)
    for text in PRELOAD:
        entry = f2.express.get_phrase(text, BEING)
        assert entry is not None and Path(entry.audio_path).exists(), text
        assert entry.is_rendered == (text.lower() not in recorded), text


# ── refusals ──────────────────────────────────────────────────────────────────

def test_word_not_in_lexicon_is_refused(make_factory, root):
    req = _alphavox_side(root).priority_synthesize(
        "I need a zzqxvv", BEING, "alphavox_default_en_us", priority=vx.Priority.HIGH)
    f = make_factory()
    assert f.work_once()
    failed = root / "priority_queue" / "failed" / f"{req.request_id}.json"
    assert failed.exists()
    assert "WordNotInLexicon" in json.loads(failed.read_text())["error"]
    assert not f.express.is_cached("I need a zzqxvv", BEING)
    assert not list(f.cache_dir.glob("*.wav"))


def test_blocked_text_is_refused(make_factory):
    f = make_factory()
    o = f.produce("nobody loves you", origin="test")
    assert o.status == "failed" and "dignity" in o.error
    assert not f.express.is_cached("nobody loves you", BEING)


def _write_wav(path, samples, rate):
    pcm = array.array("h", (int(s * 32767) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def test_validation_refuses_silence_and_wrong_rate(tmp_path):
    silent = tmp_path / "silent.wav"
    _write_wav(silent, [0.0] * 22050, 22050)
    checks = vf.validate_wav(silent)
    assert not checks["peak"]["passed"] and not checks["voiced_fraction"]["passed"]

    wrong_rate = tmp_path / "wrong_rate.wav"
    _write_wav(wrong_rate, [0.5 * math.sin(2 * math.pi * 200 * i / 16000) for i in range(16000)], 16000)
    checks = vf.validate_wav(wrong_rate)
    assert not checks["sample_rate"]["passed"]
    assert checks["peak"]["passed"]


# ── whole preload, no network ─────────────────────────────────────────────────

def test_full_preload_with_network_refused(make_factory, env, monkeypatch):
    def _refuse(*a, **k):
        raise AssertionError("the factory tried to open a network socket")
    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)

    f = make_factory()
    f.run(once=True)
    assert not [o for o in f.outcomes if o.status == "failed"], [o.error for o in f.outcomes]
    recorded = _recorded_texts(env)
    statuses = {o.text: o.status for o in f.outcomes}
    for text in PRELOAD:
        assert statuses[text] == ("recording" if text.lower() in recorded else "rendered"), text
