# LLM Poison Detector

**A fully local safety evaluation sandbox for LLMs.**
Realtime dashboard · Dual-LLM pipeline · CLI REPL · SQLite logging

Classifies prompts across five safety labels, gates them through a configurable risk threshold, and compares a main LLM against an unfiltered baseline — all on your own hardware.

---

## Preview

![Dashboard — single prompt and batch evaluation panels](screenshot/screenshot1.png)

![Dashboard — batch run with blocked results and probability distribution](screenshot/screenshot2.png)

> **Demo video:** https://youtu.be/o6rQuGOuJmA

---

## The Safety Classifier

This sandbox is built around a custom-trained safety classifier — the pipeline exists to demonstrate and evaluate it.
The model is a fine-tuned **DeBERTa-v3-Large** 5-class softmax classifier, trained from scratch on a purpose-built dataset and hosted publicly on HuggingFace:

**[`rebas9512/llm-sandbox-safetymodel`](https://huggingface.co/rebas9512/llm-sandbox-safetymodel)**

### What it detects

The classifier assigns each prompt to exactly one of five mutually exclusive labels:

| Label | Description |
|-------|-------------|
| `clean` | Benign, non-adversarial input |
| `prompt_injection` | Instruction-override / jailbreak attempts |
| `malicious` | Explicit harmful intent |
| `semantic_poisoning` | Educationally framed content encoding harmful strategies |
| `embedding_anomaly` | Structurally corrupted or out-of-distribution text |

Output per prompt: per-class probability distribution · risk score (`1 − clean_prob`) · `allow / block` decision.

### Training dataset

A purpose-built corpus of **54,354 samples** constructed from public sources, adversarial red-team data, template-based augmentation, and synthetic generation:

| Category | Count | Source |
|----------|-------|--------|
| Clean | 36,236 | BYU PCCL Chitchat, Alexa Topical-Chat, harmless instructional queries; stratified by length to achieve a 2:1 clean-to-harm ratio |
| Malicious | 13,003 | Anthropic HH-RLHF red-team-attempts (filtered for high adversarial rating and explicit harm) |
| Prompt injection | 3,585 | deepset/prompt-injections, Prompt_Injection_Benign_Prompt_Dataset, safe-guard-prompt-injection |
| Semantic poisoning | 1,000 | PoisonBench + template-driven augmentation (crime topics × role framings × multi-section structure) |
| Embedding anomaly | 530 | Synthetically generated: malformed Unicode, raw byte fragments, damaged JSON/HTML, random token noise |

### Model development

Two iterations were explored before the final architecture:

**v1 — ModernBERT multi-label (abandoned)**
`answerdotai/ModernBERT-base` with per-class sigmoid heads and BCEWithLogitsLoss. Achieved good separation for clear-cut cases but produced blurry malicious/clean boundaries because the training objective assumed multi-label behavior while annotations were strictly single-label.

**v2 — DeBERTa-v3-Large softmax (final)**
Reformulated as a standard 5-way softmax classifier (`microsoft/deberta-v3-large` backbone + linear classification head). Single-label cross-entropy with class weights eliminates the objective mismatch and produces sharper decision boundaries.

**Training configuration:**
- 3 epochs · lr 2e-5 · weight decay · batch 16 / 32 (train / eval)
- Max sequence length 256 tokens
- Class-weighted cross-entropy (weights from training label frequencies)
- Stratified train / val / test split; best checkpoint selected by val macro F1
- Mixed-precision training when GPU is available

### Evaluation results

**Overall (held-out test set, 2,718 samples):**

| Metric | Validation | Test |
|--------|-----------|------|
| Accuracy | 0.9875 | **0.9882** |
| Macro F1 | 0.9912 | **0.9883** |
| Micro F1 | 0.9875 | **0.9882** |
| Loss | 0.066 | 0.134 |

Validation and test metrics align closely, indicating strong generalization with limited overfitting.
Inference throughput on the test set: **~574 samples/second**.

**Per-class test performance:**

| Class | Precision | Recall | F1 | Support |
|-------|-----------|--------|----|---------|
| `prompt_injection` | 0.9889 | 0.9944 | **0.9916** | 179 |
| `malicious` | 0.9697 | 0.9846 | **0.9771** | 650 |
| `semantic_poisoning` | 1.0000 | 1.0000 | **1.0000** | 50 |
| `embedding_anomaly` | 1.0000 | 0.9630 | **0.9811** | 27 |
| `clean` | 0.9945 | 0.9890 | **0.9917** | 1,812 |

All five categories — including low-resource minority classes (semantic poisoning: 50 samples, embedding anomaly: 27) — achieve F1 ≥ 0.98 on the test set. The classifier handles both high-frequency benign inputs and rare adversarial patterns without meaningful performance degradation.

---

## 1. Getting Started

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10+ | 3.12 recommended |
| Git | For cloning |
| CUDA GPU (optional) | CPU fallback supported; GPU strongly recommended for batch runs |

### One-liner install (macOS / Linux / WSL)

```bash
curl -fsSL https://raw.githubusercontent.com/Rebas9512/llm-poison-detector/main/install.sh | bash
```

The installer prompts for a clone target directory (default: `~/llm-poison-detector`), then creates a `.venv`, installs all dependencies, and runs the first-time environment check.

**Env-var options** (set before the pipe):

```bash
LLP_DIR=~/tools/llm-poison-detector  curl -fsSL … | bash   # custom install path
LLP_NO_SETUP=1                        curl -fsSL … | bash   # skip env check (CI)
LLP_AUTO_BACKBONE=1                   curl -fsSL … | bash   # auto-download TinyLlama
```

**Installer layout:**
- Cloned source + `.venv/` → install directory you choose
- If that directory exists and is non-empty, the installer falls back to an `llm-poison-detector/` subdirectory inside it
- User config metadata → `~/.llmpoison/`
- CLI registered as `~/.local/bin/llmpoison`

### Windows

```powershell
irm https://raw.githubusercontent.com/Rebas9512/llm-poison-detector/main/install.ps1 | iex
```

```cmd
curl -fsSL https://raw.githubusercontent.com/Rebas9512/llm-poison-detector/main/install.cmd -o install.cmd && install.cmd && del install.cmd
```

Same layout as Linux, with:
- Default install path: `%USERPROFILE%\llm-poison-detector`
- CLI exposed via the venv `Scripts\` directory on PATH
- Config files in `%USERPROFILE%\.llmpoison\`

### Manual install (clone-and-run)

**macOS / Linux / WSL**

```bash
git clone https://github.com/Rebas9512/llm-poison-detector.git
cd llm-poison-detector
chmod +x setup.sh && ./setup.sh
```

**Windows**

```powershell
git clone https://github.com/Rebas9512/llm-poison-detector.git
cd llm-poison-detector
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.sh` / `setup.ps1` create an isolated `.venv/` inside the project directory.

**Manual setup flags:**

| Flag | Effect |
|------|--------|
| `--reinstall` / `-Reinstall` | Delete and recreate `.venv` from scratch |
| `--skip-check` / `-SkipCheck` | Skip the first-run environment check |
| `--headless` / `-Headless` | Non-interactive CI mode (implies skip-check) |
| `--auto-backbone` / `-AutoBackbone` | Auto-download TinyLlama without prompting |
| `--doctor` / `-Doctor` | Run environment check only, then exit |

### After install — start the dashboard

```bash
llmpoison
```

Or without activating the venv:

```bash
.venv/bin/python run.py          # macOS / Linux
.venv\Scripts\python run.py      # Windows
```

The dashboard opens automatically at `http://127.0.0.1:8000/static/index.html`.

---

## 2. First-Run Flow

```
llmpoison
  │
  ├─ load .env
  ├─ scripts/check_env.py
  │    ├─ detect device (CPU / CUDA / MPS)
  │    ├─ ensure safety model is downloaded   ← auto from HuggingFace
  │    ├─ scan ./models/ for local backbones
  │    └─ if no backbone found:
  │         ├─ LLP_AUTO_BACKBONE=1  →  silent download of TinyLlama
  │         └─ interactive          →  prompt user (path or "default")
  │
  ├─ spawn uvicorn subprocess (new process group on POSIX)
  ├─ poll /api/ready until backend is up (up to 120 s)
  │    └─ lifespan startup: preload safety model into VRAM
  ├─ open browser
  └─ wait — Ctrl+C sends SIGTERM to process group → VRAM cleared
```

**Model download happens once.** After first run everything works offline.

---

## 3. Model Distribution

Models are **not bundled** with the project. They are downloaded on first run.

| Model | Size | How downloaded |
|-------|------|----------------|
| `rebas9512/llm-sandbox-safetymodel` | ~1.7 GB | Auto on first run via `check_env.py` |
| `TinyLlama/TinyLlama-1.1B-Chat-v1.0` (default backbone) | ~2.2 GB | Optional: prompted at first run, or `LLP_AUTO_BACKBONE=1` |

Both repos are public on HuggingFace — **no token required**.

To download the backbone manually at any time:

```bash
python scripts/download_default_backbone.py
```

To use your own local HF model instead, point `.env` at it:

```env
MAIN_LLM_BACKEND=local
MAIN_LLM_MODEL_PATH=./models/your-model-dir
BASELINE_LLM_BACKEND=local
BASELINE_LLM_MODEL_PATH=./models/your-model-dir
```

Or connect an OpenAI-compatible server (LM Studio, vLLM, Ollama):

```env
MAIN_LLM_BACKEND=openai_api
MAIN_LLM_API_BASE_URL=http://localhost:11434/v1
MAIN_LLM_API_MODEL=llama3.2
```

---

## 4. Architecture

### Safety model

See **[The Safety Classifier](#the-safety-classifier)** above for full training details, dataset breakdown, and evaluation results.

In brief: a fine-tuned DeBERTa-v3-Large 5-class softmax classifier trained on 54,354 samples.
Hosted at [`rebas9512/llm-sandbox-safetymodel`](https://huggingface.co/rebas9512/llm-sandbox-safetymodel) — auto-downloaded on first run.

Output per prompt: per-class probabilities · risk score (`1 − clean_prob`) · `allow / block` decision.

### Dual-LLM pipeline

```
Prompt
  │
  ├─ Safety Model ──→ risk score ──→ decision
  │                                    │
  │           ┌───── block ────────────┘
  │           │
  │           ▼
  ├─ Main LLM  ──→ gated response (blocked prompts return stub)
  └─ Baseline  ──→ unfiltered response  (always runs)
```

Both LLMs support local HF checkpoints and OpenAI-compatible API backends.
Backbones are discovered at startup and switchable live from the dashboard or REPL.

### Backend components

| Module | Purpose |
|--------|---------|
| `run.py` | Entry point: env check → uvicorn → browser |
| `api/dashboard_api.py` | FastAPI REST + WebSocket endpoints; lifespan model preload / unload |
| `scripts/models_runtime.py` | Safety model + LLM inference; in-process model cache |
| `scripts/db_runtime.py` | SQLite schema init + event logging |
| `scripts/check_env.py` | Environment validation + model downloads |
| `scripts/download_default_backbone.py` | Downloads TinyLlama (or custom backbone) |
| `scripts/pipeline_repl.py` | CLI REPL |

### Memory management

| Event | Action |
|-------|--------|
| Server startup (lifespan) | Safety model preloaded into VRAM |
| All WebSocket clients disconnect for 15 s | Models unloaded, VRAM freed |
| Client reconnects | Idle timer cancelled; model reloads on next request |
| Ctrl+C / SIGTERM | `terminate_process_tree` + `torch.cuda.empty_cache()` |

The idle timeout is configurable: `LLP_IDLE_UNLOAD_DELAY=30` (seconds).

---

## 5. Configuration Reference

The repo ships with a working `.env`. Override any value as needed:

```env
# Safety model path (auto-downloaded into this directory)
MLC_MODEL_PATH=./models/safetymodel

# Main LLM
MAIN_LLM_BACKEND=local                              # local | openai_api
MAIN_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# Baseline LLM
BASELINE_LLM_BACKEND=local
BASELINE_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# OpenAI-compatible backend (used when BACKEND=openai_api)
# MAIN_LLM_API_BASE_URL=http://localhost:11434/v1
# MAIN_LLM_API_MODEL=llama3.2

# Safety threshold (0.0–1.0; lower = stricter)
RISK_THRESHOLD=0.5

# DB
SQLITE_DB_PATH=./db/llm_poison.db
SCHEMA_PATH=./schema/001_init.sql

# Dashboard
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000
DASHBOARD_READY_TIMEOUT=120     # seconds to wait for backend before opening browser
```

---

## 6. Web Dashboard

Located in `static/` (`index.html` + `app.js`):

- **Single Prompt** — type a prompt, run safety + main + baseline, see probability bars
- **Batch Evaluation** — sample from the built-in dataset, run in bulk, inspect per-item decisions
- **Backbone Selector** — switch main/baseline model live without restarting
- **Safety Probability Chart** — bar chart of per-label scores for the latest batch
- **Metrics Panel** — running totals: blocked / allowed / FP rate / FN rate
- **DB Logging Toggle** — write events to SQLite (off by default)
- **Loop Mode** — replay baseline stream continuously

---

## 7. CLI REPL

No browser required:

```bash
python scripts/pipeline_repl.py
python scripts/pipeline_repl.py --with-baseline
```

| Command | Effect |
|---------|--------|
| `:help` | Show all commands |
| `:baseline on\|off` | Toggle baseline LLM |
| `:db on\|off` | Toggle SQLite logging |
| `:mlc <n>` | Show last n MLC events from DB |
| `:llm <n>` | Show last n LLM outputs from DB |
| `:reset_db` | Clear all DB tables |
| `:eval mixed <n>` | Batch eval n prompts (mixed labels) |
| `:backbones` | List discovered backbones |
| `:use main <id>` | Switch main backbone |

---

## 8. Project Structure

```
llm-poison-detector/
├── api/
│   └── dashboard_api.py        # FastAPI app (REST + WebSocket + lifespan)
├── static/
│   ├── index.html              # Dashboard UI
│   └── app.js                  # WebSocket client
├── scripts/
│   ├── models_runtime.py       # MLC + LLM inference, model cache
│   ├── db_runtime.py           # SQLite schema + logging
│   ├── check_env.py            # Env validation + model downloads
│   ├── download_default_backbone.py
│   └── pipeline_repl.py        # CLI REPL
├── schema/
│   └── 001_init.sql            # DB schema (mlc_events, llm_outputs, prompt_pool)
├── tests/
│   ├── test_run.py             # run.py helper unit tests
│   ├── test_db.py              # DB lifecycle tests
│   └── test_distribution.py   # Distribution integrity tests
├── models/                     # Placeholder — models downloaded here
├── db/                         # SQLite database
├── screenshot/                 # Dashboard screenshots
├── data/
│   └── merged_dataset_*.jsonl  # Built-in eval dataset
├── run.py                      # Entry point (env check → uvicorn → browser)
├── pyproject.toml              # PEP 517 build config + llmpoison CLI entry point
├── requirements.txt
├── setup.sh                    # Manual install — macOS / Linux / WSL
├── setup.ps1                   # Manual install — Windows
├── install.sh                  # One-liner installer — macOS / Linux / WSL
├── install.ps1                 # One-liner installer — Windows
├── install.cmd                 # One-liner installer — Windows CMD bootstrap
└── .github/workflows/ci.yml   # CI: 3 OS × Python 3.10 + 3.12
```

---

## 9. CI

GitHub Actions runs on every push and PR across Linux, macOS, and Windows with Python 3.10 and 3.12:

- Shell syntax check (`bash -n`) for `install.sh` / `setup.sh`
- PowerShell AST parse for `install.ps1` / `setup.ps1`
- Executable-bit check for `setup.sh` / `install.sh`
- Full pytest suite (`tests/`) — no model downloads required

---

## 10. Capabilities Summary

| Feature | Status |
|---------|--------|
| 5-class softmax safety classifier | ✔ |
| Auto-download safety model from HuggingFace | ✔ |
| TinyLlama 1.1B default backbone (public, no token) | ✔ |
| OpenAI-compatible API backend support | ✔ |
| Dual-LLM evaluation (main vs baseline) | ✔ |
| Realtime dashboard with probability charts | ✔ |
| VRAM release on idle (15 s WebSocket timeout) | ✔ |
| CLI REPL with backbone switching + DB tools | ✔ |
| SQLite logging for offline analysis | ✔ |
| Fully offline operation after first download | ✔ |
| One-liner install: Linux / macOS / Windows | ✔ |
| CI: 3 OS × 2 Python versions | ✔ |
