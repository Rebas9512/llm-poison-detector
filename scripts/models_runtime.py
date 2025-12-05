import os

# Disable torch.compile / inductor to avoid Triton build
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
os.environ.setdefault("TORCHINDUCTOR_DISABLE", "1")

import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional, List

import torch
from dotenv import load_dotenv
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    AutoModelForCausalLM,
)

try:
    import requests
except ImportError:
    requests = None

load_dotenv()

# Project root = parent of this "scripts" folder
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(p: str) -> str:
    if not p:
        raise ValueError("Empty path")
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def select_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if platform.system() == "Darwin" and getattr(torch.backends, "mps", None):
        if torch.backends.mps.is_available():
            return "mps"
    return "cpu"


# Safety model (fixed safetymodel dir by default)
MLC_MODEL_PATH = resolve_path(os.getenv("MLC_MODEL_PATH", "./models/safetymodel"))

# LLM backends / paths
MAIN_LLM_BACKEND = os.getenv("MAIN_LLM_BACKEND", "local").lower()
MAIN_LLM_MODEL_PATH = resolve_path(
    os.getenv("MAIN_LLM_MODEL_PATH", "./models/Llama-3.2-1B-Instruct")
)
MAIN_LLM_API_BASE_URL = os.getenv("MAIN_LLM_API_BASE_URL")
MAIN_LLM_API_MODEL = os.getenv("MAIN_LLM_API_MODEL", "default")

BASELINE_LLM_BACKEND = os.getenv("BASELINE_LLM_BACKEND", "local").lower()
BASELINE_LLM_MODEL_PATH = resolve_path(
    os.getenv("BASELINE_LLM_MODEL_PATH", "./models/Llama-3.2-1B-Instruct")
)
BASELINE_LLM_API_BASE_URL = os.getenv("BASELINE_LLM_API_BASE_URL")
BASELINE_LLM_API_MODEL = os.getenv("BASELINE_LLM_API_MODEL", "default")

RISK_THRESHOLD_DEFAULT = float(os.getenv("RISK_THRESHOLD", "0.5"))

DEVICE = select_device()

# MLC labels
MLC_LABEL_NAMES = [
    "prompt_injection",
    "malicious",
    "semantic_poisoning",
    "embedding_anomaly",
    "clean",
]
MLC_RISK_LABELS = [
    "prompt_injection",
    "malicious",
    "semantic_poisoning",
    "embedding_anomaly",
]

_MLC_CACHE: Dict[str, Any] = {}
_LLM_CACHE: Dict[str, Any] = {}


def _resolve_llm_backend(model_path: str) -> tuple[str, Optional[str], Optional[str]]:
    if model_path == MAIN_LLM_MODEL_PATH:
        return MAIN_LLM_BACKEND, MAIN_LLM_API_BASE_URL, MAIN_LLM_API_MODEL
    if model_path == BASELINE_LLM_MODEL_PATH:
        return BASELINE_LLM_BACKEND, BASELINE_LLM_API_BASE_URL, BASELINE_LLM_API_MODEL
    return "local", None, None


def _openai_api_generate(
    base_url: str,
    model: str,
    prompt: str,
    max_new_tokens: int,
) -> str:
    if not requests:
        raise RuntimeError("requests package not installed.")
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_new_tokens,
        "temperature": 0.0,
    }
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    return (content or "").strip()


def _load_mlc_model() -> tuple[AutoTokenizer, AutoModelForSequenceClassification]:
    key = (MLC_MODEL_PATH, DEVICE)
    if key in _MLC_CACHE:
        return _MLC_CACHE[key]  # type: ignore[return-value]

    tokenizer = AutoTokenizer.from_pretrained(MLC_MODEL_PATH)
    model = AutoModelForSequenceClassification.from_pretrained(
        MLC_MODEL_PATH,
        num_labels=len(MLC_LABEL_NAMES),
    )
    model.to(DEVICE)
    model.eval()
    _MLC_CACHE[key] = (tokenizer, model)
    return tokenizer, model  # type: ignore[return-value]


