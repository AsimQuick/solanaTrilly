# ---
# module: core.rate_limiter
# sprint: sprint-6
# story: US-24 AC-24.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: redis, time, logging, os
# ---
"""Redis token-bucket rate limiter — ported from solanaBilly (§6.3).

This limiter gates the on-demand score-time snapshot read (SnapshotFetcher.fetch).
The per-token-poll scheduler regime is RETIRED; the limiter remains to prevent
runaway on-demand REST reads if many tokens graduate in a short burst.

The Lua script executes atomically on Redis so there is no TOCTOU race when
multiple Celery workers compete for the same token bucket.

Key prefix changed from 'solanabilly:rate_limiter' → 'solanatrilly:rate_limiter'
to isolate this project's bucket from any solanaBilly bucket on the same Redis.
"""
import logging
import os
import time

import redis as redis_lib

logger = logging.getLogger(__name__)

DEFAULT_RATE = 40.0
DEFAULT_CAPACITY = 50
DEFAULT_KEY_PREFIX = "solanatrilly:rate_limiter"

# Atomic token-bucket acquire script.
# Returns 1 if a token was acquired, 0 if the bucket is empty.
_ACQUIRE_SCRIPT = """
local key_tokens = KEYS[1]
local key_last   = KEYS[2]
local rate       = tonumber(ARGV[1])
local capacity   = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local last_raw   = redis.call('get', key_last)
local tokens_raw = redis.call('get', key_tokens)
local last   = last_raw   and tonumber(last_raw)   or now
local tokens = tokens_raw and tonumber(tokens_raw) or capacity
local elapsed   = math.max(0, now - last)
local refilled  = math.min(capacity, tokens + elapsed * rate)
local acquired = 0
if refilled >= 1.0 then
    refilled  = refilled - 1.0
    acquired  = 1
end
redis.call('set', key_tokens, tostring(refilled))
redis.call('set', key_last,   tostring(now))
return acquired
"""


class RateLimiter:
    """Redis token-bucket rate limiter.

    Ported verbatim from solanaBilly with key-prefix adjusted to
    'solanatrilly:rate_limiter' so the two projects share a Redis without
    interfering with each other's buckets.

    Args:
        redis_client: A redis.Redis instance (must be connected to the Redis
            service at redis://redis:6379 inside the Docker network).
        rate: Token refill rate in tokens per second (default 40.0).
        capacity: Maximum bucket size in tokens (default 50).
        key_prefix: Redis key prefix for this limiter (default
            'solanatrilly:rate_limiter').
    """

    def __init__(
        self,
        redis_client,
        rate: float = DEFAULT_RATE,
        capacity: int = DEFAULT_CAPACITY,
        key_prefix: str = DEFAULT_KEY_PREFIX,
    ) -> None:
        self.redis = redis_client
        self.rate = rate
        self.capacity = capacity
        self._key_tokens = f"{key_prefix}:tokens"
        self._key_last = f"{key_prefix}:last"
        self._acquire = self.redis.register_script(_ACQUIRE_SCRIPT)

    def acquire(self) -> bool:
        """Attempt to acquire one token from the bucket.

        Returns True if a token was available (caller may proceed),
        False if the bucket is empty (caller should back off).
        """
        now = time.time()
        result = self._acquire(
            keys=[self._key_tokens, self._key_last],
            args=[self.rate, self.capacity, now],
        )
        return bool(result)

    def wait_for_token(self, poll_interval: float = 0.05) -> None:
        """Block until a token is available, then consume it.

        Polls the bucket every poll_interval seconds (default 50 ms).
        Logs a warning on the first wait so operators can detect sustained
        throttling without reading Redis directly.
        """
        waited = False
        while not self.acquire():
            if not waited:
                waited = True
                logger.warning(
                    "RateLimiter: bucket empty — waiting for token "
                    "(key=%s, rate=%.1f/s, capacity=%d)",
                    self._key_tokens,
                    self.rate,
                    self.capacity,
                )
            time.sleep(poll_interval)


def get_redis_client():
    """Return a Redis client connected to the project Redis instance.

    Reads REDIS_URL from the environment (default: redis://redis:6379/0).
    Uses DB 1 to keep rate-limiter keys separate from the broker/result DB.
    """
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return redis_lib.Redis.from_url(redis_url, db=1, decode_responses=True)
