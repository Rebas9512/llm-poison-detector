import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv

from db_runtime import (
    SQLITE_DB_PATH,
    SCHEMA_PATH,
    ensure_schema,
    log_llm_output,
    log_mlc_event,
    fetch_eval_prompts,
    clear_mlc_events,
)
import models_runtime as mr
from check_env import list_backbones, select_backbone
import importlib

load_dotenv()

LABEL_SCHEMA_VERSION = os.getenv("LABEL_SCHEMA_VERSION", "v1")
SOURCE_TAG = os.getenv("SOURCE_TAG", "manual")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMP_EVENT_LOG_PATH = os.getenv("TEMP_EVENT_LOG_PATH", "./temp_event_log/latest_eval.json")
TEMP_EVENT_LOG_PATH = str((PROJECT_ROOT / TEMP_EVENT_LOG_PATH).resolve())


# ---------- Env / file checks ----------


def check_files() -> None:
    """Basic checks for safety model and DB schema."""
    problems: List[str] = []

    # MLC must always exist
    if not mr.MLC_MODEL_PATH or not os.path.exists(mr.MLC_MODEL_PATH):
        problems.append(f"MLC_MODEL_PATH missing or not found: {mr.MLC_MODEL_PATH}")

    # For main/baseline, only enforce when backend=local
    if getattr(mr, "MAIN_LLM_BACKEND", "local") == "local":
        if not mr.MAIN_LLM_MODEL_PATH or not os.path.exists(mr.MAIN_LLM_MODEL_PATH):
            problems.append(f"MAIN_LLM_MODEL_PATH does not exist: {mr.MAIN_LLM_MODEL_PATH}")

    if getattr(mr, "BASELINE_LLM_BACKEND", "local") == "local":
        if not mr.BASELINE_LLM_MODEL_PATH or not os.path.exists(mr.BASELINE_LLM_MODEL_PATH):
            problems.append(f"BASELINE_LLM_MODEL_PATH does not exist: {mr.BASELINE_LLM_MODEL_PATH}")

    if not SCHEMA_PATH or not os.path.exists(SCHEMA_PATH):
        problems.append(f"SCHEMA_PATH missing or not found: {SCHEMA_PATH}")

    if problems:
        print("Environment / file check failed:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    try:
        ensure_schema(db_path=SQLITE_DB_PATH, schema_path=SCHEMA_PATH)
    except Exception as e:  # noqa: BLE001
        print("Failed to ensure DB schema:", e)
        sys.exit(1)


def print_env_summary() -> None:
    print("=== Init ===")
    print("Device:", mr.DEVICE)
    print("DB path:", SQLITE_DB_PATH)
    print("Schema:", SCHEMA_PATH)
    print("Label schema:", LABEL_SCHEMA_VERSION)
    print("Source tag:", SOURCE_TAG)
    print("Default risk threshold:", mr.RISK_THRESHOLD_DEFAULT)
    print("Temp event log:", TEMP_EVENT_LOG_PATH)
    print("Main backend:", getattr(mr, "MAIN_LLM_BACKEND", "local"))
    print("Baseline backend:", getattr(mr, "BASELINE_LLM_BACKEND", "local"))
    print("================")
    print()



# ---------- DB view helpers ----------


def show_latest_mlc(limit: int = 5) -> None:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              request_id,
              source,
              substr(text, 1, 40) AS text_snippet,
              risk_score,
              clean_prob,
              best_risk_label,
              decision,
              risk_threshold,
              is_baseline,
              is_gold,
              tee_to_baseline,
              created_at
            FROM mlc_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("(no mlc_events rows)")
        return

    print(f"--- latest {len(rows)} mlc_events ---")
    for r in rows:
        (
            request_id,
            source,
            text_snippet,
            risk_score,
            clean_prob,
            best_risk_label,
            decision,
            risk_threshold,
            is_baseline,
            is_gold,
            tee_to_baseline,
            created_at,
        ) = r
        print(f"request_id: {request_id}")
        print(f"  source:        {source}")
        print(f"  text:          {text_snippet!r}")
        print(f"  risk_score:    {risk_score}  clean_prob: {clean_prob}")
        print(f"  best_label:    {best_risk_label}  decision: {decision}")
        print(f"  threshold:     {risk_threshold}")
        print(
            f"  flags:         baseline={bool(is_baseline)} "
            f"gold={bool(is_gold)} tee={bool(tee_to_baseline)}"
        )
        print(f"  created_at:    {created_at}")
        print()
    print("-------------------------------")


def show_latest_llm(limit: int = 5) -> None:
    conn = sqlite3.connect(SQLITE_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              id,
              request_id,
              pipeline,
              model_role,
              model_name,
              substr(prompt_text, 1, 40) AS prompt_snippet,
              substr(llm_output_text, 1, 80) AS output_snippet,
              created_at
            FROM llm_outputs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("(no llm_outputs rows)")
        return

    print(f"--- latest {len(rows)} llm_outputs ---")
    for r in rows:
        (
            row_id,
            request_id,
            pipeline,
            model_role,
            model_name,
            prompt_snippet,
            output_snippet,
            created_at,
        ) = r
        print(f"id: {row_id}  request_id: {request_id}")
        print(f"  pipeline:   {pipeline}")
        print(f"  role:       {model_role}")
        print(f"  model:      {model_name}")
        print(f"  prompt:     {prompt_snippet!r}")
        print(f"  output:     {output_snippet!r}")
        print(f"  created_at: {created_at}")
        print()
    print("-------------------------------")


# ---------- Backbone helpers (REPL) ----------


def show_backbones() -> None:
    bks = list_backbones()
    if not bks:
        print("(no backbones found)")
        return

    print("--- available backbones ---")
    for idx, b in enumerate(bks):
        kind = b["kind"]
        src = b["source"]
        avail = b["available"]
        if kind == "local":
            loc = b["path"]
            extra = ""
        else:
            loc = b["base_url"]
            extra = f" model={b['model']}"
        print(f"[{idx}] id={b['id']}")
        print(f"     kind={kind} source={src} available={avail}")
        print(f"     {loc}{extra}")
    print("---------------------------")


def use_backbone(target: str, token: str) -> None:
    bks = list_backbones()
    if not bks:
        print("No backbones to choose from.")
        return

    # token can be index or id
    chosen_id: Optional[str] = None
    if token.isdigit():
        idx = int(token)
        if 0 <= idx < len(bks):
            chosen_id = bks[idx]["id"]
        else:
            print("Index out of range.")
            return
    else:
        # direct id match
        for b in bks:
            if b["id"] == token:
                chosen_id = b["id"]
                break
        if chosen_id is None:
            print("Unknown backbone id.")
            return

    try:
        select_backbone(target, chosen_id)
    except Exception as e:  # noqa: BLE001
        print(f"Failed to select backbone: {e}")
        return

    # reload models_runtime to pick up new env
    global mr
    mr = importlib.reload(mr)

    print(f"Switched {target} backbone to {chosen_id}")
    print(f"Main backend:     {getattr(mr, 'MAIN_LLM_BACKEND', 'local')}")
    print(f"Main model path:  {getattr(mr, 'MAIN_LLM_MODEL_PATH', None)}")
    print(f"Baseline backend: {getattr(mr, 'BASELINE_LLM_BACKEND', 'local')}")
    print(f"Baseline path:    {getattr(mr, 'BASELINE_LLM_MODEL_PATH', None)}")
    print()


# ---------- Core pipeline ----------


def now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def process_prompt(
    text: str,
    *,
    tee_baseline: bool,
    log_to_db: bool,
) -> Dict[str, Any]:
    """Full pipeline for one text."""
    request_id = str(uuid.uuid4())

    detect = mr.run_mlc(text, threshold=mr.RISK_THRESHOLD_DEFAULT)
    decision = detect.get("decision", "allow")
    blocked = decision != "allow"

    if log_to_db:
        log_mlc_event(
            db_path=None,
            request_id=request_id,
            source=SOURCE_TAG,
            text=text,
            detector_version=detect.get("version"),
            label_schema_version=LABEL_SCHEMA_VERSION,
            detect_result=detect,
            risk_threshold=detect["risk_threshold"],
            is_baseline=False,
            is_gold=False,
            tee_to_baseline=tee_baseline,
        )

    main_out = mr.run_llm(
        text,
        blocked=blocked,
        model_path=mr.MAIN_LLM_MODEL_PATH,
    )

    if log_to_db:
        log_llm_output(
            db_path=None,
            request_id=request_id,
            prompt_text=text,
            pipeline="main",
            model_role="main_llm",
            model_name=main_out.get("model_id", ""),
            llm_output_text=main_out.get("response", ""),
            safety_decision=decision,
            safety_risk_score=detect.get("risk_score"),
            safety_best_label=detect.get("best_risk_label"),
        )

    baseline_out: Optional[Dict[str, Any]] = None
    if tee_baseline:
        baseline_out = mr.run_llm(
            text,
            blocked=False,
            model_path=mr.BASELINE_LLM_MODEL_PATH,
        )
        if log_to_db:
            log_llm_output(
                db_path=None,
                request_id=request_id,
                prompt_text=text,
                pipeline="baseline",
                model_role="baseline_llm",
                model_name=baseline_out.get("model_id", ""),
                llm_output_text=baseline_out.get("response", ""),
                safety_decision=None,
                safety_risk_score=None,
                safety_best_label=None,
            )

    return {
        "request_id": request_id,
        "detect": detect,
        "main": main_out,
        "baseline": baseline_out,
    }


def print_result(res: Dict[str, Any], tee_baseline: bool) -> None:
    rid = res["request_id"]
    detect = res["detect"]
    main_out = res["main"]
    baseline_out = res["baseline"]

    print()
    print("=== Result ===")
    print("request_id:", rid)
    print("[safety]")
    print("  decision:", detect["decision"])
    print("  risk_score:", detect["risk_score"])
    print("  clean_prob:", detect["clean_prob"])
    print("  best_risk_label:", detect["best_risk_label"])
    print("  threshold:", detect["risk_threshold"])
    print()

    print("[main]")
    print("  model:", main_out.get("model_id"))
    print("  skipped:", main_out.get("llm_main", {}).get("skipped"))
    print("  reason:", main_out.get("llm_main", {}).get("reason"))
    print("  response:")
    print(main_out.get("response", ""))
    print()

    if tee_baseline:
        print("[baseline]")
        if baseline_out is None:
            print("  (not executed)")
        else:
            print("  model:", baseline_out.get("model_id"))
            print("  skipped:", baseline_out.get("llm_main", {}).get("skipped"))
            print("  reason:", baseline_out.get("llm_main", {}).get("reason"))
            print("  response:")
            print(baseline_out.get("response", ""))
        print()

    print("=========================")
    print()


# ---------- Batch evaluation from DB ----------


def run_batch_from_db(
    label_mode: str,
    batch_size: int,
    *,
    tee_baseline: bool,
    log_to_db: bool,
) -> int:
    """Run a batch evaluation using prompts from prompt_pool."""
    if label_mode == "any":
        labels = None
    elif label_mode == "mixed":
        labels = None
    else:
        labels = [label_mode]

    samples = fetch_eval_prompts(
        limit=batch_size,
        labels=labels,
        source=None,
        only_gold=True,
    )

    if not samples:
        return 0

    texts = [item["prompt"] for item in samples]
    n = len(texts)

    # MLC batch
    detect_list = mr.run_mlc_batch(
        texts,
        threshold=mr.RISK_THRESHOLD_DEFAULT,
    )
    decisions = [d.get("decision", "allow") for d in detect_list]
    blocked_flags = [dec != "allow" for dec in decisions]

    # Request IDs
    request_ids = [str(uuid.uuid4()) for _ in range(n)]

    # Log MLC
    if log_to_db:
        for item, rid, det in zip(samples, request_ids, detect_list):
            log_mlc_event(
                db_path=None,
                request_id=rid,
                source=SOURCE_TAG,
                text=item["prompt"],
                detector_version=det.get("version"),
                label_schema_version=LABEL_SCHEMA_VERSION,
                detect_result=det,
                risk_threshold=det["risk_threshold"],
                is_baseline=False,
                is_gold=True,
                tee_to_baseline=tee_baseline,
                prompt_id=item["prompt_id"],
            )

    # Main LLM batch
    main_outputs = mr.run_llm_batch(
        texts,
        blocked_flags=blocked_flags,
        model_path=mr.MAIN_LLM_MODEL_PATH,
        max_new_tokens=128,
        micro_batch_size=16,
    )

    if log_to_db:
        for item, rid, det, out in zip(samples, request_ids, detect_list, main_outputs):
            log_llm_output(
                db_path=None,
                request_id=rid,
                prompt_text=item["prompt"],
                pipeline="main",
                model_role="main_llm",
                model_name=out.get("model_id", ""),
                llm_output_text=out.get("response", ""),
                safety_decision=det.get("decision"),
                safety_risk_score=det.get("risk_score"),
                safety_best_label=det.get("best_risk_label"),
                prompt_id=item["prompt_id"],
            )

    # Baseline LLM batch (optional, never blocked)
    baseline_outputs: List[Optional[Dict[str, Any]]] = [None] * n
    if tee_baseline:
        baseline_outputs = mr.run_llm_batch(
            texts,
            blocked_flags=[False] * n,
            model_path=mr.BASELINE_LLM_MODEL_PATH,
            max_new_tokens=128,
            micro_batch_size=16,
        )
        if log_to_db:
            for item, rid, out in zip(samples, request_ids, baseline_outputs):
                log_llm_output(
                    db_path=None,
                    request_id=rid,
                    prompt_text=item["prompt"],
                    pipeline="baseline",
                    model_role="baseline_llm",
                    model_name=out.get("model_id", ""),
                    llm_output_text=out.get("response", ""),
                    safety_decision=None,
                    safety_risk_score=None,
                    safety_best_label=None,
                    prompt_id=item["prompt_id"],
                )

    started_at = now_iso_z()
    finished_at = now_iso_z()

    results: List[Dict[str, Any]] = []
    for item, rid, det, main_out, base_out in zip(
        samples, request_ids, detect_list, main_outputs, baseline_outputs
    ):
        results.append(
            {
                "prompt_id": item["prompt_id"],
                "label": item["label"],
                "prompt": item["prompt"],
                "request_id": rid,
                "detect": det,
                "main": main_out,
                "baseline": base_out,
            }
        )

    run_meta: Dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "started_at": started_at,
        "finished_at": finished_at,
        "label_mode": label_mode,
        "requested_batch_size": batch_size,
        "actual_batch_size": len(results),
        "tee_baseline": tee_baseline,
        "db_logging": log_to_db,
        "temp_event_log_path": TEMP_EVENT_LOG_PATH,
        "results": results,
    }

    os.makedirs(os.path.dirname(TEMP_EVENT_LOG_PATH), exist_ok=True)
    with open(TEMP_EVENT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(run_meta, f, ensure_ascii=False, indent=2)

    return len(results)


# ---------- REPL ----------


HELP_TEXT = """
Commands:
  :help                Show this help
  :quit / :exit        Exit
  :baseline            Show baseline status
  :baseline on|off     Enable/disable baseline path
  :db                  Show DB logging status
  :db on|off           Enable/disable DB logging
  :mlc [N]             Show latest N rows from mlc_events
  :llm [N]             Show latest N rows from llm_outputs
  :reset_db            Reset eval DB (mlc_events + llm_outputs)
  :eval MODE N         Run batch eval from DB
  :backbones           List available LLM backbones
  :use main ID|IDX     Select backbone for main LLM
  :use baseline ID|IDX Select backbone for baseline LLM
""".strip()


def repl(tee_baseline: bool) -> None:
    print("Simple LLM poison sandbox REPL")
    print("Type :help for commands, :quit to exit.")
    print()

    baseline_flag = tee_baseline
    db_flag = False

    while True:
        try:
            line = input("prompt> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        line = line.strip()
        if not line:
            continue

        if line.startswith(":"):
            parts = line[1:].strip().split()
            if not parts:
                continue

            cmd = parts[0].lower()
            args = parts[1:]

            # exit
            if cmd in ("quit", "exit"):
                break

            # help
            if cmd == "help":
                print(HELP_TEXT)
                continue

            # baseline toggle
            if cmd == "baseline":
                if not args:
                    print("baseline:", baseline_flag)
                else:
                    val = args[0].lower()
                    baseline_flag = val in ("on", "true", "1", "yes", "y")
                    print("baseline:", baseline_flag)
                continue

            # db logging toggle
            if cmd == "db":
                if not args:
                    print("db_logging:", db_flag)
                else:
                    val = args[0].lower()
                    db_flag = val in ("on", "true", "1", "yes", "y")
                    print("db_logging:", db_flag)
                continue

            # show mlc rows
            if cmd == "mlc":
                n = 5
                if args:
                    try:
                        n = max(1, int(args[0]))
                    except ValueError:
                        pass
                show_latest_mlc(limit=n)
                continue

            # show llm rows
            if cmd == "llm":
                n = 5
                if args:
                    try:
                        n = max(1, int(args[0]))
                    except ValueError:
                        pass
                show_latest_llm(limit=n)
                continue

            # reset eval DB
            if cmd == "reset_db":
                deleted = clear_mlc_events(dry_run=False)
                print(f"Eval DB reset. Deleted rows: {deleted}")
                continue

            # run batch eval
            if cmd == "eval":
                if len(args) < 2:
                    print("Usage: :eval MODE N")
                    continue
                mode = args[0].lower()
                try:
                    count = max(1, int(args[1]))
                except ValueError:
                    print("Invalid count.")
                    continue

                processed = run_batch_from_db(
                    label_mode=mode,
                    batch_size=count,
                    tee_baseline=baseline_flag,
                    log_to_db=db_flag,
                )

                route = "dual-route" if baseline_flag else "single-route"
                print(
                    f"Batch run completed: {route}, {processed} prompts processed. "
                    f"Results written to {TEMP_EVENT_LOG_PATH}"
                )
                continue

            # list backbones
            if cmd == "backbones":
                show_backbones()
                continue

            # use backbone
            if cmd == "use":
                if len(args) < 2:
                    print("Usage: :use main|baseline ID|IDX")
                    continue
                target = args[0].lower()
                token = args[1]
                if target not in ("main", "baseline"):
                    print("Target must be 'main' or 'baseline'.")
                    continue
                use_backbone(target, token)
                continue

            # unknown
            print("Unknown command. Type :help for list.")
            continue

        # normal prompt
        res = process_prompt(
            line,
            tee_baseline=baseline_flag,
            log_to_db=db_flag,
        )
        print_result(res, tee_baseline=baseline_flag)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-baseline",
        action="store_true",
        help="also run baseline LLM path for each prompt",
    )
    args = parser.parse_args()

    check_files()
    print_env_summary()
    repl(tee_baseline=args.with_baseline)


if __name__ == "__main__":
    main()
