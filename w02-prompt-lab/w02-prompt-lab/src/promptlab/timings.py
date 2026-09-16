from __future__ import annotations

from typing import Any


def ollama_durations_ms(payload: dict[str, Any] | None) -> dict[str, float | None]:
    """Convert Ollama nanosecond clocks to milliseconds. stream stays False."""
    keys = (
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
        "total_duration",
    )
    out: dict[str, float | None] = {}
    if not payload:
        for key in keys:
            out[f"{key}_ms"] = None
        return out
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, int | float) and raw >= 0:
            out[f"{key}_ms"] = float(raw) / 1_000_000.0
        else:
            out[f"{key}_ms"] = None
    return out
