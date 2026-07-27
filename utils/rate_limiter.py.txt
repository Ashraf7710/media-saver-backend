import time
import threading
from typing import Dict
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_requests_per_minute: int = 30,
                 max_requests_per_hour: int = 200):
        self._per_minute = max_requests_per_minute
        self._per_hour = max_requests_per_hour
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()
        self._start_cleanup()

    def allow_request(self, client_id: str) -> bool:
        with self._lock:
            now = time.time()
            self._requests[client_id] = [
                t for t in self._requests[client_id] if now - t < 3600
            ]
            requests = self._requests[client_id]
            recent_minute = sum(1 for t in requests if now - t < 60)
            if recent_minute >= self._per_minute:
                return False
            if len(requests) >= self._per_hour:
                return False
            requests.append(now)
            return True

    def get_retry_after(self, client_id: str) -> int:
        with self._lock:
            now = time.time()
            requests = self._requests.get(client_id, [])
            if not requests:
                return 0
            minute_requests = [t for t in requests if now - t < 60]
            if len(minute_requests) >= self._per_minute:
                oldest = min(minute_requests)
                return max(1, int(60 - (now - oldest)))
            return 60

    def _start_cleanup(self):
        def cleanup():
            while True:
                time.sleep(1800)
                with self._lock:
                    now = time.time()
                    expired = [
                        ip for ip, times in self._requests.items()
                        if not times or now - max(times) > 7200
                    ]
                    for ip in expired:
                        del self._requests[ip]
        threading.Thread(target=cleanup, daemon=True).start()