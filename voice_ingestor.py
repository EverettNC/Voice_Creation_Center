"""
================================================================================
FILE: voice_ingestor.py
PROJECT: Christman Voice Creation Center — Front Door
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    The Front Door of the Christman Voice Creation Center.

    Drop an MP3, WAV, or any audio file into the incoming/ folder.
    Walk away.

    The ingestor handles everything:
        1. Detects the incoming file
        2. Converts to WAV if needed
        3. Splits on silence — finds natural phrase boundaries
        4. Names files intelligently
        5. Registers everything into the express index automatically
        6. Reports exactly what got ingested and what's now live

    WATCH MODE:
        Run with --watch and it monitors the incoming/ folder continuously.
        Anyone drops a file in. It's live. No terminal. No commands.
        No asking anyone for help.

    This is the welcome mat. You drop it here. We handle the rest.

CARDINAL RULE 13: No fake ingestion. No phantom registrations.
    Every file registered must exist. Every phrase must be real audio.
================================================================================
"""

import argparse
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_ingestor")

# ── Internal paths ────────────────────────────────────────────────────────────
# incoming/, processed/, failed/, packs/phrases/ and express/express_index.json
# all sit under the VCC's one base directory (vcc_paths): the base_dir passed
# in, else $VCC_HOME, else this repo. (They used to be built from
# Path(__file__).parent.parent, which from the repo root is the folder ABOVE
# the repo.)
from vcc_paths import VCCPaths
import voice_express  # the one writer of express index entries

# Silence detection defaults
SILENCE_THRESH_DB  = -40    # dB — anything quieter than this is silence
MIN_SILENCE_MS     = 500    # minimum silence length to split on (ms)
MIN_PHRASE_MS      = 300    # minimum phrase length to keep (ms)
MAX_PHRASE_MS      = 15000  # maximum phrase length (15 seconds)

# Supported input formats
SUPPORTED_FORMATS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_dirs(paths: VCCPaths) -> None:
    """Ensure all required directories exist."""
    for d in [paths.incoming, paths.processed, paths.failed, paths.ingested_phrases,
              paths.express]:
        d.mkdir(parents=True, exist_ok=True)


def _make_phrase_id(text: str, being_name: str, language: str) -> str:
    """Generate a stable unique ID for a phrase (voice_express.make_phrase_id)."""
    return voice_express.make_phrase_id(text, being_name, language)


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_to_wav(input_path: Path, output_path: Path) -> bool:
    """
    Convert any supported audio format to WAV.
    Uses pydub, which runs the ffmpeg program for any input that is not
    already WAV. Without pydub it returns False and says so; there is no
    other converter. Returns True if successful.
    """
    try:
        from pydub import AudioSegment
        suffix = input_path.suffix.lower()
        fmt_map = {
            ".mp3": "mp3", ".wav": "wav", ".flac": "flac",
            ".ogg": "ogg", ".m4a": "mp4", ".aac": "aac"
        }
        fmt = fmt_map.get(suffix, "mp3")
        audio = AudioSegment.from_file(str(input_path), format=fmt)
        # Normalize to 22050Hz mono — Christman SDK standard
        audio = audio.set_frame_rate(22050).set_channels(1)
        audio.export(str(output_path), format="wav")
        logger.info(f"Converted {input_path.name} → {output_path.name}")
        return True
    except ImportError:
        logger.error(
            "pydub not installed. Run: pip install pydub\n"
            "Also ensure ffmpeg is installed: brew install ffmpeg"
        )
        return False
    except Exception as e:
        logger.error(f"Conversion failed for {input_path.name}: {e}")
        return False


# ── Splitting ─────────────────────────────────────────────────────────────────

