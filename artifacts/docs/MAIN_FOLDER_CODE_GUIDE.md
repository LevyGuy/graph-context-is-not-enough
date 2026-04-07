# Main Folder Code Guide

## Executive Summary

Before using the scripts in this repository, it is useful to keep the current project conclusion in mind:

- the graph pipeline is already showing value for bug localization and semantic diagnosis
- the current bottleneck is downstream repair quality, not only retrieval quality

Two useful status notes:

- [Current Status](/Users/guylevy/Projects/natural-language-index_2/artifacts/docs/CURRENT_STATUS.md)
- [Lessons Learned: Context Vs. Repair](/Users/guylevy/Projects/natural-language-index_2/artifacts/docs/LESSONS_LEARNED_CONTEXT_VS_REPAIR.md)

## Purpose

This document explains the top-level Python scripts in this repository, what each one does, the important functions inside each file, and how to run the experiment end to end.

This guide only covers the code files in the project root:

- `prepare_dataset.py`
- `graph_index_pipeline.py`
- `vector_index_pipeline.py`
- `run_inference.py`
- `generate_report.py`
- `localization_eval.py`
- `graph_exact_patch_pipeline.py`
- `exact_line_replacement_driver.py`
- `single_symbol_patch_driver.py`
- `debug_instance_flow.py`
- `graph_summary_only_experiment.py`
- `graph_summary_with_files_experiment.py`

Shared libraries used by these scripts live under `experiment/`, but that package is not enumerated in full here.

## Repository Workflow

The intended workflow is:

1. Prepare SWE-bench instances and local repo checkouts.
2. Build the graph index and semantic descriptions.
3. Optionally build the vector baseline.
4. Run localization or inference experiments.
5. Generate patches.
6. Run the SWE-bench harness.
7. Generate comparison reports.

There are now also debugging and controlled patching scripts for targeted experiments.

## Core Files

### `prepare_dataset.py`

#### What this file does

Downloads SWE-bench Lite metadata, selects the requested subset of instances, clones each target repository into `artifacts/workspaces/`, checks out the exact `base_commit`, and writes local metadata files used by the rest of the pipeline.

#### Important functions

- `parse_args()`
  - Parses CLI flags such as the metadata subset / output behavior.

- `clone_and_checkout(clone_url, base_commit, target_dir, refresh)`
  - Clones a repository if missing.
  - Optionally refreshes it.
  - Checks out the exact commit required by the SWE-bench instance.

- `main()`
  - Loads dataset rows.
  - Limits the instance set.
  - Clones/checks out repos.
  - Writes metadata under `artifacts/metadata`.

#### When to use it

Use this first whenever you want to bootstrap or refresh local SWE-bench workspaces.

---

### `graph_index_pipeline.py`

#### What this file does

Builds the semantic graph index for each instance. It runs SCIP, extracts Python definitions, enriches symbols with LLM descriptions, and stores the result in SQLite. It also builds the richer structural layers:

- file nodes
- block nodes
- relation edges

This is the main index-building script for the graph approach.

#### Important classes

- `DefinitionRecord`
  - Stored symbol-level unit for a definition/class plus its generated description.

- `FileRecord`
  - File-level node with source, imports, constants, and symbol names.

- `BlockRecord`
  - Intra-file node for assignments, conditionals, loops, returns, try blocks, and constant definition blocks.

- `RelationRecord`
  - Edge between files, symbols, constants, classes, and blocks.

#### Important functions

- `parse_args()`
  - Parses DB path, metadata subset, and structural-only mode.

- `open_database(path)`
  - Creates or opens the SQLite DB.
  - Creates `symbols`, `files`, `blocks`, `relations`, and their FTS tables/triggers.

- `build_scip_index(workspace_dir, index_dir, reuse_existing)`
  - Runs `scip-python` and `scip print --json`.
  - Produces `index.json` per instance.

- `_definition_nodes(source_text)`
  - Parses Python AST and returns top-level definitions.

- `_extract_blocks_from_tree(...)`
  - Builds block nodes from AST statements.

- `extract_file_records(instance_id, repo_name, workspace_dir)`
  - Builds file nodes, block nodes, and relation edges for one workspace.

- `extract_definitions_from_document(...)`
  - Converts SCIP document occurrences into symbol-level definition records.

- `enrich_definition(llm_client, definition)`
  - Sends code to the LLM and gets the short semantic description.

