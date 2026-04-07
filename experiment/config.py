from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional for local runs without python-dotenv
    load_dotenv = None


def load_local_env(project_root: Path | None = None) -> None:
    root = project_root or Path.cwd()
    env_path = root / ".env"
    if env_path.exists() and load_dotenv is not None:
        load_dotenv(env_path, override=True)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    workspaces_dir: Path
    indexes_dir: Path
    vector_db_dir: Path
    metadata_dir: Path
    reports_dir: Path
    tools_dir: Path
    dataset_name: str
    dataset_split: str
    max_instances: int
    llm_provider: str
    llm_model: str
    description_llm_provider: str
    description_llm_model: str
    description_reasoning_effort: str
    patch_llm_provider: str
    patch_llm_model: str
    patch_reasoning_effort: str
    embedding_provider: str
    embedding_model: str
    max_budget_usd: float
    enrich_workers: int
    db_commit_interval: int
    reuse_scip_index: bool
    chunk_size: int
    chunk_overlap: int
    graph_top_k: int
    vector_top_k: int
    max_summary_tokens: int
    candidate_budget: int


def load_settings() -> Settings:
    project_root = Path(os.getenv("NLI_PROJECT_ROOT", Path.cwd())).resolve()
    load_local_env(project_root)
    data_root = Path(os.getenv("NLI_DATA_ROOT", project_root / "artifacts")).resolve()
    return Settings(
        project_root=project_root,
        data_root=data_root,
        workspaces_dir=(data_root / "workspaces"),
        indexes_dir=(data_root / "indexes"),
        vector_db_dir=(data_root / "vector_db"),
        metadata_dir=(data_root / "metadata"),
        reports_dir=(data_root / "reports"),
        tools_dir=(data_root / "tools"),
        dataset_name=os.getenv("NLI_DATASET_NAME", "princeton-nlp/SWE-bench_Lite"),
        dataset_split=os.getenv("NLI_DATASET_SPLIT", "test"),
        max_instances=_env_int("NLI_MAX_INSTANCES", 10),
        llm_provider=os.getenv("NLI_LLM_PROVIDER", "openai"),
        llm_model=os.getenv("NLI_LLM_MODEL", "gpt-4.1-mini"),
        description_llm_provider=os.getenv(
            "NLI_DESCRIPTION_LLM_PROVIDER", os.getenv("NLI_LLM_PROVIDER", "openai")
        ),
        description_llm_model=os.getenv(
            "NLI_DESCRIPTION_LLM_MODEL", os.getenv("NLI_LLM_MODEL", "gpt-4o-mini")
        ),
        description_reasoning_effort=os.getenv("NLI_DESCRIPTION_REASONING_EFFORT", "medium"),
        patch_llm_provider=os.getenv(
            "NLI_PATCH_LLM_PROVIDER", os.getenv("NLI_LLM_PROVIDER", "openai")
        ),
        patch_llm_model=os.getenv(
            "NLI_PATCH_LLM_MODEL", os.getenv("NLI_LLM_MODEL", "gpt-4.1-mini")
        ),
        patch_reasoning_effort=os.getenv("NLI_PATCH_REASONING_EFFORT", "high"),
        embedding_provider=os.getenv("NLI_EMBEDDING_PROVIDER", "openai"),
        embedding_model=os.getenv("NLI_EMBEDDING_MODEL", "text-embedding-3-small"),
        max_budget_usd=float(os.getenv("NLI_MAX_BUDGET_USD", "70")),
        enrich_workers=_env_int("NLI_ENRICH_WORKERS", 8),
        db_commit_interval=_env_int("NLI_DB_COMMIT_INTERVAL", 25),
        reuse_scip_index=os.getenv("NLI_REUSE_SCIP_INDEX", "1") != "0",
        chunk_size=_env_int("NLI_CHUNK_SIZE", 1200),
        chunk_overlap=_env_int("NLI_CHUNK_OVERLAP", 200),
        graph_top_k=_env_int("NLI_GRAPH_TOP_K", 8),
        vector_top_k=_env_int("NLI_VECTOR_TOP_K", 8),
        max_summary_tokens=_env_int("NLI_MAX_SUMMARY_TOKENS", 12000),
        candidate_budget=_env_int("NLI_CANDIDATE_BUDGET", 10),
    )


def ensure_directories(settings: Settings) -> None:
    for path in (
        settings.data_root,
        settings.workspaces_dir,
        settings.indexes_dir,
        settings.vector_db_dir,
        settings.metadata_dir,
        settings.reports_dir,
        settings.tools_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
