LLM Poison Detector — Local Safety Evaluation Sandbox + Realtime Dashboard + REPL

A fully local, script-based LLM safety sandbox for:

prompt safety classification

dataset scoring and evaluation

main vs baseline LLM comparison

adversarial prompt experiments

offline analysis with SQLite

The system runs entirely on your machine, supports GPU acceleration, and exposes:

a web dashboard (FastAPI + WebSocket + HTML/JS)

a CLI REPL (terminal only, no browser required)

The project depends on a safety classifier hosted on HuggingFace:

https://huggingface.co/rebas9512/llm-sandbox-safetymodel

This model is automatically downloaded on first run (via the env check script), or you can download and place it manually.

Note: the safety model is a multiclass softmax classifier (not multilabel).
The legacy name “MLC” is kept in code, but the model always predicts exactly one label per prompt.

Python Dependencies (install these first)

You must have a Python environment that can install and run the following stack:

# --- Web backend ---
fastapi>=0.115.0,<0.116.0
uvicorn[standard]>=0.30.0,<0.31.0
pydantic>=2.7.0,<3.0.0
python-dotenv>=1.0.1
requests>=2.31.0

# --- ML + HF stack ---
torch>=2.4.0      # GPU or CPU; install the appropriate build for your system
transformers>=4.44.0,<5.0.0
safetensors>=0.4.3
sentencepiece>=0.2.0
huggingface_hub>=0.22.0   # required for snapshot_download()

# --- Optional but recommended ---
accelerate>=0.33.0        # smoother HF pipeline loading


After cloning the repo, you can simply run:

pip install -r requirements.txt


The requirements.txt file matches the list above.

System Requirements

This repository does not ship with Docker images. You must bring your own:

Python 3.10–3.12

Working CUDA / GPU stack or CPU-only PyTorch build

Internet access for:

auto-downloading the safety model from
https://huggingface.co/rebas9512/llm-sandbox-safetymodel (first run)

optionally downloading the default TinyLlama backbone

Once models are downloaded, everything can run fully offline.

Quick Start (Recommended Path)

This is the fast path. If it fails, see “Manual Configuration Path” below.

3.1 Clone the repository
git clone https://github.com/Rebas9512/llm-poison-detector
cd llm-poison-detector

3.2 Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

3.3 Run the sandbox
python run.py


What run.py does:

loads .env (the repo already ships with a reasonable default)

runs scripts/check_env.py:

verifies Python + library environment

auto-downloads the safety model from
rebas9512/llm-sandbox-safetymodel if needed

checks for local LLM backbones or OpenAI-compatible APIs

if no local LLM model is found:

prints instructions

prompts you in the terminal:

enter a local HF model directory path, or

enter default to automatically run:

python scripts/download_default_backbone.py


which downloads TinyLlama/TinyLlama-1.1B-Chat-v1.0 (public, no token required) into
./models/TinyLlama-1.1B-Chat-v1.0 and uses it for both main and baseline for this run

starts the FastAPI app (Uvicorn)

opens your browser at:

http://127.0.0.1:8000/static/index.html


If everything is installed correctly, you should be able to:

watch the dashboard load

send a single prompt

run batch evaluations

view safety decisions and LLM outputs in real time

If run.py fails due to configuration issues, see the manual setup path next.

Manual Configuration Path (Optional)

Use this section only if:

you want to customize .env up front, or

the quick path above failed and you need to debug.

4.1 .env basics

A minimal .env (already included in the repo) looks like:

# Safety model (HF) – auto-downloaded if missing
MLC_MODEL_PATH=rebas9512/llm-sandbox-safetymodel

# Default local backbone (public, no token required)
# Download with:
#   python scripts/download_default_backbone.py
DEFAULT_BACKBONE_REPO=TinyLlama/TinyLlama-1.1B-Chat-v1.0
DEFAULT_BACKBONE_DIR=./models/TinyLlama-1.1B-Chat-v1.0

# Main LLM backend
MAIN_LLM_BACKEND=local
MAIN_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# Used only when backend=openai_api
MAIN_LLM_API_BASE_URL=http://localhost:1234/v1
MAIN_LLM_API_MODEL=default

# Baseline LLM backend
BASELINE_LLM_BACKEND=local
BASELINE_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# Used only when backend=openai_api
BASELINE_LLM_API_BASE_URL=http://localhost:1234/v1
BASELINE_LLM_API_MODEL=default

