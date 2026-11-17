from collections import deque
from math import ceil
from threading import Lock
from time import monotonic


SETUP_CONNECTION_LIMIT = 10
SETUP_CONNECTION_WINDOW_SECONDS = 60
MAX_CLIENT_KEYS = 4096

_attempts: dict[str, deque[float]] = {}
_lock = Lock()


def setup_connection_retry_after(client: str) -> int:
    now = monotonic()
    with _lock:
        timestamps = _attempts.get(client)
        if timestamps is None:
            if len(_attempts) >= MAX_CLIENT_KEYS:
                expired = [key for key, values in _attempts.items() if not values or now - values[-1] >= SETUP_CONNECTION_WINDOW_SECONDS]
                for key in expired:
                    del _attempts[key]
            if len(_attempts) >= MAX_CLIENT_KEYS:
                oldest = min(_attempts, key=lambda key: _attempts[key][-1])
                del _attempts[oldest]
            timestamps = deque()
            _attempts[client] = timestamps

        while timestamps and now - timestamps[0] >= SETUP_CONNECTION_WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= SETUP_CONNECTION_LIMIT:
            return max(1, ceil(SETUP_CONNECTION_WINDOW_SECONDS - (now - timestamps[0])))

        timestamps.append(now)
        return 0
