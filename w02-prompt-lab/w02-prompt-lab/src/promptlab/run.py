from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, ModelConfig, Settings
from promptlab.corpus import GoldLabel, load_cases, validate_corpus
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, ScoreRecord, UsageRecord, append_record
from promptlab.rules import VersionCandidate, select_current_version
from promptlab.schemas import (
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    TriageOutputWithAnalysis,
    schema_description,
)
from promptlab.scoring import SCORER_VERSION, failure_scores, score_output
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

TASKS: dict[TaskName, tuple[str, str, type[StrictModel], int]] = {
    "summarization": ("summarize", "v1", SummarizationOutput, 1024),
    "extraction": ("extract", "v2", PolicyExtraction, 1024),
    "triage": ("triage", "v2", TriageOutputWithAnalysis, 512),
}

UsageKind = Literal["primary", "transport_retry", "repair", "repair_retry"]
UsageStatus = Literal["success", "schema_invalid", "transport_error"]


class CountingAdapter:
    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0
        self.results: list[CompletionResult] = []
        self.timings: list[dict[str, float | None]] = []

    def reset(self) -> None:
        self.calls = 0
        self.results = []
        self.timings = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        result = self._inner.complete(request, run_id)
        self.results.append(result)
        self.timings.extend(self._inner.attempt_timings)
        return result

    def records(self) -> list[CallRecord]:
        collected: list[CallRecord] = []
        for result in self.results:
            collected.extend(result.records)
        return collected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local two-model prompt comparison")
    parser.add_argument("--run-id", help="Stable identifier for this run")
    parser.add_argument("--task", choices=["triage", "summarization", "extraction"])
    parser.add_argument("--model", choices=["mistral", "qwen"])
    parser.add_argument("--limit", type=int, help="Limit cases per task for a smoke run")
    parser.add_argument(
        "--prompt-version",
        choices=["v2", "v3"],
        help="Extraction only: v2 is transfer, v3 writes docs/day5-extract-v3-*.jsonl",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and corpus without calling Ollama",
    )
    return parser


def _kind(result_index: int, record_index: int) -> UsageKind:
    if result_index == 0:
        return "primary" if record_index == 0 else "transport_retry"
    return "repair" if record_index == 0 else "repair_retry"


def _status(record: CallRecord) -> UsageStatus:
    if record.error_type is None:
        return "success"
    return "transport_error"


def _usage_from_case(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model: ModelConfig,
    version: str,
    adapter: CountingAdapter,
    schema_failed: bool,
) -> list[UsageRecord]:
    rows: list[UsageRecord] = []
    index = 0
    for result_index, result in enumerate(adapter.results):
        for record_index, record in enumerate(result.records):
            timings = adapter.timings[index] if index < len(adapter.timings) else {}
            index += 1
            status: UsageStatus = _status(record)
            if (
                schema_failed
                and result_index == len(adapter.results) - 1
                and record_index == len(result.records) - 1
                and record.error_type is None
            ):
                status = "schema_invalid"
            rows.append(
                UsageRecord(
                    run_id=run_id,
                    task=task,
                    case_id=case_id,
                    model_name=model.logical_name,
                    model_id=model.model_id,
                    prompt_version=version,
                    attempt=record.attempt,
                    kind=_kind(result_index, record_index),
                    status=status,
                    prompt_tokens=record.input_tokens,
                    completion_tokens=record.output_tokens,
                    latency_ms=float(record.latency_ms),
                    cost_usd=Decimal(str(record.cost_usd)),
                    error=record.error_type,
                    load_duration_ms=timings.get("load_duration_ms"),
                    prompt_eval_duration_ms=timings.get("prompt_eval_duration_ms"),
                    eval_duration_ms=timings.get("eval_duration_ms"),
                    total_duration_ms=timings.get("total_duration_ms"),
                )
            )
    return rows


