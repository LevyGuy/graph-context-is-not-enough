from __future__ import annotations

import json
from pathlib import Path


MODEL_PRICING_PER_1M_TOKENS = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def estimate_text_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_PER_1M_TOKENS.get(model)
    if not pricing:
        return 0.0
    return (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
    )


def budget_state_path(metadata_dir: Path) -> Path:
    return metadata_dir / "budget_state.json"


def load_budget_state(metadata_dir: Path) -> dict:
    path = budget_state_path(metadata_dir)
    if not path.exists():
        return {"spent_usd": 0.0, "events": []}
    return json.loads(path.read_text(encoding="utf-8"))


def record_budget_event(
    metadata_dir: Path,
    *,
    phase: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    metadata: dict | None = None,
) -> dict:
    state = load_budget_state(metadata_dir)
    state["spent_usd"] = float(state.get("spent_usd", 0.0)) + float(cost_usd)
    state.setdefault("events", []).append(
        {
            "phase": phase,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "metadata": metadata or {},
        }
    )
    budget_state_path(metadata_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state
