from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from experiment.config import ensure_directories, load_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the experiment comparison report.")
    parser.add_argument(
        "--graph-eval-dir",
        type=Path,
        required=True,
        help="Evaluation output directory for the graph predictions.",
    )
    parser.add_argument(
        "--vector-eval-dir",
        type=Path,
        required=True,
        help="Evaluation output directory for the vector predictions.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown report output path.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_summary_payload(eval_dir: Path) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for json_path in eval_dir.rglob("*.json"):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            candidates.append(payload)

    for payload in candidates:
        if any(key in payload for key in ("resolved", "resolved_ids", "pass@1", "report")):
            return payload
    raise FileNotFoundError(f"Could not find a recognizable evaluation summary in {eval_dir}.")


def extract_pass_at_1(payload: dict[str, Any]) -> tuple[int, int]:
    if "resolved" in payload and isinstance(payload["resolved"], int):
        total = payload.get("total_instances") or payload.get("total") or 10
        return payload["resolved"], int(total)
    if "resolved_ids" in payload:
        resolved_ids = payload["resolved_ids"]
        total = payload.get("total_instances") or payload.get("total") or len(resolved_ids)
        return len(resolved_ids), int(total)
    if "report" in payload and isinstance(payload["report"], dict):
        return extract_pass_at_1(payload["report"])
    if "pass@1" in payload:
        ratio = float(payload["pass@1"])
        total = payload.get("total_instances") or payload.get("total") or 10
        return round(ratio * int(total)), int(total)
    raise KeyError("No pass@1 summary found.")


def summarize_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "avg_context_chars": mean(row["context_chars"] for row in rows),
        "avg_context_tokens_estimate": mean(row["context_tokens_estimate"] for row in rows),
        "avg_llm_input_tokens": mean(row["llm_input_tokens"] for row in rows),
        "avg_retrieved_items": mean(row["retrieved_items"] for row in rows),
    }


def render_report(
    graph_passed: int,
    graph_total: int,
    vector_passed: int,
    vector_total: int,
    graph_metrics: dict[str, float],
    vector_metrics: dict[str, float],
) -> str:
    graph_pass_rate = graph_passed / graph_total if graph_total else 0
    vector_pass_rate = vector_passed / vector_total if vector_total else 0
    return f"""# Semantic Graph Index vs Vector Search Report

## Pass@1 Comparison

| Pipeline | Resolved | Total | Pass@1 |
| --- | ---: | ---: | ---: |
| Pipeline A (Graph + descriptions) | {graph_passed} | {graph_total} | {graph_pass_rate:.2%} |
| Pipeline B (Vector chunks) | {vector_passed} | {vector_total} | {vector_pass_rate:.2%} |

## Context Size Comparison

| Metric | Pipeline A | Pipeline B | Delta (A - B) |
| --- | ---: | ---: | ---: |
| Avg context chars | {graph_metrics['avg_context_chars']:.1f} | {vector_metrics['avg_context_chars']:.1f} | {graph_metrics['avg_context_chars'] - vector_metrics['avg_context_chars']:.1f} |
| Avg context tokens (estimated) | {graph_metrics['avg_context_tokens_estimate']:.1f} | {vector_metrics['avg_context_tokens_estimate']:.1f} | {graph_metrics['avg_context_tokens_estimate'] - vector_metrics['avg_context_tokens_estimate']:.1f} |
| Avg LLM input tokens | {graph_metrics['avg_llm_input_tokens']:.1f} | {vector_metrics['avg_llm_input_tokens']:.1f} | {graph_metrics['avg_llm_input_tokens'] - vector_metrics['avg_llm_input_tokens']:.1f} |
| Avg retrieved items | {graph_metrics['avg_retrieved_items']:.1f} | {vector_metrics['avg_retrieved_items']:.1f} | {graph_metrics['avg_retrieved_items'] - vector_metrics['avg_retrieved_items']:.1f} |

## Interpretation

Pipeline A supplies structured code blocks plus pre-computed natural-language role descriptions.
Pipeline B supplies raw vector-retrieved chunks without business-logic summaries.

Use the Pass@1 table as the primary outcome metric. The context-size table explains whether any quality difference came with a larger or smaller prompt budget.
"""


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    graph_payload = find_summary_payload(args.graph_eval_dir)
    vector_payload = find_summary_payload(args.vector_eval_dir)
    graph_passed, graph_total = extract_pass_at_1(graph_payload)
    vector_passed, vector_total = extract_pass_at_1(vector_payload)

    graph_metric_rows = load_jsonl(settings.metadata_dir / "graph_context_metrics.jsonl")
    vector_metric_rows = load_jsonl(settings.metadata_dir / "vector_context_metrics.jsonl")
    graph_metrics = summarize_metrics(graph_metric_rows)
    vector_metrics = summarize_metrics(vector_metric_rows)

    report = render_report(
        graph_passed,
        graph_total,
        vector_passed,
        vector_total,
        graph_metrics,
        vector_metrics,
    )
    output_path = args.output or (settings.reports_dir / "comparison_report.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(json.dumps({"report_path": str(output_path)}, indent=2))


if __name__ == "__main__":
    main()
