# Deterministic Selector Plan

## Goal

Keep the current high semantic understanding from the graph pipeline, but stop losing that signal during target selection.

The selector should treat the structured summary JSON as authoritative guidance, not as another fuzzy input mixed back into broad retrieval.

## Problem

Current flow:

1. Retrieve graph context.
2. Generate prose summary and structured summary JSON.
3. Mix structured summary back into broad candidate search.
4. Let heuristics/ranking choose a file and line.

This causes drift:

- the summary often identifies the right bug mechanism
- sometimes even the right file or symbol
- but the later selector still chooses a different file or region

## Desired Flow

New deterministic flow:

1. Generate structured summary JSON.
2. Normalize and validate the JSON fields.
3. Apply hard or semi-hard selector rules in order.
4. Only if those rules fail, fall back to broad retrieval.

This makes the selector obey the diagnosis instead of re-deciding everything.

## Structured Summary Fields To Trust

Primary fields:

- `likely_bug_files`
- `likely_symbols`
- `implementation_files`
- `constant_names`
- `suspicious_line_patterns`
- `fix_mechanism`
- `confidence`

Secondary fields:

- `entrypoint_files`
- `issue_shape`

## Deterministic Selection Rules

### Rule 1: Single likely bug file

If `likely_bug_files` contains exactly one valid repo path:

- restrict target selection to that file first
- do not allow other files to outrank it
- search symbols, blocks, and suspicious lines only inside that file

Fallback:

- if no usable line or symbol candidate is found in that file, expand to `implementation_files`

### Rule 2: Multiple likely bug files

If `likely_bug_files` contains multiple valid repo paths:

- restrict phase-1 selection to only those files
- rank files by:
  - symbol match
  - constant match
  - suspicious line pattern match
  - block match

Fallback:

- if all fail, expand to `implementation_files`

### Rule 3: Named symbol constraint

If `likely_symbols` is non-empty:

- search the named symbol definitions first
- then search blocks contained by that symbol
- then search nearby line windows around the symbol

This should happen before any full-file fuzzy scoring.

### Rule 4: Constant-definition constraint

If `constant_names` is non-empty:

- search definition sites for those constants first
- prioritize files where the constant is top-level
- prefer config/definition files over runtime/caller files

### Rule 5: Implementation file priority

If `implementation_files` is non-empty:

- use them as the second-tier candidate pool
- only after exhausting `likely_bug_files`
- never let `entrypoint_files` outrank `implementation_files`

### Rule 6: Entry points are context, not edit targets

If `entrypoint_files` is non-empty:

- keep them available for explanation/debugging
- but penalize them for edit targeting unless no implementation file exists

This directly addresses cases like:

- `ui.py` vs `rst.py`
- generic framework caller vs leaf implementation

### Rule 7: Broad retrieval only as fallback

Only if Rules 1-6 fail:

- run broad file/block/relation retrieval
- allow general heuristic ranking

This ensures the structured summary remains the primary guide.

## Proposed Selector Pipeline

### Phase A: Normalize structured summary

Create a helper:

- `normalize_structured_summary(summary_json, workspace_dir) -> normalized_summary`

Responsibilities:

- deduplicate paths
- resolve dotted module mentions to file paths
- discard invalid paths
- canonicalize symbol names
- cap confidence to `[0, 1]`

### Phase B: Build constrained candidate pools

Create a helper:

- `build_constrained_candidate_pools(normalized_summary, db, workspace_dir)`

Output:

- `primary_files`
- `secondary_files`
- `entrypoint_files`
- `symbol_candidates`
- `block_candidates`
- `line_candidates`

### Phase C: Deterministic target search

Create a helper:

- `select_target_deterministic(...)`

Order:

1. named symbol spans in primary files
2. suspicious blocks in primary files
3. suspicious lines in primary files
4. named symbol spans in secondary files
5. suspicious blocks in secondary files
6. constant definition sites
7. fallback broad retrieval

### Phase D: Fallback search

Only if deterministic search yields no viable target:

- call existing broader retrieval/ranking logic

## Required Code Changes

### 1. `run_inference.py`

Add:

- summary normalization helper
- deterministic candidate pool builder
- deterministic target selector

Update:

- graph path to use deterministic selector before any broad retrieval

### 2. `graph_exact_patch_pipeline.py`

Update:

- consume normalized structured summary
- use deterministic target selection
- only use broad candidate search as fallback

### 3. `localization_eval.py`

Update:

- benchmark the deterministic selector, not the old mixed selector
- log which rule produced the final target

New fields in result JSON:

- `selector_mode`
- `selector_rule`
- `used_fallback`

### 4. Logging

For each instance, write:

- `structured_summary_normalized.json`
- `selector_candidate_pools.json`
- `selector_decision.json`

This is required for debugging drift.

## Success Criteria

We want to preserve:

- high `semantic_correct_fix_mechanism`

We want to improve:

- `semantic_correct_file`
- `target_in_gold_file`
- `target_line_within_gold_hunk`

Expected benchmark outcome:

- semantic metrics stay roughly stable
- file grounding improves
- exact-hunk localization improves modestly

## Rollout Plan

### Step 1

Implement normalization + logging only.

### Step 2

Implement deterministic file constraints:

- Rule 1
- Rule 2
- Rule 5
- Rule 6

Rerun 10-instance benchmark.

### Step 3

Implement symbol and constant constraints:

- Rule 3
- Rule 4

Rerun 10-instance benchmark.

### Step 4

Keep broad retrieval only as explicit fallback:

- Rule 7

Rerun 10-instance benchmark and compare against current results.

## Recommendation

Do not change patch generation yet.

The next clean experiment is:

1. keep current graph summary generation
2. replace target selection with deterministic structured-summary-first selection
3. rerun the 10-instance localization benchmark

Only after that should we revisit repair generation.