def split_on_silence(
    wav_path: Path,
    being_name: str,
    output_dir: Path,
    phrase_map: Optional[list] = None,
    language: str = "en-US",
) -> list[dict]:
    """
    Split a WAV file on silence into individual phrase files.
    Returns one dict per phrase file written: its path, its duration and
    sample rate as read back from the written file, and its text when the
    phrase map gives one (None otherwise).

    Args:
        wav_path:    Path to the WAV file to split
        being_name:  Name of the being this voice belongs to
        output_dir:  Directory to write phrase WAV files
        phrase_map:  Optional ordered list of phrase texts matching the splits
                     e.g. ["I love you", "I need help", "Yes", "No"]
                     Without it the chunks are written with numbered names
                     and their text is unknown (None): a number is not what
                     was said.
        language:    BCP-47 language code of the speech

    WHAT CHANGED AND WHY (2026-10-28): the language was hard-coded "en-US"
    whatever the caller passed; the sample rate was written as 22050 without
    reading the file (a 44100 Hz WAV was recorded as 22050); the quality was
    written as 1.0 without measuring anything; and without a phrase map the
    text of record became "phrase_001". Now the language is the caller's,
    the rate and duration are read from each written file, no quality is
    recorded, and an unknown text stays unknown.
    """
    try:
        from pydub import AudioSegment
        from pydub.silence import split_on_silence as pydub_split
    except ImportError:
        logger.error("pydub not installed. Run: pip install -r requirements.txt")
        return []

    try:
        audio = AudioSegment.from_wav(str(wav_path))
        logger.info(
            f"Splitting {wav_path.name} — "
            f"duration: {len(audio)/1000:.1f}s, {audio.frame_rate} Hz, "
            f"silence threshold: {SILENCE_THRESH_DB}dB"
        )

        chunks = pydub_split(
            audio,
            min_silence_len=MIN_SILENCE_MS,
            silence_thresh=SILENCE_THRESH_DB,
            keep_silence=150,  # Keep 150ms of silence on each end for natural sound
        )

        # Filter out chunks that are too short or too long
        valid_chunks = [
            c for c in chunks
            if MIN_PHRASE_MS <= len(c) <= MAX_PHRASE_MS
        ]

        logger.info(
            f"Found {len(chunks)} chunks, "
            f"{len(valid_chunks)} valid after filtering."
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = "".join(c.lower() if c.isalnum() else "_" for c in being_name).strip("_") or "being"
        phrases = []

        for i, chunk in enumerate(valid_chunks):
            # Determine phrase text
            if phrase_map and i < len(phrase_map):
                text = phrase_map[i]
                safe_name = "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")[:40]
            else:
                text = None
                safe_name = f"{wav_path.stem}_chunk_{i+1:03d}"

            filename   = f"{stem}_{safe_name}.wav"
            chunk_path = output_dir / filename

            # Normalize volume
            chunk = chunk.normalize()
            chunk.export(str(chunk_path), format="wav")
            duration, sample_rate = voice_express.measure_wav(chunk_path)

            phrases.append({
                "being_name":        being_name,
                "language":          language,
                "text":              text,
                "audio_path":        str(chunk_path.resolve()),
                "duration_seconds":  duration,
                "sample_rate":       sample_rate,
                "original_filename": wav_path.name,
            })

            logger.info(
                f"  [{i+1}/{len(valid_chunks)}] {text!r} → "
                f"{filename} ({duration:.2f}s, {sample_rate} Hz)"
            )

        return phrases

    except Exception as e:
        logger.error(f"Splitting failed for {wav_path.name}: {e}", exc_info=True)
        return []


# ── Registration ──────────────────────────────────────────────────────────────

def register_phrases(phrases: list[dict], base_dir: Optional[str] = None) -> int:
    """
    Register phrase files into the express index through
    voice_express.register_phrase (which reads each file's duration and
    sample rate itself). A phrase whose text is unknown is not registered:
    the index holds what is said in each file, and nobody said it.
    Skips phrases already registered. Returns count of newly registered phrases.
    """
    if not phrases:
        return 0
    express = voice_express.VoiceExpress(base_dir=base_dir)
    if not express.load():
        raise RuntimeError(f"express index could not be loaded: {express.index_path}")

    count = 0
    for phrase in phrases:
        if not phrase.get("text"):
            logger.info(f"Not registered (its text is unknown): {phrase['audio_path']}")
            continue
        if express.is_cached(phrase["text"], phrase["being_name"], phrase["language"]):
            logger.debug(f"Already registered: '{phrase['text']}' — skipping.")
            continue
        express.register_phrase(
            text=phrase["text"],
            being_name=phrase["being_name"],
            language=phrase["language"],
            audio_path=phrase["audio_path"],
            source="voice_ingestor",
            original_filename=phrase.get("original_filename"),
        )
        count += 1

    if count:
        logger.info(f"Registered {count} new phrases into express index.")
    return count