- `upsert_definition(...)`
  - Writes a symbol record into SQLite.

- `upsert_file_record(...)`
  - Writes a file node into SQLite.

- `upsert_block_record(...)`
  - Writes a block node into SQLite.

- `upsert_relation_record(...)`
  - Writes a graph relation into SQLite.

- `symbol_exists(...)`
  - Checks whether a symbol record already exists, used for resume behavior.

- `main()`
  - Orchestrates the full graph indexing pipeline.
  - In `--structural-only` mode, skips SCIP/LLM work and only builds `files`, `blocks`, and `relations`.

#### When to use it

Use this after dataset preparation whenever you want to create or refresh the graph database.

---

### `vector_index_pipeline.py`

#### What this file does

Builds the vector-search baseline. It chunks `.py` files, embeds the chunks, and stores them in Chroma.

#### Important classes

- `CustomEmbeddingFunction`
  - Adapter that lets the vector DB call the configured embedding backend.

#### Important functions

- `parse_args()`
  - Parses collection/output settings.

- `python_files(root)`
  - Enumerates tracked Python files for indexing.

- `main()`
  - Loads prepared workspaces.
  - Chunks Python source.
  - Embeds chunks.
  - Writes vector DB artifacts under `artifacts/vector_db`.

#### When to use it

Use this to build the vector baseline for comparisons against the graph pipeline.

---

### `run_inference.py`

#### What this file does

Runs the main inference pipeline for both graph and vector approaches. It retrieves context, generates graph summaries, produces structured summary JSON, generates patches, validates patches, and writes prediction JSONL files.

This file contains the largest amount of retrieval and patch orchestration logic.

#### Important functions

- `parse_args()`
  - Parses inference-time options.

- `retrieve_graph_context(...)`
  - Retrieves symbol-level graph hits from SQLite FTS.

- `retrieve_graph_file_candidates(...)`
  - Retrieves file/block-level graph candidates from the richer structural index.

- `expand_related_file_candidates(...)`
  - Expands file candidates over graph relations.

- `extract_problem_file_mentions(problem_statement)`
  - Pulls file mentions from the issue text.

- `resolve_problem_file_paths(...)`
  - Resolves issue-mentioned file names to workspace paths.

- `expand_graph_file_context(...)`
  - Converts graph hits into full file context and attached symbol metadata.

- `retrieve_vector_context(...)`
  - Retrieves top vector chunks from Chroma.

- `render_graph_context(items)`
  - Renders symbol-level graph hits as prompt text.

- `render_graph_summary_context(files)`
  - Renders file-level graph context, including symbol descriptions, for summary generation.

- `render_vector_context(items)`
  - Renders retrieved vector chunks as prompt text.

- `write_prompt_artifact(path, content)`
  - Writes prompts, summaries, and patch artifacts to disk for debugging.

- `build_graph_summary_prompt(problem_statement, graph_context)`
  - Builds the prose graph-summary prompt.

- `build_structured_summary_prompt(problem_statement, graph_context)`
  - Builds the structured-summary JSON prompt.

- `build_patch_prompt(...)`
  - Builds the final patch-generation prompt.

- `build_hybrid_graph_vector_context(...)`
  - Combines graph and vector context for hybrid prompt variants.

- `generate_graph_summary(...)`
  - Produces the prose graph summary.

- `generate_structured_summary(...)`
  - Produces the structured JSON summary.

- `generate_patch(...)`
  - Produces a raw patch from the LLM.

- `validate_patch(...)`
  - Uses `git apply --check` against a workspace to validate patch applicability.

- `build_patch_repair_prompt(...)`
  - Builds the retry prompt when a patch is malformed.

- `ensure_valid_patch(...)`
  - Attempts repair loops and fallback synthesis when generated patches fail validation.

- `build_file_rewrite_prompt(...)`
  - Fallback prompt for full-file rewrite generation.

- `synthesize_patch_from_files(...)`
  - Builds a diff from original vs rewritten file contents.

- `merge_allowed_regions(...)`
  - Merges allowed line windows for constrained edits.

- `build_structured_edit_prompt(...)`
  - Asks for bounded JSON edits instead of unconstrained file rewrites.

- `apply_structured_edits(...)`
  - Applies JSON edit payloads locally.

- `synthesize_patch_from_structured_edits(...)`
  - Converts structured edits into a unified diff.

- `main()`
  - Runs inference for the configured instance set.
  - Writes `predictions_graph.jsonl` and `predictions_vector.jsonl`.

