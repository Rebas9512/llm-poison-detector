LLM Poison Detector — Local Safety Evaluation Sandbox

Realtime Dashboard + Local Pipeline + REPL

A fully local, script-based sandbox for evaluating LLM safety behavior:

prompt safety classification

dataset scoring & evaluation

main vs. baseline LLM comparison

adversarial prompt experimentation

offline logging via SQLite

The system runs entirely on your machine, supports GPU acceleration, and includes:

Web Dashboard (FastAPI + WebSocket + pure HTML/JS)

CLI REPL (no browser required)

This project depends on a multiclass softmax safety classifier hosted on HuggingFace:

👉 https://huggingface.co/rebas9512/llm-sandbox-safetymodel

It is auto-downloaded on first run, or you can download it manually.

⚠️ Note
The safety model predicts exactly one label, not multilabel.
The legacy name “MLC” remains in code for compatibility.

1. Python Requirements

You must install the following before running anything:

# --- Web backend ---
fastapi>=0.115.0,<0.116.0
uvicorn[standard]>=0.30.0,<0.31.0
pydantic>=2.7.0,<3.0.0
python-dotenv>=1.0.1
requests>=2.31.0

# --- ML + HF stack ---
torch>=2.4.0
transformers>=4.44.0,<5.0.0
safetensors>=0.4.3
sentencepiece>=0.2.0
huggingface_hub>=0.22.0

# --- Optional but recommended ---
accelerate>=0.33.0


Install them:

pip install -r requirements.txt

System prerequisites

Python 3.10–3.12

CUDA-enabled PyTorch or CPU-only PyTorch

Internet access only for first run to download:

safety model (rebas9512/llm-sandbox-safetymodel)

optional default backbone (TinyLlama-1.1B-Chat-v1.0)

Once downloaded, everything runs fully offline.

2. Quick Start (Recommended)

This is the fastest way to get the sandbox running.
If anything breaks, see Manual Configuration below.

2.1 Clone the repo
git clone https://github.com/Rebas9512/llm-poison-detector
cd llm-poison-detector

2.2 Create environment & install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

2.3 Run the sandbox
python run.py

What happens when you run run.py:

Loads .env (repo ships with good defaults).

Runs scripts/check_env.py:

validates environment

ensures safety model is downloaded

detects available local LLMs or API backends

if no local LLM found:

prints instructions

prompts you:

Enter local HF model path, or "default" to download TinyLlama:


entering default auto-executes:

python scripts/download_default_backbone.py


(downloads TinyLlama-1.1B-Chat, no token needed)

Starts FastAPI backend

Opens browser at:

http://127.0.0.1:8000/static/index.html


You can now:

send prompts

inspect safety scores & MLC probability bars

compare main vs baseline

run batch evaluations

switch LLM backbones live

3. Manual Configuration (Optional)

Only needed if you prefer to configure .env manually.

A minimal .env (already included) looks like:

# Safety model (auto-downloads if missing)
MLC_MODEL_PATH=rebas9512/llm-sandbox-safetymodel

# Default backbone (public, no token)
DEFAULT_BACKBONE_REPO=TinyLlama/TinyLlama-1.1B-Chat-v1.0
DEFAULT_BACKBONE_DIR=./models/TinyLlama-1.1B-Chat-v1.0

# Main LLM
MAIN_LLM_BACKEND=local
MAIN_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# Baseline LLM
BASELINE_LLM_BACKEND=local
BASELINE_LLM_MODEL_PATH=./models/TinyLlama-1.1B-Chat-v1.0

# DB
TEMP_EVENT_LOG_PATH=./temp_event_log/latest_eval.json
SQLITE_DB_PATH=./db/llm_poison.db
SCHEMA_PATH=./schema/001_init.sql

# Pipeline defaults
RISK_THRESHOLD=1
TEE_BASELINE=true
DROP_ON_BLOCK=false
LABEL_SCHEMA_VERSION=v1
SOURCE_TAG=manual

# Dashboard
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000


To run environment validation manually:

python scripts/check_env.py


This will:

print device info (CPU / CUDA / MPS)

download the safety model if missing

detect local LLM directories

validate DB schema

prompt for backbone selection if none exist

4. Architecture Overview
4.1 Safety Model (multiclass softmax)

Predicts exactly one of:

clean

prompt_injection

malicious

semantic_poisoning

embedding_anomaly

Outputs include:

per-class probabilities

risk score (max harmful prob)

decision (allow / block)

Hosted at: https://huggingface.co/rebas9512/llm-sandbox-safetymodel

Auto-downloaded into ./models/safetymodel.

4.2 Dual-LLM Pipeline

Both main and baseline can be:

local HF CausalLM directories

OpenAI-compatible servers (LM Studio, vLLM, Ollama, custom API)

Backbones are discovered via check_env.list_backbones() and can be selected:

Dashboard → Backend selector

REPL → :backbones, :use main 0, etc.

4.3 Backend Components
Module	Purpose
api/dashboard_api.py	REST + WS endpoints
scripts/models_runtime.py	MLC + LLM inference
scripts/db_runtime.py	SQLite schema + logging
scripts/check_env.py	environment validation + model downloads
scripts/download_default_backbone.py	downloads TinyLlama
scripts/pipeline_repl.py	CLI REPL
5. Web Dashboard

Located in static/:

index.html — UI

app.js — WebSocket client

Features:

realtime prompt + response streaming

fixed panel layout (long content scrolls)

probability chart

backbone switching

batch evaluation tools

loop mode (replay baseline stream)

DB logging toggle (default OFF)

6. CLI REPL (No UI Needed)

Run:

python scripts/pipeline_repl.py


or with baseline:

python scripts/pipeline_repl.py --with-baseline


Type prompts directly, or use commands:

:help
:baseline on|off
:db on|off
:mlc 10
:llm 10
:reset_db
:eval mixed 100
:backbones
:use main 0


The REPL provides:

single prompt evaluation

batch testing

DB inspection

backbone switching

Everything the dashboard can do, but in terminal form.

7. Project Structure
llm-poison-detector/
├── api/
├── static/                      # dashboard UI
├── scripts/                     # core pipeline, env check, REPL
├── models/                      # placeholder; models downloaded here
├── data/
├── db/
├── schema/
├── temp_event_log/
├── run.py                       # main entrypoint
└── readme.md

8. Capabilities Summary

✔ Multiclass softmax safety classifier (5 labels)

✔ Auto-download safety model from HuggingFace

✔ Optional TinyLlama 1.1B (public, no token)

✔ Dual-LLM evaluation (main + baseline)

✔ Realtime dashboard with charts & streaming

✔ REPL with backbone switching + DB tools

✔ SQLite logging for offline analysis

✔ Fully offline operation once models downloaded

✔ Zero build system (pure Python + HTML/JS)