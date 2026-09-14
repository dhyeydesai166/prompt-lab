from __future__ import annotations

import json
import uuid
from pathlib import Path
from statistics import mean, median
from typing import Any

from promptlab.adapters.base import CompletionRequest
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord

CASES_PATH = PROJECT_ROOT / "cases" / "summarization.jsonl"
PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
MAX_OUTPUT_TOKENS = 256
DOCUMENT_OPEN = "<document>"
DOCUMENT_CLOSE = "</document>"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    if len(cases) != 12:
        raise ValueError(f"expected 12 summarization cases, found {len(cases)}")
    return cases


def split_prompt(template: str, document_text: str) -> tuple[str, str]:
    if DOCUMENT_OPEN not in template or DOCUMENT_CLOSE not in template:
        raise ValueError("baseline prompt is missing document markers")
    system, _, remainder = template.partition(DOCUMENT_OPEN)
    _, _, _ignored = remainder.partition(DOCUMENT_CLOSE)
    user_content = (
        f"{DOCUMENT_OPEN}\n{document_text}\n{DOCUMENT_CLOSE}"
    )
    return system.strip(), user_content


def summarize(records: list[CallRecord], model_id: str) -> dict[str, float | int | str]:
    selected = [record for record in records if record.model_id == model_id]
    successes = [record for record in selected if record.error_type is None]
    latencies = [record.latency_ms for record in successes]
    return {
        "model_id": model_id,
        "attempts": len(selected),
        "successes": len(successes),
        "input_tokens": sum(record.input_tokens for record in successes),
        "output_tokens": sum(record.output_tokens for record in successes),
        "latency_ms": sum(latencies),
        "mean_latency_ms": round(mean(latencies), 1) if latencies else 0,
        "median_latency_ms": median(latencies) if latencies else 0,
    }


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    cases = load_cases(CASES_PATH)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    all_records: list[CallRecord] = []

    for model in settings.models.values():
        adapter = OllamaAdapter(model_id=model.model_id)
        for case in cases:
            system, user_content = split_prompt(template, case["source"])
            result = adapter.complete(
                CompletionRequest(
                    task="summarization",
                    case_id=case["id"],
                    prompt_id="baseline",
                    prompt_version="v0",
                    system=system,
                    user_content=user_content,
                    temperature=settings.temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
                run_id,
            )
            all_records.extend(result.records)
            print(
                f"{model.logical_name} {case['id']} succeeded={result.succeeded} "
                f"attempts={len(result.records)}"
            )

    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    evidence = docs_dir / "day2-run.jsonl"
    evidence.write_text(
        "".join(record.model_dump_json() + "\n" for record in all_records),
        encoding="utf-8",
    )
    print(f"run_id={run_id}")
    print(f"wrote {evidence} ({len(all_records)} records)")
    for model in settings.models.values():
        print(summarize(all_records, model.model_id))


if __name__ == "__main__":
    main()