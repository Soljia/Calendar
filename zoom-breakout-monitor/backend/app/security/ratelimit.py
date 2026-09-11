"""In-process sliding-window rate limiter.

Adequate for the single-instance self-hosted deployment this project targets.
Running multiple backend replicas would need a shared store (Redis); that is
called out in the README rather than pretended away here.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            return max(1, int(self.window - (now - bucket[0])) + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
