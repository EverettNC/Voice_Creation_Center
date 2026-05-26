"""
================================================================================
FILE: voice_path_registrar.py
PROJECT: Christman Voice Creation Center — Express Service
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    Path Registration Utility for the Christman Voice Creation Center.

    The WAV files don't move. They stay exactly where they live —
    in Derek's folder, AlphaVox's folder, Giuseppe's folder, Sierra's.

    This utility scans a being's audio directory, reads every WAV file,
    and registers its path into the express index. No copying.
    No transferring. Just a clean internal reference that says
    "when you need this phrase, it lives HERE."

    One command. Every WAV file in that folder is live.

CARDINAL RULE 13: Files are registered only if they actually exist.
    No phantom entries. No broken references.
================================================================================
"""

import hashlib
import json
import logging
import os
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_path_registrar")

EXPRESS_ROOT  = Path(__file__).parent.parent / "express"
EXPRESS_INDEX = EXPRESS_ROOT / "express_index.json"


def _get_wav_duration(wav_path: Path) -> float:
    """
    Read the actual duration of a WAV file in seconds.
    Returns 0.0 if file is unreadable — never crashes.
    """
    try:
        with wave.open(str(wav_path), 'rb') as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return round(frames / float(rate), 3)
    except Exception as e:
        logger.warning(f"Could not read WAV duration for {wav_path}: {e}")
        return 0.0


def _get_wav_sample_rate(wav_path: Path) -> int:
    """Read the sample rate of a WAV file."""
    try:
        with wave.open(str(wav_path), 'rb') as w:
            return w.getframerate()
    except Exception as e:
        logger.warning(f"Could not read sample rate for {wav_path}: {e}")
        return 22050


def _make_phrase_id(text: str, being_name: str, language: str) -> str:
    """Generate a stable unique ID for a phrase."""
    raw = f"{being_name.lower()}::{language.lower()}::{text.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _infer_text_from_filename(filename: str) -> str:
    """
    Infer the phrase text from the WAV filename.
    Converts underscores to spaces and strips the extension.

    Examples:
        i_love_you.wav      → "i love you"
        you_are_safe.wav    → "you are safe"
        help_is_coming.wav  → "help is coming"
    """
    stem = Path(filename).stem
    return stem.replace("_", " ").replace("-", " ").strip()


