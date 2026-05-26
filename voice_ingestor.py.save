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
import time"""
================================================================================
FILE: voice_ingestor.py
PROJECT: Christman Voice Creation Center — Front Door
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------

from datetime import datetime,
