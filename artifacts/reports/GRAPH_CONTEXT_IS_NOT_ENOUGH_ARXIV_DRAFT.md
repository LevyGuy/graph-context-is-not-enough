# Graph Context Is Not Enough: From Graph Retrieval to Developer Workflow for SWE-bench Localization

**Author draft:** replace with final author list, affiliations, and contact information before submission.

## Abstract

Repository-level software engineering benchmarks such as SWE-bench have intensified interest in context engineering for bug localization and repair. A common hypothesis is that structural code graphs, such as SCIP- or AST-derived repository graphs, can provide the large language model (LLM) with the right context to localize issues reliably. The intuition behind this hypothesis is twofold: first, that a small accurate context should outperform a large noisy repository dump; second, that if structural facts are converted into a short developer-style natural-language story, the LLM should reason about the issue more effectively. We evaluate that hypothesis through a staged empirical study. We first build a graph-centric localization pipeline over a stratified SWE-bench-style sample and show that graph-only structural context is not sufficient as the primary localization strategy: on a 93-instance baseline, graph-first localization achieves 32.3% semantic localization match despite 75.3% fix-mechanism correctness. We then redesign the system around a broader static developer workflow consisting of anchor extraction, symbol lookup, file lookup, repository grep, bounded graph expansion, evidence synthesis, file-first comparison, and staged region selection. On a 95-instance sample, this tool-first workflow improves semantic localization match to 66.3%, semantic correct file to 82.1%, semantic correct function to 69.5%, and semantic correct fix mechanism to 87.4%. We also perform a paired stack-trace subgroup analysis on 22 issues whose problem statements contain explicit execution anchors; graph-only semantic localization rises only modestly to 36.4%, while the tool-first workflow remains clearly stronger at 54.5%. We further study runtime augmentation and instrumented debugging. Naive runtime fallback initially fails to help because command inference and execution environments are brittle; later targeted runtime improvements recover meaningful traceback signal on selected cases but remain preliminary at benchmark scale. The main contribution of this work is an empirical negative-to-positive result: graph context alone is not enough, while a broader developer-workflow abstraction materially improves localization. We release a detailed failure taxonomy, benchmark artifacts, and a reproducible engineering retrospective to support future work on software engineering agents.

## 1. Introduction

Large language models are increasingly evaluated on repository-level software engineering tasks, where success depends not only on code generation quality but also on effective problem localization. SWE-bench formalized this setting by pairing real GitHub issues with repository snapshots and gold patches, creating a challenging benchmark for automated issue resolution [1]. In practice, however, issue resolution remains bottlenecked by localization: before an agent can edit code correctly, it must identify the right file, function, or region.

This paper began from a specific hypothesis:

- structural graph context should substantially improve LLM bug understanding and localization.

The intuition had two parts.

First, we expected small accurate context to beat large noisy context. Repository-scale prompts often mix the real implementation site with many lexically similar but causally irrelevant files. If the graph could compress the repository into a much smaller evidence packet centered on relevant symbols, ownership relations, imports, and nearby blocks, then the model should face a better signal-to-noise ratio than when reading large raw code excerpts.

Second, we expected developer-style natural-language explanations to help the model use that compressed context. LLMs are trained on code and natural language, and in practice they often reason better when evidence is presented as a coherent story rather than as disconnected code fragments. The hoped-for chain was:

1. deterministic structural tools recover the relevant facts;
2. those facts are converted into a compact developer-oriented explanation of the issue;
3. the LLM uses that explanation to choose the right implementation site and fix mechanism.

Human developers use structure when navigating code: ownership relations, symbol definitions, imports, and call edges often narrow the search space quickly. If an LLM received a sufficiently complete repository graph, perhaps augmented with semantic descriptions, it should localize issues more effectively than with unstructured context alone.

That hypothesis did not survive contact with the benchmark.

