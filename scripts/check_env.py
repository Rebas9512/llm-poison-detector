# check_env.py
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import torch
from dotenv import load_dotenv

try:
    import requests
except ImportError:
    requests = None

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(p: str | None) -> str | None:
    if not p:
        return None
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def str2bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


def select_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if platform.system() == "Darwin" and getattr(torch.backends, "mps", None):
        if torch.backends.mps.is_available():
            return "mps"
    return "cpu"


def exists(p: str | None) -> bool:
    return bool(p and os.path.exists(p))


def check_openai_api(base_url: str | None) -> bool:
    if not base_url or not requests:
        return False
    url = base_url.rstrip("/") + "/models"
    try:
        r = requests.get(url, timeout=3)
        return r.ok
    except Exception:
        return False


# -------------------------------------------------------------------
# Env snapshot
# -------------------------------------------------------------------

# Safety model HF repo
MLC_MODEL_REPO_ID = os.getenv(
    "MLC_MODEL_REPO_ID",
    "rebas9512/llm-sandbox-safetymodel",
)

# Safety model local dir
MLC_MODEL_PATH = resolve_path(os.getenv("MLC_MODEL_PATH", "./models/safetymodel"))

# Default backbone (for auto-download)
DEFAULT_BACKBONE_REPO = os.getenv(
    "DEFAULT_BACKBONE_REPO",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
)
DEFAULT_BACKBONE_DIR = resolve_path(
    os.getenv("DEFAULT_BACKBONE_DIR", "./models/TinyLlama-1.1B-Chat-v1.0")
)

MAIN_LLM_BACKEND = os.getenv("MAIN_LLM_BACKEND", "local").lower()
MAIN_LLM_MODEL_PATH = resolve_path(os.getenv("MAIN_LLM_MODEL_PATH"))
MAIN_LLM_API_BASE_URL = os.getenv("MAIN_LLM_API_BASE_URL")
MAIN_LLM_API_MODEL = os.getenv("MAIN_LLM_API_MODEL")

BASELINE_LLM_BACKEND = os.getenv("BASELINE_LLM_BACKEND", "local").lower()
BASELINE_LLM_MODEL_PATH = resolve_path(os.getenv("BASELINE_LLM_MODEL_PATH"))
BASELINE_LLM_API_BASE_URL = os.getenv("BASELINE_LLM_API_BASE_URL")
BASELINE_LLM_API_MODEL = os.getenv("BASELINE_LLM_API_MODEL")

SQLITE_DB_PATH = resolve_path(os.getenv("SQLITE_DB_PATH"))
SCHEMA_PATH = resolve_path(os.getenv("SCHEMA_PATH"))

RISK_THRESHOLD = float(os.getenv("RISK_THRESHOLD", "0.5"))
TEE_BASELINE = str2bool(os.getenv("TEE_BASELINE", "true"), default=True)
DROP_ON_BLOCK = str2bool(os.getenv("DROP_ON_BLOCK", "false"), default=False)
LABEL_SCHEMA_VERSION = os.getenv("LABEL_SCHEMA_VERSION", "v1")
SOURCE_TAG = os.getenv("SOURCE_TAG", "manual")

DEVICE = select_device()


# -------------------------------------------------------------------
# Safety model auto-download
# -------------------------------------------------------------------

def ensure_safety_model_local() -> None:
    """Ensure safety model dir exists; auto-download from HF if missing."""
    global MLC_MODEL_PATH

    if exists(MLC_MODEL_PATH):
        return

    if not MLC_MODEL_REPO_ID:
        print("[warn] No safety model found and no MLC_MODEL_REPO_ID set.")
        return

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[warn] huggingface_hub is not installed; cannot auto-download safety model.")
        print("       pip install huggingface_hub or install transformers[torch].")
        return

    if not MLC_MODEL_PATH:
        MLC_MODEL_PATH = str((PROJECT_ROOT / "models" / "safetymodel").resolve())

    target_dir = Path(MLC_MODEL_PATH)
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"[info] Safety model not found locally, downloading from HF: {MLC_MODEL_REPO_ID}")
    print(f"[info] Target dir: {target_dir}")
    try:
        snapshot_download(
            repo_id=MLC_MODEL_REPO_ID,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )
        print("[ok] Safety model downloaded.")
    except Exception as e:  # noqa: BLE001
        print(f"[error] Failed to download safety model: {e}")


