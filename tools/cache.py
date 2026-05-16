"""
Simple TTL cache for tool outputs.

PeeringDB rate-limits aggressively. RPKI lookups are slow. The agent often
calls the same lookup repeatedly within a session. Cache the result with a
short TTL so re-asks within the same task are free.

In-memory only — process-scoped. Real deployments would use Redis with a
shared TTL across agent replicas.
"""

import time
from functools import wraps
from typing import Callable


def ttl_cache(seconds: int = 300):
    """Decorator: cache function result for `seconds` keyed on positional args."""
    def decorator(fn: Callable) -> Callable:
        store: dict[tuple, tuple[float, object]] = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            if key in store:
                stored_at, value = store[key]
                if now - stored_at < seconds:
                    return value
            value = fn(*args, **kwargs)
            store[key] = (now, value)
            return value

        wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return wrapper
    return decorator
