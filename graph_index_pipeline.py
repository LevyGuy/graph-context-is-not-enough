from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from json_repair import repair_json
from tqdm import tqdm

from experiment.budget import estimate_text_cost_usd, load_budget_state, record_budget_event
from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import read_metadata
from experiment.llm_clients import build_llm_client, estimate_tokens, with_retries
from experiment.utils import run_command, run_command_capture


DESCRIPTION_SYSTEM_PROMPT = """You analyze Python code for debugging context construction.
Write a concise 1-2 sentence description of the function or class role in the broader business logic and user journey.
Return only valid JSON with exactly one key: description."""


@dataclass
class DefinitionRecord:
    instance_id: str
    repo_name: str
    symbol: str
    symbol_name: str
    symbol_kind: str
    relative_path: str
    start_line: int
    end_line: int
    code: str
    description: str


@dataclass
class FileRecord:
    instance_id: str
    repo_name: str
    relative_path: str
    source: str
    imports_json: str
    top_level_constants_json: str
    symbol_names_json: str


@dataclass
class BlockRecord:
    instance_id: str
    repo_name: str
    relative_path: str
    block_id: str
    block_type: str
    start_line: int
    end_line: int
    code: str
    summary: str
    parent_symbol_name: str
    referenced_symbols_json: str
    referenced_constants_json: str


@dataclass
class RelationRecord:
    instance_id: str
    src_kind: str
    src_ref: str
    dst_kind: str
    dst_ref: str
    relation_type: str
    weight: float


