#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  LLM Poison Detector — Setup (macOS / Linux / WSL)
#
#  Usage (first-time):
#    git clone <repo-url> llm-poison-detector && cd llm-poison-detector
#    chmod +x setup.sh && ./setup.sh
#
#  Options:
#    --reinstall           Delete and recreate the .venv
#    --skip-check          Skip the first-run environment check
#    --headless            Non-interactive / CI mode: implies --skip-check.
#                          Exit code reflects success (0) or failure (non-zero).
#    --auto-backbone       Auto-download default backbone without prompting
#                          (sets LLP_AUTO_BACKBONE=1 for check_env.py).
#    --doctor              Run environment check only, then exit.
#
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── ANSI colours ─────────────────────────────────────────────────────────────
if [[ -t 1 && "${NO_COLOR:-}" == "" && "${TERM:-dumb}" != "dumb" ]]; then
    BOLD='\033[1m'
    GREEN='\033[38;2;0;229;180m'
    YELLOW='\033[38;2;255;176;32m'
    RED='\033[38;2;230;57;70m'
    MUTED='\033[38;2;110;120;148m'
    NC='\033[0m'
else
    BOLD='' GREEN='' YELLOW='' RED='' MUTED='' NC=''
fi

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${MUTED}·${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }

section() {
    echo ""
    echo -e "${BOLD}── $* ──${NC}"
}

# ── project root ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

# ── argument parsing ──────────────────────────────────────────────────────────
REINSTALL=false
SKIP_CHECK=false
HEADLESS=false
AUTO_BACKBONE=false
DOCTOR=false

for arg in "$@"; do
    case "$arg" in
        --reinstall)       REINSTALL=true ;;
        --skip-check)      SKIP_CHECK=true ;;
        --headless)        HEADLESS=true; SKIP_CHECK=true ;;
        --auto-backbone)   AUTO_BACKBONE=true ;;
        --doctor)          DOCTOR=true ;;
        --help|-h)
            echo "Usage: ./setup.sh [--reinstall] [--skip-check] [--headless] [--auto-backbone] [--doctor]"
            exit 0
            ;;
        *)
            warn "Unknown option: $arg  (ignored)"
            ;;
    esac
done

# ── banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  LLM Poison Detector — Setup${NC}"
echo -e "${MUTED}  Creates a Python virtual environment and installs all dependencies.${NC}"
echo ""

# ── Step 1: detect OS ─────────────────────────────────────────────────────────
section "Step 1 / 4  —  Platform"

OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ -n "${WSL_DISTRO_NAME:-}" || -n "${WSL_INTEROP:-}" ]]; then
    OS="wsl"
elif [[ "$OSTYPE" == "linux-gnu"* || "$OSTYPE" == "linux"* ]]; then
    OS="linux"
fi

if [[ "$OS" == "unknown" ]]; then
    fail "Unsupported operating system ($OSTYPE).  Run setup.ps1 on Windows."
fi
ok "Platform: $OS"

# ── Step 2: find Python 3.10+ ─────────────────────────────────────────────────
section "Step 2 / 4  —  Python"

