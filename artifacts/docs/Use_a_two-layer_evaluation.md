Use a two-layer evaluation.

First layer: localization benchmark.  
This is the one we can measure cleanly right now.

For each instance, score:
- did the graph summary identify the correct file?
- did it identify the correct function/class/region?
- did the exact target selector pick the right line neighborhood?

This removes patch-generation noise and directly tests your core thesis:
- “Does the graph provide better debugging context than raw vector search?”

Second layer: repair benchmark.  
Only after localization is measured separately.

For both graph and baseline:
- use the same patch-shape control
- same final model
- same diff validation
- same evaluation harness

That isolates retrieval/context quality from patch formatting/model drift.

What I’d implement next:

1. Build a labeled mini eval set
- start with 20 instances from the repos already indexed
- for each instance, record:
  - gold file
  - optional gold function/region
- we can derive many of these from SWE-bench patches or manually annotate a smaller subset

2. Add localization metrics
- `top1_file_match`
- `top3_file_match`
- `target_line_within_gold_region`
- `summary_mentions_gold_file`

3. Freeze one graph pipeline
- graph retrieval
- file mention recovery
- no vector
- exact-line or bounded-edit patching only

4. Freeze one baseline
- vector retrieval only
- same final patching controller
- same final model

5. Run in stages
- Stage A: localization only on 20 instances
- Stage B: full SWE-bench on the same 20
- compare:
  - file-hit rate
  - patch apply rate
  - resolved rate

Why this is the right next step:
- if graph wins localization but not resolution, you know patch synthesis is the bottleneck
- if graph doesn’t win localization, the thesis is weaker
- if graph wins both, then you have real benchmark evidence

My recommendation for the immediate next concrete task:
- implement a `localization_eval.py` script
- run it on 10 to 20 already indexed instances first

That will give us a much cleaner answer than continuing to rely only on SWE-bench resolved/unresolved.

If you want, I can build that script now and define the annotation format.