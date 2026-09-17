from __future__ import annotations

import time
from collections.abc import Callable

from promptlab.timings import ollama_durations_ms


def measure_case_latency(fn: Callable[[], None]) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def test_ollama_durations_ns_to_ms() -> None:
    got = ollama_durations_ms(
        {
            "load_duration": 1_000_000_000,
            "prompt_eval_duration": 2_000_000_000,
            "eval_duration": 3_000_000_000,
            "total_duration": 6_000_000_000,
        }
    )
    assert got["load_duration_ms"] == 1000.0
    assert got["eval_duration_ms"] == 3000.0


def test_case_latency_is_one_number_for_two_http_calls() -> None:
    http_ms: list[float] = []

    def two_posts() -> None:
        for delay in (0.02, 0.03):
            t0 = time.perf_counter()
            time.sleep(delay)
            http_ms.append((time.perf_counter() - t0) * 1000)

    case_ms = measure_case_latency(two_posts)
    assert len(http_ms) == 2
    assert case_ms >= sum(http_ms) - 5
