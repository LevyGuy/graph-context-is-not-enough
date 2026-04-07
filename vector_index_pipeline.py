from __future__ import annotations

import argparse
import json
from pathlib import Path

import chromadb
from chromadb.api.types import EmbeddingFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tqdm import tqdm

from experiment.budget import estimate_text_cost_usd, load_budget_state, record_budget_event
from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import batched, build_embedding_client, estimate_tokens


class CustomEmbeddingFunction(EmbeddingFunction):
    def __init__(self, provider: str, model: str) -> None:
        self.client = build_embedding_client(provider, model)

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.client.embed_texts(input)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the vector-search baseline.")
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
    return parser.parse_args()


def python_files(root: Path) -> list[Path]:
    selected: list[Path] = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts:
            continue
        relative = str(path.relative_to(root)).lower()
        if relative.endswith("conftest.py"):
            continue
        if "/tests/" in f"/{relative}" or relative.startswith("tests/"):
            continue
        if "/test_" in f"/{relative}" or relative.endswith("_test.py"):
            continue
        selected.append(path)
    return selected


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", " ", ""],
    )

    client = chromadb.PersistentClient(path=str(settings.vector_db_dir))
    collection = client.get_or_create_collection(
        name=args.collection_name,
        embedding_function=CustomEmbeddingFunction(
            provider=settings.embedding_provider,
            model=settings.embedding_model,
        ),
        metadata={"hnsw:space": "cosine"},
    )

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    rows = read_metadata(metadata_path.parent)
    if metadata_path != settings.metadata_dir / "instances.jsonl":
        import json

        rows = []
        with metadata_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    total_embedding_tokens = 0
    for row in rows:
        instance_id = row["instance_id"]
        workspace_dir = settings.workspaces_dir / instance_id
        for file_path in tqdm(python_files(workspace_dir), desc=f"Chunking {instance_id}"):
            source = file_path.read_text(encoding="utf-8", errors="replace")
            chunks = splitter.split_text(source)
            if not chunks:
                continue
            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict] = []
            for index, chunk in enumerate(chunks):
                chunk_id = f"{instance_id}:{file_path.relative_to(workspace_dir)}:{index}"
                ids.append(chunk_id)
                documents.append(chunk)
                total_embedding_tokens += estimate_tokens(chunk)
                metadatas.append(
                    {
                        "instance_id": instance_id,
                        "repo_name": row["repo_name"],
                        "relative_path": str(file_path.relative_to(workspace_dir)),
                        "chunk_index": index,
                    }
                )
            for ids_batch, docs_batch, meta_batch in zip(
                batched(ids, 64),
                batched(documents, 64),
                batched(metadatas, 64),
            ):
                collection.upsert(ids=ids_batch, documents=docs_batch, metadatas=meta_batch)

    estimated_cost = estimate_text_cost_usd(settings.embedding_model, total_embedding_tokens, 0)
    budget_state = load_budget_state(settings.metadata_dir)
    if budget_state.get("spent_usd", 0.0) + estimated_cost > settings.max_budget_usd:
        raise RuntimeError(
            f"Budget cap reached after vector indexing estimate. "
            f"Spent=${budget_state.get('spent_usd', 0.0):.2f}, "
            f"embedding_estimate=${estimated_cost:.2f}, cap=${settings.max_budget_usd:.2f}"
        )
    record_budget_event(
        settings.metadata_dir,
        phase="vector_embedding",
        model=settings.embedding_model,
        input_tokens=total_embedding_tokens,
        output_tokens=0,
        cost_usd=estimated_cost,
        metadata={"collection_name": args.collection_name},
    )

    print(
        json.dumps(
            {
                "vector_db_dir": str(settings.vector_db_dir),
                "collection_name": args.collection_name,
                "estimated_embedding_cost_usd": round(estimated_cost, 4),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