#### When to use it

Use this for the main benchmark-style inference flow.

---

### `generate_report.py`

#### What this file does

Reads evaluation outputs and context metrics, then generates a Markdown summary comparing pipelines.

#### Important functions

- `parse_args()`
  - Parses paths to evaluation outputs and metrics.

- `load_jsonl(path)`
  - Reads JSONL metrics files.

- `find_summary_payload(eval_dir)`
  - Finds the summary JSON produced by the SWE-bench harness.

- `extract_pass_at_1(payload)`
  - Pulls resolved/submitted counts from the evaluation summary.

- `summarize_metrics(rows)`
  - Computes aggregate token/context statistics.

- `render_report(...)`
  - Produces the Markdown comparison report.

- `main()`
  - Ties everything together and writes the report.

#### When to use it

Use this after running SWE-bench to produce a human-readable comparison.

---

### `localization_eval.py`

#### What this file does

Runs the localization benchmark. It measures whether the graph pipeline identifies the right file, region, and repair mechanism before patch generation.

This is the main diagnostic benchmark for retrieval and grounding quality.

#### Important classes

- `GoldFile`
  - Parsed representation of a gold patch file plus changed hunks.

#### Important functions

- `parse_args()`
  - Parses benchmark input/output paths.

- `parse_patch_gold(patch_text)`
  - Converts the gold SWE-bench patch into per-file changed hunks.

- `load_dataset_patch_map(...)`
  - Loads gold patch structure from the dataset.

- `load_dataset_raw_patch_texts(...)`
  - Loads the raw gold patch text.

- `build_report(results, limit)`
  - Renders the localization benchmark report.

- `contains_gold_file_reference(graph_summary, gold_files)`
  - Checks whether the summary explicitly mentions a gold file.

- `line_in_hunks(line_number, hunks)`
  - Checks whether a chosen line falls inside the gold patch hunks.

- `build_semantic_judge_prompt(...)`
  - Builds the LLM judge prompt for semantic localization.

- `judge_semantic_localization(...)`
  - Judges file/function/fix-mechanism quality of the localization result.

- `main()`
  - Runs localization eval for the selected instances.
  - Writes per-instance logs and aggregate reports.

#### When to use it

Use this when you want to debug retrieval/localization quality separately from repair success.

---

### `graph_exact_patch_pipeline.py`

#### What this file does

Runs the graph-only exact-target repair pipeline. It uses graph context and summaries to choose a single exact line or assignment RHS to patch, then synthesizes a clean diff locally.

This is the main controlled patch-shape pipeline for graph-only repair experiments.

#### Important functions

- `parse_args()`
  - Parses instance selection and output options.

- `build_target_selection_prompt(...)`
  - Builds the exact-target selection prompt.

- `choose_target(...)`
  - Asks the LLM for `path`, `line_number`, and `mode`.

- `extract_identifiers(...)`
  - Utility for lexical token scoring.

- `extract_summary_code_lines(...)`
  - Pulls code-like lines out of the prose summary.

- `extract_file_paths(...)`
  - Pulls `.py` paths from text.

- `extract_likely_bug_section(...)`
  - Extracts the “Likely Bug Location” section from the summary.

- `extract_summary_symbol_names(...)`
  - Pulls function/class-like names from the summary.

- `extract_constant_names(...)`
  - Pulls constant-like tokens from text.

- `extract_dotted_module_candidates(...)`
  - Pulls dotted module references like `ascii.rst`.

- `resolve_dotted_module_paths(...)`
  - Resolves dotted module references into workspace file paths.

- `resolve_summary_file_paths(...)`
  - Matches summary file mentions against available file items.

- `dedupe_preserve_order(...)`
  - Deduplicates while preserving order.

- `resolve_file_mentions_to_workspace(...)`
  - Normalizes structured summary file references against the workspace.

- `normalize_structured_summary(...)`
  - Produces the normalized structured-summary JSON used by the selector.

- `build_constrained_candidate_pools(...)`
  - Builds deterministic candidate pools from normalized summary fields.

- `select_target_deterministic(...)`
  - Applies structured-summary-first deterministic rules before broad fallback.

- `find_constant_definition_file(...)`
  - Finds likely constant definition files.

- `build_symbol_ranges(...)`
  - Maps summary-named symbols to line ranges.

- `line_in_ranges(...)`
  - Tests whether a line falls within summary-targeted symbol ranges.

