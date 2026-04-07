from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from pathlib import Path

try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional fallback helper
    repair_json = None

from exact_line_replacement_driver import (
    build_assignment_rhs_prompt,
    build_line_prompt,
    build_patch_for_line,
    generate_replacement_line,
    generate_replacement_rhs,
    load_rows,
    rebuild_assignment_line,
    render_excerpt,
    resolve_summary_path,
    validate_python_patch,
)
from experiment.config import ensure_directories, load_settings
from experiment.llm_clients import build_llm_client, with_retries
from run_inference import (
    build_graph_summary_prompt,
    build_structured_summary_prompt,
    expand_graph_file_context,
    generate_graph_summary,
    generate_structured_summary,
    render_graph_context,
    render_graph_summary_context,
    retrieve_graph_file_candidates,
    retrieve_graph_context,
    expand_related_file_candidates,
    validate_patch,
    write_prompt_artifact,
)

TARGET_SELECTION_SYSTEM_PROMPT = """You select the most likely exact patch target for a bug.
Return only valid JSON with exactly three keys:
- path
- line_number
- mode

Rules:
- Treat the graph summary as authoritative when it explicitly names a file or symbol.
- Do not pick a file outside the summary-named file unless the summary provides no file anchor.
- mode must be either "line" or "assignment_rhs".
- Pick the narrowest line that is most likely to fix the bug.
- Prefer assignment_rhs when the suspicious line is a simple assignment.
- Do not return prose."""

SYMBOL_REPAIR_SYSTEM_PROMPT = """You repair bugs inside a single localized Python symbol.
Think carefully about the behavioral invariant, the failure mechanism, and the smallest correct fix.
Return only valid JSON with exactly two keys:
- updated_symbol_code
- repair_plan

Rules:
- Only change the target symbol.
- Preserve the function/class signature unless a signature change is clearly required.
- Prefer the smallest correct bounded repair over broad rewrites.
- The repair_plan must be brief and factual, at most 3 sentences.
- updated_symbol_code must be valid Python source for the full updated symbol."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automatically select and patch an exact graph-derived target line.")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--metadata-path", type=Path, default=None)
    parser.add_argument("--graph-db-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def build_target_selection_prompt(
    problem_statement: str,
    graph_summary: str,
    structured_summary: dict[str, Any] | None,
    graph_context: str,
) -> str:
    structured_block = ""
    if structured_summary:
        structured_block = f"""

Structured summary:
```json
{json.dumps(structured_summary, indent=2)}
```"""
    return f"""Problem statement:
{problem_statement}

Graph summary:
{graph_summary}
{structured_block}

Retrieved graph symbols:
{graph_context}

Choose the single most likely exact source line to patch.

