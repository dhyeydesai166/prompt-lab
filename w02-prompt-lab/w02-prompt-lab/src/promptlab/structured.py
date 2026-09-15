from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, ModelAdapter


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _json_payload(text: str | None) -> Any:
    if text is None or not text.strip():
        raise ValueError("empty completion text")
    candidate = _strip_code_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(candidate[start : end + 1])


def _validate[T: BaseModel](text: str | None, schema: type[T]) -> tuple[T | None, str]:
    try:
        return schema.model_validate(_json_payload(text)), ""
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return None, str(exc)


def complete_structured[T: BaseModel](
    adapter: ModelAdapter,
    request: CompletionRequest,
    schema: type[T],
    run_id: str,
    max_repairs: int = 1,
) -> T:
    """Return a schema-validated completion with a bounded semantic repair loop.

    Transport retry remains inside the adapter.
    Schema/content repair belongs here.

    On validation failure, send the validation error text back to the model and
    instruct it to correct only what the error concerns. Do not perform more
    than max_repairs semantic repair attempts.
    """

    last_error = "structured completion failed"
    current = request
    for repair_index in range(max_repairs + 1):
        result = adapter.complete(current, run_id)
        parsed, last_error = _validate(result.text, schema)
        if parsed is not None:
            return parsed
        if repair_index >= max_repairs:
            break
        current = request.model_copy(
            update={
                "user_content": (
                    "The previous JSON failed validation.\n\n"
                    f"{last_error}\n\n"
                    "Correct only what the validation error concerns. "
                    "Return only the JSON object. Do not wrap it in Markdown."
                )
            }
        )
    raise ValueError(last_error)