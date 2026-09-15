# Day 4 notes

- run_id: 2a1db94d-8fe6-4695-bc41-8b5e2d9a0b32
- model: mistral
- temperature: 0.0
- provider/API cost: $0.00

## v1
- queue correct: 7/12
- escalation correct: 5/12
- missed escalations: 2
- unnecessary escalations: 3
- human-boundary passes: 8/12
- parse failures: T02, T06, T08, T12 (4/12, each used one repair)
- output tokens: 2068
- median latency ms: 6056.5
- max latency ms: 10327
- observations: 16

## v2
- queue correct: 6/12
- escalation correct: 10/12
- missed escalations: 0
- unnecessary escalations: 1
- human-boundary passes: 11/12
- parse failures: T02 (1/12, one repair)
- output tokens: 2320
- median latency ms: 7615.0
- max latency ms: 9304
- observations: 13

- changed-queue count: 4
- token difference (v2-v1): 252

Twelve cases: the one-case queue drop is not a finding. Escalation and parse success moved several cases. Analysis was the only prompt/schema change; scoring did not use the analysis field. Prompts were not edited after this run.