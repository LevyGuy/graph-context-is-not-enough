# Localization Audit Rubric

Use this rubric for the 30-instance manual audit subset.

For each audited instance, fill these fields in the audit jsonl row:

- `audit_correct_file`
- `audit_correct_region`
- `audit_correct_fix_mechanism`
- `audit_graph_found_issue`
- `audit_notes`

## Definitions

### `audit_correct_file`

Set to `true` if the graph pipeline clearly identified the correct source file where the bug should be fixed, even if the official SWE-bench patch used a slightly different implementation strategy.

### `audit_correct_region`

Set to `true` if the graph pipeline clearly identified the right function, method, class, or local code region where the bug lives.

This is broader than exact gold-hunk matching.

### `audit_correct_fix_mechanism`

Set to `true` if the graph summary correctly identified the repair mechanism or bug family.

Examples:

- in-place mutation vs rebinding
- case-insensitive parsing
- config default should be changed at the definition site
- merge/order logic should preserve dependency ordering

### `audit_graph_found_issue`

Set to `true` only if the graph pipeline found the issue strongly enough that a human would consider the issue correctly localized for debugging purposes.

Recommended rule:

- `audit_graph_found_issue = audit_correct_fix_mechanism AND (audit_correct_file OR audit_correct_region)`

### `audit_notes`

Use for short explanations when:

- the weak-label benchmark looks wrong
- the official patch is one valid implementation among several
- the graph found the right bug but the official gold hunk is elsewhere
- the issue is ambiguous or truly multi-file

## Labeling Guidance

- Judge the graph pipeline as a debugging-localization system, not as a patch generator.
- Do not penalize the graph for missing the exact gold patch line if it still clearly found the correct issue location and mechanism.
- If the graph named an entrypoint plus the true implementation site, favor the implementation site in your judgment.
- If the graph identified the correct bug family but only at a very broad subsystem level, mark `audit_correct_fix_mechanism=true` but `audit_correct_region=false`.
