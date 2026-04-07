# Graph And Developer Workflow Retrospective

## Goal

This project started with one core hypothesis:

- Better context engineering should improve LLM bug understanding and localization on SWE-bench-style issues.

More specifically:

- If we provide the LLM with the kinds of tools a strong developer uses while debugging, localization quality should improve.
- Some of those tools are deterministic and reliable, such as structural code indexing and text search.
- Combining deterministic tools with LLM reasoning might improve SWE-bench performance over graph-only retrieval or prompt-only approaches.

The practical question became:

- Can we build a static developer workflow that reliably points the LLM to the right issue location and fix mechanism?

This report summarizes what we implemented, what we measured, what failed, and what we learned.

## Initial Benchmark Direction

The first benchmark plan was a research-style graph localization study:

- freeze one graph-localization pipeline configuration
- sample a stratified subset from SWE-bench Lite
- measure file retrieval, semantic mechanism correctness, and semantic localization
- later audit a subset manually

The original intended sample size was 120, then later 95, and later a stable 37-instance ready subset was used for faster iteration.

## Phase 1: Full Study Infrastructure

We first built the study infrastructure rather than changing retrieval logic.

Implemented:

- [localization_study.py](/Users/guylevy/Projects/natural-language-index_2/localization_study.py)
- [select_localization_audit_sample.py](/Users/guylevy/Projects/natural-language-index_2/select_localization_audit_sample.py)
- extensions to [localization_eval.py](/Users/guylevy/Projects/natural-language-index_2/localization_eval.py)
- extensions to [prepare_dataset.py](/Users/guylevy/Projects/natural-language-index_2/prepare_dataset.py)

What this added:

- frozen sample manifests
- repo-stratified study sample generation
- confidence intervals
- weak-label metrics
- audited-label hooks
- failure taxonomy support
- study-specific reports and metadata artifacts

## Phase 2: Full-Repo Semantic Enrichment Attempt

The first implementation path tried to precompute semantic graph descriptions for all symbols across the benchmark sample.

Workflow shape:

1. prepare workspaces
2. generate SCIP output
3. enrich symbols repo-wide with LLM-written descriptions
4. run localization on top of that enriched graph

### Operational problems

This approach failed operationally:

- the wrong `scip` binary was being invoked at one point
- large indexing runs hit OpenAI rate limits
- throughput collapsed on large Django repos
- progress slowed to only a few instances per day

Main lesson:

- repo-wide semantic enrichment is too expensive to be a practical benchmark strategy
- the dominant cost was LLM enrichment, not raw structural indexing

This led to the first major redesign.

## Phase 3: Structural-Only Graph Indexing

We then switched to:

- structural-only indexing for all repos
- no repo-wide semantic enrichment
- summary generation only at issue time

That led to changes in:

- [graph_index_pipeline.py](/Users/guylevy/Projects/natural-language-index_2/graph_index_pipeline.py)
- [localization_study.py](/Users/guylevy/Projects/natural-language-index_2/localization_study.py)
- [run_inference.py](/Users/guylevy/Projects/natural-language-index_2/run_inference.py)
- [graph_exact_patch_pipeline.py](/Users/guylevy/Projects/natural-language-index_2/graph_exact_patch_pipeline.py)

### Important indexing bug discovered

During manual inspection of `django__django-11049`, we found a serious graph completeness bug:

- the issue explicitly named `DurationField`
- but `DurationField` was missing from the graph symbol table

This showed:

- the problem was not just retrieval ranking
- the graph itself was incomplete

We then moved to an AST-first extractor so top-level classes, nested methods, and key structural blocks would be represented correctly.

## Phase 4: AST-First Structural Graph v2

We implemented an AST-first structural graph pipeline to improve completeness.

Key changes:

- top-level classes and functions indexed directly from AST
- nested methods captured
- block extraction for assignments, constants, regexes, returns, raises, conditionals, and try/except
- structural-only storage with deterministic placeholder descriptions

This improved:

- symbol completeness
- graph coverage on framework-heavy files
- retrieval for anchored cases like `DurationField`

### v2 localization results on 37 instances

