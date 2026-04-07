from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import chromadb
except Exception:  # pragma: no cover - optional vector dependency
    chromadb = None
try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional fallback helper
    repair_json = None

from experiment.budget import estimate_text_cost_usd, load_budget_state, record_budget_event
from experiment.config import ensure_directories, load_settings
from experiment.dataset_utils import extract_stacktrace_file_hints, read_metadata
from experiment.llm_clients import (
    build_embedding_client,
    build_llm_client,
    estimate_tokens,
    with_retries,
)
from experiment.utils import write_jsonl

PATCH_SYSTEM_PROMPT = """You are fixing a bug in a checked-out SWE-bench repository.
Return only a unified diff patch that can be applied with git apply.
Do not include markdown fences, commentary, or any prose outside the patch.

Your patch must be minimal:
- Change as few files as possible.
- Change as few lines as possible.
- Do not refactor, rename, extract helpers, reformat, or edit comments/docstrings unless required for the fix.
- Prefer the smallest local fix over broader cleanup.
- Preserve all unrelated behavior."""

GRAPH_SUMMARY_SYSTEM_PROMPT = """You analyze repository structure and code relationships for debugging.
Summarize the code path, user journey, and data flow relevant to the reported issue.
Focus on how the retrieved files and symbols interact, where the bug is likely to live, and what parts of the code should change.
Be concrete and concise."""

STRUCTURED_SUMMARY_SYSTEM_PROMPT = """You analyze debugging context and return structured localization data.
Return only valid JSON with exactly these keys:
- likely_bug_files
- likely_symbols
- issue_shape
- fix_mechanism
- entrypoint_files
- implementation_files
- constant_names
- suspicious_line_patterns
- confidence

Rules:
- likely_bug_files: list of likely source file paths
- likely_symbols: list of likely functions/classes/methods/symbols
- issue_shape: short string like config_constant, parser_regex, dataflow_mask_arithmetic, format_backend, ordering_merge, generic
- fix_mechanism: one concise sentence
- entrypoint_files: list of call-path or dispatch files
- implementation_files: list of likely implementation files where the actual edit should happen
- constant_names: list of uppercase constant/config names if relevant
- suspicious_line_patterns: list of short code patterns or expressions likely involved
- confidence: number between 0 and 1
- Prefer precision over recall.
- Do not include prose outside the JSON."""

PATCH_REPAIR_SYSTEM_PROMPT = """You repair malformed unified diff patches for git apply.
Return only a valid unified diff patch that can be applied with git apply.
Do not include markdown fences, commentary, or any prose outside the patch.

Preserve the intended fix while minimizing scope:
- Keep the same target files unless the patch is impossible to apply otherwise.
- Do not introduce refactors, helper extraction, formatting cleanup, or doc/comment edits.
- Emit the smallest valid patch."""

FILE_REWRITE_SYSTEM_PROMPT = """You rewrite repository files to fix a bug.
Return only valid JSON with exactly one key: files.
The value must be a list of objects with exactly two keys: path and content.
Each content value must be the full updated file text for that path."""

STRUCTURED_EDIT_SYSTEM_PROMPT = """You repair bugs by proposing minimal bounded line edits.
Return only valid JSON with exactly one key: edits.
The value must be a list of objects with exactly four keys:
- path
- start_line
- end_line
- replacement

Rules:
- Keep edits minimal and localized.
- Only edit within the allowed line ranges provided.
- The replacement must contain only the new text for the specified line range, not surrounding lines outside that range.
- Prefer changing a single existing line over replacing a larger block.
- Preserve indentation and surrounding structure.
- Do not modify comments, docstrings, examples, or formatting unless required for the fix.
- Do not refactor, add helpers, or rewrite unrelated code."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run patch generation for both pipelines.")
    parser.add_argument(
        "--graph-db-path",
        type=Path,
        default=None,
        help="Path to enriched_graph.db.",
    )
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


def _issue_keywords(problem_statement: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "when",
        "where",
        "while",
        "which",
        "should",
        "would",
        "could",
        "there",
        "their",
        "about",
        "current",
        "actual",
        "behavior",
        "invalid",
        "error",
        "message",
        "field",
        "issue",
        "value",
        "string",
        "format",
        "return",
        "raised",
    }
    tokens = []
    for raw in problem_statement.replace("/", " ").replace("`", " ").split():
        token = raw.strip(".,:;()[]{}<>\"'`")
        lowered = token.lower()
        if (
            len(token) >= 3
            and token.replace("_", "").isalnum()
            and lowered not in stopwords
            and not lowered.isdigit()
        ):
            tokens.append(token)
    return tokens


def extract_issue_anchors(problem_statement: str) -> dict[str, list[str] | bool]:
    file_hints = dedupe_preserve(
        extract_stacktrace_file_hints(problem_statement) + extract_problem_file_mentions(problem_statement)
    )
    keywords = _issue_keywords(problem_statement)
    camel_case = [
        token
        for token in keywords
        if re.match(r"^[A-Z][A-Za-z0-9_]+$", token)
    ]
    snake_case = [
        token
        for token in keywords
        if "_" in token and re.match(r"^[A-Za-z_][A-Za-z0-9_]+$", token)
    ]
    suffix_matches = [
        token
        for token in keywords
        if re.search(r"(Field|Error|Exception|Serializer|Form|Model|Manager|View|Parser|Regex)$", token)
    ]
    constants = [token for token in keywords if token.isupper()]
    symbol_hints = dedupe_preserve(camel_case + snake_case + suffix_matches)
    return {
        "file_hints": file_hints,
        "symbol_hints": symbol_hints,
        "constant_hints": dedupe_preserve(constants),
        "keywords": keywords,
        "anchorless": not bool(file_hints or symbol_hints or constants),
    }


def dedupe_preserve(items: list[str]) -> list[str]:
    ordered: list[str] = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def _build_fts_query(tokens: list[str], limit: int = 20) -> str:
    return " OR ".join(f'"{token}"' for token in dedupe_preserve(tokens)[:limit]) or '"error"'


def _fetch_symbols_for_file(
    connection: sqlite3.Connection,
    instance_id: str,
    relative_path: str,
    preferred_symbols: list[str],
    limit: int = 6,
) -> list[dict[str, Any]]:
    preferred_set = set(preferred_symbols)
    rows = connection.execute(
        """
        SELECT symbol_name, symbol_kind, start_line, end_line, code, description
        FROM symbols
        WHERE instance_id = ? AND relative_path = ?
        ORDER BY start_line
        """,
        (instance_id, relative_path),
    ).fetchall()
    scored: list[tuple[tuple[int, int], tuple[Any, ...]]] = []
    for row in rows:
        score = 0 if str(row[0]) in preferred_set else 1
        scored.append(((score, int(row[2])), row))
    scored.sort(key=lambda item: item[0])
    return [
        {
            "relative_path": relative_path,
            "symbol_name": row[0],
            "symbol_kind": row[1],
            "start_line": row[2],
            "end_line": row[3],
            "code": row[4],
            "description": row[5],
        }
        for _, row in scored[:limit]
    ]

def retrieve_graph_context(
    connection: sqlite3.Connection, instance_id: str, problem_statement: str, top_k: int
) -> list[dict]:
    anchors = extract_issue_anchors(problem_statement)
    symbol_hints = list(anchors["symbol_hints"])
    candidate_files = retrieve_graph_file_candidates(connection, instance_id, problem_statement, max(top_k, 8))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()

    if symbol_hints:
        placeholders = ",".join("?" for _ in symbol_hints)
        exact_rows = connection.execute(
            f"""
            SELECT relative_path, symbol_name, symbol_kind, start_line, end_line, code, description
            FROM symbols
            WHERE instance_id = ? AND symbol_name IN ({placeholders})
            ORDER BY start_line
            """,
            (instance_id, *symbol_hints),
        ).fetchall()
        for row in exact_rows:
            key = (str(row[0]), str(row[1]), int(row[3]))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "relative_path": row[0],
                    "symbol_name": row[1],
                    "symbol_kind": row[2],
                    "start_line": row[3],
                    "end_line": row[4],
                    "code": row[5],
                    "description": row[6],
                }
            )

    if len(items) < top_k:
        fts_query = _build_fts_query(list(anchors["keywords"]))
        rows = connection.execute(
            """
            SELECT s.relative_path, s.symbol_name, s.symbol_kind, s.start_line, s.end_line, s.code, s.description,
                   bm25(symbols_fts) AS score
            FROM symbols_fts
            JOIN symbols s ON s.rowid = symbols_fts.rowid
            WHERE symbols_fts MATCH ? AND s.instance_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, instance_id, top_k * 4),
        ).fetchall()
        for row in rows:
            key = (str(row[0]), str(row[1]), int(row[3]))
            if key in seen:
                continue
            if str(row[0]) not in candidate_files[: max(top_k, 6)] and not any(str(row[1]) == hint for hint in symbol_hints):
                continue
            seen.add(key)
            items.append(
                {
                    "relative_path": row[0],
                    "symbol_name": row[1],
                    "symbol_kind": row[2],
                    "start_line": row[3],
                    "end_line": row[4],
                    "code": row[5],
                    "description": row[6],
                }
            )
            if len(items) >= top_k:
                break

    for relative_path in candidate_files:
        if len(items) >= top_k:
            break
        for item in _fetch_symbols_for_file(connection, instance_id, relative_path, symbol_hints, limit=4):
            key = (str(item["relative_path"]), str(item["symbol_name"]), int(item["start_line"]))
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= top_k:
                break
    return items[:top_k]