# -------------------------------------------------------------------
# Default backbone helper
# -------------------------------------------------------------------

def run_default_backbone_download() -> bool:
    """Run helper script to download the default backbone."""
    script = PROJECT_ROOT / "scripts" / "download_default_backbone.py"
    if not script.exists():
        print(f"[error] {script} not found.")
        return False

    print(f"[info] Running default backbone downloader: {script}")
    try:
        result = subprocess.run(
            [os.environ.get("PYTHON", "python"), str(script)],
            cwd=str(PROJECT_ROOT),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[error] Failed to run downloader: {e}")
        return False

    if result.returncode != 0:
        print(f"[error] download_default_backbone.py exited with code {result.returncode}")
        return False

    global MAIN_LLM_MODEL_PATH, BASELINE_LLM_MODEL_PATH
    if DEFAULT_BACKBONE_DIR:
        MAIN_LLM_MODEL_PATH = DEFAULT_BACKBONE_DIR
        BASELINE_LLM_MODEL_PATH = DEFAULT_BACKBONE_DIR
        os.environ["MAIN_LLM_MODEL_PATH"] = DEFAULT_BACKBONE_DIR
        os.environ["BASELINE_LLM_MODEL_PATH"] = DEFAULT_BACKBONE_DIR
        print(f"[ok] Using default backbone at {DEFAULT_BACKBONE_DIR} for main & baseline (this run only).")

    return True


# -------------------------------------------------------------------
# Backbones discovery
# -------------------------------------------------------------------

def _find_local_model_dirs(root: Path) -> List[str]:
    """Scan ./models for HF-style model dirs (ignore safety model)."""
    result: List[str] = []
    if not root.exists() or not root.is_dir():
        return result
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.lower() == "safetymodel":
            continue
        cfg = entry / "config.json"
        tok_cfg = entry / "tokenizer_config.json"
        if cfg.exists() or tok_cfg.exists():
            p = str(entry.resolve())
            if MLC_MODEL_PATH and p == MLC_MODEL_PATH:
                continue
            result.append(p)
    return result


def list_backbones() -> List[Dict[str, Any]]:
    """List local HF dirs and OpenAI-compatible backends."""
    backbones: List[Dict[str, Any]] = []
    seen_local: set[str] = set()
    seen_api: set[tuple[str, str]] = set()

    # Local from env (main / baseline)
    if MAIN_LLM_MODEL_PATH:
        p = MAIN_LLM_MODEL_PATH
        if p not in seen_local and (not MLC_MODEL_PATH or p != MLC_MODEL_PATH):
            backbones.append(
                {
                    "id": f"local:env_main:{p}",
                    "kind": "local",
                    "source": "env_main",
                    "path": p,
                    "name": os.path.basename(p.rstrip(os.sep)),
                    "available": exists(p),
                }
            )
            seen_local.add(p)

    if BASELINE_LLM_MODEL_PATH:
        p = BASELINE_LLM_MODEL_PATH
        if p not in seen_local and (not MLC_MODEL_PATH or p != MLC_MODEL_PATH):
            backbones.append(
                {
                    "id": f"local:env_baseline:{p}",
                    "kind": "local",
                    "source": "env_baseline",
                    "path": p,
                    "name": os.path.basename(p.rstrip(os.sep)),
                    "available": exists(p),
                }
            )
            seen_local.add(p)

    # Local from ./models/*
    models_root = PROJECT_ROOT / "models"
    for p in _find_local_model_dirs(models_root):
        if p in seen_local:
            continue
        backbones.append(
            {
                "id": f"local:models:{p}",
                "kind": "local",
                "source": "models_dir",
                "path": p,
                "name": os.path.basename(p.rstrip(os.sep)),
                "available": exists(p),
            }
        )
        seen_local.add(p)

    # API from env (main / baseline)
    if MAIN_LLM_API_BASE_URL and MAIN_LLM_API_MODEL:
        key = (MAIN_LLM_API_BASE_URL, MAIN_LLM_API_MODEL)
        if key not in seen_api:
            backbones.append(
                {
                    "id": f"api:env_main:{MAIN_LLM_API_BASE_URL}:{MAIN_LLM_API_MODEL}",
                    "kind": "openai_api",
                    "source": "env_main",
                    "base_url": MAIN_LLM_API_BASE_URL,
                    "model": MAIN_LLM_API_MODEL,
                    "available": check_openai_api(MAIN_LLM_API_BASE_URL),
                }
            )
            seen_api.add(key)

    if BASELINE_LLM_API_BASE_URL and BASELINE_LLM_API_MODEL:
        key = (BASELINE_LLM_API_BASE_URL, BASELINE_LLM_API_MODEL)
        if key not in seen_api:
            backbones.append(
                {
                    "id": f"api:env_baseline:{BASELINE_LLM_API_BASE_URL}:{BASELINE_LLM_API_MODEL}",
                    "kind": "openai_api",
                    "source": "env_baseline",
                    "base_url": BASELINE_LLM_API_BASE_URL,
                    "model": BASELINE_LLM_API_MODEL,
                    "available": check_openai_api(BASELINE_LLM_API_BASE_URL),
                }
            )
            seen_api.add(key)

    return backbones


def select_backbone(target: str, backbone_id: str) -> None:
    """Set backbone for 'main' or 'baseline' via env vars."""
    target = target.lower()
    if target not in ("main", "baseline"):
        raise ValueError("target must be 'main' or 'baseline'")

    bmap = {b["id"]: b for b in list_backbones()}
    if backbone_id not in bmap:
        raise ValueError(f"unknown backbone_id: {backbone_id}")

    b = bmap[backbone_id]
    prefix = "MAIN_LLM_" if target == "main" else "BASELINE_LLM_"

    if b["kind"] == "local":
        os.environ[prefix + "BACKEND"] = "local"
        os.environ[prefix + "MODEL_PATH"] = b["path"]
    elif b["kind"] == "openai_api":
        os.environ[prefix + "BACKEND"] = "openai_api"
        os.environ[prefix + "API_BASE_URL"] = b["base_url"]
        os.environ[prefix + "API_MODEL"] = b["model"]
    else:
        raise ValueError(f"unknown backbone kind: {b['kind']}")


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    print("=== LLM Poison Sandbox: env check ===")
    print(f"Project root:   {PROJECT_ROOT}")
    print(f"Device:         {DEVICE}")
    print()

    ensure_safety_model_local()

    print("Safety model:")
    print(f"  MLC_MODEL_REPO_ID     = {MLC_MODEL_REPO_ID}")
    print(f"  MLC_MODEL_PATH        = {MLC_MODEL_PATH}")
    print()

    print("Main LLM:")
    print(f"  MAIN_LLM_BACKEND      = {MAIN_LLM_BACKEND}")
    print(f"  MAIN_LLM_MODEL_PATH   = {MAIN_LLM_MODEL_PATH}")
    print(f"  MAIN_LLM_API_BASE_URL = {MAIN_LLM_API_BASE_URL}")
    print(f"  MAIN_LLM_API_MODEL    = {MAIN_LLM_API_MODEL}")
    print()

    print("Baseline LLM:")
    print(f"  BASELINE_LLM_BACKEND      = {BASELINE_LLM_BACKEND}")
    print(f"  BASELINE_LLM_MODEL_PATH   = {BASELINE_LLM_MODEL_PATH}")
    print(f"  BASELINE_LLM_API_BASE_URL = {BASELINE_LLM_API_BASE_URL}")
    print(f"  BASELINE_LLM_API_MODEL    = {BASELINE_LLM_API_MODEL}")
    print()

    print("DB / schema:")
    print(f"  SQLITE_DB_PATH = {SQLITE_DB_PATH}")
    print(f"  SCHEMA_PATH    = {SCHEMA_PATH}")
    print()

    print("Defaults:")
    print(f"  RISK_THRESHOLD       = {RISK_THRESHOLD}")
    print(f"  TEE_BASELINE         = {TEE_BASELINE}")
    print(f"  DROP_ON_BLOCK        = {DROP_ON_BLOCK}")
    print(f"  LABEL_SCHEMA_VERSION = {LABEL_SCHEMA_VERSION}")
    print(f"  SOURCE_TAG           = {SOURCE_TAG}")
    print()

    print("=== Files (local backends only) ===")
    print(f"{'MLC model':15s}: {exists(MLC_MODEL_PATH)}  ({MLC_MODEL_PATH})")

    if MAIN_LLM_BACKEND == "local":
        print(f"{'Main LLM':15s}: {exists(MAIN_LLM_MODEL_PATH)}  ({MAIN_LLM_MODEL_PATH})")
    else:
        print(f"{'Main LLM':15s}: backend=openai_api (skip local check)")

    if BASELINE_LLM_BACKEND == "local":
        print(f"{'Baseline LLM':15s}: {exists(BASELINE_LLM_MODEL_PATH)}  ({BASELINE_LLM_MODEL_PATH})")
    else:
        print(f"{'Baseline LLM':15s}: backend=openai_api (skip local check)")

    print(f"{'SQLite DB':15s}: {exists(SQLITE_DB_PATH)}  ({SQLITE_DB_PATH})")
    print(f"{'Schema SQL':15s}: {exists(SCHEMA_PATH)}  ({SCHEMA_PATH})")
    print()

    # Local fallback: reuse main as baseline
    has_main_local = MAIN_LLM_BACKEND == "local" and exists(MAIN_LLM_MODEL_PATH)
    has_base_local = BASELINE_LLM_BACKEND == "local" and exists(BASELINE_LLM_MODEL_PATH)

    if BASELINE_LLM_BACKEND == "local" and not has_base_local and has_main_local:
        print("[info] No local baseline model found. Baseline will reuse MAIN_LLM_MODEL_PATH.")
        os.environ["BASELINE_LLM_MODEL_PATH"] = MAIN_LLM_MODEL_PATH or ""
        print(f"[info] BASELINE_LLM_MODEL_PATH => {os.environ['BASELINE_LLM_MODEL_PATH']}")
        print()

    # Local fallback: prompt user when both use local backend and none exists
    has_any_local_llm = has_main_local or has_base_local
    both_local_backends = MAIN_LLM_BACKEND == "local" and BASELINE_LLM_BACKEND == "local"

    if both_local_backends and not has_any_local_llm:
        print("[warn] No local LLM model found.")
        print("You can:")
        print("  - place a HF model under ./models and set *_MODEL_PATH in .env")
        print("  - run an OpenAI-compatible server and set *_BACKEND=openai_api")
        print(f"  - type 'default' to download: {DEFAULT_BACKBONE_REPO} -> {DEFAULT_BACKBONE_DIR}")
        print()
        user_in = input(
            "Optional: enter a local HF model path for both main & baseline, "
            "or type 'default' to auto-download (empty to skip): "
        ).strip()

        if user_in.lower() == "default":
            ok = run_default_backbone_download()
            if not ok:
                print("[error] Default backbone download failed.")
        elif user_in:
            rp = resolve_path(user_in)
            if rp and os.path.exists(rp):
                os.environ["MAIN_LLM_MODEL_PATH"] = rp
                os.environ["BASELINE_LLM_MODEL_PATH"] = rp
                print(f"[ok] Using {rp} for MAIN_LLM_MODEL_PATH and BASELINE_LLM_MODEL_PATH (this run only).")
            else:
                print(f"[error] Path not found: {rp}")
        print()

    # OpenAI-compatible API check
    if MAIN_LLM_BACKEND == "openai_api" or BASELINE_LLM_BACKEND == "openai_api":
        print("=== OpenAI-compatible API ===")
        if MAIN_LLM_BACKEND == "openai_api":
            ok = check_openai_api(MAIN_LLM_API_BASE_URL)
            print(f"Main  : {MAIN_LLM_API_BASE_URL}  (model={MAIN_LLM_API_MODEL}, reachable={ok})")
        if BASELINE_LLM_BACKEND == "openai_api":
            ok = check_openai_api(BASELINE_LLM_API_BASE_URL)
            print(f"Base  : {BASELINE_LLM_API_BASE_URL}  (model={BASELINE_LLM_API_MODEL}, reachable={ok})")
        if not requests:
            print("[warn] requests not installed, API reachability not checked.")
        print("Server should expose /v1/chat/completions.")
        print()

    print("Env check done.")


if __name__ == "__main__":
    main()