@dataclass
class ExtractionFailureRecord:
    instance_id: str
    repo_name: str
    relative_path: str
    content_hash: str
    missing_symbols_json: str
    expected_symbols_json: str
    indexed_symbols_json: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the enriched SCIP graph index.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override the SQLite output path. Defaults to artifacts/enriched_graph.db.",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Optional metadata jsonl path to limit which instances are processed.",
    )
    parser.add_argument(
        "--structural-only",
        action="store_true",
        help="Skip SCIP symbol extraction and LLM enrichment; only populate files, blocks, and relations.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Log per-instance indexing failures and continue with remaining instances.",
    )
    parser.add_argument(
        "--failures-path",
        type=Path,
        default=None,
        help="Optional jsonl path to append per-instance indexing failures.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory placeholder for future on-disk cache coordination.",
    )
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        help="Reuse cached structural extraction payloads keyed by file hash.",
    )
    parser.add_argument(
        "--validate-extraction",
        action="store_true",
        help="Validate that top-level AST-discovered symbols were inserted and log mismatches.",
    )
    return parser.parse_args()


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols (
            instance_id TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            symbol_name TEXT NOT NULL,
            symbol_kind TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            code TEXT NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (instance_id, symbol, relative_path, start_line)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            instance_id TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            source TEXT NOT NULL,
            imports_json TEXT NOT NULL,
            top_level_constants_json TEXT NOT NULL,
            symbol_names_json TEXT NOT NULL,
            PRIMARY KEY (instance_id, relative_path)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            instance_id TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            block_id TEXT NOT NULL,
            block_type TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            code TEXT NOT NULL,
            summary TEXT NOT NULL,
            parent_symbol_name TEXT NOT NULL,
            referenced_symbols_json TEXT NOT NULL,
            referenced_constants_json TEXT NOT NULL,
            PRIMARY KEY (instance_id, relative_path, block_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS relations (
            instance_id TEXT NOT NULL,
            src_kind TEXT NOT NULL,
            src_ref TEXT NOT NULL,
            dst_kind TEXT NOT NULL,
            dst_ref TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            weight REAL NOT NULL,
            PRIMARY KEY (instance_id, src_kind, src_ref, dst_kind, dst_ref, relation_type)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS file_contents (
            content_hash TEXT PRIMARY KEY,
            source TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS index_cache (
            repo_name TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (repo_name, relative_path, content_hash)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS extraction_failures (
            instance_id TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            missing_symbols_json TEXT NOT NULL,
            expected_symbols_json TEXT NOT NULL,
            indexed_symbols_json TEXT NOT NULL,
            PRIMARY KEY (instance_id, relative_path, content_hash)
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            instance_id UNINDEXED,
            repo_name UNINDEXED,
            relative_path,
            symbol_name,
            code,
            description,
            content='symbols',
            content_rowid='rowid'
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
            instance_id UNINDEXED,
            repo_name UNINDEXED,
            relative_path,
            source,
            imports_json,
            top_level_constants_json,
            symbol_names_json,
            content='files',
            content_rowid='rowid'
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
            instance_id UNINDEXED,
            repo_name UNINDEXED,
            relative_path,
            block_type,
            code,
            summary,
            parent_symbol_name,
            referenced_symbols_json,
            referenced_constants_json,
            content='blocks',
            content_rowid='rowid'
        )
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
            INSERT INTO symbols_fts(rowid, instance_id, repo_name, relative_path, symbol_name, code, description)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.symbol_name, new.code, new.description);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
            INSERT INTO symbols_fts(symbols_fts, rowid, instance_id, repo_name, relative_path, symbol_name, code, description)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.symbol_name, old.code, old.description);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
            INSERT INTO symbols_fts(symbols_fts, rowid, instance_id, repo_name, relative_path, symbol_name, code, description)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.symbol_name, old.code, old.description);
            INSERT INTO symbols_fts(rowid, instance_id, repo_name, relative_path, symbol_name, code, description)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.symbol_name, new.code, new.description);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
            INSERT INTO files_fts(rowid, instance_id, repo_name, relative_path, source, imports_json, top_level_constants_json, symbol_names_json)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.source, new.imports_json, new.top_level_constants_json, new.symbol_names_json);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, instance_id, repo_name, relative_path, source, imports_json, top_level_constants_json, symbol_names_json)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.source, old.imports_json, old.top_level_constants_json, old.symbol_names_json);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, instance_id, repo_name, relative_path, source, imports_json, top_level_constants_json, symbol_names_json)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.source, old.imports_json, old.top_level_constants_json, old.symbol_names_json);
            INSERT INTO files_fts(rowid, instance_id, repo_name, relative_path, source, imports_json, top_level_constants_json, symbol_names_json)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.source, new.imports_json, new.top_level_constants_json, new.symbol_names_json);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
            INSERT INTO blocks_fts(rowid, instance_id, repo_name, relative_path, block_type, code, summary, parent_symbol_name, referenced_symbols_json, referenced_constants_json)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.block_type, new.code, new.summary, new.parent_symbol_name, new.referenced_symbols_json, new.referenced_constants_json);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
            INSERT INTO blocks_fts(blocks_fts, rowid, instance_id, repo_name, relative_path, block_type, code, summary, parent_symbol_name, referenced_symbols_json, referenced_constants_json)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.block_type, old.code, old.summary, old.parent_symbol_name, old.referenced_symbols_json, old.referenced_constants_json);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS blocks_au AFTER UPDATE ON blocks BEGIN
            INSERT INTO blocks_fts(blocks_fts, rowid, instance_id, repo_name, relative_path, block_type, code, summary, parent_symbol_name, referenced_symbols_json, referenced_constants_json)
            VALUES('delete', old.rowid, old.instance_id, old.repo_name, old.relative_path, old.block_type, old.code, old.summary, old.parent_symbol_name, old.referenced_symbols_json, old.referenced_constants_json);
            INSERT INTO blocks_fts(rowid, instance_id, repo_name, relative_path, block_type, code, summary, parent_symbol_name, referenced_symbols_json, referenced_constants_json)
            VALUES (new.rowid, new.instance_id, new.repo_name, new.relative_path, new.block_type, new.code, new.summary, new.parent_symbol_name, new.referenced_symbols_json, new.referenced_constants_json);
        END
        """
    )
    return connection


def scip_json_path(index_dir: Path) -> Path:
    return index_dir / "index.json"


def _is_valid_scip_json(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and ("documents" in payload or "metadata" in payload)


def _resolve_scip_bin() -> str:
    configured = os.getenv("NLI_SCIP_BIN")
    if configured:
        return configured

    repo_local = Path(__file__).resolve().parent / "tools" / "scip-code" / "scip"
    if repo_local.exists():
        return str(repo_local)

    candidate = shutil.which("scip")
    if candidate and "homebrew" not in candidate.lower():
        return candidate

    return "scip"


def build_scip_index(workspace_dir: Path, index_dir: Path, reuse_existing: bool) -> Path:
    scip_python_bin = os.getenv("NLI_SCIP_PYTHON_BIN", "scip-python")
    scip_bin = _resolve_scip_bin()
    path = scip_json_path(index_dir)
    if reuse_existing and path.exists() and _is_valid_scip_json(path):
        return path
    index_dir.mkdir(parents=True, exist_ok=True)
    run_command([scip_python_bin, "index", ".", "--project-name", "test"], cwd=workspace_dir)
    output = run_command_capture([scip_bin, "print", "--json", "index.scip"], cwd=workspace_dir)
    path.write_text(output, encoding="utf-8")
    if not _is_valid_scip_json(path):
        raise RuntimeError(f"Generated invalid SCIP JSON at {path} using binary {scip_bin}")
    return path


def _get_occurrence_range(occurrence: dict[str, Any]) -> list[int]:
    return occurrence.get("range") or occurrence.get("enclosing_range") or occurrence.get("enclosingRange") or []


def _get_symbol_roles(occurrence: dict[str, Any]) -> int:
    return int(occurrence.get("symbol_roles") or occurrence.get("symbolRoles") or 0)


def _is_definition(occurrence: dict[str, Any]) -> bool:
    return bool(_get_symbol_roles(occurrence) & 1)


def _symbol_name(symbol: str) -> str:
    trimmed = symbol.rstrip(".")
    if "/" in trimmed:
        trimmed = trimmed.split("/")[-1]
    if "." in trimmed:
        trimmed = trimmed.split(".")[-1]
    if "(" in trimmed:
        trimmed = trimmed.split("(")[0]
    return trimmed or symbol


def _definition_nodes(source_text: str) -> list[ast.AST]:
    tree = ast.parse(source_text)
    nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes.append(node)
    return nodes


def _parse_tree(source_text: str) -> ast.AST:
    return ast.parse(source_text)


def _content_hash(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _node_span(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno")
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min([start] + [decorator.lineno for decorator in decorators])
    end = getattr(node, "end_lineno", start)
    return start, end


def _code_for_node(lines: list[str], node: ast.AST) -> tuple[int, int, str]:
    start, end = _node_span(node)
    snippet = "".join(lines[start - 1 : end])
    return start, end, snippet


def _kind_for_node(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function"
    return "function"


def _symbol_kind_for_node(node: ast.AST, parent_class: str | None) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    if parent_class:
        return "async_method" if isinstance(node, ast.AsyncFunctionDef) else "method"
    return _kind_for_node(node)


def _iter_definition_nodes_full(tree: ast.AST) -> list[tuple[ast.AST, str | None]]:
    items: list[tuple[ast.AST, str | None]] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            items.append((node, None))
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    items.append((child, node.name))
    return items


def _definition_symbol(relative_path: str, symbol_name: str, start_line: int, parent_class: str | None) -> str:
    qualname = f"{parent_class}.{symbol_name}" if parent_class else symbol_name
    return f"{relative_path}::{qualname}:{start_line}"


def _extract_definitions_from_ast(
    instance_id: str,
    repo_name: str,
    relative_path: str,
    source_text: str,
    tree: ast.AST,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    lines = source_text.splitlines(keepends=True)
    definitions: list[dict[str, Any]] = []
    expected_top_level: list[str] = []
    indexed_top_level: list[str] = []
    for node, parent_class in _iter_definition_nodes_full(tree):
        symbol_name = getattr(node, "name", "")
        if not symbol_name:
            continue
        start_line, end_line, code = _code_for_node(lines, node)
        kind = _symbol_kind_for_node(node, parent_class)
        definitions.append(
            {
                "instance_id": instance_id,
                "repo_name": repo_name,
                "symbol": _definition_symbol(relative_path, symbol_name, start_line, parent_class),
                "symbol_name": symbol_name,
                "symbol_kind": kind,
                "relative_path": relative_path,
                "start_line": start_line,
                "end_line": end_line,
                "code": code,
            }
        )
        if parent_class is None:
            expected_top_level.append(symbol_name)
            indexed_top_level.append(symbol_name)
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for item in definitions:
        unique[(item["symbol"], item["start_line"])] = item
    return list(unique.values()), expected_top_level, indexed_top_level


def _is_test_path(relative_path: str) -> bool:
    normalized = relative_path.lower()
    if normalized.endswith("conftest.py"):
        return True
    if "/tests/" in f"/{normalized}" or normalized.startswith("tests/"):
        return True
    if "/test_" in f"/{normalized}" or normalized.endswith("_test.py"):
        return True
    return False


def _should_skip_path(relative_path: str) -> bool:
    return _is_test_path(relative_path)


def _import_module_candidates(current_relative_path: str, node: ast.AST) -> list[str]:
    current_parts = Path(current_relative_path).with_suffix("").parts
    base_parts = list(current_parts[:-1])
    candidates: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            candidates.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        level = int(node.level or 0)
        relative_parts = list(base_parts)
        if level > 0:
            relative_parts = relative_parts[: max(0, len(relative_parts) - (level - 1))]
        if module:
            parts = module.split(".")
            candidates.append(".".join(relative_parts + parts))
        else:
            candidates.append(".".join(relative_parts))
    return [candidate for candidate in candidates if candidate]


def _resolve_module_to_path(workspace_dir: Path, module_name: str) -> str | None:
    normalized = module_name.replace(".", "/").strip("/")
    if not normalized:
        return None
    for candidate in (workspace_dir / f"{normalized}.py", workspace_dir / normalized / "__init__.py"):
        if candidate.exists():
            return str(candidate.relative_to(workspace_dir))
    suffix = f"{Path(normalized).name}.py"
    matches = sorted(
        str(path.relative_to(workspace_dir))
        for path in workspace_dir.rglob(suffix)
        if path.is_file()
    )
    return matches[0] if len(matches) == 1 else None


def _node_identifiers(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _top_level_constants(tree: ast.AST) -> list[str]:
    constants: list[str] = []
    for node in getattr(tree, "body", []):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                if target.id not in constants:
                    constants.append(target.id)
    return constants


def _top_level_symbol_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def _block_summary(block_type: str, parent_symbol_name: str, referenced_constants: list[str], code: str) -> str:
    base = {
        "assign": "assignment or mutation logic",
        "if": "conditional guard or branching logic",
        "for": "iterative loop logic",
        "while": "loop control logic",
        "return": "return value shaping logic",
        "try": "error handling or fallback logic",
        "constant_definition": "top-level constant or configuration definition",
    }.get(block_type, "localized implementation logic")
    symbol_part = f" inside {parent_symbol_name}" if parent_symbol_name else ""
    constants_part = f" touching constants {', '.join(referenced_constants[:3])}" if referenced_constants else ""
    snippet_part = " with parser/regex behavior" if "re." in code or "regex" in code.lower() else ""
    return f"{base}{symbol_part}{constants_part}{snippet_part}."


def _extract_blocks_from_tree(
    instance_id: str,
    repo_name: str,
    relative_path: str,
    source_text: str,
    tree: ast.AST,
) -> list[BlockRecord]:
    lines = source_text.splitlines(keepends=True)
    blocks: list[BlockRecord] = []

    class BlockVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.parents: list[str] = []

        def _record(self, node: ast.AST, block_type: str) -> None:
            if not hasattr(node, "lineno"):
                return
            start_line, end_line = _node_span(node)
            code = "".join(lines[start_line - 1 : end_line]).strip("\n")
            if not code.strip():
                return
            identifiers = _node_identifiers(node)
            referenced_constants = sorted(name for name in identifiers if name.isupper())
            referenced_symbols = sorted(name for name in identifiers if not name.isupper() and len(name) >= 3)
            parent_symbol_name = self.parents[-1] if self.parents else ""
            block_id = f"{block_type}:{start_line}:{end_line}"
            blocks.append(
                BlockRecord(
                    instance_id=instance_id,
                    repo_name=repo_name,
                    relative_path=relative_path,
                    block_id=block_id,
                    block_type=block_type,
                    start_line=start_line,
                    end_line=end_line,
                    code=code,
                    summary=_block_summary(block_type, parent_symbol_name, referenced_constants, code),
                    parent_symbol_name=parent_symbol_name,
                    referenced_symbols_json=json.dumps(referenced_symbols),
                    referenced_constants_json=json.dumps(referenced_constants),
                )
            )

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_Assign(self, node: ast.Assign) -> Any:
            self._record(node, "assign")
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
            self._record(node, "assign")
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> Any:
            self._record(node, "assign")
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> Any:
            self._record(node, "if")
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> Any:
            self._record(node, "for")
            self.generic_visit(node)

        def visit_While(self, node: ast.While) -> Any:
            self._record(node, "while")
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> Any:
            self._record(node, "return")
            self.generic_visit(node)

        def visit_Try(self, node: ast.Try) -> Any:
            self._record(node, "try")
            self.generic_visit(node)

    for node in getattr(tree, "body", []):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                start_line, end_line = _node_span(node)
                code = "".join(lines[start_line - 1 : end_line]).strip("\n")
                blocks.append(
                    BlockRecord(
                        instance_id=instance_id,
                        repo_name=repo_name,
                        relative_path=relative_path,
                        block_id=f"constant_definition:{start_line}:{end_line}",
                        block_type="constant_definition",
                        start_line=start_line,
                        end_line=end_line,
                        code=code,
                        summary=_block_summary("constant_definition", "", [target.id], code),
                        parent_symbol_name="",
                        referenced_symbols_json=json.dumps([]),
                        referenced_constants_json=json.dumps([target.id]),
                    )
                )

    BlockVisitor().visit(tree)

    unique: dict[str, BlockRecord] = {}
    for block in blocks:
        unique[block.block_id] = block
    return list(unique.values())


def extract_file_records(
    instance_id: str,
    repo_name: str,
    workspace_dir: Path,
) -> tuple[list[FileRecord], list[BlockRecord], list[RelationRecord]]:
    file_records: list[FileRecord] = []
    block_records: list[BlockRecord] = []
    relation_records: list[RelationRecord] = []
    symbol_definitions: dict[str, list[str]] = {}

    try:
        result = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=workspace_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked_paths = [workspace_dir / line for line in result.stdout.splitlines() if line.strip()]
        python_paths = sorted(path for path in tracked_paths if path.is_file())
    except subprocess.CalledProcessError:
        python_paths = sorted(path for path in workspace_dir.rglob("*.py") if path.is_file())
    parsed_trees: dict[str, ast.AST] = {}

    for path in python_paths:
        relative_path = str(path.relative_to(workspace_dir))
        source_text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = _parse_tree(source_text)
        except SyntaxError:
            continue
        parsed_trees[relative_path] = tree
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module_name in _import_module_candidates(relative_path, node):
                    resolved = _resolve_module_to_path(workspace_dir, module_name)
                    if resolved and resolved not in imports:
                        imports.append(resolved)
        constants = _top_level_constants(tree)
        symbol_names = _top_level_symbol_names(tree)
        file_records.append(
            FileRecord(
                instance_id=instance_id,
                repo_name=repo_name,
                relative_path=relative_path,
                source=source_text,
                imports_json=json.dumps(imports),
                top_level_constants_json=json.dumps(constants),
                symbol_names_json=json.dumps(symbol_names),
            )
        )
        block_records.extend(
            _extract_blocks_from_tree(
                instance_id=instance_id,
                repo_name=repo_name,
                relative_path=relative_path,
                source_text=source_text,
                tree=tree,
            )
        )
        for symbol_name in symbol_names:
            symbol_definitions.setdefault(symbol_name, []).append(relative_path)
        for imported in imports:
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="file",
                    src_ref=relative_path,
                    dst_kind="file",
                    dst_ref=imported,
                    relation_type="imports",
                    weight=1.0,
                )
            )
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="file",
                    src_ref=imported,
                    dst_kind="file",
                    dst_ref=relative_path,
                    relation_type="imported_by",
                    weight=0.8,
                )
            )
        for constant_name in constants:
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="constant",
                    src_ref=constant_name,
                    dst_kind="file",
                    dst_ref=relative_path,
                    relation_type="defined_in",
                    weight=1.2,
                )
            )

    for file_record in file_records:
        for symbol_name in json.loads(file_record.symbol_names_json):
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="symbol",
                    src_ref=symbol_name,
                    dst_kind="file",
                    dst_ref=file_record.relative_path,
                    relation_type="defined_in",
                    weight=1.0,
                )
            )

    for relative_path, tree in parsed_trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        relation_records.append(
                            RelationRecord(
                                instance_id=instance_id,
                                src_kind="class",
                                src_ref=node.name,
                                dst_kind="symbol",
                                dst_ref=child.name,
                                relation_type="owns_method",
                                weight=1.0,
                            )
                        )

    block_index = {(block.relative_path, block.block_id): block for block in block_records}
    for block in block_records:
        relation_records.append(
            RelationRecord(
                instance_id=instance_id,
                src_kind="file",
                src_ref=block.relative_path,
                dst_kind="block",
                dst_ref=f"{block.relative_path}:{block.block_id}",
                relation_type="contains_block",
                weight=0.9,
            )
        )
        if block.parent_symbol_name:
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="symbol",
                    src_ref=block.parent_symbol_name,
                    dst_kind="block",
                    dst_ref=f"{block.relative_path}:{block.block_id}",
                    relation_type="contains_block",
                    weight=0.9,
                )
            )
        for constant_name in json.loads(block.referenced_constants_json):
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="block",
                    src_ref=f"{block.relative_path}:{block.block_id}",
                    dst_kind="constant",
                    dst_ref=constant_name,
                    relation_type="references_constant",
                    weight=0.8,
                )
            )
        for symbol_name in json.loads(block.referenced_symbols_json):
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind="block",
                    src_ref=f"{block.relative_path}:{block.block_id}",
                    dst_kind="symbol",
                    dst_ref=symbol_name,
                    relation_type="references_symbol",
                    weight=0.6,
                )
            )

    unique_relations: dict[tuple[str, str, str, str, str], RelationRecord] = {}
    for relation in relation_records:
        key = (
            relation.src_kind,
            relation.src_ref,
            relation.dst_kind,
            relation.dst_ref,
            relation.relation_type,
        )
        unique_relations[key] = relation
    return file_records, block_records, list(unique_relations.values())


def _serialize_structural_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _load_cached_payload(
    connection: sqlite3.Connection,
    repo_name: str,
    base_commit: str,
    relative_path: str,
    content_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT payload_json
        FROM index_cache
        WHERE repo_name = ? AND relative_path = ? AND content_hash = ?
        LIMIT 1
        """,
        (repo_name, relative_path, content_hash),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _store_cached_payload(
    connection: sqlite3.Connection,
    repo_name: str,
    base_commit: str,
    relative_path: str,
    content_hash: str,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO index_cache (repo_name, base_commit, relative_path, content_hash, payload_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(repo_name, relative_path, content_hash)
        DO UPDATE SET
            base_commit=excluded.base_commit,
            payload_json=excluded.payload_json
        """,
        (repo_name, base_commit, relative_path, content_hash, _serialize_structural_payload(payload)),
    )


def _store_file_content(connection: sqlite3.Connection, content_hash: str, source_text: str) -> None:
    connection.execute(
        """
        INSERT INTO file_contents (content_hash, source)
        VALUES (?, ?)
        ON CONFLICT(content_hash) DO NOTHING
        """,
        (content_hash, source_text),
    )


def _build_structural_file_payload(relative_path: str, source_text: str, tree: ast.AST) -> dict[str, Any]:
    imports: list[str] = []
    constants = _top_level_constants(tree)
    symbol_names = _top_level_symbol_names(tree)
    return {
        "relative_path": relative_path,
        "imports": imports,
        "top_level_constants": constants,
        "symbol_names": symbol_names,
        "blocks": [],
        "definitions": [],
        "relations": [],
        "expected_top_level_symbols": symbol_names,
        "indexed_top_level_symbols": [],
    }


def extract_structural_records(
    connection: sqlite3.Connection,
    instance_id: str,
    repo_name: str,
    base_commit: str,
    workspace_dir: Path,
    reuse_cache: bool,
    validate_extraction: bool,
) -> tuple[list[FileRecord], list[DefinitionRecord], list[BlockRecord], list[RelationRecord], list[ExtractionFailureRecord]]:
    file_records: list[FileRecord] = []
    definition_records: list[DefinitionRecord] = []
    block_records: list[BlockRecord] = []
    relation_records: list[RelationRecord] = []
    extraction_failures: list[ExtractionFailureRecord] = []

    try:
        result = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=workspace_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked_paths = [workspace_dir / line for line in result.stdout.splitlines() if line.strip()]
        python_paths = sorted(path for path in tracked_paths if path.is_file())
    except subprocess.CalledProcessError:
        python_paths = sorted(path for path in workspace_dir.rglob("*.py") if path.is_file())

    for path in python_paths:
        relative_path = str(path.relative_to(workspace_dir))
        if _should_skip_path(relative_path):
            continue
        source_text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = _parse_tree(source_text)
        except SyntaxError:
            continue
        content_hash = _content_hash(source_text)
        _store_file_content(connection, content_hash, source_text)

        payload = _load_cached_payload(connection, repo_name, base_commit, relative_path, content_hash) if reuse_cache else None
        if payload is None:
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for module_name in _import_module_candidates(relative_path, node):
                        resolved = _resolve_module_to_path(workspace_dir, module_name)
                        if resolved and resolved not in imports:
                            imports.append(resolved)
            definitions, expected_top_level, indexed_top_level = _extract_definitions_from_ast(
                instance_id=instance_id,
                repo_name=repo_name,
                relative_path=relative_path,
                source_text=source_text,
                tree=tree,
            )
            blocks = _extract_blocks_from_tree(
                instance_id=instance_id,
                repo_name=repo_name,
                relative_path=relative_path,
                source_text=source_text,
                tree=tree,
            )
            relations: list[dict[str, Any]] = []
            for imported in imports:
                relations.append(
                    {
                        "src_kind": "file",
                        "src_ref": relative_path,
                        "dst_kind": "file",
                        "dst_ref": imported,
                        "relation_type": "imports",
                        "weight": 1.0,
                    }
                )
                relations.append(
                    {
                        "src_kind": "file",
                        "src_ref": imported,
                        "dst_kind": "file",
                        "dst_ref": relative_path,
                        "relation_type": "imported_by",
                        "weight": 0.8,
                    }
                )
            for node, parent_class in _iter_definition_nodes_full(tree):
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            relations.append(
                                {
                                    "src_kind": "class",
                                    "src_ref": node.name,
                                    "dst_kind": "symbol",
                                    "dst_ref": child.name,
                                    "relation_type": "owns_method",
                                    "weight": 1.0,
                                }
                            )
            for constant_name in _top_level_constants(tree):
                relations.append(
                    {
                        "src_kind": "constant",
                        "src_ref": constant_name,
                        "dst_kind": "file",
                        "dst_ref": relative_path,
                        "relation_type": "defined_in",
                        "weight": 1.2,
                    }
                )
            for symbol_name in _top_level_symbol_names(tree):
                relations.append(
                    {
                        "src_kind": "symbol",
                        "src_ref": symbol_name,
                        "dst_kind": "file",
                        "dst_ref": relative_path,
                        "relation_type": "defined_in",
                        "weight": 1.0,
                    }
                )
            for block in blocks:
                block_ref = f"{block.relative_path}:{block.block_id}"
                relations.append(
                    {
                        "src_kind": "file",
                        "src_ref": block.relative_path,
                        "dst_kind": "block",
                        "dst_ref": block_ref,
                        "relation_type": "contains_block",
                        "weight": 0.9,
                    }
                )
                if block.parent_symbol_name:
                    relations.append(
                        {
                            "src_kind": "symbol",
                            "src_ref": block.parent_symbol_name,
                            "dst_kind": "block",
                            "dst_ref": block_ref,
                            "relation_type": "contains_block",
                            "weight": 0.9,
                        }
                    )
                for constant_name in json.loads(block.referenced_constants_json):
                    relations.append(
                        {
                            "src_kind": "block",
                            "src_ref": block_ref,
                            "dst_kind": "constant",
                            "dst_ref": constant_name,
                            "relation_type": "references_constant",
                            "weight": 0.8,
                        }
                    )
                for symbol_name in json.loads(block.referenced_symbols_json):
                    relations.append(
                        {
                            "src_kind": "block",
                            "src_ref": block_ref,
                            "dst_kind": "symbol",
                            "dst_ref": symbol_name,
                            "relation_type": "references_symbol",
                            "weight": 0.6,
                        }
                    )
            payload = {
                "relative_path": relative_path,
                "imports": imports,
                "top_level_constants": _top_level_constants(tree),
                "symbol_names": _top_level_symbol_names(tree),
                "definitions": [
                    {
                        "symbol": definition["symbol"],
                        "symbol_name": definition["symbol_name"],
                        "symbol_kind": definition["symbol_kind"],
                        "start_line": definition["start_line"],
                        "end_line": definition["end_line"],
                    }
                    for definition in definitions
                ],
                "blocks": [
                    {
                        "block_id": block.block_id,
                        "block_type": block.block_type,
                        "start_line": block.start_line,
                        "end_line": block.end_line,
                        "summary": block.summary,
                        "parent_symbol_name": block.parent_symbol_name,
                        "referenced_symbols": json.loads(block.referenced_symbols_json),
                        "referenced_constants": json.loads(block.referenced_constants_json),
                    }
                    for block in blocks
                ],
                "relations": relations,
                "expected_top_level_symbols": expected_top_level,
                "indexed_top_level_symbols": indexed_top_level,
            }
            if reuse_cache:
                _store_cached_payload(connection, repo_name, base_commit, relative_path, content_hash, payload)

        lines = source_text.splitlines(keepends=True)
        file_records.append(
            FileRecord(
                instance_id=instance_id,
                repo_name=repo_name,
                relative_path=relative_path,
                source=source_text,
                imports_json=json.dumps(payload["imports"]),
                top_level_constants_json=json.dumps(payload["top_level_constants"]),
                symbol_names_json=json.dumps(payload["symbol_names"]),
            )
        )
        for definition in payload["definitions"]:
            snippet = "".join(lines[definition["start_line"] - 1 : definition["end_line"]])
            definition_records.append(
                deterministic_definition_record(
                    {
                        "instance_id": instance_id,
                        "repo_name": repo_name,
                        "symbol": definition["symbol"],
                        "symbol_name": definition["symbol_name"],
                        "symbol_kind": definition["symbol_kind"],
                        "relative_path": relative_path,
                        "start_line": definition["start_line"],
                        "end_line": definition["end_line"],
                        "code": snippet,
                    }
                )
            )
        for block in payload["blocks"]:
            snippet = "".join(lines[block["start_line"] - 1 : block["end_line"]]).strip("\n")
            block_records.append(
                BlockRecord(
                    instance_id=instance_id,
                    repo_name=repo_name,
                    relative_path=relative_path,
                    block_id=block["block_id"],
                    block_type=block["block_type"],
                    start_line=block["start_line"],
                    end_line=block["end_line"],
                    code=snippet,
                    summary=block["summary"],
                    parent_symbol_name=block["parent_symbol_name"],
                    referenced_symbols_json=json.dumps(block["referenced_symbols"]),
                    referenced_constants_json=json.dumps(block["referenced_constants"]),
                )
            )
        for relation in payload["relations"]:
            relation_records.append(
                RelationRecord(
                    instance_id=instance_id,
                    src_kind=relation["src_kind"],
                    src_ref=relation["src_ref"],
                    dst_kind=relation["dst_kind"],
                    dst_ref=relation["dst_ref"],
                    relation_type=relation["relation_type"],
                    weight=float(relation["weight"]),
                )
            )
        if validate_extraction:
            expected = payload.get("expected_top_level_symbols", [])
            indexed = payload.get("indexed_top_level_symbols", [])
            missing = [name for name in expected if name not in indexed]
            if missing:
                extraction_failures.append(
                    ExtractionFailureRecord(
                        instance_id=instance_id,
                        repo_name=repo_name,
                        relative_path=relative_path,
                        content_hash=content_hash,
                        missing_symbols_json=json.dumps(missing),
                        expected_symbols_json=json.dumps(expected),
                        indexed_symbols_json=json.dumps(indexed),
                    )
                )

    unique_relations: dict[tuple[str, str, str, str, str], RelationRecord] = {}
    for relation in relation_records:
        key = (
            relation.src_kind,
            relation.src_ref,
            relation.dst_kind,
            relation.dst_ref,
            relation.relation_type,
        )
        unique_relations[key] = relation
    return file_records, definition_records, block_records, list(unique_relations.values()), extraction_failures


def upsert_extraction_failure(connection: sqlite3.Connection, record: ExtractionFailureRecord) -> None:
    connection.execute(
        """
        INSERT INTO extraction_failures (
            instance_id, repo_name, relative_path, content_hash,
            missing_symbols_json, expected_symbols_json, indexed_symbols_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, relative_path, content_hash)
        DO UPDATE SET
            repo_name=excluded.repo_name,
            missing_symbols_json=excluded.missing_symbols_json,
            expected_symbols_json=excluded.expected_symbols_json,
            indexed_symbols_json=excluded.indexed_symbols_json
        """,
        (
            record.instance_id,
            record.repo_name,
            record.relative_path,
            record.content_hash,
            record.missing_symbols_json,
            record.expected_symbols_json,
            record.indexed_symbols_json,
        ),
    )


def extract_definitions_from_document(
    instance_id: str,
    repo_name: str,
    workspace_dir: Path,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    relative_path = document.get("relative_path") or document.get("relativePath")
    if not relative_path:
        return []
    if _should_skip_path(relative_path):
        return []
    source_path = workspace_dir / relative_path
    if not source_path.exists() or source_path.suffix != ".py":
        return []

    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    try:
        nodes = _definition_nodes(source_text)
    except SyntaxError:
        return []
    lines = source_text.splitlines(keepends=True)
    definitions: list[dict[str, Any]] = []

    for occurrence in document.get("occurrences", []):
        if not _is_definition(occurrence):
            continue
        symbol = occurrence.get("symbol")
        if not symbol:
            continue
        range_values = _get_occurrence_range(occurrence)
        if not range_values:
            continue
        line_number = int(range_values[0]) + 1
        symbol_name = _symbol_name(symbol)
        candidates = []
        for node in nodes:
            if getattr(node, "name", None) != symbol_name:
                continue
            start, end = _node_span(node)
            if start <= line_number <= end:
                candidates.append(node)
        if not candidates:
            continue
        node = sorted(candidates, key=lambda item: _node_span(item))[0]
        start_line, end_line, code = _code_for_node(lines, node)
        definitions.append(
            {
                "instance_id": instance_id,
                "repo_name": repo_name,
                "symbol": symbol,
                "symbol_name": symbol_name,
                "symbol_kind": _kind_for_node(node),
                "relative_path": relative_path,
                "start_line": start_line,
                "end_line": end_line,
                "code": code,
            }
        )

    unique: dict[tuple[str, str, int], dict[str, Any]] = {}
    for item in definitions:
        unique[(item["relative_path"], item["symbol"], item["start_line"])] = item
    return list(unique.values())


def enrich_definition(llm_client, definition: dict[str, Any]) -> tuple[DefinitionRecord, dict]:
    def fallback_description() -> str:
        symbol_name = definition["symbol_name"]
        kind = definition["symbol_kind"]
        path = definition["relative_path"]
        if kind == "class":
            return (
                f"{symbol_name} is a class defined in {path}. "
                "It groups related behavior and state for this module."
            )
        return (
            f"{symbol_name} is a {kind} defined in {path}. "
            "It participates in this module's local control flow and behavior."
        )

    user_prompt = f"""Repository: {definition['repo_name']}
File: {definition['relative_path']}
Symbol: {definition['symbol_name']}
Kind: {definition['symbol_kind']}

```python
{definition['code']}
```"""
    try:
        text, usage = with_retries(
            lambda: llm_client.generate_text(DESCRIPTION_SYSTEM_PROMPT, user_prompt),
            attempts=8,
            initial_delay=5.0,
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = json.loads(repair_json(text))
        description = str(payload["description"]).strip()
    except Exception as exc:
        description = fallback_description()
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "fallback": "heuristic",
            "error": str(exc),
        }
    return DefinitionRecord(description=description, **definition), usage


def deterministic_definition_record(definition: dict[str, Any]) -> DefinitionRecord:
    symbol_name = definition["symbol_name"]
    kind = definition["symbol_kind"]
    path = definition["relative_path"]
    if kind == "class":
        description = (
            f"{symbol_name} is a class defined in {path}. "
            "It groups related behavior and state for this module."
        )
    else:
        description = (
            f"{symbol_name} is a {kind} defined in {path}. "
            "It participates in this module's local control flow and behavior."
        )
    return DefinitionRecord(description=description, **definition)


_thread_local = threading.local()


def enrich_definition_threadsafe(
    provider: str, model: str, definition: dict[str, Any]
) -> tuple[DefinitionRecord, dict, dict[str, Any]]:
    client = getattr(_thread_local, "llm_client", None)
    if client is None:
        client = build_llm_client(provider, model)
        _thread_local.llm_client = client
    record, usage = enrich_definition(client, definition)
    return record, usage, definition


def upsert_definition(connection: sqlite3.Connection, record: DefinitionRecord) -> None:
    connection.execute(
        """
        INSERT INTO symbols (
            instance_id, repo_name, symbol, symbol_name, symbol_kind,
            relative_path, start_line, end_line, code, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, symbol, relative_path, start_line)
        DO UPDATE SET
            repo_name=excluded.repo_name,
            symbol_name=excluded.symbol_name,
            symbol_kind=excluded.symbol_kind,
            end_line=excluded.end_line,
            code=excluded.code,
            description=excluded.description
        """,
        (
            record.instance_id,
            record.repo_name,
            record.symbol,
            record.symbol_name,
            record.symbol_kind,
            record.relative_path,
            record.start_line,
            record.end_line,
            record.code,
            record.description,
        ),
    )


def upsert_file_record(connection: sqlite3.Connection, record: FileRecord) -> None:
    connection.execute(
        """
        INSERT INTO files (
            instance_id, repo_name, relative_path, source, imports_json, top_level_constants_json, symbol_names_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, relative_path)
        DO UPDATE SET
            repo_name=excluded.repo_name,
            source=excluded.source,
            imports_json=excluded.imports_json,
            top_level_constants_json=excluded.top_level_constants_json,
            symbol_names_json=excluded.symbol_names_json
        """,
        (
            record.instance_id,
            record.repo_name,
            record.relative_path,
            record.source,
            record.imports_json,
            record.top_level_constants_json,
            record.symbol_names_json,
        ),
    )


def upsert_block_record(connection: sqlite3.Connection, record: BlockRecord) -> None:
    connection.execute(
        """
        INSERT INTO blocks (
            instance_id, repo_name, relative_path, block_id, block_type, start_line, end_line,
            code, summary, parent_symbol_name, referenced_symbols_json, referenced_constants_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, relative_path, block_id)
        DO UPDATE SET
            repo_name=excluded.repo_name,
            block_type=excluded.block_type,
            start_line=excluded.start_line,
            end_line=excluded.end_line,
            code=excluded.code,
            summary=excluded.summary,
            parent_symbol_name=excluded.parent_symbol_name,
            referenced_symbols_json=excluded.referenced_symbols_json,
            referenced_constants_json=excluded.referenced_constants_json
        """,
        (
            record.instance_id,
            record.repo_name,
            record.relative_path,
            record.block_id,
            record.block_type,
            record.start_line,
            record.end_line,
            record.code,
            record.summary,
            record.parent_symbol_name,
            record.referenced_symbols_json,
            record.referenced_constants_json,
        ),
    )


def upsert_relation_record(connection: sqlite3.Connection, record: RelationRecord) -> None:
    connection.execute(
        """
        INSERT INTO relations (
            instance_id, src_kind, src_ref, dst_kind, dst_ref, relation_type, weight
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, src_kind, src_ref, dst_kind, dst_ref, relation_type)
        DO UPDATE SET
            weight=excluded.weight
        """,
        (
            record.instance_id,
            record.src_kind,
            record.src_ref,
            record.dst_kind,
            record.dst_ref,
            record.relation_type,
            record.weight,
        ),
    )


def symbol_exists(connection: sqlite3.Connection, definition: dict[str, Any]) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM symbols
        WHERE instance_id = ? AND symbol = ? AND relative_path = ? AND start_line = ?
        LIMIT 1
        """,
        (
            definition["instance_id"],
            definition["symbol"],
            definition["relative_path"],
            definition["start_line"],
        ),
    ).fetchone()
    return row is not None


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    db_path = args.db_path or (settings.data_root / "enriched_graph.db")
    connection = open_database(db_path)

    if args.metadata_path is None or args.metadata_path == settings.metadata_dir / "instances.jsonl":
        rows = read_metadata(settings.metadata_dir)
    else:
        rows = []
        with args.metadata_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    for row in rows:
        instance_id = row["instance_id"]
        try:
            workspace_dir = settings.workspaces_dir / instance_id
            if args.structural_only:
                connection.execute("DELETE FROM relations WHERE instance_id = ?", (instance_id,))
                connection.execute("DELETE FROM blocks WHERE instance_id = ?", (instance_id,))
                connection.execute("DELETE FROM files WHERE instance_id = ?", (instance_id,))
                connection.execute("DELETE FROM symbols WHERE instance_id = ?", (instance_id,))
                connection.execute("DELETE FROM extraction_failures WHERE instance_id = ?", (instance_id,))
                file_records, definition_records, block_records, relation_records, extraction_failures = extract_structural_records(
                    connection=connection,
                    instance_id=instance_id,
                    repo_name=row["repo_name"],
                    base_commit=str(row.get("base_commit", "")),
                    workspace_dir=workspace_dir,
                    reuse_cache=args.reuse_cache,
                    validate_extraction=args.validate_extraction,
                )
                for record in file_records:
                    upsert_file_record(connection, record)
                for record in definition_records:
                    upsert_definition(connection, record)
                for record in block_records:
                    upsert_block_record(connection, record)
                for record in relation_records:
                    upsert_relation_record(connection, record)
                for record in extraction_failures:
                    upsert_extraction_failure(connection, record)
                connection.commit()
                print(
                    json.dumps(
                        {
                            "instance_id": instance_id,
                            "structural_only": True,
                            "symbols": len(definition_records),
                            "files": len(file_records),
                            "blocks": len(block_records),
                            "relations": len(relation_records),
                            "extraction_failures": len(extraction_failures),
                        }
                    ),
                    flush=True,
                )
                continue
            extracted: list[dict[str, Any]] = []
            index_dir = settings.indexes_dir / instance_id
            json_path = build_scip_index(workspace_dir, index_dir, settings.reuse_scip_index)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            documents = payload.get("documents", [])
            for document in documents:
                extracted.extend(
                    extract_definitions_from_document(
                        instance_id=instance_id,
                        repo_name=row["repo_name"],
                        workspace_dir=workspace_dir,
                        document=document,
                    )
                )
            file_records, block_records, relation_records = extract_file_records(
                instance_id=instance_id,
                repo_name=row["repo_name"],
                workspace_dir=workspace_dir,
            )
            for record in file_records:
                upsert_file_record(connection, record)
            for record in block_records:
                upsert_block_record(connection, record)
            connection.execute("DELETE FROM relations WHERE instance_id = ?", (instance_id,))
            for record in relation_records:
                upsert_relation_record(connection, record)
            connection.commit()
            pending = [definition for definition in extracted if not symbol_exists(connection, definition)]
            commit_counter = 0
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=settings.enrich_workers
            ) as executor:
                futures = []
                for definition in pending:
                    estimated_input_tokens = estimate_tokens(
                        f"{DESCRIPTION_SYSTEM_PROMPT}\n{definition['repo_name']}\n{definition['relative_path']}\n{definition['code']}"
                    )
                    estimated_output_tokens = 80
                    estimated_cost = estimate_text_cost_usd(
                        settings.description_llm_model,
                        estimated_input_tokens,
                        estimated_output_tokens,
                    )
                    budget_state = load_budget_state(settings.metadata_dir)
                    if budget_state.get("spent_usd", 0.0) + estimated_cost > settings.max_budget_usd:
                        raise RuntimeError(
                            f"Budget cap reached before processing {instance_id} {definition['symbol_name']}. "
                            f"Spent=${budget_state.get('spent_usd', 0.0):.2f}, next_estimate=${estimated_cost:.4f}, "
                            f"cap=${settings.max_budget_usd:.2f}"
                        )
                    futures.append(
                        executor.submit(
                            enrich_definition_threadsafe,
                            settings.description_llm_provider,
                            settings.description_llm_model,
                            definition,
                        )
                    )

                for future in tqdm(
                    concurrent.futures.as_completed(futures),
                    total=len(futures),
                    desc=f"Enriching {instance_id}",
                ):
                    record, usage, definition = future.result()
                    upsert_definition(connection, record)
                    commit_counter += 1
                    if commit_counter % settings.db_commit_interval == 0:
                        connection.commit()
                    estimated_input_tokens = estimate_tokens(
                        f"{DESCRIPTION_SYSTEM_PROMPT}\n{definition['repo_name']}\n{definition['relative_path']}\n{definition['code']}"
                    )
                    estimated_output_tokens = 80
                    actual_cost = estimate_text_cost_usd(
                        settings.description_llm_model,
                        usage.get("input_tokens", estimated_input_tokens),
                        usage.get("output_tokens", estimated_output_tokens),
                    )
                    record_budget_event(
                        settings.metadata_dir,
                        phase="graph_enrichment",
                        model=settings.description_llm_model,
                        input_tokens=usage.get("input_tokens", estimated_input_tokens),
                        output_tokens=usage.get("output_tokens", estimated_output_tokens),
                        cost_usd=actual_cost,
                        metadata={
                            "instance_id": instance_id,
                            "symbol_name": definition["symbol_name"],
                            "relative_path": definition["relative_path"],
                        },
                    )
                connection.commit()
        except Exception as exc:
            connection.rollback()
            failure = {
                "instance_id": instance_id,
                "repo_name": row.get("repo_name"),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            if args.failures_path is not None:
                args.failures_path.parent.mkdir(parents=True, exist_ok=True)
                with args.failures_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(failure) + "\n")
            print(json.dumps({"index_failure": failure}), flush=True)
            if not args.continue_on_error:
                raise

    connection.close()
    print(
        json.dumps(
            {
                "db_path": str(db_path),
                "budget_state_path": str(settings.metadata_dir / "budget_state.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
