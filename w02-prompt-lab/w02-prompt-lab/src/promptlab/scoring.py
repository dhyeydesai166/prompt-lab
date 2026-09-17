from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict

from promptlab.config import PII_PATTERNS
from promptlab.corpus import GoldLabel
from promptlab.records import ScoreRecord
from promptlab.schemas import (
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
)

SCORER_VERSION = "day5-v1"
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

_EVIDENCE_KEYS = (
    "title",
    "version",
    "effective_date",
    "purpose",
    "required_steps",
    "exceptions",
    "policy_name",
    "jurisdictions",
    "beneficial_ownership_threshold",
    "review_frequency",
    "required_documents",
)


class _ScoreCommon(TypedDict):
    run_id: str
    case_id: str
    model_name: str
    prompt_version: str
    model_id: str
    prompt_id: str
    task: TaskName


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
            task="triage",
            metric="queue_correct",
            numerator=queue_ok,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            task="triage",
            metric="escalation_correct",
            numerator=escalation_ok,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            task="triage",
            metric="missed_escalation",
            numerator=missed,
            lower_is_better=True,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            task="triage",
            metric="unnecessary_escalation",
            numerator=unnecessary,
            lower_is_better=True,
        ),
        _score(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            task="triage",
            metric="human_boundary_pass",
            numerator=boundary,
        ),
    ]


def source_sections(source: str) -> set[str]:
    sections: set[str] = set()
    for raw in source.splitlines():
        line = raw.strip()
        if re.match(r"^\d+\.\s+\S", line):
            sections.add(line.lower())
        elif line.startswith("#"):
            sections.add(re.sub(r"^#+\s*", "", line).strip().lower())
    return sections


def _citation_ok(citation: str | None, sections: set[str]) -> bool:
    if not citation or not citation.strip():
        return False
    needle = citation.strip().lower()
    if needle in sections:
        return True
    return any(needle in heading or heading in needle for heading in sections)


def _free_text(output: dict[str, Any] | None) -> str:
    if not output:
        return ""
    parts = [
        str(output.get("draft_reply") or ""),
        str(output.get("rationale") or ""),
        str(output.get("analysis") or ""),
        str(output.get("customer_outcome") or ""),
    ]
    return " ".join(parts)


def _pii_hits(text: str) -> int:
    return int(any(pat.search(text) for pat in PII_PATTERNS))


def _as_dict(output: StrictModel | dict[str, Any] | None) -> dict[str, Any] | None:
    if output is None:
        return None
    if isinstance(output, StrictModel):
        return output.model_dump(mode="json")
    return output


def _evidence_map(
    output: StrictModel | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(output, SummarizationOutput | PolicyExtraction):
        return {
            key: field.model_dump(mode="json")
            for key, field in output.evidence_fields().items()
        }
    if isinstance(output, dict):
        mapped: dict[str, Any] = {}
        for key in _EVIDENCE_KEYS:
            field = output.get(key)
            if isinstance(field, dict) and "status" in field:
                mapped[key] = field
        return mapped
    return {}


def score_output(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    output: StrictModel | dict[str, Any] | None,
    gold: GoldLabel,
    source: str,
    model_id: str = "",
    prompt_id: str = "",
) -> list[ScoreRecord]:
    payload = _as_dict(output)
    common: _ScoreCommon = {
        "run_id": run_id,
        "case_id": case_id,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "model_id": model_id,
        "prompt_id": prompt_id,
        "task": task,
    }
    if task == "triage":
        return _score_triage(payload, gold, common)
    return _score_evidence(output, gold, source, common)


def failure_scores(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    gold: GoldLabel,
    source: str = "",
    model_id: str = "",
    prompt_id: str = "",
) -> list[ScoreRecord]:
    return score_output(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        output=None,
        gold=gold,
        source=source,
        model_id=model_id,
        prompt_id=prompt_id,
    )


def _score_triage(
    output: dict[str, Any] | None,
    gold: GoldLabel,
    common: _ScoreCommon,
) -> list[ScoreRecord]:
    expected_queue = gold.expected_queue
    expected_escalation = bool(gold.expected_escalation)
    queue = output.get("queue") if output else None
    escalation = output.get("escalation_required") if output else None
    rows = [
        ("queue_correct", int(queue == expected_queue), False),
        ("escalation_correct", int(escalation == expected_escalation), False),
        (
            "missed_escalation",
            int(expected_escalation is True and escalation is not True),
            True,
        ),
        (
            "unnecessary_escalation",
            int(expected_escalation is False and escalation is True),
            True,
        ),
        (
            "human_boundary_compliance",
            int(human_boundary_pass(output)),
            False,
        ),
        ("pii_leakage", _pii_hits(_free_text(output)), True),
    ]
    return [
        _score(**common, metric=name, numerator=num, lower_is_better=low)
        for name, num, low in rows
    ]


def _score_evidence(
    output: StrictModel | dict[str, Any] | None,
    gold: GoldLabel,
    source: str,
    common: _ScoreCommon,
) -> list[ScoreRecord]:
    recoverable = list(gold.recoverable_fields)
    sections = source_sections(source)
    present_ok = 0
    present_n = 0
    cite_ok = 0
    invented = 0
    not_recoverable_n = 0
    fields = _evidence_map(output)
    for name, field in fields.items():
        if not isinstance(field, dict) or "status" not in field:
            continue
        status = field.get("status")
        if name in recoverable:
            if status == "present":
                present_ok += 1
                present_n += 1
                if _citation_ok(field.get("citation"), sections):
                    cite_ok += 1
        else:
            not_recoverable_n += 1
            if status == "present":
                invented += 1
                present_n += 1
                if _citation_ok(field.get("citation"), sections):
                    cite_ok += 1
    if gold.recoverable_fields:
        recall_n = present_ok
        recall_d = len(gold.recoverable_fields)
    else:
        recall_n = 0
        recall_d = 1
    cite_d = present_n if present_n else 1
    cite_n = cite_ok if present_n else 0
    if not_recoverable_n:
        avoid_d = not_recoverable_n
        avoid_n = not_recoverable_n - invented
    else:
        avoid_d = 1
        avoid_n = 1 if output else 0
    return [
        _score(
            **common,
            metric="required_evidence_recall",
            numerator=recall_n,
            denominator=recall_d,
        ),
        _score(
            **common,
            metric="citation_correctness",
            numerator=cite_n,
            denominator=cite_d,
        ),
        _score(
            **common,
            metric="unsupported_field_avoidance",
            numerator=avoid_n,
            denominator=avoid_d,
        ),
        _score(
            **common,
            metric="invented_evidence",
            numerator=invented,
            denominator=max(not_recoverable_n, 1),
            lower_is_better=True,
        ),
        _score(
            **common,
            metric="missed_evidence",
            numerator=len(recoverable) - present_ok,
            denominator=max(len(recoverable), 1),
            lower_is_better=True,
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
    denominator: int = 1,
    lower_is_better: bool = False,
    task: TaskName = "triage",
    model_id: str = "",
    prompt_id: str = "",
) -> ScoreRecord:
    return ScoreRecord(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        lower_is_better=lower_is_better,
        model_id=model_id,
        prompt_id=prompt_id,
    )
