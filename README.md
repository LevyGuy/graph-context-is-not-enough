# Graph Context Is Not Enough

Code and publication-facing artifacts for a study of repository-level bug localization on a SWE-bench-style benchmark.

Repository:

- [github.com/LevyGuy/graph-context-is-not-enough](https://github.com/LevyGuy/graph-context-is-not-enough)

## What This Repository Contains

This repository contains:

- graph-first localization pipelines;
- static tool-first developer-workflow pipelines;
- runtime and instrumented-debugging prototypes;
- subgroup-analysis scripts;
- publication-facing reports and paper drafts.

The core research question is whether a structural code graph by itself can provide enough context for an LLM to localize software issues accurately, and how that compares to a broader developer workflow built from deterministic tools plus LLM synthesis.

## Main Finding

The current headline result is:

- graph-first baseline semantic localization: `30/93 (32.3%)`
- best static developer-workflow semantic localization: `63/95 (66.3%)`

We also ran a deterministic stack-trace subgroup study:

- graph-only stack-trace subset semantic localization: `8/22 (36.4%)`
- tool-first stack-trace subset semantic localization: `12/22 (54.5%)`

The current evidence supports a narrower conclusion than the original hypothesis:

- compact, developer-style context helps;
- graph context alone is not enough;
- a broader tool-first workflow is much stronger than graph-first retrieval.

## Repository Layout

Top-level code:

- [developer_workflow.py](developer_workflow.py): static workflow retrieval, merge, comparison, and selection logic
- [developer_workflow_localization.py](developer_workflow_localization.py): benchmark runner and reporting entry point
- [graph_index_pipeline.py](graph_index_pipeline.py): structural indexing pipeline
- [runtime_repro.py](runtime_repro.py): runtime fallback and repro command inference
- [instrumented_runtime.py](instrumented_runtime.py): temporary instrumentation planning, patching, and log parsing
- [analyze_stacktrace_subset.py](analyze_stacktrace_subset.py): deterministic stack-trace subgroup extraction and paired comparison

Support code:

- [experiment](experiment): shared config, budget, dataset, and LLM-client utilities
- [artifacts/docs](artifacts/docs): internal planning and evaluation notes that remain useful as project documentation

Publication-facing reports:

- [paper draft](artifacts/reports/GRAPH_CONTEXT_IS_NOT_ENOUGH_ARXIV_DRAFT.md)
- [retrospective](artifacts/reports/GRAPH_AND_WORKFLOW_RETROSPECTIVE.md)
- [publication assessment](artifacts/reports/MIDPOINT_PUBLICATION_ASSESSMENT.md)
- [research update](artifacts/reports/RESEARCH_UPDATE_HYPOTHESIS_EXAMPLES_AND_CURRENT_STATE.md)
- [graph-first baseline report](artifacts/reports/localization_study_95_structural_reused_ready.md)
- [tool-first baseline report](artifacts/reports/developer_workflow_full95_v2_region.md)
- [stack-trace subgroup report](artifacts/reports/stacktrace_subset_comparison.md)

Publication-facing metadata:

- [paired 95-style metadata](artifacts/metadata/localization_study_95_structural_reused_ready.jsonl)
- [stack-trace subset rows](artifacts/metadata/stacktrace_subset.jsonl)
- [stack-trace subset manifest](artifacts/metadata/stacktrace_subset_manifest.json)

## What Is Intentionally Omitted From GitHub

This repository intentionally does **not** publish large or local-only working state such as:

- repository indexes;
- local graph databases and journal files;
- expanded workspaces for benchmark instances;
- local execution logs;
- vector stores;
- LLM conversation transcripts used during exploration;
- council/advisory notes from side discussions.

Those files were useful during development but are not required for understanding the code or the paper, and many are too large or too noisy for a public source repository.

## Reproducibility Plan

The repository is structured so that source code, key metadata manifests, and publication-facing reports are available here, while large benchmark outputs can be released separately as an artifact bundle.

Recommended publication setup:

1. GitHub for source code, scripts, manifests, and paper materials.
2. Zenodo or similar archive for frozen large artifacts and exact benchmark outputs.

## Citation

This repository includes:

- [CITATION.cff](CITATION.cff)
- [LICENSE](LICENSE)

If you reference the code in a paper or artifact evaluation, cite the repository and, once available, the frozen artifact DOI.

## Setup

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Notes

- Some experimental runtime flows still require per-repository environment setup and are not yet turnkey.
- Runtime and instrumentation results are exploratory; the static workflow results are the benchmark-backed centerpiece of the current research state.
- The repository is released under the MIT License.
