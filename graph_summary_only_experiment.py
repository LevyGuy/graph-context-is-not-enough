from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_llm_client
from run_inference import (
    build_patch_prompt,
    ensure_valid_patch,
    generate_patch,
    write_prompt_artifact,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a patch using only the graph summary and problem statement."
    )
    parser.add_argument("--instance-id", required=True, help="SWE-bench instance_id to inspect.")
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Path to the metadata jsonl file. Defaults to artifacts/metadata/instances.jsonl.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Path to an existing graph_summary.md. Defaults to logs/prompts/<instance_id>/graph_summary.md or artifacts/debug/<instance_id>/graph_summary.md.",
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
    llm_client = build_llm_client(settings.patch_llm_provider, settings.patch_llm_model)

    context = "No raw code or vector retrieval context is provided for this run. Use only the graph summary."
    prompt = build_patch_prompt(
        problem_statement,
        context,
        "graph_summary_only",
        graph_summary=graph_summary,
    )
    write_prompt_artifact(output_dir / "graph_summary_only_patch_prompt.md", prompt)

    patch_text, generation_usage = generate_patch(
        llm_client,
        problem_statement,
        context,
        "graph_summary_only",
        graph_summary=graph_summary,
    )
    patch_text, repair_usage = ensure_valid_patch(
        llm_client,
        workspace_dir,
        problem_statement,
        context,
        "graph_summary_only",
        patch_text,
        [],
        graph_summary=graph_summary,
        allow_fallback=False,
    )
    write_prompt_artifact(output_dir / "graph_summary_only_patch.diff", patch_text)

    result = {
        "instance_id": instance_id,
        "summary_path": str(summary_path),
        "output_dir": str(output_dir),
        "generation_usage": generation_usage,
        "repair_usage": repair_usage,
    }
    write_prompt_artifact(output_dir / "graph_summary_only_result.json", json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
