# Stack-Trace Subgroup Comparison

## Summary

- Metadata source: [localization_study_95_structural_reused_ready.jsonl](/Users/guylevy/Projects/natural-language-index_2/artifacts/metadata/localization_study_95_structural_reused_ready.jsonl)
- Graph baseline: [localization_study_95_structural_reused_ready.json](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/localization_study_95_structural_reused_ready.json)
- Tool-first baseline: [developer_workflow_full95_v2_region.json](/Users/guylevy/Projects/natural-language-index_2/artifacts/reports/developer_workflow_full95_v2_region.json)
- Stack-trace subgroup size on the paired intersection: `22`

## Stack-Trace Definition

- Include an issue if its problem statement contains any of:
  - `Traceback (most recent call last):` or `Traceback`
  - a Python stack frame of the form `File "…", line N`
  - a test failure header of the form `FAIL: ... (...)` or `ERROR: ... (...)`
- Exclude generic mentions of `error` or `exception` without a trace-like structure.

## Publication-Safe Interpretation

- Graph-only does not improve materially on stack-trace issues relative to its full-sample result. This strengthens the negative claim that graph context alone is insufficient even when issues include execution anchors.
- The tool-first workflow remains clearly stronger than graph-only on the stack-trace subset, so stack traces do not make graph-only retrieval competitive with the broader workflow.

## Main Comparison

| Metric | Graph full sample | Graph stack-trace subset | Tool-first full sample | Tool-first stack-trace subset |
|---|---|---|---|---|
| Semantic correct file | 40/93 (43.0%, 95% CI 33.4-53.2%) | 12/22 (54.5%, 95% CI 34.7-73.1%) | 76/93 (81.7%, 95% CI 72.7-88.3%) | 18/22 (81.8%, 95% CI 61.5-92.7%) |
| Semantic correct function | 33/93 (35.5%, 95% CI 26.5-45.6%) | 8/22 (36.4%, 95% CI 19.7-57.0%) | 65/93 (69.9%, 95% CI 59.9-78.3%) | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Semantic correct fix mechanism | 70/93 (75.3%, 95% CI 65.6-82.9%) | 18/22 (81.8%, 95% CI 61.5-92.7%) | 81/93 (87.1%, 95% CI 78.8-92.5%) | 18/22 (81.8%, 95% CI 61.5-92.7%) |
| Semantic localization match | 30/93 (32.3%, 95% CI 23.6-42.3%) | 8/22 (36.4%, 95% CI 19.7-57.0%) | 62/93 (66.7%, 95% CI 56.6-75.4%) | 12/22 (54.5%, 95% CI 34.7-73.1%) |
| Retrieved top-3 file match | 25/93 (26.9%, 95% CI 18.9-36.7%) | 5/22 (22.7%, 95% CI 10.1-43.4%) | 55/93 (59.1%, 95% CI 49.0-68.6%) | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Merged candidate top-3 contains gold file | N/A | N/A | 55/93 (59.1%, 95% CI 49.0-68.6%) | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Final target in gold file | 26/93 (28.0%, 95% CI 19.9-37.8%) | 9/22 (40.9%, 95% CI 23.3-61.3%) | 53/93 (57.0%, 95% CI 46.8-66.6%) | 11/22 (50.0%, 95% CI 30.7-69.3%) |
| Weak graph/workflow found issue | 39/93 (41.9%, 95% CI 32.4-52.1%) | 11/22 (50.0%, 95% CI 30.7-69.3%) | 74/93 (79.6%, 95% CI 70.3-86.5%) | 17/22 (77.3%, 95% CI 56.6-89.9%) |

## Delta: Graph full sample → graph stack-trace subset

| Metric | Delta |
|---|---|
| Semantic correct file | +11.5 pts |
| Semantic correct function | +0.9 pts |
| Semantic correct fix mechanism | +6.5 pts |
| Semantic localization match | +4.1 pts |
| Retrieved top-3 file match | -4.2 pts |
| Merged candidate top-3 contains gold file | N/A |
| Final target in gold file | +13.0 pts |
| Weak graph/workflow found issue | +8.1 pts |

