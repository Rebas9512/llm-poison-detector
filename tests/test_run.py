"""
Tests for run.py helper functions.

All tests run without loading any ML models or touching the real database.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from run import clear_model_memory, terminate_process_tree, wait_for_backend


# ── clear_model_memory ────────────────────────────────────────────────────────


def test_clear_model_memory_no_crash_when_no_models() -> None:
    """Should not raise even when models_runtime was never imported."""
    clear_model_memory()


def test_clear_model_memory_idempotent() -> None:
    """Calling twice in a row must not raise."""
    clear_model_memory()
    clear_model_memory()


# ── wait_for_backend ──────────────────────────────────────────────────────────


def test_wait_for_backend_raises_timeout_when_no_server() -> None:
    with pytest.raises(TimeoutError):
        wait_for_backend(
            "http://127.0.0.1:19879/api/ready",
            timeout_s=1.0,
            poll_s=0.2,
        )


def test_wait_for_backend_success_on_200() -> None:
    """Returns normally when the target URL responds 200."""

    class OkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args: object) -> None:  # silence server logs in tests
            pass

    server = HTTPServer(("127.0.0.1", 0), OkHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    wait_for_backend(f"http://127.0.0.1:{port}/api/ready", timeout_s=5.0, poll_s=0.1)

    t.join(timeout=2)
    server.server_close()


def test_wait_for_backend_raises_if_process_dies() -> None:
    """Should raise RuntimeError when the monitored process exits before ready."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.1)"],
    )
    with pytest.raises((RuntimeError, TimeoutError)):
        wait_for_backend(
            "http://127.0.0.1:19878/api/ready",
            proc=proc,
            timeout_s=5.0,
            poll_s=0.1,
        )


# ── terminate_process_tree ────────────────────────────────────────────────────


def test_terminate_already_dead_process() -> None:
    """Should be a silent no-op when the process has already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    terminate_process_tree(proc)  # must not raise


def test_terminate_live_process() -> None:
    """Should terminate a running process cleanly.

    Uses start_new_session=True to mirror how run.py spawns uvicorn —
    the subprocess gets its own process group (PGID == PID), which is
    required for os.killpg to work correctly on POSIX.
    """
    import os

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=(os.name == "posix"),
    )
    terminate_process_tree(proc, grace_s=3.0)
    assert proc.poll() is not None, "Process should have been terminated"
