# Research Update: Hypothesis, Concrete Workflow Example, and Current Results

## Purpose

This note updates the research narrative with three things that were missing or under-specified in the earlier draft:

1. a clearer explanation of the original hypothesis and why it was plausible;
2. one concrete end-to-end example showing how the workflow moved from structural index data to graph rows to a developer-style prompt;
3. the current quantitative state of the research, including the new stack-trace subgroup analysis.

This note is intended to be used alongside:

- [GRAPH_CONTEXT_IS_NOT_ENOUGH_ARXIV_DRAFT.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/GRAPH_CONTEXT_IS_NOT_ENOUGH_ARXIV_DRAFT.md)
- [GRAPH_AND_WORKFLOW_RETROSPECTIVE.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/GRAPH_AND_WORKFLOW_RETROSPECTIVE.md)
- [stacktrace_subset_comparison.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/stacktrace_subset_comparison.md)

## 1. Original Hypothesis and Why It Seemed Plausible

The original working hypothesis was stronger than "graphs might help a little." It was:

> If we can replace a large, noisy repository context with a small, accurate structural story of the issue, an LLM should localize and solve the issue more reliably.

There were two main reasons to believe this.

### 1.1 Small, accurate context should beat large, noisy context

The practical intuition was that large context windows degrade useful reasoning when most of the supplied tokens are irrelevant. In repository-level debugging, a model is often exposed to:

- too many files;
- too much local code with no causal connection to the bug;
- too much lexical overlap from adjacent modules;
- too much surface-level similarity between callers, wrappers, helpers, and the true implementation site.

The hypothesis was therefore that a structural graph could act as a compression mechanism. Instead of feeding raw repository text, we would feed:

- the relevant symbols;
- ownership relations;
- imports and references;
- nearby control-flow-relevant blocks; and
- a short natural-language description of those structures.

The expected benefit was not just fewer tokens. It was a better ratio of signal to noise.

### 1.2 A developer-style natural-language story should fit how LLMs reason

The second intuition was about the model itself. LLMs are trained on natural language and code, and they often perform best when the task is expressed as a coherent story instead of a pile of unrelated code fragments.

The expected chain was:

1. deterministic tools recover structural facts;
2. those facts are translated into a compact developer-style explanation;
3. the explanation tells the model what kind of issue this is, what code path matters, and which files are likely relevant;
4. the model then uses that story to choose the implementation site and propose the fix.

In other words, the strong form of the hypothesis was:

- graph structure would let us compress the repository into a small, accurate issue narrative;
- and that narrative would be easier for the LLM to act on than raw large-context code dumps.

This was a reasonable hypothesis. The experiments show that it was also incomplete.

## 2. What the Experiments Actually Showed

The experiments support a narrower conclusion than the original hypothesis.

### 2.1 What did work

- Structural information is useful.
- A compact issue narrative is useful.
- File-first reasoning is useful.
- Developer-workflow evidence is much better than graph context alone.

### 2.2 What did not work

- Graph context alone was not enough to reliably ground the issue to the correct file and function.
- The graph-only system often understood the broad mechanism of the bug without locating the true implementation site.
- Even on issues containing stack traces, graph-first retrieval did not become competitive with the broader workflow.

So the updated interpretation is:

> The key idea was directionally right, but the retrieval mechanism was too narrow. A small accurate story helps, but the best story was not produced by graph context alone. It emerged only when structural context was combined with a broader developer workflow.

## 3. Concrete Example of the Workflow

This section shows one real issue, `django__django-13933`, to make the workflow concrete.

The issue is:

- `ModelChoiceField` does not include the invalid value when raising `ValidationError`.

This case is useful because it exposes the whole intended pipeline:

1. structural index evidence;
2. graph/SQL evidence with short descriptions;
3. a developer-style prompt built from that evidence.

### 3.1 Example A: SCIP-style structural index evidence

Source:

- [index.json](/Users/guylevy/Projects/natural-language-index_2/artifacts/indexes/django__django-13933/index.json)

Relevant occurrence sample from `django/forms/models.py`:

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

Why this mattered:

- the structural index shows that `django/forms/models.py` imports and uses `ValidationError`;
- that already makes it more plausible than a random neighboring file;
- but by itself, it still does not tell us whether this is the exact implementation site or just an adjacent consumer.

