from __future__ import annotations

import argparse
import json
import random
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import all_dataset_rows
from experiment.utils import write_json, write_jsonl
from graph_index_pipeline import open_database


@dataclass(frozen=True)
class RepoAllocation:
    repo_name: str
    available: int
    allocated: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and run the research-grade graph localization study.")
    parser.add_argument("--study-name", default="localization_study_120")
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--audit-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260331)
    parser.add_argument("--min-per-repo", type=int, default=3)
    parser.add_argument("--prepare-missing", action="store_true")
    parser.add_argument("--index-missing", action="store_true")
    parser.add_argument("--run-benchmark", action="store_true")
    parser.add_argument("--select-audit", action="store_true")
    parser.add_argument("--structural-only", action="store_true")
    parser.add_argument("--reuse-graph-db", action="store_true")
    parser.add_argument("--reuse-cache", action="store_true")
    parser.add_argument("--sample-path", type=Path, default=None)
    parser.add_argument("--max-summary-tokens", type=int, default=None)
    parser.add_argument("--candidate-budget", type=int, default=None)
    return parser.parse_args()


def stable_repo_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["repo_name"])].append(row)
    for repo_name in grouped:
        grouped[repo_name] = sorted(grouped[repo_name], key=lambda item: str(item["instance_id"]))
    return dict(sorted(grouped.items()))


def allocate_sample(rows: list[dict[str, Any]], sample_size: int, min_per_repo: int) -> list[RepoAllocation]:
    grouped = stable_repo_rows(rows)
    if sample_size > len(rows):
        raise ValueError(f"Requested sample_size={sample_size} exceeds dataset size {len(rows)}")

    base_allocations: dict[str, int] = {}
    for repo_name, repo_rows in grouped.items():
        repo_count = len(repo_rows)
        base_allocations[repo_name] = min(repo_count, min_per_repo)

    base_total = sum(base_allocations.values())
    if base_total > sample_size:
        raise ValueError(
            f"Minimum-per-repo allocation requires {base_total} samples but sample_size={sample_size}"
        )

    remaining = sample_size - base_total
    capacities = {
        repo_name: len(repo_rows) - base_allocations[repo_name]
        for repo_name, repo_rows in grouped.items()
    }
    total_capacity = sum(capacities.values())
    extra_allocations = {repo_name: 0 for repo_name in grouped}
    if remaining > 0 and total_capacity > 0:
        exact_shares = {
            repo_name: remaining * capacities[repo_name] / total_capacity
            for repo_name in grouped
        }
        for repo_name, share in exact_shares.items():
            extra_allocations[repo_name] = min(capacities[repo_name], int(share))
        allocated_extra = sum(extra_allocations.values())
        slots_left = remaining - allocated_extra
        remainders = sorted(
            (
                exact_shares[repo_name] - extra_allocations[repo_name],
                repo_name,
            )
            for repo_name in grouped
        )
        for _, repo_name in reversed(remainders):
            if slots_left <= 0:
                break
            if extra_allocations[repo_name] >= capacities[repo_name]:
                continue
            extra_allocations[repo_name] += 1
            slots_left -= 1

    allocations = [
        RepoAllocation(
            repo_name=repo_name,
            available=len(grouped[repo_name]),
            allocated=base_allocations[repo_name] + extra_allocations[repo_name],
        )
        for repo_name in grouped
    ]
    if sum(item.allocated for item in allocations) != sample_size:
        raise AssertionError("Sample allocation did not sum to requested sample size")
    return allocations


def sample_instances(
    rows: list[dict[str, Any]],
    sample_size: int,
    min_per_repo: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[RepoAllocation]]:
    grouped = stable_repo_rows(rows)
    allocations = allocate_sample(rows, sample_size, min_per_repo)
    rng = random.Random(seed)
    sampled: list[dict[str, Any]] = []
    for allocation in allocations:
        repo_rows = list(grouped[allocation.repo_name])
        chosen = rng.sample(repo_rows, allocation.allocated)
        sampled.extend(sorted(chosen, key=lambda item: str(item["instance_id"])))
    sampled.sort(key=lambda item: (str(item["repo_name"]), str(item["instance_id"])))
    return sampled, allocations