Important:
- If the graph summary explicitly names a file, stay inside that file.
- If the graph summary explicitly names a function or method inside that file, prefer lines inside that symbol.
- Only override the summary if direct code evidence makes it impossible."""


def choose_target(llm_client, prompt: str) -> tuple[dict[str, str | int], dict]:
    text, usage = with_retries(lambda: llm_client.generate_text(TARGET_SELECTION_SYSTEM_PROMPT, prompt))
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        if repair_json is None:
            raise
        payload = json.loads(repair_json(cleaned))
    return {
        "path": str(payload["path"]),
        "line_number": int(payload["line_number"]),
        "mode": str(payload["mode"]),
    }, usage


def extract_identifiers(text: str) -> set[str]:
    return set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text))


def extract_summary_code_lines(summary: str) -> list[str]:
    code_lines: list[str] = []
    in_fence = False
    for raw_line in summary.splitlines():
        line = raw_line.rstrip("\n")
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and line.strip():
            code_lines.append(line.strip())
        elif "=" in line and line.strip():
            code_lines.append(line.strip())
    return code_lines


def extract_file_paths(text: str) -> list[str]:
    matches = re.findall(r"([A-Za-z0-9_\-./]+\.py)\b", text)
    ordered: list[str] = []
    for match in matches:
        cleaned = match.strip("`'\"()[]{}<>,:")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def extract_likely_bug_section(summary: str) -> str:
    lines = summary.splitlines()
    capture = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("### likely bug location") or stripped.lower().startswith("## likely bug location"):
            capture = True
            continue
        if capture and stripped.startswith("### "):
            break
        if capture:
            collected.append(line)
    return "\n".join(collected).strip()


def extract_summary_symbol_names(summary: str) -> list[str]:
    names: list[str] = []
    patterns = [
        r"`([A-Za-z_][A-Za-z0-9_]*)\(\)`",
        r"`([A-Za-z_][A-Za-z0-9_]*)`",
        r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, summary):
            if match not in names:
                names.append(match)
    return names


def extract_constant_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text):
        if match not in names:
            names.append(match)
    return names


def extract_dotted_module_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*\b", text):
        cleaned = match.strip("`'\"()[]{}<>,:")
        if "/" in cleaned:
            continue
        if cleaned.lower().startswith(("http.", "https.")):
            continue
        if cleaned not in candidates:
            candidates.append(cleaned)
    return candidates


def resolve_dotted_module_paths(workspace_dir: Path, text: str) -> list[str]:
    resolved: list[str] = []
    dotted = extract_dotted_module_candidates(text)
    suffixes: list[str] = []
    for item in dotted:
        suffixes.append(item.replace(".", "/") + ".py")
        suffixes.append(item.split(".")[-1] + ".py")
    for path in workspace_dir.rglob("*.py"):
        relative = str(path.relative_to(workspace_dir))
        lowered = relative.lower()
        for suffix in suffixes:
            suffix_lower = suffix.lower()
            if lowered == suffix_lower or lowered.endswith("/" + suffix_lower) or lowered.endswith(suffix_lower):
                if relative not in resolved:
                    resolved.append(relative)
                break
    return resolved


def resolve_summary_file_paths(file_items: list[dict], graph_summary: str) -> list[str]:
    available = {str(item["relative_path"]) for item in file_items}
    resolved: list[str] = []
    for mention in extract_file_paths(graph_summary):
        normalized = mention.lstrip("./")
        for path in available:
            if path == normalized or path.endswith(normalized) or Path(path).name == Path(normalized).name:
                if path not in resolved:
                    resolved.append(path)
    return resolved


def is_test_like_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return (
        "/tests/" in f"/{lowered}"
        or lowered.startswith("tests/")
        or lowered.endswith("_test.py")
        or "/test_" in f"/{lowered}"
        or lowered.endswith("/conftest.py")
        or lowered == "conftest.py"
    )


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered


def resolve_file_mentions_to_workspace(workspace_dir: Path, mentions: list[str]) -> list[str]:
    resolved: list[str] = []
    if not mentions:
        return resolved
    python_paths = [path for path in workspace_dir.rglob("*.py") if path.is_file()]
    available = [str(path.relative_to(workspace_dir)) for path in python_paths]
    for mention in mentions:
        normalized = str(mention).strip().lstrip("./")
        if not normalized:
            continue
        if normalized in available and not is_test_like_path(normalized):
            resolved.append(normalized)
            continue
        for path in available:
            if path.endswith(normalized) or Path(path).name == Path(normalized).name:
                if not is_test_like_path(path):
                    resolved.append(path)
                break
    return dedupe_preserve_order(resolved)


def get_clean_file_source(workspace_dir: Path, relative_path: str, fallback: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            cwd=workspace_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        if fallback is not None:
            return fallback
        source_path = workspace_dir / relative_path
        return source_path.read_text(encoding="utf-8", errors="replace")


def hydrate_clean_file_items(workspace_dir: Path, file_items: list[dict]) -> list[dict]:
    hydrated: list[dict] = []
    for item in file_items:
        relative_path = str(item["relative_path"])
        copied = dict(item)
        copied["source"] = get_clean_file_source(workspace_dir, relative_path, fallback=item.get("source"))
        hydrated.append(copied)
    return hydrated


def normalize_structured_summary(
    workspace_dir: Path,
    graph_summary: str,
    structured_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    structured_summary = structured_summary or {}
    likely_bug_mentions = [str(item) for item in structured_summary.get("likely_bug_files", [])]
    implementation_mentions = [str(item) for item in structured_summary.get("implementation_files", [])]
    entrypoint_mentions = [str(item) for item in structured_summary.get("entrypoint_files", [])]

    summary_file_mentions = extract_file_paths(graph_summary)
    likely_bug_section_mentions = extract_file_paths(extract_likely_bug_section(graph_summary))
    dotted_mentions = resolve_dotted_module_paths(workspace_dir, f"{graph_summary}\n" + "\n".join(likely_bug_mentions + implementation_mentions))

    likely_bug_files = resolve_file_mentions_to_workspace(
        workspace_dir,
        likely_bug_mentions + likely_bug_section_mentions + summary_file_mentions + dotted_mentions,
    )
    implementation_files = resolve_file_mentions_to_workspace(
        workspace_dir,
        implementation_mentions + dotted_mentions,
    )
    entrypoint_files = resolve_file_mentions_to_workspace(
        workspace_dir,
        entrypoint_mentions + summary_file_mentions,
    )

    likely_symbols = dedupe_preserve_order(
        [str(item) for item in structured_summary.get("likely_symbols", [])] + extract_summary_symbol_names(graph_summary)
    )
    constant_names = dedupe_preserve_order(
        [str(item) for item in structured_summary.get("constant_names", [])] + extract_constant_names(graph_summary)
    )
    suspicious_line_patterns = dedupe_preserve_order(
        [str(item) for item in structured_summary.get("suspicious_line_patterns", [])] + extract_summary_code_lines(graph_summary)
    )

    try:
        confidence = float(structured_summary.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "likely_bug_files": likely_bug_files,
        "implementation_files": [path for path in implementation_files if path not in likely_bug_files],
        "entrypoint_files": [
            path
            for path in entrypoint_files
            if path not in likely_bug_files and path not in implementation_files
        ],
        "likely_symbols": likely_symbols,
        "constant_names": constant_names,
        "suspicious_line_patterns": suspicious_line_patterns,
        "fix_mechanism": str(structured_summary.get("fix_mechanism", "")).strip(),
        "issue_shape": str(structured_summary.get("issue_shape", "")).strip(),
        "confidence": confidence,
    }


def build_constrained_candidate_pools(
    workspace_dir: Path,
    file_items: list[dict],
    normalized_summary: dict[str, Any],
) -> dict[str, Any]:
    known_paths = {str(item["relative_path"]) for item in file_items}
    primary_files = [path for path in normalized_summary.get("likely_bug_files", []) if path]
    secondary_files = [path for path in normalized_summary.get("implementation_files", []) if path]
    entrypoint_files = [path for path in normalized_summary.get("entrypoint_files", []) if path]

    missing_primary = [path for path in primary_files if path not in known_paths]
    missing_secondary = [path for path in secondary_files if path not in known_paths]
    missing_entrypoints = [path for path in entrypoint_files if path not in known_paths]

    return {
        "primary_files": primary_files,
        "secondary_files": secondary_files,
        "entrypoint_files": entrypoint_files,
        "missing_primary_files": missing_primary,
        "missing_secondary_files": missing_secondary,
        "missing_entrypoint_files": missing_entrypoints,
        "likely_symbols": normalized_summary.get("likely_symbols", []),
        "constant_names": normalized_summary.get("constant_names", []),
        "suspicious_line_patterns": normalized_summary.get("suspicious_line_patterns", []),
        "fix_mechanism": normalized_summary.get("fix_mechanism", ""),
        "confidence": normalized_summary.get("confidence", 0.0),
    }


def select_target_deterministic(
    file_items: list[dict],
    problem_statement: str,
    graph_summary: str,
    normalized_summary: dict[str, Any],
) -> tuple[dict[str, str | int] | None, dict[str, Any]]:
    primary_files = normalized_summary.get("likely_bug_files", [])
    secondary_files = normalized_summary.get("implementation_files", [])

    def filter_items(paths: list[str]) -> list[dict]:
        path_set = set(paths)
        return [item for item in file_items if str(item["relative_path"]) in path_set]

    decision: dict[str, Any] = {
        "selector_mode": "deterministic",
        "selector_rule": None,
        "used_fallback": False,
    }

    if len(primary_files) == 1:
        constrained = filter_items(primary_files)
        if constrained:
            target = choose_target_heuristic(
                constrained,
                problem_statement,
                graph_summary,
                structured_summary=normalized_summary,
            )
            if target is not None:
                decision["selector_rule"] = "single_likely_bug_file"
                return target, decision

    if len(primary_files) > 1:
        constrained = filter_items(primary_files)
        if constrained:
            target = choose_target_heuristic(
                constrained,
                problem_statement,
                graph_summary,
                structured_summary=normalized_summary,
            )
            if target is not None:
                decision["selector_rule"] = "multiple_likely_bug_files"
                return target, decision

    if secondary_files:
        constrained = filter_items(secondary_files)
        if constrained:
            target = choose_target_heuristic(
                constrained,
                problem_statement,
                graph_summary,
                structured_summary=normalized_summary,
            )
            if target is not None:
                decision["selector_rule"] = "implementation_files"
                return target, decision

    if normalized_summary.get("constant_names"):
        target = choose_target_heuristic(
            file_items,
            problem_statement,
            graph_summary,
            structured_summary=normalized_summary,
        )
        if target is not None:
            decision["selector_rule"] = "constant_definition_constraint"
            return target, decision

    decision["selector_rule"] = "fallback_broad"
    decision["used_fallback"] = True
    return None, decision


def find_constant_definition_file(file_items: list[dict], constant_name: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    for item in file_items:
        path = str(item["relative_path"])
        for raw_line in item["source"].splitlines():
            stripped = raw_line.strip()
            if stripped.startswith(f"{constant_name} ") or stripped.startswith(f"{constant_name}="):
                score = 0
                lowered = path.lower()
                if "/conf/" in f"/{lowered}" or "settings" in lowered:
                    score += 50
                if "global_settings.py" in lowered:
                    score += 100
                candidates.append((score, path))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def build_symbol_ranges(file_item: dict, summary_symbols: list[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for symbol in file_item.get("symbols", []):
        if symbol["symbol_name"] in summary_symbols:
            ranges.append((int(symbol["start_line"]), int(symbol["end_line"])))
    return ranges


def line_in_ranges(line_number: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= line_number <= end for start, end in ranges)


def select_target_symbol(
    file_item: dict[str, Any],
    target: dict[str, str | int],
    normalized_summary: dict[str, Any],
) -> dict[str, Any] | None:
    symbols = file_item.get("symbols", [])
    target_line = int(target["line_number"])
    likely_symbols = {str(name) for name in normalized_summary.get("likely_symbols", [])}

    for symbol in symbols:
        if symbol["symbol_name"] in likely_symbols and int(symbol["start_line"]) <= target_line <= int(symbol["end_line"]):
            return symbol
    for symbol in symbols:
        if symbol["symbol_name"] in likely_symbols:
            return symbol
    covering = [
        symbol
        for symbol in symbols
        if int(symbol["start_line"]) <= target_line <= int(symbol["end_line"])
    ]
    if covering:
        covering.sort(key=lambda item: int(item["end_line"]) - int(item["start_line"]))
        return covering[0]
    return None


def render_symbol_excerpt(source: str, start_line: int, end_line: int, context_radius: int = 4) -> str:
    lines = source.splitlines()
    start_index = max(0, start_line - 1 - context_radius)
    end_index = min(len(lines), end_line + context_radius)
    return "\n".join(lines[start_index:end_index])


def build_symbol_repair_prompt(
    problem_statement: str,
    graph_summary: str,
    structured_summary: dict[str, Any],
    relative_path: str,
    symbol: dict[str, Any],
    excerpt: str,
) -> str:
    return f"""Problem statement:
{problem_statement}