def retrieve_graph_file_candidates(
    connection: sqlite3.Connection,
    instance_id: str,
    problem_statement: str,
    top_k: int,
) -> list[str]:
    anchors = extract_issue_anchors(problem_statement)
    file_hints = list(anchors["file_hints"])
    symbol_hints = list(anchors["symbol_hints"])
    keywords = list(anchors["keywords"])
    fts_query = _build_fts_query(keywords, limit=8)
    scored: dict[str, float] = {}
    exact_symbol_paths: list[str] = []

    if symbol_hints:
        placeholders = ",".join("?" for _ in symbol_hints)
        exact_symbol_rows = connection.execute(
            f"""
            SELECT DISTINCT relative_path
            FROM symbols
            WHERE instance_id = ? AND symbol_name IN ({placeholders})
            """,
            (instance_id, *symbol_hints),
        ).fetchall()
        for (relative_path,) in exact_symbol_rows:
            path = str(relative_path)
            exact_symbol_paths.append(path)
            scored[path] = min(scored.get(path, float("inf")), -50.0)

    for hint in file_hints:
        hint_name = Path(hint).name
        hinted_rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND (relative_path = ? OR relative_path LIKE ?)
            """,
            (instance_id, hint, f"%/{hint_name}"),
        ).fetchall()
        for (relative_path,) in hinted_rows:
            scored[str(relative_path)] = min(scored.get(str(relative_path), float("inf")), -20.0)

    # If we have strong anchors, prefer exact symbol/file candidates and graph expansion
    # instead of a broad repo-wide FTS scan.
    if exact_symbol_paths or file_hints:
        seed_paths = dedupe_preserve(exact_symbol_paths + [path for path in scored])
        for path in expand_related_file_candidates(connection, instance_id, seed_paths, limit=max(top_k, 8)):
            scored[path] = min(scored.get(path, float("inf")), -5.0)
        ordered = [path for path, _ in sorted(scored.items(), key=lambda item: item[1])]
        return ordered[:top_k]

    file_rows = connection.execute(
        """
        SELECT f.relative_path, bm25(files_fts) AS score
        FROM files_fts
        JOIN files f ON f.rowid = files_fts.rowid
        WHERE files_fts MATCH ? AND f.instance_id = ?
        ORDER BY score
        LIMIT ?
        """,
        (fts_query, instance_id, top_k * 3),
    ).fetchall()
    for relative_path, score in file_rows:
        scored[str(relative_path)] = min(scored.get(str(relative_path), float("inf")), float(score))

    block_rows = connection.execute(
        """
        SELECT b.relative_path, bm25(blocks_fts) AS score
        FROM blocks_fts
        JOIN blocks b ON b.rowid = blocks_fts.rowid
        WHERE blocks_fts MATCH ? AND b.instance_id = ?
        ORDER BY score
        LIMIT ?
        """,
        (fts_query, instance_id, top_k * 4),
    ).fetchall()
    for relative_path, score in block_rows:
        path = str(relative_path)
        adjusted = float(score) - 0.2
        scored[path] = min(scored.get(path, float("inf")), adjusted)

    if symbol_hints:
        block_hint_query = _build_fts_query(symbol_hints, limit=10)
        block_hint_rows = connection.execute(
            """
            SELECT DISTINCT b.relative_path, bm25(blocks_fts) AS score
            FROM blocks_fts
            JOIN blocks b ON b.rowid = blocks_fts.rowid
            WHERE blocks_fts MATCH ? AND b.instance_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (block_hint_query, instance_id, top_k * 3),
        ).fetchall()
        for relative_path, score in block_hint_rows:
            scored[str(relative_path)] = min(scored.get(str(relative_path), float("inf")), float(score) - 0.5)

    ordered = [path for path, _ in sorted(scored.items(), key=lambda item: item[1])]
    return ordered[:top_k]


