from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from datasets import load_dataset
except Exception:  # pragma: no cover - optional for metadata-file flows
    load_dataset = None

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata, read_metadata_file
from experiment.llm_clients import build_llm_client
from experiment.utils import write_jsonl
from graph_exact_patch_pipeline import (
    build_constrained_candidate_pools,
    build_target_selection_prompt,
    choose_target,
    choose_target_heuristic,
    normalize_structured_summary,
    search_workspace_candidate_files,
    select_target_deterministic,
)
from run_inference import (
    build_graph_summary_prompt,
    build_structured_summary_prompt,
    expand_graph_file_context,
    expand_related_file_candidates,
    generate_graph_summary,
    generate_structured_summary,
    render_graph_context,
    render_graph_summary_context,
    retrieve_graph_file_candidates,
    retrieve_graph_context,
    write_prompt_artifact,
)


PATCH_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

SEMANTIC_LOCALIZATION_SYSTEM_PROMPT = """You judge whether a debugging-localization result is semantically correct.
Return only valid JSON with exactly these boolean keys and one short string key:
- correct_file
- correct_function
- correct_fix_mechanism
- semantic_localization_match
- rationale

Rules:
- Judge against the official gold patch, but allow alternative valid implementations.
- correct_file means the localization clearly points to the right source file.
- correct_function means it clearly points to the right function, method, or local code region.
- correct_fix_mechanism means it identifies the right bug mechanism or repair strategy, even if the exact line differs from the gold patch.
- semantic_localization_match should be true only if all three booleans are true.
- rationale must be one short sentence."""