def _version_fields(output: StrictModel) -> tuple[str, str] | None:
    if isinstance(output, SummarizationOutput | PolicyExtraction):
        version = output.version
        effective = output.effective_date
        if (
            version.status == "present"
            and effective.status == "present"
            and isinstance(version.value, str)
            and isinstance(effective.value, str)
        ):
            return version.value, effective.value
    return None


def _add_version_scores(
    *,
    run_id: str,
    task: TaskName,
    model_name: str,
    prompt_id: str,
    prompt_version: str,
    labels: list[GoldLabel],
    outputs: dict[str, StrictModel],
    scores_path: Path,
    all_scores: list[ScoreRecord],
    model_id: str,
) -> None:
    grouped: dict[str, list[GoldLabel]] = defaultdict(list)
    for label in labels:
        if label.version_group:
            grouped[label.version_group].append(label)

    for group_name, group_labels in grouped.items():
        if len(group_labels) < 2:
            continue
        expected = next(
            (
                label.expected_current_case_id
                for label in group_labels
                if label.expected_current_case_id
            ),
            None,
        )
        as_of_raw = next((label.as_of for label in group_labels if label.as_of), None)
        if expected is None or as_of_raw is None:
            continue
        candidates: list[VersionCandidate] = []
        for label in group_labels:
            output = outputs.get(label.id)
            if output is None:
                continue
            extracted = _version_fields(output)
            if extracted is None:
                continue
            version, effective_raw = extracted
            try:
                effective = date.fromisoformat(effective_raw)
            except ValueError:
                continue
            candidates.append(
                VersionCandidate(case_id=label.id, version=version, effective_date=effective)
            )
        selected = select_current_version(candidates, date.fromisoformat(as_of_raw))
        record = ScoreRecord(
            run_id=run_id,
            task=task,
            case_id=f"version:{group_name}",
            model_name=model_name,
            prompt_version=prompt_version,
            scorer_version=SCORER_VERSION,
            metric="version_selection_accuracy",
            numerator=int(selected is not None and selected.case_id == expected),
            denominator=1,
            detail=f"expected={expected}; selected={selected.case_id if selected else 'none'}",
            model_id=model_id,
            prompt_id=prompt_id,
        )
        append_record(scores_path, record)
        all_scores.append(record)


