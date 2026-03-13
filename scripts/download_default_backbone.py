# scripts/download_default_backbone.py
"""
Download a small public 1B chat model into ./models for quick startup.

Default: TinyLlama/TinyLlama-1.1B-Chat-v1.0
- Public HF repo, no token required.
- Saved under: ./models/TinyLlama-1.1B-Chat-v1.0
"""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# You can override these via env if needed
DEFAULT_REPO_ID = os.getenv(
    "DEFAULT_BACKBONE_REPO",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
)
DEFAULT_LOCAL_DIR = os.getenv(
    "DEFAULT_BACKBONE_DIR",
    "./models/TinyLlama-1.1B-Chat-v1.0",
)


def resolve_path(p: str) -> Path:
    """Resolve path relative to project root."""
    path = Path(p)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def main() -> None:
    repo_id = DEFAULT_REPO_ID
    local_dir = resolve_path(DEFAULT_LOCAL_DIR)

    print("=== Download default LLM backbone ===")
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Repo id      : {repo_id}")
    print(f"Local dir    : {local_dir}")
    print()

    local_dir.mkdir(parents=True, exist_ok=True)

    print("[info] Starting download from Hugging Face (public, no token required)...")
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    print("[ok] Download finished.")
    print()

    # Check whether .env already points to this model so we don't print
    # misleading "you need to set these" instructions when defaults are fine.
    model_rel = f"./models/{local_dir.name}"
    env_file = PROJECT_ROOT / ".env"
    env_src = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    already_configured = (
        f"MAIN_LLM_MODEL_PATH={model_rel}" in env_src
        and f"BASELINE_LLM_MODEL_PATH={model_rel}" in env_src
    )

    if already_configured:
        print(f"[ok] Your .env already points to {model_rel} — no changes needed.")
    else:
        print("Ensure your .env contains:")
        print(f"  MAIN_LLM_BACKEND=local")
        print(f"  MAIN_LLM_MODEL_PATH={model_rel}")
        print(f"  BASELINE_LLM_BACKEND=local")
        print(f"  BASELINE_LLM_MODEL_PATH={model_rel}")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
