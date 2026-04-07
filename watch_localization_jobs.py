from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB_PATH = Path(
    "/Users/guylevy/Projects/natural-language-index_2/artifacts/localization_study_95_structural_v2_graph.db"
)
DEFAULT_EVAL_LOG_DIR = Path(
    "/Users/guylevy/Projects/natural-language-index_2/artifacts/logs/localization_study_95_structural_v2_ready_37"
)
DEFAULT_LOG_PATH = Path(
    "/Users/guylevy/Projects/natural-language-index_2/artifacts/logs/localization_watchdog.log"
)
DEFAULT_INDEX_PATTERN = (
    "graph_index_pipeline.py --metadata-path "
    "/Users/guylevy/Projects/natural-language-index_2/artifacts/metadata/localization_study_95_structural_reused_ready.jsonl "
    "--db-path /Users/guylevy/Projects/natural-language-index_2/artifacts/localization_study_95_structural_v2_graph.db"
)
DEFAULT_EVAL_PATTERN = (
    "localization_eval.py --metadata-path "
    "/Users/guylevy/Projects/natural-language-index_2/artifacts/metadata/localization_study_95_structural_v2_ready_current.jsonl"
)


@dataclass
class Snapshot:
    timestamp: float
    index_pid: int | None
    eval_pid: int | None
    ready_instances: int
    eval_started: int
    eval_done: int


def report_exists_for_log_dir(log_dir: Path) -> bool:
    parent = log_dir.parent
    stem = log_dir.name
    return (parent.parent / "reports" / f"{stem}.md").exists() or (parent.parent / "reports" / f"{stem}.json").exists()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch localization indexing/eval jobs.")
    parser.add_argument("--interval-seconds", type=int, default=600)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--eval-log-dir", type=Path, default=DEFAULT_EVAL_LOG_DIR)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--index-pattern", default=DEFAULT_INDEX_PATTERN)
    parser.add_argument("--eval-pattern", default=DEFAULT_EVAL_PATTERN)
    parser.add_argument("--stalled-intervals", type=int, default=2)
    return parser.parse_args()


def find_pid(pattern: str) -> int | None:
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return int(result.stdout.strip().splitlines()[0])
    except ValueError:
        return None


def count_ready_instances(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        return int(
            connection.execute(
                """
                SELECT count(*) FROM (
                  SELECT instance_id,
                         max(case when src='files' then 1 else 0 end) has_files,
                         max(case when src='symbols' then 1 else 0 end) has_symbols,
                         max(case when src='blocks' then 1 else 0 end) has_blocks,
                         max(case when src='relations' then 1 else 0 end) has_relations
                  FROM (
                    SELECT DISTINCT instance_id, 'files' src FROM files
                    UNION ALL
                    SELECT DISTINCT instance_id, 'symbols' src FROM symbols
                    UNION ALL
                    SELECT DISTINCT instance_id, 'blocks' src FROM blocks
                    UNION ALL
                    SELECT DISTINCT instance_id, 'relations' src FROM relations
                  ) t
                  GROUP BY instance_id
                  HAVING has_files=1 AND has_symbols=1 AND has_blocks=1 AND has_relations=1
                )
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()


def count_eval_progress(log_dir: Path) -> tuple[int, int]:
    if not log_dir.exists():
        return (0, 0)
    started = 0
    done = 0
    for child in log_dir.iterdir():
        if not child.is_dir():
            continue
        started += 1
        names = {path.name for path in child.iterdir()}
        if "localization_result.json" in names or "instance_error.json" in names:
            done += 1
    return (started, done)


def append_log(log_path: Path, payload: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def notify(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def snapshot(args: argparse.Namespace) -> Snapshot:
    eval_started, eval_done = count_eval_progress(args.eval_log_dir)
    return Snapshot(
        timestamp=time.time(),
        index_pid=find_pid(args.index_pattern),
        eval_pid=find_pid(args.eval_pattern),
        ready_instances=count_ready_instances(args.db_path),
        eval_started=eval_started,
        eval_done=eval_done,
    )


def main() -> int:
    args = parse_args()
    stop = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True
        append_log(
            args.log_path,
            {"event": "signal", "signum": signum, "timestamp": time.time()},
        )

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    stagnant_ready = 0
    stagnant_eval = 0
    previous = snapshot(args)
    append_log(
        args.log_path,
        {
            "event": "start",
            "timestamp": previous.timestamp,
            "index_pid": previous.index_pid,
            "eval_pid": previous.eval_pid,
            "ready_instances": previous.ready_instances,
            "eval_started": previous.eval_started,
            "eval_done": previous.eval_done,
            "interval_seconds": args.interval_seconds,
        },
    )

    while not stop:
        time.sleep(args.interval_seconds)
        current = snapshot(args)
        payload = {
            "event": "tick",
            "timestamp": current.timestamp,
            "index_pid": current.index_pid,
            "eval_pid": current.eval_pid,
            "ready_instances": current.ready_instances,
            "eval_started": current.eval_started,
            "eval_done": current.eval_done,
        }

        alerts: list[str] = []
        if current.index_pid is None:
            alerts.append("indexer missing")
        eval_completed = (
            previous.eval_pid is not None
            and current.eval_pid is None
            and current.eval_started > 0
            and current.eval_done == current.eval_started
            and report_exists_for_log_dir(args.eval_log_dir)
        )
        if current.eval_pid is None and not eval_completed:
            alerts.append("evaluator missing")

        if current.ready_instances <= previous.ready_instances:
            stagnant_ready += 1
        else:
            stagnant_ready = 0
        if current.eval_done <= previous.eval_done:
            stagnant_eval += 1
        else:
            stagnant_eval = 0

        if stagnant_ready >= args.stalled_intervals and current.index_pid is not None:
            alerts.append(
                f"ready count stalled at {current.ready_instances} for {stagnant_ready} checks"
            )
        if stagnant_eval >= args.stalled_intervals and current.eval_pid is not None:
            alerts.append(
                f"eval completion stalled at {current.eval_done} for {stagnant_eval} checks"
            )

        if eval_completed:
            payload["completion"] = "evaluator completed normally"

        if alerts:
            payload["alerts"] = alerts
            notify("Localization Watchdog", "; ".join(alerts))

        append_log(args.log_path, payload)
        previous = current

    append_log(args.log_path, {"event": "stop", "timestamp": time.time()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