We found three important things. First, graph-only localization underperformed badly in its strong form. Even after fixing major indexing problems, the graph-centric pipeline was much better at recovering the broad fix mechanism than at grounding the issue to the correct implementation site. Second, a broader static workflow, closer to how experienced developers actually debug issues, produced much larger gains. Deterministic search tools such as file lookup, symbol lookup, and repository grep were more useful than graph retrieval alone, and graph structure was most valuable as a supporting tool rather than the primary retrieval engine. Third, the remaining hard tail increasingly appeared runtime-sensitive, but naive runtime augmentation failed because reaching the correct execution path proved substantially harder than anticipated.

The result is a different paper than originally intended. This is not a paper claiming that graph context solves SWE-bench localization. It is a paper showing that:

1. graph-centric context engineering is insufficient as the main localization strategy;
2. a broader static developer workflow materially outperforms graph-only retrieval; and
3. runtime debugging remains the next unresolved frontier.

### 1.1 Research Questions

We organize the paper around three research questions.

**RQ1.** How effective is graph-only structural context for SWE-bench-style bug localization?

**RQ2.** Does a broader static developer workflow materially outperform graph-first retrieval?

**RQ3.** What failure modes remain after the static workflow redesign, and what do they imply for the next generation of software engineering agents?

### 1.2 Main Contributions

This paper makes four contributions.

1. It presents a negative empirical result: graph-only structural context is not sufficient as the primary localization strategy for SWE-bench-style bug localization.
2. It introduces and evaluates a broader static developer workflow that more than doubles semantic localization on our large-sample comparison (`32.3% -> 66.3%`).
3. It provides a detailed failure taxonomy spanning candidate discovery, graph expansion, file comparison, region selection, and runtime-sensitive failures.
4. It documents operational lessons from semantic enrichment, structural indexing, static tool design, runtime fallback, and temporary instrumented debugging.

Throughout the paper, we distinguish between three levels of claim strength:

- benchmark-backed graph-first and static tool-first results;
- benchmark-backed subgroup analysis on stack-trace issues; and
- exploratory runtime and instrumentation findings that are operationally informative but not yet benchmark-positive.

## 2. Background and Motivation

SWE-bench evaluates whether a model or agent can resolve real GitHub issues in real repositories [1]. This setting is difficult because a model must do more than generate plausible code. It must identify the relevant implementation site, understand the issue in the context of repository structure, and often reason across files, tests, and framework layers.

A popular response to this challenge is graph-centric context engineering: build a code graph, retrieve a subgraph around issue anchors, and feed that structured context to the LLM. This idea is attractive for two reasons. First, graphs are deterministic and relatively interpretable. Second, graph retrieval appears better aligned with software structure than raw token similarity alone.

There was also a more practical motivation behind our initial design. The project did not begin from the belief that "graphs are elegant," but from the belief that context compression matters. Large-context prompting tends to degrade when a repository dump contains too many superficially relevant but behaviorally irrelevant files. A graph seemed like a principled way to compress the repository into a small, structured story. If that story could then be translated into concise natural language, it would align well with how LLMs appear to use developer-facing explanations.

However, graph-centric approaches make a strong assumption: repository structure is the dominant missing ingredient. Our work tests that assumption directly rather than taking it for granted.

## 3. Related Work

Our work sits at the intersection of repository-level software engineering benchmarks, issue localization, and coding-agent systems.

SWE-bench introduced a large repository-level benchmark for real-world GitHub issues and highlighted the difficulty of the task even for strong models [1]. Since then, a growing body of work has explored localization and repair strategies on SWE-bench and related benchmarks. CoSIL, for example, studies issue localization via LLM-driven repository graph searching and reports strong top-1 localization results using graph-guided search [2]. At the same time, recent analyses have questioned how benchmark outcomes should be interpreted, including concerns about leaderboard analysis [3], contamination and memorization [4], and behavioral differences among coding agents [5].

Our paper differs from prior graph-guided localization work in two ways. First, it is organized explicitly around a failed strong-form graph hypothesis. Second, it compares graph-centric retrieval to a broader static developer workflow built from deterministic developer tools and staged LLM synthesis, rather than positioning graph search as the dominant retrieval mechanism.

