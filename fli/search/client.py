"""HTTP client implementation with impersonation, rate limiting and retry functionality.

This module provides a robust HTTP client that handles:
- User agent impersonation (to mimic a browser)
- Rate limiting (15 requests per second, process-wide)
- Automatic retries with exponential backoff
- Session management
- Error handling
"""

import threading
import time
from typing import Any

from curl_cffi import requests
from tenacity import retry, stop_after_attempt, wait_exponential

_thread_local = threading.local()

_rate_lock = threading.Lock()
_rate_calls: int = 0
_rate_window_start: float = 0.0
_RATE_MAX_CALLS = 15
_RATE_PERIOD = 1.0


def _acquire_rate_limit_slot() -> None:
    """Block until a process-wide rate-limit slot is available.

    The lock is held only long enough to read/update the counter,
    never during sleep, so parallel threads are not starved.
    """
    global _rate_calls, _rate_window_start
    while True:
        with _rate_lock:
            now = time.monotonic()
            if now - _rate_window_start >= _RATE_PERIOD:
                _rate_calls = 0
                _rate_window_start = now
            if _rate_calls < _RATE_MAX_CALLS:
                _rate_calls += 1
                return
            sleep_time = _RATE_PERIOD - (now - _rate_window_start)
        time.sleep(max(sleep_time, 0.005))


class Client:
    """HTTP client with built-in rate limiting, retry and user agent impersonation functionality."""

    DEFAULT_HEADERS = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    }

    def __init__(self):
        """Initialize a new client session with default headers."""
        self._client = requests.Session()
        self._client.headers.update(self.DEFAULT_HEADERS)

    def __del__(self):
        """Clean up client session on deletion."""
        if hasattr(self, "_client"):
            self._client.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(), reraise=True)
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Make a rate-limited GET request with automatic retries.

        Args:
            url: Target URL for the request
            **kwargs: Additional arguments passed to requests.get()

        Returns:
            Response object from the server

        Raises:
            Exception: If request fails after all retries

        """
        try:
            _acquire_rate_limit_slot()
            response = self._client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as e:
            raise Exception(f"GET request failed: {str(e)}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(), reraise=True)
    def post(self, url: str, **kwargs: Any) -> requests.Response:
        """Make a rate-limited POST request with automatic retries.

        Args:
            url: Target URL for the request
            **kwargs: Additional arguments passed to requests.post()

        Returns:
            Response object from the server

        Raises:
            Exception: If request fails after all retries

        """
        try:
            _acquire_rate_limit_slot()
            response = self._client.post(url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as e:
            raise Exception(f"POST request failed: {str(e)}") from e


def get_client() -> Client:
    """Get or create a per-thread HTTP client instance.

    Returns:
        Thread-local instance of the HTTP client

    """
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = Client()
        _thread_local.client = client
    return client
