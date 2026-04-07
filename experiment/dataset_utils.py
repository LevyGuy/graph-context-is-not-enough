from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from datasets import load_dataset
except Exception:  # pragma: no cover - optional for local metadata-file flows
    load_dataset = None

from experiment.config import Settings
from experiment.utils import write_jsonl


def normalize_repo_name(value: str) -> str:
    repo = value.strip()
    repo = repo.removeprefix("https://github.com/")
    repo = repo.removesuffix(".git")
    return repo


def build_clone_url(record: dict[str, Any]) -> str:
    for key in ("repo", "repo_name", "repository"):
        if record.get(key):
            repo_name = normalize_repo_name(str(record[key]))
            if "/" in repo_name:
                return f"https://github.com/{repo_name}.git"
    raise KeyError("Dataset row is missing a GitHub repository field.")


def build_repo_name(record: dict[str, Any]) -> str:
    for key in ("repo", "repo_name", "repository"):
        if record.get(key):
            return normalize_repo_name(str(record[key]))
    raise KeyError("Dataset row is missing a repository name.")


def dataset_rows(settings: Settings) -> list[dict[str, Any]]:
    if load_dataset is None:
        raise RuntimeError("datasets is not installed; dataset_rows requires the Hugging Face datasets package.")
    dataset = load_dataset(settings.dataset_name, split=settings.dataset_split)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(dataset):
        if index >= settings.max_instances:
            break
        record = dict(row)
        rows.append(
            {
                "instance_id": record["instance_id"],
                "base_commit": record["base_commit"],
                "problem_statement": record["problem_statement"],
                "repo_name": build_repo_name(record),
                "clone_url": build_clone_url(record),
            }
        )
    return rows


def all_dataset_rows(dataset_name: str, dataset_split: str) -> list[dict[str, Any]]:
    if load_dataset is None:
        raise RuntimeError("datasets is not installed; all_dataset_rows requires the Hugging Face datasets package.")
    dataset = load_dataset(dataset_name, split=dataset_split)
    rows: list[dict[str, Any]] = []
    for row in dataset:
        record = dict(row)
        rows.append(
            {
                "instance_id": record["instance_id"],
                "base_commit": record["base_commit"],
                "problem_statement": record["problem_statement"],
                "repo_name": build_repo_name(record),
                "clone_url": build_clone_url(record),
            }
        )
    return rows


def metadata_file(metadata_dir: Path) -> Path:
    return metadata_dir / "instances.jsonl"


def write_metadata(rows: list[dict[str, Any]], metadata_dir: Path) -> Path:
    path = metadata_file(metadata_dir)
    write_jsonl(path, rows)
    return path


def read_metadata(metadata_dir: Path) -> list[dict[str, Any]]:
    return read_metadata_file(metadata_file(metadata_dir))


def read_metadata_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


STACKTRACE_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)')


def extract_stacktrace_file_hints(problem_statement: str) -> list[str]:
    hints: list[str] = []
    for match in STACKTRACE_FILE_RE.finditer(problem_statement):
        hints.append(match.group(1))
    return hints
