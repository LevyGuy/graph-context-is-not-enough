from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional fallback helper
    repair_json = None

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_llm_client, with_retries
from run_inference import expand_graph_file_context, validate_patch, write_prompt_artifact

LINE_REPLACEMENT_SYSTEM_PROMPT = """You fix bugs by replacing exactly one existing source line.
Return only valid JSON with exactly one key: replacement_line.
The value must be a single source code line including indentation.

Rules:
- Replace exactly the target line and nothing else.
- Preserve indentation.
- Do not add surrounding lines.
- Do not change semantics beyond the minimal fix."""

ASSIGNMENT_RHS_SYSTEM_PROMPT = """You fix bugs by replacing only the right-hand side of a Python assignment.
Return only valid JSON with exactly one key: replacement_rhs.
The value must be a single Python expression with no surrounding prose.

Rules:
- Return only the new right-hand side expression.
- Do not include the left-hand side.
- Do not include comments or surrounding lines.
- Make the smallest correct change."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a one-line replacement patch for a single source line.")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--target-file", required=True)
    parser.add_argument("--line-number", type=int, required=True)
    parser.add_argument(
        "--assignment-rhs",
        action="store_true",
        help="Interpret the target line as a Python assignment and ask only for the replacement RHS.",
    )
    parser.add_argument("--metadata-path", type=Path, default=None)
    parser.add_argument("--summary-path", type=Path, default=None)
    parser.add_argument("--graph-db-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def load_rows(metadata_path: Path, settings) -> list[dict]:
    if metadata_path == settings.metadata_dir / "instances.jsonl":
        return read_metadata(settings.metadata_dir)
    rows: list[dict] = []
    with metadata_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def resolve_summary_path(settings, instance_id: str, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    candidates = [
        settings.data_root / "logs" / "prompts" / instance_id / "graph_summary.md",
        settings.data_root / "debug" / instance_id / "graph_summary.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No graph summary found for {instance_id}")


def build_line_prompt(
    problem_statement: str,
    graph_summary: str,
    relative_path: str,
    line_number: int,
    original_line: str,
    excerpt: str,
) -> str:
    return f"""Problem statement:
{problem_statement}

Graph summary:
{graph_summary}

Target file:
{relative_path}

Target line number:
{line_number}

Original line:
```python
{original_line}
```

Local excerpt:
```python
{excerpt}
```

Return only the corrected replacement for the target line."""


def build_assignment_rhs_prompt(
    problem_statement: str,
    graph_summary: str,
    relative_path: str,
    line_number: int,
    original_line: str,
    excerpt: str,
) -> str:
    lhs, rhs = original_line.split("=", 1)
    return f"""Problem statement:
{problem_statement}

Graph summary:
{graph_summary}

Target file:
{relative_path}

Target line number:
{line_number}

Original assignment line:
```python
{original_line}
```

Left-hand side to preserve exactly:
```python
{lhs.rstrip()} =
```

Current right-hand side:
```python
{rhs.strip()}
```

Local excerpt:
```python
{excerpt}
```

    Return only the corrected right-hand side expression.

