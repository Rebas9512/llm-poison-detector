import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv

load_dotenv()

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(p: str) -> str:
    if not p:
        raise ValueError("Empty path")
    path = Path(p)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


SQLITE_DB_PATH = resolve_path(os.getenv("SQLITE_DB_PATH", "./db/llm_poison.db"))
SCHEMA_PATH = resolve_path(os.getenv("SCHEMA_PATH", "./schema/001_init.sql"))

_SCHEMA_OK: Dict[str, bool] = {}


def ensure_schema(db_path: Optional[str] = None, schema_path: Optional[str] = None) -> None:
    """Ensure tables exist."""
    db_path = db_path or SQLITE_DB_PATH
    schema_path = schema_path or SCHEMA_PATH
    key = str(db_path)

    if _SCHEMA_OK.get(key):
        return

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('mlc_events','llm_outputs')"
        )
        rows = cur.fetchall()

        if not any(r[0] == "mlc_events" for r in rows) or not any(r[0] == "llm_outputs" for r in rows):
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())

        conn.commit()
        _SCHEMA_OK[key] = True
    finally:
        conn.close()


def log_mlc_event(
    *,
    db_path: Optional[str],
    request_id: str,
    source: Optional[str],
    text: str,
    detector_version: Optional[str],
    label_schema_version: Optional[str],
    detect_result: Dict[str, Any],
    risk_threshold: float,
    is_baseline: bool = False,
    is_gold: bool = False,
    tee_to_baseline: bool = False,
    prompt_id: Optional[int] = None,
    error_json: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert/update one MLC event."""
    db_path = db_path or SQLITE_DB_PATH
    ensure_schema(db_path=db_path)

    import json

    label_probs_json = json.dumps(detect_result.get("label_probs", {}), ensure_ascii=False)
    risk_labels_json = json.dumps(detect_result.get("risk_labels", []), ensure_ascii=False)
    error_json_str = json.dumps(error_json, ensure_ascii=False) if error_json else None

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO mlc_events (
                request_id, prompt_id, source, text,
                detector_version, label_schema_version,
                risk_score, clean_prob, best_risk_label, decision,
                label_probs, risk_labels, risk_threshold,
                is_baseline, is_gold, tee_to_baseline, error_json
            )
            VALUES (
                :request_id, :prompt_id, :source, :text,
                :detector_version, :label_schema_version,
                :risk_score, :clean_prob, :best_risk_label, :decision,
                :label_probs, :risk_labels, :risk_threshold,
                :is_baseline, :is_gold, :tee_to_baseline, :error_json
            )
            """,
            {
                "request_id": request_id,
                "prompt_id": prompt_id,
                "source": source,
                "text": text,
                "detector_version": detector_version,
                "label_schema_version": label_schema_version,
                "risk_score": float(detect_result.get("risk_score", 0.0)),
                "clean_prob": float(detect_result.get("clean_prob", 0.0)),
                "best_risk_label": detect_result.get("best_risk_label"),
                "decision": detect_result.get("decision", "unknown"),
                "label_probs": label_probs_json,
                "risk_labels": risk_labels_json,
                "risk_threshold": float(risk_threshold),
                "is_baseline": 1 if is_baseline else 0,
                "is_gold": 1 if is_gold else 0,
                "tee_to_baseline": 1 if tee_to_baseline else 0,
                "error_json": error_json_str,
            },
        )
        conn.commit()
    finally:
        conn.close()


def log_llm_output(
    *,
    db_path: Optional[str],
    request_id: Optional[str],
    prompt_text: Optional[str],
    pipeline: str,
    model_role: str,
    model_name: str,
    llm_output_text: str,
    safety_decision: Optional[str] = None,
    safety_risk_score: Optional[float] = None,
    safety_best_label: Optional[str] = None,
    prompt_id: Optional[int] = None,
) -> None:
    """Insert one LLM output."""
    db_path = db_path or SQLITE_DB_PATH
    ensure_schema(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            INSERT INTO llm_outputs (
                request_id, prompt_id, pipeline, model_role, model_name,
                prompt_text, llm_output_text, llm_output_json,
                safety_decision, safety_risk_score, safety_best_label,
                latency_ms, prompt_tokens, completion_tokens
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                prompt_id,
                pipeline,
                model_role,
                model_name,
                prompt_text,
                llm_output_text,
                None,
                safety_decision,
                safety_risk_score,
                safety_best_label,
                None,
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Fetch prompts for batch evaluation
# ----------------------------------------------------------------------

def fetch_eval_prompts(
    *,
    db_path: Optional[str] = None,
    limit: int = 300,
    labels: Optional[List[str]] = None,
    source: Optional[str] = None,
    only_gold: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch a random batch of prompts."""
    db_path = db_path or SQLITE_DB_PATH
    ensure_schema(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        sql = "SELECT prompt_id, text, tags FROM prompt_pool WHERE 1=1"
        params: List[Any] = []

        if source is not None:
            sql += " AND source = ?"
            params.append(source)

        if labels:
            sql += " AND tags IN ({})".format(",".join("?" for _ in labels))
            params.extend(labels)

        if only_gold:
            sql += " AND is_gold = 1"

        sql += " ORDER BY RANDOM() LIMIT ?"
        params.append(int(limit))

        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    return [{"prompt_id": pid, "prompt": text, "label": tag} for pid, text, tag in rows]


# ----------------------------------------------------------------------
# Reset eval tables (mlc_events + llm_outputs)
# ----------------------------------------------------------------------

def clear_mlc_events(
    *,
    db_path: Optional[str] = None,
    source: Optional[str] = None,       # kept for compatibility
    is_baseline: Optional[bool] = None, # kept for compatibility
    dry_run: bool = False,
) -> int:
    """Clear eval data; does not touch prompt_pool."""
    db_path = db_path or SQLITE_DB_PATH
    ensure_schema(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM mlc_events")
        (mlc_count,) = cur.fetchone() or (0,)

        cur.execute("SELECT COUNT(*) FROM llm_outputs")
        (llm_count,) = cur.fetchone() or (0,)

        total = int(mlc_count + llm_count)

        if dry_run:
            return total

        cur.execute("DELETE FROM mlc_events")
        cur.execute("DELETE FROM llm_outputs")
        conn.commit()
        return total
    finally:
        conn.close()