def git_commit(project_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def instance_coverage(rows: list[dict[str, Any]], settings, db_path: Path, require_index: bool = True) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    coverage_rows: list[dict[str, Any]] = []
    ready_for_benchmark = 0
    for row in rows:
        instance_id = str(row["instance_id"])
        workspace_exists = (settings.workspaces_dir / instance_id).exists()
        index_exists = (settings.indexes_dir / instance_id / "index.json").exists()
        symbols_present = bool(connection.execute(
            "SELECT 1 FROM symbols WHERE instance_id = ? LIMIT 1",
            (instance_id,),
        ).fetchone())
        files_present = bool(connection.execute(
            "SELECT 1 FROM files WHERE instance_id = ? LIMIT 1",
            (instance_id,),
        ).fetchone())
        blocks_present = bool(connection.execute(
            "SELECT 1 FROM blocks WHERE instance_id = ? LIMIT 1",
            (instance_id,),
        ).fetchone())
        relations_present = bool(connection.execute(
            "SELECT 1 FROM relations WHERE instance_id = ? LIMIT 1",
            (instance_id,),
        ).fetchone())
        benchmark_ready = all(
            (
                workspace_exists,
                (index_exists or not require_index),
                symbols_present,
                files_present,
                blocks_present,
                relations_present,
            )
        )
        ready_for_benchmark += int(benchmark_ready)
        coverage_rows.append(
            {
                "instance_id": instance_id,
                "repo_name": row["repo_name"],
                "workspace_exists": workspace_exists,
                "index_exists": index_exists,
                "symbols_present": symbols_present,
                "files_present": files_present,
                "blocks_present": blocks_present,
                "relations_present": relations_present,
                "benchmark_ready": benchmark_ready,
            }
        )
    connection.close()
    return {
        "total_instances": len(rows),
        "ready_for_benchmark": ready_for_benchmark,
        "missing_for_benchmark": len(rows) - ready_for_benchmark,
        "rows": coverage_rows,
    }


def build_manifest(
    args: argparse.Namespace,
    settings,
    sample_path: Path,
    allocations: list[RepoAllocation],
    graph_db_path: Path,
) -> dict[str, Any]:
    return {
        "study_name": args.study_name,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(settings.project_root),
        "dataset_name": settings.dataset_name,
        "dataset_split": settings.dataset_split,
        "sample_size": args.sample_size,
        "audit_size": args.audit_size,
        "seed": args.seed,
        "min_per_repo": args.min_per_repo,
        "metadata_path": str(sample_path),
        "graph_db_path": str(graph_db_path),
        "workspaces_dir": str(settings.workspaces_dir),
        "indexes_dir": str(settings.indexes_dir),
        "frozen_pipeline": {
            "retrieval_mode": "graph_only",
            "vector_retrieval": False,
            "structural_only_indexing": args.structural_only,
            "summary_llm_provider": settings.description_llm_provider,
            "summary_llm_model": settings.description_llm_model,
            "patch_llm_provider": settings.patch_llm_provider,
            "patch_llm_model": settings.patch_llm_model,
            "selector_logic": "current_localization_selector",
            "structured_summary": True,
        },
        "allocations": [asdict(item) for item in allocations],
    }


def run_command(command: list[str], project_root: Path) -> None:
    subprocess.run(command, cwd=project_root, check=True)


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    reports_dir = settings.reports_dir
    metadata_dir = settings.metadata_dir
    log_dir = settings.data_root / "logs" / args.study_name
    graph_db_path = settings.data_root / (
        f"{args.study_name}_graph.db" if args.structural_only and not args.reuse_graph_db else "enriched_graph.db"
    )
    open_database(graph_db_path).close()

    sample_path = metadata_dir / f"{args.study_name}.jsonl"
    manifest_path = reports_dir / f"{args.study_name}_manifest.json"
    coverage_path = reports_dir / f"{args.study_name}_coverage.json"
    benchmark_json = reports_dir / f"{args.study_name}.json"
    benchmark_md = reports_dir / f"{args.study_name}.md"
    audit_template_path = metadata_dir / f"{args.study_name}_audit_{args.audit_size}.jsonl"
    index_failures_path = reports_dir / f"{args.study_name}_index_failures.jsonl"

    if args.sample_path is not None and args.sample_path.exists():
        sampled_rows = [json.loads(line) for line in args.sample_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        allocations = allocate_sample(sampled_rows, len(sampled_rows), min(args.min_per_repo, len(sampled_rows)))
        sample_path = args.sample_path
    else:
        rows = all_dataset_rows(settings.dataset_name, settings.dataset_split)
        sampled_rows, allocations = sample_instances(
            rows=rows,
            sample_size=args.sample_size,
            min_per_repo=args.min_per_repo,
            seed=args.seed,
        )
        write_jsonl(sample_path, sampled_rows)
    write_json(manifest_path, build_manifest(args, settings, sample_path, allocations, graph_db_path))

    coverage = instance_coverage(sampled_rows, settings, graph_db_path, require_index=not args.structural_only)

    if args.prepare_missing:
        run_command(
            [
                ".venv/bin/python",
                "prepare_dataset.py",
                "--metadata-path",
                str(sample_path),
            ],
            settings.project_root,
        )
        coverage = instance_coverage(sampled_rows, settings, graph_db_path, require_index=not args.structural_only)

    if args.index_missing:
        command = [
            ".venv/bin/python",
            "graph_index_pipeline.py",
            "--metadata-path",
            str(sample_path),
            "--db-path",
            str(graph_db_path),
            "--continue-on-error",
            "--failures-path",
            str(index_failures_path),
        ]
        if args.structural_only:
            command.append("--structural-only")
        if args.reuse_cache:
            command.append("--reuse-cache")
        if args.structural_only:
            command.append("--validate-extraction")
        run_command(command, settings.project_root)
        coverage = instance_coverage(sampled_rows, settings, graph_db_path, require_index=not args.structural_only)

    write_json(coverage_path, coverage)

    if args.run_benchmark:
        run_command(
            [
                ".venv/bin/python",
                "localization_eval.py",
                "--metadata-path",
                str(sample_path),
                "--graph-db-path",
                str(graph_db_path),
                "--limit",
                str(args.sample_size),
                "--study-name",
                args.study_name,
                "--output-json",
                str(benchmark_json),
                "--output-md",
                str(benchmark_md),
                "--log-dir",
                str(log_dir),
                "--resume",
                "--continue-on-error",
                "--max-summary-tokens",
                str(args.max_summary_tokens or settings.max_summary_tokens),
                "--candidate-budget",
                str(args.candidate_budget or settings.candidate_budget),
                *(
                    ["--audit-path", str(audit_template_path)]
                    if audit_template_path.exists()
                    else []
                ),
            ],
            settings.project_root,
        )

    if args.select_audit and benchmark_json.exists():
        run_command(
            [
                ".venv/bin/python",
                "select_localization_audit_sample.py",
                "--results-json",
                str(benchmark_json),
                "--output-path",
                str(audit_template_path),
                "--sample-size",
                str(args.audit_size),
                "--seed",
                str(args.seed),
            ],
            settings.project_root,
        )

    print(
        json.dumps(
            {
                "study_name": args.study_name,
                "sample_path": str(sample_path),
                "manifest_path": str(manifest_path),
                "coverage_path": str(coverage_path),
                "benchmark_json": str(benchmark_json),
                "benchmark_md": str(benchmark_md),
                "audit_template_path": str(audit_template_path),
                "ready_for_benchmark": coverage["ready_for_benchmark"],
                "missing_for_benchmark": coverage["missing_for_benchmark"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