# Logging / DB
TEMP_EVENT_LOG_PATH=./temp_event_log/latest_eval.json
SQLITE_DB_PATH=./db/llm_poison.db
SCHEMA_PATH=./schema/001_init.sql

# Defaults
RISK_THRESHOLD=1
TEE_BASELINE=true
DROP_ON_BLOCK=false
LABEL_SCHEMA_VERSION=v1
SOURCE_TAG=manual

# Dashboard server
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000
DASHBOARD_URL=http://127.0.0.1:8000/static/index.html


Safety model options:

keep MLC_MODEL_PATH=rebas9512/llm-sandbox-safetymodel:

scripts/check_env.py will auto-download from HF and cache it under ./models/safetymodel

or set MLC_MODEL_PATH to a local directory where you have manually placed the contents of
https://huggingface.co/rebas9512/llm-sandbox-safetymodel

Backbone options:

if you want to use your own local LLM dir:

set MAIN_LLM_MODEL_PATH and BASELINE_LLM_MODEL_PATH to that path

if you want only an API backend:

set *_LLM_BACKEND=openai_api and configure *_API_BASE_URL and *_API_MODEL

4.2 Run environment check directly
python scripts/check_env.py


This script:

prints project root and device (CPU / CUDA / MPS)

ensures the safety model is available (auto-downloads from HF if missing)

enumerates available local LLM backbones:

*.MODEL_PATH from .env