## Delta: Tool-first full sample → tool-first stack-trace subset

| Metric | Delta |
|---|---|
| Semantic correct file | +0.1 pts |
| Semantic correct function | -10.8 pts |
| Semantic correct fix mechanism | -5.3 pts |
| Semantic localization match | -12.1 pts |
| Retrieved top-3 file match | -0.0 pts |
| Merged candidate top-3 contains gold file | -0.0 pts |
| Final target in gold file | -7.0 pts |
| Weak graph/workflow found issue | -2.3 pts |

## Delta: Graph stack-trace subset → tool-first stack-trace subset

| Metric | Delta |
|---|---|
| Semantic correct file | +27.3 pts |
| Semantic correct function | +22.7 pts |
| Semantic correct fix mechanism | +0.0 pts |
| Semantic localization match | +18.2 pts |
| Retrieved top-3 file match | +36.4 pts |
| Merged candidate top-3 contains gold file | N/A |
| Final target in gold file | +9.1 pts |
| Weak graph/workflow found issue | +27.3 pts |

## Graph Stack-Trace Subset

| Metric | Value |
|---|---|
| Semantic correct file | 12/22 (54.5%, 95% CI 34.7-73.1%) |
| Semantic correct function | 8/22 (36.4%, 95% CI 19.7-57.0%) |
| Semantic correct fix mechanism | 18/22 (81.8%, 95% CI 61.5-92.7%) |
| Semantic localization match | 8/22 (36.4%, 95% CI 19.7-57.0%) |
| Retrieved top-3 file match | 5/22 (22.7%, 95% CI 10.1-43.4%) |
| Merged candidate top-3 contains gold file | N/A |
| Final target in gold file | 9/22 (40.9%, 95% CI 23.3-61.3%) |
| Weak graph/workflow found issue | 11/22 (50.0%, 95% CI 30.7-69.3%) |

### Repo Breakdown

| Repo | Sample | Semantic Localization | Weak Graph/Workflow Found Issue |
|---|---:|---|---|
| astropy/astropy | 1 | 1/1 (100.0%, 95% CI 20.7-100.0%) | 1/1 (100.0%, 95% CI 20.7-100.0%) |
| django/django | 9 | 1/9 (11.1%, 95% CI 2.0-43.5%) | 3/9 (33.3%, 95% CI 12.1-64.6%) |
| matplotlib/matplotlib | 5 | 3/5 (60.0%, 95% CI 23.1-88.2%) | 3/5 (60.0%, 95% CI 23.1-88.2%) |
| mwaskom/seaborn | 2 | 0/2 (0.0%, 95% CI 0.0-65.8%) | 1/2 (50.0%, 95% CI 9.5-90.5%) |
| pylint-dev/pylint | 1 | 0/1 (0.0%, 95% CI 0.0-79.3%) | 0/1 (0.0%, 95% CI 0.0-79.3%) |
| pytest-dev/pytest | 1 | 0/1 (0.0%, 95% CI 0.0-79.3%) | 0/1 (0.0%, 95% CI 0.0-79.3%) |
| scikit-learn/scikit-learn | 3 | 3/3 (100.0%, 95% CI 43.8-100.0%) | 3/3 (100.0%, 95% CI 43.8-100.0%) |

### Failure Taxonomy

| Bucket | Count |
|---|---:|
| localized successfully | 11 |
| retrieval missed correct file | 10 |
| summary understood issue but named wrong implementation site | 1 |

## Tool-First Stack-Trace Subset