def expand_related_file_candidates(
    connection: sqlite3.Connection,
    instance_id: str,
    seed_paths: list[str],
    limit: int = 8,
) -> list[str]:
    if not seed_paths:
        return []
    placeholders = ",".join("?" for _ in seed_paths)
    rows = connection.execute(
        f"""
        SELECT dst_ref, relation_type, weight
        FROM relations
        WHERE instance_id = ? AND src_kind = 'file' AND src_ref IN ({placeholders}) AND dst_kind = 'file'
        """,
        (instance_id, *seed_paths),
    ).fetchall()
    scores: dict[str, float] = {}
    relation_bonus = {
        "imports": 0.9,
        "imported_by": 0.7,
    }
    for dst_ref, relation_type, weight in rows:
        path = str(dst_ref)
        scores[path] = scores.get(path, 0.0) + relation_bonus.get(str(relation_type), 0.4) * float(weight)
    ordered = [path for path, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
    return [path for path in ordered if path not in seed_paths][:limit]


def extract_problem_file_mentions(problem_statement: str) -> list[str]:
    matches = re.findall(r"([A-Za-z0-9_\-./]+\.py)\b", problem_statement)
    ordered: list[str] = []
    for match in matches:
        cleaned = match.strip("`'\"()[]{}<>,:")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def resolve_problem_file_paths(
    workspace_dir: Path, file_mentions: list[str], existing_paths: list[str]
) -> list[str]:
    resolved: list[str] = []
    existing_set = set(existing_paths)
    for mention in file_mentions:
        normalized = mention.lstrip("./")
        if normalized in existing_set and normalized not in resolved:
            resolved.append(normalized)
            continue
        basename = Path(normalized).name
        if not basename:
            continue
        candidates = sorted(
            str(path.relative_to(workspace_dir))
            for path in workspace_dir.rglob(basename)
            if path.is_file()
        )
        if len(candidates) == 1:
            candidate = candidates[0]
            if candidate not in existing_set and candidate not in resolved:
                resolved.append(candidate)
    return resolved


def expand_graph_file_context(
    connection: sqlite3.Connection,
    workspace_dir: Path,
    instance_id: str,
    graph_items: list[dict[str, Any]],
    problem_statement: str | None = None,
) -> list[dict[str, Any]]:
    anchors = extract_issue_anchors(problem_statement or "")
    file_paths: list[str] = []
    for item in graph_items:
        relative_path = item["relative_path"]
        if relative_path not in file_paths:
            file_paths.append(relative_path)
    if problem_statement:
        file_paths.extend(
            path
            for path in resolve_problem_file_paths(
                workspace_dir,
                extract_problem_file_mentions(problem_statement),
                file_paths,
            )
            if path not in file_paths
        )

    expanded: list[dict[str, Any]] = []
    for relative_path in file_paths:
        rows = connection.execute(
            """
            SELECT symbol_name, symbol_kind, start_line, end_line, description, code
            FROM symbols
            WHERE instance_id = ? AND relative_path = ?
            ORDER BY start_line
            """,
            (instance_id, relative_path),
        ).fetchall()
        source_path = workspace_dir / relative_path
        if not source_path.exists():
            continue
        block_rows = connection.execute(
            """
            SELECT block_id, block_type, start_line, end_line, summary, parent_symbol_name,
                   referenced_symbols_json, referenced_constants_json, code
            FROM blocks
            WHERE instance_id = ? AND relative_path = ?
            ORDER BY start_line
            """,
            (instance_id, relative_path),
        ).fetchall()
        symbol_hints = set(anchors["symbol_hints"])
        constant_hints = set(anchors["constant_hints"])
        ranked_blocks: list[tuple[tuple[int, int], tuple[Any, ...]]] = []
        for row in block_rows:
            referenced_symbols = json.loads(row[6])
            referenced_constants = json.loads(row[7])
            score = 2
            if symbol_hints.intersection(referenced_symbols) or str(row[5]) in symbol_hints:
                score = 0
            elif constant_hints.intersection(referenced_constants):
                score = 1
            ranked_blocks.append(((score, int(row[2])), row))
        ranked_blocks.sort(key=lambda item: item[0])
        expanded.append(
            {
                "relative_path": relative_path,
                "source": source_path.read_text(encoding="utf-8", errors="replace"),
                "anchors": anchors,
                "symbols": [
                    {
                        "symbol_name": row[0],
                        "symbol_kind": row[1],
                        "start_line": row[2],
                        "end_line": row[3],
                        "description": row[4],
                        "code": row[5],
                    }
                    for row in rows
                ],
                "blocks": [
                    {
                        "block_id": row[0],
                        "block_type": row[1],
                        "start_line": row[2],
                        "end_line": row[3],
                        "summary": row[4],
                        "parent_symbol_name": row[5],
                        "referenced_symbols": json.loads(row[6]),
                        "referenced_constants": json.loads(row[7]),
                        "code": row[8],
                    }
                    for _, row in ranked_blocks[:8]
                ],
            }
        )
    return expanded


def retrieve_vector_context(
    collection, embedding_client, instance_id: str, problem_statement: str, top_k: int
) -> list[dict]:
    query_embedding = embedding_client.embed_texts([problem_statement])[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"instance_id": instance_id},
    )
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    return [
        {
            "relative_path": metadata["relative_path"],
            "chunk_index": metadata["chunk_index"],
            "code": document,
        }
        for document, metadata in zip(documents, metadatas)
    ]


def render_graph_context(items: list[dict]) -> str:
    blocks = []
    for item in items:
        description = str(item.get("description") or "No precomputed description available.")
        blocks.append(
            f"""File: {item['relative_path']}:{item['start_line']}-{item['end_line']}
Symbol: {item['symbol_name']} ({item['symbol_kind']})
Description: {description}
```python
{item['code']}
```"""
        )
    return "\n\n".join(blocks)


