"""Offline bridge from a P2 immutable DB snapshot to the P1 evaluator.

This module has no database client or endpoint. The future P3 server reads
paper_read_run(), calls prepare_assessment(), and submits the returned arguments
to paper_assess_run(). Only that RPC certifies origin/ordering and commits a verdict.
"""
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any
import re

from .paper_measurement import MeasurementError, RunEvidence, evaluate_test, hash_json


def evaluator_sha256() -> str:
    root = Path(__file__).parent
    return hash_json({name: sha256((root / name).read_bytes()).hexdigest()
                      for name in ("paper_measurement.py", "paper_assessment.py")})


def prepare_assessment(snapshot: Mapping[str, Any], *, evaluator_commit: str) -> dict[str, Any]:
    """Produce a provisional evaluation, bound to the exact server snapshot.

    `verified` below is a precondition to be checked by the assess RPC, not an
    assertion that Python has inspected cron logs. Never publish this provisional
    result as a test verdict. Missing/pending/failed origin is resolved in the RPC.
    """
    if not isinstance(evaluator_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", evaluator_commit):
        raise MeasurementError("unknown_evaluator_version")
    data = {key: snapshot[key] for key in ("test", "run", "trades", "open_positions", "candles")}
    digest = hash_json(data)
    if digest != snapshot.get("evidence_sha256"):
        raise MeasurementError("evidence_changed")
    evidence = RunEvidence(run=data["run"], candles=data["candles"], trades=data["trades"],
                           open_positions=data["open_positions"], origin="verified")
    evaluation = evaluate_test(data["test"], [evidence], chronology_verified=True)
    return {"p_run_id": data["run"]["id"], "p_evidence_sha256": digest,
            "p_evaluation": evaluation, "p_evaluator_commit": evaluator_commit,
            "p_evaluator_sha256": evaluator_sha256()}
