"""
================================================================================
FILE: voice_loader.py
PROJECT: Christman Voice Creation Center — Helper Suite
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    Fetches voice packs from internal inventory.
    Handles all path logic so voice_engine.py stays lean.
    All pathways internal. Nothing reaches outside the center.
================================================================================
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_loader")

PACKS_DIR = Path(__file__).parent.parent / "packs"
INVENTORY_DIR = Path(__file__).parent.parent / "inventory"
INDEX_FILE = INVENTORY_DIR / "__index__.json"


def load_pack(pack_id: str) -> Optional[dict]:
    """
    Load a voice pack by ID from internal inventory.
    Returns the pack manifest dict, or None if not found.
    Never raises — always returns or logs.
    """
    pack_path = PACKS_DIR / pack_id
    manifest = pack_path / "manifest.json"

    if not pack_path.exists():
        logger.error(f"Pack directory not found: {pack_id}")
        return None

    if not manifest.exists():
        logger.error(f"Pack manifest missing: {pack_id}")
        return None

    try:
        with open(manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Pack loaded: {pack_id}")
        return data
    except Exception as e:
        logger.error(f"Failed to load pack {pack_id}: {e}")
        return None


def list_packs(being: str = None, language: str = None) -> list[dict]:
    """
    List all available packs from the master index.
    Optionally filter by being name or language code.
    """
    if not INDEX_FILE.exists():
        logger.warning("Master index not found. Run voice_registry to build it.")
        return []

    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            index = json.load(f)

        packs = index.get("packs", [])

        if being:
            packs = [p for p in packs if p.get("being_name", "").lower() == being.lower()]

        if language:
            packs = [p for p in packs if p.get("language", "").lower() == language.lower()]

        return packs

    except Exception as e:
        logger.error(f"Failed to read index: {e}")
        return []


def pack_exists(pack_id: str) -> bool:
    """Quick existence check without loading the full pack."""
    return (PACKS_DIR / pack_id / "manifest.json").exists()


def get_pack_path(pack_id: str) -> Optional[Path]:
    """Return the internal path to a pack directory."""
    path = PACKS_DIR / pack_id
    return path if path.exists() else None