## 4. Experimental Setup

### 4.1 Benchmark Setting

We work on a stratified SWE-bench-style sample drawn from the same repository family used throughout the project. The experimental program evolved over time:

- early graph studies used a 120-instance planning target;
- later graph and workflow evaluations focused on a stable 95-instance sample;
- some intermediate iterations used smaller subsets, including a stable 37-instance ready subset and targeted failed-tail subsets.

For the primary large-sample comparison in this paper:

- the graph-first baseline report contains 93 completed instances;
- the best static developer-workflow report contains 95 completed instances.

We retain this asymmetry because the experiments were run in sequence during development, but we report the exact denominators for every metric.

### 4.2 Evaluation Metrics

We use both exact and semantic metrics.

**Candidate discovery and ranking**
- retrieved top-1 file match
- retrieved top-3 file match
- retrieved top-5 file match
- merged candidate top-3 contains gold file
- merged candidate top-5 contains gold file

These metrics ask whether the correct file appears in the retrieved candidate set before final selection. For example, "retrieved top-3 file match" means that at least one gold file appears among the first three files returned by the retrieval stage. "Merged candidate top-3 contains gold file" asks the same question after evidence from multiple tools has been merged and reranked.

**Selection and grounding**
- selected file is gold
- selected region is gold
- final target in gold file
- final target within gold hunk

These metrics ask whether the final selector picked the correct implementation location. "Selected file is gold" means the chosen file matches the gold file. "Selected region is gold" is stricter: the selected symbol or region must also align with the gold implementation region. "Final target within gold hunk" is the strictest exact metric because it requires overlap with the patch hunk itself.

**Semantic evaluation**
- semantic correct file
- semantic correct function
- semantic correct fix mechanism
- semantic localization match

The semantic metrics are broader than exact span overlap. "Semantic correct file" means the chosen file is judged to be the right conceptual implementation site even if the exact edit hunk differs from the gold patch. "Semantic correct function" asks the same question at the function or method level. "Semantic correct fix mechanism" asks whether the system understood what kind of change was needed, such as error-message formatting, migration serialization, validation logic, or query lookup behavior. "Semantic localization match" is the strongest semantic metric: the system must get the file, function or region, and fix mechanism all conceptually correct.

We also track a weaker issue-finding proxy:

- weak workflow found issue

This metric is intentionally permissive. It records whether the system identified the right implementation area or issue family strongly enough to be useful for a human or downstream agent, even if the final file or region selection was not fully correct.

The semantic metrics are intentionally broader than exact hunk overlap. This matters because some workflows identify the correct implementation conceptually without naming the exact gold hunk.

### 4.3 Reporting and Confidence Intervals

All aggregate reports include 95% confidence intervals. We also maintain:

- per-instance artifacts;
- explicit failure taxonomy labels;
- per-repo breakdowns; and
- stage-level metrics for candidate discovery, expansion, comparison, and selection.

## 5. Systems and Development Stages

The paper compares multiple stages of system design rather than a single frozen agent.

### 5.1 Graph-First Study Infrastructure

We first built study infrastructure rather than changing localization logic. This included:

- frozen sample manifests;
- stratified sampling;
- report generation;
- confidence intervals;
- weak-label metrics; and
- audit hooks.

The goal was to make later engineering iterations measurable rather than anecdotal.

### 5.2 Repo-Wide Semantic Enrichment

Our first implementation path attempted to enrich repository graphs with LLM-written semantic descriptions for symbols and graph nodes. This path failed operationally due to:

- high cost;
- rate limits;
- throughput collapse on large repositories such as Django; and
- poor scalability for benchmark-scale execution.

This result is not the main empirical claim of the paper, but it matters methodologically: repo-wide semantic enrichment was not a practical evaluation strategy for this benchmark.

### 5.3 Structural-Only Graph Indexing

