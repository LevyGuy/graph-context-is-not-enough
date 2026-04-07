# Lessons Learned: Context Vs. Repair

## Core Finding

The experiments so far support a clear conclusion:

- better semantic context helps bug localization a lot
- better semantic context alone is not enough for reliable code repair

This updates the working hypothesis from:

- "If we provide the best possible context, the LLM should produce the best possible fix"

to:

- "Better context improves the model's ability to understand where and why a bug exists, but correct repair also depends on reasoning quality, edit control, and behavioral validation"

## What The Graph Clearly Helped With

The graph-based pipeline improved:

- file and function localization
- bug mechanism recognition
- code-path understanding
- semantic summaries of the relevant logic

In several cases, the graph was able to identify:

- the correct file
- the correct symbol or code region
- the correct repair mechanism

even when the final patch still failed.

This means the graph is genuinely useful as a debugging aid and retrieval system.

## What The Graph Did Not Automatically Solve

Even when the graph summary was directionally correct, the patch model still often failed to:

- choose the exact correct line or sub-region
- produce the smallest correct code change
- preserve hidden invariants enforced by tests
- avoid regressions in previously passing behavior

Examples from the experiments:

- `astropy__astropy-6938`
  - the graph identified the right bug family
  - the model still failed to produce the correct in-place mutation semantics

- `django__django-11019`
  - the graph identified the right file and merge/order bug family
  - the model still failed to synthesize the correct algorithmic repair

## Updated Mental Model

The results suggest that LLM performance on repair tasks is not a simple function of context quality alone.

A better approximation is:

- repair performance = context quality × reasoning quality × edit control × feedback quality

Where:

- `context quality`
  - whether the model sees the right code and relationships

- `reasoning quality`
  - whether it can infer the correct invariant and repair strategy

- `edit control`
  - whether the system constrains the model to patch the right scope cleanly

- `feedback quality`
  - whether the system can validate, test, and refine the patch

The graph primarily improved `context quality`.
The remaining failures mostly came from the other three factors.

## Why This Is Not A Failure Of Context Engineering

The graph still delivered something important:

- it reduced the search problem
- it improved understanding
- it made failure modes narrower and easier to inspect

That is a real gain.

The main lesson is not:

- "context engineering does not matter"

The lesson is:

- "context engineering is necessary but not sufficient for code repair"

## Practical Implication

For hard repair tasks, the next improvements should not come only from adding more context.

They should come from combining strong context with:

- bounded repair scopes
- better target grounding
- structured repair planning
- execution-guided refinement
- test-aware repair loops

## Research Interpretation

This is an important result for the project:

- graph-based semantic indexing appears valuable for bug localization
- but one-shot patch generation does not reliably convert semantic understanding into correct code repair

So the current evidence supports:

- "Graph context improves debugging and localization"

more strongly than it supports:

- "Graph context alone improves end-to-end SWE-bench resolution"

That distinction should guide the next phase of the benchmark design.
