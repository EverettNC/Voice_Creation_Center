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
    in each being's own folder.

    This utility scans a being's audio directory, reads every WAV file,
    and registers its path into the express index. No copying.
    No transferring. Just a clean internal reference that says
    "when you need this phrase, it lives HERE."

    One command. Every WAV file in that folder is live.

    It is also how the express index is built: the index ships empty, and
    each being registers its own audio with this command.

CARDINAL RULE 13: Files are registered only if they actually exist.
    No phantom entries. No broken references.

WHAT CHANGED AND WHY (2026-10-28)
    A file that could not be read was registered anyway, with a duration of
    0.0 and a sample rate of 22050 Hz that nobody measured, and every entry
    carried a quality_score of 1.0 that nobody measured either. Entries are
    now written by voice_express.register_phrase, the one writer: duration
    and sample rate are read from the file, an unreadable or empty file is
    refused and listed in the report, and no quality is recorded. When the
    text comes from a file's name (no text map given), the entry says so
    (text_from "filename").
================================================================================
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_path_registrar")

import sys as _sys
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
from vcc_paths import VCCPaths  # one base directory for the whole VCC
import voice_express  # the one writer of express index entries


def _make_phrase_id(text: str, being_name: str, language: str) -> str:
    """Generate a stable unique ID for a phrase (voice_express.make_phrase_id)."""
    return voice_express.make_phrase_id(text, being_name, language)


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


def _open_express(base_dir: Optional[str]) -> voice_express.VoiceExpress:
    express = voice_express.VoiceExpress(base_dir=base_dir)
    if not express.load():
        raise RuntimeError(f"express index could not be loaded: {express.index_path}")
    return express


def register_audio_folder(
    folder_path: str,
    being_name: str,
    language: str = "en-US",
    text_map: Optional[dict] = None,
    dry_run: bool = False,
    base_dir: Optional[str] = None,
) -> dict:
    """
    Scan a folder for WAV files and register every one into the
    express index. The files stay exactly where they are.

    Args:
        folder_path:   Path to the being's audio folder.
        being_name:    Name of the being the audio belongs to.
        language:      BCP-47 language code (default: 'en-US').
        text_map:      Optional dict mapping filename → phrase text.
                       If not provided, text is inferred from filename,
                       and each entry records text_from "filename".
                       Example: {"i_love_you.wav": "I love you"}
        dry_run:       If True, scans, measures and reports without writing anything.
        base_dir:      The VCC base directory (vcc_paths).

    Returns:
        Registration report dict with counts and any errors.
    """
    folder = Path(folder_path).expanduser()

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
    wav_files = sorted(set(folder.glob("*.wav")) | set(folder.glob("*.WAV")))

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

    express = _open_express(base_dir)
    registered = 0
    skipped = 0
    errors = []

    for wav_file in wav_files:
        if text_map and wav_file.name in text_map:
            text, text_from = text_map[wav_file.name], "given"
        else:
            text, text_from = _infer_text_from_filename(wav_file.name), "filename"

        if not text:
            logger.warning(f"Could not determine text for {wav_file.name} — skipping.")
            skipped += 1
            continue

        if express.is_cached(text, being_name, language):
            logger.debug(f"Already registered: '{text}' for {being_name} — skipping.")
            skipped += 1
            continue

        try:
            duration, sample_rate = voice_express.measure_wav(wav_file)
            if not dry_run:
                express.register_phrase(
                    text=text, being_name=being_name, language=language,
                    audio_path=str(wav_file), source="path_registrar",
                    text_from=text_from, original_filename=wav_file.name,
                )
            registered += 1
            logger.info(
                f"{'[DRY RUN] ' if dry_run else ''}"
                f"Registered: '{text}' → {wav_file.name} "
                f"({duration:.2f}s, {sample_rate}Hz)"
            )
        except voice_express.UnmeasurableAudio as e:
            error_msg = f"Not registered, {wav_file.name}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

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
    base_dir: Optional[str] = None,
    source: str = "path_registrar",
) -> dict:
    """
    Register a single WAV file into the express index.
    Use this when you want precise control over the phrase text.

    Args:
        wav_path:      Path to the WAV file.
        being_name:    Name of the being.
        language:      BCP-47 language code.
        text:          Exact phrase text this audio represents.
        base_dir:      The VCC base directory (vcc_paths).
        source:        Where the audio came from (voice_express.SOURCES);
                       the studio passes "studio_export".

    Returns:
        Registration result dict.
    """
    wav = Path(wav_path).expanduser()

    if not wav.exists():
        logger.error(f"WAV file not found: {wav_path}")
        return {"success": False, "error": f"File not found: {wav_path}"}

    express = _open_express(base_dir)
    phrase_id = _make_phrase_id(text, being_name, language)

    if express.is_cached(text, being_name, language):
        logger.info(f"Phrase already registered: '{text}' for {being_name}")
        return {
            "success": True,
            "registered": False,
            "message": "Already registered.",
            "phrase_id": phrase_id
        }

    try:
        phrase = express.register_phrase(
            text=text, being_name=being_name, language=language,
            audio_path=str(wav), source=source, original_filename=wav.name,
        )
    except voice_express.UnmeasurableAudio as e:
        logger.error(f"Not registered: {e}")
        return {"success": False, "error": str(e)}

    logger.info(
        f"Single file registered: '{text}' → {wav.name} "
        f"for {being_name} [{language}]"
    )
    return {
        "success": True,
        "registered": True,
        "phrase_id": phrase.phrase_id,
        "duration_seconds": phrase.duration_seconds,
        "sample_rate": phrase.sample_rate,
    }