PYTHON=""
find_python() {
    local ver maj min
    for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            continue
        fi
        ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        if [[ -z "$ver" ]]; then
            continue
        fi
        maj="${ver%%.*}"
        min="${ver##*.}"
        if [[ "$maj" -ge 3 && "$min" -ge 10 ]]; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

if ! PYTHON="$(find_python)"; then
    echo ""
    fail "Python 3.10+ is required but was not found in PATH.

Install it from https://www.python.org/downloads/ and re-run this script.
  macOS:  brew install python@3.12
  Ubuntu: sudo apt install python3.12 python3.12-venv"
fi

PYTHON_VERSION="$("$PYTHON" -c 'import sys; print(sys.version)' 2>/dev/null)"
ok "Python: $PYTHON  ($PYTHON_VERSION)"

# ── Step 3: create / reuse venv ───────────────────────────────────────────────
section "Step 3 / 4  —  Virtual environment"

if [[ -d "$VENV_DIR" ]]; then
    if [[ "$REINSTALL" == "true" ]]; then
        info "Removing existing .venv (--reinstall)"
        rm -rf "$VENV_DIR"
    else
        if [[ -x "$VENV_DIR/bin/python" ]]; then
            ok ".venv exists — reusing"
            info "  (pass --reinstall to force a clean rebuild)"
        else
            warn "Existing .venv appears broken — recreating"
            rm -rf "$VENV_DIR"
        fi
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating .venv with $PYTHON ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok ".venv created: $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

info "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

# On Linux/WSL without CUDA, install CPU-only torch to avoid the multi-GB
# CUDA bundle that pip selects by default.
if [[ "$OS" != "macos" ]] && ! command -v nvidia-smi >/dev/null 2>&1; then
    info "No CUDA detected — installing CPU-only torch ..."
    "$VENV_PIP" install "torch>=2.4.0" \
        --index-url https://download.pytorch.org/whl/cpu --quiet
fi

info "Installing dependencies from requirements.txt ..."
info "  (torch + transformers may take several minutes on first install)"
echo ""
"$VENV_PIP" install -r "$REQUIREMENTS"
echo ""
ok "Dependencies installed."

info "Registering 'llmpoison' CLI command ..."
"$VENV_PIP" install -e . --no-deps --quiet
ok "'llmpoison' command registered."

# ── Step 4: environment check ─────────────────────────────────────────────────
section "Step 4 / 4  —  Environment check"

if [[ "$DOCTOR" == "true" ]]; then
    info "Running environment check (--doctor) ..."
    "$VENV_PYTHON" "$SCRIPT_DIR/scripts/check_env.py"
    exit $?
fi

if [[ "$SKIP_CHECK" == "true" ]]; then
    if [[ "$HEADLESS" == "true" ]]; then
        info "Headless mode — skipping environment check."
        info "Run manually: .venv/bin/python scripts/check_env.py"
    else
        info "Skipping environment check (--skip-check)"
    fi
else
    [[ "$AUTO_BACKBONE" == "true" ]] && export LLP_AUTO_BACKBONE=1

    CHECK_EXIT=0
    if [[ -r /dev/tty && -w /dev/tty ]]; then
        "$VENV_PYTHON" "$SCRIPT_DIR/scripts/check_env.py" < /dev/tty || CHECK_EXIT=$?
    else
        # No TTY available (e.g. stdin is a pipe): LLP_AUTO_BACKBONE=1 will
        # still trigger a silent backbone download; interactive prompt is skipped.
        warn "No TTY detected — interactive backbone prompt unavailable."
        warn "Set LLP_AUTO_BACKBONE=1 or use --auto-backbone for an unattended download."
        "$VENV_PYTHON" "$SCRIPT_DIR/scripts/check_env.py" || CHECK_EXIT=$?
    fi
    if [[ $CHECK_EXIT -ne 0 ]]; then
        echo ""
        warn "Environment check did not pass (exit code $CHECK_EXIT)."
        warn "This usually means models are not yet downloaded — that is OK."
        warn "Download the default backbone:"
        warn "  .venv/bin/python scripts/download_default_backbone.py"
    fi
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Setup complete!${NC}"
echo ""
echo -e "  Activate the venv once per terminal session, then launch:"
echo ""
echo -e "    ${GREEN}source .venv/bin/activate${NC}"
echo -e "    ${GREEN}llmpoison${NC}              # start the dashboard (opens browser)"
echo ""
echo "  Or invoke directly without activating:"
echo -e "    ${MUTED}.venv/bin/llmpoison${NC}"
echo ""
echo "  To download the default backbone model:"
echo -e "    ${MUTED}.venv/bin/python scripts/download_default_backbone.py${NC}"
echo ""
