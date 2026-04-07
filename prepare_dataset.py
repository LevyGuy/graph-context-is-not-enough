from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import dataset_rows, read_metadata_file, write_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare SWE-bench Lite repositories.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Delete and reclone instance workspaces if they already exist.",
    )
    parser.add_argument(
        "--max-instances",
        type=int,
        default=None,
        help="Override the configured instance count for this run.",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Use an explicit metadata jsonl file instead of generating instances.jsonl from the dataset.",
    )
    return parser.parse_args()


def clone_and_checkout(clone_url: str, base_commit: str, target_dir: Path, refresh: bool) -> None:
    if refresh and target_dir.exists():
        shutil.rmtree(target_dir)
    if not target_dir.exists():
        subprocess.run(["git", "clone", clone_url, str(target_dir)], check=True)
    subprocess.run(["git", "fetch", "--all", "--tags"], cwd=target_dir, check=True)
    subprocess.run(["git", "checkout", "--force", base_commit], cwd=target_dir, check=True)


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    if args.metadata_path is not None:
        rows = read_metadata_file(args.metadata_path)
        if args.max_instances is not None:
            rows = rows[: args.max_instances]
        metadata_path = args.metadata_path
    else:
        rows = dataset_rows(settings)
        if args.max_instances is not None:
            rows = rows[: args.max_instances]
        metadata_path = write_metadata(rows, settings.metadata_dir)

    for row in rows:
        workspace_dir = settings.workspaces_dir / row["instance_id"]
        clone_and_checkout(
            clone_url=row["clone_url"],
            base_commit=row["base_commit"],
            target_dir=workspace_dir,
            refresh=args.refresh,
        )

    summary = {
        "dataset_name": settings.dataset_name,
        "dataset_split": settings.dataset_split,
        "max_instances": settings.max_instances,
        "metadata_path": str(metadata_path),
        "workspaces_dir": str(settings.workspaces_dir),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
