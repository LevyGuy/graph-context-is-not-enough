from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import chromadb

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_embedding_client, build_llm_client
from experiment.utils import write_json
from run_inference import (
    build_graph_summary_prompt,
    build_patch_prompt,
    expand_graph_file_context,
    generate_graph_summary,
    generate_patch,
    render_graph_context,
    render_graph_summary_context,
    render_vector_context,
    retrieve_graph_context,
    retrieve_vector_context,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug one SWE-bench instance end to end.")
    parser.add_argument("--instance-id", required=True, help="SWE-bench instance_id to inspect.")
    parser.add_argument(
        "--graph-db-path",
        type=Path,
        default=None,
        help="Path to enriched_graph.db.",
    )
    parser.add_argument(
        "--collection-name",
        default="swebench_python_chunks",
        help="Chroma collection name.",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Path to the metadata jsonl file. Defaults to artifacts/metadata/instances.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write debug artifacts. Defaults to artifacts/debug/<instance_id>/.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Do not call the LLM. Still writes retrieval context and prompts.",
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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    rows = load_rows(metadata_path, settings)
    row = next((item for item in rows if item["instance_id"] == args.instance_id), None)
    if row is None:
        raise SystemExit(f"Instance not found in metadata: {args.instance_id}")

    output_dir = args.output_dir or (settings.data_root / "debug" / args.instance_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    graph_connection = sqlite3.connect(graph_db_path)
    chroma_client = chromadb.PersistentClient(path=str(settings.vector_db_dir))
    collection = chroma_client.get_collection(name=args.collection_name)

    embedding_client = build_embedding_client(settings.embedding_provider, settings.embedding_model)
    llm_client = None if args.skip_llm else build_llm_client(
        settings.patch_llm_provider, settings.patch_llm_model
    )

    instance_id = row["instance_id"]
    problem_statement = row["problem_statement"]
    workspace_dir = settings.workspaces_dir / instance_id

    graph_items = retrieve_graph_context(
        graph_connection, instance_id, problem_statement, settings.graph_top_k
    )
    graph_symbol_context = render_graph_context(graph_items)

    graph_file_items = expand_graph_file_context(
        graph_connection,
        workspace_dir,
        instance_id,
        graph_items,
        problem_statement=problem_statement,
    )
    graph_file_context = render_graph_summary_context(graph_file_items)
    graph_summary_prompt = build_graph_summary_prompt(problem_statement, graph_file_context)

    vector_items = retrieve_vector_context(
        collection,
        embedding_client,
        instance_id,
        problem_statement,
        settings.vector_top_k,
    )
    vector_context = render_vector_context(vector_items)

    graph_summary = ""
    graph_summary_usage: dict = {}
    if llm_client is not None:
        graph_summary, graph_summary_usage = generate_graph_summary(
            llm_client, problem_statement, graph_file_context
        )

    graph_hybrid_context = "\n\n".join(
        [
            "Graph symbol context:",
            graph_symbol_context,
            "Actual code context from vector retrieval:",
            vector_context,
        ]
    )
    graph_patch_prompt = build_patch_prompt(
        problem_statement,
        graph_hybrid_context,
        "graph_hybrid",
        graph_summary=graph_summary or None,
    )
    vector_patch_prompt = build_patch_prompt(problem_statement, vector_context, "vector")

    graph_patch = ""
    graph_patch_usage: dict = {}
    vector_patch = ""
    vector_patch_usage: dict = {}
    if llm_client is not None:
        graph_patch, graph_patch_usage = generate_patch(
            llm_client,
            problem_statement,
            graph_hybrid_context,
            "graph_hybrid",
            graph_summary=graph_summary,
        )
        vector_patch, vector_patch_usage = generate_patch(
            llm_client, problem_statement, vector_context, "vector"
        )

    payload = {
        "instance": row,
        "graph_items": graph_items,
        "graph_file_items": graph_file_items,
        "vector_items": vector_items,
        "graph_summary_usage": graph_summary_usage,
        "graph_patch_usage": graph_patch_usage,
        "vector_patch_usage": vector_patch_usage,
    }
    write_json(output_dir / "debug_payload.json", payload)

    write_text(output_dir / "problem_statement.md", problem_statement)
    write_text(output_dir / "graph_symbol_context.md", graph_symbol_context)
    write_text(output_dir / "graph_file_context.md", graph_file_context)
    write_text(output_dir / "graph_summary_prompt.md", graph_summary_prompt)
    write_text(output_dir / "graph_summary.md", graph_summary)
    write_text(output_dir / "vector_context.md", vector_context)
    write_text(output_dir / "graph_patch_prompt.md", graph_patch_prompt)
    write_text(output_dir / "graph_patch.diff", graph_patch)
    write_text(output_dir / "vector_patch_prompt.md", vector_patch_prompt)
    write_text(output_dir / "vector_patch.diff", vector_patch)

    graph_connection.close()

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "files": [
                    "problem_statement.md",
                    "graph_symbol_context.md",
                    "graph_file_context.md",
                    "graph_summary_prompt.md",
                    "graph_summary.md",
                    "vector_context.md",
                    "graph_patch_prompt.md",
                    "graph_patch.diff",
                    "vector_patch_prompt.md",
                    "vector_patch.diff",
                    "debug_payload.json",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
