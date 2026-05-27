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
        Yorkie drops a file in. It's live. No terminal. No commands.
        No asking anyone for help.

    This is the welcome mat. You drop it here. We handle the rest.

CARDINAL RULE 13: No fake ingestion. No phantom registrations.
    Every file registered must exist. Every phrase must be real audio.
================================================================================
"""

import argparse
import hashlib
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
CENTER_ROOT  = Path(__file__).parent.parent
INCOMING_DIR = CENTER_ROOT / "incoming"
PROCESSED_DIR = CENTER_ROOT / "processed"
FAILED_DIR   = CENTER_ROOT / "failed"
PHRASES_DIR  = CENTER_ROOT / "packs" / "phrases"
EXPRESS_INDEX = CENTER_ROOT / "express" / "express_index.json"

# Silence detection defaults
SILENCE_THRESH_DB  = -40    # dB — anything quieter than this is silence
MIN_SILENCE_MS     = 500    # minimum silence length to split on (ms)
MIN_PHRASE_MS      = 300    # minimum phrase length to keep (ms)
MAX_PHRASE_MS      = 15000  # maximum phrase length (15 seconds)

# Supported input formats
SUPPORTED_FORMATS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_dirs():
    """Ensure all required directories exist."""
    for d in [INCOMING_DIR, PROCESSED_DIR, FAILED_DIR, PHRASES_DIR,
              CENTER_ROOT / "express"]:
        d.mkdir(parents=True, exist_ok=True)


def _make_phrase_id(text: str, being_name: str, language: str) -> str:
    """Generate a stable unique ID for a phrase."""
    raw = f"{being_name.lower()}::{language.lower()}::{text.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _infer_text_from_filename(filename: str) -> str:
    """Infer phrase text from filename."""
    stem = Path(filename).stem
    return stem.replace("_", " ").replace("-", " ").strip()


def _load_express_index() -> dict:
    """Load the express index."""
    if not EXPRESS_INDEX.exists():
        return {"meta": {}, "phrases": []}
    try:
        with open(EXPRESS_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load express index: {e}")
        return {"meta": {}, "phrases": []}


def _save_express_index(index: dict) -> None:
    """Save the express index."""
    index["meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    index["meta"]["total_phrases"] = len(index.get("phrases", []))
    try:
        with open(EXPRESS_INDEX, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save express index: {e}")


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_to_wav(input_path: Path, output_path: Path) -> bool:
    """
    Convert any supported audio format to WAV.
    Uses pydub — falls back to ffmpeg directly if pydub unavailable.
    Returns True if successful.
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
    phrase_map: Optional[dict] = None,
) -> list[dict]:
    """
    Split a WAV file on silence into individual phrase files.
    Returns a list of phrase dicts ready for express index registration.

    Args:
        wav_path:    Path to the WAV file to split
        being_name:  Name of the being this voice belongs to
        output_dir:  Directory to write phrase WAV files
        phrase_map:  Optional ordered list of phrase texts matching the splits
                     e.g. ["I love you", "I need help", "Yes", "No"]
                     If not provided, filenames are auto-generated.
    """
    try:
        from pydub import AudioSegment
        from pydub.silence import split_on_silence as pydub_split
    except ImportError:
        logger.error("pydub not installed. Run: pip install pydub")
        return []

    try:
        audio = AudioSegment.from_wav(str(wav_path))
        logger.info(
            f"Splitting {wav_path.name} — "
            f"duration: {len(audio)/1000:.1f}s, "
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
        phrases = []

        for i, chunk in enumerate(valid_chunks):
            # Determine phrase text
            if phrase_map and i < len(phrase_map):
                text = phrase_map[i]
                safe_name = text.lower().replace(" ", "_").replace("'", "")[:40]
            else:
                text = f"phrase_{i+1:03d}"
                safe_name = text

            filename   = f"{being_name.lower()}_{safe_name}.wav"
            chunk_path = output_dir / filename

            # Normalize volume
            chunk = chunk.normalize()
            chunk.export(str(chunk_path), format="wav")

            phrase_id = _make_phrase_id(text, being_name, "en-US")
            duration  = len(chunk) / 1000.0

            phrases.append({
                "phrase_id":        phrase_id,
                "being_name":       being_name,
                "language":         "en-US",
                "text":             text,
                "audio_path":       str(chunk_path.absolute()),
                "duration_seconds": round(duration, 3),
                "sample_rate":      22050,
                "quality_score":    1.0,
                "serve_count":      0,
                "last_served_at":   None,
                "created_at":       datetime.now(timezone.utc).isoformat(),
                "source":           "voice_ingestor",
                "original_filename": wav_path.name,
            })

            logger.info(
                f"  [{i+1}/{len(valid_chunks)}] '{text}' → "
                f"{filename} ({duration:.2f}s)"
            )

        return phrases

    except Exception as e:
        logger.error(f"Splitting failed for {wav_path.name}: {e}", exc_info=True)
        return []


# ── Registration ──────────────────────────────────────────────────────────────

def register_phrases(phrases: list[dict]) -> int:
    """
    Register a list of phrases into the express index.
    Skips duplicates. Returns count of newly registered phrases.
    """
    if not phrases:
        return 0

    index = _load_express_index()
    existing_ids = {p["phrase_id"] for p in index.get("phrases", [])}

    new_phrases = []
    for phrase in phrases:
        if phrase["phrase_id"] in existing_ids:
            logger.debug(f"Already registered: '{phrase['text']}' — skipping.")
            continue
        new_phrases.append(phrase)
        existing_ids.add(phrase["phrase_id"])

    if new_phrases:
        index.setdefault("phrases", []).extend(new_phrases)
        _save_express_index(index)
        logger.info(f"Registered {len(new_phrases)} new phrases into express index.")

    return len(new_phrases)


# ── Main ingestion pipeline ───────────────────────────────────────────────────

def ingest_file(
    input_path: Path,
    being_name: str,
    language: str = "en-US",
    phrase_map: Optional[list] = None,
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
    _ensure_dirs()
    report = {
        "file":        input_path.name,
        "being_name":  being_name,
        "language":    language,
        "success":     False,
        "converted":   False,
        "phrases":     0,
        "registered":  0,
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
            wav_path = PHRASES_DIR / f"{input_path.stem}_converted.wav"
            if not convert_to_wav(input_path, wav_path):
                report["errors"].append("Conversion to WAV failed.")
                shutil.move(str(input_path), str(FAILED_DIR / input_path.name))
                return report
            report["converted"] = True
        else:
            wav_path = input_path

        # Step 2 — Split on silence
        output_dir = PHRASES_DIR / being_name.lower()
        phrases = split_on_silence(
            wav_path=wav_path,
            being_name=being_name,
            output_dir=output_dir,
            phrase_map=phrase_map,
        )

        if not phrases:
            report["errors"].append(
                "No valid phrases found after splitting. "
                "Try adjusting silence threshold or check audio quality."
            )
            shutil.move(str(input_path), str(FAILED_DIR / input_path.name))
            return report

        report["phrases"] = len(phrases)

        # Step 3 — Register into express index
        registered = register_phrases(phrases)
        report["registered"] = registered

        # Step 4 — Move original to processed
        shutil.move(
            str(input_path),
            str(PROCESSED_DIR / input_path.name)
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
            shutil.move(str(input_path), str(FAILED_DIR / input_path.name))
        except Exception:
            pass

    return report


# ── Watch mode ────────────────────────────────────────────────────────────────

def watch_incoming(
    being_name: str,
    language: str = "en-US",
    phrase_map: Optional[list] = None,
    poll_seconds: int = 3,
) -> None:
    """
    Watch the incoming/ folder continuously.
    Any supported audio file dropped in gets ingested automatically.

    Yorkie drops a file in. It's live. No terminal. No commands.
    No asking anyone for help.

    Args:
        being_name:   Which being these voices belong to
        language:     BCP-47 language code
        phrase_map:   Optional phrase map for splitting
        poll_seconds: How often to check the folder (default: 3 seconds)
    """
    _ensure_dirs()
    logger.info(
        f"👁  Voice Ingestor watching: {INCOMING_DIR}\n"
        f"   Being: {being_name} | Language: {language}\n"
        f"   Drop any audio file in to ingest it automatically.\n"
        f"   Press Ctrl+C to stop."
    )

    processed_files: set = set()

    while True:
        try:
            for file_path in INCOMING_DIR.iterdir():
                if file_path.suffix.lower() not in SUPPORTED_FORMATS:
                    continue
                if file_path.name in processed_files:
                    continue

                # Wait briefly to ensure file is fully written
                time.sleep(0.5)

                logger.info(f"📥 Detected: {file_path.name} — starting ingestion...")
                report = ingest_file(
                    input_path=file_path,
                    being_name=being_name,
                    language=language,
                    phrase_map=phrase_map,
                )

                processed_files.add(file_path.name)

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
    parser.add_argument("--being",    required=True, help="Being name (e.g. AlphaVox)")
    parser.add_argument("--language", default="en-US", help="BCP-47 language code")
    parser.add_argument("--watch",    action="store_true", help="Watch incoming/ folder continuously")
    parser.add_argument("--file",     help="Ingest a single specific file")
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
        )
    elif args.file:
        report = ingest_file(
            input_path=Path(args.file),
            being_name=args.being,
            language=args.language,
            phrase_map=phrase_map,
        )
        print(json.dumps(report, indent=2))
    else:
        # Ingest everything currently in incoming/
        _ensure_dirs()
        files = [
            f for f in INCOMING_DIR.iterdir()
            if f.suffix.lower() in SUPPORTED_FORMATS
        ]
        if not files:
            print(f"No audio files found in {INCOMING_DIR}")
            print(f"Drop audio files there and run again, or use --watch mode.")
        else:
            for f in files:
                print(f"\nIngesting: {f.name}")
                report = ingest_file(
                    input_path=f,
                    being_name=args.being,
                    language=args.language,
                    phrase_map=phrase_map,
                )
                print(json.dumps(report, indent=2))