def render_graph_summary_context(
    files: list[dict[str, Any]],
    max_tokens: int | None = None,
    candidate_budget: int | None = None,
) -> str:
    blocks = []
    limited_files = files[: candidate_budget or len(files)]
    for index, file_item in enumerate(limited_files):
        symbol_lines = []
        preferred_symbols = set(file_item.get("anchors", {}).get("symbol_hints", []))
        ranked_symbols = sorted(
            file_item["symbols"],
            key=lambda symbol: (
                0 if str(symbol["symbol_name"]) in preferred_symbols else 1,
                int(symbol["start_line"]),
            ),
        )
        for symbol in ranked_symbols[:8]:
            description = str(symbol.get("description") or "No precomputed description available.")
            symbol_lines.append(
                f"- {symbol['symbol_name']} ({symbol['symbol_kind']}) "
                f"[{symbol['start_line']}-{symbol['end_line']}]: {description}"
            )
        block_lines = []
        for block_item in file_item.get("blocks", [])[:6]:
            block_lines.append(
                f"- {block_item['block_type']} [{block_item['start_line']}-{block_item['end_line']}]: {block_item['summary']}"
            )
        source_block = ""
        if index < 2:
            source_block = f"""
Relevant file excerpt:
```python
{file_item['source']}
```"""
        block = f"""File: {file_item['relative_path']}
Symbols:
{chr(10).join(symbol_lines) if symbol_lines else '- No symbol metadata found'}
Relevant blocks:
{chr(10).join(block_lines) if block_lines else '- No block metadata found'}{source_block}"""
        blocks.append(block)
        if max_tokens is not None and estimate_tokens("\n\n".join(blocks)) >= max_tokens:
            break
    return "\n\n".join(blocks)


def render_vector_context(items: list[dict]) -> str:
    blocks = []
    for item in items:
        blocks.append(
            f"""File: {item['relative_path']} chunk={item['chunk_index']}
```python
{item['code']}
```"""
        )
    return "\n\n".join(blocks)


def write_prompt_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_graph_summary_prompt(problem_statement: str, graph_context: str) -> str:
    return f"""Problem statement:
{problem_statement}

Retrieved graph/file context:
{graph_context}

Summarize the relevant code journey, user journey, and data flow for this bug.
Call out the most likely bug location and why."""


def build_structured_summary_prompt(problem_statement: str, graph_context: str) -> str:
    return f"""Problem statement:
{problem_statement}

Retrieved graph/file context:
{graph_context}

Return structured localization data for the most likely bug location."""


def build_patch_prompt(
    problem_statement: str,
    context: str,
    pipeline_name: str,
    graph_summary: str | None = None,
) -> str:
    summary_block = ""
    if graph_summary:
        summary_block = f"""

Graph-derived code and data journey summary:
{graph_summary}"""
    return f"""Pipeline: {pipeline_name}

Problem statement:
{problem_statement}
{summary_block}

Retrieved context:
{context}

Produce the smallest correct patch.

Requirements:
- Edit the fewest files possible.
- Edit the fewest lines possible.
- Do not add helpers, move code, or rewrite surrounding logic unless absolutely necessary.
- Do not modify docstrings, comments, examples, or formatting unless required for the fix.
- Keep the patch narrowly targeted to the bug described above.
- Preserve existing passing behavior."""


def build_hybrid_graph_vector_context(
    graph_symbol_context: str,
    graph_summary: str,
    vector_context: str,
) -> str:
    return f"""Graph results:
These results come from the semantic graph index. They are included to provide structured symbol-level context,
file relationships, and precomputed descriptions of what the retrieved code does in the broader codebase.

Graph symbol context:
{graph_symbol_context}

Graph-derived user/code/data journey:
{graph_summary}

Vector search results:
These results come from raw vector similarity search over code chunks. They are included to provide nearby literal
implementation details that may be useful for writing the final patch.

Vector code chunks:
{vector_context}"""


def generate_graph_summary(llm_client, problem_statement: str, graph_context: str) -> tuple[str, dict]:
    prompt = build_graph_summary_prompt(problem_statement, graph_context)
    return with_retries(lambda: llm_client.generate_text(GRAPH_SUMMARY_SYSTEM_PROMPT, prompt))


def generate_structured_summary(llm_client, problem_statement: str, graph_context: str) -> tuple[dict[str, Any], dict]:
    prompt = build_structured_summary_prompt(problem_statement, graph_context)
    payload = with_retries(lambda: llm_client.generate_json(STRUCTURED_SUMMARY_SYSTEM_PROMPT, prompt))
    if not isinstance(payload, dict):
        payload = {}
    normalized = {
        "likely_bug_files": [str(item) for item in payload.get("likely_bug_files", []) if str(item).strip()],
        "likely_symbols": [str(item) for item in payload.get("likely_symbols", []) if str(item).strip()],
        "issue_shape": str(payload.get("issue_shape", "generic")).strip() or "generic",
        "fix_mechanism": str(payload.get("fix_mechanism", "")).strip(),
        "entrypoint_files": [str(item) for item in payload.get("entrypoint_files", []) if str(item).strip()],
        "implementation_files": [str(item) for item in payload.get("implementation_files", []) if str(item).strip()],
        "constant_names": [str(item) for item in payload.get("constant_names", []) if str(item).strip()],
        "suspicious_line_patterns": [str(item) for item in payload.get("suspicious_line_patterns", []) if str(item).strip()],
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
    }
    return normalized, {}


def generate_patch(
    llm_client,
    problem_statement: str,
    context: str,
    pipeline_name: str,
    graph_summary: str | None = None,
) -> tuple[str, dict]:
    prompt = build_patch_prompt(problem_statement, context, pipeline_name, graph_summary=graph_summary)
    return with_retries(lambda: llm_client.generate_text(PATCH_SYSTEM_PROMPT, prompt))


