# Day 3 notes

- Summarization repair rate: 4/12
- Extraction repair rate: 1/12
- Example leakage count: 3
- Citation-existence failure count: 6

The most common validation error was the model returning the generated JSON Schema (`$defs`, schema title) instead of a SummarizationOutput instance, so Pydantic reported missing fields or extra_forbidden.

In response I kept schema_description as the code-derived contract, relied on the one-shot repair (which did not convert schema echo into an instance), and treated E12's Northglass/Norwyn/Bellwater copy as few-shot leakage rather than editing extract.v2 after this run.