@dataclass
class GoldFile:
    path: str
    hunks: list[tuple[int, int]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate graph localization on SWE-bench instances.")
    parser.add_argument("--metadata-path", type=Path, default=None)
    parser.add_argument("--graph-db-path", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--study-name", type=str, default=None)
    parser.add_argument("--audit-path", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--max-summary-tokens", type=int, default=None)
    parser.add_argument("--candidate-budget", type=int, default=None)
    parser.add_argument("--enable-vector-discovery-for-anchorless", action="store_true")
    return parser.parse_args()


def parse_patch_gold(patch_text: str) -> dict[str, GoldFile]:
    gold: dict[str, GoldFile] = {}
    current_path: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[len("+++ b/") :].strip()
            gold.setdefault(current_path, GoldFile(path=current_path, hunks=[]))
            continue
        if current_path is None:
            continue
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match:
            start = int(match.group(1))
            length = int(match.group(2) or "1")
            end = start + max(length - 1, 0)
            gold[current_path].hunks.append((start, end))
    return gold


def load_dataset_patch_map(dataset_name: str, split: str) -> dict[str, dict[str, GoldFile]]:
    dataset = load_dataset(dataset_name, split=split)
    mapping: dict[str, dict[str, GoldFile]] = {}
    for row in dataset:
        patch_text = str(row.get("patch") or "")
        mapping[str(row["instance_id"])] = parse_patch_gold(patch_text)
    return mapping


def load_dataset_raw_patch_texts(dataset_name: str, split: str) -> dict[str, str]:
    dataset = load_dataset(dataset_name, split=split)
    return {
        str(row["instance_id"]): str(row.get("patch") or "")
        for row in dataset
    }


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + (z**2 / total)
    center = (p + (z**2 / (2 * total))) / denominator
    margin = (
        z
        * math.sqrt((p * (1 - p) / total) + (z**2 / (4 * total**2)))
        / denominator
    )
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return lower, upper


def format_rate(count: int, total: int) -> str:
    pct = (count / total * 100) if total else 0.0
    lower, upper = wilson_interval(count, total)
    return f"{count}/{total} ({pct:.1f}%, 95% CI {lower * 100:.1f}-{upper * 100:.1f}%)"


def count_metric(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key))


def weak_graph_found_issue(row: dict[str, Any]) -> bool:
    return bool(
        row.get("semantic_correct_fix_mechanism")
        and (
            row.get("semantic_correct_file")
            or row.get("semantic_correct_function")
            or row.get("target_line_within_gold_hunk")
        )
    )


def classify_failure_taxonomy(row: dict[str, Any]) -> str:
    if (
        row.get("audit_graph_found_issue")
        or row.get("semantic_localization_match")
        or row.get("weak_graph_found_issue")
        or row.get("target_line_within_gold_hunk")
    ):
        return "localized successfully"
    if row.get("audit_graph_found_issue") and not row.get("semantic_localization_match"):
        return "weak-label disagreement but audited localization acceptable"
    if row.get("gold_file_count", 0) > 1:
        return "ambiguous / multi-file issue"
    if not row.get("retrieved_top5_file_match"):
        return "retrieval missed correct file"
    if row.get("summary_mentions_gold_file") and not row.get("target_in_gold_file"):
        return "selector drifted away from summary"
    if row.get("semantic_correct_fix_mechanism") and not row.get("semantic_correct_file"):
        return "summary understood issue but named wrong implementation site"
    if row.get("semantic_correct_file") and not row.get("semantic_correct_fix_mechanism"):
        return "correct issue family but wrong concrete fix mechanism"
    if row.get("semantic_correct_fix_mechanism") and not row.get("target_line_within_gold_hunk"):
        return "summary understood issue but named wrong implementation site"
    return "ambiguous / multi-file issue"


def load_audit_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = read_metadata_file(path)
    return {str(row["instance_id"]): row for row in rows}


def repo_breakdown(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(str(row["repo_name"]), []).append(row)
    breakdown: list[dict[str, Any]] = []
    for repo_name, repo_rows in sorted(grouped.items()):
        audit_rows = [row for row in repo_rows if row.get("audit_graph_found_issue") is not None]
        breakdown.append(
            {
                "repo_name": repo_name,
                "sample_size": len(repo_rows),
                "semantic_localization_match": format_rate(
                    count_metric(repo_rows, "semantic_localization_match"),
                    len(repo_rows),
                ),
                "weak_graph_found_issue": format_rate(
                    count_metric(repo_rows, "weak_graph_found_issue"),
                    len(repo_rows),
                ),
                "audit_graph_found_issue": (
                    format_rate(
                        count_metric(audit_rows, "audit_graph_found_issue"),
                        len(audit_rows),
                    )
                    if audit_rows
                    else "N/A"
                ),
            }
        )
    return breakdown


def build_report(
    results: list[dict[str, Any]],
    limit: int,
    study_name: str,
    audit_rows_present: int,
    intended_total: int | None = None,
    error_rows: list[dict[str, Any]] | None = None,
) -> str:
    total = len(results)
    error_rows = error_rows or []
    taxonomy_counts: dict[str, int] = {}
    for row in results:
        taxonomy_counts[str(row["failure_taxonomy"])] = taxonomy_counts.get(str(row["failure_taxonomy"]), 0) + 1

    lines = [
        f"# {study_name}",
        "",
        f"Instances evaluated: {total}",
        (f"Intended instances: {intended_total}" if intended_total is not None else ""),
        (f"Failed instances: {len(error_rows)}" if error_rows else "Failed instances: 0"),
        "",
        "## Aggregate Metrics",
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
        f"- Weak proxy Graph found issue: {format_rate(count_metric(results, 'weak_graph_found_issue'), total)}",
        "",
    ]
    if audit_rows_present:
        lines.extend(
            [
                "## Audit Metrics",
                "",
                f"- Audited rows: {audit_rows_present}",
                f"- Audit correct file: {format_rate(count_metric(results, 'audit_correct_file'), audit_rows_present)}",
                f"- Audit correct region: {format_rate(count_metric(results, 'audit_correct_region'), audit_rows_present)}",
                f"- Audit correct fix mechanism: {format_rate(count_metric(results, 'audit_correct_fix_mechanism'), audit_rows_present)}",
                f"- Audit Graph found issue: {format_rate(count_metric(results, 'audit_graph_found_issue'), audit_rows_present)}",
                "",
            ]
        )
    lines.extend(
        [
        "## Repo Breakdown",
        "",
        "| Repo | Sample | Semantic Localization | Weak Graph Found Issue | Audited Graph Found Issue |",
        "|---|---:|---|---|---|",
        ]
    )
    for repo_row in repo_breakdown(results):
        lines.append(
            f"| {repo_row['repo_name']} | {repo_row['sample_size']} | {repo_row['semantic_localization_match']} | {repo_row['weak_graph_found_issue']} | {repo_row['audit_graph_found_issue']} |"
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
        "## Per-Instance Results",
        "",
        "| Instance | Repo | Gold Files | Retrieved Top Files | Target | Top-3 | Summary | Target File | Target Hunk | Semantic | Weak Graph | Taxonomy |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in results[:limit]:
        lines.append(
            "| {instance_id} | {repo_name} | {gold_files} | {retrieved_files} | {target} | {top3} | {summary} | {target_file} | {target_hunk} | {semantic} | {weak_graph} | {taxonomy} |".format(
                instance_id=row["instance_id"],
                repo_name=row["repo_name"],
                gold_files=", ".join(row["gold_files"]) or "-",
                retrieved_files=", ".join(row["retrieved_top_files"][:3]) or "-",
                target=f"{row['target_path']}:{row['target_line']}" if row["target_path"] else "-",
                top3="yes" if row["retrieved_top3_file_match"] else "no",
                summary="yes" if row["summary_mentions_gold_file"] else "no",
                target_file="yes" if row["target_in_gold_file"] else "no",
                target_hunk="yes" if row["target_line_within_gold_hunk"] else "no",
                semantic="yes" if row.get("semantic_localization_match") else "no",
                weak_graph="yes" if row.get("weak_graph_found_issue") else "no",
                taxonomy=row["failure_taxonomy"],
            )
        )
    return "\n".join(lines) + "\n"


def contains_gold_file_reference(graph_summary: str, gold_files: list[str]) -> bool:
    lowered = graph_summary.lower()
    for gold_file in gold_files:
        if gold_file.lower() in lowered:
            return True
        if Path(gold_file).name.lower() in lowered:
            return True
    return False


def line_in_hunks(line_number: int | None, hunks: list[tuple[int, int]]) -> bool:
    if line_number is None:
        return False
    for start, end in hunks:
        if start <= line_number <= end:
            return True
    return False


def build_semantic_judge_prompt(
    problem_statement: str,
    gold_files: list[str],
    gold_patch: str,
    graph_summary: str,
    retrieved_files: list[str],
    target_path: str | None,
    target_line: int | None,
) -> str:
    target_text = f"{target_path}:{target_line}" if target_path and target_line else "None"
    return f"""Problem statement:
{problem_statement}

Official gold files:
{json.dumps(gold_files, indent=2)}

Official gold patch:
```diff
{gold_patch}
```

Graph retrieved files:
{json.dumps(retrieved_files[:5], indent=2)}

Graph summary:
{graph_summary}

Selected final target:
{target_text}

Judge whether this localization is semantically correct."""


def judge_semantic_localization(
    llm_client,
    problem_statement: str,
    gold_files: list[str],
    gold_patch: str,
    graph_summary: str,
    retrieved_files: list[str],
    target_path: str | None,
    target_line: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = build_semantic_judge_prompt(
        problem_statement,
        gold_files,
        gold_patch,
        graph_summary,
        retrieved_files,
        target_path,
        target_line,
    )
    payload = llm_client.generate_json(SEMANTIC_LOCALIZATION_SYSTEM_PROMPT, prompt)
    usage = {}
    if not isinstance(payload, dict):
        payload = {}
    result = {
        "correct_file": bool(payload.get("correct_file")),
        "correct_function": bool(payload.get("correct_function")),
        "correct_fix_mechanism": bool(payload.get("correct_fix_mechanism")),
        "semantic_localization_match": bool(payload.get("semantic_localization_match")),
        "rationale": str(payload.get("rationale", "")).strip(),
    }
    if result["semantic_localization_match"] is False:
        result["semantic_localization_match"] = (
            result["correct_file"] and result["correct_function"] and result["correct_fix_mechanism"]
        )
    return result, usage


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    output_json = args.output_json or (settings.reports_dir / "localization_eval_10.json")
    output_md = args.output_md or (settings.reports_dir / "localization_eval_10.md")
    study_name = args.study_name or f"Localization Eval ({args.limit} Instances)"
    log_root = args.log_dir or (settings.data_root / "logs" / "localization_eval")
    audit_rows = load_audit_rows(args.audit_path)

    if args.metadata_path is None or metadata_path == settings.metadata_dir / "instances.jsonl":
        rows = read_metadata(settings.metadata_dir)[: args.limit]
    else:
        rows = read_metadata_file(metadata_path)[: args.limit]
    patch_map = load_dataset_patch_map(settings.dataset_name, settings.dataset_split)
    raw_patch_texts = load_dataset_raw_patch_texts(settings.dataset_name, settings.dataset_split)

    summary_llm_client = build_llm_client(
        settings.description_llm_provider, settings.description_llm_model
    )
    connection = sqlite3.connect(f"file:{graph_db_path}?mode=ro", uri=True, timeout=30.0)
    connection.execute("PRAGMA busy_timeout=30000")

    results: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    for row in rows:
        instance_id = row["instance_id"]
        problem_statement = row["problem_statement"]
        repo_name = row["repo_name"]
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

            graph_items = retrieve_graph_context(connection, instance_id, problem_statement, settings.graph_top_k)
            retrieved_files = []
            for item in graph_items:
                if item["relative_path"] not in retrieved_files:
                    retrieved_files.append(item["relative_path"])

            graph_context = render_graph_context(graph_items)
            graph_file_items = expand_graph_file_context(
                connection,
                workspace_dir,
                instance_id,
                graph_items,
                problem_statement=problem_statement,
            )
            graph_file_context = render_graph_summary_context(
                graph_file_items,
                max_tokens=args.max_summary_tokens or settings.max_summary_tokens,
                candidate_budget=args.candidate_budget or settings.candidate_budget,
            )
            summary_prompt = build_graph_summary_prompt(problem_statement, graph_file_context)
            write_prompt_artifact(instance_output_dir / "graph_summary_prompt.md", summary_prompt)
            graph_summary, summary_usage = generate_graph_summary(
                summary_llm_client, problem_statement, graph_file_context
            )
            write_prompt_artifact(instance_output_dir / "graph_summary.md", graph_summary)
            structured_summary_prompt = build_structured_summary_prompt(problem_statement, graph_file_context)
            write_prompt_artifact(instance_output_dir / "graph_summary_structured_prompt.md", structured_summary_prompt)
            structured_summary, structured_summary_usage = generate_structured_summary(
                summary_llm_client, problem_statement, graph_file_context
            )
            write_prompt_artifact(instance_output_dir / "graph_summary.json", json.dumps(structured_summary, indent=2))
            normalized_summary = normalize_structured_summary(workspace_dir, graph_summary, structured_summary)
            write_prompt_artifact(
                instance_output_dir / "structured_summary_normalized.json",
                json.dumps(normalized_summary, indent=2),
            )
            write_prompt_artifact(instance_output_dir / "graph_retrieved_symbols.md", graph_context)

            candidate_pools = build_constrained_candidate_pools(workspace_dir, graph_file_items, normalized_summary)
            write_prompt_artifact(
                instance_output_dir / "selector_candidate_pools.json",
                json.dumps(candidate_pools, indent=2),
            )
            prioritized_paths = (
                candidate_pools["primary_files"]
                + candidate_pools["secondary_files"]
                + candidate_pools["entrypoint_files"]
            )
            existing_paths = {str(item["relative_path"]) for item in graph_file_items}
            prioritized_items = expand_graph_file_context(
                connection,
                workspace_dir,
                instance_id,
                [{"relative_path": path} for path in prioritized_paths if path not in existing_paths],
                problem_statement=problem_statement,
            )
            file_items = graph_file_items + prioritized_items

            heuristic_target, selector_decision = select_target_deterministic(
                file_items,
                problem_statement,
                graph_summary,
                normalized_summary,
            )

            if heuristic_target is None:
                extra_paths = retrieve_graph_file_candidates(connection, instance_id, problem_statement, settings.graph_top_k)
                extra_paths.extend(
                    expand_related_file_candidates(
                        connection,
                        instance_id,
                        [str(item["relative_path"]) for item in graph_file_items],
                    )
                )
                extra_paths.extend(
                    search_workspace_candidate_files(
                        workspace_dir,
                        problem_statement,
                        graph_summary,
                        structured_summary=normalized_summary,
                    )
                )
                existing_paths = {str(item["relative_path"]) for item in file_items}
                extra_items = expand_graph_file_context(
                    connection,
                    workspace_dir,
                    instance_id,
                    [{"relative_path": path} for path in extra_paths if path not in existing_paths],
                    problem_statement=problem_statement,
                )
                file_items.extend(extra_items)
                heuristic_target = choose_target_heuristic(
                    file_items,
                    problem_statement,
                    graph_summary,
                    structured_summary=normalized_summary,
                )

            target_usage: dict[str, Any] = {}
            if heuristic_target is not None:
                target = heuristic_target
                write_prompt_artifact(instance_output_dir / "target_selection_prompt.md", "Heuristic target selection used.")
            else:
                target_prompt = build_target_selection_prompt(problem_statement, graph_summary, normalized_summary, graph_context)
                write_prompt_artifact(instance_output_dir / "target_selection_prompt.md", target_prompt)
                target, target_usage = choose_target(summary_llm_client, target_prompt)
            selector_decision["target"] = target
            write_prompt_artifact(instance_output_dir / "selector_decision.json", json.dumps(selector_decision, indent=2))
            write_prompt_artifact(instance_output_dir / "target_selection.json", json.dumps(target, indent=2))

            target_path = str(target["path"]) if target else None
            target_line = int(target["line_number"]) if target else None
            target_hunks = gold.get(target_path).hunks if target_path in gold else []
            semantic_judgment, semantic_usage = judge_semantic_localization(
                summary_llm_client,
                problem_statement,
                gold_files,
                gold_patch,
                graph_summary,
                retrieved_files,
                target_path,
                target_line,
            )
            write_prompt_artifact(instance_output_dir / "semantic_localization_judgment.json", json.dumps(semantic_judgment, indent=2))

            result = {
                "instance_id": instance_id,
                "repo_name": repo_name,
                "gold_files": gold_files,
                "gold_file_count": len(gold_files),
                "retrieved_top_files": retrieved_files,
                "retrieved_top1_file_match": bool(retrieved_files[:1] and any(path in gold for path in retrieved_files[:1])),
                "retrieved_top3_file_match": any(path in gold for path in retrieved_files[:3]),
                "retrieved_top5_file_match": any(path in gold for path in retrieved_files[:5]),
                "summary_mentions_gold_file": contains_gold_file_reference(graph_summary, gold_files),
                "target_path": target_path,
                "target_line": target_line,
                "target_in_gold_file": bool(target_path in gold),
                "target_line_within_gold_hunk": bool(target_path in gold and line_in_hunks(target_line, target_hunks)),
                "semantic_correct_file": semantic_judgment["correct_file"],
                "semantic_correct_function": semantic_judgment["correct_function"],
                "semantic_correct_fix_mechanism": semantic_judgment["correct_fix_mechanism"],
                "semantic_localization_match": semantic_judgment["semantic_localization_match"],
                "semantic_rationale": semantic_judgment["rationale"],
                "summary_usage": summary_usage,
                "structured_summary_usage": structured_summary_usage,
                "target_usage": target_usage,
                "semantic_usage": semantic_usage,
                "selector_mode": selector_decision["selector_mode"],
                "selector_rule": selector_decision["selector_rule"],
                "used_fallback": selector_decision["used_fallback"],
            }
            result["weak_graph_found_issue"] = weak_graph_found_issue(result)
            audit_row = audit_rows.get(instance_id, {})
            result["audit_correct_file"] = audit_row.get("audit_correct_file")
            result["audit_correct_region"] = audit_row.get("audit_correct_region")
            result["audit_correct_fix_mechanism"] = audit_row.get("audit_correct_fix_mechanism")
            result["audit_graph_found_issue"] = audit_row.get("audit_graph_found_issue")
            result["audit_notes"] = audit_row.get("audit_notes", "")
            result["failure_taxonomy"] = classify_failure_taxonomy(result)
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
    audit_rows_present = sum(1 for row in results if row.get("audit_graph_found_issue") is not None)
    output_md.write_text(
        build_report(
            results,
            args.limit,
            study_name,
            audit_rows_present,
            intended_total=len(rows),
            error_rows=error_rows,
        ),
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
