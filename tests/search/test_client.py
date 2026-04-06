"""Tests for the shared search client helpers."""

import threading
from queue import Queue

from fli.search import client as client_module


def test_get_client_returns_same_client_within_thread() -> None:
    """A thread should reuse its own cached HTTP client instance."""
    first = client_module.get_client()
    second = client_module.get_client()

    assert first is second


def test_get_client_returns_distinct_clients_across_threads() -> None:
    """Batch workers should not share one curl session across threads."""
    queue: Queue[client_module.Client] = Queue()

    def worker() -> None:
        queue.put(client_module.get_client())

    first_thread = threading.Thread(target=worker)
    second_thread = threading.Thread(target=worker)
    first_thread.start()
    second_thread.start()
    first_thread.join()
    second_thread.join()

    first = queue.get_nowait()
    second = queue.get_nowait()

    assert first is not second
