from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_llm_client
from run_inference import (
    build_patch_prompt,
    build_patch_repair_prompt,
    ensure_valid_patch,
    expand_graph_file_context,
    generate_patch,
    validate_patch,
    with_retries,
    write_prompt_artifact,
    PATCH_REPAIR_SYSTEM_PROMPT,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and validate a patch for one symbol window.")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--target-file", required=True)
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
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


def render_context(file_item: dict, start_line: int, end_line: int) -> str:
    lines = file_item["source"].splitlines()
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), end_line)
    excerpt = "\n".join(lines[start_index:end_index])
    return f"""Target file: {file_item['relative_path']}
Allowed edit range: {start_line}-{end_line}

Full file:
```python
{file_item['source']}
```

Target excerpt:
```python
{excerpt}
```"""


def validate_python_patch(workspace_dir: Path, patch_text: str, target_file: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        shutil.copytree(workspace_dir, tmp_path / "repo", dirs_exist_ok=True)
        patch_path = tmp_path / "candidate.diff"
        patch_path.write_text(patch_text if patch_text.endswith("\n") else patch_text + "\n", encoding="utf-8")
        apply_result = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=tmp_path / "repo",
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
                str(tmp_path / "repo" / target_file),
            ],
            capture_output=True,
            text=True,
        )
        combined = (compile_result.stdout or "") + (compile_result.stderr or "")
        return compile_result.returncode == 0, combined.strip()


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
    context = render_context(file_item, args.start_line, args.end_line)
    llm_client = build_llm_client(settings.patch_llm_provider, settings.patch_llm_model)
    prompt = build_patch_prompt(
        problem_statement,
        context,
        "single_symbol_graph_summary",
        graph_summary=graph_summary,
    )
    write_prompt_artifact(output_dir / "single_symbol_patch_prompt.md", prompt)

    patch_text, generation_usage = generate_patch(
        llm_client,
        problem_statement,
        context,
        "single_symbol_graph_summary",
        graph_summary=graph_summary,
    )
    patch_text, repair_usage = ensure_valid_patch(
        llm_client,
        workspace_dir,
        problem_statement,
        context,
        "single_symbol_graph_summary",
        patch_text,
        [args.target_file],
        graph_summary=graph_summary,
        allowed_regions=[
            {
                "path": args.target_file,
                "start_line": args.start_line,
                "end_line": args.end_line,
            }
        ],
    )

    apply_valid, apply_message = validate_patch(workspace_dir, patch_text)
    syntax_valid, syntax_message = validate_python_patch(workspace_dir, patch_text, args.target_file)

    if apply_valid and not syntax_valid:
        repair_prompt = build_patch_repair_prompt(
            problem_statement,
            context,
            "single_symbol_graph_summary",
            patch_text,
            f"Python syntax validation failed after applying the patch:\n{syntax_message}",
            graph_summary=graph_summary,
        )
        write_prompt_artifact(output_dir / "single_symbol_syntax_repair_prompt.md", repair_prompt)
        repaired_patch, syntax_repair_usage = with_retries(
            lambda: llm_client.generate_text(PATCH_REPAIR_SYSTEM_PROMPT, repair_prompt)
        )
        repaired_patch, syntax_repair_followup = ensure_valid_patch(
            llm_client,
            workspace_dir,
            problem_statement,
            context,
            "single_symbol_graph_summary",
            repaired_patch,
            [args.target_file],
            graph_summary=graph_summary,
            allowed_regions=[
                {
                    "path": args.target_file,
                    "start_line": args.start_line,
                    "end_line": args.end_line,
                }
            ],
        )
        patch_text = repaired_patch
        apply_valid, apply_message = validate_patch(workspace_dir, patch_text)
        syntax_valid, syntax_message = validate_python_patch(workspace_dir, patch_text, args.target_file)
    else:
        syntax_repair_usage = {}
        syntax_repair_followup = {}

    write_prompt_artifact(output_dir / "single_symbol_patch.diff", patch_text)
    result = {
        "instance_id": instance_id,
        "target_file": args.target_file,
        "start_line": args.start_line,
        "end_line": args.end_line,
        "generation_usage": generation_usage,
        "repair_usage": repair_usage,
        "syntax_repair_usage": syntax_repair_usage,
        "syntax_repair_followup": syntax_repair_followup,
        "apply_valid": apply_valid,
        "apply_message": apply_message,
        "syntax_valid": syntax_valid,
        "syntax_message": syntax_message,
    }
    write_prompt_artifact(output_dir / "single_symbol_patch_result.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
