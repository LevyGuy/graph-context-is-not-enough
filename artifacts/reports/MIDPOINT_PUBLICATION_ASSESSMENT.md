# Midpoint Publication Assessment

## Purpose

This note is meant to help answer a specific research decision:

- Is there enough evidence already to justify publishing a midpoint paper based on the graph-context and developer-workflow experiments so far?

This is not a full project retrospective. That already exists in:

- [GRAPH_AND_WORKFLOW_RETROSPECTIVE.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/GRAPH_AND_WORKFLOW_RETROSPECTIVE.md)

Instead, this document focuses on:

- what claims are currently supported
- what claims are not yet supported
- what the strongest paper framing would be right now
- what evidence gaps remain

## Short Answer

Yes, there is enough evidence for a publishable midpoint paper, but the paper should **not** be framed as:

- "graph context dramatically improves SWE-bench localization"

That claim is not supported.

The evidence does support a stronger and more interesting claim:

- **graph-only context is insufficient as the main localization strategy**
- **a broader developer workflow materially outperforms graph-only context**
- **runtime/instrumented debugging remains an important unresolved frontier**

So the publishable contribution is likely:

- a negative-to-positive research arc
- graph-only fails as the main hypothesis
- tool-first developer workflow substantially improves over graph-only
- naive runtime augmentation is harder than expected and exposes execution-environment bottlenecks

## What The Existing Retrospective Already Covers

The current retrospective file already does a good job on:

- project motivation
- chronological development history
- graph-only phase
- structural graph redesign
- tool-first workflow redesign
- iterative improvement loop
- high-level conclusions

In particular, it already contains:

- the original graph-context hypothesis
- the semantic-enrichment failure
- the AST-first structural fix
- the key graph-only result on the 37-instance subset
- the shift from graph-first to tool-first workflow
- the larger 37-instance tool-first result
- the conclusion that graph-only was not enough

## What The Existing Retrospective Does Not Yet Cover Well Enough

For a publication decision, the retrospective is missing several important things:

### 1. A publishable claim statement

It does not clearly separate:

- what is publishable now
- what remains unfinished

### 2. Cross-phase headline comparisons

It does not cleanly center the strongest before/after comparison:

- old graph-first 95 baseline
- best static tool-first 95 result

### 3. Limits on the graph-only claim

It implies the direction of the project, but it does not explicitly state:

- the graph-only hypothesis failed in its strong form
- graph context alone did not deliver reliable localization

### 4. Publication-risk framing

It does not discuss:

- novelty
- what kind of paper this would be
- what reviewers might challenge
- what evidence would still strengthen the story

### 5. Runtime status

It predates most of the runtime and instrumented-debugging work, which now matters for the "what next?" story.

## Most Important Experimental Evidence

### A. Old graph-first baseline on the 95 sample

From:

- [localization_study_95_structural_reused_ready.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_study_95_structural_reused_ready.md)

Headline numbers:

- semantic correct file: `40/93 (43.0%)`
- semantic correct function: `33/93 (35.5%)`
- semantic correct fix mechanism: `70/93 (75.3%)`
- semantic localization match: `30/93 (32.3%)`

Interpretation:

- graph-only context was not reliably localizing bugs
- the system often understood the issue family better than the actual implementation site

### B. Best static tool-first workflow on the 95 sample

From:

- [developer_workflow_full95_v2_region.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_full95_v2_region.md)

Headline numbers:

- semantic correct file: `78/95 (82.1%)`
- semantic correct function: `66/95 (69.5%)`
- semantic correct fix mechanism: `83/95 (87.4%)`
- semantic localization match: `63/95 (66.3%)`

Interpretation:

- the broader static developer workflow clearly outperformed graph-first retrieval
- the strongest gains came from better file/function grounding, not just mechanism understanding

### C. Runtime fallback on the failed tail

From:

- [/tmp/runtime_failed_tail.md](/tmp/runtime_failed_tail.md)

Headline numbers:

- static-only semantic localization on failed tail: `9/29 (31.0%)`
- runtime-augmented semantic localization: `0/29`
- runtime attempted: `16/29`
- runtime produced traceback: `0/29`

Interpretation:

- naive runtime fallback did not help
- the main problem was command inference and environment reliability

### D. More recent runtime smoke evidence

From:

- [/tmp/runtime_smoke_pylint7228_v4.md](/tmp/runtime_smoke_pylint7228_v4.md)

Most important qualitative result:

- runtime command inference is now good enough to reach the intended CLI path for at least some cases
- but environment blockers like missing dependencies still limit the usefulness of runtime

This is useful as evidence of where the next frontier is, but it is not yet a benchmark-scale result.

## What You Can Honestly Claim Right Now

These claims are supported by the current evidence:

### Supported Claim 1

- **Graph-only structural context is not sufficient as the primary localization strategy for SWE-bench-style bug localization.**

Evidence:

- graph-only 95 result: `32.3%` semantic localization
- repeated qualitative failures where the model understood the bug class but not the implementation site

### Supported Claim 2

- **A broader static developer workflow materially outperforms graph-first retrieval.**

Evidence:

- `32.3% -> 66.3%` semantic localization on the large benchmark comparison
- substantial gains in semantic correct file and semantic correct function

### Supported Claim 3

- **The main value comes from combining deterministic retrieval tools with LLM synthesis, not from graph context alone.**

Evidence:

- tool-first workflow includes anchor extraction, symbol lookup, file lookup, grep, graph expansion, file comparison, and staged selection
- this broader workflow strongly outperforms graph-first

### Supported Claim 4

- **The remaining tail is increasingly runtime-sensitive or execution-environment-sensitive.**

