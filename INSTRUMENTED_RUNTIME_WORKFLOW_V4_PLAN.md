# Developer Workflow v4: Instrumented Runtime Tracing

## Summary

Add an **instrumented debugging stage** on top of the current static workflow. Instead of relying only on guessed repro commands and hoping for a traceback, the system should temporarily modify a small number of likely files to emit targeted logs, run a narrow repro command, parse the resulting logs, and then revert the instrumentation.

This is meant to approximate a real developer workflow:
- inspect likely code locations
- add temporary debug logging
- run the code
- observe the real path/state
- remove the temporary instrumentation

This stage should be:
- selective
- minimal
- reversible
- bounded to a small candidate set

It should **not** become broad patch generation.
It should only generate **temporary instrumentation patches**.

## High-Level Workflow

The workflow becomes:

1. run the current best static workflow
2. if static confidence is weak or issue shape is runtime-sensitive, trigger instrumentation gate
3. choose a small candidate shortlist of files/regions to instrument
4. generate a temporary instrumentation patch
5. apply the patch
6. run the repro command
7. collect emitted debug logs
8. parse logs into structured evidence
9. rerank / reselect file and region
10. revert the instrumentation patch
11. report:
   - static result
   - instrumented-runtime result
   - whether instrumentation improved localization

## Goals

This stage is specifically designed to improve cases where:
- the right file is not obvious statically
- several adjacent files are plausible
- the issue depends on dynamic lifecycle flow
- the issue depends on runtime values/config/state
- a normal traceback is unavailable or insufficient

Examples of target issue shapes:
- autoreload / dev server
- template rendering / settings lookup
- management command execution
- request/response path
- ORM/query compilation path
- serializer/validation runtime behavior
- dynamic imports / plugin registration

## Implementation Changes

### 1. Add an instrumentation gate

Create a deterministic gate after static localization and before runtime execution.

Inputs:
- static result
- issue text
- failure taxonomy
- issue shape
- file-selection confidence
- candidate ambiguity

Trigger instrumentation when any of these are true:
- failure taxonomy is:
  - `deterministic candidate discovery missed correct file`
  - `comparison preferred wrong file despite good evidence`
  - `issue likely requires runtime execution/reproduction`
  - `file chosen correctly but region selection missed`
- issue text suggests runtime-only behavior:
  - autoreload
  - request handling
  - template rendering
  - management command behavior
  - dynamic configuration
  - warnings/errors only at runtime
- top candidate files are semantically adjacent and difficult to distinguish statically
- repro command exists or can likely be inferred safely

Do not trigger when:
- static localization already succeeded with high confidence
- no safe repro command can be inferred
- no plausible instrumentation site can be bounded

Output:
- `instrumentation_gate.json`
  - `should_run`
  - `reasons`
  - `candidate_files`
  - `candidate_regions`
  - `expected_value`

### 2. Add instrumentation planning

Create a planner that converts candidate files/regions into a minimal instrumentation plan.

New module:
- `instrumented_runtime.py`

Planner responsibilities:
- choose at most `N` files to instrument, default `N=3`
- choose at most `M` instrumentation points total, default `M=6`
- prefer:
  - selected file
  - top file-comparison runner-up files
  - files with strong workflow/implementation evidence
  - exact matched symbols/blocks
- decide what to log:
  - entry into function/method
  - key branch decision
  - key variable/config values
  - chosen backend/path
  - returned/derived type/value summary
- assign a unique marker prefix for parsing, e.g.:
  - `NLI_TRACE|...`

Instrumentation points should remain generic:
- function entry
- before/after conditionals
- before return
- around key config access
- around dispatch choice

Avoid:
- many logs per file
- full object dumps
- noisy loops
- high-volume hot paths unless bounded

Output:
- `instrumentation_plan.json`

### 3. Generate a temporary instrumentation patch

Add a patch generator specifically for debug logging.

Requirements:
- patch only the selected candidate files
- patch must be minimal
- patch must be reversible
- patch must preserve syntax
- patch must avoid semantic changes except adding logging

Instrumentation style:
- prefer `print(...)` or lightweight logging only if safe and already appropriate in repo context
- emitted log format must be structured enough to parse, e.g.:
  - `NLI_TRACE|file=<...>|symbol=<...>|event=<...>|value=<...>`
- include:
  - file
  - symbol or block label
  - event type
  - selected values if relevant

Examples of event types:
- `enter`
- `branch_taken`
- `config_value`
- `dispatch_target`
- `returning`
- `exception_path`

Output:
- `instrumentation_patch.diff`
- `instrumentation_patch_plan.json`

### 4. Safely apply and revert instrumentation

Never use destructive git commands.

Use this exact patch lifecycle:
1. write instrumentation patch to disk
2. apply with `git apply`
3. run repro
4. revert with `git apply -R`
5. verify revert succeeded

Important constraints:
- preserve user changes in dirty worktrees
- only revert the exact instrumentation patch we added
- if revert fails, stop and record failure instead of trying destructive cleanup

Artifacts:
- `instrumentation_apply.json`
- `instrumentation_revert.json`

### 5. Repro command selection for instrumented runs

Reuse runtime command inference, but strengthen it for instrumented runs.

Command priority:
1. explicit repro command from issue
2. issue-specific command snippet
3. narrow management command / app startup command for framework issues
4. narrow test target when clearly relevant
5. skip if no safe repro exists

Because instrumentation is expensive and invasive:
- only run one repro command per instance by default
- allow one retry if command clearly failed for unrelated shell/env reasons
- do not run broad full test suites

Artifacts:
- `instrumented_runtime_command.json`
- `instrumented_runtime_stdout.txt`
- `instrumented_runtime_stderr.txt`