We then shifted to a structural-only graph pipeline with issue-time summarization. This required a substantial indexing redesign after discovering a key completeness bug: in `django__django-11049`, the symbol `DurationField` was missing from the graph despite being named explicitly in the issue.

That failure revealed an important confound. Weak graph performance was not only a retrieval issue; the graph itself was incomplete.

We therefore moved to an AST-first extractor with:

- top-level classes and functions;
- nested methods;
- block extraction for assignments, constants, regexes, conditionals, returns, raises, and try/except; and
- structural-only storage with span-based retrieval.

### 5.4 Tool-First Static Developer Workflow

After the graph-only reassessment, we redesigned the localization system around a broader static workflow. The resulting pipeline contains:

1. issue anchor extraction;
2. exact symbol search;
3. exact file/path search;
4. repository grep / text search;
5. candidate merge with provenance;
6. bounded graph expansion from deterministic seeds;
7. evidence packet rendering;
8. file-first candidate comparison;
9. file selection;
10. region/function selection;
11. LLM synthesis and semantic judgment.

Graph structure remains present, but only as one tool in a broader workflow.

### 5.5 Concrete Example: From SCIP to SQL Summary to Developer Prompt

To make the workflow concrete, we use one representative issue:

- `django__django-13933`

The issue states that `ModelChoiceField` does not include the invalid value when raising `ValidationError`. This case is useful because it shows the intended path from structural index data to graph summaries to a developer-style prompt.

#### SCIP-style index evidence

From:

- [index.json](/Users/guylevy/Projects/natural-language-index_2/artifacts/indexes/django__django-13933/index.json)

the document for `django/forms/models.py` includes occurrences such as:

```json
{
  "relative_path": "django/forms/models.py",
  "relevant_occurrences": [
    {
      "range": [7, 56, 71],
      "symbol": "scip-python ... `django.core.exceptions`/ValidationError#",
      "symbol_roles": 8
    },
    {
      "range": [383, 32, 36],
      "symbol": "scip-python ... `django.core.exceptions`/ValidationError#code.",
      "symbol_roles": 8
    },
    {
      "range": [384, 28, 35],
      "symbol": "scip-python ... `django.core.exceptions`/ValidationError#message.",
      "symbol_roles": 8
    }
  ]
}
```

This already tells us that `django/forms/models.py` imports and uses `ValidationError`, which makes it structurally relevant. But it does not yet tell us whether this is the correct implementation file or merely an adjacent consumer.

#### SQL row with short semantic descriptions

From:

- [localization_study_95_structural_v2_graph.db](/Users/guylevy/Projects/natural-language-index_2/artifacts/localization_study_95_structural_v2_graph.db)

we can query the same issue and obtain compact semantic rows:

```text
symbol_name       symbol_kind  relative_path           start_line  end_line  description
fields_for_model  function     django/forms/models.py  112         198       fields_for_model is a function defined in django/forms/models.py. It participates in this module's local control flow and behavior.
ModelChoiceField  class        django/forms/models.py  1186        1298      ModelChoiceField is a class defined in django/forms/models.py. It groups related behavior and state for this module.
```

This stage illustrates what the original graph hypothesis was trying to achieve: turn structural evidence into a small natural-language summary that can fit comfortably into an issue-time prompt.

#### Developer-style prompt built from the gathered evidence

From:

- [summary_prompt.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/logs/developer_workflow_full95_v2_region/django__django-13933/summary_prompt.md)

the rendered prompt contains:

```md
Problem statement:
ModelChoiceField does not provide value of invalid choice when raising ValidationError

Extracted anchors:
- ModelChoiceField
- ValidationError
- ChoiceField
- invalid_choice

Top symbol/file/grep evidence:
- django/forms/models.py | rank=1 | score=831.0 | tools=symbol_lookup, file_lookup, repo_grep, example_lookup, implementation_trace, workflow_layer_lookup
- django/forms/widgets.py | rank=2 | score=715.0
- django/forms/fields.py | rank=3 | score=630.0
- django/core/exceptions.py | rank=4 | score=571.0

Expanded implementation context:
File: django/forms/models.py
Symbols:
- fields_for_model (function) [112-198]
- ModelChoiceField (class) [1186-1298]
```