@torch.no_grad()
def run_mlc(
    text: str,
    threshold: Optional[float] = None,
    max_length: int = 256,
) -> Dict[str, Any]:
    """Softmax-based safety classification for a single input."""
    if threshold is None:
        threshold = RISK_THRESHOLD_DEFAULT

    tok, mdl = _load_mlc_model()

    enc = tok(
        text,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    enc = {k: v.to(DEVICE) for k, v in enc.items()}

    outputs = mdl(**enc)
    probs = torch.softmax(outputs.logits, dim=-1)[0].cpu().numpy()

    label_probs = {
        MLC_LABEL_NAMES[i]: float(probs[i])
        for i in range(len(MLC_LABEL_NAMES))
    }

    clean_prob = label_probs["clean"]
    risk_score = 1.0 - clean_prob  # total risk probability
    best_risk_label = max(MLC_RISK_LABELS, key=lambda n: label_probs[n])
    decision = "block" if risk_score >= threshold else "allow"

    return {
        "text": text,
        "label_probs": label_probs,
        "risk_score": float(risk_score),
        "clean_prob": float(clean_prob),
        "best_risk_label": best_risk_label,
        "risk_labels": list(MLC_RISK_LABELS),
        "risk_threshold": float(threshold),
        "decision": decision,
        "version": "safetymodel_v1",
    }


@torch.no_grad()
def run_mlc_batch(
    texts: List[str],
    threshold: Optional[float] = None,
    max_length: int = 256,
) -> List[Dict[str, Any]]:
    """Softmax-based safety classification for a batch of inputs."""
    if threshold is None:
        threshold = RISK_THRESHOLD_DEFAULT
    if not texts:
        return []

    tok, mdl = _load_mlc_model()

    enc = tok(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    enc = {k: v.to(DEVICE) for k, v in enc.items()}

    outputs = mdl(**enc)
    probs = torch.softmax(outputs.logits, dim=-1).cpu().numpy()

    results = []
    for idx, text in enumerate(texts):
        row = probs[idx]

        label_probs = {
            MLC_LABEL_NAMES[i]: float(row[i])
            for i in range(len(MLC_LABEL_NAMES))
        }

        clean_prob = label_probs["clean"]
        risk_score = 1.0 - clean_prob
        best_risk_label = max(MLC_RISK_LABELS, key=lambda n: label_probs[n])
        decision = "block" if risk_score >= threshold else "allow"

        results.append(
            {
                "text": text,
                "label_probs": label_probs,
                "risk_score": float(risk_score),
                "clean_prob": float(clean_prob),
                "best_risk_label": best_risk_label,
                "risk_labels": list(MLC_RISK_LABELS),
                "risk_threshold": float(threshold),
                "decision": decision,
                "version": "safetymodel_v1",
            }
        )

    return results


def _load_llm(model_path: str) -> tuple[Any, Any]:
    key = (model_path, DEVICE)
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(model_path)

    if tokenizer.pad_token is None:
        if tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        else:
            tokenizer.add_special_tokens({"pad_token": "[PAD]"})
            model.resize_token_embeddings(len(tokenizer))
    if getattr(model.config, "pad_token_id", None) is None:
        model.config.pad_token_id = tokenizer.pad_token_id

    model.to(DEVICE)
    model.eval()
    _LLM_CACHE[key] = (tokenizer, model)
    return tokenizer, model


def _stub_blocked(model_id: str, reason: str, extra_error: Optional[str] = None) -> Dict[str, Any]:
    now = time.time()
    payload: Dict[str, Any] = {
        "response": "prompt has been blocked by safety module." if reason == "safety_blocked" else "",
        "model_id": model_id,
        "llm_main": {
            "model_id": model_id,
            "generated_at": now,
            "skipped": True,
            "reason": reason,
        },
    }
    if extra_error is not None:
        payload["response"] = extra_error
        payload["llm_main"]["error"] = extra_error  # type: ignore[index]
    return payload


def run_llm(
    text: str,
    *,
    blocked: bool = False,
    model_path: Optional[str] = None,
    max_new_tokens: int = 128,
) -> Dict[str, Any]:
    if model_path is None:
        model_path = MAIN_LLM_MODEL_PATH

    backend, api_base, api_model = _resolve_llm_backend(model_path)
    model_id = api_model if backend == "openai_api" and api_model else model_path

    if blocked:
        return _stub_blocked(model_id, "safety_blocked")

    if not (text or "").strip():
        now = time.time()
        return {
            "response": "empty prompt.",
            "model_id": model_id,
            "llm_main": {
                "model_id": model_id,
                "generated_at": now,
                "skipped": True,
                "reason": "empty_text",
            },
        }

    # OpenAI-compatible backend
    if backend == "openai_api":
        if not (api_base and api_model):
            return _stub_blocked(
                model_id,
                "model_error",
                "openai_api backend requires *_API_BASE_URL and *_API_MODEL.",
            )
        try:
            response = _openai_api_generate(api_base, api_model, text, max_new_tokens)
        except Exception as e:  # noqa: BLE001
            return _stub_blocked(model_id, "generation_error", f"openai_api generation error: {e}")

        return {
            "response": response,
            "model_id": model_id,
            "llm_main": {
                "model_id": model_id,
                "generated_at": time.time(),
                "skipped": False,
                "reason": None,
            },
        }

    # Local HF backend
    try:
        tok, mdl = _load_llm(model_path)
    except Exception as e:  # noqa: BLE001
        return _stub_blocked(model_id, "model_error", f"model load error: {e}")

    try:
        inputs = tok(text, return_tensors="pt").to(DEVICE)
        input_ids = inputs["input_ids"]
        out_ids = mdl.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
        prompt_len = input_ids.shape[1]
        gen_ids = out_ids[0, prompt_len:]
        if gen_ids.numel() > 0:
            decoded = tok.decode(gen_ids, skip_special_tokens=True)
        else:
            decoded = tok.decode(out_ids[0], skip_special_tokens=True)
        response = decoded.strip()
    except Exception as e:  # noqa: BLE001
        return _stub_blocked(model_id, "generation_error", f"generation error: {e}")

    return {
        "response": response,
        "model_id": model_id,
        "llm_main": {
            "model_id": model_id,
            "generated_at": time.time(),
            "skipped": False,
            "reason": None,
        },
    }


@torch.no_grad()
def run_llm_batch(
    texts: List[str],
    *,
    blocked_flags: Optional[List[bool]] = None,
    model_path: Optional[str] = None,
    max_new_tokens: int = 128,
    micro_batch_size: int = 16,
) -> List[Dict[str, Any]]:
    if model_path is None:
        model_path = MAIN_LLM_MODEL_PATH

    backend, api_base, api_model = _resolve_llm_backend(model_path)
    model_id = api_model if backend == "openai_api" and api_model else model_path

    n = len(texts)
    if n == 0:
        return []

    if blocked_flags is None:
        blocked_flags = [False] * n
    if len(blocked_flags) != n:
        raise ValueError("blocked_flags length must match texts length")

    results: List[Optional[Dict[str, Any]]] = [None] * n
    to_generate: List[int] = []

    for i, (text, blocked) in enumerate(zip(texts, blocked_flags)):
        if blocked:
            results[i] = _stub_blocked(model_id, "safety_blocked")
        elif not (text or "").strip():
            now = time.time()
            results[i] = {
                "response": "empty prompt.",
                "model_id": model_id,
                "llm_main": {
                    "model_id": model_id,
                    "generated_at": now,
                    "skipped": True,
                    "reason": "empty_text",
                },
            }
        else:
            to_generate.append(i)

    if not to_generate:
        return [r for r in results if r is not None]  # type: ignore[list-item]

    # OpenAI-compatible backend: per-item calls
    if backend == "openai_api":
        if not (api_base and api_model):
            err = _stub_blocked(
                model_id,
                "model_error",
                "openai_api backend requires *_API_BASE_URL and *_API_MODEL.",
            )
            for i in to_generate:
                results[i] = err
            return [r for r in results if r is not None]  # type: ignore[list-item]

        for i in to_generate:
            try:
                response = _openai_api_generate(api_base, api_model, texts[i], max_new_tokens)
                results[i] = {
                    "response": response,
                    "model_id": model_id,
                    "llm_main": {
                        "model_id": model_id,
                        "generated_at": time.time(),
                        "skipped": False,
                        "reason": None,
                    },
                }
            except Exception as e:  # noqa: BLE001
                results[i] = _stub_blocked(
                    model_id,
                    "generation_error",
                    f"openai_api generation error: {e}",
                )

        return [r for r in results if r is not None]  # type: ignore[list-item]

    # Local HF backend: micro-batch
    try:
        tok, mdl = _load_llm(model_path)
    except Exception as e:  # noqa: BLE001
        err = _stub_blocked(model_id, "model_error", f"model load error: {e}")
        for i in to_generate:
            results[i] = err
        return [r for r in results if r is not None]  # type: ignore[list-item]

    for start in range(0, len(to_generate), micro_batch_size):
        chunk_idx = to_generate[start : start + micro_batch_size]
        chunk_texts = [texts[i] for i in chunk_idx]

        try:
            inputs = tok(chunk_texts, return_tensors="pt", padding=True).to(DEVICE)
            input_ids = inputs["input_ids"]
            attn = inputs.get("attention_mask", None)

            out_ids = mdl.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )

            for row_offset, (idx, out) in enumerate(zip(chunk_idx, out_ids)):
                if attn is not None:
                    prompt_len = int(attn[row_offset].sum().item())
                else:
                    if tok.pad_token_id is not None:
                        prompt_len = int(
                            (input_ids[row_offset] != tok.pad_token_id).sum().item()
                        )
                    else:
                        prompt_len = input_ids.shape[1]

                gen_ids = out[prompt_len:]
                if gen_ids.numel() > 0:
                    decoded = tok.decode(gen_ids, skip_special_tokens=True)
                else:
                    decoded = tok.decode(out, skip_special_tokens=True)
                response = decoded.strip()

                results[idx] = {
                    "response": response,
                    "model_id": model_id,
                    "llm_main": {
                        "model_id": model_id,
                        "generated_at": time.time(),
                        "skipped": False,
                        "reason": None,
                    },
                }
        except Exception as e:  # noqa: BLE001
            err = _stub_blocked(model_id, "generation_error", f"generation error: {e}")
            for idx in chunk_idx:
                results[idx] = err

    return [r for r in results if r is not None]  # type: ignore[list-item]
