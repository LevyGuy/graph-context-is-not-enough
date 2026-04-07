## Goal

Measure whether the graph pipeline improves bug localization on SWE-bench Lite before using resolved/unresolved as the main benchmark signal.

## Why This Evaluation Exists

Resolved-rate is still noisy for this project because patch synthesis quality can hide or distort retrieval quality.

We therefore split evaluation into two layers:

1. Localization benchmark
2. Repair benchmark

The immediate next step is the localization benchmark on the first 10 already-indexed instances.

## Scope For This Run

- Dataset slice: first 10 instances already prepared in `/Users/guylevy/Projects/natural-language-index_2/artifacts/metadata/instances.jsonl`
- Retrieval mode: graph only
- Vector search: excluded
- Summary model: current graph summary model from `.env` / settings
- Outputs:
  - per-instance localization artifacts
  - machine-readable JSON summary
  - Markdown report

## Gold Labels

For this benchmark, the weak gold labels come from the official SWE-bench `patch` field:

- gold files = files modified by the gold patch
- gold line regions = hunk line ranges from the gold patch

This is not perfect, but it is a reasonable first localization target and lets us evaluate 10 instances quickly without hand-labeling.

## Metrics

For each instance:

- `gold_file_count`
- `retrieved_top1_file_match`
- `retrieved_top3_file_match`
- `retrieved_top5_file_match`
- `summary_mentions_gold_file`
- `target_in_gold_file`
- `target_line_within_gold_hunk`

Aggregate metrics:

- Top-1 file hit rate
- Top-3 file hit rate
- Top-5 file hit rate
- Summary mentions gold file rate
- Final selected target file hit rate
- Final selected target line-in-gold-hunk rate

## Graph Pipeline Frozen For This Benchmark

The localization benchmark uses the current graph-only stack:

1. Retrieve top graph symbols from SQLite FTS
2. Expand to file-level context
3. Inject issue-mentioned files even if symbol coverage is sparse
4. Generate graph summary
5. Select final exact target using current heuristic/LLM logic

No vector chunks are used in this benchmark.

## Execution Plan

1. Build a dedicated `localization_eval.py` script.
2. Run it on the 10 prepared instances.
3. Save per-instance artifacts for debugging.
4. Save a JSON results file.
5. Save a Markdown report with aggregate metrics and per-instance outcomes.
6. Review whether the graph is strong enough on localization to justify a 20-instance run.

## Decision Rule After 10 Instances

After the first 10:

- If graph Top-3 file hit rate looks strong and target-file accuracy is decent, expand to 20.
- If graph retrieval misses too many gold files, fix retrieval before running more repair experiments.
- If graph retrieval is strong but target-line accuracy is weak, focus next on target selection and patch-shape control.

## Expected Artifacts

- `/Users/guylevy/Projects/natural-language-index_2/localization_eval.py`
- `/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_eval_10.md`
- `/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_eval_10.json`
- `/Users/guylevy/Projects/natural-language-index_2/artifacts/logs/localization_eval/<instance_id>/...`
