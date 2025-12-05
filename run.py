#!/usr/bin/env python3
import os
import sys
import time
import webbrowser
import subprocess
from pathlib import Path
from dotenv import load_dotenv


def run_env_check():
    """Run environment checker if available."""
    try:
        from scripts import check_env
        check_env.main()
    except Exception:
        print("[run] Env check skipped.")


def main():
    load_dotenv()

    root = Path(__file__).resolve().parent

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = os.getenv("DASHBOARD_PORT", "8000")

    # Target ASGI app: api/dashboard_api.py -> app
    app_target = os.getenv("DASHBOARD_APP", "api.dashboard_api:app")

    # Where to open in browser
    url = os.getenv(
        "DASHBOARD_URL",
        f"http://{host}:{port}/static/index.html",
    )

    run_env_check()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        app_target,
        "--host",
        host,
        "--port",
        str(port),
        "--reload",
    ]

    print("[run] Launch backend:", " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=root)

    try:
        time.sleep(2)
        print("[run] Opening:", url)
        webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        print("\n[run] Stopping...")
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