### 6. Parse emitted logs into structured evidence

Build a parser for the structured log marker output.

Extract:
- which instrumented files were actually reached
- which symbol/block was reached
- which branch was taken
- key config/value observations
- the last successful instrumented point before failure/stall
- the highest-frequency or deepest path markers

Output evidence channels:
- `instrumentation_file_evidence`
- `instrumentation_symbol_evidence`
- `instrumentation_branch_evidence`
- `instrumentation_value_evidence`

Artifact:
- `instrumentation_evidence.json`

This evidence should be stronger than normal grep/example evidence, because it reflects actual execution.

### 7. Add instrumentation-aware reranking and selection

Feed instrumentation evidence back into the workflow.

New behavior:
- files actually reached at runtime outrank static-only neighbors
- files showing the decisive branch/config/value outrank mere entrypoint files
- if the selected file is confirmed at runtime, region selection should focus on the exact logged symbol/block
- if runtime shows a runner-up file is the true implementation path, allow file selection to switch

Priority inside selected file:
1. exact instrumented symbol/block
2. exact logged line or nearest enclosing symbol
3. runtime traceback frame if available
4. static structured fix mechanism
5. previous static rules

Artifacts:
- `instrumentation_file_comparison.json`
- `instrumentation_region_selection.json`

### 8. Artifact model

Per-instance artifacts to add:

- `instrumentation_gate.json`
- `instrumentation_plan.json`
- `instrumentation_patch.diff`
- `instrumentation_patch_plan.json`
- `instrumentation_apply.json`
- `instrumentation_revert.json`
- `instrumented_runtime_command.json`
- `instrumented_runtime_stdout.txt`
- `instrumented_runtime_stderr.txt`
- `instrumentation_evidence.json`
- `instrumentation_file_comparison.json`
- `instrumentation_region_selection.json`

Final per-instance result should include:
- `static_result`
- `runtime_result` if plain runtime was attempted
- `instrumented_runtime_result` if instrumentation was attempted
- `final_mode`:
  - `static`
  - `runtime_augmented`
  - `instrumented_runtime`

### 9. Metrics and reporting

Add instrumentation-specific metrics:

- `instrumentation_attempted_count`
- `instrumentation_patch_applied_count`
- `instrumentation_patch_reverted_count`
- `instrumentation_produced_useful_signal_count`
- `instrumentation_changed_selected_file_count`
- `instrumentation_changed_selected_region_count`
- `instrumentation_improved_semantic_localization_count`
- `instrumentation_regressed_semantic_localization_count`

Add instrumentation taxonomy:
- `instrumentation_not_attempted_by_gate`
- `instrumentation_patch_generation_failed`
- `instrumentation_apply_failed`
- `instrumentation_run_no_useful_signal`
- `instrumentation_recovered_correct_file`
- `instrumentation_recovered_correct_region`
- `instrumentation_attempted_but_selection_still_wrong`
- `instrumentation_revert_failed`

The report should compare:
- static-only
- plain runtime
- instrumented runtime
- final chosen mode

### 10. Safety constraints

This stage needs strict safety rules.

Required:
- patch only a small bounded shortlist
- patch only temporary logs
- patch must be reverted by the exact reverse patch
- stop on revert failure
- do not use:
  - `git reset --hard`
  - `git checkout --`
  - destructive cleanup

Also:
- do not instrument files outside the repo workspace
- do not instrument more than the configured cap
- do not keep instrumentation in the working tree after the run

### 11. Benchmark order

Run in this order:

1. implement instrumentation-capable workflow
2. smoke test on 2-3 runtime-sensitive cases
3. inspect:
   - patch correctness
   - log emission
   - revert correctness
4. run only on the current runtime-sensitive / failed-tail subset
5. compare:
   - static-only
   - plain runtime
   - instrumented runtime
6. only if instrumentation produces real gains, consider the full 95

Promotion criteria:
- instrumentation must produce useful signal on a meaningful fraction of attempted cases
- revert must succeed reliably
- localization improvement must appear on the failed/runtime-sensitive tail
- no persistent workspace mutation

## Public Interfaces / CLI

Extend the current workflow CLI with:

- `--enable-instrumented-runtime`
- `--instrumentation-max-files`
- `--instrumentation-max-points`
- `--instrumentation-timeout-seconds`
- `--instrumentation-only-on-failures`
- `--instrumentation-gate-threshold`

Defaults:
- instrumentation disabled unless explicitly enabled
- bounded to small file/point counts
- only run after static workflow
- optionally after plain runtime, or instead of plain runtime if plain runtime is clearly unhelpful

## Test Plan

### Patch safety tests

- generated instrumentation patch applies cleanly
- reverse patch removes it cleanly
- no extra files are modified
- dirty worktree survives unchanged except for the temporary patch lifecycle

### Logging tests

- inserted logs have the expected marker format
- parser extracts file/symbol/event/value correctly
- no-useful-signal case is classified correctly

### Selection tests

- logged implementation file can outrank static runner-up files
- logged symbol/branch can drive region selection
- selector remains bounded to candidate/instrumented files

### Acceptance tests

- smoke on:
  - Django autoreload issue
  - management command issue
  - one non-Django runtime-sensitive case
- failed/runtime-sensitive tail benchmark
- compare against:
  - current static best
  - current plain-runtime v3

## Assumptions and Defaults

- Instrumentation is temporary and must always be reverted
- The system is allowed to modify repo code temporarily for debugging only
- Instrumentation is selective, not global
- Plain runtime remains available, but instrumented runtime is the stronger debugging stage
- Full benchmark rollout happens only after failed-tail validation shows real gain