# ── Main ingestion pipeline ───────────────────────────────────────────────────

def ingest_file(
    input_path: Path,
    being_name: str,
    language: str = "en-US",
    phrase_map: Optional[list] = None,
    base_dir: Optional[str] = None,
) -> dict:
    """
    Full ingestion pipeline for a single audio file.

    Drop it in. We handle the rest.

    Args:
        input_path:  Path to the audio file (MP3, WAV, etc.)
        being_name:  Which being this voice belongs to
        language:    BCP-47 language code
        phrase_map:  Optional ordered list of phrase texts

    Returns:
        Ingestion report dict.
    """
    paths = VCCPaths(base_dir)
    _ensure_dirs(paths)
    report = {
        "file":        input_path.name,
        "being_name":  being_name,
        "language":    language,
        "success":     False,
        "converted":   False,
        "phrases":     0,
        "registered":  0,
        "unregistered_text_unknown": [],
        "errors":      [],
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }

    if not input_path.exists():
        report["errors"].append(f"File not found: {input_path}")
        return report

    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        report["errors"].append(
            f"Unsupported format: {suffix}. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}"
        )
        return report

    try:
        # Step 1 — Convert to WAV if needed
        if suffix != ".wav":
            wav_path = paths.ingested_phrases / f"{input_path.stem}_converted.wav"
            if not convert_to_wav(input_path, wav_path):
                report["errors"].append("Conversion to WAV failed.")
                shutil.move(str(input_path), str(paths.failed / input_path.name))
                return report
            report["converted"] = True
        else:
            wav_path = input_path

        # Step 2 — Split on silence
        output_dir = paths.ingested_phrases / (
            "".join(c.lower() if c.isalnum() else "_" for c in being_name).strip("_") or "being"
        )
        phrases = split_on_silence(
            wav_path=wav_path,
            being_name=being_name,
            output_dir=output_dir,
            phrase_map=phrase_map,
            language=language,
        )

        if not phrases:
            report["errors"].append(
                "No valid phrases found after splitting. "
                "Try adjusting silence threshold or check audio quality."
            )
            shutil.move(str(input_path), str(paths.failed / input_path.name))
            return report

        report["phrases"] = len(phrases)

        # A phrase map must line up one text per phrase. If the file split
        # into a different number of phrases, no text can be trusted to sit
        # on the right audio, so none is registered.
        if phrase_map and len(phrase_map) != len(phrases):
            report["errors"].append(
                f"The phrase map has {len(phrase_map)} texts but the file split into "
                f"{len(phrases)} phrases; nothing was registered, because the texts "
                f"cannot be matched to the audio. Phrase files written: "
                f"{[p['audio_path'] for p in phrases]}"
            )
            shutil.move(str(input_path), str(paths.failed / input_path.name))
            return report

        report["unregistered_text_unknown"] = [p["audio_path"] for p in phrases if not p["text"]]

        # Step 3 — Register into express index
        registered = register_phrases(phrases, base_dir)
        report["registered"] = registered

        # Step 4 — Move original to processed
        shutil.move(
            str(input_path),
            str(paths.processed / input_path.name)
        )

        report["success"] = True
        logger.info(
            f"✅ Ingestion complete: {input_path.name} → "
            f"{len(phrases)} phrases, {registered} registered."
        )

    except Exception as e:
        logger.error(f"Ingestion failed for {input_path.name}: {e}", exc_info=True)
        report["errors"].append(str(e))
        try:
            shutil.move(str(input_path), str(paths.failed / input_path.name))
        except Exception as move_error:
            report["errors"].append(f"and it could not be moved to failed/: {move_error}")
            logger.error(f"Could not move {input_path.name} to failed/: {move_error}")

    return report


# ── Watch mode ────────────────────────────────────────────────────────────────

