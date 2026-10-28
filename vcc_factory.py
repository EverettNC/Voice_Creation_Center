"""
================================================================================
FILE: vcc_factory.py
PROJECT: Christman Voice Creation Center — the Factory
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026-10-28
--------------------------------------------------------------------------------
PURPOSE:
    The factory fills a being's express cache (voice_express.py, Track 1) in
    that being's own voice, so the being can serve its phrases instantly.

    It is one local process. Each turn it does the most urgent thing waiting:
        1. the priority queue (Track 2), CRITICAL first, then HIGH, STANDARD,
           PRELOAD — including anything the being queued since the last turn;
        2. otherwise the next phrase of the being's preload list
           (VoiceExpress.DEFAULT_PHRASES) that is not in the cache yet.
    For each phrase it either registers the being's own recording of it, or
    renders it, validates the file, and registers it with register_phrase().

    The phrases come only from DEFAULT_PHRASES, the queue, and what the being
    queues. The factory never makes up a phrase list.

THE RENDERER
    Christman-Sound's sovereign synthesizer (christman_sound.synth: the C port
    of Codec War's synthesizer, words from Everett's Vosk lexicon), speaking
    with the being's measured voice profile (christman_sound.voice_profile).
    The factory renders a being only in that being's own measured voice. If
    the installed Christman-Sound has no measured voice for the being, the
    factory refuses to start and renders nothing. No outside speech engine,
    no network, no paid service.

RECORDINGS OUTRANK RENDERS
    A phrase the being has a recording of is never rendered: the recording is
    registered in place (the file is read, never moved, copied or written).
    If a rendered entry exists and a recording of that phrase turns up, the
    recording replaces it in the index. The factory writes only inside
    <express root>/phrase_cache/<being>/, and refuses any write path outside it.

PERSISTENCE
    The index (express_index.json), the rendered files and the queue (one
    JSON file per request in priority_queue/) are all on disk. A request
    stays on disk until it is done, so a stop or a crash loses nothing: the
    next start picks up where this one left off.

NETWORK
    None. The factory binds no port and opens no connection.

INSTALL (once, into a venv)
    uv pip install -e <fresh clone of EverettNC/Christman-Sound main>
    uv pip install -e <clone of this repo>

RUN IT (Everett's Mac)
    CHRISTMAN_EXPRESS_ROOT=~/claude-work/vcc-express-alphavox \\
    ~/claude-work/vcc-factory-venv/bin/vcc-factory \\
        --being AlphaVox \\
        --lexicon ~/models/vosk-model-en-us-0.22/graph/phones/align_lexicon.txt \\
        --recordings ~/AlphaVox/data/phrases/alphavox_verified

    AlphaVox reads the same CHRISTMAN_EXPRESS_ROOT. The factory keeps running
    and picks up what AlphaVox queues; Ctrl+C (or SIGTERM) stops it after the
    phrase in hand. Add --once to do everything waiting and then stop.
    Nothing starts it at login; that waits on Everett's approval.

FOR ALPHAVOX
    express = VoiceExpress(root=Path("~/claude-work/vcc-express-alphavox").expanduser())
    express.load()
    express.refresh_index()                          # see the factory's new phrases
    entry = express.get_phrase(text, "AlphaVox", "en-US")   # ExpressPhrase or None
    express.priority_synthesize(text, "AlphaVox", pack_id="alphavox_default_en_us",
                                priority=Priority.HIGH)     # queue it for the factory

CARDINAL RULE 13: No stubs. Every registered file is real audio that passed
    every check below. A phrase that fails is reported and not registered.
================================================================================
"""

from __future__ import annotations

import argparse
import array
import fcntl
import hashlib
import importlib
import json
import logging
import math
import os
import signal
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE / "engines")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voice_express as vx  # noqa: E402  (this repo)
import voice_validator      # noqa: E402  (engines/voice_validator.py)

logger = logging.getLogger("christman.vcc_factory")


# ── Validation thresholds — each a named value with its reason ───────────────

