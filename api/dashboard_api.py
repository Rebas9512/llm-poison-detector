# dashboard_api.py
from typing import Any, Dict, List, Literal, Optional, Set
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import asyncio
import gc
import os
import sys
import uuid
import importlib
import random

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts import db_runtime
from scripts.db_runtime import fetch_eval_prompts
import scripts.models_runtime as mr
from scripts.check_env import list_backbones, select_backbone


ModeType = Literal["safety_only", "main_only", "baseline_only", "dual"]


class SinglePromptRequest(BaseModel):
    prompt: str
    mode: ModeType = "dual"
    risk_threshold: Optional[float] = None
    log_to_db: bool = True
    label_hint: Optional[str] = None


class BatchRequest(BaseModel):
    label_mode: str = "mixed"
    batch_size: int = 100
    mode: ModeType = "dual"
    risk_threshold: Optional[float] = None
    log_to_db: bool = True


class PromptEvent(BaseModel):
    type: Literal["prompt"]
    data: Dict[str, Any]


class ResponseEvent(BaseModel):
    type: Literal["response"]
    data: Dict[str, Any]


class MetricsEvent(BaseModel):
    type: Literal["metrics"]
    data: Dict[str, Any]


class BatchStatusEvent(BaseModel):
    type: Literal["batch_status"]
    data: Dict[str, Any]


class SelectBackboneRequest(BaseModel):
    backbone_id: str


WSMessage = PromptEvent | ResponseEvent | MetricsEvent | BatchStatusEvent

# How long (seconds) all clients must be absent before models are unloaded.
_IDLE_UNLOAD_DELAY: float = float(os.getenv("LLP_IDLE_UNLOAD_DELAY", "15"))


# ---------------------------------------------------------------------
# Model memory management
# ---------------------------------------------------------------------


def _unload_models() -> None:
    """Clear model caches and free GPU VRAM in this process."""
    with suppress(Exception):
        _mr = sys.modules.get("scripts.models_runtime")
        if _mr is not None:
            _mr._MLC_CACHE.clear()
            _mr._LLM_CACHE.clear()
    gc.collect()
    with suppress(Exception):
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            with suppress(Exception):
                torch.cuda.ipc_collect()


