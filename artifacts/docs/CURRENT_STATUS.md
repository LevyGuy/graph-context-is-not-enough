# Current Status

## What Works

- The graph pipeline can often identify the correct bug mechanism.
- The graph pipeline can often localize the correct file or function family.
- Clean-checkout patch validation is working.
- Prompt and patch artifacts are logged per instance for debugging.
- Localization and repair are now measured separately.

## What Does Not Work Reliably Yet

- End-to-end SWE-bench resolution is still inconsistent.
- Correct semantic diagnosis does not reliably turn into a correct patch.
- Function-bounded repair is more realistic than one-line repair, but it still drifts.
- Stronger patch models improved patch robustness more than benchmark success.

## Current Evidence

- Localization benchmark:
  - semantic fix mechanism is often correct
  - exact gold-hunk grounding is still weak

- End-to-end repair benchmark:
  - the graph pipeline can produce real SWE-bench wins
  - but it still fails on cases where the exact implementation semantics are subtle

## Main Lesson

- Better context improved diagnosis.
- Better context alone did not guarantee better repair.

This means the project should now focus on:

- graph localization quality
- repair control
- test-aware or execution-guided refinement

not only on adding more retrieval context.

## Best Current Interpretation

The project currently supports this claim more strongly:

- graph-based semantic indexing improves debugging and localization

than this claim:

- graph-based semantic indexing alone improves end-to-end code repair

## Recommended Next Steps

1. Keep localization and repair benchmarks separate.
2. Continue measuring where the graph fails to identify the right issue.
3. Add stronger repair loops instead of assuming context alone is sufficient.
4. Use the graph as the localization engine, then improve the downstream repair system.
