"""
================================================================================
FILE: voice_registry.py
PROJECT: Christman Voice Creation Center
AUTHOR: The Christman AI Project | Luma Cognify AI
CREATED: 2026
PATENT PENDING: TCAP-2026-001 | TCAP-2026-002
--------------------------------------------------------------------------------
PURPOSE:
    The Quality Control Manager of the Christman Voice Creation Center.

    The registry is the big honcho. It does three things and it does them
    without flinching:

    1. INVENTORY MANAGEMENT
       Knows every voice pack inside the center. Who owns it, what language,
       what dialect, what state it's in. All pathways are internal — nothing
       reaches outside the Voice Creation Center.

    2. LEARNING GATEWAY
       When a being wants a new language or dialect (AlphaVox wants French,
       Derek wants Spanish, Giuseppe wants Italian), the registry spins up
       the request, routes it through the factory mill, and the being comes
       out the other side speaking that language in THEIR voice — not a
       generic voice. Theirs. Retrained.

    3. DEGRADATION DETECTION & AUTO-REFRESH
       Tracks voice pack health over time. Monitors usage patterns, phoneme
       accuracy drift, and quality scores. When a pack shows early signs of
       degradation, it pulls it back into the factory for a refresh
       BEFORE the user ever notices anything is wrong.
       Proactive. Not reactive.

ALL PATHWAYS ARE INTERNAL.
    No external API calls. No reaching into individual being codebases.
    Voice packs live in christman_voice_center/packs/ only.
    The center owns the voices. The beings call the center.

CARDINAL RULE 13: No stubs. No fake monitoring. No pretend health scores.
================================================================================
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("christman.voice_registry")

# ---------------------------------------------------------------------------
# Internal paths — all routing stays inside the Voice Creation Center
# ---------------------------------------------------------------------------

REGISTRY_ROOT = Path(__file__).parent.parent  # christman_voice_center/
PACKS_DIR     = REGISTRY_ROOT / "packs"
INVENTORY_DIR = REGISTRY_ROOT / "inventory"
INDEX_FILE    = INVENTORY_DIR / "__index__.json"

# Degradation threshold — when quality score drops below this, auto-refresh triggers
DEGRADATION_THRESHOLD = 0.72

# Usage count that triggers a proactive quality check
USAGE_CHECK_INTERVAL = 500


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PackStatus(str, Enum):
    HEALTHY    = "healthy"       # Clean, crisp, within quality bounds
    MONITORING = "monitoring"    # Showing early drift — watching closely
    DEGRADED   = "degraded"      # Below threshold — queued for refresh
    REFRESHING = "refreshing"    # Currently in factory for retraining
    NEW        = "new"           # Just created, not yet baselined
    RETIRED    = "retired"       # Replaced by newer version


class LearnRequestStatus(str, Enum):
    QUEUED     = "queued"
    PROCESSING = "processing"
    COMPLETE   = "complete"
    FAILED     = "failed"


# ---------------------------------------------------------------------------
# VoicePackRecord — The full health record for one voice pack
# ---------------------------------------------------------------------------

@dataclass
class VoicePackRecord:
    """
    Complete health record for a single voice pack.
    This is what the registry stores and monitors for every pack.
    """

    # Identity
    pack_id: str                          # e.g. "alphavox_default_en_us"
    being_name: str                       # e.g. "AlphaVox"
    language: str                         # BCP-47 e.g. "en-US"
    dialect: Optional[str] = None        # e.g. "southern_us", "parisian_fr"
    version: str = "1.0.0"
    status: PackStatus = PackStatus.NEW

    # Internal path — always relative to packs/ directory
    internal_path: str = ""               # e.g. "alphavox/en_us/alphavox_default_en_us.cvp"

    # Quality tracking
    quality_score: float = 1.0           # 0.0 → 1.0. Below 0.72 triggers refresh.
    baseline_score: float = 1.0          # Score at time of creation/last refresh
    phoneme_accuracy: float = 1.0        # Phoneme labeler accuracy score
    affect_consistency: float = 1.0      # How consistently affect is rendered

    # Usage tracking
    total_uses: int = 0
    uses_since_last_check: int = 0
    last_used_at: Optional[str] = None   # ISO 8601

    # Lifecycle timestamps
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_refreshed_at: Optional[str] = None
    next_check_due_at: Optional[str] = None

    # Factory history
    refresh_count: int = 0
    learn_history: list = field(default_factory=list)  # Languages learned over time

    # Metadata
    offline_capable: bool = True
    population: str = ""                 # e.g. "dementia_care", "aac_nonverbal"
    notes: str = ""

    def is_degraded(self) -> bool:
        return self.quality_score < DEGRADATION_THRESHOLD

    def needs_check(self) -> bool:
        return self.uses_since_last_check >= USAGE_CHECK_INTERVAL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "VoicePackRecord":
        data["status"] = PackStatus(data.get("status", PackStatus.NEW.value))
        return cls(**data)


# ---------------------------------------------------------------------------
# LearnRequest — A being requesting a new language/dialect
# ---------------------------------------------------------------------------

@dataclass
class LearnRequest:
    """
    A being has requested to learn a new language or dialect.
    The registry queues this, routes it to the factory, and tracks it.
    """
    request_id: str
    being_name: str
    source_pack_id: str          # The pack that will be retrained
    target_language: str         # BCP-47 language tag
    target_dialect: Optional[str] = None
    status: LearnRequestStatus = LearnRequestStatus.QUEUED
    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    new_pack_id: Optional[str] = None    # Set when complete
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# VoiceRegistry — The Quality Control Manager
# ---------------------------------------------------------------------------

class VoiceRegistry:
    """
    The Quality Control Manager of the Christman Voice Creation Center.

    Single source of truth for all voice packs across the family.
    All pathways are internal — packs live in christman_voice_center/packs/ only.

    Usage:
        registry = VoiceRegistry()
        registry.load()

        # Get a pack
        record = registry.get_pack("alphavox_default_en_us")

        # Register a new pack
        registry.register_pack(record)

        # Request a language learning
        req = registry.request_language_learn("AlphaVox", "alphavox_default_en_us", "fr-FR")

        # Run health check cycle
        registry.run_health_cycle()
    """

    def __init__(self):
        self._packs: dict[str, VoicePackRecord] = {}
        self._learn_queue: list[LearnRequest] = []
        self._loaded = False

        # Ensure internal directories exist
        PACKS_DIR.mkdir(parents=True, exist_ok=True)
        INVENTORY_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Load & Save
    # -----------------------------------------------------------------------

    def load(self) -> bool:
        """
        Load the registry from __index__.json.
        Creates a fresh index if none exists yet.
        Returns True if successful.
        """
        if not INDEX_FILE.exists():
            logger.info("No index found — starting fresh registry.")
            self._loaded = True
            self._save()
            return True

        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for pack_data in data.get("packs", []):
                record = VoicePackRecord.from_dict(pack_data)
                self._packs[record.pack_id] = record

            for req_data in data.get("learn_queue", []):
                req_data["status"] = LearnRequestStatus(req_data.get("status", "queued"))
                self._learn_queue.append(LearnRequest(**req_data))

            self._loaded = True
            logger.info(
                f"Registry loaded — {len(self._packs)} packs, "
                f"{len(self._learn_queue)} learn requests in queue."
            )
            return True

        except Exception as e:
            logger.error(f"Registry load failed: {e}", exc_info=True)
            return False

    def _save(self) -> bool:
        """
        Persist the registry to __index__.json.
        Called automatically after any mutation.
        """
        try:
            data = {
                "meta": {
                    "version": "1.0.0",
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "total_packs": len(self._packs),
                    "project": "Christman Voice Creation Center",
                    "patent_pending": "TCAP-2026-001 | TCAP-2026-002"
                },
                "packs": [r.to_dict() for r in self._packs.values()],
                "learn_queue": [r.to_dict() for r in self._learn_queue]
            }
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True

        except Exception as e:
            logger.error(f"Registry save failed: {e}", exc_info=True)
            return False

    # -----------------------------------------------------------------------
    # Pack Management
    # -----------------------------------------------------------------------

    def register_pack(self, record: VoicePackRecord) -> bool:
        """
        Register a new voice pack in the registry.
        Sets baseline quality score and marks it healthy.
        """
        self._require_loaded()

        if record.pack_id in self._packs:
            logger.warning(
                f"Pack '{record.pack_id}' already registered. "
                f"Use update_pack_quality() to update scores."
            )
            return False

        record.status = PackStatus.HEALTHY
        record.baseline_score = record.quality_score
        self._packs[record.pack_id] = record
        self._save()

        logger.info(f"Registered pack: {record.pack_id} for {record.being_name} [{record.language}]")
        return True

    def get_pack(self, pack_id: str) -> Optional[VoicePackRecord]:
        """
        Retrieve a pack record by ID.
        Returns None if not found — never raises.
        """
        self._require_loaded()
        pack = self._packs.get(pack_id)
        if pack is None:
            logger.warning(f"Pack '{pack_id}' not found in registry.")
        return pack

    def get_packs_for_being(self, being_name: str) -> list[VoicePackRecord]:
        """
        Get all voice packs registered for a specific being.
        """
        self._require_loaded()
        return [
            r for r in self._packs.values()
            if r.being_name.lower() == being_name.lower()
        ]

    def get_packs_by_language(self, language: str) -> list[VoicePackRecord]:
        """
        Get all packs available in a specific language.
        """
        self._require_loaded()
        return [
            r for r in self._packs.values()
            if r.language.lower() == language.lower()
        ]

    def record_usage(self, pack_id: str) -> None:
        """
        Record that a pack was used. Increments usage counters
        and triggers a health check if usage interval is reached.
        """
        self._require_loaded()
        record = self._packs.get(pack_id)
        if not record:
            logger.warning(f"record_usage: pack '{pack_id}' not found.")
            return

        record.total_uses += 1
        record.uses_since_last_check += 1
        record.last_used_at = datetime.now(timezone.utc).isoformat()

        if record.needs_check():
            logger.info(f"Pack '{pack_id}' hit usage threshold — running quality check.")
            self._check_pack_health(record)

        self._save()

    # -----------------------------------------------------------------------
    # Quality Control — The Heart of the Registry
    # -----------------------------------------------------------------------

    def update_pack_quality(
        self,
        pack_id: str,
        quality_score: float,
        phoneme_accuracy: Optional[float] = None,
        affect_consistency: Optional[float] = None
    ) -> None:
        """
        Update quality metrics for a pack.
        Automatically triggers degradation handling if score drops
        below DEGRADATION_THRESHOLD.

        This is called by the factory after each training run,
        and by the health monitor during scheduled checks.
        """
        self._require_loaded()
        record = self._packs.get(pack_id)
        if not record:
            logger.error(f"update_pack_quality: pack '{pack_id}' not found.")
            return

        record.quality_score = quality_score
        if phoneme_accuracy is not None:
            record.phoneme_accuracy = phoneme_accuracy
        if affect_consistency is not None:
            record.affect_consistency = affect_consistency

        # Status logic
        if quality_score >= DEGRADATION_THRESHOLD + 0.1:
            record.status = PackStatus.HEALTHY
        elif quality_score >= DEGRADATION_THRESHOLD:
            record.status = PackStatus.MONITORING
            logger.info(
                f"Pack '{pack_id}' entering MONITORING — "
                f"quality {quality_score:.3f} approaching threshold."
            )
        else:
            record.status = PackStatus.DEGRADED
            logger.warning(
                f"Pack '{pack_id}' DEGRADED — quality {quality_score:.3f} "
                f"below threshold {DEGRADATION_THRESHOLD}. Queuing for refresh."
            )
            self._queue_refresh(record)

        self._save()

    def _check_pack_health(self, record: VoicePackRecord) -> None:
        """
        Run a health check on a pack.
        Resets the usage counter and evaluates current quality.
        If degradation trend is detected, status moves to MONITORING.

        NOTE: Actual acoustic analysis is delegated to the factory's
        voice_validator module. This method handles the registry side —
        tracking, status transitions, and refresh queuing.
        """
        record.uses_since_last_check = 0
        record.next_check_due_at = datetime.now(timezone.utc).isoformat()

        # Drift detection — if quality has dropped more than 15% from baseline
        drift = record.baseline_score - record.quality_score
        if drift > 0.15 and record.status == PackStatus.HEALTHY:
            record.status = PackStatus.MONITORING
            logger.info(
                f"Pack '{record.pack_id}' drift detected: "
                f"baseline {record.baseline_score:.3f} → current {record.quality_score:.3f}. "
                f"Moved to MONITORING."
            )

    def _queue_refresh(self, record: VoicePackRecord) -> None:
        """
        Queue a degraded pack for factory refresh.
        The factory mill will retrain it and call update_pack_quality()
        when complete, which will restore it to HEALTHY status.
        """
        if record.status == PackStatus.REFRESHING:
            logger.info(f"Pack '{record.pack_id}' already refreshing — skipping duplicate queue.")
            return

        record.status = PackStatus.REFRESHING
        logger.info(f"Pack '{record.pack_id}' queued for factory refresh.")
        # Factory picks this up via get_packs_needing_refresh()

    def get_packs_needing_refresh(self) -> list[VoicePackRecord]:
        """
        Returns all packs currently queued for factory refresh.
        Called by the factory mill on its processing cycle.
        """
        self._require_loaded()
        return [
            r for r in self._packs.values()
            if r.status in (PackStatus.DEGRADED, PackStatus.REFRESHING)
        ]

    def mark_refresh_complete(self, pack_id: str, new_quality_score: float) -> None:
        """
        Called by the factory when a pack refresh is complete.
        Resets baseline, restores health, increments refresh count.
        """
        self._require_loaded()
        record = self._packs.get(pack_id)
        if not record:
            logger.error(f"mark_refresh_complete: pack '{pack_id}' not found.")
            return

        record.quality_score = new_quality_score
        record.baseline_score = new_quality_score
        record.status = PackStatus.HEALTHY
        record.refresh_count += 1
        record.last_refreshed_at = datetime.now(timezone.utc).isoformat()
        record.uses_since_last_check = 0

        logger.info(
            f"Pack '{pack_id}' refresh complete. "
            f"New quality: {new_quality_score:.3f}. "
            f"Total refreshes: {record.refresh_count}."
        )
        self._save()

    def run_health_cycle(self) -> dict:
        """
        Run a full health cycle across all packs.
        Call this on a schedule (e.g. daily via cron or Brockston daemon).

        Returns a summary report of the cycle.
        """
        self._require_loaded()
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_packs": len(self._packs),
            "healthy": 0,
            "monitoring": 0,
            "degraded": 0,
            "refreshing": 0,
            "queued_for_refresh": [],
        }

        for record in self._packs.values():
            if record.needs_check():
                self._check_pack_health(record)

            status_key = record.status.value
            if status_key in report:
                report[status_key] += 1

            if record.status in (PackStatus.DEGRADED, PackStatus.REFRESHING):
                report["queued_for_refresh"].append(record.pack_id)

        self._save()
        logger.info(
            f"Health cycle complete — "
            f"{report['healthy']} healthy, "
            f"{report['monitoring']} monitoring, "
            f"{report['degraded']} degraded, "
            f"{report['refreshing']} refreshing."
        )
        return report

    # -----------------------------------------------------------------------
    # Language Learning Gateway
    # -----------------------------------------------------------------------

    def request_language_learn(
        self,
        being_name: str,
        source_pack_id: str,
        target_language: str,
        target_dialect: Optional[str] = None
    ) -> LearnRequest:
        """
        A being wants to learn a new language or dialect.

        The registry creates a LearnRequest, queues it, and the factory
        picks it up, retrains the source pack in the target language,
        and returns a new pack in THAT BEING'S VOICE — not a generic voice.

        Example:
            AlphaVox wants to speak French.
            registry.request_language_learn(
                being_name="AlphaVox",
                source_pack_id="alphavox_default_en_us",
                target_language="fr-FR"
            )
            → Factory retrains alphavox_default_en_us in fr-FR
            → New pack: alphavox_default_fr_fr
            → AlphaVox now speaks French in her own voice.
        """
        self._require_loaded()

        # Verify source pack exists
        source = self._packs.get(source_pack_id)
        if not source:
            raise ValueError(
                f"Source pack '{source_pack_id}' not found in registry. "
                f"Cannot initiate language learning without a source voice."
            )

        if source.status == PackStatus.DEGRADED:
            logger.warning(
                f"Source pack '{source_pack_id}' is degraded. "
                f"Recommend refreshing it before language learning for best results."
            )

        # Build the request
        request_id = (
            f"learn_{being_name.lower()}_{target_language.replace('-','_').lower()}"
            f"_{int(time.time())}"
        )
        req = LearnRequest(
            request_id=request_id,
            being_name=being_name,
            source_pack_id=source_pack_id,
            target_language=target_language,
            target_dialect=target_dialect,
            status=LearnRequestStatus.QUEUED
        )

        self._learn_queue.append(req)
        self._save()

        logger.info(
            f"Language learn queued: {being_name} → {target_language} "
            f"(source: {source_pack_id}, request: {request_id})"
        )
        return req

    def get_pending_learn_requests(self) -> list[LearnRequest]:
        """
        Returns all queued language learn requests.
        Called by the factory mill on its processing cycle.
        """
        return [
            r for r in self._learn_queue
            if r.status == LearnRequestStatus.QUEUED
        ]

    def mark_learn_complete(
        self,
        request_id: str,
        new_pack_id: str,
        new_pack_record: VoicePackRecord
    ) -> None:
        """
        Called by the factory when a language learn is complete.
        Registers the new pack and updates the request status.
        """
        for req in self._learn_queue:
            if req.request_id == request_id:
                req.status = LearnRequestStatus.COMPLETE
                req.completed_at = datetime.now(timezone.utc).isoformat()
                req.new_pack_id = new_pack_id

                # Add to learn history on source pack
                source = self._packs.get(req.source_pack_id)
                if source:
                    source.learn_history.append({
                        "language": req.target_language,
                        "dialect": req.target_dialect,
                        "new_pack_id": new_pack_id,
                        "completed_at": req.completed_at
                    })

                # Register the new pack
                self.register_pack(new_pack_record)

                logger.info(
                    f"Language learn complete: {req.being_name} → {req.target_language}. "
                    f"New pack: {new_pack_id}"
                )
                self._save()
                return

        logger.error(f"mark_learn_complete: request_id '{request_id}' not found.")

    # -----------------------------------------------------------------------
    # Registry Status
    # -----------------------------------------------------------------------

    def get_status_report(self) -> dict:
        """
        Full status report. Used by Brockston admin dashboard.
        """
        self._require_loaded()
        by_being = {}
        for record in self._packs.values():
            by_being.setdefault(record.being_name, []).append({
                "pack_id": record.pack_id,
                "language": record.language,
                "status": record.status.value,
                "quality_score": record.quality_score,
                "total_uses": record.total_uses,
                "refresh_count": record.refresh_count,
            })

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_packs": len(self._packs),
            "by_being": by_being,
            "pending_learn_requests": len(self.get_pending_learn_requests()),
            "packs_needing_refresh": len(self.get_packs_needing_refresh()),
        }

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError(
                "VoiceRegistry not loaded. Call registry.load() before any operation."
            )