EXPECTED_SAMPLE_RATE = 22050
"""Hz. The Christman SDK standard: studio/recorder.py SAMPLE_RATE, the rate
voice_ingestor.py converts to, and register_phrase()'s default. christman_synth
renders it natively (it takes 16000 or 22050), so nothing is resampled."""

EXPECTED_CHANNELS = 1
"""Mono. studio/recorder.py CHANNELS: all beings speak in mono."""

EXPECTED_SAMPLE_WIDTH = 2
"""Bytes per sample: 16-bit PCM (studio/recorder.py BIT_DEPTH), which is what
christman_sound.synth.write_wav writes."""

MIN_DURATION_S = 0.3
"""Seconds. voice_ingestor.py MIN_PHRASE_MS: the shortest phrase the center
keeps. Codec War's own lead silence (0.12 s) and word gap (0.08 s) alone are
0.2 s, so a render shorter than this holds almost no speech."""

MAX_DURATION_S = 15.0
"""Seconds. voice_ingestor.py MAX_PHRASE_MS: the longest phrase the center
keeps. An express phrase is a short phrase; anything longer is not one."""

MIN_PEAK = 0.05
"""Linear full scale (about -26 dBFS). christman_synth's output peaks at 0.7
(christman_sound.synth.write_wav). A render whose loudest sample sits 23 dB
under the synthesizer's own peak did not sound."""

CLIP_LEVEL = 0.98
"""Linear full scale. studio/recorder.py CLIPPING_THRESHOLD. christman_synth
never goes past 0.7, so a sample at or over this means something is wrong."""

SILENCE_THRESH_DBFS = -40.0
"""dBFS. voice_ingestor.py SILENCE_THRESH_DB: quieter than this is silence."""

FRAME_S = 0.02
"""Seconds. The frame for measuring how much of the file is voiced: 20 ms,
short enough to see a single short phone."""

MIN_VOICED_FRACTION = 0.25
"""Share of 20 ms frames above SILENCE_THRESH_DBFS. The silences Codec War's
textToPhones adds (0.12 s before the phrase, 0.08 s after each word) are
under half of even a one-word render, so a real render is well over a
quarter voiced; a file under a quarter voiced is mostly silence."""


# ── What a rendered entry says about itself ─────────────────────────────────

ENGINE_NAME = "christman"
"""ExpressPhrase.engine for every render: Everett's sovereign synthesizer in
Christman-Sound. The factory checks that the synthesizer reports itself as
SYNTHESIZER before it writes this name."""

SYNTHESIZER = "christman_synth"
"""What christman_sound.synth.SynthResult.engine reports for the C port of
Codec War's synthesizer. Any other value is refused: it is not ours."""

PACK_IDS: Dict[str, str] = {
    # being (lower case) -> the pack_id a being passes to priority_synthesize().
    # The factory takes a being's requests by being_name; the pack_id is
    # recorded with the request, and a request naming another pack is refused
    # so a request meant for another voice is never spoken in this one.
    "alphavox": "alphavox_default_en_us",
}


# ── Errors ───────────────────────────────────────────────────────────────────

class FactoryUnavailable(RuntimeError):
    """The factory cannot run as asked. Nothing was rendered."""


class RenderRefused(RuntimeError):
    """This phrase was not rendered. The reason says why. Nothing was registered."""


class ValidationFailed(RenderRefused):
    """The rendered file failed a check. It was deleted, not registered."""

    def __init__(self, path: Path, checks: dict) -> None:
        self.checks = checks
        failed = [f"{k}: {v['value']} (needs {v['needs']})" for k, v in checks.items() if not v["passed"]]
        super().__init__(f"rendered audio failed validation ({path.name}): " + "; ".join(failed))


# ── Voices: a being renders only in its own measured voice ───────────────────

VOICE_LOADERS: Dict[str, tuple] = {
    # being (lower case) -> (module, function) in the installed Christman-Sound
    "alphavox": ("christman_sound.voice_profile", "load_alphavox_voice"),
}