def _load_index() -> dict:
    """Load the current express index from disk."""
    EXPRESS_ROOT.mkdir(parents=True, exist_ok=True)

    if not EXPRESS_INDEX.exists():
        return {
            "meta": {
                "version": "1.0.0",
                "project": "Christman Voice Creation Center",
                "patent_pending": "TCAP-2026-001 | TCAP-2026-002",
            },
            "phrases": []
        }

    try:
        with open(EXPRESS_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load express index: {e}")
        return {"meta": {}, "phrases": []}


def _save_index(index: dict) -> None:
    """Save the express index to disk."""
    index["meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    index["meta"]["total_phrases"] = len(index.get("phrases", []))

    try:
        with open(EXPRESS_INDEX, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
        logger.info(f"Express index saved — {index['meta']['total_phrases']} phrases.")
    except Exception as e:
        logger.error(f"Failed to save express index: {e}")


def register_audio_folder(
    folder_path: str,
    being_name: str,
    language: str = "en-US",
    quality_score: float = 1.0,
    text_map: Optional[dict] = None,
    dry_run: bool = False
) -> dict:
    """
    Scan a folder for WAV files and register every one into the
    express index. The files stay exactly where they are.

    Args:
        folder_path:   Path to the being's audio folder.
        being_name:    Name of the being (e.g., 'AlphaVox', 'Derek').
        language:      BCP-47 language code (default: 'en-US').
        quality_score: Initial quality score for all registered phrases.
        text_map:      Optional dict mapping filename → phrase text.
                       If not provided, text is inferred from filename.
                       Example: {"i_love_you.wav": "I love you"}
        dry_run:       If True, scans and reports without writing anything.

    Returns:
        Registration report dict with counts and any errors.
    """
    folder = Path(folder_path)

    if not folder.exists():
        logger.error(f"Folder not found: {folder_path}")
        return {
            "success": False,
            "error": f"Folder not found: {folder_path}",
            "registered": 0,
            "skipped": 0,
            "errors": [],
        }

    # Find all WAV files
    wav_files = list(folder.glob("*.wav")) + list(folder.glob("*.WAV"))

    if not wav_files:
        logger.warning(f"No WAV files found in {folder_path}")
        return {
            "success": True,
            "registered": 0,
            "skipped": 0,
            "errors": [],
            "message": "No WAV files found."
        }

    logger.info(
        f"Scanning {len(wav_files)} WAV files for {being_name} "
        f"[{language}] in {folder_path}"
    )

    index = _load_index()
    existing_ids = {p["phrase_id"] for p in index.get("phrases", [])}

    registered = 0
    skipped = 0
    errors = []
    new_phrases = []

    for wav_file in sorted(wav_files):
        try:
            # Get phrase text
            if text_map and wav_file.name in text_map:
                text = text_map[wav_file.name]
            else:
                text = _infer_text_from_filename(wav_file.name)

            if not text:
                logger.warning(f"Could not determine text for {wav_file.name} — skipping.")
                skipped += 1
                continue

            phrase_id = _make_phrase_id(text, being_name, language)

            # Skip if already registered
            if phrase_id in existing_ids:
                logger.debug(f"Already registered: '{text}' for {being_name} — skipping.")
                skipped += 1
                continue

            # Read WAV metadata
            duration = _get_wav_duration(wav_file)
            sample_rate = _get_wav_sample_rate(wav_file)

            phrase_entry = {
                "phrase_id": phrase_id,
                "being_name": being_name,
                "language": language,
                "text": text,
                "audio_path": str(wav_file.absolute()),
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "quality_score": quality_score,
                "serve_count": 0,
                "last_served_at": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "path_registrar",
                "original_filename": wav_file.name,
            }

            new_phrases.append(phrase_entry)
            existing_ids.add(phrase_id)
            registered += 1

            logger.info(
                f"{'[DRY RUN] ' if dry_run else ''}"
                f"Registered: '{text}' → {wav_file.name} "
                f"({duration:.2f}s, {sample_rate}Hz)"
            )

        except Exception as e:
            error_msg = f"Error processing {wav_file.name}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    # Write to index unless dry run
    if not dry_run and new_phrases:
        index.setdefault("phrases", []).extend(new_phrases)
        _save_index(index)

    report = {
        "success": True,
        "dry_run": dry_run,
        "being_name": being_name,
        "language": language,
        "folder": str(folder_path),
        "wav_files_found": len(wav_files),
        "registered": registered,
        "skipped": skipped,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        f"Registration complete — "
        f"{registered} registered, {skipped} skipped, {len(errors)} errors."
    )
    return report


def register_single_file(
    wav_path: str,
    being_name: str,
    language: str,
    text: str,
    quality_score: float = 1.0
) -> dict:
    """
    Register a single WAV file into the express index.
    Use this when you want precise control over the phrase text.

    Args:
        wav_path:      Absolute path to the WAV file.
        being_name:    Name of the being.
        language:      BCP-47 language code.
        text:          Exact phrase text this audio represents.
        quality_score: Initial quality score.

    Returns:
        Registration result dict.
    """
    wav = Path(wav_path)

    if not wav.exists():
        logger.error(f"WAV file not found: {wav_path}")
        return {"success": False, "error": f"File not found: {wav_path}"}

    index = _load_index()
    existing_ids = {p["phrase_id"] for p in index.get("phrases", [])}

    phrase_id = _make_phrase_id(text, being_name, language)

    if phrase_id in existing_ids:
        logger.info(f"Phrase already registered: '{text}' for {being_name}")
        return {
            "success": True,
            "registered": False,
            "message": "Already registered.",
            "phrase_id": phrase_id
        }

    duration    = _get_wav_duration(wav)
    sample_rate = _get_wav_sample_rate(wav)

    phrase_entry = {
        "phrase_id": phrase_id,
        "being_name": being_name,
        "language": language,
        "text": text,
        "audio_path": str(wav.absolute()),
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "quality_score": quality_score,
        "serve_count": 0,
        "last_served_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "manual_registration",
        "original_filename": wav.name,
    }

    index.setdefault("phrases", []).append(phrase_entry)
    _save_index(index)

    logger.info(
        f"Single file registered: '{text}' → {wav.name} "
        f"for {being_name} [{language}]"
    )
    return {
        "success": True,
        "registered": True,
        "phrase_id": phrase_id,
        "duration_seconds": duration,
        "sample_rate": sample_rate,
    }


def show_index_report() -> None:
    """
    Print a human-readable report of everything in the express index.
    Run this from the terminal to see what's registered.
    """
    index = _load_index()
    phrases = index.get("phrases", [])

    print("\n" + "="*60)
    print("  CHRISTMAN VOICE CENTER — EXPRESS INDEX REPORT")
    print("="*60)
    print(f"  Total phrases registered: {len(phrases)}")
    print(f"  Last updated: {index.get('meta', {}).get('last_updated', 'unknown')}")
    print("="*60)

    by_being = {}
    for p in phrases:
        by_being.setdefault(p["being_name"], []).append(p)

    for being, being_phrases in sorted(by_being.items()):
        print(f"\n  {being} ({len(being_phrases)} phrases)")
        for p in being_phrases:
            print(
                f"    [{p['language']}] '{p['text']}' "
                f"→ {Path(p['audio_path']).name} "
                f"({p['duration_seconds']}s)"
            )

    print("\n" + "="*60 + "\n")


# ---------------------------------------------------------------------------
# CLI — run directly from terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Christman Voice Center — Path Registrar"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Register a folder
    folder_cmd = subparsers.add_parser("folder", help="Register all WAV files in a folder")
    folder_cmd.add_argument("folder", help="Path to audio folder")
    folder_cmd.add_argument("being", help="Being name (e.g. AlphaVox)")
    folder_cmd.add_argument("--language", default="en-US", help="BCP-47 language code")
    folder_cmd.add_argument("--quality", type=float, default=1.0)
    folder_cmd.add_argument("--dry-run", action="store_true")

    # Register a single file
    file_cmd = subparsers.add_parser("file", help="Register a single WAV file")
    file_cmd.add_argument("wav_path", help="Path to WAV file")
    file_cmd.add_argument("being", help="Being name")
    file_cmd.add_argument("text", help="Phrase text")
    file_cmd.add_argument("--language", default="en-US")
    file_cmd.add_argument("--quality", type=float, default=1.0)

    # Show report
    subparsers.add_parser("report", help="Show full index report")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.command == "folder":
        result = register_audio_folder(
            folder_path=args.folder,
            being_name=args.being,
            language=args.language,
            quality_score=args.quality,
            dry_run=args.dry_run
        )
        print(json.dumps(result, indent=2))

    elif args.command == "file":
        result = register_single_file(
            wav_path=args.wav_path,
            being_name=args.being,
            language=args.language,
            text=args.text,
            quality_score=args.quality
        )
        print(json.dumps(result, indent=2))

    elif args.command == "report":
        show_index_report()

    else:
        parser.print_help()
