# Instrumented Runtime Workflow v4 Checklist

## Goal

Implement the instrumented-runtime debugging workflow described in:
- [INSTRUMENTED_RUNTIME_WORKFLOW_V4_PLAN.md](/Users/guylevy/Projects/natural-language-index_2/INSTRUMENTED_RUNTIME_WORKFLOW_V4_PLAN.md)

This checklist translates the plan into concrete engineering tasks, organized by file/module.

## Phase 1: Runtime v3 Cleanup

### [runtime_repro.py](/Users/guylevy/Projects/natural-language-index_2/runtime_repro.py)
- [ ] tighten repro command inference so framework-specific commands beat weak test-file probes
- [ ] add command blacklist / safety guardrails for obviously bad test-target commands
- [ ] improve traceback extraction beyond Python `File "...", line ...` patterns
- [ ] classify shell/environment bootstrap failures separately from app/runtime failures
- [ ] add a stronger `useful_signal` rule that requires:
  - traceback frames, or
  - strong exception type, or
  - clear runtime markers

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] keep runtime v3 reporting intact as the baseline runtime stage
- [ ] ensure `runtime_augmented_result` remains optional and never overwrites static data silently
- [ ] preserve `final_mode` semantics so v4 can add `instrumented_runtime`

## Phase 2: Add Instrumentation Planning

### New file: [instrumented_runtime.py](/Users/guylevy/Projects/natural-language-index_2/instrumented_runtime.py)
- [ ] add `instrumentation_gate(...)`
- [ ] add `plan_instrumentation(...)`
- [ ] add `build_instrumentation_patch(...)`
- [ ] add `apply_instrumentation_patch(...)`
- [ ] add `revert_instrumentation_patch(...)`
- [ ] add `parse_instrumentation_logs(...)`
- [ ] add `build_instrumentation_evidence(...)`

Required helper responsibilities:
- [ ] choose max 3 files by default
- [ ] choose max 6 instrumentation points by default
- [ ] assign stable `NLI_TRACE|...` marker format
- [ ] prefer function-entry / branch / config-value / return-site tracing
- [ ] keep instrumentation patch reversible

## Phase 3: Add Instrumentation Gate

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] add CLI flags:
  - [ ] `--enable-instrumented-runtime`
  - [ ] `--instrumentation-max-files`
  - [ ] `--instrumentation-max-points`
  - [ ] `--instrumentation-timeout-seconds`
  - [ ] `--instrumentation-only-on-failures`
  - [ ] `--instrumentation-gate-threshold`
- [ ] run instrumentation gate after static result and after plain runtime result is known
- [ ] write `instrumentation_gate.json`
- [ ] skip instrumentation when:
  - [ ] static localization already succeeded strongly
  - [ ] no safe repro command exists
  - [ ] no bounded candidate file shortlist exists

## Phase 4: Generate Instrumentation Patches

### New file: [instrumented_runtime.py](/Users/guylevy/Projects/natural-language-index_2/instrumented_runtime.py)
- [ ] implement minimal patch generation for Python files
- [ ] support these log insertion patterns:
  - [ ] function entry
  - [ ] branch decision
  - [ ] config lookup
  - [ ] return value summary
- [ ] avoid:
  - [ ] broad file rewrites
  - [ ] logging inside large loops unless specifically bounded
  - [ ] dumping huge object reprs

### Patch lifecycle requirements
- [ ] write `instrumentation_patch.diff`
- [ ] write `instrumentation_patch_plan.json`
- [ ] apply with `git apply`
- [ ] revert with `git apply -R`
- [ ] stop and record failure if revert fails

## Phase 5: Add Instrumented Execution

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] reuse runtime command inference for instrumented runs
- [ ] allow instrumented run to choose:
  - [ ] explicit issue command first
  - [ ] framework-safe command second
  - [ ] narrow test target only when clearly relevant
- [ ] write:
  - [ ] `instrumented_runtime_command.json`
  - [ ] `instrumented_runtime_stdout.txt`
  - [ ] `instrumented_runtime_stderr.txt`
  - [ ] `instrumentation_apply.json`
  - [ ] `instrumentation_revert.json`

## Phase 6: Parse Instrumentation Logs

### New file: [instrumented_runtime.py](/Users/guylevy/Projects/natural-language-index_2/instrumented_runtime.py)
- [ ] parse `NLI_TRACE|...` lines into structured rows
- [ ] extract:
  - [ ] reached files
  - [ ] reached symbols
  - [ ] branch decisions
  - [ ] config/value observations
  - [ ] deepest successful runtime path
- [ ] write `instrumentation_evidence.json`