Evidence:

- static workflow still leaves a small but meaningful set of discovery / region / runtime-sensitive misses
- runtime failed-tail work shows that execution support is not trivial and introduces its own bottlenecks

## What You Should Not Claim Yet

These claims are not yet supported strongly enough:

### Unsupported Claim 1

- "Graph context dramatically improves LLM understanding on its own."

Why not:

- graph-only results are much weaker than the later workflow results
- the experiments suggest graph context is one useful tool, not the decisive factor

### Unsupported Claim 2

- "Runtime augmentation already improves SWE-bench localization."

Why not:

- benchmark-scale runtime evidence is still weak
- current runtime work is promising in mechanism and infrastructure, but not yet validated at scale

### Unsupported Claim 3

- "We have solved SWE-bench localization with a general developer workflow."

Why not:

- the best current static result is strong but still leaves a meaningful miss set
- region selection, discovery, and runtime-sensitive issues remain open

## Strongest Publishable Paper Framing Right Now

The strongest framing is probably:

- **Graph Context Is Not Enough: Developer-Workflow Evidence for SWE-bench Localization**

Or more conservatively:

- **From Graph Retrieval to Developer Workflow: Lessons from SWE-bench Localization**

Or if emphasizing the negative result:

- **Why Graph-Centric Context Engineering Falls Short for SWE-bench Bug Localization**

### Why this framing is strong

Because the current evidence supports a coherent research story:

1. strong initial hypothesis
2. careful implementation of graph-only approach
3. graph-only approach underperforms
4. analysis reveals why
5. broader developer workflow substantially improves results
6. runtime/debugging is identified as the next frontier

This is a real contribution even if the original strong graph-only hypothesis was false.

## What Kind Of Paper This Is

Most plausible paper types:

### 1. Empirical negative-to-positive systems paper

Structure:

- graph-only hypothesis
- failure analysis
- redesigned workflow
- benchmark improvement

### 2. Ablation / methodology paper

Structure:

- compare graph-only vs structural fixes vs tool-first workflow
- show which workflow components matter most

### 3. Position-plus-evidence paper

Structure:

- argue that SWE-bench localization should be modeled as developer workflow, not graph retrieval
- support that argument with the measured deltas

## What Reviewers Will Likely Ask

If you publish now, expect these questions:

### 1. Is the gain really from "developer workflow" or just more heuristics?

Response needed:

- emphasize the shift from graph-first to general tools:
  - file lookup
  - symbol lookup
  - grep
  - staged comparison
  - staged selection
- distinguish this from benchmark-specific patching

### 2. Is the comparison fair?

Response needed:

- explain that both systems were evaluated on the same benchmark-style sample
- provide exact sample sizes and any incomplete-count caveats (`93` vs `95`)

### 3. How much of the gain comes from the LLM rather than the tools?

Response needed:

- emphasize the deterministic retrieval and evidence-shaping improvements
- show stage-level metrics where possible

### 4. Why not compare to stronger external baselines?

This is likely the biggest weakness if submitted immediately.

If possible, strengthen with:

- comparisons to a prompt-only baseline
- comparisons to a file-search-only baseline
- comparisons to graph-only vs graph+grep vs full workflow

## Main Weaknesses In A Midpoint Paper

### 1. The current story is stronger than the current ablation table

The narrative is good, but a paper would benefit from a cleaner ablation matrix.

### 2. Runtime work is still preliminary

This is fine if presented as future work, but not if positioned as a validated contribution.

### 3. Benchmark comparison counts are not perfectly symmetric

One baseline is over `93` completed instances, another over `95`.
This is not fatal, but should be explained carefully.

### 4. Some iterations involved heuristic tuning

This does not invalidate the outcome, but the paper should distinguish:

- general workflow design
- local iterative tuning

## Recommendation

If the goal is to publish **midway through the research**, my recommendation is:

- **Yes, consider publishing now**
- but publish the work as:
  - an empirical finding that graph-centric context engineering is insufficient
  - and that a broader developer-workflow abstraction is much stronger

Do **not** publish it as:

- "we proved graph context works"

That is the opposite of what the evidence shows.

## Best Current One-Sentence Thesis

- **Our experiments suggest that graph context alone is not enough for SWE-bench localization, while a broader static developer workflow substantially improves localization quality and exposes runtime debugging as the next unresolved frontier.**

## If You Want To Strengthen The Paper Before Submitting

The highest-value additions would be:

1. a clean ablation table:
   - graph-only
   - graph + structural fixes
   - tool-first static workflow
   - optional early runtime results as future-work appendix

2. one explicit section on negative findings:
   - repo-wide semantic enrichment too expensive
   - graph-only too weak
   - naive runtime fallback ineffective

3. one explicit section on threats to validity:
   - sample composition
   - semantic evaluation subjectivity
   - implementation iteration during development

4. one explicit section on reproducibility:
   - artifact paths
   - frozen metadata sample
   - exact report files used in comparison

## Files To Hand To Another LLM For Publication Advice

I recommend giving the other LLM these two files first:

- [GRAPH_AND_WORKFLOW_RETROSPECTIVE.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/GRAPH_AND_WORKFLOW_RETROSPECTIVE.md)
- [MIDPOINT_PUBLICATION_ASSESSMENT.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/MIDPOINT_PUBLICATION_ASSESSMENT.md)

Optional supporting evidence if you want the LLM to see raw benchmark numbers too:

- [localization_study_95_structural_reused_ready.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_study_95_structural_reused_ready.md)
- [developer_workflow_full95_v2_region.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_full95_v2_region.md)
