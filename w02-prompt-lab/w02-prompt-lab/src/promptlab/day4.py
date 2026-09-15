from __future__ import annotations

import json
import uuid
from pathlib import Path
from statistics import median
from typing import Any

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, ScoreRecord, append_record, load_records
from promptlab.schemas import TriageOutput, TriageOutputWithAnalysis
from promptlab.scoring import load_gold, score_case
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

CASES_PATH = PROJECT_ROOT / "cases" / "triage.jsonl"
GOLD_PATH = PROJECT_ROOT / "cases" / "gold" / "triage.jsonl"
MAX_OUTPUT_TOKENS = 512


class CountingAdapter:
    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0
        self.results: list[CompletionResult] = []

    def reset(self) -> None:
        self.calls = 0
        self.results = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        result = self._inner.complete(request, run_id)
        self.results.append(result)
        return result

    def records(self) -> list[CallRecord]:
        collected: list[CallRecord] = []
        for result in self.results:
            collected.extend(result.records)
        return collected


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    if len(cases) != 12:
        raise ValueError(f"expected 12 triage cases, found {len(cases)}")
    return cases


def run_version(
    adapter: CountingAdapter,
    cases: list[dict[str, Any]],
    gold: dict[str, dict[str, Any]],
    *,
    prompt_version: str,
    schema: type[TriageOutput],
    run_id: str,
    model_name: str,
    temperature: float,
    max_repairs: int,
    run_path: Path,
    score_path: Path,
) -> tuple[list[OutputRecord], list[CallRecord]]:
    template = load("triage", prompt_version)
    outputs: list[OutputRecord] = []
    call_records: list[CallRecord] = []
    for case in cases:
        adapter.reset()
        user_content = render_user(
            template,
            {"case_id": case["id"]},
            case["source"],
        )
        request = CompletionRequest(
            task="triage",
            case_id=case["id"],
            prompt_id="triage",
            prompt_version=prompt_version,
            system=template.system,
            user_content=user_content,
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        parsed: TriageOutput | None = None
        error: str | None = None
        try:
            parsed = complete_structured(
                adapter, request, schema, run_id, max_repairs=max_repairs
            )
        except (TypeError, ValueError) as exc:
            error = str(exc)

        payload = parsed.model_dump(mode="json") if parsed is not None else None
        record = OutputRecord(
            run_id=run_id,
            task="triage",
            case_id=case["id"],
            model_name=model_name,
            model_id=adapter.model_id,
            prompt_version=prompt_version,
            succeeded=parsed is not None,
            repairs=max(adapter.calls - 1, 0),
            output=payload,
            error=error,
        )
        append_record(run_path, record)
        outputs.append(record)
        call_records.extend(adapter.records())
        for score in score_case(
            run_id=run_id,
            case_id=case["id"],
            model_name=model_name,
            prompt_version=prompt_version,
            output=payload,
            gold=gold[case["id"]],
        ):
            append_record(score_path, score)
        print(
            f"triage.{prompt_version} {case['id']} succeeded={record.succeeded} "
            f"repairs={record.repairs}"
        )
    return outputs, call_records


def metric_sum(scores: list[ScoreRecord], version: str, metric: str) -> int:
    return sum(
        row.numerator
        for row in scores
        if row.prompt_version == version and row.metric == metric
    )


def summarize_calls(records: list[CallRecord]) -> dict[str, float | int]:
    latencies = [row.latency_ms for row in records]
    return {
        "output_tokens": sum(row.output_tokens for row in records),
        "median_latency_ms": float(median(latencies)) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "observations": len(records),
    }


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    model = settings.models["mistral"]
    adapter = CountingAdapter(OllamaAdapter(model_id=model.model_id))
    cases = load_cases(CASES_PATH)
    gold = load_gold(GOLD_PATH)

    docs = PROJECT_ROOT / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    run_path = docs / "day4-run.jsonl"
    score_path = docs / "day4-scores.jsonl"
    if run_path.exists():
        run_path.unlink()
    if score_path.exists():
        score_path.unlink()

    v1_outputs, v1_calls = run_version(
        adapter,
        cases,
        gold,
        prompt_version="v1",
        schema=TriageOutput,
        run_id=run_id,
        model_name=model.logical_name,
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
        run_path=run_path,
        score_path=score_path,
    )
    v2_outputs, v2_calls = run_version(
        adapter,
        cases,
        gold,
        prompt_version="v2",
        schema=TriageOutputWithAnalysis,
        run_id=run_id,
        model_name=model.logical_name,
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
        run_path=run_path,
        score_path=score_path,
    )

    scores = load_records(score_path, ScoreRecord)
    changed_queue = 0
    for first, second in zip(v1_outputs, v2_outputs, strict=True):
        q1 = (first.output or {}).get("queue")
        q2 = (second.output or {}).get("queue")
        if q1 != q2:
            changed_queue += 1

    v1_usage = summarize_calls(v1_calls)
    v2_usage = summarize_calls(v2_calls)
    print(f"run_id={run_id}")
    for version in ("v1", "v2"):
        print(f"triage.{version}")
        print(f"  queue correct: {metric_sum(scores, version, 'queue_correct')}/12")
        print(
            "  escalation correct: "
            f"{metric_sum(scores, version, 'escalation_correct')}/12"
        )
        print(f"  missed escalations: {metric_sum(scores, version, 'missed_escalation')}")
        print(
            "  unnecessary escalations: "
            f"{metric_sum(scores, version, 'unnecessary_escalation')}"
        )
        print(
            "  human-boundary passes: "
            f"{metric_sum(scores, version, 'human_boundary_pass')}/12"
        )
    print(f"changed-queue count: {changed_queue}")
    print(f"v1 usage={v1_usage}")
    print(f"v2 usage={v2_usage}")
    print(
        "token difference (v2-v1)="
        f"{int(v2_usage['output_tokens']) - int(v1_usage['output_tokens'])}"
    )
    print("provider/API cost=$0.00")


if __name__ == "__main__":
    main()