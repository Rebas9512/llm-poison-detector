#!/usr/bin/env python3
import gc
import os
import signal
import sys
import time
import webbrowser
import subprocess
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv


def _graceful_shutdown(signum, frame) -> None:
    """Convert SIGHUP / SIGTERM into KeyboardInterrupt so the finally block runs."""
    raise KeyboardInterrupt


def run_env_check():
    """Run environment checker if available."""
    try:
        from scripts import check_env
        check_env.main()
    except SystemExit:
        raise
    except Exception:
        print("[run] Env check skipped.")


def clear_model_memory() -> None:
    """Clear cached models and free GPU VRAM in this process."""
    with suppress(Exception):
        mr = sys.modules.get("scripts.models_runtime")
        if mr is not None:
            mr._MLC_CACHE.clear()
            mr._LLM_CACHE.clear()

    gc.collect()
    with suppress(Exception):
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            with suppress(Exception):
                torch.cuda.ipc_collect()


def wait_for_backend(
    ready_url: str,
    proc: subprocess.Popen | None = None,
    timeout_s: float = 60.0,
    poll_s: float = 0.5,
) -> None:
    """Poll ready_url until it returns 2xx, the process dies, or timeout."""
    deadline = time.time() + timeout_s
    last_err = None

    while time.time() < deadline:
        if proc is not None:
            code = proc.poll()
            if code is not None:
                raise RuntimeError(
                    f"Backend process exited before readiness check passed (exit code {code})."
                )
        try:
            with urlopen(ready_url, timeout=5) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    print("[run] Backend ready.")
                    return
        except (URLError, OSError) as exc:
            last_err = exc
        time.sleep(poll_s)

    raise TimeoutError(f"Backend did not become ready in {timeout_s:.0f}s: {last_err}")


def terminate_process_tree(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """Send SIGTERM (or terminate on Windows), escalate to SIGKILL if needed."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        with suppress(Exception):
            proc.wait(timeout=2)
    except ProcessLookupError:
        pass


def main():
    load_dotenv()

    root = Path(__file__).resolve().parent
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = os.getenv("DASHBOARD_PORT", "8000")
    app_target = os.getenv("DASHBOARD_APP", "api.dashboard_api:app")
    url = os.getenv("DASHBOARD_URL", f"http://{host}:{port}/static/index.html")
    ready_url = os.getenv("DASHBOARD_READY_URL", f"http://{host}:{port}/api/ready")
    ready_timeout = float(os.getenv("DASHBOARD_READY_TIMEOUT", "120"))

    run_env_check()

    # Convert terminal-close (SIGHUP) and explicit SIGTERM into KeyboardInterrupt
    # so the finally block always runs.
    if os.name == "posix":
        signal.signal(signal.SIGHUP, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        app_target,
        "--host",
        host,
        "--port",
        str(port),
    ]

    print("[run] Starting backend:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=root,
        env=os.environ.copy(),
        start_new_session=(os.name == "posix"),
    )

    try:
        print("[run] Waiting for backend to become ready...")
        wait_for_backend(ready_url, proc=proc, timeout_s=ready_timeout)
        print("[run] Opening:", url)
        webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        print("\n[run] Stopping...")
    except Exception as exc:
        print(f"[run] Startup error: {exc}")
    finally:
        terminate_process_tree(proc)
        print("[run] Clearing model memory...")
        clear_model_memory()


if __name__ == "__main__":
    main()
