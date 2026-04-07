from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TRACEBACK_HEADER_RE = re.compile(r"traceback(?:\s+\(most recent call last\):)?", re.IGNORECASE)
PYTHON_FRAME_RE = re.compile(r'File\s+"[^"]+",\s+line\s+\d+', re.IGNORECASE)
TEST_FAILURE_RE = re.compile(r"(?m)^(FAIL|ERROR):\s+.+\(.+\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze the stack-trace subgroup across frozen graph and workflow reports.")
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=Path("artifacts/metadata/localization_study_95_structural_reused_ready.jsonl"),
    )
    parser.add_argument(
        "--graph-results",
        type=Path,
        default=Path("artifacts/reports/localization_study_95_structural_reused_ready.json"),
    )
    parser.add_argument(
        "--workflow-results",
        type=Path,
        default=Path("artifacts/reports/developer_workflow_full95_v2_region.json"),
    )
    parser.add_argument(
        "--output-metadata-dir",
        type=Path,
        default=Path("artifacts/metadata"),
    )
    parser.add_argument(
        "--output-reports-dir",
        type=Path,
        default=Path("artifacts/reports"),
    )
    parser.add_argument(
        "--study-name",
        type=str,
        default="stacktrace_subset",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + (z**2 / total)
    center = (p + (z**2 / (2 * total))) / denominator
    margin = z * math.sqrt((p * (1 - p) / total) + (z**2 / (4 * total**2))) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def format_rate(count: int, total: int) -> str:
    pct = (count / total * 100) if total else 0.0
    lower, upper = wilson_interval(count, total)
    return f"{count}/{total} ({pct:.1f}%, 95% CI {lower * 100:.1f}-{upper * 100:.1f}%)"


def count_metric(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key))


def classify_stacktrace(problem_statement: str) -> dict[str, Any]:
    has_traceback_header = bool(TRACEBACK_HEADER_RE.search(problem_statement))
    has_python_frame = bool(PYTHON_FRAME_RE.search(problem_statement))
    has_test_failure_header = bool(TEST_FAILURE_RE.search(problem_statement))
    matched_rules: list[str] = []
    if has_traceback_header:
        matched_rules.append("traceback_header")
    if has_python_frame:
        matched_rules.append("python_frame")
    if has_test_failure_header:
        matched_rules.append("test_failure_header")
    return {
        "has_traceback_header": has_traceback_header,
        "has_python_frame": has_python_frame,
        "has_test_failure_header": has_test_failure_header,
        "matched_stacktrace_rule": matched_rules,
        "is_stacktrace_issue": bool(matched_rules),
    }


def repo_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["repo_name"])].append(row)
    breakdown: list[dict[str, Any]] = []
    for repo_name, repo_rows in sorted(grouped.items()):
        breakdown.append(
            {
                "repo_name": repo_name,
                "sample_size": len(repo_rows),
                "semantic_localization_match": format_rate(count_metric(repo_rows, "semantic_localization_match"), len(repo_rows)),
                "weak_found_issue": format_rate(count_metric(repo_rows, "weak_graph_found_issue"), len(repo_rows)),
            }
        )
    return breakdown


def taxonomy_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("failure_taxonomy", "unknown")) for row in rows)
    return [{"bucket": bucket, "count": count} for bucket, count in counts.most_common()]


def metric_summary(rows: list[dict[str, Any]], metric_map: list[tuple[str, str]]) -> list[dict[str, Any]]:
    total = len(rows)
    summary: list[dict[str, Any]] = []
    for key, label in metric_map:
        if key == "merged_candidate_top3_contains_gold_file" and not any(key in row for row in rows):
            summary.append({"key": key, "label": label, "available": False, "formatted": "N/A"})
            continue
        summary.append(
            {
                "key": key,
                "label": label,
                "available": True,
                "count": count_metric(rows, key),
                "formatted": format_rate(count_metric(rows, key), total),
            }
        )
    return summary