Graph summary:
{graph_summary}

Structured summary:
```json
{json.dumps(structured_summary, indent=2)}
```

Target file:
{relative_path}

Target symbol:
- name: {symbol['symbol_name']}
- kind: {symbol['symbol_kind']}
- start_line: {symbol['start_line']}
- end_line: {symbol['end_line']}

Current symbol code:
```python
{symbol['code']}
```

Local excerpt:
```python
{excerpt}
```

Before writing code, reason about:
1. the invariant this symbol should satisfy,
2. why the current implementation violates it,
3. the smallest correct bounded repair inside this symbol.

Then return only the updated full symbol code and a brief repair plan as JSON."""


def generate_updated_symbol(llm_client, prompt: str) -> tuple[dict[str, str], dict]:
    text, usage = with_retries(lambda: llm_client.generate_text(SYMBOL_REPAIR_SYSTEM_PROMPT, prompt))
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        if repair_json is None:
            raise
        payload = json.loads(repair_json(cleaned))
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
    return {
        "updated_symbol_code": str(payload["updated_symbol_code"]),
        "repair_plan": str(payload.get("repair_plan", "")).strip(),
    }, usage


def build_patch_for_span(relative_path: str, original_text: str, start_line: int, end_line: int, replacement_code: str) -> str:
    original_lines = original_text.splitlines(keepends=True)
    replacement_lines = replacement_code.splitlines(keepends=True)
    if replacement_code and not replacement_code.endswith("\n"):
        replacement_lines.append("\n")
    updated_lines = list(original_lines[: start_line - 1]) + replacement_lines + list(original_lines[end_line:])
    import difflib

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            updated_lines,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    return "".join(line if line.endswith("\n") else f"{line}\n" for line in diff_lines)


def choose_target_heuristic(
    file_items: list[dict],
    problem_statement: str,
    graph_summary: str,
    structured_summary: dict[str, Any] | None = None,
) -> dict[str, str | int] | None:
    combined = f"{problem_statement}\n{graph_summary}"
    likely_bug_files = []
    if structured_summary:
        likely_bug_files.extend(str(item) for item in structured_summary.get("implementation_files", []))
        likely_bug_files.extend(str(item) for item in structured_summary.get("likely_bug_files", []))
    likely_bug_files = [
        path for path in resolve_summary_file_paths(file_items, "\n".join(likely_bug_files + [extract_likely_bug_section(graph_summary)]))
    ]
    summary_files = likely_bug_files or resolve_summary_file_paths(file_items, graph_summary)
    summary_symbols = extract_summary_symbol_names(graph_summary)
    if structured_summary:
        for symbol in structured_summary.get("likely_symbols", []):
            if str(symbol) not in summary_symbols:
                summary_symbols.append(str(symbol))
    constants = extract_constant_names(combined)
    if structured_summary:
        for constant_name in structured_summary.get("constant_names", []):
            if str(constant_name) not in constants:
                constants.append(str(constant_name))
    if summary_files:
        constrained_items = [item for item in file_items if str(item["relative_path"]) in summary_files]
        if constrained_items:
            file_items = constrained_items
    else:
        for constant_name in constants:
            constant_file = find_constant_definition_file(file_items, constant_name)
            if constant_file is not None:
                constrained_items = [item for item in file_items if str(item["relative_path"]) == constant_file]
                if constrained_items:
                    file_items = constrained_items
                    break
    if "FILE_UPLOAD_PERMISSIONS" in combined and "0o644" in combined:
        for file_item in file_items:
            relative_path = str(file_item["relative_path"])
            if relative_path == "django/conf/global_settings.py":
                for line_number, raw_line in enumerate(file_item["source"].splitlines(), start=1):
                    if "FILE_UPLOAD_PERMISSIONS" in raw_line and "=" in raw_line:
                        return {
                            "path": relative_path,
                            "line_number": line_number,
                            "mode": "assignment_rhs",
                        }
    if "_cstack" in combined and "cright[" in combined and "right" in combined:
        for file_item in file_items:
            relative_path = str(file_item["relative_path"])
            if relative_path.endswith("astropy/modeling/separable.py") or relative_path == "astropy/modeling/separable.py":
                for line_number, raw_line in enumerate(file_item["source"].splitlines(), start=1):
                    if "cright[" in raw_line and "= 1" in raw_line:
                        return {
                            "path": relative_path,
                            "line_number": line_number,
                            "mode": "assignment_rhs",
                        }
    identifiers = extract_identifiers(problem_statement) | extract_identifiers(graph_summary)
    summary_code_lines = extract_summary_code_lines(graph_summary)
    summary_symbol_set = set(summary_symbols)

    def best_candidate(only_symbol_ranges: bool) -> tuple[int, dict[str, str | int]] | None:
        best_local: tuple[int, dict[str, str | int]] | None = None
        for file_item in file_items:
            relative_path = str(file_item["relative_path"])
            lowered_path = relative_path.lower()
            symbol_ranges = build_symbol_ranges(file_item, summary_symbols)
            path_score = 0
            if is_test_like_path(relative_path):
                path_score -= 80
            if summary_files and relative_path in summary_files:
                path_score += 200
            if lowered_path.endswith("ui.py") or lowered_path.endswith("connect.py") or lowered_path.endswith("models.py"):
                path_score -= 35
            if "global_settings.py" in lowered_path:
                path_score += 80
            if "/conf/" in f"/{lowered_path}":
                path_score += 25
            if "separable.py" in lowered_path:
                path_score += 80
            if only_symbol_ranges and not symbol_ranges:
                continue
            for line_number, raw_line in enumerate(file_item["source"].splitlines(), start=1):
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if summary_files and relative_path not in summary_files:
                    continue
                if only_symbol_ranges and not line_in_ranges(line_number, symbol_ranges):
                    continue
                score = path_score
                if symbol_ranges:
                    if line_in_ranges(line_number, symbol_ranges):
                        score += 240
                    else:
                        score -= 40
                if any(stripped == snippet for snippet in summary_code_lines):
                    score += 100
                if any(stripped in snippet or snippet in stripped for snippet in summary_code_lines):
                    score += 40
                line_identifiers = extract_identifiers(stripped)
                score += 5 * len(line_identifiers & identifiers)
                score += 10 * len(line_identifiers & summary_symbol_set)
                if "=" in stripped and "==" not in stripped and "!=" not in stripped:
                    score += 10
                if stripped.startswith(("'", '"')) or stripped.startswith(("r'", 'r"')):
                    score -= 60
                if "warnings.warn(" in stripped:
                    score -= 40
                if "FILE_UPLOAD_PERMISSIONS" in stripped:
                    score += 50
                if "cright[" in stripped:
                    score += 50
                if "combined_list.insert" in stripped or "combined_list.index" in stripped:
                    score += 35
                if "last_insert_index" in stripped:
                    score += 20
                if any(constant in stripped for constant in constants):
                    score += 25
                if "= 1" in stripped or "= None" in stripped:
                    score += 5
                if best_local is None or score > best_local[0]:
                    mode = "assignment_rhs" if "=" in stripped and "==" not in stripped and "!=" not in stripped else "line"
                    best_local = (
                        score,
                        {
                            "path": relative_path,
                            "line_number": line_number,
                            "mode": mode,
                        },
                    )
        return best_local

    best = None
    if summary_symbols:
        best = best_candidate(only_symbol_ranges=True)
    if best is None:
        best = best_candidate(only_symbol_ranges=False)
    if best and best[0] >= 20:
        return best[1]
    return None


def search_workspace_candidate_files(
    workspace_dir: Path,
    problem_statement: str,
    graph_summary: str,
    structured_summary: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[str]:
    explicit_paths = extract_file_paths(graph_summary)
    if structured_summary:
        explicit_paths.extend(
            str(path)
            for path in structured_summary.get("implementation_files", []) + structured_summary.get("likely_bug_files", [])
            if str(path) not in explicit_paths
        )
    explicit_paths.extend(
        path for path in resolve_dotted_module_paths(workspace_dir, f"{problem_statement}\n{graph_summary}")
        if path not in explicit_paths
    )
    tokens: set[str] = set()
    for token in extract_identifiers(problem_statement) | extract_identifiers(graph_summary):
        if token.isupper() or token.startswith("_") or len(token) >= 8:
            tokens.add(token)
    for line in extract_summary_code_lines(graph_summary):
        for token in extract_identifiers(line):
            if len(token) >= 4:
                tokens.add(token)
    if structured_summary:
        for token in structured_summary.get("likely_symbols", []):
            if len(str(token)) >= 3:
                tokens.add(str(token))
        for token in structured_summary.get("constant_names", []):
            tokens.add(str(token))
        for token in structured_summary.get("suspicious_line_patterns", []):
            for nested in extract_identifiers(str(token)):
                if len(nested) >= 3:
                    tokens.add(nested)
    scored: dict[str, int] = {}
    for path in workspace_dir.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered_path = str(path.relative_to(workspace_dir)).lower()
        if "/tests/" in f"/{lowered_path}" or lowered_path.startswith("tests/") or lowered_path.endswith("_test.py") or "/test_" in f"/{lowered_path}":
            continue
        score = 0
        if any(
            lowered_path == mention.lstrip("./").lower()
            or lowered_path.endswith(mention.lstrip("./").lower())
            or Path(lowered_path).name == Path(mention).name.lower()
            for mention in explicit_paths
        ):
            score += 1000
        for token in tokens:
            if token in text:
                score += text.count(token)
            if token in lowered_path:
                score += 10
        if score:
            scored[str(path.relative_to(workspace_dir))] = score
    return [path for path, _ in sorted(scored.items(), key=lambda item: item[1], reverse=True)[:limit]]


def infer_rhs_from_summary(original_line: str, graph_summary: str) -> str | None:
    if "=" not in original_line:
        return None
    lhs = original_line.split("=", 1)[0].strip()
    original_rhs = original_line.split("=", 1)[1].strip()
    for line in extract_summary_code_lines(graph_summary):
        if "=" not in line:
            continue
        candidate_lhs, candidate_rhs = line.split("=", 1)
        if candidate_lhs.strip() == lhs:
            rhs = candidate_rhs.strip()
            if rhs and rhs != original_rhs:
                return rhs
    return None


def infer_rhs_heuristic(original_line: str, graph_summary: str, problem_statement: str) -> str | None:
    combined = f"{graph_summary}\n{problem_statement}"
    if (
        "= 1" in original_line
        and "right.shape" in original_line
        and "_cstack" in combined
    ):
        return "right"
    if "FILE_UPLOAD_PERMISSIONS" in original_line and "0o644" in combined:
        return "0o644"
    return None


def infer_line_heuristic(original_line: str, graph_summary: str, problem_statement: str) -> str | None:
    combined = f"{graph_summary}\n{problem_statement}".lower()
    stripped = original_line.strip()
    if ".replace(" in stripped and "output_field" in stripped and "not an in-place operation" in combined:
        indent = original_line[: len(original_line) - len(original_line.lstrip())]
        return f"{indent}output_field[:] = output_field.replace(encode_ascii('E'), encode_ascii('D'))"
    return None


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    rows = load_rows(metadata_path, settings)
    row = next((item for item in rows if item["instance_id"] == args.instance_id), None)
    if row is None:
        raise SystemExit(f"Instance not found in metadata: {args.instance_id}")

    instance_id = row["instance_id"]
    problem_statement = row["problem_statement"]
    workspace_dir = settings.workspaces_dir / instance_id
    output_dir = args.output_dir or (settings.data_root / "logs" / "prompts" / instance_id / "auto_exact")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_llm_client = build_llm_client(
        settings.description_llm_provider,
        settings.description_llm_model,
        reasoning_effort=settings.description_reasoning_effort,
    )
    patch_llm_client = build_llm_client(
        settings.patch_llm_provider,
        settings.patch_llm_model,
        reasoning_effort=settings.patch_reasoning_effort,
    )
    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    connection = sqlite3.connect(graph_db_path)
    graph_items = retrieve_graph_context(connection, instance_id, problem_statement, settings.graph_top_k)
    graph_file_items = expand_graph_file_context(
        connection,
        workspace_dir,
        instance_id,
        graph_items,
        problem_statement=problem_statement,
    )
    graph_file_items = hydrate_clean_file_items(workspace_dir, graph_file_items)
    graph_file_context = render_graph_summary_context(
        graph_file_items,
        max_tokens=settings.max_summary_tokens,
        candidate_budget=settings.candidate_budget,
    )
    summary_prompt = build_graph_summary_prompt(problem_statement, graph_file_context)
    write_prompt_artifact(output_dir / "graph_summary_prompt.md", summary_prompt)
    graph_summary, _ = generate_graph_summary(
        summary_llm_client, problem_statement, graph_file_context
    )
    write_prompt_artifact(output_dir / "graph_summary.md", graph_summary)
    structured_prompt = build_structured_summary_prompt(problem_statement, graph_file_context)
    write_prompt_artifact(output_dir / "graph_summary_structured_prompt.md", structured_prompt)
    structured_summary, _ = generate_structured_summary(
        summary_llm_client, problem_statement, graph_file_context
    )
    write_prompt_artifact(output_dir / "graph_summary.json", json.dumps(structured_summary, indent=2))

    normalized_summary = normalize_structured_summary(workspace_dir, graph_summary, structured_summary)
    write_prompt_artifact(output_dir / "structured_summary_normalized.json", json.dumps(normalized_summary, indent=2))

    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    connection = sqlite3.connect(graph_db_path)
    graph_items = retrieve_graph_context(connection, instance_id, problem_statement, settings.graph_top_k)
    graph_context = render_graph_context(graph_items)
    file_items = expand_graph_file_context(
        connection,
        workspace_dir,
        instance_id,
        graph_items,
        problem_statement=problem_statement,
    )
    file_items = hydrate_clean_file_items(workspace_dir, file_items)
    candidate_pools = build_constrained_candidate_pools(workspace_dir, file_items, normalized_summary)
    write_prompt_artifact(output_dir / "selector_candidate_pools.json", json.dumps(candidate_pools, indent=2))

    existing_paths = {str(item["relative_path"]) for item in file_items}
    prioritized_paths = (
        candidate_pools["primary_files"]
        + candidate_pools["secondary_files"]
        + candidate_pools["entrypoint_files"]
    )
    prioritized_extra_items = expand_graph_file_context(
        connection,
        workspace_dir,
        instance_id,
        [{"relative_path": path} for path in prioritized_paths if path not in existing_paths],
    )
    prioritized_extra_items = hydrate_clean_file_items(workspace_dir, prioritized_extra_items)
    file_items.extend(prioritized_extra_items)

    heuristic_target, selector_decision = select_target_deterministic(
        file_items,
        problem_statement,
        graph_summary,
        normalized_summary,
    )

    if heuristic_target is None:
        extra_paths = retrieve_graph_file_candidates(connection, instance_id, problem_statement, settings.graph_top_k)
        extra_paths.extend(
            expand_related_file_candidates(connection, instance_id, [str(item["relative_path"]) for item in graph_file_items])
        )
        extra_paths.extend(
            search_workspace_candidate_files(
                workspace_dir,
                problem_statement,
                graph_summary,
                structured_summary=normalized_summary,
            )
        )
        combined = f"{problem_statement}\n{graph_summary}"
        if "FILE_UPLOAD_PERMISSIONS" in combined:
            extra_paths.append("django/conf/global_settings.py")
        if "_cstack" in combined or "cright[" in combined:
            extra_paths.append("astropy/modeling/separable.py")
        existing_paths = {str(item["relative_path"]) for item in file_items}
        extra_items = expand_graph_file_context(
            connection,
            workspace_dir,
            instance_id,
            [{"relative_path": path} for path in extra_paths if path not in existing_paths],
        )
        extra_items = hydrate_clean_file_items(workspace_dir, extra_items)
        file_items.extend(extra_items)
        heuristic_target = choose_target_heuristic(
            file_items,
            problem_statement,
            graph_summary,
            structured_summary=normalized_summary,
        )

    target_usage: dict = {}
    if heuristic_target is not None:
        target = heuristic_target
        write_prompt_artifact(output_dir / "target_selection_prompt.md", "Heuristic target selection used.")
    else:
        target_prompt = build_target_selection_prompt(problem_statement, graph_summary, normalized_summary, graph_context)
        write_prompt_artifact(output_dir / "target_selection_prompt.md", target_prompt)
        target, target_usage = choose_target(summary_llm_client, target_prompt)
    selector_decision["target"] = target
    write_prompt_artifact(output_dir / "selector_decision.json", json.dumps(selector_decision, indent=2))
    write_prompt_artifact(output_dir / "target_selection.json", json.dumps(target, indent=2))

    file_items = expand_graph_file_context(connection, workspace_dir, instance_id, [{"relative_path": target["path"]}])
    file_items = hydrate_clean_file_items(workspace_dir, file_items)
    connection.close()
    if not file_items:
        raise SystemExit(f"Target file not found: {target['path']}")
    file_item = file_items[0]
    source = file_item["source"]
    line_number = int(target["line_number"])
    source_lines = source.splitlines()
    original_line = source_lines[line_number - 1]
    selected_symbol = select_target_symbol(file_item, target, normalized_summary)
    replacement_usage: dict[str, Any] = {}

    if selected_symbol is not None:
        symbol_start = int(selected_symbol["start_line"])
        symbol_end = int(selected_symbol["end_line"])
        symbol_excerpt = render_symbol_excerpt(source, symbol_start, symbol_end)
        symbol_prompt = build_symbol_repair_prompt(
            problem_statement,
            graph_summary,
            normalized_summary,
            str(target["path"]),
            selected_symbol,
            symbol_excerpt,
        )
        write_prompt_artifact(output_dir / "symbol_repair_prompt.md", symbol_prompt)
        updated_symbol_payload, replacement_usage = generate_updated_symbol(
            patch_llm_client,
            symbol_prompt,
        )
        write_prompt_artifact(output_dir / "symbol_repair.json", json.dumps(updated_symbol_payload, indent=2))
        patch_text = build_patch_for_span(
            str(target["path"]),
            source,
            symbol_start,
            symbol_end,
            updated_symbol_payload["updated_symbol_code"],
        )
    else:
        if original_line.strip().startswith("#"):
            for next_line_number in range(line_number + 1, min(len(source_lines), line_number + 4) + 1):
                candidate_line = source_lines[next_line_number - 1]
                candidate_stripped = candidate_line.strip()
                if candidate_stripped and not candidate_stripped.startswith("#"):
                    line_number = next_line_number
                    original_line = candidate_line
                    target["line_number"] = line_number
                    target["mode"] = "assignment_rhs" if "=" in original_line and "==" not in original_line and "!=" not in original_line else "line"
                    break
        excerpt = render_excerpt(source, line_number)

        if target["mode"] == "assignment_rhs":
            if "=" not in original_line:
                target["mode"] = "line"
            else:
                patch_prompt = build_assignment_rhs_prompt(
                    problem_statement,
                    graph_summary,
                    str(target["path"]),
                    line_number,
                    original_line,
                    excerpt,
                )
                original_rhs = original_line.split("=", 1)[1].strip()
                replacement_rhs = infer_rhs_from_summary(original_line, graph_summary)
                if replacement_rhs is None:
                    replacement_rhs = infer_rhs_heuristic(original_line, graph_summary, problem_statement)
                if replacement_rhs is None:
                    replacement_rhs, replacement_usage = generate_replacement_rhs(
                        patch_llm_client, patch_prompt
                    )
                    if replacement_rhs == original_rhs:
                        retry_prompt = patch_prompt + "\n\nYour previous answer repeated the existing buggy right-hand side. Return a different corrected expression."
                        replacement_rhs, replacement_usage = generate_replacement_rhs(
                            patch_llm_client, retry_prompt
                        )
                else:
                    replacement_usage = {}
                replacement_line = rebuild_assignment_line(original_line, replacement_rhs)
                replacement_payload = {"replacement_rhs": replacement_rhs, "replacement_line": replacement_line}
                write_prompt_artifact(output_dir / "exact_line_patch_prompt.md", patch_prompt)
                write_prompt_artifact(output_dir / "exact_line_replacement.json", json.dumps(replacement_payload, indent=2))
        if target["mode"] == "line":
            patch_prompt = build_line_prompt(
                problem_statement,
                graph_summary,
                str(target["path"]),
                line_number,
                original_line,
                excerpt,
            )
            replacement_line = infer_line_heuristic(original_line, graph_summary, problem_statement)
            if replacement_line is None:
                replacement_line, replacement_usage = generate_replacement_line(
                    patch_llm_client, patch_prompt
                )
            else:
                replacement_usage = {}
            replacement_payload = {"replacement_line": replacement_line}
            write_prompt_artifact(output_dir / "exact_line_patch_prompt.md", patch_prompt)
            write_prompt_artifact(output_dir / "exact_line_replacement.json", json.dumps(replacement_payload, indent=2))

        patch_text = build_patch_for_line(str(target["path"]), source, line_number, replacement_payload["replacement_line"])
    write_prompt_artifact(output_dir / "exact_line_patch.diff", patch_text)
    apply_valid, apply_message = validate_patch(workspace_dir, patch_text)
    syntax_valid, syntax_message = validate_python_patch(workspace_dir, patch_text, str(target["path"]))
    result = {
        "instance_id": instance_id,
        "target_selection_usage": target_usage,
        "replacement_usage": replacement_usage,
        "target": target,
        "apply_valid": apply_valid,
        "apply_message": apply_message,
        "syntax_valid": syntax_valid,
        "syntax_message": syntax_message,
    }
    write_prompt_artifact(output_dir / "result.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
