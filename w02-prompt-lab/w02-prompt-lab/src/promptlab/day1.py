"""Day 1: instrument three Mistral extraction calls."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record, compute_cost

CASE_IDS = ("E12", "E07", "E11")
PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
CASES_PATH = PROJECT_ROOT / "cases" / "extraction.jsonl"
NORMAL_NUM_PREDICT = 256
TRUNCATION_NUM_PREDICT = 8
TIMEOUT_S = 180.0


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases[row["id"]] = row
    missing = [case_id for case_id in CASE_IDS if case_id not in cases]
    if missing:
        raise ValueError(f"Missing cases: {missing}")
    return cases


def fill_prompt(template: str, document_text: str) -> str:
    return template.replace("{document_text}", document_text)


def call_ollama(
    *,
    base_url: str,
    model_id: str,
    prompt: str,
    temperature: float,
    num_predict: int,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    response = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    latency_ms = int((time.perf_counter() - started) * 1000)
    payload: dict[str, Any] = response.json()
    return payload, latency_ms


def require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Ollama response missing integer field {key!r}: {value!r}")
    return value


def record_from_call(
    *,
    run_id: str,
    model_id: str,
    case_id: str,
    temperature: float,
    max_output_tokens: int,
    payload: dict[str, Any],
    latency_ms: int,
    error_type: str | None,
) -> CallRecord:
    input_tokens = require_int(payload, "prompt_eval_count")
    output_tokens = require_int(payload, "eval_count")
    stop_reason = payload.get("done_reason")
    response_text = payload.get("response")
    return CallRecord(
        record_id=str(uuid.uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task="extraction",
        case_id=case_id,
        prompt_id="baseline",
        prompt_version="v0",
        attempt=1,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        latency_ms=latency_ms,
        cost_usd=compute_cost(model_id, input_tokens, output_tokens),
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        error_type=error_type,
        response_text=response_text if isinstance(response_text, str) else None,
    )


def main() -> None:
    settings = Settings.from_env()
    model_id = settings.models["mistral"].model_id
    temperature = settings.temperature
    run_id = str(uuid.uuid4())

    cases = load_cases(CASES_PATH)
    template = PROMPT_PATH.read_text(encoding="utf-8")

    demo_payload, demo_latency = call_ollama(
        base_url=settings.ollama_base_url,
        model_id=model_id,
        prompt=fill_prompt(template, cases["E11"]["source"]),
        temperature=temperature,
        num_predict=TRUNCATION_NUM_PREDICT,
    )
    print(f"truncation demo done_reason={demo_payload.get('done_reason')!r}")
    demo_error = (
        "TruncatedResponseError" if demo_payload.get("done_reason") == "length" else None
    )
    append_record(
        record_from_call(
            run_id=run_id,
            model_id=model_id,
            case_id="E11",
            temperature=temperature,
            max_output_tokens=TRUNCATION_NUM_PREDICT,
            payload=demo_payload,
            latency_ms=demo_latency,
            error_type=demo_error,
        ),
        run_id,
    )

    successful: list[CallRecord] = []
    for case_id in CASE_IDS:
        payload, latency_ms = call_ollama(
            base_url=settings.ollama_base_url,
            model_id=model_id,
            prompt=fill_prompt(template, cases[case_id]["source"]),
            temperature=temperature,
            num_predict=NORMAL_NUM_PREDICT,
        )
        record = record_from_call(
            run_id=run_id,
            model_id=model_id,
            case_id=case_id,
            temperature=temperature,
            max_output_tokens=NORMAL_NUM_PREDICT,
            payload=payload,
            latency_ms=latency_ms,
            error_type=None,
        )
        append_record(record, run_id)
        successful.append(record)
        print(
            f"{record.case_id} input_tokens={record.input_tokens} "
            f"latency_ms={record.latency_ms} stop_reason={record.stop_reason}"
        )

    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    evidence = docs_dir / "day1-run.jsonl"
    evidence.write_text(
        "".join(record.model_dump_json() + "\n" for record in successful),
        encoding="utf-8",
    )
    print(f"run_id={run_id}")
    print(f"wrote {evidence} ({len(successful)} records)")


if __name__ == "__main__":
    main()