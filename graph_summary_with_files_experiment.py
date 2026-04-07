from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_llm_client
from run_inference import (
    build_patch_prompt,
    ensure_valid_patch,
    expand_graph_file_context,
    generate_patch,
    retrieve_graph_context,
    write_prompt_artifact,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a patch using the graph summary plus actual target file contents."
    )
    parser.add_argument("--instance-id", required=True, help="SWE-bench instance_id to inspect.")
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Path to the metadata jsonl file. Defaults to artifacts/metadata/instances.jsonl.",
    )
    parser.add_argument(
        "--graph-db-path",
        type=Path,
        default=None,
        help="Path to enriched_graph.db.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Path to an existing graph_summary.md. Defaults to logs/prompts/<instance_id>/graph_summary.md or artifacts/debug/<instance_id>/graph_summary.md.",
    )
    parser.add_argument(
        "--target-file",
        action="append",
        default=[],
        help="Relative file path to include. Can be passed multiple times. Defaults to top graph-retrieved files.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=2,
        help="Maximum number of auto-selected files to include when --target-file is not provided.",
    )
    parser.add_argument(
        "--start-line",
        type=int,
        default=None,
        help="Optional 1-based start line for bounded fallback edits.",
    )
    parser.add_argument(
        "--end-line",
        type=int,
        default=None,
        help="Optional 1-based end line for bounded fallback edits.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write artifacts. Defaults to artifacts/logs/prompts/<instance_id>/.",
    )
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


def render_target_file_context(file_items: list[dict]) -> str:
    blocks: list[str] = []
    for item in file_items:
        blocks.append(
            f"""Target file: {item['relative_path']}
```python
{item['source']}
```"""
        )
    return "\n\n".join(blocks)


def select_target_files(
    connection: sqlite3.Connection,
    workspace_dir: Path,
    instance_id: str,
    problem_statement: str,
    top_k: int,
    max_files: int,
    explicit_files: list[str],
) -> list[dict]:
    if explicit_files:
        graph_items = [{"relative_path": path} for path in explicit_files]
    else:
        graph_hits = retrieve_graph_context(connection, instance_id, problem_statement, top_k)
        seen: list[str] = []
        for item in graph_hits:
            relative_path = item["relative_path"]
            if relative_path not in seen:
                seen.append(relative_path)
            if len(seen) >= max_files:
                break
        graph_items = [{"relative_path": path} for path in seen]
    return expand_graph_file_context(connection, workspace_dir, instance_id, graph_items)


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
    file_items = select_target_files(
        connection,
        workspace_dir,
        instance_id,
        problem_statement,
        settings.graph_top_k,
        args.max_files,
        args.target_file,
    )
    connection.close()

    target_files_context = render_target_file_context(file_items)
    llm_client = build_llm_client(settings.patch_llm_provider, settings.patch_llm_model)
    prompt = build_patch_prompt(
        problem_statement,
        target_files_context,
        "graph_summary_with_files",
        graph_summary=graph_summary,
    )
    write_prompt_artifact(output_dir / "graph_summary_with_files_context.md", target_files_context)
    write_prompt_artifact(output_dir / "graph_summary_with_files_patch_prompt.md", prompt)

    patch_text, generation_usage = generate_patch(
        llm_client,
        problem_statement,
        target_files_context,
        "graph_summary_with_files",
        graph_summary=graph_summary,
    )
    patch_text, repair_usage = ensure_valid_patch(
        llm_client,
        workspace_dir,
        problem_statement,
        target_files_context,
        "graph_summary_with_files",
        patch_text,
        [item["relative_path"] for item in file_items],
        graph_summary=graph_summary,
        allowed_regions=(
            [
                {
                    "path": item["relative_path"],
                    "start_line": args.start_line,
                    "end_line": args.end_line,
                }
                for item in file_items
            ]
            if args.start_line is not None and args.end_line is not None
            else None
        ),
    )
    write_prompt_artifact(output_dir / "graph_summary_with_files_patch.diff", patch_text)

    result = {
        "instance_id": instance_id,
        "summary_path": str(summary_path),
        "output_dir": str(output_dir),
        "target_files": [item["relative_path"] for item in file_items],
        "generation_usage": generation_usage,
        "repair_usage": repair_usage,
    }
    write_prompt_artifact(output_dir / "graph_summary_with_files_result.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