def build_subset_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_map = [
        ("semantic_correct_file", "Semantic correct file"),
        ("semantic_correct_function", "Semantic correct function"),
        ("semantic_correct_fix_mechanism", "Semantic correct fix mechanism"),
        ("semantic_localization_match", "Semantic localization match"),
        ("retrieved_top3_file_match", "Retrieved top-3 file match"),
        ("merged_candidate_top3_contains_gold_file", "Merged candidate top-3 contains gold file"),
        ("target_in_gold_file", "Final target in gold file"),
        ("weak_graph_found_issue", "Weak graph/workflow found issue"),
    ]
    return {
        "sample_size": len(rows),
        "metrics": metric_summary(rows, metric_map),
        "repo_breakdown": repo_breakdown(rows),
        "failure_taxonomy": taxonomy_breakdown(rows),
    }


def index_by_instance(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["instance_id"]): row for row in rows}


def metrics_as_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in summary["metrics"]}


def derive_delta(from_label: str, to_label: str, from_summary: dict[str, Any], to_summary: dict[str, Any]) -> list[dict[str, Any]]:
    from_map = metrics_as_map(from_summary)
    to_map = metrics_as_map(to_summary)
    deltas: list[dict[str, Any]] = []
    for key, from_entry in from_map.items():
        to_entry = to_map.get(key)
        if not to_entry or not from_entry.get("available") or not to_entry.get("available"):
            deltas.append({"key": key, "label": from_entry["label"], "delta": "N/A"})
            continue
        deltas.append(
            {
                "key": key,
                "label": from_entry["label"],
                "from": from_entry["formatted"],
                "to": to_entry["formatted"],
                "delta": _delta_string(from_entry["count"], from_summary["sample_size"], to_entry["count"], to_summary["sample_size"]),
            }
        )
    return deltas


def _delta_string(from_count: int, from_total: int, to_count: int, to_total: int) -> str:
    from_rate = (from_count / from_total * 100) if from_total else 0.0
    to_rate = (to_count / to_total * 100) if to_total else 0.0
    diff = to_rate - from_rate
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.1f} pts"


def build_interpretation(graph_subset: dict[str, Any], workflow_subset: dict[str, Any], graph_full: dict[str, Any]) -> list[str]:
    graph_subset_map = metrics_as_map(graph_subset)
    workflow_subset_map = metrics_as_map(workflow_subset)
    graph_full_map = metrics_as_map(graph_full)
    graph_subset_loc = graph_subset_map["semantic_localization_match"]
    graph_full_loc = graph_full_map["semantic_localization_match"]
    workflow_subset_loc = workflow_subset_map["semantic_localization_match"]

    graph_subset_pct = (graph_subset_loc["count"] / graph_subset["sample_size"] * 100) if graph_subset["sample_size"] else 0.0
    graph_full_pct = (graph_full_loc["count"] / graph_full["sample_size"] * 100) if graph_full["sample_size"] else 0.0
    workflow_subset_pct = (workflow_subset_loc["count"] / workflow_subset["sample_size"] * 100) if workflow_subset["sample_size"] else 0.0

    lines: list[str] = []
    if graph_subset["sample_size"] < 15:
        lines.append(
            "The stack-trace subgroup is small enough that all conclusions should be treated as exploratory rather than stable."
        )
    if graph_subset_pct >= graph_full_pct + 10.0 and workflow_subset_pct > graph_subset_pct + 10.0:
        lines.append(
            "Graph-only is materially better on stack-trace issues than on the full sample, but it still trails the tool-first workflow clearly. This suggests a niche for graph context on execution-anchored issues, not a primary localization strategy."
        )
    elif graph_subset_pct >= graph_full_pct + 10.0:
        lines.append(
            "Graph-only improves meaningfully on stack-trace issues, but the workflow remains at least competitive. This weakens the strongest negative reading slightly, while still favoring the broader workflow."
        )
    else:
        lines.append(
            "Graph-only does not improve materially on stack-trace issues relative to its full-sample result. This strengthens the negative claim that graph context alone is insufficient even when issues include execution anchors."
        )
    if workflow_subset_pct >= graph_subset_pct + 10.0:
        lines.append(
            "The tool-first workflow remains clearly stronger than graph-only on the stack-trace subset, so stack traces do not make graph-only retrieval competitive with the broader workflow."
        )
    elif workflow_subset_pct > graph_subset_pct:
        lines.append(
            "The tool-first workflow still outperforms graph-only on the stack-trace subset, though the gap is smaller than on the full sample."
        )
    else:
        lines.append(
            "Graph-only matches or exceeds the workflow on this subgroup, which would justify presenting stack-trace issues as a specialized niche where graph context is especially helpful."
        )
    return lines


