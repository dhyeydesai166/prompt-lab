# Day 3 Assignment: Prompts That Return Validated Objects (Local Ollama Version)

## Objective

Author the summarization and extraction output schemas/prompts and build a bounded repair path
that converts a schema-invalid model response into a corrected response instead of silently
keeping or discarding it.

By the end of the lab:

- summarization returns a validated `ProcedureSummary`
- extraction returns a validated `PolicyExtraction`
- every `status: present` field carries source-section evidence
- schema failures receive at most one semantic repair attempt
- repair rate, example leakage, and citation-existence failures are measured

## Important Day 3 change

Do not use `baseline.v0.md` in this lab.

Day 3 uses:

- `src/promptlab/prompts/summarize.v1.md` for summarization
- student-authored `src/promptlab/prompts/extract.v1.md`
- student-authored `src/promptlab/prompts/extract.v2.md`

## Shipped starter material

The repository should already contain:

- `src/promptlab/schemas.py` with `FieldStatus`, `Evidence`, and `PolicyExtraction`
- `src/promptlab/prompts/summarize.v1.md`
- `src/promptlab/structured.py` stub
- `tests/test_structured_contract.py`
- `cases/summarization.jsonl` containing 12 summarization cases
- `cases/extraction.jsonl` containing 12 extraction cases
- `examples/` containing four boundary documents

The scored case corpora and the example pool are separate. Never copy a scored case into a
prompt as a few-shot example.

## Instructions

1. In `src/promptlab/schemas.py`, author `ProcedureSummary`.
   It must carry document version, effective date, superseded status, scope, required analyst
   actions, evidence to gather, deadlines, and an out-of-scope path. Use `Evidence` wherever
   the document can be absent, ambiguous, or contradictory. Set `extra="forbid"`.

2. In `src/promptlab/schemas.py`, implement:

   `schema_description(model: type[BaseModel]) -> str`

   The prompt should use this generated schema description rather than maintaining a second,
   handwritten description of the output shape.

3. Author `src/promptlab/prompts/extract.v1.md` against `PolicyExtraction`.
   Use these sections in order:

   - Task
   - Input
   - Constraints
   - Output
   - When the task cannot be completed

   Delimit the document explicitly. State that document content is data, not instruction.
   Values not present in the document must be represented as absent rather than supplied from
   model knowledge.

4. Create `src/promptlab/prompts/extract.v2.md` by adding an examples section to v1.
   Use exactly two documents from `examples/`. Choose boundary cases that teach useful behavior.
   Do not use anything from `cases/` as an example. Do not edit `extract.v1.md` after recording
   results against it.

5. Implement in `src/promptlab/structured.py`:

   `complete_structured(adapter, request, schema, run_id, max_repairs=1)`

   Flow:

   - call the existing adapter
   - parse `CompletionResult.text`
   - validate against the supplied Pydantic schema
   - if validation fails, send one repair request containing the validation error
   - tell the model to correct only what the validation error concerns
   - validate the repair
   - do not modify `adapters/` or `usage.py`

   Transport retries remain the adapter's responsibility. Schema/content repair belongs here.

6. Under one `run_id`, using one configured Ollama model and temperature `0.0`:

   - run `summarize.v1.md` over all 12 rows in `cases/summarization.jsonl`
   - run `extract.v2.md` over all 12 rows in `cases/extraction.jsonl`

7. Run the leakage check.
   Search extraction outputs for distinctive strings that appear only in the two example
   documents included in `extract.v2.md`. Record the count.

8. Run the citation-existence check.
   For every field returned with `status: present`, confirm that its `section` string occurs in
   the source document. Record the failure count.

9. Write `docs/day3-notes.md` with:

   - summarization repair rate
   - extraction repair rate
   - example leakage count
   - citation-existence failure count
   - two sentences describing the most common validation error and what changed in response

10. Run `pytest`, `ruff check`, and `mypy` until clean.

## Deliverable

A pull request containing student-authored changes plus:

- `ProcedureSummary`
- `schema_description`
- `extract.v1.md`
- `extract.v2.md`
- `structured.py`
- `docs/day3-run.jsonl`
- `docs/day3-notes.md`

The supplied `FieldStatus`, `Evidence`, `PolicyExtraction`, and `summarize.v1.md` are not student
deliverables and should not be rewritten.