The workflow also generated an explicit natural-language implementation story for this issue. In compressed form, that story was:

```md
Likely implementation path:
1. Validation starts in ModelChoiceField and follows the usual field-cleaning path.
2. The invalid_choice error is created from default_error_messages.
3. ModelMultipleChoiceField already includes %(value)s in its invalid_choice message, but ModelChoiceField does not.
4. The likely fix is therefore in django/forms/models.py, where ModelChoiceField defines its invalid_choice message and related validation behavior.
```

This kind of data-flow story is important because it demonstrates what the project was actually trying to give the LLM: not just a ranked file list, but a compact debugging narrative connecting the issue report to the likely implementation path.

This example shows that the project’s core intuition was directionally right: the system works best when it gives the LLM a compact developer-style story rather than a raw repository dump. The key empirical correction is that graph context alone did not reliably generate this story; the successful version required a broader developer workflow.

### 5.6 Runtime and Instrumented Debugging

We later added selective runtime fallback and temporary instrumentation:

- runtime command inference;
- traceback parsing;
- runtime-aware reranking;
- temporary instrumentation patches applied with `git apply`;
- execution and log capture;
- exact patch reversion via `git apply -R`.

These stages are included in the paper as exploratory evidence, not as a validated benchmark win.

## 6. Results

### 6.1 RQ1: Graph-Only Context Is Not Enough

Our old graph-first 95-style baseline is summarized in:

- [localization_study_95_structural_reused_ready.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_study_95_structural_reused_ready.md)

On 93 completed instances, the graph-first pipeline achieved:

- semantic correct file: `40/93 (43.0%)`
- semantic correct function: `33/93 (35.5%)`
- semantic correct fix mechanism: `70/93 (75.3%)`
- semantic localization match: `30/93 (32.3%)`

The gap between fix-mechanism correctness (`75.3%`) and semantic localization (`32.3%`) is the most important number in the graph-only phase. It shows that graph-only retrieval often gave the model enough context to infer the broad class of bug, but not enough to ground that understanding to the correct implementation site.

A concrete example is `django__django-11815`. In that case, the graph-first system correctly inferred a migration-serialization style bug, but its retrieved context still drifted toward adjacent field-related files rather than reliably grounding the issue in `django/db/migrations/serializer.py`. This is exactly the failure mode suggested by the aggregate numbers: broad mechanism understanding without dependable localization.

The graph-only failure taxonomy reinforces this point:

- `51` instances were labeled `retrieval missed correct file`;
- only `39` were labeled `localized successfully`.

This answers RQ1 directly: graph-only structural context was not sufficient as the primary localization strategy.

### 6.2 Intermediate Graph Improvement Did Not Solve Localization

Even after the AST-first v2 graph redesign, graph-only performance remained limited. On the 37-instance v2 subset, the graph-centric pipeline achieved:

- semantic correct fix mechanism: `89.2%`
- semantic localization match: `51.4%`

This was an improvement over the older graph-only baseline, but it still fell short of reliable localization. In other words, fixing graph completeness helped, but did not validate the original strong-form hypothesis.

### 6.3 RQ2: The Static Developer Workflow Materially Outperforms Graph-First Retrieval

Our best static workflow result is summarized in:

- [developer_workflow_full95_v2_region.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_full95_v2_region.md)

On 95 completed instances, the workflow achieved:

- semantic correct file: `78/95 (82.1%)`
- semantic correct function: `66/95 (69.5%)`
- semantic correct fix mechanism: `83/95 (87.4%)`
- semantic localization match: `63/95 (66.3%)`

The cross-phase comparison is the central positive result of the paper:

| System | Sample | Semantic Correct File | Semantic Correct Function | Semantic Correct Fix Mechanism | Semantic Localization Match |
|---|---:|---:|---:|---:|---:|
| Graph-first structural baseline | 93 | 43.0% | 35.5% | 75.3% | 32.3% |
| Tool-first static developer workflow | 95 | 82.1% | 69.5% | 87.4% | 66.3% |

