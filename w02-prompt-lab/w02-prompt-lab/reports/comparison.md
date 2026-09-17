# Local Model Comparison

Run `c9ea91da-c132-40d0-9310-5eb3c33bf5fb`. 12 cases per task. Scorer `day5-v1`.
Temperature 0.0. Local Ollama (`mistral:7b`, `qwen3:8b`). Every row names the prompt it ran.
Headline latency is case wall-clock (first HTTP → final parse or failure). n = 12 case observations per row.

## Triage

Prompt: `triage.v2` for both models. Qwen is transfer (prompt written against Mistral).

| Model | Prompt | Parse ok | Queue correct | Escalation correct | Missed escalations | Unnecessary escalations | Boundary pass | PII leaks | Median case latency | Max case latency | Cost per case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mistral | triage.v2 | 11 of 12 | 6 of 12 | 10 of 12 | 0 | 1 | 11 of 12 | 0 | 7.3s | 10.2s | $0.00 |
| qwen | triage.v2 (transfer) | 12 of 12 | 9 of 12 | 8 of 12 | 0 | 4 | 12 of 12 | 0 | 7.7s | 9.3s | $0.00 |

Cost per case includes transport retries and schema repair. Repair cases: mistral 1 of 12, qwen 0 of 12.
HTTP POST median/max (all attempts): mistral 7208 / 10213 ms (13 obs); qwen 7720 / 9323 ms (12 obs).
Mistral T02 failed parse after one repair (invalid `TriageOutput` JSON). Queue/escalation counts still include that case as a miss.

## Summarization

Prompt: `summarize.v1` for both. Qwen is transfer.

| Model | Prompt | Parse ok | Required-evidence recall | Citation correctness | Invented evidence | Missed evidence | Version selection | Median case latency | Max case latency | Cost per case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mistral | summarize.v1 | 0 of 12 | 0 of 60 | 0 of 12 | 0 | 60 of 60 | 0 of 1 | 33.3s | 38.2s | $0.00 |
| qwen | summarize.v1 (transfer) | 12 of 12 | 60 of 60 | 64 of 64 | 4 | 0 of 60 | 1 of 1 | 12.1s | 15.9s | $0.00 |

Repair cases: mistral 12 of 12, qwen 0 of 12.
Mistral failed all 12 parses: it echoed schema-shaped JSON (`document_status` as `{}`, missing field `value`/`status`) instead of `SummarizationOutput`.
HTTP: mistral 15034 / 32009 ms (24 obs); qwen 12114 / 15853 ms (12 obs).

## Extraction

Prompt: `extract.v2` for both. Qwen is transfer.

| Model | Prompt | Parse ok | Required-evidence recall | Citation correctness | Invented | Missed | Unsupported-field avoidance | Version selection | Median case latency | Max case latency | Cost per case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mistral | extract.v2 | 3 of 12 | 14 of 73 | 16 of 25 | 2 | 58 of 73 | 7 of 18 | 0 of 1 | 45.2s | 53.9s | $0.00 |
| qwen | extract.v2 (transfer) | 12 of 12 | 71 of 73 | 73 of 74 | 2 | 1 of 73 | 16 of 18 | 1 of 1 | 14.9s | 17.5s | $0.00 |

Repair cases: mistral 9 of 12, qwen 0 of 12.
Mistral parsed 3 of 12. The other 9 failed schema after repair (wrong object, extra keys, or example JSON such as `John Doe`). Failed cases score as missed evidence, not as “wrong fields on valid JSON.”
HTTP: mistral 18936 / 42277 ms (21 obs); qwen 14936 / 17496 ms (12 obs).

### Extraction add-on: adapted prompt (not in the 72-eval)

Run `extract-v3-qwen`. Same 12 extraction cases, Qwen only, `extract.v3`.

| Model | Prompt | Parse ok | Required-evidence recall | Median case latency | Max case latency | Cost per case |
| --- | --- | --- | --- | --- | --- | --- |
| qwen | extract.v3 (adapted) | 0 of 12 | 0 of 73 | 16.9s | 28.6s | $0.00 |

Repair cases: 12 of 12. Qwen `extract.v3` failed all 12 parses: extra keys and illegal statuses copied from the dumped JSON schema. This is transfer vs adapted on Qwen, not a second 72-eval.

## What this does not show

The set is 12 cases per task. A one- or two-case difference is not a ranking.
No percentages. No claim about production volume or document types absent from the set.
Qwen rows on v1/v2 ran prompts developed against Mistral and are labeled transfer. They are evidence of transfer, not of Qwen’s ceiling on an adapted prompt — except extraction, where adapted `extract.v3` was measured and parsed 0 of 12.
Latency is from this container to host Ollama on one afternoon. Median and max only; no p95.
Provider/API cost is $0.00; token and repair load still differ by row.

## Recommendation

Triage: no ranking. Both missed 0 escalations. Qwen routed 9 of 12 vs mistral 6 of 12 and over-escalated 4 vs 1. Reopen if missed escalations move above 0, or if unnecessary escalations become the binding constraint. Prompt: `triage.v2`.

Summarization: Qwen on `summarize.v1` (transfer). Mistral is eliminated on this prompt: 0 of 12 parse. Reopen if Mistral is re-measured on an adapted summarization prompt.

Extraction: Qwen on `extract.v2` (transfer) for this set. Mistral parsed 3 of 12. Qwen `extract.v3` (adapted) parsed 0 of 12, so adaptation is not an open excuse for the v2 transfer row — it was measured and it failed parse. Reopen only with a different adapted prompt, not by loosening Pydantic.

Constraints, rejected alternatives, and review triggers: `docs/model-decision.md`.

