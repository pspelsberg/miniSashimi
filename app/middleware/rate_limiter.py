"""Sliding window rate-limiting middleware for MiniSashimi.

Protects REST endpoints and authentication routes against brute-force and DoS attacks:
- SHA-256 pseudonymization of client IP addresses and auth tokens.
- Sliding window timestamp history with async locks.
- Periodic cleanup loop and LRU emergency eviction at 10,000 active client records (CWE-400).
- Independent, stricter rate limits for login/credential endpoints.
"""

from fastapi import Request, HTTPException, Depends
import os
import time
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger("minisashimi.rate_limiter")


class RateLimiter:
    """Sliding-window request rate limiter with thread-safe timestamp management."""

    def __init__(self, requests_limit: int, window_seconds: int):
        """Initializes rate limiter with quota and sliding window duration.
        
        Args:
            requests_limit: Maximum allowed requests within the time window.
            window_seconds: Duration of the sliding window in seconds.
        """
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self.history = defaultdict(list)
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    def start_cleanup(self) -> None:
        """Launches background task for periodic eviction of expired client records."""
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(self.window_seconds)
                    await self.cleanup_inactive_keys()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in RateLimiter cleanup loop: {str(e)}")
        
        self._cleanup_task = asyncio.create_task(cleanup_loop())

    async def cleanup_inactive_keys(self) -> None:
        """Removes expired request timestamps and drops empty client IDs."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            to_delete = []
            for client_id, timestamps in list(self.history.items()):
                valid_timestamps = [t for t in timestamps if t > cutoff]
                if not valid_timestamps:
                    to_delete.append(client_id)
                else:
                    self.history[client_id] = valid_timestamps
            for client_id in to_delete:
                self.history.pop(client_id, None)

    async def check_rate_limit(self, client_id: str) -> bool:
        """Checks if a client has exceeded their request quota.
        
        Args:
            client_id: Raw client identifier string (IP or token).
            
        Returns:
            True if rate limit is exceeded (request should be blocked), False if permitted.
        """
        import hashlib
        hashed_id = hashlib.sha256(client_id.encode()).hexdigest()
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            
            # Defense-in-depth: limit memory capacity to prevent OOM / DoS (CWE-400)
            if hashed_id not in self.history and len(self.history) >= 10000:
                to_delete = []
                for cid, timestamps in list(self.history.items()):
                    valid_timestamps = [t for t in timestamps if t > cutoff]
                    if not valid_timestamps:
                        to_delete.append(cid)
                    else:
                        self.history[cid] = valid_timestamps
                for cid in to_delete:
                    self.history.pop(cid, None)
                
                # If still over capacity, evict the oldest 1,000 entries (LRU-like eviction)
                if len(self.history) >= 10000:
                    sorted_keys = sorted(self.history.keys(), key=lambda k: self.history[k][-1] if self.history[k] else 0)
                    for cid in sorted_keys[:1000]:
                        self.history.pop(cid, None)
            
            timestamps = [t for t in self.history[hashed_id] if t > cutoff]
            if len(timestamps) >= self.requests_limit:
                self.history[hashed_id] = timestamps
                return True
            timestamps.append(now)
            self.history[hashed_id] = timestamps
            return False


RATE_LIMIT_REQUESTS = int(os.getenv("SASHIMI_RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("SASHIMI_RATE_LIMIT_WINDOW", "60"))

api_rate_limiter = RateLimiter(requests_limit=RATE_LIMIT_REQUESTS, window_seconds=RATE_LIMIT_WINDOW)
login_rate_limiter = RateLimiter(
    requests_limit=int(os.getenv("SASHIMI_LOGIN_RATE_LIMIT_REQUESTS", "5")),
    window_seconds=int(os.getenv("SASHIMI_LOGIN_RATE_LIMIT_WINDOW", "300")),
)


async def login_rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing strict brute-force protection on login routes."""
    client_ip = request.client.host if request.client else "unknown"
    if await login_rate_limiter.check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts")


async def rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing general API request rate limits."""
    auth = request.headers.get("Authorization", "")
    cookie_session = request.cookies.get("sashimi_session")
    if auth:
        client_id = auth
    elif cookie_session:
        client_id = cookie_session
    else:
        forwarded_for = request.headers.get("X-Forwarded-For")
        trust_proxy = os.getenv("SASHIMI_TRUST_PROXY", "false").lower() == "true"
        if trust_proxy and forwarded_for:
            client_id = forwarded_for.split(",")[0].strip()
        else:
            client_id = request.client.host if request.client else "unknown"
    
    if await api_rate_limiter.check_rate_limit(client_id):
        raise HTTPException(status_code=429, detail="Too many requests. Rate limit exceeded.")