The strongest interpretation is not that graphs are useless. It is that graphs are insufficient on their own, while a broader tool-driven workflow is much stronger.

### 6.4 Where the Gains Came From

The large static workflow gains primarily came from better grounding:

- better file selection;
- better function/region selection;
- better candidate discovery via deterministic tools; and
- better evidence synthesis before LLM reasoning.

This is visible in the workflow’s stage-level metrics:

- exact symbol hit contains gold file: `43/95 (45.3%)`
- grep hit contains gold file: `57/95 (60.0%)`
- merged candidate top-3 contains gold file: `55/95 (57.9%)`
- merged candidate top-5 contains gold file: `60/95 (63.2%)`
- expanded candidate set contains gold file: `72/95 (75.8%)`

These numbers suggest that deterministic evidence gathering and staged comparison were doing much of the practical work. The graph became more useful after the deterministic seeds were already good.

### 6.5 Stack-Trace Subgroup: Graph-Only Still Does Not Become Competitive

One possible objection to the negative graph-only result is that the full benchmark mixes together issues where graph retrieval is naturally weak with issues where graph retrieval should be more helpful. To test this, we ran a deterministic subgroup analysis over issues whose problem statements already contain explicit execution anchors.

The subgroup definition was fixed in advance:

- include an issue if the text contains `Traceback`, a Python stack frame of the form `File "…", line N`, or a test failure header such as `FAIL: ... (...)` or `ERROR: ... (...)`;
- exclude generic mentions of `error` or `exception` without a trace-like structure.

The paired comparison covers 22 issues present in both the graph-first and tool-first reports:

- [stacktrace_subset_comparison.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/stacktrace_subset_comparison.md)

Results:

| Metric | Graph full sample | Graph stack-trace subset | Tool-first full sample | Tool-first stack-trace subset |
|---|---|---|---|---|
| Semantic correct file | 43.0% | 54.5% | 81.7% | 81.8% |
| Semantic correct function | 35.5% | 36.4% | 69.9% | 59.1% |
| Semantic correct fix mechanism | 75.3% | 81.8% | 87.1% | 81.8% |
| Semantic localization match | 32.3% | 36.4% | 66.7% | 54.5% |
| Retrieved top-3 file match | 26.9% | 22.7% | 59.1% | 59.1% |

This subgroup does not rescue the graph-only hypothesis. Graph-only semantic localization rises only modestly from `32.3%` to `36.4%`, while the tool-first workflow remains clearly stronger at `54.5%`.

This is scientifically useful in two ways. First, it rules out the simpler explanation that graph-only failed merely because many issues lacked execution anchors. Second, it sharpens the paper’s claim: even when issues already contain trace-like evidence, graph-first retrieval still underperforms the broader developer workflow.

### 6.6 Remaining Failure Taxonomy in the Static Workflow

Even after the redesign, failures remained. On the 95-instance workflow result, the main remaining buckets were:

- `20` deterministic candidate discovery misses
- `4` comparison preferred wrong file despite good evidence
- `4` file chosen correctly but region selection missed
- `2` issue likely requires runtime execution/reproduction
- `1` LLM summary ignored strong evidence
- `1` selector chose wrong target from correct candidate set

This answers RQ3 in part: the broad static workflow helped substantially, but the hard tail moved from mechanism understanding toward discovery, selection, and runtime-sensitive behavior.

## 7. Runtime and Instrumented Debugging

### 7.1 Naive Runtime Fallback Failed at Benchmark Scale

We first evaluated a selective runtime fallback on a 29-instance failed tail:

- [/tmp/runtime_failed_tail.md](/tmp/runtime_failed_tail.md)

Results were poor:

- static-only semantic localization: `9/29 (31.0%)`
- runtime-augmented semantic localization: `0/29`
- runtime attempted: `16/29`
- runtime produced traceback: `0/29`

