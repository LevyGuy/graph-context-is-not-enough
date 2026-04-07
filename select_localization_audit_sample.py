from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from experiment.utils import write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a stratified manual audit subset from localization study results.")
    parser.add_argument("--results-json", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260331)
    return parser.parse_args()


def audit_bucket(row: dict[str, Any]) -> str:
    if row.get("weak_graph_found_issue"):
        return "predicted_good"
    if not row.get("semantic_correct_fix_mechanism") and not row.get("target_in_gold_file"):
        return "predicted_bad"
    return "partial_or_ambiguous"


def sample_balanced_by_repo(rows: list[dict[str, Any]], sample_size: int, rng: random.Random) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        repo_name = str(row.get("repo_name") or str(row["instance_id"]).split("__", 1)[0])
        grouped[repo_name].append(row)
    for repo_name in grouped:
        rng.shuffle(grouped[repo_name])
    chosen: list[dict[str, Any]] = []
    repo_names = sorted(grouped)
    while len(chosen) < min(sample_size, len(rows)):
        progress = False
        for repo_name in repo_names:
            bucket = grouped[repo_name]
            if not bucket:
                continue
            chosen.append(bucket.pop())
            progress = True
            if len(chosen) >= sample_size:
                break
        if not progress:
            break
    return chosen


def main() -> None:
    args = parse_args()
    rows = json.loads(args.results_json.read_text(encoding="utf-8"))
    rng = random.Random(args.seed)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[audit_bucket(row)].append(row)

    target_per_bucket = args.sample_size // 3
    selected: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for bucket_name in ("predicted_good", "partial_or_ambiguous", "predicted_bad"):
        bucket_rows = grouped.get(bucket_name, [])
        picked = sample_balanced_by_repo(bucket_rows, target_per_bucket, rng)
        selected.extend(picked)
        picked_ids = {row["instance_id"] for row in picked}
        leftovers.extend([row for row in bucket_rows if row["instance_id"] not in picked_ids])

    if len(selected) < args.sample_size:
        selected_ids = {row["instance_id"] for row in selected}
        remainder = [row for row in leftovers if row["instance_id"] not in selected_ids]
        selected.extend(sample_balanced_by_repo(remainder, args.sample_size - len(selected), rng))

    selected = selected[: args.sample_size]
    template_rows = []
    for row in selected:
        template_rows.append(
            {
                "instance_id": row["instance_id"],
                "repo_name": row.get("repo_name") or str(row["instance_id"]).split("__", 1)[0],
                "audit_bucket": audit_bucket(row),
                "gold_files": row["gold_files"],
                "target_path": row["target_path"],
                "target_line": row["target_line"],
                "semantic_rationale": row.get("semantic_rationale", ""),
                "failure_taxonomy": row.get("failure_taxonomy", ""),
                "audit_correct_file": None,
                "audit_correct_region": None,
                "audit_correct_fix_mechanism": None,
                "audit_graph_found_issue": None,
                "audit_notes": "",
            }
        )
    write_jsonl(args.output_path, template_rows)
    print(json.dumps({"output_path": str(args.output_path), "rows": len(template_rows)}, indent=2))


if __name__ == "__main__":
    main()