def build_markdown_report(
    *,
    metadata_path: Path,
    graph_results_path: Path,
    workflow_results_path: Path,
    stacktrace_rows: list[dict[str, Any]],
    graph_full: dict[str, Any],
    graph_subset: dict[str, Any],
    workflow_full: dict[str, Any],
    workflow_subset: dict[str, Any],
    deltas: dict[str, list[dict[str, Any]]],
    interpretation: list[str],
) -> str:
    lines: list[str] = [
        "# Stack-Trace Subgroup Comparison",
        "",
        "## Summary",
        "",
        f"- Metadata source: [{metadata_path.name}]({metadata_path.resolve().as_posix()})",
        f"- Graph baseline: [{graph_results_path.name}]({graph_results_path.resolve().as_posix()})",
        f"- Tool-first baseline: [{workflow_results_path.name}]({workflow_results_path.resolve().as_posix()})",
        f"- Stack-trace subgroup size on the paired intersection: `{len(stacktrace_rows)}`",
        "",
        "## Stack-Trace Definition",
        "",
        "- Include an issue if its problem statement contains any of:",
        "  - `Traceback (most recent call last):` or `Traceback`",
        "  - a Python stack frame of the form `File \"…\", line N`",
        "  - a test failure header of the form `FAIL: ... (...)` or `ERROR: ... (...)`",
        "- Exclude generic mentions of `error` or `exception` without a trace-like structure.",
        "",
        "## Publication-Safe Interpretation",
        "",
    ]
    lines.extend([f"- {line}" for line in interpretation])
    lines.extend(
        [
            "",
            "## Main Comparison",
            "",
            "| Metric | Graph full sample | Graph stack-trace subset | Tool-first full sample | Tool-first stack-trace subset |",
            "|---|---|---|---|---|",
        ]
    )
    graph_full_map = metrics_as_map(graph_full)
    graph_subset_map = metrics_as_map(graph_subset)
    workflow_full_map = metrics_as_map(workflow_full)
    workflow_subset_map = metrics_as_map(workflow_subset)
    for key in graph_full_map:
        lines.append(
            f"| {graph_full_map[key]['label']} | {graph_full_map[key]['formatted']} | {graph_subset_map[key]['formatted']} | {workflow_full_map[key]['formatted']} | {workflow_subset_map[key]['formatted']} |"
        )

    for delta_title, delta_rows in (
        ("Graph full sample → graph stack-trace subset", deltas["graph_full_to_subset"]),
        ("Tool-first full sample → tool-first stack-trace subset", deltas["workflow_full_to_subset"]),
        ("Graph stack-trace subset → tool-first stack-trace subset", deltas["graph_subset_to_workflow_subset"]),
    ):
        lines.extend(
            [
                "",
                f"## Delta: {delta_title}",
                "",
                "| Metric | Delta |",
                "|---|---|",
            ]
        )
        for row in delta_rows:
            lines.append(f"| {row['label']} | {row['delta']} |")

    for section_title, summary in (
        ("Graph Stack-Trace Subset", graph_subset),
        ("Tool-First Stack-Trace Subset", workflow_subset),
    ):
        lines.extend(
            [
                "",
                f"## {section_title}",
                "",
                "| Metric | Value |",
                "|---|---|",
            ]
        )
        for row in summary["metrics"]:
            lines.append(f"| {row['label']} | {row['formatted']} |")
        lines.extend(
            [
                "",
                "### Repo Breakdown",
                "",
                "| Repo | Sample | Semantic Localization | Weak Graph/Workflow Found Issue |",
                "|---|---:|---|---|",
            ]
        )
        for repo_row in summary["repo_breakdown"]:
            lines.append(
                f"| {repo_row['repo_name']} | {repo_row['sample_size']} | {repo_row['semantic_localization_match']} | {repo_row['weak_found_issue']} |"
            )
        lines.extend(
            [
                "",
                "### Failure Taxonomy",
                "",
                "| Bucket | Count |",
                "|---|---:|",
            ]
        )
        for bucket in summary["failure_taxonomy"]:
            lines.append(f"| {bucket['bucket']} | {bucket['count']} |")

    lines.extend(
        [
            "",
            "## Included Instances",
            "",
            "| Instance | Repo | Matched Rules |",
            "|---|---|---|",
        ]
    )
    for row in stacktrace_rows:
        lines.append(
            f"| {row['instance_id']} | {row['repo_name']} | {', '.join(row['matched_stacktrace_rule'])} |"
        )
    return "\n".join(lines) + "\n"


