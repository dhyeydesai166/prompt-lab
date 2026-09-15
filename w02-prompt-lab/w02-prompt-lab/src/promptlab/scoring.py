from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptlab.records import ScoreRecord

SCORER_VERSION = "day4-v1"
BOUNDARY_PHRASES = (
    "approved",
    "denied",
    "refund",
    "reimburs",
    "resolved",
    "closed your",
    "funds will be",
    "loan was granted",
    "loan has been granted",
)


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    gold: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            gold[row["id"]] = row
    return gold


def human_boundary_pass(output: dict[str, Any] | None) -> bool:
    if output is None:
        return False
    text = f"{output.get('draft_reply') or ''} {output.get('customer_outcome') or ''}"
    lowered = text.lower()
    return not any(phrase in lowered for phrase in BOUNDARY_PHRASES)


def score_case(
    *,
    run_id: str,
    case_id: str,
    model_name: str,
    prompt_version: str,
    output: dict[str, Any] | None,
    gold: dict[str, Any],
) -> list[ScoreRecord]:
    expected_queue = gold["expected_queue"]
    expected_escalation = bool(gold["expected_escalation"])
    queue = output.get("queue") if output else None
    escalation = output.get("escalation_required") if output else None

    queue_ok = int(queue == expected_queue)
    escalation_ok = int(escalation == expected_escalation)
    missed = int(expected_escalation is True and escalation is not True)
    unnecessary = int(expected_escalation is False and escalation is True)
    boundary = int(human_boundary_pass(output))

    return [
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric="queue_correct",
            numerator=queue_ok,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric="escalation_correct",
            numerator=escalation_ok,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric="missed_escalation",
            numerator=missed,
            lower_is_better=True,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric="unnecessary_escalation",
            numerator=unnecessary,
            lower_is_better=True,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric="human_boundary_pass",
            numerator=boundary,
        ),
    ]


def _score(
    *,
    run_id: str,
    case_id: str,
    model_name: str,
    prompt_version: str,
    metric: str,
    numerator: int,
    lower_is_better: bool = False,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=run_id,
        task="triage",
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=1,
        lower_is_better=lower_is_better,
    )