- `choose_target_heuristic(...)`
  - Heuristic exact-target selector used before LLM target selection.

- `search_workspace_candidate_files(...)`
  - Broader file search fallback over the local workspace.

- `infer_rhs_from_summary(...)`
  - Tries to derive the corrected assignment RHS directly from the graph summary.

- `infer_rhs_heuristic(...)`
  - Hardcoded small-fix heuristics for known repair shapes.

- `infer_line_heuristic(...)`
  - Hardcoded one-line fix heuristics.

- `main()`
  - Runs the exact graph patch pipeline for one instance.
  - Produces target selection logs, replacement payloads, and final diffs.

#### When to use it

Use this when you want a tightly controlled graph-only repair attempt rather than a free-form patch.

---

### `exact_line_replacement_driver.py`

#### What this file does

Provides the reusable building blocks for exact one-line or assignment-RHS patch generation and validation.

This is the low-level driver used by the exact graph pipeline and related experiments.

#### Important functions

- `parse_args()`
  - Parses target file/line and patch mode.

- `load_rows(...)`
  - Loads metadata rows.

- `resolve_summary_path(...)`
  - Locates the graph summary for the instance.

- `build_line_prompt(...)`
  - Prompt for replacing an entire line.

- `build_assignment_rhs_prompt(...)`
  - Prompt for replacing only the right-hand side of an assignment.

- `render_excerpt(...)`
  - Builds a local context excerpt around the target line.

- `build_patch_for_line(...)`
  - Synthesizes a unified diff from one changed line.

- `rebuild_assignment_line(...)`
  - Reassembles the full assignment line from an edited RHS.

- `validate_python_patch(...)`
  - Applies the diff in a temp copy and compiles the target file.

- `generate_replacement_line(...)`
  - Calls the LLM for a full-line replacement.

- `generate_replacement_rhs(...)`
  - Calls the LLM for a RHS-only replacement.

- `main()`
  - Runs the single-line patch flow end to end.

#### When to use it

Use this for isolated exact-line experiments or debugging one-file repair behavior.

---

### `single_symbol_patch_driver.py`

#### What this file does

Runs a bounded patch experiment on one symbol or one selected line window. It is a smaller targeted experiment driver used to validate line-bounded graph patches.

#### Important functions

- `parse_args()`
  - Parses instance, file, and line-window arguments.

- `load_rows(...)`
  - Loads metadata rows.

- `resolve_summary_path(...)`
  - Finds the graph summary to use.

- `render_context(file_item, start_line, end_line)`
  - Renders the exact file window used for the patch prompt.

- `validate_python_patch(...)`
  - Applies the patch in temp space and compiles the file.

- `main()`
  - Runs the one-symbol bounded patch experiment.

#### When to use it

Use this when the target file and line window are already known and you want to test a bounded patch strategy.

---

### `debug_instance_flow.py`

#### What this file does

Materializes a single instance end to end for debugging. It writes every intermediate artifact:

- problem statement
- graph symbol context
- graph file context
- summary prompts
- summaries
- vector context
- patch prompts
- returned patches

#### Important functions

- `parse_args()`
  - Parses instance selection and output location.

- `load_rows(...)`
  - Loads metadata rows.

- `write_text(path, text)`
  - Helper to write debug artifacts.

- `main()`
  - Executes the debug flow and writes all intermediate files for one instance.

#### When to use it

Use this whenever you want full visibility into what the system retrieved, summarized, and sent to the model.

---

### `graph_summary_only_experiment.py`

#### What this file does

Runs an experiment where the patch prompt uses only the graph summary, without vector context.

#### Important functions

- `parse_args()`
  - Parses instance and output arguments.

- `load_rows(...)`
  - Loads metadata rows.

- `resolve_summary_path(...)`
  - Finds the summary for the instance.

- `main()`
  - Runs the summary-only patch experiment.

#### When to use it

Use this to isolate whether the graph summary alone is enough for diagnosis or repair.

---

### `graph_summary_with_files_experiment.py`

#### What this file does

Runs an experiment where the patch prompt uses:

- the graph summary
- the actual target file contents

but not vector retrieval.

#### Important functions

- `parse_args()`
  - Parses instance and file selection arguments.

- `load_rows(...)`
  - Loads metadata rows.

- `resolve_summary_path(...)`
  - Finds the graph summary.

- `render_target_file_context(file_items)`
  - Renders full target file context.

