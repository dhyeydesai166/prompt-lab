from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.config import Settings
from promptlab.errors import (
    PermanentProviderError,
    TransientProviderError,
    TruncatedResponseError,
    UnknownModelError,
)
from promptlab.usage import CallRecord, append_record, compute_cost

MAX_ATTEMPTS = 3
TIMEOUT_S = 180.0
Outcome = Literal["success", "transient", "permanent", "truncated"]


class OllamaAdapter:
    provider = "ollama"

    def __init__(self, model_id: str) -> None:
        settings = Settings.from_env()
        configured = {model.model_id for model in settings.models.values()}
        if model_id not in configured:
            raise UnknownModelError(model_id)
        self.model_id = model_id
        self._base_url = settings.ollama_base_url

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        records: list[CallRecord] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            record, outcome = self._one_attempt(request, run_id, attempt)
            append_record(record, run_id)
            records.append(record)

            if outcome == "success":
                return CompletionResult(
                    succeeded=True,
                    text=record.response_text,
                    error_type=None,
                    records=records,
                )
            if outcome == "transient" and attempt < MAX_ATTEMPTS:
                time.sleep(_backoff_seconds(attempt))
                continue
            return CompletionResult(
                succeeded=False,
                text=record.response_text if outcome == "truncated" else None,
                error_type=record.error_type,
                records=records,
            )
        raise RuntimeError("unreachable")

    def _one_attempt(
        self,
        request: CompletionRequest,
        run_id: str,
        attempt: int,
    ) -> tuple[CallRecord, Outcome]:
        started = time.perf_counter()
        payload: dict[str, Any] | None = None
        error_type: str | None = None
        outcome: Outcome = "success"
        try:
            response: Any = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self.model_id,
                    "prompt": request.user_content,
                    "system": request.system,
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_output_tokens,
                    },
                },
                timeout=TIMEOUT_S,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            status_code = int(getattr(response, "status_code", 200))
            if status_code >= 500:
                error_type = TransientProviderError.__name__
                outcome = "transient"
            elif status_code >= 400:
                error_type = PermanentProviderError.__name__
                outcome = "permanent"
            else:
                payload = dict(response.json())
                if payload.get("done_reason") == "length":
                    error_type = TruncatedResponseError.__name__
                    outcome = "truncated"
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
            latency_ms = int((time.perf_counter() - started) * 1000)
            error_type = TransientProviderError.__name__
            outcome = "transient"

        input_tokens = _as_int(payload, "prompt_eval_count") if payload else 0
        output_tokens = _as_int(payload, "eval_count") if payload else 0
        stop_reason = payload.get("done_reason") if payload else None
        text = _response_text(payload) if payload else None

        record = CallRecord(
            record_id=str(uuid.uuid4()),
            run_id=run_id,
            timestamp=datetime.now(UTC),
            provider="ollama",
            model_id=self.model_id,
            task=request.task,
            case_id=request.case_id,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            attempt=attempt,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=None,
            latency_ms=latency_ms,
            cost_usd=compute_cost(self.model_id, input_tokens, output_tokens),
            stop_reason=stop_reason if isinstance(stop_reason, str) else None,
            error_type=error_type,
            response_text=text,
        )
        return record, outcome


def _as_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) else 0


def _response_text(payload: dict[str, Any]) -> str | None:
    response = payload.get("response")
    if isinstance(response, str):
        return response
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return None


def _backoff_seconds(attempt: int) -> float:
    return float((2 ** (attempt - 1)) * 0.1 + random.uniform(0, 0.05))