# ---------------------------------------------------------------------
# WebSocket connection + metrics
# ---------------------------------------------------------------------


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self._idle_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        # Cancel any pending idle-unload timer when a client reconnects.
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None

    async def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        # Schedule model unload if no clients remain.
        if not self.active_connections:
            self._idle_task = asyncio.create_task(
                self._idle_unload(_IDLE_UNLOAD_DELAY)
            )

    async def _idle_unload(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if not self.active_connections:
            print(
                f"[api] All clients disconnected for {delay:.0f}s "
                "— unloading models to free VRAM."
            )
            _unload_models()

    async def broadcast(self, message: WSMessage | Dict[str, Any]) -> None:
        if hasattr(message, "model_dump"):
            payload = message.model_dump()
        else:
            payload = message

        dead: List[WebSocket] = []
        for conn in list(self.active_connections):
            try:
                await conn.send_json(payload)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.active_connections.discard(d)


manager = ConnectionManager()


@dataclass
class MetricsState:
    total: int = 0
    blocked: int = 0
    allowed: int = 0
    attack_total: int = 0
    clean_total: int = 0
    correct_block: int = 0
    miss: int = 0
    false_positive: int = 0

    def to_dict(self) -> Dict[str, Any]:
        blocked_rate = self.blocked / self.total if self.total else 0.0
        fp_rate = (self.false_positive / self.clean_total) if self.clean_total else 0.0
        fn_rate = (self.miss / self.attack_total) if self.attack_total else 0.0
        return {
            "total": self.total,
            "blocked": self.blocked,
            "allowed": self.allowed,
            "blocked_rate": blocked_rate,
            "attack_total": self.attack_total,
            "clean_total": self.clean_total,
            "correct_block": self.correct_block,
            "miss": self.miss,
            "false_positive": self.false_positive,
            "fp_rate": fp_rate,
            "fn_rate": fn_rate,
        }


metrics_state = MetricsState()


def update_metrics(decision: str, label: Optional[str]) -> None:
    metrics_state.total += 1
    if decision == "block":
        metrics_state.blocked += 1
    else:
        metrics_state.allowed += 1

    if label is None:
        return

    if label == "clean":
        metrics_state.clean_total += 1
        if decision == "block":
            metrics_state.false_positive += 1
    else:
        metrics_state.attack_total += 1
        if decision == "block":
            metrics_state.correct_block += 1
        else:
            metrics_state.miss += 1


def build_metrics_event() -> MetricsEvent:
    return MetricsEvent(type="metrics", data=metrics_state.to_dict())


# ---------------------------------------------------------------------
# Backbone helpers
# ---------------------------------------------------------------------


def _describe_backbones() -> Dict[str, Any]:
    """Return backbone metadata for the dashboard API."""
    raw = list_backbones()
    backbones: List[Dict[str, Any]] = []

    for b in raw:
        path = b.get("path")
        name = b.get("name", "") or ""
        if path and path == mr.MLC_MODEL_PATH:
            continue
        lname = name.lower()
        if "safety" in lname or "safe" in lname:
            continue

        bk = dict(b)
        if bk["kind"] == "local":
            disp = bk.get("name") or "Local HF Model"
            detail = bk.get("path") or ""
        else:
            base = bk.get("base_url") or ""
            disp = f"API @ {base}"
            detail = f"{base} (OpenAI-compatible endpoint)"

        bk["display"] = disp
        bk["detail"] = detail
        backbones.append(bk)

    active_id: Optional[str] = None
    for bk in backbones:
        if mr.MAIN_LLM_BACKEND == "local" and bk["kind"] == "local":
            if bk.get("path") == mr.MAIN_LLM_MODEL_PATH:
                active_id = bk["id"]
                break
        if mr.MAIN_LLM_BACKEND == "openai_api" and bk["kind"] == "openai_api":
            if bk.get("base_url") == mr.MAIN_LLM_API_BASE_URL:
                active_id = bk["id"]
                break

    return {
        "backbones": backbones,
        "active_id": active_id,
        "main": {
            "backend": mr.MAIN_LLM_BACKEND,
            "model_path": mr.MAIN_LLM_MODEL_PATH,
            "api_base_url": mr.MAIN_LLM_API_BASE_URL,
            "api_model": mr.MAIN_LLM_API_MODEL,
        },
        "baseline": {
            "backend": mr.BASELINE_LLM_BACKEND,
            "model_path": mr.BASELINE_LLM_MODEL_PATH,
            "api_base_url": mr.BASELINE_LLM_API_BASE_URL,
            "api_model": mr.BASELINE_LLM_API_MODEL,
        },
    }


# ---------------------------------------------------------------------
# Sampling helpers for batch
# ---------------------------------------------------------------------


def _sample_prompts_for_batch(
    label_mode: str,
    batch_size: int,
) -> List[Dict[str, Any]]:
    """Sample prompts for batch mode with optional label-aware mixing."""
    if batch_size <= 0:
        return []

    # Non-mixed: simple filtered sampling
    if label_mode != "mixed":
        return fetch_eval_prompts(
            limit=batch_size,
            labels=[label_mode],
            source=None,
            only_gold=True,
        )

    # Mixed with very small batch: simple random mix
    if batch_size < 5:
        return fetch_eval_prompts(
            limit=batch_size,
            labels=None,
            source=None,
            only_gold=True,
        )

    clean_label = "clean"
    harm_labels = ["malicious", "prompt_injection", "semantic_poisoning", "embedding_anomaly"]

    # Target ratio: clean : harmful ≈ 2 : 1
    harm_target = max(1, batch_size // 3)
    clean_target = batch_size - harm_target

    # Ensure capacity for all harmful labels when possible
    if harm_target < len(harm_labels):
        harm_target = len(harm_labels)
        clean_target = max(0, batch_size - harm_target)

    samples_by_id: Dict[int, Dict[str, Any]] = {}

    def _add_samples(items: List[Dict[str, Any]]) -> None:
        for s in items:
            samples_by_id[s["prompt_id"]] = s

    # Clean samples
    clean_samples = fetch_eval_prompts(
        limit=clean_target,
        labels=[clean_label],
        source=None,
        only_gold=True,
    )
    _add_samples(clean_samples)

    # Seed: at least one per harmful label (if available)
    seed_harm: List[Dict[str, Any]] = []
    for lbl in harm_labels:
        one = fetch_eval_prompts(
            limit=1,
            labels=[lbl],
            source=None,
            only_gold=True,
        )
        if one:
            seed_harm.extend(one)
    _add_samples(seed_harm)

    # Extra harmful to reach harm_target
    current_harm = sum(1 for s in samples_by_id.values() if s["label"] != clean_label)
    need_more_harm = max(0, harm_target - current_harm)

    if need_more_harm > 0:
        extra_harm = fetch_eval_prompts(
            limit=need_more_harm * 2,
            labels=harm_labels,
            source=None,
            only_gold=True,
        )
        _add_samples(extra_harm)

    all_samples = list(samples_by_id.values())
    if not all_samples:
        return []

    # Top up with fully random mixed if still short
    if len(all_samples) < batch_size:
        extra_any = fetch_eval_prompts(
            limit=batch_size * 2,
            labels=None,
            source=None,
            only_gold=True,
        )
        _add_samples(extra_any)
        all_samples = list(samples_by_id.values())

    clean_list = [s for s in all_samples if s["label"] == clean_label]
    harm_list = [s for s in all_samples if s["label"] != clean_label]

    random.shuffle(clean_list)
    random.shuffle(harm_list)

    target_harm = min(len(harm_list), harm_target)
    target_clean = min(len(clean_list), max(0, batch_size - target_harm))

    selected: List[Dict[str, Any]] = []
    selected.extend(harm_list[:target_harm])
    selected.extend(clean_list[:target_clean])

    if len(selected) < batch_size:
        remaining = harm_list[target_harm:] + clean_list[target_clean:]
        random.shuffle(remaining)
        needed = batch_size - len(selected)
        selected.extend(remaining[:needed])

    random.shuffle(selected)
    return selected[:batch_size]


# ---------------------------------------------------------------------
# Pipeline adapters
# ---------------------------------------------------------------------


def process_single_for_api(
    text: str,
    *,
    mode: ModeType,
    risk_threshold: Optional[float],
    log_to_db: bool,
    label_hint: Optional[str],
) -> Dict[str, Any]:
    """Run a single prompt through safety + main/baseline LLMs."""
    request_id = str(uuid.uuid4())

    detect: Optional[Dict[str, Any]] = None
    decision = "allow"
    blocked = False
    thr = risk_threshold or mr.RISK_THRESHOLD_DEFAULT

    # Safety model
    if mode in ("safety_only", "main_only", "dual"):
        detect = mr.run_mlc(text, threshold=thr)
        decision = detect.get("decision", "allow")
        blocked = decision != "allow"

        update_metrics(decision, label_hint)

        if log_to_db and detect is not None:
            try:
                db_runtime.log_mlc_event(
                    db_path=None,
                    request_id=request_id,
                    source="dashboard_single",
                    text=text,
                    detector_version="dashboard_mlc",
                    label_schema_version=None,
                    detect_result={**detect},
                    risk_threshold=thr,
                    is_baseline=False,
                    is_gold=False,
                    tee_to_baseline=(mode in ("baseline_only", "dual")),
                    prompt_id=None,
                    error_json=None,
                )
            except Exception as e:
                print("[dashboard_api] log_mlc_event(single) failed:", e)

    # Main LLM
    main_out: Optional[Dict[str, Any]] = None
    if mode in ("main_only", "dual"):
        main_out = mr.run_llm(
            text,
            blocked=blocked,
            model_path=mr.MAIN_LLM_MODEL_PATH,
        )

        if log_to_db and main_out is not None:
            try:
                db_runtime.log_llm_output(
                    db_path=None,
                    request_id=request_id,
                    prompt_text=text,
                    pipeline="main",
                    model_role="main",
                    model_name=main_out.get("model_id") or "unknown",
                    llm_output_text=main_out.get("response", ""),
                    safety_decision=detect.get("decision") if detect else None,
                    safety_risk_score=detect.get("risk_score") if detect else None,
                    safety_best_label=detect.get("best_risk_label") if detect else None,
                    prompt_id=None,
                )
            except Exception as e:
                print("[dashboard_api] log_llm_output(main single) failed:", e)

    # Baseline LLM
    baseline_out: Optional[Dict[str, Any]] = None
    if mode in ("baseline_only", "dual"):
        baseline_out = mr.run_llm(
            text,
            blocked=False,
            model_path=mr.BASELINE_LLM_MODEL_PATH,
        )

        if log_to_db and baseline_out is not None:
            try:
                db_runtime.log_llm_output(
                    db_path=None,
                    request_id=f"{request_id}:baseline",
                    prompt_text=text,
                    pipeline="baseline",
                    model_role="baseline",
                    model_name=baseline_out.get("model_id") or "unknown",
                    llm_output_text=baseline_out.get("response", ""),
                    safety_decision=detect.get("decision") if detect else None,
                    safety_risk_score=detect.get("risk_score") if detect else None,
                    safety_best_label=detect.get("best_risk_label") if detect else None,
                    prompt_id=None,
                )
            except Exception as e:
                print("[dashboard_api] log_llm_output(baseline single) failed:", e)

    return {
        "request_id": request_id,
        "detect": detect,
        "main": main_out,
        "baseline": baseline_out,
    }


def iter_batch_for_api(
    *,
    label_mode: str,
    batch_size: int,
    mode: ModeType,
    risk_threshold: Optional[float],
    log_to_db: bool,
):
    """Iterate a batch for dashboard, including logging and metrics."""
    samples = _sample_prompts_for_batch(label_mode=label_mode, batch_size=batch_size)
    if not samples:
        return

    texts = [item["prompt"] for item in samples]
    n = len(texts)
    thr = risk_threshold or mr.RISK_THRESHOLD_DEFAULT

    # Safety model batch
    run_mlc_this_batch = mode in ("safety_only", "main_only", "dual")
    if run_mlc_this_batch:
        detect_list = mr.run_mlc_batch(texts, threshold=thr)
    else:
        detect_list = [None] * n

    blocked_flags: List[bool] = []
    for det in detect_list:
        if det is None:
            blocked_flags.append(False)
        else:
            decision = det.get("decision", "allow")
            blocked_flags.append(decision != "allow")

    # Main LLM batch
    run_main = mode in ("main_only", "dual")
    if run_main:
        main_outputs = mr.run_llm_batch(
            texts,
            blocked_flags=blocked_flags,
            model_path=mr.MAIN_LLM_MODEL_PATH,
            max_new_tokens=128,
            micro_batch_size=16,
        )
    else:
        main_outputs = [None] * n

    # Baseline LLM batch
    run_baseline = mode in ("baseline_only", "dual")
    if run_baseline:
        baseline_outputs = mr.run_llm_batch(
            texts,
            blocked_flags=[False] * n,
            model_path=mr.BASELINE_LLM_MODEL_PATH,
            max_new_tokens=128,
            micro_batch_size=16,
        )
    else:
        baseline_outputs = [None] * n

    # Aggregation + metrics + DB logging
    for item, det, main_out, base_out in zip(samples, detect_list, main_outputs, baseline_outputs):
        request_id = str(uuid.uuid4())
        label = item["label"]

        if det is not None:
            update_metrics(det.get("decision", "allow"), label)

        if log_to_db and det is not None:
            try:
                db_runtime.log_mlc_event(
                    db_path=None,
                    request_id=request_id,
                    source="dashboard_batch",
                    text=item["prompt"],
                    detector_version="dashboard_mlc",
                    label_schema_version=None,
                    detect_result={**det},
                    risk_threshold=thr,
                    is_baseline=False,
                    is_gold=True,
                    tee_to_baseline=(mode in ("baseline_only", "dual")),
                    prompt_id=item["prompt_id"],
                    error_json=None,
                )
            except Exception as e:
                print("[dashboard_api] log_mlc_event(batch) failed:", e)

        if log_to_db and main_out is not None:
            try:
                db_runtime.log_llm_output(
                    db_path=None,
                    request_id=request_id,
                    prompt_text=item["prompt"],
                    pipeline="main",
                    model_role="main",
                    model_name=main_out.get("model_id") or "unknown",
                    llm_output_text=main_out.get("response", ""),
                    safety_decision=det.get("decision") if det else None,
                    safety_risk_score=det.get("risk_score") if det else None,
                    safety_best_label=det.get("best_risk_label") if det else None,
                    prompt_id=item["prompt_id"],
                )
            except Exception as e:
                print("[dashboard_api] log_llm_output(main batch) failed:", e)

        if log_to_db and base_out is not None:
            try:
                db_runtime.log_llm_output(
                    db_path=None,
                    request_id=f"{request_id}:baseline",
                    prompt_text=item["prompt"],
                    pipeline="baseline",
                    model_role="baseline",
                    model_name=base_out.get("model_id") or "unknown",
                    llm_output_text=base_out.get("response", ""),
                    safety_decision=det.get("decision") if det else None,
                    safety_risk_score=det.get("risk_score") if det else None,
                    safety_best_label=det.get("best_risk_label") if det else None,
                    prompt_id=item["prompt_id"],
                )
            except Exception as e:
                print("[dashboard_api] log_llm_output(baseline batch) failed:", e)

        yield {
            "prompt_id": item["prompt_id"],
            "label": label,
            "prompt": item["prompt"],
            "request_id": request_id,
            "detect": det,
            "main": main_out,
            "baseline": base_out,
        }


# ---------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup: preload safety model so the first request is fast ────────
    try:
        import scripts.models_runtime as _mr
        _mr._load_mlc_model()
        print("[api] Safety model preloaded.")
    except Exception as exc:
        print(f"[api] Safety model preload skipped: {exc}")

    yield

    # ── Shutdown: cancel idle timer, then release GPU / VRAM ─────────────
    if manager._idle_task and not manager._idle_task.done():
        manager._idle_task.cancel()
    _unload_models()
    print("[api] Model memory cleared.")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Return 204 No Content — suppresses browser 404 noise without shipping a binary.
    return Response(status_code=204)


@app.get("/api/ready", include_in_schema=False)
async def api_ready():
    """Readiness probe used by run.py to detect when the server is up."""
    return {"status": "ok"}


@app.websocket("/ws/dashboard")
async def websocket_dashboard(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)


@app.get("/api/backbones")
async def api_backbones():
    """Return available backbones and the active one."""
    return _describe_backbones()


@app.post("/api/backbones/select")
async def api_select_backbone(req: SelectBackboneRequest):
    """Set backbone for main and baseline, then reload models_runtime."""
    select_backbone("main", req.backbone_id)
    select_backbone("baseline", req.backbone_id)

    global mr
    mr = importlib.reload(mr)

    return _describe_backbones()


@app.post("/api/single")
async def api_single(req: SinglePromptRequest):
    result = process_single_for_api(
        req.prompt,
        mode=req.mode,
        risk_threshold=req.risk_threshold,
        log_to_db=req.log_to_db,
        label_hint=req.label_hint,
    )

    base_prompt_data = {
        "source": "single",
        "prompt_id": None,
        "label": req.label_hint,
        "request_id": result["request_id"],
        "text": req.prompt,
    }

    await manager.broadcast(
        PromptEvent(
            type="prompt",
            data={**base_prompt_data, "pipeline": "main"},
        )
    )
    await manager.broadcast(
        PromptEvent(
            type="prompt",
            data={**base_prompt_data, "pipeline": "baseline"},
        )
    )

    detect = result["detect"]
    main_out = result["main"]
    baseline_out = result["baseline"]

    if main_out is not None:
        await manager.broadcast(
            ResponseEvent(
                type="response",
                data={
                    "source": "single",
                    "pipeline": "main",
                    "prompt_id": None,
                    "request_id": result["request_id"],
                    "model_name": main_out.get("model_id"),
                    "blocked": bool(detect and detect.get("decision") != "allow"),
                    "risk_score": detect.get("risk_score") if detect else None,
                    "clean_prob": detect.get("clean_prob") if detect else None,
                    "best_risk_label": detect.get("best_risk_label") if detect else None,
                    "label_probs": detect.get("label_probs") if detect else None,
                    "decision": detect.get("decision") if detect else None,
                    "text": main_out.get("response", ""),
                },
            )
        )

    if baseline_out is not None:
        await manager.broadcast(
            ResponseEvent(
                type="response",
                data={
                    "source": "single",
                    "pipeline": "baseline",
                    "prompt_id": None,
                    "request_id": result["request_id"],
                    "model_name": baseline_out.get("model_id"),
                    "blocked": False,
                    "risk_score": None,
                    "clean_prob": None,
                    "best_risk_label": None,
                    "decision": None,
                    "text": baseline_out.get("response", ""),
                },
            )
        )

    await manager.broadcast(build_metrics_event())

    return result


@app.post("/api/batch")
async def api_batch(req: BatchRequest):
    processed = 0
    batch_id = str(uuid.uuid4())

    for item in iter_batch_for_api(
        label_mode=req.label_mode,
        batch_size=req.batch_size,
        mode=req.mode,
        risk_threshold=req.risk_threshold,
        log_to_db=req.log_to_db,
    ):
        processed += 1

        base_prompt_data = {
            "source": "batch",
            "prompt_id": item["prompt_id"],
            "label": item["label"],
            "request_id": item["request_id"],
            "text": item["prompt"],
        }

        await manager.broadcast(
            PromptEvent(
                type="prompt",
                data={**base_prompt_data, "pipeline": "main"},
            )
        )
        await manager.broadcast(
            PromptEvent(
                type="prompt",
                data={**base_prompt_data, "pipeline": "baseline"},
            )
        )

        detect = item["detect"]
        main_out = item["main"]
        baseline_out = item["baseline"]

        if main_out is not None:
            await manager.broadcast(
                ResponseEvent(
                    type="response",
                    data={
                        "source": "batch",
                        "pipeline": "main",
                        "prompt_id": item["prompt_id"],
                        "request_id": item["request_id"],
                        "model_name": main_out.get("model_id"),
                        "blocked": bool(detect and detect.get("decision") != "allow"),
                        "risk_score": detect.get("risk_score") if detect else None,
                        "clean_prob": detect.get("clean_prob") if detect else None,
                        "best_risk_label": detect.get("best_risk_label") if detect else None,
                        "label_probs": detect.get("label_probs") if detect else None,
                        "decision": detect.get("decision") if detect else None,
                        "text": main_out.get("response", ""),
                    },
                )
            )

        if baseline_out is not None:
            await manager.broadcast(
                ResponseEvent(
                    type="response",
                    data={
                        "source": "batch",
                        "pipeline": "baseline",
                        "prompt_id": item["prompt_id"],
                        "request_id": item["request_id"],
                        "model_name": baseline_out.get("model_id"),
                        "blocked": False,
                        "risk_score": None,
                        "clean_prob": None,
                        "best_risk_label": None,
                        "decision": None,
                        "text": baseline_out.get("response", ""),
                    },
                )
            )

        await manager.broadcast(build_metrics_event())

    await manager.broadcast(
        BatchStatusEvent(
            type="batch_status",
            data={
                "batch_id": batch_id,
                "label_mode": req.label_mode,
                "requested": req.batch_size,
                "processed": processed,
                "status": "finished",
            },
        )
    )

    return {"batch_id": batch_id, "processed": processed}
