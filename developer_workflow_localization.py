from __future__ import annotations

import argparse
import json
import math
import sqlite3
import traceback
from pathlib import Path
from typing import Any

try:
    import chromadb
except Exception:  # pragma: no cover - optional dependency in some envs
    chromadb = None

from developer_workflow import (
    compare_candidate_files,
    extract_issue_anchors,
    example_lookup,
    file_lookup,
    graph_expander,
    implementation_trace,
    llm_summarizer,
    merge_candidates,
    repo_grep,
    test_lookup,
    symbol_lookup,
    target_selector,
    vector_lookup,
    workflow_layer_lookup,
    render_evidence_packet,
)
from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata, read_metadata_file
from experiment.llm_clients import build_embedding_client, build_llm_client
from experiment.utils import write_jsonl
from localization_eval import (
    contains_gold_file_reference,
    judge_semantic_localization,
    line_in_hunks,
    load_audit_rows,
    load_dataset_patch_map,
    load_dataset_raw_patch_texts,
    write_prompt_artifact,
)
from instrumented_runtime import (
    apply_instrumentation_patch,
    build_instrumentation_evidence,
    build_instrumentation_patch,
    instrumentation_gate,
    parse_instrumentation_logs,
    plan_instrumentation,
    revert_instrumentation_patch,
)
from runtime_repro import (
    build_runtime_evidence,
    infer_runtime_command,
    parse_runtime_traceback,
    run_runtime_command,
    runtime_gate,
    summarize_runtime_attempt,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tool-first developer workflow localization benchmark.")
    parser.add_argument("--metadata-path", type=Path, default=None)
    parser.add_argument("--graph-db-path", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--study-name", type=str, default="Developer Workflow Localization")
    parser.add_argument("--audit-path", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--max-summary-tokens", type=int, default=None)
    parser.add_argument("--candidate-budget", type=int, default=None)
    parser.add_argument("--enable-vector-for-anchorless", action="store_true")
    parser.add_argument("--enable-runtime-fallback", action="store_true")
    parser.add_argument("--runtime-timeout-seconds", type=int, default=90)
    parser.add_argument("--runtime-max-cases", type=int, default=None)
    parser.add_argument("--runtime-only-on-failures", action="store_true")
    parser.add_argument("--runtime-gate-threshold", type=float, default=0.6)
    parser.add_argument("--enable-instrumented-runtime", action="store_true")
    parser.add_argument("--instrumentation-max-files", type=int, default=3)
    parser.add_argument("--instrumentation-max-points", type=int, default=6)
    parser.add_argument("--instrumentation-timeout-seconds", type=int, default=90)
    parser.add_argument("--instrumentation-only-on-failures", action="store_true")
    parser.add_argument("--instrumentation-gate-threshold", type=float, default=0.6)
    return parser.parse_args()


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


def count_nested_metric(rows: list[dict[str, Any]], nested_key: str, metric_key: str) -> int:
    return sum(1 for row in rows if row.get(nested_key, {}).get(metric_key))


def weak_graph_found_issue(row: dict[str, Any]) -> bool:
    return bool(
        row.get("semantic_correct_fix_mechanism")
        and (
            row.get("semantic_correct_file")
            or row.get("semantic_correct_function")
            or row.get("target_line_within_gold_hunk")
        )
    )


def expanded_contains_gold_region(file_items: list[dict[str, Any]], gold: dict[str, Any]) -> bool:
    for file_item in file_items:
        relative_path = str(file_item["relative_path"])
        if relative_path not in gold:
            continue
        hunks = gold[relative_path].hunks
        for symbol in file_item.get("symbols", []):
            if any(not (int(symbol["end_line"]) < start or int(symbol["start_line"]) > end) for start, end in hunks):
                return True
        for block in file_item.get("blocks", []):
            if any(not (int(block["end_line"]) < start or int(block["start_line"]) > end) for start, end in hunks):
                return True
    return False


def summarize_mode_result(
    *,
    mode_name: str,
    gold: dict[str, Any],
    problem_statement: str,
    gold_files: list[str],
    gold_patch: str,
    anchors: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    file_comparison: dict[str, Any],
    summary_text: str,
    structured_summary: dict[str, Any],
    selector_inputs: dict[str, Any],
    selector_decision: dict[str, Any],
    target: dict[str, Any] | None,
    summary_usages: dict[str, Any],
    semantic_judgment: dict[str, Any],
    semantic_usage: dict[str, Any],
) -> dict[str, Any]:
    target_path = str(target["path"]) if target else None
    target_line = int(target["line_number"]) if target else None
    target_hunks = gold.get(target_path).hunks if target_path in gold else []
    retrieved_top_files = [str(item["relative_path"]) for item in merged_candidates]
    expanded_paths = [str(item["relative_path"]) for item in file_items]
    compared_top_files = [str(path) for path in file_comparison.get("top_files", [])]
    selected_file = str(selector_inputs["file_selection"].get("chosen_file", "")).strip()
    selected_region_is_gold = bool(target_path in gold and line_in_hunks(target_line, target_hunks))
    result = {
        "mode_name": mode_name,
        "retrieved_top_files": retrieved_top_files,
        "retrieved_top1_file_match": bool(retrieved_top_files[:1] and any(path in gold for path in retrieved_top_files[:1])),
        "retrieved_top3_file_match": any(path in gold for path in retrieved_top_files[:3]),
        "retrieved_top5_file_match": any(path in gold for path in retrieved_top_files[:5]),
        "summary_mentions_gold_file": contains_gold_file_reference(summary_text, gold_files),
        "target_path": target_path,
        "target_line": target_line,
        "target_in_gold_file": bool(target_path in gold),
        "target_line_within_gold_hunk": bool(target_path in gold and line_in_hunks(target_line, target_hunks)),
        "semantic_correct_file": semantic_judgment["correct_file"],
        "semantic_correct_function": semantic_judgment["correct_function"],
        "semantic_correct_fix_mechanism": semantic_judgment["correct_fix_mechanism"],
        "semantic_localization_match": semantic_judgment["semantic_localization_match"],
        "semantic_rationale": semantic_judgment["rationale"],
        "summary_usage": summary_usages["summary_usage"],
        "structured_summary_usage": summary_usages["structured_summary_usage"],
        "target_usage": selector_inputs["target_usage"],
        "semantic_usage": semantic_usage,
        "selector_mode": selector_decision.get("selector_mode", "deterministic"),
        "selector_rule": selector_decision.get("selector_rule", ""),
        "used_fallback": selector_decision.get("used_fallback", False),
        "exact_symbol_hit_contains_gold_file": any(item["relative_path"] in gold for item in merged_candidates if item.get("symbol_evidence")),
        "grep_hit_contains_gold_file": any(item["relative_path"] in gold for item in merged_candidates if item.get("grep_evidence")),
        "test_hit_contains_gold_file": any(item["relative_path"] in gold for item in merged_candidates if item.get("test_evidence")),
        "example_hit_contains_gold_file": any(item["relative_path"] in gold for item in merged_candidates if item.get("example_evidence")),
        "merged_candidate_top3_contains_gold_file": any(path in gold for path in retrieved_top_files[:3]),
        "merged_candidate_top5_contains_gold_file": any(path in gold for path in retrieved_top_files[:5]),
        "expanded_candidate_contains_gold_file": any(path in gold for path in expanded_paths),
        "expanded_candidate_contains_gold_region": expanded_contains_gold_region(file_items, gold),
        "file_comparison_top1_is_gold": bool(compared_top_files[:1] and any(path in gold for path in compared_top_files[:1])),
        "file_comparison_top3_contains_gold": any(path in gold for path in compared_top_files[:3]),
        "selected_file_is_gold": bool(selected_file in gold),
        "selected_region_is_gold": selected_region_is_gold,
        "anchor_extraction_has_explicit_clue": bool(
            anchors.get("file_hints") or anchors.get("symbol_hints") or anchors.get("error_types") or anchors.get("code_literals")
        ),
        "normalized_summary_has_gold_file": any(
            path in gold for path in selector_inputs["normalized_summary"].get("likely_bug_files", [])
        ),
        "workflow_help_likelihood": classify_help_likelihood(problem_statement),
        "file_selection_confidence": float(selector_inputs["file_selection"].get("confidence", 0.0) or 0.0),
    }
    result["weak_graph_found_issue"] = weak_graph_found_issue(result)
    result["failure_taxonomy"] = classify_failure_taxonomy({**result, "problem_statement": problem_statement}, anchors)
    return result


def choose_final_mode(
    static_mode: dict[str, Any],
    runtime_mode: dict[str, Any] | None,
    runtime_summary: dict[str, Any] | None,
) -> str:
    if not runtime_mode or not runtime_summary:
        return "static"
    if not runtime_summary.get("useful_signal"):
        return "static"
    if runtime_summary.get("produced_traceback") and runtime_mode.get("target_path") != static_mode.get("target_path"):
        return "runtime_augmented"
    if runtime_mode.get("target_line") != static_mode.get("target_line"):
        return "runtime_augmented"
    if float(runtime_mode.get("file_selection_confidence", 0.0) or 0.0) > float(static_mode.get("file_selection_confidence", 0.0) or 0.0) + 0.1:
        return "runtime_augmented"
    return "static"


def classify_runtime_outcome(row: dict[str, Any]) -> str:
    if not row.get("runtime_attempted"):
        return "runtime_not_attempted_by_gate"
    if row.get("runtime_summary", {}).get("environment_blocker"):
        if row.get("runtime_summary", {}).get("useful_signal"):
            return "runtime_attempted_environment_blocked_with_signal"
        return "runtime_attempted_environment_blocked_no_signal"
    if not row.get("runtime_summary", {}).get("useful_signal"):
        return "runtime_attempted_no_useful_signal"
    if row.get("runtime_improved_semantic_localization"):
        if row.get("runtime_changed_selected_file"):
            return "runtime_attempted_traceback_recovered_correct_file"
        if row.get("runtime_changed_selected_region"):
            return "runtime_attempted_changed_region_only"
    if row.get("runtime_attempted"):
        return "runtime_attempted_but_selection_still_wrong"
    return "runtime_not_attempted_by_gate"


def classify_instrumentation_outcome(row: dict[str, Any]) -> str:
    if not row.get("instrumentation_attempted"):
        return "instrumentation_not_attempted_by_gate"
    if not row.get("instrumentation_patch_applied"):
        return "instrumentation_apply_failed"
    if not row.get("instrumentation_patch_reverted"):
        return "instrumentation_revert_failed"
    if not row.get("instrumentation_summary", {}).get("produced_useful_signal"):
        return "instrumentation_run_no_useful_signal"
    if row.get("instrumentation_improved_semantic_localization"):
        if row.get("instrumentation_changed_selected_file"):
            return "instrumentation_recovered_correct_file"
        if row.get("instrumentation_changed_selected_region"):
            return "instrumentation_recovered_correct_region"
    return "instrumentation_attempted_but_selection_still_wrong"


def classify_help_likelihood(problem_statement: str) -> str:
    lowered = problem_statement.lower()
    if any(token in lowered for token in ("feature", "customizable", "formatter", "design", "documentation")):
        return "medium"
    if any(token in lowered for token in ("autoreload", "dev server", "pickle", "filteredrelation", "select_related", "compiler")):
        return "medium-high"
    return "high"


def classify_failure_taxonomy(row: dict[str, Any], anchors: dict[str, Any]) -> str:
    if row.get("semantic_localization_match"):
        return "localized successfully"
    explicit_clues = bool(anchors.get("file_hints") or anchors.get("symbol_hints") or anchors.get("error_types") or anchors.get("code_literals"))
    if explicit_clues and not row.get("anchor_extraction_has_explicit_clue"):
        return "anchor extraction missed explicit clue"
    if not row.get("merged_candidate_top5_contains_gold_file"):
        if row.get("workflow_help_likelihood") in {"medium", "medium-high"} and any(
            token in row.get("problem_statement", "").lower()
            for token in ("autoreload", "dev server", "filteredrelation", "select_related", "compiler")
        ):
            return "issue likely requires runtime execution/reproduction"
        return "deterministic candidate discovery missed correct file"
    if row.get("merged_candidate_top5_contains_gold_file") and not row.get("expanded_candidate_contains_gold_file"):
        return "graph expansion failed to bring in correct implementation file"
    if row.get("expanded_candidate_contains_gold_file") and row.get("file_comparison_top3_contains_gold") and not row.get("selected_file_is_gold"):
        return "comparison preferred wrong file despite good evidence"
    if row.get("selected_file_is_gold") and not row.get("selected_region_is_gold"):
        return "file chosen correctly but region selection missed"
    if row.get("expanded_candidate_contains_gold_file") and not row.get("summary_mentions_gold_file") and not row.get("target_in_gold_file"):
        return "LLM summary ignored strong evidence"
    if row.get("expanded_candidate_contains_gold_file") and row.get("normalized_summary_has_gold_file") is False:
        return "structured summary normalized away correct file"
    if row.get("expanded_candidate_contains_gold_file") and not row.get("target_in_gold_file"):
        return "selector chose wrong target from correct candidate set"
    if row.get("workflow_help_likelihood") == "medium":
        return "issue likely requires docs/framework knowledge not present in evidence"
    return "deterministic candidate discovery missed correct file"


def repo_breakdown(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(str(row["repo_name"]), []).append(row)
    breakdown: list[dict[str, Any]] = []
    for repo_name, repo_rows in sorted(grouped.items()):
        breakdown.append(
            {
                "repo_name": repo_name,
                "sample_size": len(repo_rows),
                "semantic_localization_match": format_rate(count_metric(repo_rows, "semantic_localization_match"), len(repo_rows)),
                "weak_graph_found_issue": format_rate(count_metric(repo_rows, "weak_graph_found_issue"), len(repo_rows)),
            }
        )
    return breakdown


def build_report(
    results: list[dict[str, Any]],
    intended_total: int,
    study_name: str,
    error_rows: list[dict[str, Any]],
) -> str:
    total = len(results)
    taxonomy_counts: dict[str, int] = {}
    for row in results:
        taxonomy_counts[str(row["failure_taxonomy"])] = taxonomy_counts.get(str(row["failure_taxonomy"]), 0) + 1
    runtime_attempted = count_metric(results, "runtime_attempted")
    runtime_succeeded = count_metric(results, "runtime_succeeded")
    runtime_traceback = count_metric(results, "runtime_produced_traceback")
    runtime_changed_file = count_metric(results, "runtime_changed_selected_file")
    runtime_changed_region = count_metric(results, "runtime_changed_selected_region")
    runtime_improved = count_metric(results, "runtime_improved_semantic_localization")
    runtime_regressed = count_metric(results, "runtime_regressed_semantic_localization")
    instrumentation_attempted = count_metric(results, "instrumentation_attempted")
    instrumentation_applied = count_metric(results, "instrumentation_patch_applied")
    instrumentation_reverted = count_metric(results, "instrumentation_patch_reverted")
    runtime_taxonomy_counts: dict[str, int] = {}
    for row in results:
        runtime_bucket = str(row.get("runtime_failure_taxonomy", "runtime_not_attempted_by_gate"))
        runtime_taxonomy_counts[runtime_bucket] = runtime_taxonomy_counts.get(runtime_bucket, 0) + 1
    instrumentation_taxonomy_counts: dict[str, int] = {}
    for row in results:
        instrumentation_bucket = str(row.get("instrumentation_failure_taxonomy", "instrumentation_not_attempted_by_gate"))
        instrumentation_taxonomy_counts[instrumentation_bucket] = instrumentation_taxonomy_counts.get(instrumentation_bucket, 0) + 1

    lines = [
        f"# {study_name}",
        "",
        f"Instances evaluated: {total}",
        f"Intended instances: {intended_total}",
        f"Failed instances: {len(error_rows)}",
        "",
        "## Runtime Comparison",
        "",
        f"- Static-only semantic localization: {format_rate(count_nested_metric(results, 'static_result', 'semantic_localization_match'), total)}",
        f"- Runtime-augmented semantic localization: {format_rate(count_nested_metric(results, 'runtime_augmented_result', 'semantic_localization_match'), total)}",
        f"- Final semantic localization: {format_rate(count_metric(results, 'semantic_localization_match'), total)}",
        f"- Runtime attempted: {format_rate(runtime_attempted, total)}",
        f"- Runtime succeeded: {format_rate(runtime_succeeded, total)}",
        f"- Runtime produced traceback: {format_rate(runtime_traceback, total)}",
        f"- Runtime changed selected file: {format_rate(runtime_changed_file, total)}",
        f"- Runtime changed selected region: {format_rate(runtime_changed_region, total)}",
        f"- Runtime improved semantic localization: {format_rate(runtime_improved, total)}",
        f"- Runtime regressed semantic localization: {format_rate(runtime_regressed, total)}",
        f"- Instrumentation attempted: {format_rate(instrumentation_attempted, total)}",
        f"- Instrumentation patch applied: {format_rate(instrumentation_applied, total)}",
        f"- Instrumentation patch reverted: {format_rate(instrumentation_reverted, total)}",
        "",
        "## Candidate Discovery Metrics",
        "",
        f"- Exact symbol hit contains gold file: {format_rate(count_metric(results, 'exact_symbol_hit_contains_gold_file'), total)}",
        f"- Grep hit contains gold file: {format_rate(count_metric(results, 'grep_hit_contains_gold_file'), total)}",
        f"- Test hit contains gold file: {format_rate(count_metric(results, 'test_hit_contains_gold_file'), total)}",
        f"- Example hit contains gold file: {format_rate(count_metric(results, 'example_hit_contains_gold_file'), total)}",
        f"- Merged candidate top-3 contains gold file: {format_rate(count_metric(results, 'merged_candidate_top3_contains_gold_file'), total)}",
        f"- Merged candidate top-5 contains gold file: {format_rate(count_metric(results, 'merged_candidate_top5_contains_gold_file'), total)}",
        "",
        "## Graph Expansion Metrics",
        "",
        f"- Expanded candidate set contains gold file: {format_rate(count_metric(results, 'expanded_candidate_contains_gold_file'), total)}",
        f"- Expanded candidate set contains gold region: {format_rate(count_metric(results, 'expanded_candidate_contains_gold_region'), total)}",
        "",
        "## File Comparison Metrics",
        "",
        f"- File comparison top-1 is gold: {format_rate(count_metric(results, 'file_comparison_top1_is_gold'), total)}",
        f"- File comparison top-3 contains gold: {format_rate(count_metric(results, 'file_comparison_top3_contains_gold'), total)}",
        "",
        "## Selection Metrics",
        "",
        f"- Selected file is gold: {format_rate(count_metric(results, 'selected_file_is_gold'), total)}",
        f"- Selected region is gold: {format_rate(count_metric(results, 'selected_region_is_gold'), total)}",
        "",
        "## Localization Metrics",
        "",
        f"- Retrieved Top-1 file match: {format_rate(count_metric(results, 'retrieved_top1_file_match'), total)}",
        f"- Retrieved Top-3 file match: {format_rate(count_metric(results, 'retrieved_top3_file_match'), total)}",
        f"- Retrieved Top-5 file match: {format_rate(count_metric(results, 'retrieved_top5_file_match'), total)}",
        f"- Summary mentions gold file: {format_rate(count_metric(results, 'summary_mentions_gold_file'), total)}",
        f"- Final target in gold file: {format_rate(count_metric(results, 'target_in_gold_file'), total)}",
        f"- Final target within gold hunk: {format_rate(count_metric(results, 'target_line_within_gold_hunk'), total)}",
        f"- Semantic correct file: {format_rate(count_metric(results, 'semantic_correct_file'), total)}",
        f"- Semantic correct function: {format_rate(count_metric(results, 'semantic_correct_function'), total)}",
        f"- Semantic correct fix mechanism: {format_rate(count_metric(results, 'semantic_correct_fix_mechanism'), total)}",
        f"- Semantic localization match: {format_rate(count_metric(results, 'semantic_localization_match'), total)}",
        f"- Weak workflow found issue: {format_rate(count_metric(results, 'weak_graph_found_issue'), total)}",
        "",
        "## Repo Breakdown",
        "",
        "| Repo | Sample | Semantic Localization | Weak Workflow Found Issue |",
        "|---|---:|---|---|",
    ]
    for repo_row in repo_breakdown(results):
        lines.append(
            f"| {repo_row['repo_name']} | {repo_row['sample_size']} | {repo_row['semantic_localization_match']} | {repo_row['weak_graph_found_issue']} |"
        )
    lines.extend(
        [
            "",
            "## Failure Taxonomy",
            "",
            "| Bucket | Count |",
            "|---|---:|",
        ]
    )
    for bucket, count in sorted(taxonomy_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {bucket} | {count} |")
    lines.extend(
        [
            "",
            "## Runtime Taxonomy",
            "",
            "| Bucket | Count |",
            "|---|---:|",
        ]
    )
    for bucket, count in sorted(runtime_taxonomy_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {bucket} | {count} |")
    lines.extend(
        [
            "",
            "## Instrumentation Taxonomy",
            "",
            "| Bucket | Count |",
            "|---|---:|",
        ]
    )
    for bucket, count in sorted(instrumentation_taxonomy_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {bucket} | {count} |")
    if error_rows:
        lines.extend(
            [
                "",
                "## Instance Errors",
                "",
                "| Instance | Repo | Error Type | Error |",
                "|---|---|---|---|",
            ]
        )
        for row in error_rows:
            lines.append(
                f"| {row['instance_id']} | {row['repo_name']} | {row['error_type']} | {str(row['error']).replace('|', '/')} |"
            )
    lines.extend(
        [
            "",
            "## Per-Instance Results",
            "",
            "| Instance | Repo | Gold Files | Top Candidates | Target | Merged Top-3 | Expanded Gold | Semantic | Taxonomy | Workflow Help |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in results:
        lines.append(
            "| {instance_id} | {repo_name} | {gold_files} | {retrieved_files} | {target} | {top3} | {expanded} | {semantic} | {taxonomy} | {help_likelihood} |".format(
                instance_id=row["instance_id"],
                repo_name=row["repo_name"],
                gold_files=", ".join(row["gold_files"]) or "-",
                retrieved_files=", ".join(row["retrieved_top_files"][:3]) or "-",
                target=f"{row['target_path']}:{row['target_line']}" if row["target_path"] else "-",
                top3="yes" if row["merged_candidate_top3_contains_gold_file"] else "no",
                expanded="yes" if row["expanded_candidate_contains_gold_file"] else "no",
                semantic="yes" if row["semantic_localization_match"] else "no",
                taxonomy=row["failure_taxonomy"],
                help_likelihood=row["workflow_help_likelihood"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    output_json = args.output_json or (settings.reports_dir / "developer_workflow_localization.json")
    output_md = args.output_md or (settings.reports_dir / "developer_workflow_localization.md")
    log_root = args.log_dir or (settings.data_root / "logs" / "developer_workflow_localization")
    rows = (
        read_metadata(settings.metadata_dir)[: args.limit]
        if args.metadata_path is None or metadata_path == settings.metadata_dir / "instances.jsonl"
        else read_metadata_file(metadata_path)[: args.limit]
    )

    patch_map = load_dataset_patch_map(settings.dataset_name, settings.dataset_split)
    raw_patch_texts = load_dataset_raw_patch_texts(settings.dataset_name, settings.dataset_split)
    summary_llm_client = build_llm_client(
        settings.description_llm_provider,
        settings.description_llm_model,
        reasoning_effort=settings.description_reasoning_effort,
    )

    chroma_client = None
    embedding_client = None
    if args.enable_vector_for_anchorless and chromadb is not None and settings.vector_db_dir.exists():
        chroma_client = chromadb.PersistentClient(path=str(settings.vector_db_dir))
        embedding_client = build_embedding_client(settings.embedding_provider, settings.embedding_model)

    connection = sqlite3.connect(f"file:{graph_db_path}?mode=ro", uri=True, timeout=30.0)
    connection.execute("PRAGMA busy_timeout=30000")

    results: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    max_summary_tokens = args.max_summary_tokens or settings.max_summary_tokens
    candidate_budget = args.candidate_budget or settings.candidate_budget
    runtime_cases_used = 0

    for row in rows:
        instance_id = row["instance_id"]
        repo_name = row["repo_name"]
        problem_statement = row["problem_statement"]
        workspace_dir = settings.workspaces_dir / instance_id
        instance_output_dir = log_root / instance_id
        instance_output_dir.mkdir(parents=True, exist_ok=True)
        result_path = instance_output_dir / "localization_result.json"
        error_path = instance_output_dir / "instance_error.json"

        if args.resume and result_path.exists():
            results.append(json.loads(result_path.read_text(encoding="utf-8")))
            continue
        if args.resume and error_path.exists():
            error_rows.append(json.loads(error_path.read_text(encoding="utf-8")))
            continue
        if error_path.exists():
            error_path.unlink()

        try:
            gold = patch_map.get(instance_id, {})
            gold_files = sorted(gold.keys())
            gold_patch = raw_patch_texts.get(instance_id, "")

            anchors = extract_issue_anchors(problem_statement, llm_client=summary_llm_client)
            write_prompt_artifact(instance_output_dir / "anchors.json", json.dumps(anchors, indent=2))

            symbol_candidates = symbol_lookup(connection, instance_id, anchors)
            file_candidates = file_lookup(connection, instance_id, anchors)
            grep_candidates = repo_grep(workspace_dir, anchors)
            test_candidates = test_lookup(workspace_dir, anchors)
            example_candidates = example_lookup(workspace_dir, anchors)
            implementation_candidates = implementation_trace(
                connection,
                instance_id,
                anchors,
                grep_candidates,
                test_candidates,
                example_candidates,
            )
            workflow_layer_candidates = workflow_layer_lookup(connection, instance_id, anchors)
            vector_candidates = []
            if args.enable_vector_for_anchorless and anchors.get("anchorless"):
                vector_candidates = vector_lookup(
                    chroma_client,
                    embedding_client,
                    instance_id,
                    problem_statement,
                    top_k=settings.vector_top_k,
                )

            write_prompt_artifact(instance_output_dir / "symbol_candidates.json", json.dumps(symbol_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "file_candidates.json", json.dumps(file_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "grep_candidates.json", json.dumps(grep_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "test_candidates.json", json.dumps(test_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "example_candidates.json", json.dumps(example_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "implementation_trace.json", json.dumps(implementation_candidates, indent=2))
            write_prompt_artifact(instance_output_dir / "workflow_layer_candidates.json", json.dumps(workflow_layer_candidates, indent=2))
            if vector_candidates:
                write_prompt_artifact(instance_output_dir / "vector_candidates.json", json.dumps(vector_candidates, indent=2))

            merged_candidates = merge_candidates(
                problem_statement,
                symbol_candidates,
                file_candidates,
                grep_candidates,
                test_candidates,
                example_candidates,
                implementation_candidates,
                workflow_layer_candidates,
                vector_candidates,
                limit=max(candidate_budget * 3, 12),
            )
            write_prompt_artifact(instance_output_dir / "candidate_merge.json", json.dumps(merged_candidates, indent=2))

            file_items, expansion_metadata = graph_expander(
                connection,
                workspace_dir,
                instance_id,
                merged_candidates,
                anchors,
                max_seed_files=min(candidate_budget, 8),
                max_related_files=min(candidate_budget, 8),
            )
            write_prompt_artifact(instance_output_dir / "graph_expansion.json", json.dumps(expansion_metadata, indent=2))

            file_comparison, file_comparison_prompt = compare_candidate_files(
                summary_llm_client,
                problem_statement,
                anchors,
                merged_candidates,
                file_items,
                candidate_budget,
            )
            write_prompt_artifact(instance_output_dir / "file_comparison_prompt.md", file_comparison_prompt)
            write_prompt_artifact(instance_output_dir / "file_comparison.json", json.dumps(file_comparison, indent=2))

            evidence_packet = render_evidence_packet(
                problem_statement,
                anchors,
                merged_candidates,
                file_items,
                file_comparison,
                max_tokens=max_summary_tokens,
                candidate_budget=candidate_budget,
            )
            write_prompt_artifact(instance_output_dir / "evidence_packet.md", evidence_packet)

            summary_text, structured_summary, prompts, usages = llm_summarizer(
                summary_llm_client,
                problem_statement,
                evidence_packet,
            )
            write_prompt_artifact(instance_output_dir / "summary_prompt.md", prompts["summary_prompt"])
            write_prompt_artifact(instance_output_dir / "summary.md", summary_text)
            write_prompt_artifact(instance_output_dir / "summary_structured.json", json.dumps(structured_summary, indent=2))

            target, selector_inputs, selector_decision = target_selector(
                summary_llm_client,
                workspace_dir,
                problem_statement,
                summary_text,
                structured_summary,
                merged_candidates,
                file_items,
                file_comparison,
                evidence_packet,
            )
            selector_input_payload = {
                "anchors": anchors,
                "merged_candidates": merged_candidates[:candidate_budget],
                "normalized_summary": selector_inputs["normalized_summary"],
                "candidate_pools": selector_inputs["candidate_pools"],
                "file_comparison": file_comparison,
            }
            write_prompt_artifact(instance_output_dir / "selector_input.json", json.dumps(selector_input_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "selector_decision.json", json.dumps(selector_decision, indent=2))
            write_prompt_artifact(instance_output_dir / "file_selection.json", json.dumps(selector_inputs["file_selection"], indent=2))
            write_prompt_artifact(instance_output_dir / "region_selection.json", json.dumps(selector_inputs["region_selection"], indent=2))

            target_path = str(target["path"]) if target else None
            semantic_judgment, semantic_usage = judge_semantic_localization(
                summary_llm_client,
                problem_statement,
                gold_files,
                gold_patch,
                summary_text,
                [str(item["relative_path"]) for item in merged_candidates],
                target_path,
                int(target["line_number"]) if target else None,
            )
            static_result = summarize_mode_result(
                mode_name="static",
                gold=gold,
                problem_statement=problem_statement,
                gold_files=gold_files,
                gold_patch=gold_patch,
                anchors=anchors,
                merged_candidates=merged_candidates,
                file_items=file_items,
                file_comparison=file_comparison,
                summary_text=summary_text,
                structured_summary=structured_summary,
                selector_inputs=selector_inputs,
                selector_decision=selector_decision,
                target=target,
                summary_usages=usages,
                semantic_judgment=semantic_judgment,
                semantic_usage=semantic_usage,
            )

            gate = runtime_gate(
                problem_statement,
                {**static_result, "problem_statement": problem_statement},
                gate_threshold=args.runtime_gate_threshold,
                runtime_only_on_failures=args.runtime_only_on_failures,
            )
            if args.runtime_max_cases is not None and runtime_cases_used >= args.runtime_max_cases:
                gate = {
                    "should_run": False,
                    "reasons": ["runtime_max_cases_reached"],
                    "gate_confidence": 0.0,
                    "expected_runtime_value": "low",
                }
            write_prompt_artifact(instance_output_dir / "runtime_gate.json", json.dumps(gate, indent=2))

            runtime_augmented_result = None
            runtime_summary_payload = None
            runtime_changed_selected_file = False
            runtime_changed_selected_region = False
            runtime_attempted = False
            runtime_succeeded = False
            runtime_produced_traceback = False

            runtime_command_payload: dict[str, Any] = {"mode": "skip", "command": [], "cwd": str(workspace_dir), "target": "", "reason": "Runtime fallback disabled or gate did not pass."}
            runtime_traceback_payload: dict[str, Any] = {"frames": [], "exception_type": "", "exception_message": "", "top_stack_files": [], "top_stack_lines": [], "produced_traceback": False}
            runtime_evidence_payload: dict[str, Any] = {"summary": {"useful_signal": False}, "evidence": [], "traceback": runtime_traceback_payload}

            if args.enable_runtime_fallback and gate.get("should_run"):
                command_spec = infer_runtime_command(workspace_dir, repo_name, problem_statement, test_candidates)
                runtime_command_payload = command_spec
                if command_spec.get("mode") != "skip":
                    runtime_cases_used += 1
                execution = run_runtime_command(
                    workspace_dir,
                    command_spec,
                    timeout_seconds=args.runtime_timeout_seconds,
                )
                runtime_attempted = bool(execution.get("attempted"))
                runtime_succeeded = bool(execution.get("succeeded"))
                traceback_payload = parse_runtime_traceback(
                    workspace_dir,
                    str(execution.get("stdout", "")),
                    str(execution.get("stderr", "")),
                )
                runtime_traceback_payload = traceback_payload
                runtime_produced_traceback = bool(traceback_payload.get("produced_traceback"))
                runtime_evidence = build_runtime_evidence(command_spec, execution, traceback_payload)
                runtime_evidence_payload = runtime_evidence
                runtime_summary_payload = summarize_runtime_attempt(gate, command_spec, execution, runtime_evidence)

                write_prompt_artifact(instance_output_dir / "runtime_command.json", json.dumps(command_spec, indent=2))
                write_prompt_artifact(instance_output_dir / "runtime_stdout.txt", str(execution.get("stdout", "")))
                write_prompt_artifact(instance_output_dir / "runtime_stderr.txt", str(execution.get("stderr", "")))
                write_prompt_artifact(instance_output_dir / "runtime_traceback.json", json.dumps(traceback_payload, indent=2))
                write_prompt_artifact(instance_output_dir / "runtime_summary.json", json.dumps(runtime_summary_payload, indent=2))
                write_prompt_artifact(instance_output_dir / "runtime_evidence.json", json.dumps(runtime_evidence, indent=2))

                if runtime_evidence["summary"].get("useful_signal"):
                    runtime_merged_candidates = merge_candidates(
                        problem_statement,
                        symbol_candidates,
                        file_candidates,
                        grep_candidates,
                        test_candidates,
                        example_candidates,
                        implementation_candidates,
                        workflow_layer_candidates,
                        vector_candidates,
                        runtime_candidates=runtime_evidence["evidence"],
                        limit=max(candidate_budget * 3, 12),
                    )
                    runtime_file_items, runtime_expansion_metadata = graph_expander(
                        connection,
                        workspace_dir,
                        instance_id,
                        runtime_merged_candidates,
                        anchors,
                        max_seed_files=min(candidate_budget, 8),
                        max_related_files=min(candidate_budget, 8),
                    )
                    write_prompt_artifact(instance_output_dir / "runtime_graph_expansion.json", json.dumps(runtime_expansion_metadata, indent=2))
                    runtime_file_comparison, runtime_file_comparison_prompt = compare_candidate_files(
                        summary_llm_client,
                        problem_statement,
                        anchors,
                        runtime_merged_candidates,
                        runtime_file_items,
                        candidate_budget,
                    )
                    write_prompt_artifact(instance_output_dir / "runtime_file_comparison.json", json.dumps(runtime_file_comparison, indent=2))
                    write_prompt_artifact(instance_output_dir / "runtime_file_comparison_prompt.md", runtime_file_comparison_prompt)
                    runtime_evidence_packet = render_evidence_packet(
                        problem_statement,
                        anchors,
                        runtime_merged_candidates,
                        runtime_file_items,
                        runtime_file_comparison,
                        max_tokens=max_summary_tokens,
                        candidate_budget=candidate_budget,
                    )
                    write_prompt_artifact(instance_output_dir / "runtime_evidence_packet.md", runtime_evidence_packet)
                    runtime_summary_text, runtime_structured_summary, runtime_prompts, runtime_usages = llm_summarizer(
                        summary_llm_client,
                        problem_statement,
                        runtime_evidence_packet,
                    )
                    write_prompt_artifact(instance_output_dir / "runtime_summary_prompt.md", runtime_prompts["summary_prompt"])
                    write_prompt_artifact(instance_output_dir / "runtime_summary.md", runtime_summary_text)
                    write_prompt_artifact(instance_output_dir / "runtime_summary_structured.json", json.dumps(runtime_structured_summary, indent=2))
                    runtime_target, runtime_selector_inputs, runtime_selector_decision = target_selector(
                        summary_llm_client,
                        workspace_dir,
                        problem_statement,
                        runtime_summary_text,
                        runtime_structured_summary,
                        runtime_merged_candidates,
                        runtime_file_items,
                        runtime_file_comparison,
                        runtime_evidence_packet,
                        runtime_evidence=runtime_evidence,
                    )
                    write_prompt_artifact(instance_output_dir / "runtime_region_selection.json", json.dumps(runtime_selector_inputs["region_selection"], indent=2))
                    runtime_target_path = str(runtime_target["path"]) if runtime_target else None
                    runtime_semantic_judgment, runtime_semantic_usage = judge_semantic_localization(
                        summary_llm_client,
                        problem_statement,
                        gold_files,
                        gold_patch,
                        runtime_summary_text,
                        [str(item["relative_path"]) for item in runtime_merged_candidates],
                        runtime_target_path,
                        int(runtime_target["line_number"]) if runtime_target else None,
                    )
                    runtime_augmented_result = summarize_mode_result(
                        mode_name="runtime_augmented",
                        gold=gold,
                        problem_statement=problem_statement,
                        gold_files=gold_files,
                        gold_patch=gold_patch,
                        anchors=anchors,
                        merged_candidates=runtime_merged_candidates,
                        file_items=runtime_file_items,
                        file_comparison=runtime_file_comparison,
                        summary_text=runtime_summary_text,
                        structured_summary=runtime_structured_summary,
                        selector_inputs=runtime_selector_inputs,
                        selector_decision=runtime_selector_decision,
                        target=runtime_target,
                        summary_usages=runtime_usages,
                        semantic_judgment=runtime_semantic_judgment,
                        semantic_usage=runtime_semantic_usage,
                    )
                    runtime_changed_selected_file = (
                        runtime_augmented_result.get("target_path") != static_result.get("target_path")
                    ) or (
                        runtime_selector_inputs["file_selection"].get("chosen_file")
                        != selector_inputs["file_selection"].get("chosen_file")
                    )
                    runtime_changed_selected_region = (
                        runtime_augmented_result.get("target_line") != static_result.get("target_line")
                    )
                else:
                    write_prompt_artifact(instance_output_dir / "runtime_file_comparison.json", json.dumps({}, indent=2))
                    write_prompt_artifact(instance_output_dir / "runtime_region_selection.json", json.dumps({}, indent=2))
            else:
                runtime_summary_payload = {
                    "gate": gate,
                    "attempted": False,
                    "succeeded": False,
                    "timed_out": False,
                    "exit_code": None,
                    "produced_traceback": False,
                    "useful_signal": False,
                    "top_stack_files": [],
                    "exception_type": "",
                }
            write_prompt_artifact(instance_output_dir / "runtime_command.json", json.dumps(runtime_command_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "runtime_traceback.json", json.dumps(runtime_traceback_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "runtime_summary.json", json.dumps(runtime_summary_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "runtime_evidence.json", json.dumps(runtime_evidence_payload, indent=2))
            if not (instance_output_dir / "runtime_stdout.txt").exists():
                write_prompt_artifact(instance_output_dir / "runtime_stdout.txt", "")
            if not (instance_output_dir / "runtime_stderr.txt").exists():
                write_prompt_artifact(instance_output_dir / "runtime_stderr.txt", "")
            if not (instance_output_dir / "runtime_file_comparison.json").exists():
                write_prompt_artifact(instance_output_dir / "runtime_file_comparison.json", json.dumps({}, indent=2))
            if not (instance_output_dir / "runtime_region_selection.json").exists():
                write_prompt_artifact(instance_output_dir / "runtime_region_selection.json", json.dumps({}, indent=2))

            instrumentation_gate_payload = {
                "should_run": False,
                "reasons": ["instrumented_runtime_disabled"],
                "candidate_files": [],
                "candidate_regions": [],
                "gate_confidence": 0.0,
                "expected_value": "low",
            }
            instrumentation_plan_payload = {
                "candidate_files": [],
                "candidate_regions": [],
                "trace_prefix": "NLI_TRACE",
                "problem_summary": problem_statement[:240],
            }
            instrumentation_patch_payload = {
                "patch_text": "",
                "patched_files": [],
                "trace_prefix": "NLI_TRACE",
            }
            instrumentation_apply_payload = {
                "applied": False,
                "reverse": False,
                "command": [],
                "stdout": "",
                "stderr": "",
                "exit_code": None,
            }
            instrumentation_revert_payload = {
                "applied": False,
                "reverse": True,
                "command": [],
                "stdout": "",
                "stderr": "",
                "exit_code": None,
            }
            instrumentation_summary_payload = {
                "produced_useful_signal": False,
                "reached_files": [],
                "reached_symbols": [],
            }
            instrumented_runtime_result = None
            instrumentation_attempted = False
            instrumentation_patch_applied = False
            instrumentation_patch_reverted = False
            instrumentation_changed_selected_file = False
            instrumentation_changed_selected_region = False

            pre_instrument_result = runtime_augmented_result if runtime_augmented_result else static_result
            if args.enable_instrumented_runtime:
                instrumentation_gate_payload = instrumentation_gate(
                    problem_statement,
                    pre_instrument_result,
                    merged_candidates,
                    file_items,
                    threshold=args.instrumentation_gate_threshold,
                    only_on_failures=args.instrumentation_only_on_failures,
                )
                instrumentation_plan_payload = plan_instrumentation(
                    problem_statement,
                    pre_instrument_result,
                    merged_candidates,
                    file_items,
                    max_files=args.instrumentation_max_files,
                    max_points=args.instrumentation_max_points,
                )
                instrumentation_patch_payload = build_instrumentation_patch(
                    workspace_dir,
                    instrumentation_plan_payload,
                )
            write_prompt_artifact(instance_output_dir / "instrumentation_gate.json", json.dumps(instrumentation_gate_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_plan.json", json.dumps(instrumentation_plan_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_patch_plan.json", json.dumps(instrumentation_patch_payload.get("patched_files", []), indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_patch.diff", instrumentation_patch_payload.get("patch_text", ""))
            write_prompt_artifact(instance_output_dir / "instrumentation_apply.json", json.dumps(instrumentation_apply_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_revert.json", json.dumps(instrumentation_revert_payload, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_evidence.json", json.dumps({"summary": instrumentation_summary_payload, "evidence": [], "parsed_logs": {}}, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_file_comparison.json", json.dumps({}, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumentation_region_selection.json", json.dumps({}, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumented_runtime_command.json", json.dumps({}, indent=2))
            write_prompt_artifact(instance_output_dir / "instrumented_runtime_stdout.txt", "")
            write_prompt_artifact(instance_output_dir / "instrumented_runtime_stderr.txt", "")

            base_final_mode = choose_final_mode(static_result, runtime_augmented_result, runtime_summary_payload)
            base_final_result = runtime_augmented_result if base_final_mode == "runtime_augmented" and runtime_augmented_result else static_result

            if (
                args.enable_instrumented_runtime
                and instrumentation_gate_payload.get("should_run")
                and instrumentation_patch_payload.get("patch_text", "").strip()
            ):
                instrumentation_attempted = True
                patch_path = instance_output_dir / "instrumentation_patch.diff"
                instrumentation_apply_payload = apply_instrumentation_patch(workspace_dir, patch_path)
                instrumentation_patch_applied = bool(instrumentation_apply_payload.get("applied"))
                write_prompt_artifact(instance_output_dir / "instrumentation_apply.json", json.dumps(instrumentation_apply_payload, indent=2))

                if instrumentation_patch_applied:
                    instrumented_command = infer_runtime_command(workspace_dir, repo_name, problem_statement, test_candidates)
                    write_prompt_artifact(instance_output_dir / "instrumented_runtime_command.json", json.dumps(instrumented_command, indent=2))
                    instrumented_execution = run_runtime_command(
                        workspace_dir,
                        instrumented_command,
                        timeout_seconds=args.instrumentation_timeout_seconds,
                    )
                    write_prompt_artifact(instance_output_dir / "instrumented_runtime_stdout.txt", str(instrumented_execution.get("stdout", "")))
                    write_prompt_artifact(instance_output_dir / "instrumented_runtime_stderr.txt", str(instrumented_execution.get("stderr", "")))
                    parsed_logs = parse_instrumentation_logs(
                        str(instrumented_execution.get("stdout", "")),
                        str(instrumented_execution.get("stderr", "")),
                    )
                    instrumentation_evidence_payload = build_instrumentation_evidence(parsed_logs)
                    instrumentation_summary_payload = instrumentation_evidence_payload["summary"]
                    write_prompt_artifact(instance_output_dir / "instrumentation_evidence.json", json.dumps(instrumentation_evidence_payload, indent=2))

                    instrumentation_revert_payload = revert_instrumentation_patch(workspace_dir, patch_path)
                    instrumentation_patch_reverted = bool(instrumentation_revert_payload.get("applied"))
                    write_prompt_artifact(instance_output_dir / "instrumentation_revert.json", json.dumps(instrumentation_revert_payload, indent=2))

                    if instrumentation_evidence_payload["summary"].get("produced_useful_signal") and instrumentation_patch_reverted:
                        instrumented_merged_candidates = merge_candidates(
                            problem_statement,
                            symbol_candidates,
                            file_candidates,
                            grep_candidates,
                            test_candidates,
                            example_candidates,
                            implementation_candidates,
                            workflow_layer_candidates,
                            vector_candidates,
                            runtime_candidates=runtime_evidence_payload.get("evidence", []),
                            instrumentation_candidates=instrumentation_evidence_payload.get("evidence", []),
                            limit=max(candidate_budget * 3, 12),
                        )
                        instrumented_file_items, instrumented_expansion_metadata = graph_expander(
                            connection,
                            workspace_dir,
                            instance_id,
                            instrumented_merged_candidates,
                            anchors,
                            max_seed_files=min(candidate_budget, 8),
                            max_related_files=min(candidate_budget, 8),
                        )
                        write_prompt_artifact(instance_output_dir / "instrumentation_graph_expansion.json", json.dumps(instrumented_expansion_metadata, indent=2))
                        instrumented_file_comparison, instrumented_file_comparison_prompt = compare_candidate_files(
                            summary_llm_client,
                            problem_statement,
                            anchors,
                            instrumented_merged_candidates,
                            instrumented_file_items,
                            candidate_budget,
                        )
                        write_prompt_artifact(instance_output_dir / "instrumentation_file_comparison.json", json.dumps(instrumented_file_comparison, indent=2))
                        write_prompt_artifact(instance_output_dir / "instrumentation_file_comparison_prompt.md", instrumented_file_comparison_prompt)
                        instrumented_evidence_packet = render_evidence_packet(
                            problem_statement,
                            anchors,
                            instrumented_merged_candidates,
                            instrumented_file_items,
                            instrumented_file_comparison,
                            max_tokens=max_summary_tokens,
                            candidate_budget=candidate_budget,
                        )
                        write_prompt_artifact(instance_output_dir / "instrumentation_evidence_packet.md", instrumented_evidence_packet)
                        instrumented_summary_text, instrumented_structured_summary, instrumented_prompts, instrumented_usages = llm_summarizer(
                            summary_llm_client,
                            problem_statement,
                            instrumented_evidence_packet,
                        )
                        write_prompt_artifact(instance_output_dir / "instrumentation_summary_prompt.md", instrumented_prompts["summary_prompt"])
                        write_prompt_artifact(instance_output_dir / "instrumentation_summary.md", instrumented_summary_text)
                        write_prompt_artifact(instance_output_dir / "instrumentation_summary_structured.json", json.dumps(instrumented_structured_summary, indent=2))
                        instrumented_target, instrumented_selector_inputs, instrumented_selector_decision = target_selector(
                            summary_llm_client,
                            workspace_dir,
                            problem_statement,
                            instrumented_summary_text,
                            instrumented_structured_summary,
                            instrumented_merged_candidates,
                            instrumented_file_items,
                            instrumented_file_comparison,
                            instrumented_evidence_packet,
                            runtime_evidence=runtime_evidence_payload if runtime_evidence_payload.get("summary", {}).get("useful_signal") else None,
                            instrumentation_evidence=instrumentation_evidence_payload,
                        )
                        write_prompt_artifact(instance_output_dir / "instrumentation_region_selection.json", json.dumps(instrumented_selector_inputs["region_selection"], indent=2))
                        instrumented_target_path = str(instrumented_target["path"]) if instrumented_target else None
                        instrumented_semantic_judgment, instrumented_semantic_usage = judge_semantic_localization(
                            summary_llm_client,
                            problem_statement,
                            gold_files,
                            gold_patch,
                            instrumented_summary_text,
                            [str(item["relative_path"]) for item in instrumented_merged_candidates],
                            instrumented_target_path,
                            int(instrumented_target["line_number"]) if instrumented_target else None,
                        )
                        instrumented_runtime_result = summarize_mode_result(
                            mode_name="instrumented_runtime",
                            gold=gold,
                            problem_statement=problem_statement,
                            gold_files=gold_files,
                            gold_patch=gold_patch,
                            anchors=anchors,
                            merged_candidates=instrumented_merged_candidates,
                            file_items=instrumented_file_items,
                            file_comparison=instrumented_file_comparison,
                            summary_text=instrumented_summary_text,
                            structured_summary=instrumented_structured_summary,
                            selector_inputs=instrumented_selector_inputs,
                            selector_decision=instrumented_selector_decision,
                            target=instrumented_target,
                            summary_usages=instrumented_usages,
                            semantic_judgment=instrumented_semantic_judgment,
                            semantic_usage=instrumented_semantic_usage,
                        )
                        instrumentation_changed_selected_file = (
                            instrumented_runtime_result.get("target_path") != base_final_result.get("target_path")
                        ) or (
                            instrumented_selector_inputs["file_selection"].get("chosen_file")
                            != base_final_result.get("target_path")
                        )
                        instrumentation_changed_selected_region = (
                            instrumented_runtime_result.get("target_line") != base_final_result.get("target_line")
                        )
                else:
                    write_prompt_artifact(instance_output_dir / "instrumented_runtime_command.json", json.dumps({"mode": "skip", "reason": "Instrumentation patch did not apply."}, indent=2))

            final_mode = base_final_mode
            final_result = base_final_result
            if instrumented_runtime_result and instrumentation_summary_payload.get("produced_useful_signal"):
                if (
                    instrumented_runtime_result.get("target_path") != base_final_result.get("target_path")
                    or instrumented_runtime_result.get("target_line") != base_final_result.get("target_line")
                    or float(instrumented_runtime_result.get("file_selection_confidence", 0.0) or 0.0)
                    > float(base_final_result.get("file_selection_confidence", 0.0) or 0.0) + 0.1
                ):
                    final_mode = "instrumented_runtime"
                    final_result = instrumented_runtime_result

            result = {
                "instance_id": instance_id,
                "repo_name": repo_name,
                "problem_statement": problem_statement,
                "gold_files": gold_files,
                "gold_file_count": len(gold_files),
                **final_result,
                "static_result": static_result,
                "runtime_augmented_result": runtime_augmented_result or {},
                "instrumented_runtime_result": instrumented_runtime_result or {},
                "final_mode": final_mode,
                "runtime_attempted": runtime_attempted,
                "runtime_succeeded": runtime_succeeded,
                "runtime_produced_traceback": runtime_produced_traceback,
                "runtime_changed_selected_file": runtime_changed_selected_file,
                "runtime_changed_selected_region": runtime_changed_selected_region,
                "runtime_improved_semantic_localization": bool(
                    runtime_augmented_result
                    and runtime_augmented_result.get("semantic_localization_match")
                    and not static_result.get("semantic_localization_match")
                ),
                "runtime_regressed_semantic_localization": bool(
                    runtime_augmented_result
                    and static_result.get("semantic_localization_match")
                    and not runtime_augmented_result.get("semantic_localization_match")
                ),
                "runtime_summary": runtime_summary_payload or {},
                "instrumentation_attempted": instrumentation_attempted,
                "instrumentation_patch_applied": instrumentation_patch_applied,
                "instrumentation_patch_reverted": instrumentation_patch_reverted,
                "instrumentation_changed_selected_file": instrumentation_changed_selected_file,
                "instrumentation_changed_selected_region": instrumentation_changed_selected_region,
                "instrumentation_improved_semantic_localization": bool(
                    instrumented_runtime_result
                    and instrumented_runtime_result.get("semantic_localization_match")
                    and not base_final_result.get("semantic_localization_match")
                ),
                "instrumentation_regressed_semantic_localization": bool(
                    instrumented_runtime_result
                    and base_final_result.get("semantic_localization_match")
                    and not instrumented_runtime_result.get("semantic_localization_match")
                ),
                "instrumentation_summary": instrumentation_summary_payload,
                "instrumentation_gate": instrumentation_gate_payload,
            }
            result["runtime_failure_taxonomy"] = classify_runtime_outcome(result)
            result["instrumentation_failure_taxonomy"] = classify_instrumentation_outcome(result)
            write_prompt_artifact(result_path, json.dumps(result, indent=2))
            results.append(result)
        except Exception as exc:
            error_payload = {
                "instance_id": instance_id,
                "repo_name": repo_name,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            write_prompt_artifact(error_path, json.dumps(error_payload, indent=2))
            error_rows.append(error_payload)
            if not args.continue_on_error:
                raise

    connection.close()

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    output_md.write_text(
        build_report(results, intended_total=len(rows), study_name=args.study_name, error_rows=error_rows),
        encoding="utf-8",
    )
    write_jsonl(output_json.with_suffix(".jsonl"), results)
    print(
        json.dumps(
            {
                "results_path": str(output_json),
                "report_path": str(output_md),
                "instances": len(results),
                "errors": len(error_rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