def validate_patch(workspace_dir: Path, patch_text: str) -> tuple[bool, str]:
    normalized = patch_text if patch_text.endswith("\n") else patch_text + "\n"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_dir = tmp_path / "repo"
            subprocess.run(
                ["git", "clone", "--quiet", str(workspace_dir), str(repo_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".diff", delete=False) as handle:
                handle.write(normalized)
                patch_path = Path(handle.name)
            try:
                result = subprocess.run(
                    ["git", "apply", "--check", str(patch_path)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
                combined = (result.stdout or "") + (result.stderr or "")
                return result.returncode == 0, combined.strip()
            finally:
                patch_path.unlink(missing_ok=True)
    except subprocess.CalledProcessError as exc:
        combined = (exc.stdout or "") + (exc.stderr or "")
        return False, combined.strip()


def build_patch_repair_prompt(
    problem_statement: str,
    context: str,
    pipeline_name: str,
    invalid_patch: str,
    apply_error: str,
    graph_summary: str | None = None,
) -> str:
    summary_block = ""
    if graph_summary:
        summary_block = f"""

Graph-derived code and data journey summary:
{graph_summary}"""
    return f"""Pipeline: {pipeline_name}

Problem statement:
{problem_statement}
{summary_block}

Retrieved context:
{context}

The previous patch was invalid for `git apply`.

git apply error:
{apply_error}

Invalid patch:
{invalid_patch}

Rewrite the patch so it is a valid unified diff with correct file headers and hunk headers."""


def ensure_valid_patch(
    llm_client,
    workspace_dir: Path,
    problem_statement: str,
    context: str,
    pipeline_name: str,
    patch_text: str,
    candidate_paths: list[str],
    graph_summary: str | None = None,
    max_attempts: int = 3,
    allow_fallback: bool = True,
    allowed_regions: list[dict[str, int]] | None = None,
) -> tuple[str, dict[str, Any]]:
    usage_summary: dict[str, Any] = {"repair_attempts": 0, "repair_input_tokens": 0, "repair_output_tokens": 0}
    candidate = patch_text
    for attempt in range(max_attempts):
        valid, error_text = validate_patch(workspace_dir, candidate)
        if valid:
            return candidate if candidate.endswith("\n") else candidate + "\n", usage_summary
        usage_summary["last_apply_error"] = error_text
        if attempt == max_attempts - 1:
            break
        prompt = build_patch_repair_prompt(
            problem_statement,
            context,
            pipeline_name,
            candidate,
            error_text,
            graph_summary=graph_summary,
        )
        repaired_text, repair_usage = with_retries(
            lambda: llm_client.generate_text(PATCH_REPAIR_SYSTEM_PROMPT, prompt)
        )
        usage_summary["repair_attempts"] += 1
        usage_summary["repair_input_tokens"] += repair_usage.get("input_tokens", 0)
        usage_summary["repair_output_tokens"] += repair_usage.get("output_tokens", 0)
        candidate = repaired_text
    if not allow_fallback:
        valid, error_text = validate_patch(workspace_dir, candidate)
        usage_summary["fallback_valid"] = valid
        usage_summary["fallback_apply_error"] = error_text
        return candidate if candidate.endswith("\n") else candidate + "\n", usage_summary
    if allowed_regions:
        fallback_patch, fallback_usage = synthesize_patch_from_structured_edits(
            llm_client,
            workspace_dir,
            problem_statement,
            context,
            pipeline_name,
            candidate_paths,
            allowed_regions,
            graph_summary=graph_summary,
        )
        usage_summary["fallback_input_tokens"] = fallback_usage.get("input_tokens", 0)
        usage_summary["fallback_output_tokens"] = fallback_usage.get("output_tokens", 0)
        valid, error_text = validate_patch(workspace_dir, fallback_patch)
        usage_summary["fallback_apply_error"] = error_text
        usage_summary["fallback_valid"] = valid
        return fallback_patch if fallback_patch.endswith("\n") else fallback_patch + "\n", usage_summary
    fallback_patch, fallback_usage = synthesize_patch_from_files(
        llm_client,
        workspace_dir,
        problem_statement,
        context,
        pipeline_name,
        candidate_paths,
        graph_summary=graph_summary,
    )
    usage_summary["fallback_input_tokens"] = fallback_usage.get("input_tokens", 0)
    usage_summary["fallback_output_tokens"] = fallback_usage.get("output_tokens", 0)
    valid, error_text = validate_patch(workspace_dir, fallback_patch)
    usage_summary["fallback_apply_error"] = error_text
    usage_summary["fallback_valid"] = valid
    return fallback_patch if fallback_patch.endswith("\n") else fallback_patch + "\n", usage_summary


def build_file_rewrite_prompt(
    problem_statement: str,
    context: str,
    pipeline_name: str,
    file_payloads: list[dict[str, str]],
    graph_summary: str | None = None,
) -> str:
    summary_block = ""
    if graph_summary:
        summary_block = f"""

Graph-derived code and data journey summary:
{graph_summary}"""
    files_block = []
    for item in file_payloads:
        files_block.append(
            f"""Path: {item['path']}
```python
{item['content']}
```"""
        )
    return f"""Pipeline: {pipeline_name}

Problem statement:
{problem_statement}
{summary_block}

Retrieved context:
{context}

Candidate files to rewrite:
{chr(10).join(files_block)}

Update only the files that must change to fix the bug.

Requirements:
- Keep edits minimal and localized.
- Preserve all unrelated lines exactly.
- Do not add helpers, refactor, or clean up formatting unless absolutely necessary for the fix.
- Do not modify docstrings, comments, or examples unless required.

Return full file contents."""


def synthesize_patch_from_files(
    llm_client,
    workspace_dir: Path,
    problem_statement: str,
    context: str,
    pipeline_name: str,
    candidate_paths: list[str],
    graph_summary: str | None = None,
) -> tuple[str, dict[str, Any]]:
    file_payloads: list[dict[str, str]] = []
    for relative_path in candidate_paths[:3]:
        source_path = workspace_dir / relative_path
        if source_path.exists():
            file_payloads.append(
                {
                    "path": relative_path,
                    "content": source_path.read_text(encoding="utf-8", errors="replace"),
                }
            )
    prompt = build_file_rewrite_prompt(
        problem_statement,
        context,
        pipeline_name,
        file_payloads,
        graph_summary=graph_summary,
    )
    text, usage = with_retries(lambda: llm_client.generate_text(FILE_REWRITE_SYSTEM_PROMPT, prompt))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if repair_json is None:
            raise
        payload = json.loads(repair_json(text))
    patches: list[str] = []
    for item in payload.get("files", []):
        relative_path = item["path"]
        source_path = workspace_dir / relative_path
        if not source_path.exists():
            continue
        updated_content = str(item["content"])
        if "\n" not in updated_content and "\\n" in updated_content:
            updated_content = updated_content.replace("\\r\\n", "\n").replace("\\n", "\n")
        original = source_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        updated = updated_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                original,
                updated,
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
        )
        if diff_lines:
            patches.append("".join(line if line.endswith("\n") else f"{line}\n" for line in diff_lines))
    return "".join(patches), usage


def merge_allowed_regions(
    allowed_regions: list[dict[str, int]],
    margin: int = 3,
) -> list[dict[str, int]]:
    if not allowed_regions:
        return []
    normalized = sorted(
        (
            {
                "path": str(item["path"]),
                "start_line": max(1, int(item["start_line"]) - margin),
                "end_line": int(item["end_line"]) + margin,
            }
            for item in allowed_regions
        ),
        key=lambda item: (item["path"], item["start_line"], item["end_line"]),
    )
    merged: list[dict[str, int]] = []
    for item in normalized:
        if not merged or merged[-1]["path"] != item["path"] or item["start_line"] > merged[-1]["end_line"] + 1:
            merged.append(item.copy())
            continue
        merged[-1]["end_line"] = max(merged[-1]["end_line"], item["end_line"])
    return merged


def build_structured_edit_prompt(
    problem_statement: str,
    context: str,
    pipeline_name: str,
    file_payloads: list[dict[str, str]],
    allowed_regions: list[dict[str, int]],
    graph_summary: str | None = None,
) -> str:
    summary_block = ""
    if graph_summary:
        summary_block = f"""

Graph-derived code and data journey summary:
{graph_summary}"""
    files_block = []
    for item in file_payloads:
        files_block.append(
            f"""Path: {item['path']}
```python
{item['content']}
```"""
        )
    regions_block = []
    for item in allowed_regions:
        regions_block.append(f"- {item['path']}:{item['start_line']}-{item['end_line']}")
    excerpt_block = []
    for item in allowed_regions:
        matching = next((payload for payload in file_payloads if payload["path"] == item["path"]), None)
        if matching is None:
            continue
        lines = matching["content"].splitlines()
        start_index = max(0, item["start_line"] - 1)
        end_index = min(len(lines), item["end_line"])
        excerpt = "\n".join(lines[start_index:end_index])
        excerpt_block.append(
            f"""Path: {item['path']} lines {item['start_line']}-{item['end_line']}
```python
{excerpt}
```"""
        )
    return f"""Pipeline: {pipeline_name}

Problem statement:
{problem_statement}
{summary_block}

Retrieved context:
{context}

Candidate files:
{chr(10).join(files_block)}

Allowed edit ranges:
{chr(10).join(regions_block)}

Original text for allowed ranges:
{chr(10).join(excerpt_block)}

Return minimal bounded edits only.
Do not edit outside the allowed ranges.
If a one-line change is sufficient, return a one-line replacement."""


def apply_structured_edits(
    workspace_dir: Path,
    edits: list[dict[str, Any]],
) -> dict[str, str]:
    updated_files: dict[str, str] = {}
    for edit in edits:
        relative_path = str(edit["path"])
        source_path = workspace_dir / relative_path
        if not source_path.exists():
            continue
        original_text = updated_files.get(
            relative_path,
            source_path.read_text(encoding="utf-8", errors="replace"),
        )
        lines = original_text.splitlines(keepends=True)
        start_index = max(0, int(edit["start_line"]) - 1)
        end_index = min(len(lines), int(edit["end_line"]))
        replacement = str(edit["replacement"])
        replacement_lines = replacement.splitlines(keepends=True)
        if replacement and not replacement.endswith("\n") and replacement_lines:
            replacement_lines[-1] = replacement_lines[-1] + "\n"
        lines[start_index:end_index] = replacement_lines
        updated_files[relative_path] = "".join(lines)
    return updated_files


def synthesize_patch_from_structured_edits(
    llm_client,
    workspace_dir: Path,
    problem_statement: str,
    context: str,
    pipeline_name: str,
    candidate_paths: list[str],
    allowed_regions: list[dict[str, int]],
    graph_summary: str | None = None,
) -> tuple[str, dict[str, Any]]:
    merged_regions = merge_allowed_regions(allowed_regions)
    file_payloads: list[dict[str, str]] = []
    for relative_path in candidate_paths[:3]:
        source_path = workspace_dir / relative_path
        if source_path.exists():
            file_payloads.append(
                {
                    "path": relative_path,
                    "content": source_path.read_text(encoding="utf-8", errors="replace"),
                }
            )
    prompt = build_structured_edit_prompt(
        problem_statement,
        context,
        pipeline_name,
        file_payloads,
        merged_regions,
        graph_summary=graph_summary,
    )
    text, usage = with_retries(lambda: llm_client.generate_text(STRUCTURED_EDIT_SYSTEM_PROMPT, prompt))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if repair_json is None:
            raise
        payload = json.loads(repair_json(text))
    allowed_by_path: dict[str, list[tuple[int, int]]] = {}
    for item in merged_regions:
        allowed_by_path.setdefault(item["path"], []).append((item["start_line"], item["end_line"]))
    filtered_edits: list[dict[str, Any]] = []
    for item in payload.get("edits", []):
        relative_path = str(item["path"])
        start_line = int(item["start_line"])
        end_line = int(item["end_line"])
        ranges = allowed_by_path.get(relative_path, [])
        if any(start_line >= allowed_start and end_line <= allowed_end for allowed_start, allowed_end in ranges):
            filtered_edits.append(
                {
                    "path": relative_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "replacement": str(item["replacement"]),
                }
            )
    updated_files = apply_structured_edits(workspace_dir, filtered_edits)
    patches: list[str] = []
    for relative_path, updated_content in updated_files.items():
        source_path = workspace_dir / relative_path
        original = source_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        updated = updated_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                original,
                updated,
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
        )
        if diff_lines:
            patches.append("".join(line if line.endswith("\n") else f"{line}\n" for line in diff_lines))
    return "".join(patches), usage


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ensure_directories(settings)

    graph_db_path = args.graph_db_path or (settings.data_root / "enriched_graph.db")
    graph_connection = sqlite3.connect(graph_db_path)
    summary_llm_client = build_llm_client(
        settings.description_llm_provider, settings.description_llm_model
    )
    patch_llm_client = build_llm_client(settings.patch_llm_provider, settings.patch_llm_model)
    embedding_client = build_embedding_client(settings.embedding_provider, settings.embedding_model)

    chroma_client = chromadb.PersistentClient(path=str(settings.vector_db_dir))
    collection = chroma_client.get_collection(name=args.collection_name)

    metadata_path = args.metadata_path or (settings.metadata_dir / "instances.jsonl")
    rows = read_metadata(settings.metadata_dir)
    if metadata_path != settings.metadata_dir / "instances.jsonl":
        rows = []
        with metadata_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    graph_predictions: list[dict] = []
    vector_predictions: list[dict] = []
    graph_metrics: list[dict] = []
    vector_metrics: list[dict] = []

    for row in rows:
        instance_id = row["instance_id"]
        problem_statement = row["problem_statement"]
        workspace_dir = settings.workspaces_dir / instance_id
        prompt_dir = settings.data_root / "logs" / "prompts" / instance_id

        vector_items = retrieve_vector_context(
            collection,
            embedding_client,
            instance_id,
            problem_statement,
            settings.vector_top_k,
        )
        vector_context = render_vector_context(vector_items)
        write_prompt_artifact(prompt_dir / "vector_context.md", vector_context)

        graph_items = retrieve_graph_context(
            graph_connection, instance_id, problem_statement, settings.graph_top_k
        )
        graph_symbol_context = render_graph_context(graph_items)
        write_prompt_artifact(prompt_dir / "graph_symbol_context.md", graph_symbol_context)
        graph_file_items = expand_graph_file_context(
            graph_connection,
            workspace_dir,
            instance_id,
            graph_items,
            problem_statement=problem_statement,
        )
        extra_file_paths = retrieve_graph_file_candidates(
            graph_connection, instance_id, problem_statement, settings.graph_top_k
        )
        extra_file_paths.extend(
            expand_related_file_candidates(
                graph_connection,
                instance_id,
                [str(item["relative_path"]) for item in graph_file_items],
            )
        )
        existing_paths = {str(item["relative_path"]) for item in graph_file_items}
        extra_file_items = expand_graph_file_context(
            graph_connection,
            workspace_dir,
            instance_id,
            [{"relative_path": path} for path in extra_file_paths if path not in existing_paths],
            problem_statement=problem_statement,
        )
        graph_file_items.extend(extra_file_items)
        graph_file_context = render_graph_summary_context(
            graph_file_items,
            max_tokens=settings.max_summary_tokens,
            candidate_budget=settings.candidate_budget,
        )
        write_prompt_artifact(prompt_dir / "graph_file_context.md", graph_file_context)
        write_prompt_artifact(prompt_dir / "problem_statement.md", problem_statement)
        graph_summary_prompt = build_graph_summary_prompt(problem_statement, graph_file_context)
        write_prompt_artifact(prompt_dir / "graph_summary_prompt.md", graph_summary_prompt)
        graph_summary, graph_summary_usage = generate_graph_summary(
            summary_llm_client, problem_statement, graph_file_context
        )
        write_prompt_artifact(prompt_dir / "graph_summary.md", graph_summary)
        graph_structured_summary_prompt = build_structured_summary_prompt(problem_statement, graph_file_context)
        write_prompt_artifact(prompt_dir / "graph_summary_structured_prompt.md", graph_structured_summary_prompt)
        graph_structured_summary, graph_structured_summary_usage = generate_structured_summary(
            summary_llm_client, problem_statement, graph_file_context
        )
        write_prompt_artifact(prompt_dir / "graph_summary.json", json.dumps(graph_structured_summary, indent=2))
        graph_generation_context = build_hybrid_graph_vector_context(
            graph_symbol_context,
            graph_summary,
            vector_context,
        )
        graph_patch_prompt = build_patch_prompt(
            problem_statement,
            graph_generation_context,
            "graph_hybrid",
            graph_summary=graph_summary,
        )
        write_prompt_artifact(prompt_dir / "graph_patch_prompt.md", graph_patch_prompt)
        graph_patch, graph_usage = generate_patch(
            patch_llm_client,
            problem_statement,
            graph_generation_context,
            "graph_hybrid",
            graph_summary=graph_summary,
        )
        graph_patch, graph_repair_usage = ensure_valid_patch(
            patch_llm_client,
            workspace_dir,
            problem_statement,
            graph_generation_context,
            "graph_hybrid",
            graph_patch,
            [item["relative_path"] for item in graph_items],
            graph_summary=graph_summary,
            allowed_regions=[
                {
                    "path": item["relative_path"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                }
                for item in graph_items
            ],
        )
        write_prompt_artifact(prompt_dir / "graph_patch.diff", graph_patch)
        graph_predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": settings.patch_llm_model,
                "model_patch": graph_patch,
            }
        )
        record_budget_event(
            settings.metadata_dir,
            phase="graph_summary",
            model=settings.description_llm_model,
            input_tokens=graph_summary_usage.get(
                "input_tokens", estimate_tokens(graph_file_context + problem_statement)
            ),
            output_tokens=graph_summary_usage.get("output_tokens", estimate_tokens(graph_summary)),
            cost_usd=estimate_text_cost_usd(
                settings.description_llm_model,
                graph_summary_usage.get(
                    "input_tokens", estimate_tokens(graph_file_context + problem_statement)
                ),
                graph_summary_usage.get("output_tokens", estimate_tokens(graph_summary)),
            ),
            metadata={"instance_id": instance_id},
        )
        record_budget_event(
            settings.metadata_dir,
            phase="graph_structured_summary",
            model=settings.description_llm_model,
            input_tokens=graph_structured_summary_usage.get(
                "input_tokens", estimate_tokens(graph_file_context + problem_statement)
            ),
            output_tokens=graph_structured_summary_usage.get("output_tokens", estimate_tokens(json.dumps(graph_structured_summary))),
            cost_usd=estimate_text_cost_usd(
                settings.description_llm_model,
                graph_structured_summary_usage.get(
                    "input_tokens", estimate_tokens(graph_file_context + problem_statement)
                ),
                graph_structured_summary_usage.get("output_tokens", estimate_tokens(json.dumps(graph_structured_summary))),
            ),
            metadata={"instance_id": instance_id},
        )
        record_budget_event(
            settings.metadata_dir,
            phase="graph_patch",
            model=settings.patch_llm_model,
            input_tokens=graph_usage.get(
                "input_tokens",
                estimate_tokens(graph_symbol_context + vector_context + graph_summary + problem_statement),
            ),
            output_tokens=graph_usage.get("output_tokens", estimate_tokens(graph_patch)),
            cost_usd=estimate_text_cost_usd(
                settings.patch_llm_model,
                graph_usage.get(
                    "input_tokens",
                    estimate_tokens(graph_symbol_context + vector_context + graph_summary + problem_statement),
                ),
                graph_usage.get("output_tokens", estimate_tokens(graph_patch)),
            ),
            metadata={"instance_id": instance_id},
        )
        if graph_repair_usage.get("repair_attempts", 0):
            record_budget_event(
                settings.metadata_dir,
                phase="graph_patch_repair",
                model=settings.patch_llm_model,
                input_tokens=graph_repair_usage.get("repair_input_tokens", 0),
                output_tokens=graph_repair_usage.get("repair_output_tokens", 0),
                cost_usd=estimate_text_cost_usd(
                    settings.patch_llm_model,
                    graph_repair_usage.get("repair_input_tokens", 0),
                    graph_repair_usage.get("repair_output_tokens", 0),
                ),
                metadata={
                    "instance_id": instance_id,
                    "repair_attempts": graph_repair_usage.get("repair_attempts", 0),
                    "last_apply_error": graph_repair_usage.get("last_apply_error", ""),
                },
            )
        if graph_repair_usage.get("fallback_input_tokens", 0) or graph_repair_usage.get("fallback_output_tokens", 0):
            record_budget_event(
                settings.metadata_dir,
                phase="graph_patch_fallback",
                model=settings.patch_llm_model,
                input_tokens=graph_repair_usage.get("fallback_input_tokens", 0),
                output_tokens=graph_repair_usage.get("fallback_output_tokens", 0),
                cost_usd=estimate_text_cost_usd(
                    settings.patch_llm_model,
                    graph_repair_usage.get("fallback_input_tokens", 0),
                    graph_repair_usage.get("fallback_output_tokens", 0),
                ),
                metadata={
                    "instance_id": instance_id,
                    "fallback_valid": graph_repair_usage.get("fallback_valid", False),
                    "fallback_apply_error": graph_repair_usage.get("fallback_apply_error", ""),
                },
            )
        graph_metrics.append(
            {
                "instance_id": instance_id,
                "pipeline": "graph",
                "retrieved_items": len(graph_items),
                "retrieved_files": len(graph_file_items),
                "summary_chars": len(graph_summary),
                "context_chars": len(graph_symbol_context) + len(vector_context) + len(graph_summary),
                "context_tokens_estimate": estimate_tokens(graph_symbol_context + vector_context + graph_summary),
                "llm_input_tokens": graph_usage.get("input_tokens", 0)
                + graph_repair_usage.get("repair_input_tokens", 0)
                + graph_repair_usage.get("fallback_input_tokens", 0),
                "llm_output_tokens": graph_usage.get("output_tokens", 0)
                + graph_repair_usage.get("repair_output_tokens", 0)
                + graph_repair_usage.get("fallback_output_tokens", 0),
            }
        )

        vector_patch_prompt = build_patch_prompt(problem_statement, vector_context, "vector")
        write_prompt_artifact(prompt_dir / "vector_patch_prompt.md", vector_patch_prompt)
        vector_patch, vector_usage = generate_patch(
            patch_llm_client, problem_statement, vector_context, "vector"
        )
        vector_patch, vector_repair_usage = ensure_valid_patch(
            patch_llm_client,
            workspace_dir,
            problem_statement,
            vector_context,
            "vector",
            vector_patch,
            [item["relative_path"] for item in vector_items],
        )
        write_prompt_artifact(prompt_dir / "vector_patch.diff", vector_patch)
        vector_predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": settings.patch_llm_model,
                "model_patch": vector_patch,
            }
        )
        record_budget_event(
            settings.metadata_dir,
            phase="vector_patch",
            model=settings.patch_llm_model,
            input_tokens=vector_usage.get("input_tokens", estimate_tokens(vector_context + problem_statement)),
            output_tokens=vector_usage.get("output_tokens", estimate_tokens(vector_patch)),
            cost_usd=estimate_text_cost_usd(
                settings.patch_llm_model,
                vector_usage.get("input_tokens", estimate_tokens(vector_context + problem_statement)),
                vector_usage.get("output_tokens", estimate_tokens(vector_patch)),
            ),
            metadata={"instance_id": instance_id},
        )
        if vector_repair_usage.get("repair_attempts", 0):
            record_budget_event(
                settings.metadata_dir,
                phase="vector_patch_repair",
                model=settings.patch_llm_model,
                input_tokens=vector_repair_usage.get("repair_input_tokens", 0),
                output_tokens=vector_repair_usage.get("repair_output_tokens", 0),
                cost_usd=estimate_text_cost_usd(
                    settings.patch_llm_model,
                    vector_repair_usage.get("repair_input_tokens", 0),
                    vector_repair_usage.get("repair_output_tokens", 0),
                ),
                metadata={
                    "instance_id": instance_id,
                    "repair_attempts": vector_repair_usage.get("repair_attempts", 0),
                    "last_apply_error": vector_repair_usage.get("last_apply_error", ""),
                },
            )
        if vector_repair_usage.get("fallback_input_tokens", 0) or vector_repair_usage.get("fallback_output_tokens", 0):
            record_budget_event(
                settings.metadata_dir,
                phase="vector_patch_fallback",
                model=settings.patch_llm_model,
                input_tokens=vector_repair_usage.get("fallback_input_tokens", 0),
                output_tokens=vector_repair_usage.get("fallback_output_tokens", 0),
                cost_usd=estimate_text_cost_usd(
                    settings.patch_llm_model,
                    vector_repair_usage.get("fallback_input_tokens", 0),
                    vector_repair_usage.get("fallback_output_tokens", 0),
                ),
                metadata={
                    "instance_id": instance_id,
                    "fallback_valid": vector_repair_usage.get("fallback_valid", False),
                    "fallback_apply_error": vector_repair_usage.get("fallback_apply_error", ""),
                },
            )
        vector_metrics.append(
            {
                "instance_id": instance_id,
                "pipeline": "vector",
                "retrieved_items": len(vector_items),
                "context_chars": len(vector_context),
                "context_tokens_estimate": estimate_tokens(vector_context),
                "llm_input_tokens": vector_usage.get("input_tokens", 0)
                + vector_repair_usage.get("repair_input_tokens", 0)
                + vector_repair_usage.get("fallback_input_tokens", 0),
                "llm_output_tokens": vector_usage.get("output_tokens", 0)
                + vector_repair_usage.get("repair_output_tokens", 0)
                + vector_repair_usage.get("fallback_output_tokens", 0),
            }
        )

    write_jsonl(settings.project_root / "predictions_graph.jsonl", graph_predictions)
    write_jsonl(settings.project_root / "predictions_vector.jsonl", vector_predictions)
    write_jsonl(settings.metadata_dir / "graph_context_metrics.jsonl", graph_metrics)
    write_jsonl(settings.metadata_dir / "vector_context_metrics.jsonl", vector_metrics)

    graph_connection.close()
    print(
        json.dumps(
            {
                "predictions_graph": str(settings.project_root / "predictions_graph.jsonl"),
                "predictions_vector": str(settings.project_root / "predictions_vector.jsonl"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
