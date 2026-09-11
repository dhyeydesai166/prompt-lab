# Day 2 model comparison

This assignment compares how many calls finished and how many tokens and milliseconds each model used. Both models ran the same 12 cases, the same prompt, the same temperature, and the same max output tokens. Charge is $0.00 for both, so there is no dollar ranking.

With thinking on and a 256-token ceiling, Mistral finished 12/12. Every Qwen call stopped with `done_reason=length`. The adapter recorded `TruncatedResponseError` and did not retry. Token and latency totals could not be compared because Qwen did not finish.

The official run also uses a 256-token ceiling, but the adapter sends `think: false` for both models. Both then finished 12/12, so counts, tokens, and latency can be compared.

| Model | Successful completions | Attempts | Input tokens | Output tokens | Total latency (ms) | Mean latency (ms) | Median latency (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mistral:7b | 12 | 12 | 2775 | 1318 | 54675 | 4556.2 | 3919.5 |
| qwen3:8b | 12 | 12 | 2559 | 746 | 37791 | 3149.2 | 2789.0 |

Qwen used fewer output tokens and less time than Mistral on this set. That is a workload difference, not a dollar ranking.