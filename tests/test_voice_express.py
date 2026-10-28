"""
Tests for the express lane: the index, its one writer, the priority queue
and its consumer, the registrar and the ingestor.

Tests that render need a voice handed in from outside the VCC
(VCC_TEST_VOICE_PROFILE and CHRISTMAN_LEXICON_PATH); without it they are
NOT RUN with that reason. Nothing here plays sound.
"""

import json
import os
from pathlib import Path

import pytest

import take_listener
import voice_express
import voice_path_registrar
from signals_for_tests import write_phrases_with_gaps_wav, write_voiced_wav
from voice_express import Priority, UnmeasurableAudio, VoiceExpress

ROOT = Path(__file__).resolve().parent.parent
BEING = "being under test"


@pytest.fixture
def express(tmp_path):
    e = VoiceExpress(base_dir=str(tmp_path))
    assert e.load()
    return e


@pytest.fixture(scope="module")
def engine():
    profile = os.environ.get("VCC_TEST_VOICE_PROFILE")
    lexicon = os.environ.get("CHRISTMAN_LEXICON_PATH")
    if not profile:
        pytest.skip("NOT RUN: no voice was handed in; set VCC_TEST_VOICE_PROFILE to a measured voice profile")
    if not lexicon:
        pytest.skip("NOT RUN: no lexicon; set CHRISTMAN_LEXICON_PATH to a Vosk align_lexicon.txt")
    from voice_engine import VoiceEngine
    e = VoiceEngine(profile, lexicon_path=lexicon)
    assert e.is_ready, e.not_ready_reason
    return e


# ── The committed index ──────────────────────────────────────────────────────

def test_committed_index_loads_and_holds_nothing_unreal():
    data = json.loads((ROOT / "express" / "express_index.json").read_text())
    for entry in data["phrases"]:
        assert Path(entry["audio_path"]).exists(), f"points at a missing file: {entry['audio_path']}"
        assert "quality_score" not in entry
        assert (entry["duration_seconds"], entry["sample_rate"]) == voice_express.measure_wav(entry["audio_path"])
    # It loads with the module that serves it (it used to fail on 'source').
    phrases = [voice_express.ExpressPhrase.from_dict(dict(e)) for e in data["phrases"]]
    assert len(phrases) == data["meta"]["total_phrases"]


# ── The one writer ───────────────────────────────────────────────────────────

def test_register_reads_duration_and_rate_from_the_file(express, tmp_path):
    wav = write_voiced_wav(tmp_path / "hello.wav", seconds=1.0, rate=44100)
    p = express.register_phrase("Hello", BEING, "en-US", str(wav), source="recording")
    assert p.sample_rate == 44100
    assert p.duration_seconds == 1.0
    saved = json.loads(express.index_path.read_text())["phrases"][0]
    assert saved["sample_rate"] == 44100 and "quality_score" not in saved


