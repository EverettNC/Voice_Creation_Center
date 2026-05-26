"""
================================================================================
FILE: voice_cache_manager.py
PROJECT: Christman Voice Creation Center — Helper Suite
AUTHOR: The Christman AI Project | Luma Cognify AI
--------------------------------------------------------------------------------
PURPOSE:
    LRU in-memory cache for loaded voice packs.
    Keeps the most-used packs hot in memory so the engine
    never has to hit disk twice for the same pack.
    Supports offline-first operation.
================================================================================
"""

import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("christman.voice_cache_manager")

DEFAULT_MAX_SIZE = 20  # Max packs in memory at once


class VoiceCacheManager:
    """
    LRU cache for voice packs.
    Evicts least-recently-used packs when max size is reached.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self.max_size = max_size
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._hit_counts: dict[str, int] = {}
        self._miss_counts: dict[str, int] = {}

    def get(self, pack_id: str) -> Optional[dict]:
        """
        Get a pack from cache.
        Moves it to most-recently-used position on hit.
        Returns None on miss.
        """
        if pack_id in self._cache:
            self._cache.move_to_end(pack_id)
            self._hit_counts[pack_id] = self._hit_counts.get(pack_id, 0) + 1
            logger.debug(f"Cache hit: {pack_id}")
            return self._cache[pack_id]

        self._miss_counts[pack_id] = self._miss_counts.get(pack_id, 0) + 1
        logger.debug(f"Cache miss: {pack_id}")
        return None

    def put(self, pack_id: str, pack_data: dict) -> None:
        """
        Store a pack in cache.
        Evicts LRU pack if at capacity.
        """
        if pack_id in self._cache:
            self._cache.move_to_end(pack_id)
            self._cache[pack_id] = pack_data
            return

        if len(self._cache) >= self.max_size:
            evicted_id, _ = self._cache.popitem(last=False)
            logger.info(f"Cache evicted (LRU): {evicted_id}")

        self._cache[pack_id] = pack_data
        logger.debug(f"Cache stored: {pack_id}")

    def evict(self, pack_id: str) -> bool:
        """
        Manually evict a pack — called when registry detects degradation
        and sends the pack for refresh.
        """
        if pack_id in self._cache:
            del self._cache[pack_id]
            logger.info(f"Pack evicted for refresh: {pack_id}")
            return True
        return False

    def clear(self) -> None:
        """Clear entire cache."""
        self._cache.clear()
        logger.info("Voice cache cleared.")

    def stats(self) -> dict:
        """Cache statistics for Brockston dashboard."""
        total_hits = sum(self._hit_counts.values())
        total_misses = sum(self._miss_counts.values())
        total = total_hits + total_misses
        return {
            "cached_packs": len(self._cache),
            "max_size": self.max_size,
            "pack_ids": list(self._cache.keys()),
            "total_hits": total_hits,
            "total_misses": total_misses,
            "hit_rate": round(total_hits / total, 3) if total > 0 else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