### Evidence buckets to produce
- [ ] `instrumentation_file_evidence`
- [ ] `instrumentation_symbol_evidence`
- [ ] `instrumentation_branch_evidence`
- [ ] `instrumentation_value_evidence`

## Phase 7: Feed Instrumentation Evidence Back Into Ranking

### [developer_workflow.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow.py)
- [ ] extend `merge_candidates(...)` with instrumentation evidence inputs
- [ ] add component buckets:
  - [ ] `instrumentation_file`
  - [ ] `instrumentation_symbol`
  - [ ] `instrumentation_branch`
  - [ ] `instrumentation_value`
- [ ] add ranking weights so instrumented execution evidence beats static-only adjacency
- [ ] render instrumentation evidence in `_summarize_candidate_by_source(...)`
- [ ] include instrumentation evidence sections in `render_evidence_packet(...)`

## Phase 8: Instrumentation-Aware Selection

### [developer_workflow.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow.py)
- [ ] extend file comparison to show instrumentation evidence explicitly
- [ ] add instrumentation-aware file preference logic
- [ ] extend region selection priority to:
  1. [ ] exact instrumented symbol/block
  2. [ ] exact instrumented line
  3. [ ] runtime traceback frame
  4. [ ] structured fix mechanism
  5. [ ] existing static fallback
- [ ] keep region selection bounded to the selected file

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] write:
  - [ ] `instrumentation_file_comparison.json`
  - [ ] `instrumentation_region_selection.json`
- [ ] record `instrumented_runtime_result`
- [ ] add `final_mode = instrumented_runtime` when chosen

## Phase 9: Reporting

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] add instrumentation metrics:
  - [ ] `instrumentation_attempted_count`
  - [ ] `instrumentation_patch_applied_count`
  - [ ] `instrumentation_patch_reverted_count`
  - [ ] `instrumentation_produced_useful_signal_count`
  - [ ] `instrumentation_changed_selected_file_count`
  - [ ] `instrumentation_changed_selected_region_count`
  - [ ] `instrumentation_improved_semantic_localization_count`
  - [ ] `instrumentation_regressed_semantic_localization_count`
- [ ] add instrumentation taxonomy
- [ ] compare:
  - [ ] static-only
  - [ ] plain runtime
  - [ ] instrumented runtime
  - [ ] final chosen mode

## Phase 10: Safety and Reversion

### [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)
- [ ] verify instrumented files are clean after revert
- [ ] if revert fails:
  - [ ] record `instrumentation_revert_failed`
  - [ ] stop further execution for that instance
- [ ] never use:
  - [ ] `git reset --hard`
  - [ ] `git checkout --`

## Phase 11: Validation

### Compile / static validation
- [ ] `py_compile` on:
  - [ ] `instrumented_runtime.py`
  - [ ] `developer_workflow.py`
  - [ ] `developer_workflow_localization.py`

### Smoke tests
- [ ] one Django autoreload issue
- [ ] one management command issue
- [ ] one non-Django runtime-sensitive issue

For each smoke test verify:
- [ ] patch applied
- [ ] logs emitted
- [ ] logs parsed
- [ ] patch reverted
- [ ] no leftover worktree mutation

### Failed-tail benchmark
- [ ] run only on current runtime-sensitive / failed-tail subset first
- [ ] compare against:
  - [ ] static-only v2 region baseline
  - [ ] plain runtime v3

Promotion criteria:
- [ ] instrumentation produces useful signal on a meaningful fraction of attempted cases
- [ ] revert succeeds reliably
- [ ] semantic localization improves on failed-tail subset
- [ ] no persistent file mutation remains

## Suggested Implementation Order

1. [ ] create `instrumented_runtime.py` with gate + plan + patch lifecycle skeleton
2. [ ] implement log marker format and parser
3. [ ] implement minimal Python instrumentation patch generator
4. [ ] integrate apply/run/revert into `developer_workflow_localization.py`
5. [ ] feed instrumentation evidence into `developer_workflow.py`
6. [ ] add reporting and taxonomy
7. [ ] run smoke tests
8. [ ] run failed-tail benchmark

## Definition Of Done

The v4 implementation is done when all of these are true:
- [ ] instrumentation can be gated selectively
- [ ] a temporary patch is generated, applied, and reverted safely
- [ ] runtime logs are parsed into structured evidence
- [ ] instrumentation evidence influences file/region selection
- [ ] per-instance and aggregate reports include instrumentation metrics
- [ ] smoke tests pass
- [ ] failed-tail benchmark shows meaningful improvement or gives a clear negative result
