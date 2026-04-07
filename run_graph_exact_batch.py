from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from experiment.config import ensure_directories, load_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run graph_exact_patch_pipeline.py over a metadata subset.")
    parser.add_argument("--metadata-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--predictions-path", type=Path, required=True)
    parser.add_argument("--graph-db-path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    rows = load_rows(args.metadata_path)
    if args.limit is not None:
        rows = rows[: args.limit]

    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    args.output_root.mkdir(parents=True, exist_ok=True)
    predictions: list[dict[str, str]] = []

    for index, row in enumerate(rows, start=1):
        instance_id = row["instance_id"]
        output_dir = args.output_root / instance_id
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{index}/{len(rows)}] {instance_id}", flush=True)
        subprocess.run(
            [
                str(settings.project_root / ".venv" / "bin" / "python"),
                str(settings.project_root / "graph_exact_patch_pipeline.py"),
                "--instance-id",
                instance_id,
                "--metadata-path",
                str(args.metadata_path),
                "--graph-db-path",
                str(graph_db_path),
                "--output-dir",
                str(output_dir),
            ],
            cwd=settings.project_root,
            check=True,
        )
        result_path = output_dir / "result.json"
        patch_path = output_dir / "exact_line_patch.diff"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result.get("apply_valid"):
            continue
        predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": settings.patch_llm_model,
                "model_patch": patch_path.read_text(encoding="utf-8"),
            }
        )

    args.predictions_path.write_text(
        "".join(json.dumps(item) + "\n" for item in predictions),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "input_instances": len(rows),
                "prediction_count": len(predictions),
                "predictions_path": str(args.predictions_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