This captures the central limitation of the graph-first idea: structure alone narrows the space, but often does not finish the job.

### 3.2 Example B: SQL rows from the structural graph with LLM descriptions

Source graph DB:

- [localization_study_95_structural_v2_graph.db](/Users/guylevy/Projects/natural-language-index_2/artifacts/localization_study_95_structural_v2_graph.db)

Representative SQL result for the same issue:

```text
symbol_name       symbol_kind  relative_path           start_line  end_line  description
fields_for_model  function     django/forms/models.py  112         198       fields_for_model is a function defined in django/forms/models.py. It participates in this module's local control flow and behavior.
ModelChoiceField  class        django/forms/models.py  1186        1298      ModelChoiceField is a class defined in django/forms/models.py. It groups related behavior and state for this module.
```

Why this mattered:

- the graph DB converted raw structural nodes into a short semantic summary;
- the summary is compact enough to fit into an issue-time prompt;
- the system can now say more than "this file mentions the symbol" and instead say "this file defines the relevant class."

This was the intended bridge from deterministic structure to LLM-readable explanation.

### 3.3 Example C: Developer-style prompt built from the evidence

Source:

- [summary_prompt.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/logs/developer_workflow_full95_v2_region/django__django-13933/summary_prompt.md)

Compressed excerpt:

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

This is the clearest example of the intended workflow:

1. the issue text provides explicit anchors;
2. deterministic tools recover candidate files and symbols;
3. graph expansion adds nearby implementation structure;
4. the final prompt is not a giant repository dump but a short developer-style story.

That part of the hypothesis was correct. The key mistake was assuming that graph-derived context by itself would be enough to produce this story reliably.

## 4. Current Quantitative State of the Research

## 4.1 Graph-first baseline

Source:

- [localization_study_95_structural_reused_ready.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_study_95_structural_reused_ready.md)

Completed instances:

- `93`

Main results:

- semantic correct file: `40/93 (43.0%, 95% CI 33.4-53.2%)`
- semantic correct function: `33/93 (35.5%, 95% CI 26.5-45.6%)`
- semantic correct fix mechanism: `70/93 (75.3%, 95% CI 65.6-82.9%)`
- semantic localization match: `30/93 (32.3%, 95% CI 23.6-42.3%)`

Most important interpretation:

- graph-only could often infer the bug class;
- graph-only could not reliably ground that understanding to the right implementation site.

The key gap is:

- `75.3%` correct mechanism versus `32.3%` semantic localization.

That gap is the strongest evidence against the original strong-form graph-only hypothesis.

## 4.2 Best static developer-workflow baseline

Source:

- [developer_workflow_full95_v2_region.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_full95_v2_region.md)

Completed instances:

- `95`

Main results:

- semantic correct file: `78/95 (82.1%, 95% CI 73.2-88.5%)`
- semantic correct function: `66/95 (69.5%, 95% CI 59.6-77.8%)`
- semantic correct fix mechanism: `83/95 (87.4%, 95% CI 79.2-92.6%)`
- semantic localization match: `63/95 (66.3%, 95% CI 56.3-75.0%)`

Important stage metrics:

- exact symbol hit contains gold file: `43/95 (45.3%)`
- grep hit contains gold file: `57/95 (60.0%)`
- merged candidate top-3 contains gold file: `55/95 (57.9%)`
- expanded candidate set contains gold file: `72/95 (75.8%)`
- file comparison top-1 is gold: `53/95 (55.8%)`

Interpretation:

- a broader developer workflow more than doubled semantic localization relative to the graph-first baseline;
- the large gain came from better grounding, not merely better high-level diagnosis.

### 4.3 Large-sample comparison

| System | Sample | Semantic Correct File | Semantic Correct Function | Semantic Correct Fix Mechanism | Semantic Localization Match |
|---|---:|---:|---:|---:|---:|
| Graph-first structural baseline | 93 | 43.0% | 35.5% | 75.3% | 32.3% |
| Tool-first static developer workflow | 95 | 82.1% | 69.5% | 87.4% | 66.3% |

This is the central result of the project so far.

## 4.4 Stack-trace subgroup analysis