This was not evidence that runtime is useless. It was evidence that naive runtime support was too brittle:

- repro command inference was weak;
- commands often failed before reaching the intended path; and
- repository environments were frequently incomplete for targeted CLI or framework-level probes.

### 7.2 Targeted Runtime Improvements Produced Better Signal

After improving command-family inference, interpreter handling, temporary config support, and traceback parsing, we obtained stronger runtime behavior on selected smoke cases. For example, in:

- [/tmp/runtime_smoke_pylint7228_v4.md](/tmp/runtime_smoke_pylint7228_v4.md)

the runtime system:

- inferred a real Pylint CLI invocation;
- materialized a temporary `.pylintrc` from the issue text;
- reached the intended execution path; and
- recovered a meaningful traceback over relevant Pylint files.

The run was still blocked by an environment issue (`ModuleNotFoundError: No module named 'tomlkit'`). This should be interpreted honestly as a weakness of the current runtime setup rather than as evidence about model reasoning alone: in a single manually curated repro, installing the missing package would likely have been straightforward. The reason we keep this example is different. It shows that benchmark-scale runtime support requires a reliable per-repository environment-preparation strategy, not just better prompting. In other words, the failure had moved from "we cannot infer the command" to "we do not yet have robust automated environment bootstrapping."

### 7.3 Instrumented Debugging Was Operationally Feasible

We also implemented temporary instrumentation patches:

- generate patch;
- apply with `git apply`;
- run the repro;
- parse logs;
- revert with `git apply -R`.

The patch lifecycle was safe enough in smoke tests, but instrumentation did not yet produce a benchmark-scale improvement. Its value at this stage is methodological: it points to a plausible next step for a more realistic debugging workflow.

## 8. Discussion

### 8.1 What Failed

The original strong-form hypothesis failed:

- graph context alone did not produce reliable localization;
- even improved graph completeness did not fix the grounding problem; and
- repo-wide semantic enrichment was operationally impractical.

The stack-trace subgroup strengthens this conclusion rather than weakening it. If graph-only retrieval had become competitive on trace-rich issues, the paper’s conclusion would need to be narrowed to "graph context has a strong niche." That is not what happened. The subgroup result is more consistent with a weaker and more defensible claim:

- graph structure is useful as one component of a developer workflow, but insufficient as the primary localization strategy even on issues with explicit execution anchors.

This is the negative result of the paper, and it is central rather than incidental.

### 8.2 What Worked

Three design principles worked well.

**First, deterministic evidence before LLM synthesis.** Exact symbol lookup, file/path lookup, and grep were more valuable than graph retrieval alone.

**Second, file-first comparison before region selection.** For many cases, separating file choice from region choice reduced drift.

**Third, bounded graph expansion after deterministic seeds.** The graph was helpful once good candidates already existed. It was not the right first tool.

**Fourth, developer-style narrative rendering.** The most effective prompts did not look like giant code dumps. They looked like short debugging briefs: issue statement, extracted anchors, ranked candidate files, and bounded implementation context. This preserves part of the original intuition while revising the mechanism that actually made it work.

### 8.3 Why This Matters

The broader implication is that repository-level bug localization should be modeled as a developer workflow rather than as a graph-retrieval problem. Developers do not debug by querying an abstract graph in isolation. They:

- read the issue;
- extract anchors;
- search for symbols and files;
- grep for literals and errors;
- compare plausible files;
- inspect nearby implementation sites; and
- only then synthesize a localization hypothesis.

Our results suggest that software engineering agents benefit from mirroring that process.

## 9. Threats to Validity

This work has several limitations.

### 9.1 Sample Size and Sample Symmetry

The main graph-first baseline used 93 completed instances while the best workflow run used 95. This mismatch is not ideal and should be kept explicit in any submission.

### 9.2 Iterative Tuning

The workflow was improved iteratively, including failed-subset loops. Although the final system is not a bag of issue-specific hard-coded patches, some tuning decisions were informed by repeated miss analysis. This creates some risk of overfitting.

