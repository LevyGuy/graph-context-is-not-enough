# Natural Language Indexer Experiment

## 1. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Install SCIP tooling

```bash
npm install -g @sourcegraph/scip-python
brew install scip
```

If you do not use Homebrew, install the `scip` CLI from the Sourcegraph GitHub releases page.

## 3. Set environment variables

OpenAI for generation plus embeddings:

```bash
export OPENAI_API_KEY=...
export NLI_LLM_PROVIDER=openai
export NLI_LLM_MODEL=gpt-4.1-mini
export NLI_DESCRIPTION_LLM_MODEL=gpt-4o-mini
export NLI_PATCH_LLM_MODEL=gpt-4.1-mini
export NLI_EMBEDDING_PROVIDER=openai
export NLI_EMBEDDING_MODEL=text-embedding-3-small
export NLI_MAX_BUDGET_USD=70
```

Anthropic for generation plus local embeddings:

```bash
export ANTHROPIC_API_KEY=...
export NLI_LLM_PROVIDER=anthropic
export NLI_LLM_MODEL=claude-3-7-sonnet-latest
export NLI_EMBEDDING_PROVIDER=sentence-transformers
export NLI_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

## 4. Prepare the first 10 SWE-bench Lite instances

```bash
python prepare_dataset.py
```

This creates:

- `artifacts/metadata/instances.jsonl`
- `artifacts/workspaces/<instance_id>`

## 5. Build Pipeline A

```bash
python graph_index_pipeline.py
```

This creates `artifacts/enriched_graph.db`.

## 6. Build Pipeline B

```bash
python vector_index_pipeline.py
```

This creates a persistent Chroma database under `artifacts/vector_db/`.

## 7. Generate predictions

```bash
python run_inference.py
```

This creates:

- `predictions_graph.jsonl`
- `predictions_vector.jsonl`
- `artifacts/metadata/graph_context_metrics.jsonl`
- `artifacts/metadata/vector_context_metrics.jsonl`

## 7b. Debug One Instance End To End

```bash
python debug_instance_flow.py \
  --instance-id astropy__astropy-12907 \
  --metadata-path artifacts/metadata/instances_2.jsonl \
  --collection-name swebench_python_chunks_2_instances
```

This writes intermediate artifacts under `artifacts/debug/<instance_id>/`, including:

- problem statement
- retrieved graph symbols
- expanded full-file graph context
- graph summary prompt and summary
- vector context
- final graph-hybrid and vector patch prompts
- returned patches
- a JSON payload with the structured intermediate data

## 8. Run SWE-bench evaluation

```bash
python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Lite --predictions_path predictions_graph.jsonl --max_workers 4 --run_id graph_eval
python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Lite --predictions_path predictions_vector.jsonl --max_workers 4 --run_id vector_eval
```

## 9. Build the comparison report

Replace the evaluation directories below with the actual output locations created by the harness in your environment.

```bash
python generate_report.py \
  --graph-eval-dir evaluation_results/graph_eval \
  --vector-eval-dir evaluation_results/vector_eval
```

The report will be written to `artifacts/reports/comparison_report.md`.
