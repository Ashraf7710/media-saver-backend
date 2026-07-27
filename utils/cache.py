import time
import threading
from typing import Dict, Optional, Any
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

class SimpleCache:
    def __init__(self, max_size: int = 500, ttl_hours: float = 6):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_hours * 3600
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._start_cleanup_timer()

    def get(self, key: str) -> Optional[Dict]:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            entry = self._cache[key]
            if time.time() - entry["timestamp"] > self._ttl_seconds:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry["data"]

    def put(self, key: str, data: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = {"data": data, "timestamp": time.time()}
                return
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = {"data": data, "timestamp": time.time()}

    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cleanup_expired(self) -> None:
        with self._lock:
            now = time.time()
            expired = [
                key for key, entry in self._cache.items()
                if now - entry["timestamp"] > self._ttl_seconds
            ]
            for key in expired:
                del self._cache[key]

    def _start_cleanup_timer(self) -> None:
        def cleanup_loop():
            while True:
                time.sleep(3600)
                self._cleanup_expired()
        threading.Thread(target=cleanup_loop, daemon=True).start()