def show_index_report(base_dir: Optional[str] = None) -> None:
    """
    Print a human-readable report of everything in the express index.
    Run this from the terminal to see what's registered.
    """
    express = _open_express(base_dir)
    phrases = [p for being in sorted({p.being_name for p in express._phrases.values()})
               for p in express.get_phrases_for_being(being)]

    print("\n" + "="*60)
    print("  CHRISTMAN VOICE CENTER — EXPRESS INDEX REPORT")
    print("="*60)
    print(f"  Index: {express.index_path}")
    print(f"  Total phrases registered: {len(phrases)}")
    print(f"  Entries left out (file missing or changed): {len(express.load_problems)}")
    print("="*60)

    by_being = {}
    for p in phrases:
        by_being.setdefault(p.being_name, []).append(p)

    for being, being_phrases in sorted(by_being.items()):
        print(f"\n  {being} ({len(being_phrases)} phrases)")
        for p in being_phrases:
            print(
                f"    [{p.language}] '{p.text}' "
                f"→ {Path(p.audio_path).name} "
                f"({p.duration_seconds}s, {p.sample_rate} Hz, {p.source}"
                f"{', text from filename' if p.text_from == 'filename' else ''})"
            )
    for problem in express.load_problems:
        print(f"\n  LEFT OUT: {problem}")

    print("\n" + "="*60 + "\n")


# ---------------------------------------------------------------------------
# CLI — run directly from terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Christman Voice Center — Path Registrar (builds the express index)"
    )
    parser.add_argument("--base-dir", default=None,
                        help="The VCC's base directory (default: $VCC_HOME, else this repo)")
    subparsers = parser.add_subparsers(dest="command")

    # Register a folder
    folder_cmd = subparsers.add_parser("folder", help="Register all WAV files in a folder")
    folder_cmd.add_argument("folder", help="Path to audio folder")
    folder_cmd.add_argument("being", help="Name of the being the audio belongs to")
    folder_cmd.add_argument("--language", default="en-US", help="BCP-47 language code")
    folder_cmd.add_argument("--text-map", default=None,
                            help="JSON file mapping filename to the exact phrase text")
    folder_cmd.add_argument("--dry-run", action="store_true")

    # Register a single file
    file_cmd = subparsers.add_parser("file", help="Register a single WAV file")
    file_cmd.add_argument("wav_path", help="Path to WAV file")
    file_cmd.add_argument("being", help="Name of the being the audio belongs to")
    file_cmd.add_argument("text", help="Phrase text")
    file_cmd.add_argument("--language", default="en-US")

    # Show report
    subparsers.add_parser("report", help="Show full index report")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.command == "folder":
        text_map = json.loads(Path(args.text_map).read_text(encoding="utf-8")) if args.text_map else None
        result = register_audio_folder(
            folder_path=args.folder,
            being_name=args.being,
            language=args.language,
            text_map=text_map,
            dry_run=args.dry_run,
            base_dir=args.base_dir,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "file":
        result = register_single_file(
            wav_path=args.wav_path,
            being_name=args.being,
            language=args.language,
            text=args.text,
            base_dir=args.base_dir,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "report":
        show_index_report(args.base_dir)

    else:
        parser.print_help()