def load_being_voice(being: str):
    """The being's measured VoiceProfile from Christman-Sound, or FactoryUnavailable."""
    key = being.lower()
    if key not in VOICE_LOADERS:
        raise FactoryUnavailable(
            f"no measured voice is known for {being!r}; the factory renders a being "
            f"only in its own voice (known: {sorted(VOICE_LOADERS)})"
        )
    module_name, func_name = VOICE_LOADERS[key]
    try:
        module = importlib.import_module(module_name)
        loader = getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        import christman_sound
        raise FactoryUnavailable(
            f"the installed Christman-Sound ({Path(christman_sound.__file__).parent}) has no "
            f"{module_name}.{func_name}: {exc}. {being}'s voice profile is not there yet, "
            f"so nothing is rendered. Pull Christman-Sound main and reinstall it."
        ) from exc
    profile = loader()
    if str(getattr(profile, "name", "")).lower() != key:
        raise FactoryUnavailable(
            f"{module_name}.{func_name}() returned the voice {getattr(profile, 'name', None)!r}, "
            f"not {being!r}; refusing to render {being} in another voice"
        )
    return profile


# ── Audio checks ─────────────────────────────────────────────────────────────

def _check(value, needs: str, passed: bool) -> dict:
    return {"value": value, "needs": needs, "passed": bool(passed)}


def validate_wav(path: Path) -> dict:
    """
    Read the file as it will be served and check it against every threshold
    above. Returns {check name: {value, needs, passed}}; every check is run.
    """
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    checks = {
        "sample_rate": _check(rate, f"== {EXPECTED_SAMPLE_RATE}", rate == EXPECTED_SAMPLE_RATE),
        "channels": _check(channels, f"== {EXPECTED_CHANNELS}", channels == EXPECTED_CHANNELS),
        "sample_width_bytes": _check(width, f"== {EXPECTED_SAMPLE_WIDTH}", width == EXPECTED_SAMPLE_WIDTH),
    }
    duration = nframes / float(rate) if rate else 0.0
    checks["duration_s"] = _check(
        round(duration, 3), f"{MIN_DURATION_S} to {MAX_DURATION_S}",
        MIN_DURATION_S <= duration <= MAX_DURATION_S,
    )
    if width != 2 or channels != 1 or nframes == 0:
        # The level checks below read 16-bit mono; they cannot be measured on this file.
        for name in ("peak", "clipped_samples", "voiced_fraction"):
            checks[name] = _check(None, "16-bit mono samples to measure", False)
        return checks

    pcm = array.array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    peak = max(abs(s) for s in pcm) / 32768.0
    clip_at = CLIP_LEVEL * 32768.0
    clipped = sum(1 for s in pcm if abs(s) >= clip_at)

    frame = max(1, int(round(FRAME_S * rate)))
    thresh = (10.0 ** (SILENCE_THRESH_DBFS / 20.0)) * 32768.0
    frames = voiced = 0
    for start in range(0, len(pcm), frame):
        chunk = pcm[start:start + frame]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
        frames += 1
        if rms > thresh:
            voiced += 1
    voiced_fraction = voiced / frames

    checks["peak"] = _check(round(peak, 4), f">= {MIN_PEAK}", peak >= MIN_PEAK)
    checks["clipped_samples"] = _check(clipped, f"== 0 at or over {CLIP_LEVEL}", clipped == 0)
    checks["voiced_fraction"] = _check(
        round(voiced_fraction, 3), f">= {MIN_VOICED_FRACTION}", voiced_fraction >= MIN_VOICED_FRACTION
    )
    return checks


_WAV_PCM, _WAV_FLOAT, _WAV_EXTENSIBLE = 1, 3, 0xFFFE


