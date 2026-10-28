"""
One base directory for the whole VCC.

Every module that reads or writes must use the same base: the base_dir passed
in, else $VCC_HOME, else this repo. Before, three modules at the repo root
wrote to the folder above the repo while the rest used the repo, so the loader
could never see what the registry wrote.
"""

import json
from pathlib import Path

import pytest

import vcc_paths
from vcc_paths import VCCPaths

ROOT = Path(__file__).resolve().parent.parent


def _files_under(folder: Path) -> set:
    return {p for p in folder.rglob("*") if p.is_file()}


def test_default_base_is_the_repo_not_the_folder_above(monkeypatch):
    monkeypatch.delenv(vcc_paths.HOME_ENV, raising=False)
    assert VCCPaths().base == ROOT
    assert VCCPaths().express_index == ROOT / "express" / "express_index.json"


def test_vcc_home_moves_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(vcc_paths.HOME_ENV, str(tmp_path))
    p = VCCPaths()
    assert p.base == tmp_path.resolve()
    for folder in (p.packs, p.inventory, p.express, p.incoming, p.logs, p.studio_raw):
        assert tmp_path.resolve() in folder.parents


def test_passed_base_dir_wins_over_vcc_home(monkeypatch, tmp_path):
    monkeypatch.setenv(vcc_paths.HOME_ENV, str(tmp_path / "home"))
    assert VCCPaths(tmp_path / "given").base == (tmp_path / "given").resolve()


def test_registry_and_loader_meet_in_one_base(tmp_path):
    """What the registry writes, the loader reads: both under the same base."""
    from Voice_registry import VoiceRegistry, VoicePackRecord
    import voice_loader

    reg = VoiceRegistry(base_dir=str(tmp_path))
    reg.load()
    reg.register_pack(VoicePackRecord(pack_id="pack_one", being_name="being one", language="en-US"))

    assert (tmp_path / "inventory" / "__index__.json").exists()
    listed = voice_loader.list_packs(base_dir=str(tmp_path))
    assert [p["pack_id"] for p in listed] == ["pack_one"]


def test_every_writer_stays_inside_its_base(tmp_path):
    """Registry, express, logger and studio write under the base and nowhere else nearby."""
    from Voice_registry import VoiceRegistry
    from voice_express import VoiceExpress
    import voice_logger
    import live_studio

    base = tmp_path / "base"
    outside_before = _files_under(tmp_path)

    VoiceRegistry(base_dir=str(base)).load()
    VoiceExpress(base_dir=str(base)).load()
    voice_logger.log_safety_event("test", "pack_one", "being one", "path test", base_dir=str(base))
    live_studio.LiveStudio(export_dir=base / "exports", base_dir=base)

    written = _files_under(tmp_path) - outside_before
    assert written, "the writers wrote nothing"
    for f in written:
        assert base.resolve() in f.resolve().parents, f"{f} is outside the base {base}"
    assert (base / "inventory" / "__index__.json").exists()
    assert (base / "express" / "express_index.json").exists()
    assert (base / "logs" / "voice_safety.jsonl").exists()
    assert (base / "studio" / "raw").is_dir()
    events = voice_logger.get_recent_safety_events(base_dir=str(base))
    assert events and events[-1]["reason"] == "path test"


def test_ingestor_writes_under_its_base(tmp_path):
    pytest.importorskip("pydub", reason="NOT RUN: pydub is not installed (pip install -r requirements.txt)")
    import voice_ingestor
    from signals_for_tests import write_voiced_wav

    base = tmp_path / "base"
    paths = VCCPaths(base)
    paths.incoming.mkdir(parents=True)
    wav = write_voiced_wav(paths.incoming / "take.wav")

    report = voice_ingestor.ingest_file(wav, being_name="being one", phrase_map=["hello"], base_dir=str(base))
    assert report["success"], report
    assert (paths.processed / "take.wav").exists()
    index = json.loads(paths.express_index.read_text())
    assert index["phrases"]
    for entry in index["phrases"]:
        assert base.resolve() in Path(entry["audio_path"]).resolve().parents
