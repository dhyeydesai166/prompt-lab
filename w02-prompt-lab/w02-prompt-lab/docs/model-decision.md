# Model Decision Record

Run `c9ea91da-c132-40d0-9310-5eb3c33bf5fb`. Local Ollama, temperature 0.0, scorer `day5-v1`, 12 cases per task, provider cost $0.00.
Evidence tables: `reports/comparison.md`. Qwen on v1/v2 is transfer unless noted.

## Decision

Keep **Qwen** for summarization (`summarize.v1`) and extraction (`extract.v2`) on this set.
Do **not** rank triage: both missed 0 escalations on `triage.v2`; Qwen routed more cases and over-escalated more.

## Constraints this was made against

- Output must validate the frozen Pydantic schemas after at most one repair.
- Do not loosen `extra="forbid"` or coerce JSON in code to raise parse counts.
- Twelve cases support direction and hard-constraint cuts, not ranking by one or two cases.
- Cost is $0.00; load still matters (repairs, tokens, case latency).

## Rejected alternatives

- **Mistral + `summarize.v1`:** 0 of 12 parse. Eliminated on this prompt.
- **Mistral + `extract.v2`:** 3 of 12 parse. Not usable as the extraction path here.
- **Qwen + `extract.v3` (adapted):** 0 of 12 parse. Adaptation was measured; it failed. Do not treat the v2 transfer row as “untested on an adapted prompt.”

## What would reopen it

- Missed-escalation count above 0 on a scheduled triage run, or a product rule that treats unnecessary escalations as the binding cost.
- A *different* adapted extraction/summarization prompt (not v3 as written) that parses and then beats `extract.v2` / `summarize.v1` on recall, citations, and invented fields.
- A larger case set. This set cannot support production-volume claims.