def build_publication_note(
    *,
    stacktrace_rows: list[dict[str, Any]],
    graph_subset: dict[str, Any],
    workflow_subset: dict[str, Any],
    interpretation: list[str],
) -> str:
    graph_loc = metrics_as_map(graph_subset)["semantic_localization_match"]["formatted"]
    workflow_loc = metrics_as_map(workflow_subset)["semantic_localization_match"]["formatted"]
    return "\n".join(
        [
            "# Stack-Trace Subgroup Interpretation",
            "",
            "## Why This Subgroup Matters",
            "",
            "This subgroup tests a narrower version of the original graph-context hypothesis: whether graph-centric retrieval helps more on issues that already contain execution anchors, especially stack traces.",
            "",
            "## Deterministic Definition",
            "",
            "An issue is included if its problem statement contains at least one of:",
            "",
            "- `Traceback (most recent call last):` or `Traceback`",
            "- a Python stack frame matching `File \"…\", line N`",
            "- a test failure header matching `FAIL: ... (...)` or `ERROR: ... (...)`",
            "",
            "No manual exceptions or hand-picked additions were used.",
            "",
            "## Exact Result",
            "",
            f"- Paired stack-trace subset size: `{len(stacktrace_rows)}`",
            f"- Graph-only semantic localization on the subgroup: `{graph_loc}`",
            f"- Tool-first semantic localization on the subgroup: `{workflow_loc}`",
            "",
            "## Publication Guidance",
            "",
        ]
        + [f"- {line}" for line in interpretation]
        + [
            "",
            "## What This Does Not Justify",
            "",
            "- It does not justify reviving the strong graph-only claim.",
            "- It does not justify replacing the main full-sample comparison with the subgroup result.",
            "- It should be presented as a stratified analysis, not a new headline benchmark.",
            "",
            "## Best Use In The Paper",
            "",
            "Use this as a subgroup-analysis subsection that sharpens the main claim: either graph context has a narrower niche on trace-rich issues, or graph-only remains weak even under favorable anchoring conditions.",
        ]
    ) + "\n"