- `select_target_files(...)`
  - Chooses which files to include for the summary-with-files experiment.

- `main()`
  - Runs the experiment and writes prompts/results.

#### When to use it

Use this to test whether graph diagnosis plus raw target file contents are sufficient without vector search.

## How To Run The Code

### Environment

Requirements:

- Docker Desktop running
- OpenAI API key in `.env`
- project venv in `.venv`
- SCIP tools installed

The code already prefers the local `.env` over shell variables.

### 1. Prepare the dataset and workspaces

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python /Users/guylevy/Projects/natural-language-index_2/prepare_dataset.py
```

Outputs:

- `artifacts/workspaces/`
- `artifacts/metadata/instances.jsonl`

### 2. Build the graph index

Full graph build:

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python /Users/guylevy/Projects/natural-language-index_2/graph_index_pipeline.py
```

Structural-only refresh:

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python /Users/guylevy/Projects/natural-language-index_2/graph_index_pipeline.py --structural-only
```

Key outputs:

- `artifacts/enriched_graph.db`
- `artifacts/indexes/<instance_id>/`

### 3. Build the vector baseline

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python /Users/guylevy/Projects/natural-language-index_2/vector_index_pipeline.py
```

Output:

- `artifacts/vector_db/`

### 4. Run the main inference benchmark

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python /Users/guylevy/Projects/natural-language-index_2/run_inference.py
```

Outputs:

- `predictions_graph.jsonl`
- `predictions_vector.jsonl`

Prompt/debug artifacts are written under:

- `artifacts/logs/prompts/`

### 5. Run localization evaluation

```bash
DOCKER_HOST=unix:///Users/guylevy/.docker/run/docker.sock \
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python \
/Users/guylevy/Projects/natural-language-index_2/localization_eval.py
```

Outputs:

- `artifacts/reports/localization_eval_10.md`
- `artifacts/reports/localization_eval_10.json`
- `artifacts/logs/localization_eval/`

### 6. Run graph-only exact patching for one instance

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python \
/Users/guylevy/Projects/natural-language-index_2/graph_exact_patch_pipeline.py \
  --instance-id astropy__astropy-12907
```

Outputs:

- `artifacts/logs/prompts/<instance_id>/...`

### 7. Run the official SWE-bench harness

Graph:

```bash
DOCKER_HOST=unix:///Users/guylevy/.docker/run/docker.sock \
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python \
  -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path /Users/guylevy/Projects/natural-language-index_2/predictions_graph.jsonl \
  --max_workers 4 \
  --run_id graph_eval
```

Vector:

```bash
DOCKER_HOST=unix:///Users/guylevy/.docker/run/docker.sock \
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python \
  -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path /Users/guylevy/Projects/natural-language-index_2/predictions_vector.jsonl \
  --max_workers 4 \
  --run_id vector_eval
```

Evaluation logs are stored under:

- `artifacts/logs/run_evaluation/`

### 8. Generate a final report

```bash
/Users/guylevy/Projects/natural-language-index_2/.venv/bin/python \
/Users/guylevy/Projects/natural-language-index_2/generate_report.py
```

Outputs:

- Markdown report under `artifacts/reports/`

## Debugging Guidance

### If graph summaries look good but target selection drifts

Inspect:

- `graph_summary.md`
- `graph_summary.json`
- `structured_summary_normalized.json`
- `selector_candidate_pools.json`
- `selector_decision.json`

These are written under:

- `artifacts/logs/prompts/<instance_id>/...`
- `artifacts/logs/localization_eval/<instance_id>/...`

### If a patch is valid locally but fails in SWE-bench

Check whether the local workspace is dirty relative to the base commit.

This matters because:

- local diff synthesis can accidentally use already-modified files
- SWE-bench evaluates against the pristine repo state

### If localization metrics and SWE-bench disagree

Use both:

- `localization_eval.py` for understanding retrieval/grounding quality
- the SWE-bench harness for true repair success

The localization benchmark is diagnostic, not the final truth metric.

## Recommended Entry Points

If you are trying to understand the codebase quickly, start here:

1. `prepare_dataset.py`
2. `graph_index_pipeline.py`
3. `run_inference.py`
4. `localization_eval.py`
5. `graph_exact_patch_pipeline.py`

That gives the cleanest top-down understanding of:

- data preparation
- graph construction
- retrieval and prompting
- localization measurement
- controlled patch generation
