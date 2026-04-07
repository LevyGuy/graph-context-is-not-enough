from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any


TRACE_PREFIX = "NLI_TRACE"
RUNTIME_SHAPES = {
    "migration_serialization",
    "autoreload_runtime",
    "query_lookup_semantics",
    "ui_rendering",
}


def instrumentation_gate(
    problem_statement: str,
    static_result: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    threshold: float = 0.6,
    only_on_failures: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0.0
    failure_taxonomy = str(static_result.get("failure_taxonomy", "")).strip()
    issue_shape = str(static_result.get("issue_shape", "")).strip()
    if only_on_failures and static_result.get("semantic_localization_match"):
        return {
            "should_run": False,
            "reasons": ["static_localization_already_succeeded"],
            "candidate_files": [],
            "candidate_regions": [],
            "gate_confidence": 0.0,
            "expected_value": "low",
        }
    if failure_taxonomy in {
        "deterministic candidate discovery missed correct file",
        "comparison preferred wrong file despite good evidence",
        "issue likely requires runtime execution/reproduction",
        "file chosen correctly but region selection missed",
    }:
        reasons.append(f"failure_taxonomy:{failure_taxonomy}")
        score += 0.45
    lowered = problem_statement.lower()
    if any(token in lowered for token in ("autoreload", "runserver", "manage.py", "template", "request", "response", "compiler")):
        reasons.append("runtime_sensitive_terms")
        score += 0.25
    if issue_shape in RUNTIME_SHAPES:
        reasons.append(f"issue_shape:{issue_shape}")
        score += 0.2
    if float(static_result.get("file_selection_confidence", 0.0) or 0.0) < 0.65:
        reasons.append("low_file_selection_confidence")
        score += 0.15
    if len(merged_candidates) >= 2:
        top1 = float(merged_candidates[0].get("normalized_score", 0.0) or 0.0)
        top2 = float(merged_candidates[1].get("normalized_score", 0.0) or 0.0)
        if abs(top1 - top2) <= 15.0:
            reasons.append("top_candidate_ambiguity")
            score += 0.15

    plan = plan_instrumentation(
        problem_statement,
        static_result,
        merged_candidates,
        file_items,
    )
    should_run = score >= threshold and bool(plan["candidate_files"])
    return {
        "should_run": should_run,
        "reasons": reasons or ["no_instrumentation_signal"],
        "candidate_files": plan["candidate_files"],
        "candidate_regions": plan["candidate_regions"],
        "gate_confidence": round(min(score, 1.0), 4),
        "expected_value": "high" if score >= 0.75 else "medium" if should_run else "low",
    }


def plan_instrumentation(
    problem_statement: str,
    static_result: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    max_files: int = 3,
    max_points: int = 6,
) -> dict[str, Any]:
    chosen_file = str(static_result.get("target_path") or "").strip()
    shortlist: list[str] = []
    if chosen_file:
        shortlist.append(chosen_file)
    shortlist.extend(str(path) for path in static_result.get("retrieved_top_files", [])[: max_files + 2])
    candidate_files = []
    file_map = {str(item["relative_path"]): item for item in file_items}
    for path in _dedupe(shortlist):
        if path in file_map or any(str(c["relative_path"]) == path for c in merged_candidates):
            candidate_files.append(path)
        if len(candidate_files) >= max_files:
            break

    candidate_regions: list[dict[str, Any]] = []
    for path in candidate_files:
        file_item = file_map.get(path)
        if not file_item:
            continue
        symbols = file_item.get("symbols", [])
        if symbols:
            symbol = symbols[0]
            candidate_regions.append(
                {
                    "relative_path": path,
                    "symbol_name": str(symbol["symbol_name"]),
                    "line_number": int(symbol["start_line"]),
                    "event": "enter",
                }
            )
        elif file_item.get("blocks"):
            block = file_item["blocks"][0]
            candidate_regions.append(
                {
                    "relative_path": path,
                    "symbol_name": "",
                    "line_number": int(block["start_line"]),
                    "event": "block",
                }
            )
        if len(candidate_regions) >= max_points:
            break
    return {
        "candidate_files": candidate_files,
        "candidate_regions": candidate_regions[:max_points],
        "trace_prefix": TRACE_PREFIX,
        "problem_summary": problem_statement[:240],
    }


def build_instrumentation_patch(
    workspace_dir: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    patch_chunks: list[str] = []
    patched_files: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for region in plan.get("candidate_regions", []):
        grouped.setdefault(str(region["relative_path"]), []).append(region)

    for relative_path, regions in grouped.items():
        file_path = workspace_dir / relative_path
        if not file_path.exists():
            continue
        original_lines = file_path.read_text(encoding="utf-8").splitlines()
        updated_lines = list(original_lines)
        offset = 0
        applied_points: list[dict[str, Any]] = []
        for region in sorted(regions, key=lambda item: int(item["line_number"])):
            line_number = max(1, int(region["line_number"]))
            insertion_line = _resolve_insertion_line(updated_lines, line_number + offset)
            insert_at = min(len(updated_lines), insertion_line)
            symbol_name = str(region.get("symbol_name", "")).strip() or "?"
            event = str(region.get("event", "enter")).strip() or "enter"
            message = (
                f'print("{TRACE_PREFIX}|file={relative_path}|symbol={symbol_name}|event={event}|line={line_number}")'
            )
            indent = _infer_indent(updated_lines, insert_at - 1)
            updated_lines.insert(insert_at, f"{indent}{message}")
            offset += 1
            applied_points.append(
                {
                    "relative_path": relative_path,
                    "symbol_name": symbol_name,
                    "line_number": line_number,
                    "inserted_line": insert_at + 1,
                    "event": event,
                }
            )
        if not applied_points:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                original_lines,
                updated_lines,
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
        )
        if diff:
            patch_chunks.append(diff)
            patched_files.append(
                {
                    "relative_path": relative_path,
                    "points": applied_points,
                }
            )
    patch_text = "\n".join(chunk for chunk in patch_chunks if chunk.strip()).strip()
    if patch_text:
        patch_text += "\n"
    return {
        "patch_text": patch_text,
        "patched_files": patched_files,
        "trace_prefix": TRACE_PREFIX,
    }


def apply_instrumentation_patch(
    workspace_dir: Path,
    patch_path: Path,
) -> dict[str, Any]:
    return _run_git_apply(workspace_dir, patch_path, reverse=False)


def revert_instrumentation_patch(
    workspace_dir: Path,
    patch_path: Path,
) -> dict[str, Any]:
    return _run_git_apply(workspace_dir, patch_path, reverse=True)


def parse_instrumentation_logs(stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    events: list[dict[str, Any]] = []
    for line in combined.splitlines():
        if TRACE_PREFIX not in line:
            continue
        marker = line[line.index(TRACE_PREFIX) :]
        fields: dict[str, str] = {}
        for part in marker.split("|")[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
        if fields:
            events.append(fields)
    reached_files = _dedupe([str(event.get("file", "")).strip() for event in events if str(event.get("file", "")).strip()])
    reached_symbols = _dedupe([str(event.get("symbol", "")).strip() for event in events if str(event.get("symbol", "")).strip() and str(event.get("symbol")) != "?"])
    return {
        "trace_prefix": TRACE_PREFIX,
        "events": events,
        "reached_files": reached_files,
        "reached_symbols": reached_symbols,
        "produced_useful_signal": bool(events),
    }


def build_instrumentation_evidence(parsed_logs: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for event in parsed_logs.get("events", []):
        relative_path = str(event.get("file", "")).strip()
        if not relative_path:
            continue
        symbol_name = str(event.get("symbol", "")).strip()
        line_number = int(event.get("line", "1") or "1")
        evidence.append(
            {
                "relative_path": relative_path,
                "tool": "instrumentation_trace",
                "match_type": "instrumentation_file",
                "anchor": str(event.get("event", "trace")),
                "symbol_name": symbol_name or None,
                "line_number": line_number,
                "event": str(event.get("event", "trace")),
            }
        )
        if symbol_name and symbol_name != "?":
            evidence.append(
                {
                    "relative_path": relative_path,
                    "tool": "instrumentation_trace",
                    "match_type": "instrumentation_symbol",
                    "anchor": symbol_name,
                    "symbol_name": symbol_name,
                    "line_number": line_number,
                    "event": str(event.get("event", "trace")),
                }
            )
    return {
        "summary": {
            "produced_useful_signal": bool(parsed_logs.get("produced_useful_signal")),
            "reached_files": parsed_logs.get("reached_files", []),
            "reached_symbols": parsed_logs.get("reached_symbols", []),
        },
        "evidence": evidence,
        "parsed_logs": parsed_logs,
    }


def _run_git_apply(workspace_dir: Path, patch_path: Path, reverse: bool) -> dict[str, Any]:
    command = ["git", "apply"]
    if reverse:
        command.append("-R")
    command.append(str(patch_path))
    completed = subprocess.run(
        command,
        cwd=str(workspace_dir),
        capture_output=True,
        text=True,
    )
    return {
        "applied": completed.returncode == 0,
        "reverse": reverse,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
    }


def _infer_indent(lines: list[str], index: int) -> str:
    if 0 <= index < len(lines):
        line = lines[index]
        base_indent = line[: len(line) - len(line.lstrip(" "))]
        if line.rstrip().endswith(":"):
            return base_indent + "    "
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            next_indent = next_line[: len(next_line) - len(next_line.lstrip(" "))]
            if len(next_indent) > len(base_indent):
                return next_indent
        return base_indent
    return ""


def _resolve_insertion_line(lines: list[str], line_number: int) -> int:
    start_index = max(0, min(len(lines) - 1, line_number - 1))
    for index in range(start_index, min(len(lines), start_index + 8)):
        stripped = lines[index].lstrip()
        if stripped.startswith("@"):
            continue
        if stripped.startswith(("def ", "async def ", "class ")) and lines[index].rstrip().endswith(":"):
            return index + 1
        if lines[index].rstrip().endswith(":"):
            return index + 1
    return min(len(lines), line_number)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
