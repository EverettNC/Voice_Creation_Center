"""
================================================================================
FILE: vcc_paths.py
PROJECT: Christman Voice Creation Center
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    The one place every VCC path comes from.

    WHAT CHANGED AND WHY (added 2026-10-28)
    The VCC was split in two. Voice_registry.py, voice_express.py and
    voice_ingestor.py sit at the repo root but built their paths from
    Path(__file__).parent.parent, so they read and wrote packs/, inventory/,
    express/, incoming/ and the rest in the folder ABOVE the repo, while
    engines/voice_loader.py, engines/voice_path_registrar.py and the studio
    used the repo itself. The two halves never met: the loader could not see
    what the registry wrote, and three writers kept two different express
    indexes.

    Now there is one base directory. It is, in order:
      1. the base_dir a caller passes in, else
      2. the folder named by $VCC_HOME, else
      3. this repo.
    Every folder below is a fixed name under it, the same for every module.
================================================================================
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

#: The repo this file sits in: the base when nothing else is given.
REPO_ROOT = Path(__file__).resolve().parent

#: Environment variable that moves the whole VCC's data somewhere else.
HOME_ENV = "VCC_HOME"


class VCCPaths:
    """Every folder and file the VCC reads or writes, under one base directory."""

    def __init__(self, base_dir: Optional[Union[str, Path]] = None):
        chosen = base_dir if base_dir is not None else os.environ.get(HOME_ENV) or REPO_ROOT
        self.base = Path(chosen).expanduser().resolve()

        # Voice packs and the registry's inventory (Voice_registry.py, engines/voice_loader.py)
        self.packs = self.base / "packs"
        self.inventory = self.base / "inventory"
        self.inventory_index = self.inventory / "__index__.json"

        # The express lane (voice_express.py, engines/voice_path_registrar.py, voice_ingestor.py)
        self.express = self.base / "express"
        self.express_index = self.express / "express_index.json"
        self.phrase_cache = self.express / "phrase_cache"
        self.priority_queue = self.express / "priority_queue"

        # The ingestor's front door (voice_ingestor.py)
        self.incoming = self.base / "incoming"
        self.processed = self.base / "processed"
        self.failed = self.base / "failed"
        self.ingested_phrases = self.packs / "phrases"

        # Audit logs (engines/voice_logger.py)
        self.logs = self.base / "logs"

        # The studio's raw takes (studio/live_studio.py)
        self.studio_raw = self.base / "studio" / "raw"

    def __repr__(self) -> str:
        return f"VCCPaths(base={str(self.base)!r})"


def vcc_paths(base_dir: Optional[Union[str, Path]] = None) -> VCCPaths:
    """The VCC's paths under `base_dir`, else $VCC_HOME, else this repo."""
    return VCCPaths(base_dir)
