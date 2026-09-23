"""حد المعدل داخل العملية (نافذة منزلقة).

يكفي لخادم بعملية واحدة وللاختبارات. في النشر متعدد العمليات يُطبَّق الحد أيضًا في nginx
(15-deployment)، ويُستبدل هذا المخزن بـ Redis عند إضافة Celery/Redis.
"""
import threading
import time
from collections import defaultdict, deque

from app.core.errors import DomainError


class RateLimited(DomainError):
    status_code = 429
    code = "RATE_LIMITED"


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window - (now - q[0])) + 1
                raise RateLimited("عدد كبير من المحاولات، حاول لاحقًا.", details={"retry_after": retry})
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