Source:

- [stacktrace_subset_comparison.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/stacktrace_subset_comparison.md)

Paired subset size:

- `22`

Results on the exact same stack-trace-defined subset:

| Metric | Graph-only stack-trace subset | Tool-first stack-trace subset |
|---|---:|---:|
| Semantic correct file | 54.5% | 81.8% |
| Semantic correct function | 36.4% | 59.1% |
| Semantic correct fix mechanism | 81.8% | 81.8% |
| Semantic localization match | 36.4% | 54.5% |
| Retrieved top-3 file match | 22.7% | 59.1% |
| Final target in gold file | 40.9% | 50.0% |
| Weak graph/workflow found issue | 50.0% | 77.3% |

Most important comparison:

- graph full sample semantic localization: `32.3%`
- graph stack-trace subset semantic localization: `36.4%`
- tool-first stack-trace subset semantic localization: `54.5%`

Interpretation:

- stack traces do not rescue graph-only retrieval;
- graph-only gets only a small improvement on trace-rich issues;
- the tool-first workflow remains clearly stronger even when the issue already contains execution anchors.

This strengthens rather than weakens the negative claim:

> Graph context alone is insufficient even on a favorable subgroup where execution anchors are already present in the issue description.

## 4.5 Runtime and instrumented debugging status

Runtime and instrumentation are still exploratory rather than benchmark-positive.

Source:

- [/tmp/runtime_failed_tail.md](/tmp/runtime_failed_tail.md)

On a 29-instance failed-tail runtime study:

- static-only semantic localization: `9/29 (31.0%)`
- runtime-augmented semantic localization: `0/29`
- final semantic localization: `9/29 (31.0%)`
- runtime attempted: `16/29`
- runtime succeeded: `0/29`
- runtime produced traceback: `0/29`

Interpretation:

- naive runtime fallback did not help;
- the main problem was not runtime-aware ranking, but failing to reach the correct execution path and environment reliably.

This is still useful for the paper because it supports a precise claim:

> Runtime debugging looks like the next frontier, but naive command inference is too brittle to claim gains yet.

## 5. Revised Research Position

The most defensible research position now is:

1. The original strong-form hypothesis was false.
   - Graph context alone did not reliably produce the right localization context.

2. The underlying intuition was still partly right.
   - Small, accurate context is better than large, noisy context.
   - Developer-style natural-language narratives are useful for LLM reasoning.

3. The missing ingredient was breadth of workflow, not merely better graph structure.
   - The best results came from combining:
     - anchor extraction;
     - exact symbol and file search;
     - grep;
     - bounded graph expansion;
     - file-first comparison;
     - staged region selection.

4. The stack-trace subgroup strengthens the paper.
   - It shows that even on issues that already contain execution anchors, graph-only retrieval does not become competitive.

5. The next open problem is runtime debugging.
   - but the current runtime evidence is not yet strong enough to headline as a success.

## 6. Suggested Paper-Level Wording

If this note is folded into the paper, the core message should be framed like this:

> We began with the hypothesis that a structural repository graph could compress the repository into a small, accurate issue narrative and thereby improve LLM localization. The experiments partially validated the importance of compact developer-style context, but rejected the strong graph-only version of the hypothesis. Graph context alone was not enough. A broader static developer workflow more than doubled semantic localization, and a stack-trace subgroup analysis showed that graph-first retrieval remained substantially weaker even on issues already containing execution anchors.

## 7. Recommended Files to Cite Together

For paper revision or external consultation, the most useful file bundle is:

- [GRAPH_CONTEXT_IS_NOT_ENOUGH_ARXIV_DRAFT.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/GRAPH_CONTEXT_IS_NOT_ENOUGH_ARXIV_DRAFT.md)
- [RESEARCH_UPDATE_HYPOTHESIS_EXAMPLES_AND_CURRENT_STATE.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/RESEARCH_UPDATE_HYPOTHESIS_EXAMPLES_AND_CURRENT_STATE.md)
- [stacktrace_subset_comparison.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/stacktrace_subset_comparison.md)
- [STACKTRACE_SUBGROUP_INTERPRETATION.md](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/STACKTRACE_SUBGROUP_INTERPRETATION.md)