Requirements:
- If the current right-hand side is the suspected bug, do not repeat it unchanged.
- Prefer the smallest semantic change that fixes the bug.
- Your output must be a valid replacement for the existing assignment line."""


def render_excerpt(source: str, line_number: int, context_radius: int = 3) -> str:
    lines = source.splitlines()
    start_index = max(0, line_number - 1 - context_radius)
    end_index = min(len(lines), line_number + context_radius)
    return "\n".join(lines[start_index:end_index])


def build_patch_for_line(relative_path: str, original_text: str, line_number: int, replacement_line: str) -> str:
    lines = original_text.splitlines(keepends=True)
    index = line_number - 1
    replacement = replacement_line if replacement_line.endswith("\n") else replacement_line + "\n"
    updated = list(lines)
    updated[index] = replacement
    import difflib

    diff_lines = list(
        difflib.unified_diff(
            lines,
            updated,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    return "".join(line if line.endswith("\n") else f"{line}\n" for line in diff_lines)


def rebuild_assignment_line(original_line: str, replacement_rhs: str) -> str:
    indent = original_line[: len(original_line) - len(original_line.lstrip())]
    lhs, rhs = original_line.split("=", 1)
    rhs_body = rhs.rstrip()
    trailing_comma = "," if rhs_body.endswith(",") else ""
    comment = ""
    if "#" in rhs_body:
        before_comment, comment_part = rhs_body.split("#", 1)
        rhs_body = before_comment.rstrip()
        comment = "  #" + comment_part
        trailing_comma = "," if rhs_body.endswith(",") else trailing_comma
    return f"{indent}{lhs.strip()} = {replacement_rhs}{trailing_comma}{comment}"


def validate_python_patch(workspace_dir: Path, patch_text: str, target_file: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_dir = tmp_path / "repo"
        subprocess.run(
            ["git", "clone", "--quiet", str(workspace_dir), str(repo_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        patch_path = tmp_path / "candidate.diff"
        patch_path.write_text(patch_text if patch_text.endswith("\n") else patch_text + "\n", encoding="utf-8")
        apply_result = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if apply_result.returncode != 0:
            return False, ((apply_result.stdout or "") + (apply_result.stderr or "")).strip()
        compile_result = subprocess.run(
            [
                str(Path.cwd() / ".venv" / "bin" / "python"),
                "-m",
                "py_compile",
                str(repo_dir / target_file),
            ],
            capture_output=True,
            text=True,
        )
        combined = (compile_result.stdout or "") + (compile_result.stderr or "")
        return compile_result.returncode == 0, combined.strip()


def generate_replacement_line(llm_client, prompt: str) -> tuple[str, dict]:
    text, usage = with_retries(lambda: llm_client.generate_text(LINE_REPLACEMENT_SYSTEM_PROMPT, prompt))
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            if repair_json is None:
                raise
            payload = json.loads(repair_json(cleaned))
        except Exception:
            payload = None
    if isinstance(payload, dict) and "replacement_line" in payload:
        return str(payload["replacement_line"]), usage
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and "replacement_line" in first:
            return str(first["replacement_line"]), usage
        return str(first), usage
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    if cleaned:
        return cleaned.splitlines()[0], usage
    raise ValueError("Model did not return a usable replacement line")


def generate_replacement_rhs(llm_client, prompt: str) -> tuple[str, dict]:
    text, usage = with_retries(lambda: llm_client.generate_text(ASSIGNMENT_RHS_SYSTEM_PROMPT, prompt))
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            if repair_json is None:
                raise
            payload = json.loads(repair_json(cleaned))
        except Exception:
            payload = None
    if isinstance(payload, dict) and "replacement_rhs" in payload:
        return str(payload["replacement_rhs"]).strip(), usage
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and "replacement_rhs" in first:
            return str(first["replacement_rhs"]).strip(), usage
        return str(first).strip(), usage
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    if cleaned:
        return cleaned.splitlines()[0].strip(), usage
    raise ValueError("Model did not return a usable replacement rhs")


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
    output_dir = args.output_dir or (settings.data_root / "logs" / "prompts" / instance_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = resolve_summary_path(settings, instance_id, args.summary_path)
    graph_summary = summary_path.read_text(encoding="utf-8")
    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    connection = sqlite3.connect(graph_db_path)
    file_items = expand_graph_file_context(
        connection,
        workspace_dir,
        instance_id,
        [{"relative_path": args.target_file}],
    )
    connection.close()
    if not file_items:
        raise SystemExit(f"Target file not found in graph/workspace: {args.target_file}")

    file_item = file_items[0]
    source = file_item["source"]
    lines = source.splitlines()
    original_line = lines[args.line_number - 1]
    excerpt = render_excerpt(source, args.line_number)
    if args.assignment_rhs:
        prompt = build_assignment_rhs_prompt(
            problem_statement,
            graph_summary,
            args.target_file,
            args.line_number,
            original_line,
            excerpt,
        )
    else:
        prompt = build_line_prompt(
            problem_statement,
            graph_summary,
            args.target_file,
            args.line_number,
            original_line,
            excerpt,
        )
    write_prompt_artifact(output_dir / "exact_line_patch_prompt.md", prompt)

    llm_client = build_llm_client(settings.patch_llm_provider, settings.patch_llm_model)
    if args.assignment_rhs:
        replacement_rhs, usage = generate_replacement_rhs(llm_client, prompt)
        replacement_line = rebuild_assignment_line(original_line, replacement_rhs)
        write_prompt_artifact(output_dir / "exact_line_raw_response.txt", replacement_rhs)
        write_prompt_artifact(output_dir / "exact_line_replacement.json", json.dumps({"replacement_rhs": replacement_rhs, "replacement_line": replacement_line}, indent=2))
    else:
        replacement_line, usage = generate_replacement_line(llm_client, prompt)
        write_prompt_artifact(output_dir / "exact_line_raw_response.txt", replacement_line)
        write_prompt_artifact(output_dir / "exact_line_replacement.json", json.dumps({"replacement_line": replacement_line}, indent=2))

    patch_text = build_patch_for_line(args.target_file, source, args.line_number, replacement_line)
    write_prompt_artifact(output_dir / "exact_line_patch.diff", patch_text)

    apply_valid, apply_message = validate_patch(workspace_dir, patch_text)
    syntax_valid, syntax_message = validate_python_patch(workspace_dir, patch_text, args.target_file)
    result = {
        "instance_id": instance_id,
        "target_file": args.target_file,
        "line_number": args.line_number,
        "usage": usage,
        "apply_valid": apply_valid,
        "apply_message": apply_message,
        "syntax_valid": syntax_valid,
        "syntax_message": syntax_message,
    }
    write_prompt_artifact(output_dir / "exact_line_patch_result.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