def watch_incoming(
    being_name: str,
    language: str = "en-US",
    phrase_map: Optional[list] = None,
    poll_seconds: int = 3,
    base_dir: Optional[str] = None,
) -> None:
    """
    Watch the incoming/ folder continuously.
    Any supported audio file dropped in gets ingested automatically.

    Anyone drops a file in. It's live. No terminal. No commands.
    No asking anyone for help.

    Args:
        being_name:   Which being these voices belong to
        language:     BCP-47 language code
        phrase_map:   Optional phrase map for splitting
        poll_seconds: How often to check the folder (default: 3 seconds)
    """
    paths = VCCPaths(base_dir)
    _ensure_dirs(paths)
    logger.info(
        f"👁  Voice Ingestor watching: {paths.incoming}\n"
        f"   Being: {being_name} | Language: {language}\n"
        f"   Drop any audio file in to ingest it automatically.\n"
        f"   Press Ctrl+C to stop."
    )

    # A file is remembered by name, size and modification time, so a new
    # file dropped in later under the same name is still ingested.
    processed_files: set = set()

    while True:
        try:
            for file_path in paths.incoming.iterdir():
                if file_path.suffix.lower() not in SUPPORTED_FORMATS:
                    continue
                try:
                    st = file_path.stat()
                except FileNotFoundError:
                    continue
                seen_key = (file_path.name, st.st_size, st.st_mtime_ns)
                if seen_key in processed_files:
                    continue

                # Wait briefly to ensure file is fully written
                time.sleep(0.5)

                logger.info(f"📥 Detected: {file_path.name} — starting ingestion...")
                report = ingest_file(
                    input_path=file_path,
                    being_name=being_name,
                    language=language,
                    phrase_map=phrase_map,
                    base_dir=base_dir,
                )

                processed_files.add(seen_key)

                if report["success"]:
                    logger.info(
                        f"✅ {file_path.name} → "
                        f"{report['phrases']} phrases, "
                        f"{report['registered']} registered and live."
                    )
                else:
                    logger.error(
                        f"❌ {file_path.name} failed: "
                        f"{'; '.join(report['errors'])}"
                    )

        except KeyboardInterrupt:
            logger.info("Voice Ingestor stopped.")
            break
        except Exception as e:
            logger.error(f"Watch loop error: {e}")

        time.sleep(poll_seconds)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Christman Voice Center — Voice Ingestor\n"
                    "Drop audio files in. We handle the rest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--being",    required=True, help="Name of the being this voice belongs to")
    parser.add_argument("--language", default="en-US", help="BCP-47 language code")
    parser.add_argument("--watch",    action="store_true", help="Watch incoming/ folder continuously")
    parser.add_argument("--file",     help="Ingest a single specific file")
    parser.add_argument("--base-dir", default=None,
                        help="The VCC's base directory (default: $VCC_HOME, else this repo)")
    parser.add_argument(
        "--phrases",
        nargs="+",
        help='Ordered phrase texts e.g. --phrases "I love you" "I need help" "Yes" "No"'
    )

    args = parser.parse_args()
    phrase_map = args.phrases or None

    if args.watch:
        watch_incoming(
            being_name=args.being,
            language=args.language,
            phrase_map=phrase_map,
            base_dir=args.base_dir,
        )
    elif args.file:
        report = ingest_file(
            input_path=Path(args.file),
            being_name=args.being,
            language=args.language,
            phrase_map=phrase_map,
            base_dir=args.base_dir,
        )
        print(json.dumps(report, indent=2))
    else:
        # Ingest everything currently in incoming/
        cli_paths = VCCPaths(args.base_dir)
        _ensure_dirs(cli_paths)
        files = [
            f for f in cli_paths.incoming.iterdir()
            if f.suffix.lower() in SUPPORTED_FORMATS
        ]
        if not files:
            print(f"No audio files found in {cli_paths.incoming}")
            print(f"Drop audio files there and run again, or use --watch mode.")
        else:
            for f in files:
                print(f"\nIngesting: {f.name}")
                report = ingest_file(
                    input_path=f,
                    being_name=args.being,
                    language=args.language,
                    phrase_map=phrase_map,
                    base_dir=args.base_dir,
                )
                print(json.dumps(report, indent=2))