### 9.3 Semantic Evaluation

Several important metrics are semantic rather than exact. This better reflects whether the system identified the right conceptual fix, but it also introduces evaluation subjectivity.

### 9.4 Limited External Baselines

This paper compares graph-first and workflow-based systems developed within the same project. It does not yet provide a strict apples-to-apples comparison against recent external localization systems such as CoSIL on the same exact sample and evaluation protocol.

### 9.5 Runtime Results Are Preliminary

The runtime and instrumentation results are exploratory and should not be over-claimed. They are best interpreted as evidence about the next frontier, not as a validated benchmark contribution.

## 10. Reproducibility and Artifacts

This project emphasizes traceability. The submission version of this paper should therefore be accompanied by a public artifact release containing:

- the frozen sample manifests;
- the graph-first aggregate report and per-instance outputs;
- the static developer-workflow aggregate report and per-instance outputs;
- the stack-trace subgroup extractor and comparison report;
- the retrospective and publication-support notes;
- the scripts used to build reports and subgroup analyses.

For the working draft, these artifacts are referenced as local paths because the paper was written inside the experiment repository. For publication, these links should be replaced by:

- a public GitHub repository for code, manifests, and report-generation scripts; and
- an archived artifact snapshot, for example on Zenodo or a comparable long-term host, for the exact benchmark outputs used in the paper.

Per-instance prompts, summaries, selector decisions, and other detailed logs are valuable for auditability, but they can be released as artifact bundles rather than embedded directly in the paper.

## 11. Conclusion

This paper began as an attempt to show that graph-centric context engineering could dramatically improve LLM bug localization. It did not.

Instead, the experiments support a different conclusion:

- graph-only structural context is not enough;
- a broader static developer workflow materially outperforms graph-first retrieval; and
- runtime and instrumented debugging remain promising but unfinished.

The main lesson is not that graphs are useless. It is that graphs are a supporting tool, not a complete debugging workflow. The strongest gains came from a workflow that combined deterministic search tools with bounded graph expansion and staged LLM synthesis. That result is both practically useful and scientifically important because it reframes the problem: the right unit of abstraction for SWE-bench localization is not just repository structure, but the broader workflow by which developers gather, compare, and validate evidence.

## Acknowledgments

Replace this section with the final acknowledgments, funding statement, and any model/provider disclosure required by the target venue.

## References

[1] Carlos E. Jimenez, John Yang, Alexander Wettig, Shunyu Yao, Kexin Pei, Ofir Press, and Karthik Narasimhan. *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* arXiv:2310.06770, 2023. [https://arxiv.org/abs/2310.06770](https://arxiv.org/abs/2310.06770)

[2] Zhonghao Jiang, Xiaoxue Ren, Meng Yan, Wei Jiang, Yong Li, and Zhongxin Liu. *CoSIL: Software Issue Localization via LLM-Driven Code Repository Graph Searching.* arXiv:2503.22424, 2025. [https://arxiv.org/abs/2503.22424](https://arxiv.org/abs/2503.22424)

[3] Matias Martinez and Xavier Franch. *Dissecting the SWE-Bench Leaderboards: Profiling Submitters and Architectures of LLM- and Agent-Based Repair Systems.* arXiv:2506.17208, 2025. [https://arxiv.org/abs/2506.17208](https://arxiv.org/abs/2506.17208)

[4] Shanchao Liang, Spandan Garg, and Roshanak Zilouchian Moghaddam. *The SWE-Bench Illusion: When State-of-the-Art LLMs Remember Instead of Reason.* arXiv:2506.12286, 2025. [https://arxiv.org/abs/2506.12286](https://arxiv.org/abs/2506.12286)

[5] Tural Mehtiyev and Wesley Assunção. *Beyond Resolution Rates: Behavioral Drivers of Coding Agent Success and Failure.* arXiv:2604.02547, 2026. [https://arxiv.org/abs/2604.02547](https://arxiv.org/abs/2604.02547)