def main() -> None:
    args = _parser().parse_args()
    counts = validate_corpus()
    if args.validate_only:
        print("Corpus valid: " + ", ".join(f"{task}={count}" for task, count in counts.items()))
        return

    run_id = cast(str | None, args.run_id)
    if run_id is None or not RUN_ID_PATTERN.fullmatch(run_id):
        raise SystemExit("--run-id is required and must use letters, numbers, '.', '_' or '-'")
    limit = cast(int | None, args.limit)
    if limit is not None and limit < 1:
        raise SystemExit("--limit must be at least 1")

    selected_tasks: list[TaskName]
    if args.task:
        selected_tasks = [cast(TaskName, args.task)]
    else:
        selected_tasks = ["summarization", "extraction", "triage"]

    prompt_version_override = cast(str | None, args.prompt_version)
    if prompt_version_override == "v3" and selected_tasks != ["extraction"]:
        raise SystemExit("--prompt-version v3 requires --task extraction")

    settings = Settings.from_env()
    selected_models = [cast(str, args.model)] if args.model else list(settings.models)
    docs = PROJECT_ROOT / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    if prompt_version_override == "v3":
        usage_path = docs / "day5-extract-v3-usage.jsonl"
        outputs_path = docs / "day5-extract-v3-run.jsonl"
        scores_path = docs / "day5-extract-v3-scores.jsonl"
    else:
        usage_path = docs / "day5-usage.jsonl"
        outputs_path = docs / "day5-run.jsonl"
        scores_path = docs / "day5-scores.jsonl"
    for path in (usage_path, outputs_path, scores_path):
        if path.exists():
            path.unlink()

    all_usage: list[UsageRecord] = []
    all_outputs: list[OutputRecord] = []
    all_scores: list[ScoreRecord] = []
    total_cost = Decimal("0")
    validated_by_task_model: dict[tuple[TaskName, str], dict[str, StrictModel]] = defaultdict(dict)
    labels_by_task: dict[TaskName, list[GoldLabel]] = defaultdict(list)

    for task in selected_tasks:
        prompt_id, version, schema, max_tokens = TASKS[task]
        if task == "extraction" and prompt_version_override is not None:
            version = prompt_version_override
        template = load(prompt_id, version)
        pairs = load_cases(task)
        if limit is not None:
            pairs = pairs[:limit]
        labels_by_task[task] = [gold for _case, gold in pairs]
        for model_name in selected_models:
            model = settings.models[model_name]
            adapter = CountingAdapter(OllamaAdapter(model_id=model.model_id))
            for case, gold in pairs:
                if total_cost >= settings.per_run_cap_usd:
                    raise SystemExit(
                        f"Per-run cost cap reached before {task}/{model_name}/{case.id}"
                    )
                adapter.reset()
                variables = {
                    "schema_description": schema_description(schema),
                    "case_id": case.id,
                }
                user_content = render_user(template, variables, case.document_text)
                request = CompletionRequest(
                    task=task,
                    case_id=case.id,
                    prompt_id=prompt_id,
                    prompt_version=version,
                    system=template.system,
                    user_content=user_content,
                    temperature=settings.temperature,
                    max_output_tokens=max_tokens,
                )
                t0 = time.perf_counter()
                parsed: StrictModel | None
                error: str | None
                try:
                    parsed = complete_structured(
                        adapter,
                        request,
                        schema,
                        run_id,
                        max_repairs=settings.max_schema_repairs,
                    )
                    error = None
                except (TypeError, ValueError) as exc:
                    parsed = None
                    error = str(exc)
                case_latency_ms = (time.perf_counter() - t0) * 1000

                usage_rows = _usage_from_case(
                    run_id=run_id,
                    task=task,
                    case_id=case.id,
                    model=model,
                    version=version,
                    adapter=adapter,
                    schema_failed=parsed is None,
                )
                for usage_record in usage_rows:
                    append_record(usage_path, usage_record)
                    all_usage.append(usage_record)
                    total_cost += usage_record.cost_usd

                payload = parsed.model_dump(mode="json") if parsed is not None else None
                output_record = OutputRecord(
                    run_id=run_id,
                    task=task,
                    case_id=case.id,
                    model_name=model_name,
                    model_id=model.model_id,
                    prompt_version=version,
                    succeeded=parsed is not None,
                    repairs=max(adapter.calls - 1, 0),
                    output=payload,
                    error=error,
                    case_latency_ms=case_latency_ms,
                )
                append_record(outputs_path, output_record)
                all_outputs.append(output_record)
                if parsed is not None:
                    validated_by_task_model[(task, model_name)][case.id] = parsed
                    case_scores = score_output(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=version,
                        output=parsed,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )
                else:
                    case_scores = failure_scores(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=version,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )
                for score in case_scores:
                    append_record(scores_path, score)
                    all_scores.append(score)
                print(
                    f"{task:13} {model_name:8} {case.id:5} "
                    f"{'ok' if output_record.succeeded else 'failed'} "
                    f"case_ms={case_latency_ms:.0f} repairs={output_record.repairs}"
                )
            if task != "triage":
                _add_version_scores(
                    run_id=run_id,
                    task=task,
                    model_name=model_name,
                    prompt_id=prompt_id,
                    prompt_version=version,
                    labels=labels_by_task[task],
                    outputs=validated_by_task_model[(task, model_name)],
                    scores_path=scores_path,
                    all_scores=all_scores,
                    model_id=model.model_id,
                )

    print(f"run_id={run_id}")
    print(f"outputs={outputs_path}")
    print(f"scores={scores_path}")
    print(f"usage={usage_path}")
    print(f"provider/API cost=${total_cost}")
    print("provider/API cost=$0.00")


if __name__ == "__main__":
    main()
