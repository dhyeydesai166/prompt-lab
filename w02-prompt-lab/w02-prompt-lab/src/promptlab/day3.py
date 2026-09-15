from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.schemas import PolicyExtraction, SummarizationOutput, schema_description
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

DOCUMENT_OPEN = "<document>"
DOCUMENT_CLOSE = "</document>"
MAX_OUTPUT_TOKENS = 1024
SUMMARIZATION_CASES = PROJECT_ROOT / "cases" / "summarization.jsonl"
EXTRACTION_CASES = PROJECT_ROOT / "cases" / "extraction.jsonl"
SUMMARIZE_PROMPT = PROJECT_ROOT / "src" / "prompts" / "summarize.v1.md"
EXTRACT_PROMPT = PROJECT_ROOT / "src" / "prompts" / "extract.v2.md"
LEAKAGE_NEEDLES = (
    "Norwyn",
    "Northglass",
    "Bellwater",
    "East Kestrel",
    "Redhaven",
)


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
        raise ValueError(f"expected 12 cases in {path}, found {len(cases)}")
    return cases


def fill_prompt(
    template: str,
    document_text: str,
    schema: type[BaseModel],
) -> tuple[str, str]:
    filled = template.replace("{schema_description}", schema_description(schema))
    if DOCUMENT_OPEN not in filled or DOCUMENT_CLOSE not in filled:
        raise ValueError("prompt is missing document markers")
    before, _, rest = filled.partition(DOCUMENT_OPEN)
    _, _, after = rest.partition(DOCUMENT_CLOSE)
    system = (before + after).strip()
    user_content = f"{DOCUMENT_OPEN}\n{document_text}\n{DOCUMENT_CLOSE}"
    return system, user_content


def headings_from_source(source: str) -> list[str]:
    headings: list[str] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if re.match(r"^\d+\.\s+\S", line):
            headings.append(line)
        elif line.startswith("#"):
            headings.append(re.sub(r"^#+\s*", "", line).strip())
    return headings


def citation_failures(
    output: SummarizationOutput | PolicyExtraction,
    source: str,
) -> list[str]:
    headings = headings_from_source(source)
    failures: list[str] = []
    for name, field in output.evidence_fields().items():
        if field.status != "present":
            continue
        citation = (field.citation or "").strip()
        if not citation:
            failures.append(f"{name}: missing citation")
            continue
        matched = any(
            citation == heading or citation in heading or heading in citation
            for heading in headings
        )
        if not matched:
            failures.append(f"{name}: {citation!r} is not a source heading")
    return failures


def leakage_hits(payload: dict[str, Any]) -> list[str]:
    blob = json.dumps(payload)
    return [needle for needle in LEAKAGE_NEEDLES if needle.lower() in blob.lower()]


def run_cases(
    adapter: CountingAdapter,
    cases: list[dict[str, Any]],
    *,
    task: str,
    prompt_path: Path,
    prompt_id: str,
    prompt_version: str,
    schema: type[SummarizationOutput] | type[PolicyExtraction],
    run_id: str,
    temperature: float,
    max_repairs: int,
    check_leakage: bool,
) -> list[dict[str, Any]]:
    template = prompt_path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for case in cases:
        adapter.reset()
        system, user_content = fill_prompt(template, case["source"], schema)
        request = CompletionRequest(
            task=task,  # type: ignore[arg-type]
            case_id=case["id"],
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            system=system,
            user_content=user_content,
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        output_obj: SummarizationOutput | PolicyExtraction | None = None
        error: str | None = None
        try:
            output_obj = complete_structured(
                adapter, request, schema, run_id, max_repairs=max_repairs
            )
        except (TypeError, ValueError) as exc:
            error = str(exc)

        payload = output_obj.model_dump(mode="json") if output_obj is not None else None
        citation_issues = (
            citation_failures(output_obj, case["source"]) if output_obj is not None else []
        )
        leak = leakage_hits(payload) if check_leakage and payload is not None else []
        row = {
            "run_id": run_id,
            "case_id": case["id"],
            "task": task,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "model_id": adapter.model_id,
            "succeeded": output_obj is not None,
            "repair_used": adapter.calls > 1,
            "adapter_calls": adapter.calls,
            "error": error,
            "output": payload,
            "citation_failures": citation_issues,
            "leakage_hits": leak,
            "records": [record.model_dump(mode="json") for record in adapter.records()],
        }
        rows.append(row)
        print(
            f"{task} {case['id']} succeeded={row['succeeded']} "
            f"repair_used={row['repair_used']} "
            f"citation_failures={len(citation_issues)} "
            f"leakage={len(leak)}"
        )
    return rows


def rate(flag: str, rows: list[dict[str, Any]]) -> str:
    hits = sum(1 for row in rows if row[flag])
    return f"{hits}/{len(rows)}"


def count_list_field(field: str, rows: list[dict[str, Any]]) -> int:
    return sum(len(row[field]) for row in rows)


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    model = settings.models["mistral"]
    adapter = CountingAdapter(OllamaAdapter(model_id=model.model_id))

    summarization_rows = run_cases(
        adapter,
        load_cases(SUMMARIZATION_CASES),
        task="summarization",
        prompt_path=SUMMARIZE_PROMPT,
        prompt_id="summarize",
        prompt_version="v1",
        schema=SummarizationOutput,
        run_id=run_id,
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
        check_leakage=False,
    )
    extraction_rows = run_cases(
        adapter,
        load_cases(EXTRACTION_CASES),
        task="extraction",
        prompt_path=EXTRACT_PROMPT,
        prompt_id="extract",
        prompt_version="v2",
        schema=PolicyExtraction,
        run_id=run_id,
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
        check_leakage=True,
    )

    all_rows = summarization_rows + extraction_rows
    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    evidence = docs_dir / "day3-run.jsonl"
    evidence.write_text(
        "".join(json.dumps(row) + "\n" for row in all_rows),
        encoding="utf-8",
    )

    print(f"run_id={run_id}")
    print(f"wrote {evidence} ({len(all_rows)} rows)")
    print(f"summarization_repair_rate={rate('repair_used', summarization_rows)}")
    print(f"extraction_repair_rate={rate('repair_used', extraction_rows)}")
    print(f"example_leakage_count={count_list_field('leakage_hits', extraction_rows)}")
    print(
        "citation_existence_failure_count="
        f"{count_list_field('citation_failures', all_rows)}"
    )


if __name__ == "__main__":
    main()