| Metric | Value |
|---|---|
| Semantic correct file | 18/22 (81.8%, 95% CI 61.5-92.7%) |
| Semantic correct function | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Semantic correct fix mechanism | 18/22 (81.8%, 95% CI 61.5-92.7%) |
| Semantic localization match | 12/22 (54.5%, 95% CI 34.7-73.1%) |
| Retrieved top-3 file match | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Merged candidate top-3 contains gold file | 13/22 (59.1%, 95% CI 38.7-76.7%) |
| Final target in gold file | 11/22 (50.0%, 95% CI 30.7-69.3%) |
| Weak graph/workflow found issue | 17/22 (77.3%, 95% CI 56.6-89.9%) |

### Repo Breakdown

| Repo | Sample | Semantic Localization | Weak Graph/Workflow Found Issue |
|---|---:|---|---|
| astropy/astropy | 1 | 1/1 (100.0%, 95% CI 20.7-100.0%) | 1/1 (100.0%, 95% CI 20.7-100.0%) |
| django/django | 9 | 3/9 (33.3%, 95% CI 12.1-64.6%) | 7/9 (77.8%, 95% CI 45.3-93.7%) |
| matplotlib/matplotlib | 5 | 3/5 (60.0%, 95% CI 23.1-88.2%) | 4/5 (80.0%, 95% CI 37.6-96.4%) |
| mwaskom/seaborn | 2 | 2/2 (100.0%, 95% CI 34.2-100.0%) | 2/2 (100.0%, 95% CI 34.2-100.0%) |
| pylint-dev/pylint | 1 | 0/1 (0.0%, 95% CI 0.0-79.3%) | 0/1 (0.0%, 95% CI 0.0-79.3%) |
| pytest-dev/pytest | 1 | 0/1 (0.0%, 95% CI 0.0-79.3%) | 0/1 (0.0%, 95% CI 0.0-79.3%) |
| scikit-learn/scikit-learn | 3 | 3/3 (100.0%, 95% CI 43.8-100.0%) | 3/3 (100.0%, 95% CI 43.8-100.0%) |

### Failure Taxonomy

| Bucket | Count |
|---|---:|
| localized successfully | 12 |
| deterministic candidate discovery missed correct file | 5 |
| file chosen correctly but region selection missed | 2 |
| comparison preferred wrong file despite good evidence | 2 |
| issue likely requires runtime execution/reproduction | 1 |

## Included Instances

| Instance | Repo | Matched Rules |
|---|---|---|
| astropy__astropy-7746 | astropy/astropy | traceback_header |
| django__django-11964 | django/django | traceback_header, python_frame, test_failure_header |
| django__django-14016 | django/django | traceback_header |
| django__django-14017 | django/django | traceback_header |
| django__django-14238 | django/django | traceback_header, python_frame |
| django__django-14580 | django/django | python_frame |
| django__django-14672 | django/django | python_frame |
| django__django-15781 | django/django | traceback_header |
| django__django-16408 | django/django | traceback_header, python_frame, test_failure_header |
| django__django-16873 | django/django | traceback_header, python_frame, test_failure_header |
| matplotlib__matplotlib-22711 | matplotlib/matplotlib | python_frame |
| matplotlib__matplotlib-23299 | matplotlib/matplotlib | traceback_header |
| matplotlib__matplotlib-24265 | matplotlib/matplotlib | traceback_header |
| matplotlib__matplotlib-25442 | matplotlib/matplotlib | traceback_header, python_frame |
| matplotlib__matplotlib-25498 | matplotlib/matplotlib | traceback_header, python_frame |
| mwaskom__seaborn-2848 | mwaskom/seaborn | traceback_header |
| mwaskom__seaborn-3010 | mwaskom/seaborn | traceback_header |
| pylint-dev__pylint-7228 | pylint-dev/pylint | traceback_header, python_frame |
| pytest-dev__pytest-7168 | pytest-dev/pytest | traceback_header, python_frame |
| scikit-learn__scikit-learn-10508 | scikit-learn/scikit-learn | traceback_header, python_frame |
| scikit-learn__scikit-learn-10949 | scikit-learn/scikit-learn | traceback_header |
| scikit-learn__scikit-learn-14894 | scikit-learn/scikit-learn | traceback_header, python_frame |