HF-style model directories under ./models/* (excluding safetymodel)

checks OpenAI-compatible API endpoints if configured

validates SQLite DB path and schema (creates tables if needed)

if no local LLM is found and both main + baseline backends are local:

prints guidance

prompts you for:

a local HF model path, or

default to download TinyLlama via download_default_backbone.py

Once this passes, you can launch the dashboard with:

python run.py


Architecture Overview

5.1 Safety model (multiclass “MLC”)

HuggingFace sequence classifier with a softmax head

Predicts exactly one of:

clean

prompt_injection

malicious

semantic_poisoning

embedding_anomaly

Exposes:

per-class probabilities

risk_score (maximum harmful label probability)

decision (allow / block)

Hosted on HuggingFace:

repo id: rebas9512/llm-sandbox-safetymodel

Automatically downloaded if missing (via check_env.ensure_safety_model_local), or you can copy the files into ./models/safetymodel and set MLC_MODEL_PATH to that directory.

5.2 Dual-LLM pipeline

Two LLMs can be evaluated in parallel:

main LLM – the pipeline under test

baseline LLM – comparison model (never blocked by safety)

Both support:

local HF CausalLM directories

OpenAI-compatible APIs (LM Studio, vLLM, Ollama, custom servers)

Backbones are discovered in check_env.list_backbones() and can be selected:

through the dashboard:

GET /api/backbones

POST /api/backbones/select

from the REPL:

:backbones

:use main|baseline ID|IDX

5.3 Backend components

api/dashboard_api.py:

FastAPI app with endpoints:

POST /api/single

POST /api/batch

GET /api/backbones

POST /api/backbones/select

WebSocket endpoint:

/ws/dashboard (streams prompts, responses, metrics, batch status)

scripts/models_runtime.py:

wraps:

safety model (run_mlc, run_mlc_batch)

LLMs (run_llm, run_llm_batch)

handles tokenizer, padding, and device assignment

scripts/db_runtime.py:

SQLite schema management (via ensure_schema)

logging from the pipeline into:

mlc_events

llm_outputs

prompt_pool (evaluation prompts)

scripts/check_env.py:

environment check

safety model auto-download

backbone discovery and selection helpers

Web Dashboard

The dashboard is a pure HTML/JS app served from static/:

static/index.html – layout and controls

static/app.js – WebSocket client, UI logic, chart rendering

Features:

streaming panels:

incoming prompts (main + baseline)

LLM outputs

safety decisions and risk scores

fixed-size card layout:

long prompts and outputs scroll inside their containers

panel sizes do not change based on content

metrics panel:

total prompts, blocked / allowed counts

FP / FN rates

breakdown by clean vs harmful totals

probability chart:

safety label distribution visualization

backbone selector:

uses /api/backbones to list all backends

uses /api/backbones/select to switch main/baseline models at runtime

batch controls:

label mode (e.g. clean / malicious / mixed)

batch size

mode (safety-only or dual)

DB logging toggle (defaults off)

loop mode:

visual replay loop for the baseline stream

CLI REPL (no UI)

If you prefer to work in the terminal, you can use the REPL instead of the dashboard.

7.1 Running the REPL

From the project root:

# main path only
python scripts/pipeline_repl.py

# main + baseline paths
python scripts/pipeline_repl.py --with-baseline


On startup, the REPL:

validates files and schema (check_files)

prints an environment summary (print_env_summary)

then enters an interactive loop:

Simple LLM poison sandbox REPL
Type :help for commands, :quit to exit.

prompt>

7.2 Using the REPL

Any line not starting with : is treated as a prompt:

runs safety model (decision, risk score, best label)

runs main LLM (possibly skipped if blocked)

optionally runs baseline LLM (never blocked)

prints a structured result for both paths

Commands (type :help to see):

:help                  Show help
:quit / :exit          Exit
:baseline              Show baseline status
:baseline on|off       Enable/disable baseline path
:db                    Show DB logging status
:db on|off             Enable/disable DB logging
:mlc [N]               Show latest N rows from mlc_events
:llm [N]               Show latest N rows from llm_outputs
:reset_db              Reset eval DB (mlc_events + llm_outputs)
:eval MODE N           Run batch eval from DB
:backbones             List available LLM backbones
:use main ID|IDX       Select backbone for main LLM
:use baseline ID|IDX   Select backbone for baseline LLM


Highlights:

:baseline on / off

toggles whether baseline LLM runs for new prompts

:db on / off

toggles DB logging for safety and LLM outputs

:mlc 10, :llm 10

inspect recent rows in mlc_events / llm_outputs

:reset_db

clears evaluation tables (safe for experimentation)

:eval mixed 100

runs a batch evaluation from prompt_pool:

uses run_mlc_batch and run_llm_batch

optionally runs baseline path

writes a JSON report to TEMP_EVENT_LOG_PATH
(default temp_event_log/latest_eval.json)

:backbones

shows all discovered backbones with indices

:use main 0 (or :use baseline 0)

switches main or baseline to a chosen backbone by index or id, then reloads models_runtime so settings take effect immediately

This REPL exposes the same core pipeline as the dashboard, but in a text-based interface that is convenient for quick experiments and DB inspection.

Project Layout

llm-poison-detector/
├── api/
│   └── dashboard_api.py          # FastAPI app and WebSocket handlers
│
├── static/
│   ├── index.html                # Dashboard UI
│   └── app.js                    # JS app + WebSocket client
│
├── scripts/
│   ├── check_env.py              # Env check + safety model auto-download + backbone discovery
│   ├── models_runtime.py         # Safety + LLM inference wrappers
│   ├── db_runtime.py             # SQLite schema and logging helpers
│   ├── download_default_backbone.py  # Downloads TinyLlama default backbone
│   ├── pipeline_repl.py          # CLI REPL for prompts, batches, and DB view
│   └── __init__.py (optional)
│
├── models/
│   ├── safetymodel/              # Auto-downloaded safety classifier (HF snapshot)
│   └── TinyLlama-1.1B-Chat-v1.0/ # Default backbone (optional, script-downloaded)
│
├── data/
│   └── merged_dataset_v4_clean2x_harm.jsonl
│
├── db/
│   └── llm_poison.db             # Prompt pool and evaluation logs
│
├── schema/
│   └── 001_init.sql              # DB schema
│
├── temp_event_log/
│   └── latest_eval.json          # Last batch run summary
│
├── run.py                        # Main entrypoint (env check + uvicorn + browser)
├── requirements.txt              # Matches the dependency list above
└── readme.md


Current Capabilities (Summary)

Multiclass softmax safety classifier (5 labels)

HF-hosted safety model (rebas9512/llm-sandbox-safetymodel) with automatic local download

Optional, token-free default backbone (TinyLlama 1.1B Chat) via a single script or via the env check prompt

Dual-LLM pipeline: main + baseline

Web dashboard with realtime streaming, metrics, charts, backbone selection, and batch evaluation

CLI REPL with:

single prompt evaluation

DB logging toggle

batch evaluation from DB

inspection of mlc_events and llm_outputs

backbone listing and selection

SQLite-backed logging and JSON exports for offline analysis

Entirely script-based, no build tools, offline-compatible once models are downloaded