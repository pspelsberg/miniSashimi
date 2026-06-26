from fastapi import Request, HTTPException, Depends
import os
import time
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger("minisashimi.rate_limiter")

class RateLimiter:
    def __init__(self, requests_limit: int, window_seconds: int):
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self.history = defaultdict(list)
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    def start_cleanup(self):
        """Startet den Hintergrund-Task zur Bereinigung inaktiver IDs (F-034)."""
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

    async def cleanup_inactive_keys(self):
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
        import hashlib
        hashed_id = hashlib.sha256(client_id.encode()).hexdigest()
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            
            # Defense-in-depth: limit memory capacity to prevent OOM / DoS (CWE-400)
            if hashed_id not in self.history and len(self.history) >= 10000:
                # Perform emergency cleanup of expired entries
                to_delete = []
                for cid, timestamps in list(self.history.items()):
                    valid_timestamps = [t for t in timestamps if t > cutoff]
                    if not valid_timestamps:
                        to_delete.append(cid)
                    else:
                        self.history[cid] = valid_timestamps
                for cid in to_delete:
                    self.history.pop(cid, None)
                
                # If still over capacity, reject new client requests to protect system memory
                if len(self.history) >= 10000:
                    return True
            
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

async def rate_limit(request: Request):
    # Verwende Authorization-Token oder Client-IP (unter Berücksichtigung von Reverse-Proxys) als Identifikator
    auth = request.headers.get("Authorization", "")
    if auth:
        client_id = auth
    else:
        forwarded_for = request.headers.get("X-Forwarded-For")
        trust_proxy = os.getenv("SASHIMI_TRUST_PROXY", "false").lower() == "true"
        if trust_proxy and forwarded_for:
            client_id = forwarded_for.split(",")[0].strip()
        else:
            client_id = request.client.host if request.client else "unknown"
    
    if await api_rate_limiter.check_rate_limit(client_id):
        raise HTTPException(status_code=429, detail="Too many requests. Rate limit exceeded.")