def main() -> None:
    args = parse_args()
    metadata_rows = read_jsonl(args.metadata_path)
    graph_rows = read_json(args.graph_results)
    workflow_rows = read_json(args.workflow_results)

    graph_by_id = index_by_instance(graph_rows)
    workflow_by_id = index_by_instance(workflow_rows)
    metadata_by_id = index_by_instance(metadata_rows)

    paired_ids = sorted(graph_by_id.keys() & workflow_by_id.keys())
    stacktrace_rows: list[dict[str, Any]] = []
    subset_rows: list[dict[str, Any]] = []
    for instance_id in paired_ids:
        metadata_row = metadata_by_id.get(instance_id)
        if metadata_row is None:
            continue
        classification = classify_stacktrace(str(metadata_row.get("problem_statement", "")))
        if not classification["is_stacktrace_issue"]:
            continue
        subset_row = dict(metadata_row)
        subset_row.update(
            {
                "has_traceback_header": classification["has_traceback_header"],
                "has_python_frame": classification["has_python_frame"],
                "has_test_failure_header": classification["has_test_failure_header"],
                "matched_stacktrace_rule": classification["matched_stacktrace_rule"],
            }
        )
        subset_rows.append(subset_row)
        stacktrace_rows.append(
            {
                "instance_id": instance_id,
                "repo_name": str(metadata_row["repo_name"]),
                "has_traceback_header": classification["has_traceback_header"],
                "has_python_frame": classification["has_python_frame"],
                "has_test_failure_header": classification["has_test_failure_header"],
                "matched_stacktrace_rule": classification["matched_stacktrace_rule"],
            }
        )

    graph_subset_rows = [graph_by_id[row["instance_id"]] for row in subset_rows]
    workflow_subset_rows = [workflow_by_id[row["instance_id"]] for row in subset_rows]

    graph_full = build_subset_metrics([graph_by_id[instance_id] for instance_id in paired_ids])
    workflow_full = build_subset_metrics([workflow_by_id[instance_id] for instance_id in paired_ids])
    graph_subset = build_subset_metrics(graph_subset_rows)
    workflow_subset = build_subset_metrics(workflow_subset_rows)

    interpretation = build_interpretation(graph_subset, workflow_subset, graph_full)
    deltas = {
        "graph_full_to_subset": derive_delta("graph_full", "graph_subset", graph_full, graph_subset),
        "workflow_full_to_subset": derive_delta("workflow_full", "workflow_subset", workflow_full, workflow_subset),
        "graph_subset_to_workflow_subset": derive_delta("graph_subset", "workflow_subset", graph_subset, workflow_subset),
    }

    manifest = {
        "study_name": args.study_name,
        "metadata_path": str(args.metadata_path),
        "graph_results_path": str(args.graph_results),
        "workflow_results_path": str(args.workflow_results),
        "paired_intersection_size": len(paired_ids),
        "stacktrace_subset_size": len(subset_rows),
        "workflow_only_ids_excluded": sorted(workflow_by_id.keys() - graph_by_id.keys()),
        "included_instances": stacktrace_rows,
    }
    comparison = {
        "study_name": args.study_name,
        "paired_intersection_size": len(paired_ids),
        "stacktrace_subset_size": len(subset_rows),
        "graph_full": graph_full,
        "graph_subset": graph_subset,
        "workflow_full": workflow_full,
        "workflow_subset": workflow_subset,
        "deltas": deltas,
        "interpretation": interpretation,
    }

    metadata_dir = args.output_metadata_dir
    reports_dir = args.output_reports_dir
    write_jsonl(metadata_dir / f"{args.study_name}.jsonl", subset_rows)
    write_json(metadata_dir / f"{args.study_name}_instance_ids.json", [row["instance_id"] for row in subset_rows])
    write_json(metadata_dir / f"{args.study_name}_manifest.json", manifest)
    write_json(reports_dir / f"{args.study_name}_comparison.json", comparison)
    (reports_dir / f"{args.study_name}_comparison.md").write_text(
        build_markdown_report(
            metadata_path=args.metadata_path,
            graph_results_path=args.graph_results,
            workflow_results_path=args.workflow_results,
            stacktrace_rows=stacktrace_rows,
            graph_full=graph_full,
            graph_subset=graph_subset,
            workflow_full=workflow_full,
            workflow_subset=workflow_subset,
            deltas=deltas,
            interpretation=interpretation,
        ),
        encoding="utf-8",
    )
    (reports_dir / "STACKTRACE_SUBGROUP_INTERPRETATION.md").write_text(
        build_publication_note(
            stacktrace_rows=stacktrace_rows,
            graph_subset=graph_subset,
            workflow_subset=workflow_subset,
            interpretation=interpretation,
        ),
        encoding="utf-8",
    )

    # Deterministic extraction sanity checks from the spec.
    included_ids = {row["instance_id"] for row in subset_rows}
    assert "django__django-16408" in included_ids
    assert "pylint-dev__pylint-7228" in included_ids
    assert "django__django-15388" not in included_ids

    print(
        json.dumps(
            {
                "subset_jsonl": str((metadata_dir / f"{args.study_name}.jsonl").resolve()),
                "subset_size": len(subset_rows),
                "comparison_json": str((reports_dir / f"{args.study_name}_comparison.json").resolve()),
                "comparison_md": str((reports_dir / f"{args.study_name}_comparison.md").resolve()),
            }
        )
    )


if __name__ == "__main__":
    main()