The graph-only v2 pipeline produced materially better results than the older graph-only run.

On the 37-instance v2 subset, headline results were:

- semantic correct fix mechanism: 89.2%
- semantic localization match: 51.4%
- weak proxy graph found issue: 64.9%

Interpretation:

- mechanism understanding improved substantially
- exact localization was still weak
- the largest remaining failure bucket was still retrieval grounding

This was better than the earlier baseline, but still not good enough if the bar is reliable pinpointing.

## Phase 5: Critical Reassessment

At this point, we stepped back and examined what the graph-only pipeline was actually doing.

Main conclusion:

- graph-only retrieval was not enough
- even when the model often understood the bug mechanism, it still often failed to ground that understanding to the correct implementation site

This led to a more critical framing:

- the graph should be one developer tool, not the whole workflow
- the real hypothesis should be tested with a broader static developer workflow

## Phase 6: Hypothesis Review

We then reviewed failed cases directly against the broader developer-workflow hypothesis.

Artifacts created:

- [HYPOTHESIS_ASSESSMENT.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/hypothesis_review/HYPOTHESIS_ASSESSMENT.md)
- [INSTANCE_INDEX.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/hypothesis_review/INSTANCE_INDEX.md)

And per-instance reports under:

- [instances](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/hypothesis_review/instances)

This review suggested:

- many failures were not evidence against the developer-workflow hypothesis itself
- many were evidence that the graph-only workflow lacked other general developer tools such as text search, symbol search, and better evidence synthesis

## Phase 7: Tool-First Developer Workflow

We then implemented a new tool-first localization pipeline.

New files:

- [developer_workflow.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow.py)
- [developer_workflow_localization.py](/Users/guylevy/Projects/natural-language-index_2/developer_workflow_localization.py)

The workflow stages were:

1. issue anchor extraction
2. deterministic candidate discovery
3. candidate merge and provenance capture
4. bounded graph expansion
5. evidence packet rendering
6. LLM summary and structured localization JSON
7. target selection
8. aggregate reporting

Implemented tools:

- anchor extraction
- symbol lookup
- file lookup
- repo grep
- optional vector helper for anchorless issues
- graph expander
- evidence renderer
- LLM summarizer
- target selector

Per-instance artifacts now include:

- `anchors.json`
- `symbol_candidates.json`
- `file_candidates.json`
- `grep_candidates.json`
- `candidate_merge.json`
- `graph_expansion.json`
- `evidence_packet.md`
- `summary.md`
- `summary_structured.json`
- `selector_decision.json`
- `localization_result.json`

## Phase 8: Small-Subset Iteration Loop

We then began a proper loop:

1. run benchmark
2. analyze misses
3. write per-instance miss reports
4. implement fixes
5. rerun

### First failed-subset baseline

We ran the first tool-first workflow on a failed subset and observed:

- semantic localization match: 5/10

### Iteration 1

We improved:

- anchor extraction
- dotted-reference parsing
- candidate ranking
- repeated-grep over-weighting
- targeted implementation-file bonuses for some well-understood patterns

Artifacts:

- [developer_workflow_failed18.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_failed18.md)
- [developer_workflow_failed18_iter1.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_failed18_iter1.md)
- [ITERATION_REPORT.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_iterations/iteration_1/ITERATION_REPORT.md)

Iteration 1 result:

- semantic localization improved from 5/10 to 7/10
- merged top-3 gold coverage improved from 2/10 to 8/10

This was a real gain.

### Iteration 2 and later failed-subset experiments

Later iterations tried:

- stronger global anchor sanitization
- more aggressive selector preferences
- anchor tiering

These did not improve the failed subset further in a stable way.

In some runs:

- the failed-subset result regressed badly
- in later anchor-tier runs the result was effectively flat

Main lesson:

- broad generic anchor cleanup is not enough
- and pushing more special-case heuristics into the pipeline starts to look like benchmark-specific tuning rather than a general workflow

## Phase 9: Larger-Sample Tool-First Baseline

We then ran the tool-first workflow on the stable 37-instance ready subset.

Final completed larger-sample tool-first baseline:

- [developer_workflow_ready37_iter0_full.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_ready37_iter0_full.md)
- [developer_workflow_ready37_iter0_full.json](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_ready37_iter0_full.json)

Headline results on 37:

- exact symbol hit contains gold file: 62.2%
- merged candidate top-3 contains gold file: 64.9%
- expanded candidate set contains gold file: 67.6%
- semantic correct file: 73.0%
- semantic correct function: 70.3%
- semantic correct fix mechanism: 91.9%
- semantic localization match: 70.3%
- weak workflow found issue: 73.0%

Failure taxonomy:

- localized successfully: 27
- deterministic candidate discovery missed correct file: 7
- issue likely requires runtime execution/reproduction: 2
- LLM summary ignored strong evidence: 1

This was the strongest result achieved during the work.

## What We Learned

### 1. Repo-wide semantic enrichment is the wrong indexing strategy

It is too expensive in:

- time
- CPU
- API cost
- operational fragility

Structural-only indexing plus issue-time summarization is the correct direction.

### 2. Graph completeness matters

Missing symbols like `DurationField` invalidate retrieval before ranking even begins.

AST-first structural extraction was necessary and improved real benchmark results.

### 3. Graph-only retrieval is not enough

Graph-only retrieval produced:

- decent mechanism understanding
- but weak exact localization

The graph should be treated as one tool in the workflow, not the whole retrieval strategy.

### 4. Tool-first retrieval is substantially better than graph-first retrieval

The larger-sample tool-first baseline was materially stronger than the earlier graph-first runs.

This supports the high-level developer-workflow hypothesis more than the graph-only hypothesis.

### 5. The dominant remaining failure is candidate discovery

The main recurring failure class is still:

- deterministic candidate discovery missed correct file

This means the next improvements should focus on better evidence gathering, not just better summarization.

### 6. Overfitting is a real risk

When we started adding more issue-family-specific bonuses and heuristics, we moved away from testing a general developer workflow.

That is a warning sign.

The next system should prioritize general workflow improvements, not per-case patches.

## Where The Current Tool-First Workflow Still Falls Short

The current tool-first workflow still misses important general-purpose developer tools.

Most important missing tools:

- test/example lookup as a first-class retrieval source
- explicit comparison of competing candidate files
- strict file-first then region-level selection
- better separation of:
  - API/symbol evidence
  - file/path evidence
  - grep/text evidence
  - test/example evidence
  - graph expansion evidence

Without these, the workflow still collapses too early onto one candidate story.

## Current Best Interpretation Of The Hypothesis

The developer-workflow hypothesis is still alive.

What the evidence currently supports:

- Better static workflow tooling does improve localization materially.
- Deterministic tools plus LLM synthesis are stronger than graph-only retrieval.
- Structural graph context is useful, but should be secondary to broader developer evidence gathering.

What the evidence does not yet support:

- That the current static workflow is sufficient for reliably solving SWE-bench localization.
- That graph retrieval alone is the right path.
- That adding more case-specific heuristics is a good long-term strategy.

## Recommended Next Direction

Do not continue by adding more issue-family patches.

Instead, build a more faithful general developer workflow with:

1. anchor extraction
2. symbol lookup
3. file lookup
4. grep/text search
5. test/example lookup
6. bounded graph expansion
7. candidate comparison across top files
8. file-first selection
9. region selection inside the chosen file

That is the most principled next step if the goal is to test whether a general developer workflow can improve SWE-bench.

## Bottom Line

The project moved through three major stages:

1. graph-only localization study infrastructure
2. structural graph redesign
3. tool-first developer workflow

The strongest outcome so far is:

- the tool-first workflow on the 37-instance subset, with 70.3% semantic localization and 91.9% fix-mechanism correctness

The clearest remaining limitation is:

- deterministic candidate discovery still misses the correct file too often

The strongest overall conclusion is:

- The hypothesis that a broader developer workflow can improve SWE-bench is more plausible now than it was at the start.
- But the current workflow is still incomplete, and the next gains are more likely to come from adding general developer tools than from pushing graph heuristics further.