def _wav_facts(path: Path) -> tuple:
    """
    (duration in seconds, sample rate) read from the RIFF header itself.
    The stdlib wave module reads only integer PCM, and AlphaVox's recordings
    are 32-bit float WAV (format 3), so the header is read directly. The file
    is only read. Raises ValueError for a file that is not a WAV it can measure.
    """
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path.name} is not a RIFF/WAVE file")
        fmt = None
        data_bytes = None
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                break
            cid, size = chunk[:4], int.from_bytes(chunk[4:], "little")
            if cid == b"fmt ":
                body = f.read(size)
                tag = int.from_bytes(body[0:2], "little")
                channels = int.from_bytes(body[2:4], "little")
                rate = int.from_bytes(body[4:8], "little")
                block_align = int.from_bytes(body[12:14], "little")
                if tag == _WAV_EXTENSIBLE and len(body) >= 26:
                    tag = int.from_bytes(body[24:26], "little")
                fmt = (tag, channels, rate, block_align)
                if size % 2:
                    f.read(1)
            elif cid == b"data":
                data_bytes = size
                break
            else:
                f.seek(size + (size % 2), 1)
    if fmt is None or data_bytes is None:
        raise ValueError(f"{path.name} has no fmt or data chunk")
    tag, channels, rate, block_align = fmt
    if tag not in (_WAV_PCM, _WAV_FLOAT) or rate <= 0 or block_align <= 0:
        raise ValueError(f"{path.name}: WAV format {tag} at {rate} Hz cannot be measured")
    return (data_bytes // block_align) / float(rate), rate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── The factory ──────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """What happened to one phrase."""
    text: str
    phrase_id: str
    status: str                 # "rendered", "recording", "already_cached", "failed"
    origin: str                 # "preload" or "queue:<priority>"
    audio_path: Optional[str] = None
    duration_s: Optional[float] = None
    sample_rate: Optional[int] = None
    engine: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None
    at: str = field(default_factory=_now)


class Factory:
    """
    One being's factory. Build it, call start(), then run() or work_once().

        f = Factory("AlphaVox", lexicon_path=..., recordings_dir=...)
        f.start()
        f.run(once=True)
    """

    def __init__(
        self,
        being: str,
        lexicon_path: Optional[str],
        recordings_dir: str,
        express_root: Optional[str] = None,
        language: str = "en-US",
        poll_seconds: float = 1.0,
    ) -> None:
        self.being = being
        self.language = language
        self.poll_seconds = poll_seconds
        self.lexicon_path = lexicon_path
        self.recordings_dir = Path(recordings_dir).expanduser().resolve()
        self.express = vx.VoiceExpress(root=Path(express_root) if express_root else None)
        self.cache_dir = (self.express.phrase_cache / being.lower()).resolve()
        self.outcomes: List[Outcome] = []
        self._failed_preload: Dict[str, str] = {}
        self._recordings: Dict[str, Path] = {}
        self._stop = False
        self._lock_file = None
        self.voice = None
        self.lexicon = None
        self.dsp = None
        self._synthesize: Optional[Callable] = None

    # -- start ---------------------------------------------------------------

    def start(self) -> None:
        """Load everything the factory needs, or raise FactoryUnavailable having rendered nothing."""
        if not self.recordings_dir.is_dir():
            raise FactoryUnavailable(f"recordings folder not found: {self.recordings_dir}")
        cache_root = self.express.phrase_cache.resolve()
        if self.recordings_dir == cache_root or cache_root in self.recordings_dir.parents:
            raise FactoryUnavailable(
                f"recordings folder {self.recordings_dir} sits inside the render cache {cache_root}; "
                f"refusing, so a render can never land on a recording"
            )
        self._acquire_lock()
        if not self.express.load():
            raise FactoryUnavailable(f"express index could not be loaded: {self.express.index_path}")

        from christman_sound.dsp import get_dsp
        from christman_sound.lexicon import load_lexicon
        from christman_sound.synth import synthesize

        self.voice = load_being_voice(self.being)
        self.lexicon = load_lexicon(self.lexicon_path)
        self.dsp = get_dsp()
        self._synthesize = synthesize
        self._recordings = self.scan_recordings()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # A half-written render from a stop mid-phrase is the factory's own file; clear it.
        for partial in self.cache_dir.glob("*.rendering.wav"):
            partial.unlink()
        logger.info(
            "Factory ready — %s in voice %r, %d recordings, express root %s",
            self.being, self.voice.name, len(self._recordings), self.express.root,
        )

    def _acquire_lock(self) -> None:
        """One factory per express root at a time."""
        self.express.root.mkdir(parents=True, exist_ok=True)
        path = self.express.root / "factory.lock"
        fh = open(path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise FactoryUnavailable(f"another factory holds {path}") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._lock_file = fh

    def close(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def scan_recordings(self) -> Dict[str, Path]:
        """
        phrase_id -> recording, for every WAV in the being's recordings folder.
        The phrase is the file name without the being prefix:
        alphavox_thank_you.wav -> "thank you".
        """
        prefix = self.being.lower() + "_"
        found: Dict[str, Path] = {}
        for wav in sorted(self.recordings_dir.glob("*.wav")):
            stem = wav.stem
            if stem.lower().startswith(prefix):
                stem = stem[len(prefix):]
            text = stem.replace("_", " ").replace("-", " ").strip()
            if text:
                found[self.express._make_phrase_id(text, self.being, self.language)] = wav.resolve()
        return found

    # -- the loop ------------------------------------------------------------

    def stop(self, *_args) -> None:
        self._stop = True

    def run(self, once: bool = False) -> List[Outcome]:
        """
        Work until stopped (or, with once=True, until nothing is waiting).
        Stops only between phrases, so the phrase in hand is finished.
        """
        while not self._stop:
            did = self.work_once()
            if not did:
                if once:
                    break
                time.sleep(self.poll_seconds)
        return self.outcomes

    def work_once(self) -> bool:
        """Do the most urgent single thing waiting. Returns False when nothing was."""
        self.express.refresh_index()
        self.express.refresh_priority_queue()

        req = self.express.get_next_priority_request(being_name=self.being)
        if req is not None:
            self._handle_request(req)
            return True

        for text in self.express.get_preload_list(self.being):
            pid = self.express._make_phrase_id(text, self.being, self.language)
            if pid in self._failed_preload or not self._needs_work(text, pid):
                continue
            outcome = self.produce(text, origin="preload")
            if outcome.status == "failed":
                # Not retried in this run; the outcome and the log carry the reason.
                self._failed_preload[pid] = outcome.error or "failed"
            return True
        return False

    def _needs_work(self, text: str, pid: str) -> bool:
        """
        A preload phrase needs work when it is not in the cache
        (get_unrendered_phrases' rule), when its file is gone, or when it was
        rendered and a recording of it now exists.
        """
        entry = self.express.get_phrase(text, self.being, self.language)
        if entry is None:
            return True
        if not Path(entry.audio_path).exists():
            return True
        return entry.is_rendered and pid in self._recordings

    def _handle_request(self, req: vx.PriorityRequest) -> None:
        origin = f"queue:{req.priority.value}"
        if req.language != self.language:
            err = f"language {req.language!r} is not this factory's {self.language!r}"
            self.express.complete_priority_request(req, error=err)
            self._record(Outcome(req.text, "", "failed", origin, error=err))
            return
        expected_pack = PACK_IDS.get(self.being.lower())
        if req.pack_id != expected_pack:
            err = f"pack_id {req.pack_id!r} is not {self.being}'s express pack {expected_pack!r}"
            self.express.complete_priority_request(req, error=err)
            self._record(Outcome(req.text, "", "failed", origin, error=err))
            return
        outcome = self.produce(req.text, origin=origin)
        self.express.complete_priority_request(
            req,
            phrase_id=outcome.phrase_id if outcome.status != "failed" else None,
            error=outcome.error if outcome.status == "failed" else None,
        )

    # -- one phrase ----------------------------------------------------------

    def produce(self, text: str, origin: str) -> Outcome:
        """Get one phrase into the cache: its recording if there is one, else a validated render."""
        pid = self.express._make_phrase_id(text, self.being, self.language)
        try:
            ok, err = voice_validator.validate_text(text)
            if not ok:
                raise RenderRefused(err)

            recording = self._recordings.get(pid)
            entry = self.express.get_phrase(text, self.being, self.language)
            entry_ok = entry is not None and Path(entry.audio_path).exists()

            # Any recording in the cache stays: the factory never replaces one.
            # A recording in the folder replaces a render, or fills a gap.
            if recording is not None and not (entry_ok and not entry.is_rendered):
                return self._record(self._register_recording(text, pid, recording, origin))

            if entry_ok:
                # Already in the cache — recorded or rendered — and the file is there.
                return self._record(Outcome(
                    text, pid, "already_cached", origin, entry.audio_path,
                    entry.duration_seconds, entry.sample_rate, entry.engine, entry.voice,
                ))
            return self._record(self._render_and_register(text, pid, origin))
        except Exception as exc:  # reported, never swallowed: the outcome carries it
            logger.error("Phrase %r not cached: %s", text, exc)
            return self._record(Outcome(text, pid, "failed", origin, error=f"{type(exc).__name__}: {exc}"))

    def _register_recording(self, text: str, pid: str, recording: Path, origin: str) -> Outcome:
        duration, rate = _wav_facts(recording)
        try:
            self.express.register_phrase(
                text=text, being_name=self.being, language=self.language,
                audio_path=str(recording), duration_seconds=round(duration, 3),
                sample_rate=rate, source=vx.RECORDING_SOURCE, original_filename=recording.name,
                # A recording someone else registered meanwhile stays as it is.
                replace_recording=False,
            )
        except vx.RecordingInPlace:
            entry = self.express.get_phrase(text, self.being, self.language)
            return Outcome(text, pid, "already_cached", origin, entry.audio_path,
                           entry.duration_seconds, entry.sample_rate, entry.engine, entry.voice)
        self._confirm_on_disk(pid, str(recording))
        return Outcome(text, pid, "recording", origin, str(recording), round(duration, 3), rate)

    def render(self, text: str, out_path: Path):
        """
        Render `text` in the being's voice to out_path, validated.
        Returns (SynthResult, checks). Raises RenderRefused / ValidationFailed;
        on either, nothing is left at out_path.
        """
        self._assert_writable(out_path)
        result = self._synthesize(
            text, voice=self.voice, sample_rate=EXPECTED_SAMPLE_RATE,
            lexicon=self.lexicon, engine=self.dsp,
        )
        if str(getattr(result, "voice", "") or "").lower() != self.being.lower():
            raise RenderRefused(
                f"the synthesizer reports voice {getattr(result, 'voice', None)!r}, not {self.being!r}"
            )
        if getattr(result, "engine", None) != SYNTHESIZER:
            raise RenderRefused(
                f"the synthesizer reports engine {getattr(result, 'engine', None)!r}, not {SYNTHESIZER!r}"
            )
        if result.sample_rate != EXPECTED_SAMPLE_RATE:
            raise RenderRefused(
                f"the synthesizer returned {result.sample_rate} Hz, not {EXPECTED_SAMPLE_RATE}"
            )
        partial = out_path.with_name(out_path.stem + ".rendering.wav")
        result.write_wav(partial)
        checks = validate_wav(partial)
        if not all(c["passed"] for c in checks.values()):
            partial.unlink()
            raise ValidationFailed(out_path, checks)
        os.replace(partial, out_path)
        return result, checks

    def _render_and_register(self, text: str, pid: str, origin: str) -> Outcome:
        out_path = self.cache_dir / f"{pid}.wav"
        result, checks = self.render(text, out_path)
        duration, rate = _wav_facts(out_path)
        try:
            self._register_render(text, out_path, duration, rate, checks)
        except vx.RecordingInPlace:
            # A recording arrived while this phrase rendered. It stays; the render goes.
            out_path.unlink()
            entry = self.express.get_phrase(text, self.being, self.language)
            return Outcome(text, pid, "already_cached", origin, entry.audio_path,
                           entry.duration_seconds, entry.sample_rate, entry.engine, entry.voice)
        self._confirm_on_disk(pid, str(out_path))
        return Outcome(text, pid, "rendered", origin, str(out_path), round(duration, 3), rate,
                       ENGINE_NAME, self.being.lower())

    def _register_render(self, text: str, out_path: Path, duration: float, rate: int, checks: dict) -> None:
        self.express.register_phrase(
            text=text, being_name=self.being, language=self.language,
            audio_path=str(out_path), duration_seconds=round(duration, 3), sample_rate=rate,
            # 1.0 here means every check above passed (nothing that fails is
            # registered). It is not a listening score; no one has scored it.
            quality_score=1.0,
            source=vx.RENDERED_SOURCE, original_filename=out_path.name,
            engine=ENGINE_NAME, voice=self.being.lower(), validation=checks,
            # Checked again under the index lock: if a recording of this phrase
            # was registered since produce() looked, the render is not registered.
            replace_recording=False,
        )

    def _assert_writable(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.cache_dir:
            raise RenderRefused(f"refusing to write {resolved}: outside the render cache {self.cache_dir}")
        if resolved in set(self._recordings.values()):
            raise RenderRefused(f"refusing to write {resolved}: it is a recording")

    def _confirm_on_disk(self, pid: str, audio_path: str) -> None:
        """Read the index back from disk: the registration is real only if it is there."""
        with open(self.express.index_path, "r", encoding="utf-8") as f:
            on_disk = {p["phrase_id"]: p for p in json.load(f).get("phrases", [])}
        if on_disk.get(pid, {}).get("audio_path") != audio_path:
            raise RuntimeError(f"registration of {pid} did not reach {self.express.index_path}")

    def _record(self, outcome: Outcome) -> Outcome:
        self.outcomes.append(outcome)
        logger.info("%s %s %r -> %s", outcome.origin, outcome.status, outcome.text,
                    outcome.audio_path or outcome.error)
        return outcome

    # -- report ----------------------------------------------------------------

    def report(self) -> dict:
        by_status: Dict[str, int] = {}
        for o in self.outcomes:
            by_status[o.status] = by_status.get(o.status, 0) + 1
        return {
            "being": self.being,
            "voice": getattr(self.voice, "name", None),
            "voice_profile": str(getattr(self.voice, "path", "")),
            "express_root": str(self.express.root),
            "index": str(self.express.index_path),
            "counts": by_status,
            "queue_waiting": self.express.get_queue_depth(),
            "outcomes": [o.__dict__ for o in self.outcomes],
        }


# ── Command line ───────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Christman Voice Creation Center — the factory. Renders a being's "
                    "express phrases in its own voice. No network.",
    )
    parser.add_argument("--being", required=True, help="e.g. AlphaVox")
    parser.add_argument("--recordings", required=True,
                        help="the being's own recordings (read only), e.g. "
                             "~/AlphaVox/data/phrases/alphavox_verified")
    parser.add_argument("--lexicon", default=os.environ.get("CHRISTMAN_LEXICON_PATH"),
                        help="Vosk align_lexicon.txt (default: $CHRISTMAN_LEXICON_PATH)")
    parser.add_argument("--express-root", default=None,
                        help=f"express folder (default: {vx.EXPRESS_ROOT})")
    parser.add_argument("--language", default="en-US")
    parser.add_argument("--once", action="store_true",
                        help="do everything waiting, then stop")
    parser.add_argument("--poll-seconds", type=float, default=1.0,
                        help="how often to look for new work when idle")
    parser.add_argument("--report", default=None, help="also write the JSON report here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s")

    factory = Factory(
        being=args.being, lexicon_path=args.lexicon, recordings_dir=args.recordings,
        express_root=args.express_root, language=args.language, poll_seconds=args.poll_seconds,
    )
    signal.signal(signal.SIGINT, factory.stop)
    signal.signal(signal.SIGTERM, factory.stop)
    try:
        factory.start()
        factory.run(once=args.once)
    except FactoryUnavailable as exc:
        logger.error("Factory not started: %s", exc)
        return 2
    finally:
        factory.close()

    report = factory.report()
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.report:
        Path(args.report).expanduser().write_text(text, encoding="utf-8")
    return 1 if report["counts"].get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