def test_unreadable_audio_is_refused(express, tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(UnmeasurableAudio):
        express.register_phrase("Nothing", BEING, "en-US", str(empty), source="recording")
    assert json.loads(express.index_path.read_text())["phrases"] == []


def test_registrar_refuses_unreadable_files_and_marks_filename_text(tmp_path):
    folder = tmp_path / "audio"
    folder.mkdir()
    write_voiced_wav(folder / "you_are_safe.wav")
    (folder / "broken.wav").write_bytes(b"not a wav")
    report = voice_path_registrar.register_audio_folder(str(folder), BEING, base_dir=str(tmp_path))
    assert report["registered"] == 1
    assert len(report["errors"]) == 1 and "broken.wav" in report["errors"][0]
    e = VoiceExpress(base_dir=str(tmp_path))
    assert e.load()
    (p,) = e.get_phrases_for_being(BEING)
    assert p.text == "you are safe" and p.text_from == "filename" and p.source == "path_registrar"


def test_every_writer_writes_an_index_the_express_lane_loads(tmp_path):
    pytest.importorskip("pydub", reason="NOT RUN: pydub is not installed (pip install -r requirements.txt)")
    import live_studio
    import voice_ingestor

    # registrar
    one = write_voiced_wav(tmp_path / "one.wav")
    assert voice_path_registrar.register_single_file(str(one), BEING, "en-US", "One", base_dir=str(tmp_path))["registered"]
    # ingestor
    src = write_phrases_with_gaps_wav(tmp_path / "two.wav", count=1)
    rep = voice_ingestor.ingest_file(src, BEING, phrase_map=["Two"], base_dir=str(tmp_path))
    assert rep["success"] and rep["registered"] == 1, rep
    # studio export
    studio = live_studio.LiveStudio(export_dir=tmp_path / "exports", base_dir=tmp_path)
    s = studio.create_session("Three", speaker="studio test", being=BEING)
    studio.register_raw_take(s.id, str(write_voiced_wav(tmp_path / "three.wav")))
    studio.mark_validation(s.id, transcript="Three")
    studio.approve(s.id)
    studio.export_approved(s.id, register=True)

    e = VoiceExpress(base_dir=str(tmp_path))
    assert e.load(), "the express lane must load what the other writers wrote"
    assert e.load_problems == []
    sources = {p.text: p.source for p in e.get_phrases_for_being(BEING)}
    assert sources == {"One": "path_registrar", "Two": "voice_ingestor", "Three": "studio_export"}


def test_missing_file_is_left_out_at_load(express, tmp_path):
    wav = write_voiced_wav(tmp_path / "gone.wav")
    express.register_phrase("Gone", BEING, "en-US", str(wav), source="recording")
    wav.unlink()
    e = VoiceExpress(base_dir=str(tmp_path))
    assert e.load()
    assert not e.is_cached("Gone", BEING)
    assert len(e.load_problems) == 1 and "gone.wav" in e.load_problems[0]


def test_an_index_in_another_format_is_not_loaded_or_overwritten(tmp_path):
    e = VoiceExpress(base_dir=str(tmp_path))
    old = {"meta": {}, "phrases": [{"phrase_id": "x", "being_name": BEING, "language": "en-US", "text": "x",
                                    "audio_path": "/nowhere.wav", "duration_seconds": 1.0, "sample_rate": 22050,
                                    "source": "recording", "quality_score": 1.0}]}
    e.index_path.write_text(json.dumps(old))
    assert e.load() is False
    assert json.loads(e.index_path.read_text()) == old


# ── The priority queue ───────────────────────────────────────────────────────

def test_each_priority_runs_first_in_first_out(express):
    for text, pr in [("one", Priority.HIGH), ("two", Priority.HIGH), ("p", Priority.PRELOAD),
                     ("zero", Priority.CRITICAL), ("s", Priority.STANDARD), ("three", Priority.HIGH),
                     ("zero b", Priority.CRITICAL)]:
        express.priority_synthesize(text, BEING, priority=pr)
    order = []
    while (r := express.get_next_priority_request()) is not None:
        order.append(r.text)
    assert order == ["zero", "zero b", "one", "two", "three", "s", "p"]


def test_a_request_with_no_engine_fails_loudly(express):
    req = express.priority_synthesize("Help is on the way", BEING, priority=Priority.CRITICAL)
    done = express.run_queue({})
    assert done == [req]
    assert req.done and not req.succeeded and "no voice engine" in req.error
    assert express.get_result(req.request_id) is req


def test_the_consumer_renders_in_the_beings_voice(express, engine):
    req = express.priority_synthesize("I am here with you", BEING, priority=Priority.HIGH)
    express.run_queue({BEING: engine})
    assert req.succeeded, req.error
    phrase = express.get_phrase("I am here with you", BEING)
    assert phrase.source == "rendered" and phrase.voice == engine.voice_name
    assert Path(phrase.audio_path).parent == express.paths.phrase_cache / "being_under_test"
    assert take_listener.listen(phrase.audio_path).passed
    served = express.serve("I am here with you", BEING)
    assert served.success and served.audio_data == Path(phrase.audio_path).read_bytes()


def test_the_consumer_refuses_a_control_it_cannot_apply(express, engine):
    from voice_engine import VoiceParameters
    req = express.priority_synthesize("You are safe", BEING, params=VoiceParameters(porosity=0.5))
    express.run_queue({BEING: engine})
    if engine._synth_accepts("breath"):
        assert req.succeeded, req.error
    else:
        assert not req.succeeded and "porosity" in req.error
        assert not express.is_cached("You are safe", BEING)
        assert not list(express.paths.phrase_cache.rglob("*.wav"))


# ── Each being's own phrases ─────────────────────────────────────────────────

def test_phrases_come_from_the_beings_own_source(express, tmp_path):
    j = tmp_path / "phrases.json"
    j.write_text(json.dumps({"phrases": ["I need help", "Yes", "yes", ""]}))
    t = tmp_path / "phrases.txt"
    t.write_text("# a being's phrases\nI need help\n\nThank you\n")
    assert express.get_preload_list(j) == ["I need help", "Yes"]
    assert express.get_preload_list(t) == ["I need help", "Thank you"]
    express.register_phrase("Yes", BEING, "en-US", str(write_voiced_wav(tmp_path / "y.wav")), source="recording")
    assert express.get_unrendered_phrases(j, BEING, "en-US") == ["I need help"]
    queued = express.queue_preload(j, BEING)
    assert [r.text for r in queued] == ["I need help"] and queued[0].priority == Priority.PRELOAD


# ── The ingestor ─────────────────────────────────────────────────────────────

def test_ingestor_keeps_the_language_and_reads_the_real_rate(tmp_path):
    pytest.importorskip("pydub", reason="NOT RUN: pydub is not installed (pip install -r requirements.txt)")
    import voice_ingestor
    src = write_phrases_with_gaps_wav(tmp_path / "deux.wav", count=2, rate=44100)
    rep = voice_ingestor.ingest_file(src, BEING, language="fr-FR", phrase_map=["Bonjour", "Merci"], base_dir=str(tmp_path))
    assert rep["success"] and rep["registered"] == 2, rep
    e = VoiceExpress(base_dir=str(tmp_path))
    assert e.load()
    for p in e.get_phrases_for_being(BEING):
        assert p.language == "fr-FR"
        assert p.sample_rate == 44100 == voice_express.measure_wav(p.audio_path)[1]


def test_ingestor_without_a_map_registers_no_invented_text(tmp_path):
    pytest.importorskip("pydub", reason="NOT RUN: pydub is not installed (pip install -r requirements.txt)")
    import voice_ingestor
    src = write_phrases_with_gaps_wav(tmp_path / "two.wav", count=2)
    rep = voice_ingestor.ingest_file(src, BEING, base_dir=str(tmp_path))
    assert rep["success"] and rep["phrases"] == 2 and rep["registered"] == 0, rep
    assert len(rep["unregistered_text_unknown"]) == 2
    assert json.loads((tmp_path / "express" / "express_index.json").read_text())["phrases"] == []


def test_ingestor_map_that_does_not_line_up_registers_nothing(tmp_path):
    pytest.importorskip("pydub", reason="NOT RUN: pydub is not installed (pip install -r requirements.txt)")
    import voice_ingestor
    src = write_phrases_with_gaps_wav(tmp_path / "two.wav", count=2)
    rep = voice_ingestor.ingest_file(src, BEING, phrase_map=["Only one"], base_dir=str(tmp_path))
    assert not rep["success"] and rep["registered"] == 0
    assert "cannot be matched" in rep["errors"][0]
    assert (tmp_path / "failed" / "two.wav").exists()
