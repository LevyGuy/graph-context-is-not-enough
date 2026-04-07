# Stack-Trace Subgroup Interpretation

## Why This Subgroup Matters

This subgroup tests a narrower version of the original graph-context hypothesis: whether graph-centric retrieval helps more on issues that already contain execution anchors, especially stack traces.

## Deterministic Definition

An issue is included if its problem statement contains at least one of:

- `Traceback (most recent call last):` or `Traceback`
- a Python stack frame matching `File "…", line N`
- a test failure header matching `FAIL: ... (...)` or `ERROR: ... (...)`

No manual exceptions or hand-picked additions were used.

## Exact Result

- Paired stack-trace subset size: `22`
- Graph-only semantic localization on the subgroup: `8/22 (36.4%, 95% CI 19.7-57.0%)`
- Tool-first semantic localization on the subgroup: `12/22 (54.5%, 95% CI 34.7-73.1%)`

## Publication Guidance

- Graph-only does not improve materially on stack-trace issues relative to its full-sample result. This strengthens the negative claim that graph context alone is insufficient even when issues include execution anchors.
- The tool-first workflow remains clearly stronger than graph-only on the stack-trace subset, so stack traces do not make graph-only retrieval competitive with the broader workflow.

## What This Does Not Justify

- It does not justify reviving the strong graph-only claim.
- It does not justify replacing the main full-sample comparison with the subgroup result.
- It should be presented as a stratified analysis, not a new headline benchmark.

## Best Use In The Paper

Use this as a subgroup-analysis subsection that sharpens the main claim: either graph context has a narrower niche on trace-rich issues, or graph-only remains weak even under favorable anchoring conditions.
