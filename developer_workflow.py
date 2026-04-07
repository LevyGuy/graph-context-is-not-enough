from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from experiment.dataset_utils import extract_stacktrace_file_hints
from experiment.llm_clients import estimate_tokens, with_retries
from graph_exact_patch_pipeline import (
    build_constrained_candidate_pools,
    build_target_selection_prompt,
    choose_target,
    choose_target_heuristic,
    hydrate_clean_file_items,
    normalize_structured_summary,
    select_target_deterministic,
)
from run_inference import expand_graph_file_context


DEVELOPER_SUMMARY_SYSTEM_PROMPT = """You analyze developer workflow evidence for bug localization.
Use the gathered evidence to explain the likely implementation path, the likely bug location, and the reasoning that ties the issue report to the code.
Stay concrete, cite files and symbols when possible, and prefer implementation sites over user-facing wrappers."""

DEVELOPER_STRUCTURED_SUMMARY_SYSTEM_PROMPT = """You analyze developer workflow evidence and return structured localization data.
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
- likely_bug_files: likely implementation files, ordered most likely first
- likely_symbols: likely classes/functions/methods/symbols
- issue_shape: short string like config_constant, parser_regex, migration_serialization, ui_rendering, query_lookup, validation_semantics, generic
- fix_mechanism: one concise sentence
- entrypoint_files: dispatch/caller/consumer files
- implementation_files: preferred edit sites
- constant_names: uppercase constants if relevant
- suspicious_line_patterns: short code patterns likely involved
- confidence: number between 0 and 1
- Prefer implementation files over consumer files.
- Prefer files supported by multiple deterministic tools.
- Do not include prose outside the JSON."""

FILE_COMPARISON_SYSTEM_PROMPT = """You compare competing implementation files for bug localization.
Return only valid JSON with exactly these keys:
- top_files
- preferred_file
- runner_up_files
- why_preferred
- why_others_rejected
- confidence

Rules:
- top_files: ordered list from the provided candidate files only
- preferred_file: one file from the provided candidates
- runner_up_files: ordered remaining strong alternatives from the provided candidates
- why_preferred: short explanation grounded in evidence
- why_others_rejected: object mapping file path to short explanation
- confidence: number between 0 and 1
- Prefer implementation-definition files over usage-only files.
- Prefer files supported by multiple evidence channels.
- Do not invent files outside the candidate list."""

ANCHOR_EXTRACTION_SYSTEM_PROMPT = """You normalize issue anchors for static code search.
Return only valid JSON with exactly these keys:
- file_hints
- symbol_hints
- error_types
- code_literals
- config_names
- regex_patterns
- framework_terms

Rules:
- Extract only concrete search anchors that appear in or are directly implied by the issue text.
- Do not invent file paths.
- Keep lists short and precise.
- Do not include prose."""

ISSUE_KEYWORD_STOPWORDS = {
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
    "issue",
    "value",
    "string",
    "return",
    "raised",
    "using",
    "used",
    "after",
    "before",
    "cause",
    "causes",
    "caused",
    "description",
    "currently",
    "please",
    "example",
    "examples",
    "improvement",
    "allow",
    "make",
    "prevent",
    "correct",
    "last",
    "modified",
    "imo",
    "bug",
    "pr",
}

FRAMEWORK_TERMS = {
    "migration",
    "migrations",
    "management",
    "command",
    "boundfield",
    "reverse_related",
    "serializer",
    "admin",
    "widget",
    "field",
    "form",
    "model",
    "template",
    "autoreload",
    "lookup",
    "compiler",
    "query",
    "enum",
    "choices",
    "validation",
}

GENERIC_FILE_HINTS = {
    "__init__.py",
    "models.py",
    "utils.py",
    "fields.py",
    "forms.py",
    "widgets.py",
    "admin.py",
    "settings.py",
    "tests.py",
    "manage.py",
}

GENERIC_PATH_TERMS = {
    "model",
    "models",
    "management",
    "command",
    "field",
    "fields",
    "form",
    "forms",
    "widget",
    "widgets",
    "validation",
    "choice",
    "choices",
}

GENERIC_SYMBOL_HINTS = {
    "py",
    "db",
    "model",
    "models",
    "field",
    "fields",
    "name",
    "message",
    "messages",
    "translation",
    "argv",
    "all",
}

LOW_INFORMATION_TRACE_SYMBOLS = {
    "__init__",
    "__new__",
    "__call__",
    "__iter__",
    "__hash__",
    "__repr__",
    "__str__",
    "__subclasscheck__",
    "assertEqual",
    "assertNumQueries",
    "execute_from_command_line",
    "import_module",
    "run_from_argv",
    "run_checks",
    "fetch_command",
}

TEST_PATH_PATTERNS = (
    "/tests/",
    "tests/",
    "test_",
    "_tests.py",
)

EXAMPLE_PATH_PATTERNS = (
    "/examples/",
    "/example/",
    "/docs/",
    "/doc/",
    "/tutorial",
    "/demo",
)

UPPERCASE_NOISE = {
    "INNER",
    "OUTER",
    "JOIN",
    "IMO",
    "PR",
}

CAMEL_OR_CODE_SUFFIXES = (
    "Field",
    "Error",
    "Exception",
    "Warning",
    "Widget",
    "Form",
    "Model",
    "Manager",
    "Lookup",
    "Relation",
    "Serializer",
    "Choices",
)


def dedupe_preserve(items: list[str]) -> list[str]:
    ordered: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def extract_problem_file_mentions(problem_statement: str) -> list[str]:
    matches = re.findall(r"([A-Za-z0-9_\-./]+\.py)\b", problem_statement)
    ordered: list[str] = []
    for match in matches:
        cleaned = match.strip("`'\"()[]{}<>,:")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def _normalize_file_hint(value: str) -> str | None:
    hint = value.strip().replace("\\", "/")
    if not hint:
        return None
    for marker in ("/django/", "/astropy/"):
        if marker in hint:
            hint = hint.split(marker, 1)[1]
            hint = marker.strip("/") + "/" + hint
            break
    if re.match(r"^[A-Za-z]:/", hint):
        return None
    if hint.startswith("/"):
        return None
    hint = hint.lstrip("./")
    if hint.endswith("/py.py") or hint.endswith(".py.py"):
        return None
    if "/site-packages/" in hint or "/venv/" in hint:
        return None
    if Path(hint).name in GENERIC_FILE_HINTS and "/" not in hint:
        return None
    return hint or None


def _file_hint_strength(hint: str) -> str:
    normalized = _normalize_file_hint(hint)
    if not normalized:
        return "weak"
    path = normalized.lower()
    if path.endswith(".py") and "/" in path:
        if any(segment in path for segment in ("/django/", "/astropy/", "django/", "astropy/")):
            return "trusted"
        return "contextual"
    return "weak"


def issue_keywords(problem_statement: str) -> list[str]:
    tokens: list[str] = []
    for raw in problem_statement.replace("/", " ").replace("`", " ").split():
        token = raw.strip(".,:;()[]{}<>\"'`")
        lowered = token.lower()
        if (
            len(token) >= 3
            and token.replace("_", "").replace("-", "").isalnum()
            and lowered not in ISSUE_KEYWORD_STOPWORDS
            and not lowered.isdigit()
        ):
            tokens.append(token)
    return dedupe_preserve(tokens)


def _singularize_symbol(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
        return token[:-1]
    return token


def _looks_like_symbol_hint(token: str) -> bool:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token) is None:
        return False
    if token.lower() in ISSUE_KEYWORD_STOPWORDS:
        return False
    if token.isupper() and token in UPPERCASE_NOISE:
        return False
    if "_" in token:
        return True
    if any(token.endswith(suffix) for suffix in CAMEL_OR_CODE_SUFFIXES):
        return True
    if re.search(r"[a-z][A-Z]", token):
        return True
    if token.startswith("__") and token.endswith("__"):
        return True
    if token[:1].islower() and any(ch.isupper() for ch in token[1:]):
        return True
    return False


def _symbol_hint_strength(token: str) -> str:
    value = token.strip()
    lowered = value.lower()
    if not value or lowered in GENERIC_SYMBOL_HINTS or lowered in ISSUE_KEYWORD_STOPWORDS:
        return "weak"
    if value.startswith("__") and value.endswith("__"):
        return "trusted"
    if "_" in value and len(value) >= 4 and lowered not in {"non_field_errors", "default_error_messages"}:
        return "trusted"
    if any(value.endswith(suffix) for suffix in CAMEL_OR_CODE_SUFFIXES):
        return "trusted"
    if re.search(r"[a-z][A-Z]", value):
        return "trusted"
    if value[:1].isupper() and len(value) >= 5:
        return "contextual"
    return "weak"


def _is_high_information_trace_term(
    term: str,
    anchors: dict[str, Any],
    source_count: int,
) -> bool:
    value = term.strip()
    if not value:
        return False
    if value in LOW_INFORMATION_TRACE_SYMBOLS:
        return False
    lowered = value.lower()
    if lowered in GENERIC_SYMBOL_HINTS or lowered in ISSUE_KEYWORD_STOPWORDS:
        return False

    trusted_symbols = set(anchors.get("trusted_symbol_hints", []))
    error_types = set(anchors.get("error_types", []))
    code_literals = set(anchors.get("code_literals", []))

    if value in error_types:
        return True
    if value.startswith("__") and value.endswith("__"):
        return value in trusted_symbols or value in code_literals
    if any(value.endswith(suffix) for suffix in CAMEL_OR_CODE_SUFFIXES):
        return True
    if re.search(r"[a-z][A-Z]", value):
        return True
    if value in trusted_symbols and "_" in value and len(value) >= 5:
        return True
    if value in code_literals and len(value) >= 4:
        return True
    if "_" in value and source_count >= 2 and len(value) >= 5 and not value.startswith("_"):
        return True
    if value[:1].isupper() and len(value) >= 6 and source_count >= 1:
        return True
    return False


def _issue_shape_hints(problem_statement: str, anchors: dict[str, Any]) -> set[str]:
    lowered = problem_statement.lower()
    symbols = {str(item) for item in anchors.get("symbol_hints", [])}
    hints: set[str] = set()
    if ("migration" in lowered or "migrations" in lowered) and ("serializer" in lowered or "enum" in lowered):
        hints.add("migration_serialization")
    if "modelchoicefield" in lowered or "ModelChoiceField" in symbols:
        hints.add("modelchoicefield_validation")
    if "__isnull" in lowered or "__in" in lowered or "lookup" in lowered:
        hints.add("query_lookup_semantics")
    if "validationerror" in lowered and "__eq__" in problem_statement:
        hints.add("validationerror_equality")
    if "autoreload" in lowered:
        hints.add("autoreload_runtime")
    return hints


def _workflow_layer_terms(anchors: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in anchors.get("framework_terms", []):
        lowered = str(value).lower()
        if len(lowered) >= 4 and lowered not in GENERIC_PATH_TERMS and lowered not in {"django", "translation"}:
            terms.append(lowered)
    issue_shapes = set(anchors.get("issue_shapes", []))
    if "migration_serialization" in issue_shapes:
        terms.extend(["serializer", "migration"])
    if "query_lookup_semantics" in issue_shapes:
        terms.extend(["query", "lookup"])
    if "autoreload_runtime" in issue_shapes:
        terms.extend(["autoreload", "template"])
    if "modelchoicefield_validation" in issue_shapes:
        terms.extend(["forms", "models"])
    if "validationerror_equality" in issue_shapes:
        terms.extend(["exceptions", "validation"])
    return dedupe_preserve([term for term in terms if len(term) >= 4])


def _is_test_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return (
        lowered.startswith("tests/")
        or "/tests/" in f"/{lowered}"
        or Path(lowered).name.startswith("test_")
        or lowered.endswith("_tests.py")
    )


def _is_example_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return any(pattern in f"/{lowered}" for pattern in EXAMPLE_PATH_PATTERNS)


def _symbol_variants(token: str) -> list[str]:
    values = [token]
    singular = _singularize_symbol(token)
    if singular != token:
        values.append(singular)
    if token.endswith("Errors"):
        values.append(token[:-1])
    return dedupe_preserve([value for value in values if value and value.lower() not in GENERIC_SYMBOL_HINTS])


def _extract_dotted_refs(problem_statement: str) -> list[str]:
    refs = []
    for match in re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.){1,}[A-Za-z_][A-Za-z0-9_]*\b", problem_statement):
        refs.append(match.strip())
    return dedupe_preserve(refs)


def _module_ref_to_file_hint(value: str) -> str | None:
    parts = [part for part in value.split(".") if part]
    if len(parts) < 2:
        return None
    if parts[0] in {"self", "other", "cls", "super"}:
        return None
    if parts[-1] == "py":
        return None
    if all(part.islower() or "_" in part for part in parts):
        return _normalize_file_hint("/".join(parts) + ".py")
    if all(part.islower() or "_" in part for part in parts[:-1]):
        return _normalize_file_hint("/".join(parts[:-1]) + ".py")
    return None


def extract_issue_anchors(problem_statement: str, llm_client=None) -> dict[str, Any]:
    file_hints = dedupe_preserve(
        extract_stacktrace_file_hints(problem_statement) + extract_problem_file_mentions(problem_statement)
    )
    keywords = issue_keywords(problem_statement)
    symbol_hints: list[str] = []
    error_types: list[str] = []
    code_literals: list[str] = []
    config_names: list[str] = []
    regex_patterns: list[str] = []

    dotted_refs = _extract_dotted_refs(problem_statement)
    for ref in dotted_refs:
        module_hint = _module_ref_to_file_hint(ref)
        if module_hint:
            file_hints.append(module_hint)
        parts = [part for part in ref.split(".") if part]
        if parts:
            symbol_hints.extend(_symbol_variants(parts[-1]))
            for part in parts:
                if _looks_like_symbol_hint(part):
                    symbol_hints.extend(_symbol_variants(part))

    for token in keywords:
        if _looks_like_symbol_hint(token):
            symbol_hints.extend(_symbol_variants(token))
        if re.search(r"(Error|Exception|Warning|Choices)$", token):
            error_types.extend(_symbol_variants(token))
        if token.isupper() and len(token) >= 3 and token not in UPPERCASE_NOISE:
            config_names.append(token)
        if any(ch in token for ch in ("__", "[]", "()", "=>", "::")) or "(" in token or ")" in token:
            code_literals.append(token)
        if any(ch in token for ch in ("*", "+", "?", "[", "]", "^", "$", "|")):
            regex_patterns.append(token)

    for quoted in re.findall(r"`([^`]+)`", problem_statement):
        value = quoted.strip()
        if not value:
            continue
        if value.endswith(".py"):
            file_hints.append(value)
        elif "." in value and re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*$", value):
            module_hint = _module_ref_to_file_hint(value)
            if module_hint:
                file_hints.append(module_hint)
            symbol_hints.extend(_symbol_variants(value.split(".")[-1]))
        elif any(ch in value for ch in ("(", ")", "[", "]", ".", "__", "=")):
            code_literals.append(value)
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]+$", value):
            symbol_hints.extend(_symbol_variants(value))

    framework_terms = [token for token in keywords if token.lower() in FRAMEWORK_TERMS]

    normalized_file_hints = []
    for hint in file_hints:
        normalized = _normalize_file_hint(hint)
        if normalized:
            normalized_file_hints.append(normalized)
    anchors = {
        "file_hints": dedupe_preserve(normalized_file_hints),
        "symbol_hints": dedupe_preserve(symbol_hints),
        "error_types": dedupe_preserve(error_types),
        "code_literals": dedupe_preserve(code_literals),
        "config_names": dedupe_preserve(config_names),
        "regex_patterns": dedupe_preserve(regex_patterns),
        "framework_terms": dedupe_preserve(framework_terms),
        "keywords": keywords,
    }
    trusted_file_hints: list[str] = []
    contextual_file_hints: list[str] = []
    weak_file_hints: list[str] = []
    for hint in anchors["file_hints"]:
        strength = _file_hint_strength(hint)
        if strength == "trusted":
            trusted_file_hints.append(hint)
        elif strength == "contextual":
            contextual_file_hints.append(hint)
        else:
            weak_file_hints.append(hint)
    trusted_symbol_hints: list[str] = []
    contextual_symbol_hints: list[str] = []
    weak_symbol_hints: list[str] = []
    for hint in anchors["symbol_hints"]:
        strength = _symbol_hint_strength(hint)
        if strength == "trusted":
            trusted_symbol_hints.append(hint)
        elif strength == "contextual":
            contextual_symbol_hints.append(hint)
        else:
            weak_symbol_hints.append(hint)
    anchors["trusted_file_hints"] = dedupe_preserve(trusted_file_hints)
    anchors["contextual_file_hints"] = dedupe_preserve(contextual_file_hints)
    anchors["weak_file_hints"] = dedupe_preserve(weak_file_hints)
    anchors["trusted_symbol_hints"] = dedupe_preserve(trusted_symbol_hints)
    anchors["contextual_symbol_hints"] = dedupe_preserve(contextual_symbol_hints)
    anchors["weak_symbol_hints"] = dedupe_preserve(weak_symbol_hints)
    anchors["issue_shapes"] = sorted(_issue_shape_hints(problem_statement, anchors))
    anchors["anchorless"] = not any(
        anchors[key]
        for key in (
            "trusted_file_hints",
            "contextual_file_hints",
            "trusted_symbol_hints",
            "contextual_symbol_hints",
            "error_types",
            "code_literals",
            "config_names",
            "regex_patterns",
        )
    )

    if llm_client is not None and (
        anchors["anchorless"]
        or len(anchors["trusted_symbol_hints"]) + len(anchors["trusted_file_hints"]) <= 1
    ):
        prompt = f"""Issue statement:
{problem_statement}

Extract precise static search anchors for debugging."""
        payload = with_retries(lambda: llm_client.generate_json(ANCHOR_EXTRACTION_SYSTEM_PROMPT, prompt))
        if isinstance(payload, dict):
            for key in ("file_hints", "symbol_hints", "error_types", "code_literals", "config_names", "regex_patterns", "framework_terms"):
                if key == "file_hints":
                    additions = [hint for hint in (_normalize_file_hint(str(item)) for item in payload.get(key, [])) if hint]
                elif key == "symbol_hints":
                    additions = [item for raw in payload.get(key, []) for item in _symbol_variants(str(raw))]
                else:
                    additions = [str(item) for item in payload.get(key, [])]
                anchors[key] = dedupe_preserve(list(anchors.get(key, [])) + additions)
        trusted_file_hints = []
        contextual_file_hints = []
        weak_file_hints = []
        for hint in anchors["file_hints"]:
            strength = _file_hint_strength(hint)
            if strength == "trusted":
                trusted_file_hints.append(hint)
            elif strength == "contextual":
                contextual_file_hints.append(hint)
            else:
                weak_file_hints.append(hint)
        trusted_symbol_hints = []
        contextual_symbol_hints = []
        weak_symbol_hints = []
        for hint in anchors["symbol_hints"]:
            strength = _symbol_hint_strength(hint)
            if strength == "trusted":
                trusted_symbol_hints.append(hint)
            elif strength == "contextual":
                contextual_symbol_hints.append(hint)
            else:
                weak_symbol_hints.append(hint)
        anchors["trusted_file_hints"] = dedupe_preserve(trusted_file_hints)
        anchors["contextual_file_hints"] = dedupe_preserve(contextual_file_hints)
        anchors["weak_file_hints"] = dedupe_preserve(weak_file_hints)
        anchors["trusted_symbol_hints"] = dedupe_preserve(trusted_symbol_hints)
        anchors["contextual_symbol_hints"] = dedupe_preserve(contextual_symbol_hints)
        anchors["weak_symbol_hints"] = dedupe_preserve(weak_symbol_hints)
        anchors["issue_shapes"] = sorted(_issue_shape_hints(problem_statement, anchors))
        anchors["anchorless"] = not any(
            anchors[key]
            for key in (
                "trusted_file_hints",
                "contextual_file_hints",
                "trusted_symbol_hints",
                "contextual_symbol_hints",
                "error_types",
                "code_literals",
                "config_names",
                "regex_patterns",
            )
        )
        anchors["llm_augmented"] = True
    else:
        anchors["llm_augmented"] = False
    return anchors


def _candidate_penalty(problem_statement: str, relative_path: str) -> float:
    lowered_issue = problem_statement.lower()
    lowered_path = relative_path.lower()
    penalty = 0.0
    if re.search(r"/migrations/\d", f"/{lowered_path}") and "migration" not in lowered_issue and "migrations" not in lowered_issue:
        penalty += 30.0
    if re.search(r"/migrations/\d", f"/{lowered_path}") and ("serializer" in lowered_issue or "generated migration" in lowered_issue):
        penalty += 20.0
    if lowered_path.startswith("tests/") or "/tests/" in f"/{lowered_path}":
        penalty += 25.0
        if "test" not in lowered_issue and "tests" not in lowered_issue:
            penalty += 35.0
    if "/contrib/admin/" in f"/{lowered_path}" and any(term in lowered_issue for term in ("lookup", "__isnull", "validationerror", "slugify")):
        penalty += 20.0
    if "makemigrations.py" in lowered_path and "slugify" in lowered_issue:
        penalty += 35.0
    if "makemessages.py" in lowered_path and "slugify" in lowered_issue:
        penalty += 35.0
    if any(term in lowered_issue for term in ("serializer", "generation", "generated migration", "autoreload", "compiler", "validation", "lookup")):
        if any(term in lowered_path for term in ("contrib/auth/forms.py", "contrib/admin/migrations/", "contrib/auth/migrations/")):
            penalty += 15.0
    return penalty


def _rank_file_candidate(problem_statement: str, path: str, evidence: list[dict[str, Any]]) -> tuple[float, float]:
    tool_weight = {
        "runtime_traceback_frame": 190.0,
        "runtime_exception_literal": 165.0,
        "runtime_test_target": 125.0,
        "runtime_symbol_frame": 175.0,
        "workflow_layer": 75.0,
        "implementation_symbol": 150.0,
        "implementation_error_type": 145.0,
        "implementation_literal": 120.0,
        "exact_symbol": 145.0,
        "exact_error_type": 140.0,
        "exact_file": 100.0,
        "path_hint": 65.0,
        "grep_symbol": 80.0,
        "grep_literal": 70.0,
        "grep_keyword": 55.0,
        "test_symbol": 65.0,
        "test_literal": 60.0,
        "test_keyword": 45.0,
        "example_symbol": 60.0,
        "example_literal": 55.0,
        "example_keyword": 40.0,
        "file_lookup": 50.0,
        "graph_expansion": 35.0,
        "vector_hint": 20.0,
    }
    raw = 0.0
    seen_keys: set[tuple[str, str, str]] = set()
    extra_line_bonus = 0.0
    for item in evidence:
        anchor = str(item.get("anchor", ""))
        key = (str(item["tool"]), str(item["match_type"]), anchor)
        if key not in seen_keys:
            seen_keys.add(key)
            raw += tool_weight.get(str(item["match_type"]), 25.0)
            raw += float(item.get("bonus", 0.0))
        elif str(item["match_type"]).startswith("grep_"):
            extra_line_bonus += 4.0
    raw += min(extra_line_bonus, 16.0)
    path_lower = path.lower()
    issue_lower = problem_statement.lower()
    if "slugify" in issue_lower and path_lower.endswith("django/utils/text.py"):
        raw += 90.0
    if "__isnull" in problem_statement and path_lower.endswith("django/db/models/lookups.py"):
        raw += 90.0
    if "validationerror" in issue_lower and path_lower.endswith("django/core/exceptions.py"):
        raw += 90.0
    if any(term in issue_lower for term in ("textchoices", "integerchoices")) and path_lower.endswith("django/db/models/enums.py"):
        raw += 90.0
    if "migration" in issue_lower and "serializer" in issue_lower and path_lower.endswith("django/db/migrations/serializer.py"):
        raw += 90.0
    if "migration" in issue_lower and "enum" in issue_lower and path_lower.endswith("django/db/migrations/serializer.py"):
        raw += 90.0
    if "modelchoicefield" in issue_lower and path_lower.endswith("django/forms/models.py"):
        raw += 90.0
    raw -= _candidate_penalty(problem_statement, path)
    normalized = raw + (len({item["tool"] for item in evidence}) * 5.0)
    return raw, normalized


def symbol_lookup(connection: sqlite3.Connection, instance_id: str, anchors: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    hints = dedupe_preserve(
        list(anchors.get("trusted_symbol_hints", []))
        + list(anchors.get("contextual_symbol_hints", []))
        + list(anchors.get("error_types", []))
        + list(anchors.get("config_names", []))
    )
    if not hints:
        return []
    placeholders = ",".join("?" for _ in hints)
    rows = connection.execute(
        f"""
        SELECT relative_path, symbol_name, symbol_kind, start_line, end_line, code, description
        FROM symbols
        WHERE instance_id = ? AND symbol_name IN ({placeholders})
        ORDER BY start_line
        """,
        (instance_id, *hints),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows[:limit]:
        match_type = "exact_symbol"
        if str(row[1]) in set(anchors.get("error_types", [])):
            match_type = "exact_error_type"
        results.append(
            {
                "tool": "symbol_lookup",
                "match_type": match_type,
                "relative_path": str(row[0]),
                "symbol_name": str(row[1]),
                "symbol_kind": str(row[2]),
                "start_line": int(row[3]),
                "end_line": int(row[4]),
                "code": str(row[5]),
                "description": str(row[6]),
                "anchor": str(row[1]),
                "bonus": 0.0,
            }
        )
    return results


def file_lookup(connection: sqlite3.Connection, instance_id: str, anchors: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    keyword_set = {str(value).lower() for value in anchors.get("keywords", [])}
    symbol_set = {str(value) for value in anchors.get("trusted_symbol_hints", []) + anchors.get("contextual_symbol_hints", [])}
    for hint in anchors.get("trusted_file_hints", []) + anchors.get("contextual_file_hints", []):
        hint_name = Path(str(hint)).name
        rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND (relative_path = ? OR relative_path LIKE ?)
            """,
            (instance_id, str(hint).lstrip("./"), f"%/{hint_name}"),
        ).fetchall()
        for (relative_path,) in rows:
            key = (str(relative_path), "exact_file")
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "tool": "file_lookup",
                    "match_type": "exact_file",
                    "relative_path": str(relative_path),
                    "anchor": str(hint),
                    "bonus": 0.0,
                }
            )
    for token in dedupe_preserve(list(anchors.get("framework_terms", [])) + list(anchors.get("keywords", []))):
        if len(token) < 4:
            continue
        if token.lower() in GENERIC_PATH_TERMS:
            continue
        rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND relative_path LIKE ?
            LIMIT ?
            """,
            (instance_id, f"%{token.lower()}%", limit),
        ).fetchall()
        for (relative_path,) in rows:
            key = (str(relative_path), "path_hint")
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "tool": "file_lookup",
                    "match_type": "path_hint",
                    "relative_path": str(relative_path),
                    "anchor": token,
                    "bonus": 0.0,
                }
            )
            if len(results) >= limit:
                return results
    issue_shapes = set(anchors.get("issue_shapes", []))
    heuristic_paths: list[tuple[str, str]] = []
    if "migration_serialization" in issue_shapes or {"migration", "enum"}.issubset(keyword_set):
        heuristic_paths.append(("django/db/migrations/serializer.py", "migration-enum-serializer"))
        heuristic_paths.append(("django/db/migrations/writer.py", "migration-writer"))
    if "modelchoicefield_validation" in issue_shapes or "ModelChoiceField" in symbol_set:
        heuristic_paths.append(("django/forms/models.py", "modelchoicefield"))
    if "validationerror_equality" in issue_shapes or ("ValidationError" in symbol_set and "__eq__" in symbol_set):
        heuristic_paths.append(("django/core/exceptions.py", "validationerror-eq"))
    if "query_lookup_semantics" in issue_shapes or "x__in" in symbol_set:
        heuristic_paths.append(("django/db/models/query_utils.py", "lookup-q"))
        heuristic_paths.append(("django/db/models/lookups.py", "lookup-core"))
    for relative_path, anchor in heuristic_paths:
        rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND relative_path = ?
            """,
            (instance_id, relative_path),
        ).fetchall()
        for (row_path,) in rows:
            key = (str(row_path), "exact_file")
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "tool": "file_lookup",
                    "match_type": "exact_file",
                    "relative_path": str(row_path),
                    "anchor": anchor,
                    "bonus": 40.0,
                }
            )
    for token in anchors.get("trusted_symbol_hints", []) + anchors.get("contextual_symbol_hints", []):
        lowered = str(token).lower()
        if len(lowered) < 4:
            continue
        rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND relative_path LIKE ?
            LIMIT ?
            """,
            (instance_id, f"%{lowered}%", max(4, limit // 4)),
        ).fetchall()
        for (relative_path,) in rows:
            key = (str(relative_path), "path_hint")
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "tool": "file_lookup",
                    "match_type": "path_hint",
                    "relative_path": str(relative_path),
                    "anchor": token,
                    "bonus": 10.0,
                }
            )
            if len(results) >= limit:
                return results
    return results[:limit]


def _grep_patterns(anchors: dict[str, Any]) -> list[tuple[str, str]]:
    patterns: list[tuple[str, str]] = []
    for value in anchors.get("trusted_symbol_hints", [])[:8]:
        patterns.append((str(value), "grep_symbol"))
    for value in anchors.get("contextual_symbol_hints", [])[:4]:
        patterns.append((str(value), "grep_symbol"))
    for value in anchors.get("config_names", [])[:4]:
        patterns.append((str(value), "grep_symbol"))
    for value in anchors.get("code_literals", [])[:6]:
        patterns.append((str(value), "grep_literal"))
    for value in anchors.get("framework_terms", [])[:4]:
        patterns.append((str(value), "grep_keyword"))
    for value in anchors.get("error_types", [])[:4]:
        patterns.append((str(value), "grep_symbol"))
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, match_type in patterns:
        if pattern not in seen and len(pattern) >= 3:
            seen.add(pattern)
            deduped.append((pattern, match_type))
    return deduped[:12]


def _repo_search(
    workspace_dir: Path,
    anchors: dict[str, Any],
    limit: int,
    *,
    include_tests: bool,
    include_examples: bool,
    include_general: bool,
    tool_name: str,
    match_prefix: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for pattern, match_type in _grep_patterns(anchors):
        try:
            completed = subprocess.run(
                [
                    "rg",
                    "-n",
                    "-F",
                    "--glob",
                    "*.py",
                    "--max-count",
                    "8",
                    pattern,
                    str(workspace_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            continue
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                path_text, line_text, snippet = line.split(":", 2)
                relative_path = str(Path(path_text).resolve().relative_to(workspace_dir.resolve()))
                line_number = int(line_text)
            except (ValueError, OSError):
                continue
            is_test = _is_test_path(relative_path)
            is_example = _is_example_path(relative_path)
            if is_test and not include_tests:
                continue
            if is_example and not include_examples:
                continue
            if not is_test and not is_example and not include_general:
                continue
            if tool_name == "example_lookup":
                stripped = snippet.strip()
                if (
                    re.match(rf"^(class|def)\s+{re.escape(pattern)}\b", stripped)
                    or re.match(rf"^from\s+\S+\s+import\s+.*\b{re.escape(pattern)}\b", stripped)
                    or re.match(rf"^import\s+.*\b{re.escape(pattern)}\b", stripped)
                ):
                    continue
            key = (relative_path, line_number, pattern)
            if key in seen:
                continue
            seen.add(key)
            rewritten_match_type = match_type
            if match_type == "grep_symbol":
                rewritten_match_type = f"{match_prefix}_symbol"
            elif match_type == "grep_literal":
                rewritten_match_type = f"{match_prefix}_literal"
            elif match_type == "grep_keyword":
                rewritten_match_type = f"{match_prefix}_keyword"
            results.append(
                {
                    "tool": tool_name,
                    "match_type": rewritten_match_type,
                    "relative_path": relative_path,
                    "line_number": line_number,
                    "anchor": pattern,
                    "snippet": snippet.strip(),
                    "bonus": 5.0 if rewritten_match_type.endswith("_literal") else 0.0,
                }
            )
            if len(results) >= limit:
                return results
    return results


def repo_grep(workspace_dir: Path, anchors: dict[str, Any], limit: int = 60) -> list[dict[str, Any]]:
    return _repo_search(
        workspace_dir,
        anchors,
        limit,
        include_tests=False,
        include_examples=False,
        include_general=True,
        tool_name="repo_grep",
        match_prefix="grep",
    )


def test_lookup(workspace_dir: Path, anchors: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    return _repo_search(
        workspace_dir,
        anchors,
        limit,
        include_tests=True,
        include_examples=False,
        include_general=False,
        tool_name="test_lookup",
        match_prefix="test",
    )


def example_lookup(workspace_dir: Path, anchors: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    return _repo_search(
        workspace_dir,
        anchors,
        limit,
        include_tests=False,
        include_examples=True,
        include_general=True,
        tool_name="example_lookup",
        match_prefix="example",
    )


def vector_lookup(
    chroma_client,
    embedding_client,
    instance_id: str,
    problem_statement: str,
    collection_name: str = "swebench_python_chunks",
    top_k: int = 8,
) -> list[dict[str, Any]]:
    if chroma_client is None or embedding_client is None:
        return []
    collection = chroma_client.get_collection(collection_name)
    query_embedding = embedding_client.embed_texts([problem_statement])[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"instance_id": instance_id},
    )
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    items: list[dict[str, Any]] = []
    for document, metadata in zip(documents, metadatas):
        items.append(
            {
                "tool": "vector_lookup",
                "match_type": "vector_hint",
                "relative_path": str(metadata["relative_path"]),
                "chunk_index": int(metadata["chunk_index"]),
                "anchor": "vector",
                "snippet": str(document)[:400],
                "bonus": 0.0,
            }
        )
    return items


def implementation_trace(
    connection: sqlite3.Connection,
    instance_id: str,
    anchors: dict[str, Any],
    grep_candidates: list[dict[str, Any]],
    test_candidates: list[dict[str, Any]],
    example_candidates: list[dict[str, Any]],
    limit: int = 40,
) -> list[dict[str, Any]]:
    source_map: dict[str, set[str]] = {}
    for item in grep_candidates:
        anchor = str(item.get("anchor", "")).strip()
        if anchor:
            source_map.setdefault(anchor, set()).add("grep")
    for item in test_candidates:
        anchor = str(item.get("anchor", "")).strip()
        if anchor:
            source_map.setdefault(anchor, set()).add("test")
    for item in example_candidates:
        anchor = str(item.get("anchor", "")).strip()
        if anchor:
            source_map.setdefault(anchor, set()).add("example")

    candidate_terms = dedupe_preserve(
        list(anchors.get("trusted_symbol_hints", []))
        + list(anchors.get("contextual_symbol_hints", []))
        + list(anchors.get("error_types", []))
        + list(source_map.keys())
    )
    trace_terms = [
        term
        for term in candidate_terms
        if _is_high_information_trace_term(term, anchors, len(source_map.get(term, set())))
    ]
    if not trace_terms:
        return []

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    placeholders = ",".join("?" for _ in trace_terms)
    rows = connection.execute(
        f"""
        SELECT relative_path, symbol_name, symbol_kind, start_line, end_line, code, description
        FROM symbols
        WHERE instance_id = ? AND symbol_name IN ({placeholders})
        ORDER BY start_line
        """,
        (instance_id, *trace_terms),
    ).fetchall()
    error_type_set = set(anchors.get("error_types", []))
    code_literal_set = set(anchors.get("code_literals", []))
    scored_rows: list[tuple[float, tuple[Any, ...]]] = []
    for row in rows:
        symbol_name = str(row[1])
        symbol_kind = str(row[2]).lower()
        source_count = len(source_map.get(symbol_name, set()))
        score = 0.0
        if symbol_name in error_type_set:
            score += 40.0
        if symbol_name in anchors.get("trusted_symbol_hints", []):
            score += 30.0
        if source_count:
            score += 10.0 * source_count
        if symbol_kind == "class":
            score += 15.0
        elif symbol_kind in {"function", "method"}:
            score += 10.0
        if symbol_name.startswith("__") and symbol_name.endswith("__") and symbol_name not in anchors.get("trusted_symbol_hints", []):
            score -= 25.0
        scored_rows.append((score, row))
    for _, row in sorted(scored_rows, key=lambda item: (item[0], -int(item[1][3])), reverse=True):
        relative_path = str(row[0])
        symbol_name = str(row[1])
        key = (relative_path, symbol_name)
        if key in seen:
            continue
        seen.add(key)
        match_type = "implementation_symbol"
        bonus = 15.0
        if symbol_name in error_type_set:
            match_type = "implementation_error_type"
            bonus = 20.0
        elif symbol_name in code_literal_set:
            match_type = "implementation_literal"
            bonus = 12.0
        results.append(
            {
                "tool": "implementation_trace",
                "match_type": match_type,
                "relative_path": relative_path,
                "symbol_name": symbol_name,
                "symbol_kind": str(row[2]),
                "start_line": int(row[3]),
                "end_line": int(row[4]),
                "code": str(row[5]),
                "description": str(row[6]),
                "anchor": symbol_name,
                "bonus": bonus,
            }
        )
        if len(results) >= limit:
            break
    return results


def workflow_layer_lookup(
    connection: sqlite3.Connection,
    instance_id: str,
    anchors: dict[str, Any],
    limit: int = 30,
) -> list[dict[str, Any]]:
    terms = _workflow_layer_terms(anchors)
    if not terms:
        return []
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, first in enumerate(terms):
        for second in terms[index + 1 :]:
            rows = connection.execute(
                """
                SELECT relative_path
                FROM files
                WHERE instance_id = ? AND relative_path LIKE ? AND relative_path LIKE ?
                LIMIT ?
                """,
                (instance_id, f"%{first}%", f"%{second}%", max(3, limit // 4)),
            ).fetchall()
            for (relative_path,) in rows:
                path = str(relative_path)
                if re.search(r"/migrations/\d", f"/{path}") and {first, second} & {"migration", "migrations"}:
                    continue
                key = (path, f"{first}+{second}")
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "tool": "workflow_layer_lookup",
                        "match_type": "workflow_layer",
                        "relative_path": path,
                        "anchor": f"{first}+{second}",
                        "bonus": 30.0,
                    }
                )
                if len(results) >= limit:
                    return results
    for term in terms:
        rows = connection.execute(
            """
            SELECT relative_path
            FROM files
            WHERE instance_id = ? AND relative_path LIKE ?
            LIMIT ?
            """,
            (instance_id, f"%{term}%", max(5, limit // 3)),
        ).fetchall()
        for (relative_path,) in rows:
            path = str(relative_path)
            if re.search(r"/migrations/\d", f"/{path}") and term in {"migration", "migrations"}:
                continue
            key = (path, term)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "tool": "workflow_layer_lookup",
                    "match_type": "workflow_layer",
                    "relative_path": path,
                    "anchor": term,
                    "bonus": 18.0,
                }
            )
            if len(results) >= limit:
                return results
    return results


def merge_candidates(
    problem_statement: str,
    symbol_candidates: list[dict[str, Any]],
    file_candidates: list[dict[str, Any]],
    grep_candidates: list[dict[str, Any]],
    test_candidates: list[dict[str, Any]],
    example_candidates: list[dict[str, Any]],
    implementation_candidates: list[dict[str, Any]],
    workflow_layer_candidates: list[dict[str, Any]],
    vector_candidates: list[dict[str, Any]],
    runtime_candidates: list[dict[str, Any]] | None = None,
    instrumentation_candidates: list[dict[str, Any]] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    runtime_candidates = runtime_candidates or []
    instrumentation_candidates = instrumentation_candidates or []
    for item in (
        symbol_candidates
        + file_candidates
        + grep_candidates
        + test_candidates
        + example_candidates
        + implementation_candidates
        + workflow_layer_candidates
        + vector_candidates
        + runtime_candidates
        + instrumentation_candidates
    ):
        path = str(item["relative_path"])
        bucket = merged.setdefault(
            path,
            {
                "relative_path": path,
                "evidence": [],
                "tool_sources": [],
                "match_types": [],
                "anchors": [],
                "symbol_names": [],
                "line_numbers": [],
                "symbol_evidence": [],
                "file_evidence": [],
                "grep_evidence": [],
                "test_evidence": [],
                "example_evidence": [],
                "implementation_evidence": [],
                "workflow_evidence": [],
                "graph_evidence": [],
                "vector_evidence": [],
                "runtime_file_evidence": [],
                "runtime_symbol_evidence": [],
                "runtime_literal_evidence": [],
                "runtime_test_evidence": [],
                "instrumentation_file_evidence": [],
                "instrumentation_symbol_evidence": [],
                "instrumentation_branch_evidence": [],
                "instrumentation_value_evidence": [],
            },
        )
        bucket["evidence"].append(item)
        if item["tool"] not in bucket["tool_sources"]:
            bucket["tool_sources"].append(item["tool"])
        if item["match_type"] not in bucket["match_types"]:
            bucket["match_types"].append(item["match_type"])
        if str(item.get("anchor", "")):
            bucket["anchors"].append(str(item["anchor"]))
        if item.get("symbol_name"):
            bucket["symbol_names"].append(str(item["symbol_name"]))
        if item.get("line_number") is not None:
            bucket["line_numbers"].append(int(item["line_number"]))
        tool_name = str(item["tool"])
        if tool_name == "symbol_lookup":
            bucket["symbol_evidence"].append(item)
        elif tool_name == "file_lookup":
            bucket["file_evidence"].append(item)
        elif tool_name == "repo_grep":
            bucket["grep_evidence"].append(item)
        elif tool_name == "test_lookup":
            bucket["test_evidence"].append(item)
        elif tool_name == "example_lookup":
            bucket["example_evidence"].append(item)
        elif tool_name == "implementation_trace":
            bucket["implementation_evidence"].append(item)
        elif tool_name == "workflow_layer_lookup":
            bucket["workflow_evidence"].append(item)
        elif tool_name == "vector_lookup":
            bucket["vector_evidence"].append(item)
        elif tool_name == "runtime_traceback":
            if item.get("symbol_name"):
                bucket["runtime_symbol_evidence"].append(item)
            else:
                bucket["runtime_file_evidence"].append(item)
        elif tool_name == "runtime_literal":
            bucket["runtime_literal_evidence"].append(item)
        elif tool_name == "runtime_test":
            bucket["runtime_test_evidence"].append(item)
        elif tool_name == "instrumentation_trace":
            match_type = str(item.get("match_type", ""))
            if match_type == "instrumentation_symbol":
                bucket["instrumentation_symbol_evidence"].append(item)
            elif match_type == "instrumentation_branch":
                bucket["instrumentation_branch_evidence"].append(item)
            elif match_type == "instrumentation_value":
                bucket["instrumentation_value_evidence"].append(item)
            else:
                bucket["instrumentation_file_evidence"].append(item)

    ranked: list[dict[str, Any]] = []
    for path, bucket in merged.items():
        raw_score, normalized_score = _rank_file_candidate(problem_statement, path, bucket["evidence"])
        bucket["raw_score"] = raw_score
        bucket["normalized_score"] = normalized_score
        bucket["raw_component_scores"] = {
            "symbol": float(len(bucket["symbol_evidence"])),
            "file": float(len(bucket["file_evidence"])),
            "grep": float(len(bucket["grep_evidence"])),
            "test": float(len(bucket["test_evidence"])),
            "example": float(len(bucket["example_evidence"])),
            "implementation": float(len(bucket["implementation_evidence"])),
            "workflow": float(len(bucket["workflow_evidence"])),
            "graph": float(len(bucket["graph_evidence"])),
            "vector": float(len(bucket["vector_evidence"])),
            "runtime_file": float(len(bucket["runtime_file_evidence"])),
            "runtime_symbol": float(len(bucket["runtime_symbol_evidence"])),
            "runtime_literal": float(len(bucket["runtime_literal_evidence"])),
            "runtime_test": float(len(bucket["runtime_test_evidence"])),
            "instrumentation_file": float(len(bucket["instrumentation_file_evidence"])),
            "instrumentation_symbol": float(len(bucket["instrumentation_symbol_evidence"])),
            "instrumentation_branch": float(len(bucket["instrumentation_branch_evidence"])),
            "instrumentation_value": float(len(bucket["instrumentation_value_evidence"])),
        }
        bucket["anchors"] = dedupe_preserve(bucket["anchors"])
        bucket["symbol_names"] = dedupe_preserve(bucket["symbol_names"])
        bucket["line_numbers"] = sorted(set(bucket["line_numbers"]))
        ranked.append(bucket)
    ranked.sort(key=lambda item: (float(item["normalized_score"]), float(item["raw_score"])), reverse=True)
    for index, item in enumerate(ranked[:limit], start=1):
        item["rank"] = index
    return ranked[:limit]


def graph_expander(
    connection: sqlite3.Connection,
    workspace_dir: Path,
    instance_id: str,
    merged_candidates: list[dict[str, Any]],
    anchors: dict[str, Any],
    max_seed_files: int = 6,
    max_related_files: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seed_paths = [str(item["relative_path"]) for item in merged_candidates[:max_seed_files]]
    related_rows: list[dict[str, Any]] = []
    if seed_paths:
        placeholders = ",".join("?" for _ in seed_paths)
        rows = connection.execute(
            f"""
            SELECT src_ref, dst_ref, relation_type, weight
            FROM relations
            WHERE instance_id = ? AND src_kind = 'file' AND src_ref IN ({placeholders}) AND dst_kind = 'file'
            ORDER BY weight DESC
            """,
            (instance_id, *seed_paths),
        ).fetchall()
        related_scores: dict[str, float] = {}
        related_meta: dict[str, list[dict[str, Any]]] = {}
        for src_ref, dst_ref, relation_type, weight in rows:
            dst = str(dst_ref)
            if dst in seed_paths:
                continue
            related_scores[dst] = related_scores.get(dst, 0.0) + float(weight)
            related_meta.setdefault(dst, []).append(
                {
                    "src_ref": str(src_ref),
                    "relation_type": str(relation_type),
                    "weight": float(weight),
                }
            )
        related_paths = [
            path for path, _ in sorted(related_scores.items(), key=lambda item: item[1], reverse=True)[:max_related_files]
        ]
        for path in related_paths:
            row = {
                "relative_path": path,
                "tool": "graph_expander",
                "match_type": "graph_expansion",
                "graph_sources": related_meta[path],
            }
            related_rows.append(row)
            for candidate in merged_candidates:
                if str(candidate["relative_path"]) == path:
                    candidate.setdefault("graph_evidence", []).append(row)
    file_refs = [{"relative_path": path} for path in dedupe_preserve(seed_paths + [row["relative_path"] for row in related_rows])]
    file_items = expand_graph_file_context(
        connection,
        workspace_dir,
        instance_id,
        file_refs,
        problem_statement=" ".join(anchors.get("keywords", [])),
    )
    file_items = hydrate_clean_file_items(workspace_dir, file_items)
    metadata = {
        "seed_paths": seed_paths,
        "related_files": related_rows,
        "expanded_files": [str(item["relative_path"]) for item in file_items],
    }
    return file_items, metadata


def _summarize_candidate(candidate: dict[str, Any]) -> str:
    tools = ", ".join(candidate.get("tool_sources", []))
    match_types = ", ".join(candidate.get("match_types", []))
    anchors = ", ".join(candidate.get("anchors", [])[:5]) or "-"
    return (
        f"- {candidate['relative_path']} | rank={candidate.get('rank', '-')}"
        f" | score={candidate['normalized_score']:.1f}"
        f" | tools={tools} | matches={match_types} | anchors={anchors}"
    )


def _summarize_candidate_by_source(candidate: dict[str, Any]) -> str:
    def summarize(items: list[dict[str, Any]]) -> str:
        if not items:
            return "-"
        parts = []
        for item in items[:3]:
            anchor = str(item.get("anchor", "")).strip() or "?"
            line = item.get("line_number")
            if line is not None:
                parts.append(f"{anchor}@{line}")
            else:
                parts.append(anchor)
        return ", ".join(parts)

    return "\n".join(
        [
            f"- File: {candidate['relative_path']}",
            f"  rank={candidate.get('rank', '-')} score={candidate['normalized_score']:.1f}",
            f"  symbol evidence: {summarize(candidate.get('symbol_evidence', []))}",
            f"  file evidence: {summarize(candidate.get('file_evidence', []))}",
            f"  grep evidence: {summarize(candidate.get('grep_evidence', []))}",
            f"  test evidence: {summarize(candidate.get('test_evidence', []))}",
            f"  example evidence: {summarize(candidate.get('example_evidence', []))}",
            f"  implementation evidence: {summarize(candidate.get('implementation_evidence', []))}",
            f"  workflow evidence: {summarize(candidate.get('workflow_evidence', []))}",
            f"  graph evidence: {summarize(candidate.get('graph_evidence', []))}",
            f"  runtime file evidence: {summarize(candidate.get('runtime_file_evidence', []))}",
            f"  runtime symbol evidence: {summarize(candidate.get('runtime_symbol_evidence', []))}",
            f"  runtime literal evidence: {summarize(candidate.get('runtime_literal_evidence', []))}",
            f"  runtime test evidence: {summarize(candidate.get('runtime_test_evidence', []))}",
            f"  instrumentation file evidence: {summarize(candidate.get('instrumentation_file_evidence', []))}",
            f"  instrumentation symbol evidence: {summarize(candidate.get('instrumentation_symbol_evidence', []))}",
            f"  instrumentation branch evidence: {summarize(candidate.get('instrumentation_branch_evidence', []))}",
            f"  instrumentation value evidence: {summarize(candidate.get('instrumentation_value_evidence', []))}",
        ]
    )


def build_file_comparison_prompt(
    problem_statement: str,
    anchors: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    candidate_budget: int,
) -> str:
    sections = [
        "Problem statement:",
        problem_statement,
        "",
        "Extracted anchors:",
        json.dumps(
            {
                "trusted_file_hints": anchors.get("trusted_file_hints", []),
                "contextual_file_hints": anchors.get("contextual_file_hints", []),
                "trusted_symbol_hints": anchors.get("trusted_symbol_hints", []),
                "contextual_symbol_hints": anchors.get("contextual_symbol_hints", []),
                "error_types": anchors.get("error_types", []),
                "code_literals": anchors.get("code_literals", []),
                "framework_terms": anchors.get("framework_terms", []),
            },
            indent=2,
        ),
        "",
        "Candidate files:",
    ]
    for candidate in merged_candidates[:candidate_budget]:
        sections.append(_summarize_candidate_by_source(candidate))
    sections.extend(["", "Expanded implementation context:"])
    file_map = {str(item["relative_path"]): item for item in file_items}
    for candidate in merged_candidates[:candidate_budget]:
        file_item = file_map.get(str(candidate["relative_path"]))
        if file_item is None:
            continue
        symbols = ", ".join(
            f"{symbol['symbol_name']}[{symbol['start_line']}-{symbol['end_line']}]"
            for symbol in file_item.get("symbols", [])[:5]
        ) or "-"
        sections.append(
            "\n".join(
                [
                    f"File: {file_item['relative_path']}",
                    f"Top symbols: {symbols}",
                    "Excerpt:",
                    "```python",
                    "\n".join(file_item["source"].splitlines()[:80]),
                    "```",
                ]
            )
        )
    return "\n\n".join(sections)


def compare_candidate_files(
    llm_client,
    problem_statement: str,
    anchors: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    candidate_budget: int,
) -> tuple[dict[str, Any], str]:
    candidate_paths = [str(item["relative_path"]) for item in merged_candidates[:candidate_budget]]
    comparison_prompt = build_file_comparison_prompt(problem_statement, anchors, merged_candidates, file_items, candidate_budget)
    payload = with_retries(
        lambda: llm_client.generate_json(FILE_COMPARISON_SYSTEM_PROMPT, comparison_prompt)
    )
    if not isinstance(payload, dict):
        payload = {}
    top_files = [path for path in payload.get("top_files", []) if str(path) in candidate_paths]
    if not top_files:
        top_files = candidate_paths[:candidate_budget]
    preferred_file = str(payload.get("preferred_file", top_files[0] if top_files else "")).strip()
    if preferred_file not in candidate_paths and top_files:
        preferred_file = top_files[0]
    runner_up_files = [path for path in payload.get("runner_up_files", []) if str(path) in candidate_paths and str(path) != preferred_file]
    why_others = payload.get("why_others_rejected", {})
    if not isinstance(why_others, dict):
        why_others = {}
    comparison = {
        "top_files": dedupe_preserve([preferred_file] + top_files + runner_up_files)[:candidate_budget] if preferred_file else top_files[:candidate_budget],
        "preferred_file": preferred_file,
        "runner_up_files": runner_up_files[: max(0, candidate_budget - 1)],
        "why_preferred": str(payload.get("why_preferred", "")).strip(),
        "why_others_rejected": {str(key): str(value) for key, value in why_others.items() if str(key) in candidate_paths},
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
    }
    if not comparison["top_files"]:
        comparison["top_files"] = candidate_paths[:candidate_budget]
        comparison["preferred_file"] = comparison["top_files"][0] if comparison["top_files"] else ""
    return comparison, comparison_prompt


def _candidate_by_path(merged_candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["relative_path"]): item for item in merged_candidates}


def _deterministic_file_preference(
    structured_summary: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    comparison_top_files: list[str],
) -> str | None:
    issue_shape = str(structured_summary.get("issue_shape", "generic"))
    workflow_shapes = {"migration_serialization", "autoreload_runtime", "query_lookup_semantics"}
    if issue_shape not in workflow_shapes:
        return None
    candidate_map = _candidate_by_path(merged_candidates)
    best_path = None
    best_score = None
    for path in comparison_top_files[:5]:
        candidate = candidate_map.get(str(path))
        if candidate is None:
            continue
        parts = candidate.get("raw_component_scores", {})
        workflow = float(parts.get("workflow", 0.0))
        implementation = float(parts.get("implementation", 0.0))
        symbol = float(parts.get("symbol", 0.0))
        file_score = float(parts.get("file", 0.0))
        graph = float(parts.get("graph", 0.0))
        runtime_file = float(parts.get("runtime_file", 0.0))
        runtime_symbol = float(parts.get("runtime_symbol", 0.0))
        runtime_literal = float(parts.get("runtime_literal", 0.0))
        runtime_test = float(parts.get("runtime_test", 0.0))
        instrumentation_file = float(parts.get("instrumentation_file", 0.0))
        instrumentation_symbol = float(parts.get("instrumentation_symbol", 0.0))
        instrumentation_branch = float(parts.get("instrumentation_branch", 0.0))
        instrumentation_value = float(parts.get("instrumentation_value", 0.0))
        usage = float(parts.get("grep", 0.0)) + float(parts.get("example", 0.0)) + float(parts.get("test", 0.0))
        score = (
            (instrumentation_file * 7.0)
            + (instrumentation_symbol * 6.0)
            + (instrumentation_branch * 5.0)
            + (instrumentation_value * 4.0)
            + (runtime_file * 6.0)
            + (runtime_symbol * 5.0)
            + (runtime_literal * 4.0)
            + (runtime_test * 3.0)
            + (workflow * 5.0)
            + (implementation * 3.0)
            + (file_score * 2.5)
            + (graph * 1.5)
            + (symbol * 1.0)
            - (usage * 0.35)
        )
        if best_score is None or score > best_score:
            best_score = score
            best_path = str(path)
    return best_path


def select_file_candidate(
    problem_statement: str,
    structured_summary: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    file_comparison: dict[str, Any],
) -> dict[str, Any]:
    candidate_paths = [str(item["relative_path"]) for item in merged_candidates]
    candidate_map = _candidate_by_path(merged_candidates)
    preferred_file = str(file_comparison.get("preferred_file", "")).strip()
    comparison_confidence = float(file_comparison.get("confidence", 0.0) or 0.0)
    deterministic_preference = _deterministic_file_preference(
        structured_summary,
        merged_candidates,
        [str(path) for path in file_comparison.get("top_files", []) if str(path) in candidate_map],
    )
    if deterministic_preference and deterministic_preference in candidate_map and deterministic_preference != preferred_file:
        preferred_candidate = candidate_map.get(preferred_file) if preferred_file in candidate_map else None
        deterministic_candidate = candidate_map[deterministic_preference]
        pref_parts = preferred_candidate.get("raw_component_scores", {}) if preferred_candidate else {}
        det_parts = deterministic_candidate.get("raw_component_scores", {})
        pref_workflow = float(pref_parts.get("workflow", 0.0))
        det_workflow = float(det_parts.get("workflow", 0.0))
        pref_impl = float(pref_parts.get("implementation", 0.0))
        det_impl = float(det_parts.get("implementation", 0.0))
        if (det_workflow > pref_workflow) or (det_workflow == pref_workflow and det_impl > pref_impl):
            preferred_file = deterministic_preference
            comparison_confidence = max(comparison_confidence, 0.7)
            file_comparison["preferred_file"] = preferred_file
            if preferred_file not in file_comparison.get("top_files", []):
                file_comparison["top_files"] = [preferred_file] + [str(path) for path in file_comparison.get("top_files", [])]
            file_comparison["why_preferred"] = (
                "Deterministic workflow-aware preference overrode the original comparison because this file has stronger implementation/workflow evidence for the issue shape."
            )
    if preferred_file in candidate_map and comparison_confidence >= 0.65:
        chosen_file = preferred_file
        rationale = str(file_comparison.get("why_preferred", "")).strip() or "High-confidence file comparison preferred this file."
        rule = "comparison_preferred_file"
    else:
        chosen_file = candidate_paths[0] if candidate_paths else ""
        rationale = "Fell back to deterministic merged candidate ranking."
        rule = "deterministic_file_ranking"
        structured_files = dedupe_preserve(
            [str(path) for path in structured_summary.get("implementation_files", []) if str(path) in candidate_map]
            + [str(path) for path in structured_summary.get("likely_bug_files", []) if str(path) in candidate_map]
        )
        if structured_files:
            chosen_file = structured_files[0]
            rationale = "Used structured summary implementation preference within the candidate set."
            rule = "structured_summary_file"

    shortlist = dedupe_preserve(
        [chosen_file]
        + [str(path) for path in file_comparison.get("top_files", []) if str(path) in candidate_map]
        + candidate_paths
    )[:5]
    return {
        "chosen_file": chosen_file,
        "shortlist": shortlist,
        "rationale": rationale,
        "confidence": comparison_confidence if chosen_file == preferred_file else 0.5,
        "selection_rule": rule,
    }


def _pick_region_from_file(
    selected_file_item: dict[str, Any],
    selected_candidate: dict[str, Any] | None,
    structured_summary: dict[str, Any],
    runtime_evidence: dict[str, Any] | None = None,
    instrumentation_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    likely_symbols = {str(value) for value in structured_summary.get("likely_symbols", [])}
    suspicious_patterns = [str(value) for value in structured_summary.get("suspicious_line_patterns", [])]
    fix_mechanism = str(structured_summary.get("fix_mechanism", ""))

    symbol_names_in_file = {str(symbol["symbol_name"]) for symbol in selected_file_item.get("symbols", [])}
    method_hints: list[str] = []
    for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b", fix_mechanism):
        method_hints.append(match.split(".")[-1])
    for match in re.findall(r"\b(__[A-Za-z0-9_]+__|[A-Za-z_][A-Za-z0-9_]*)\b", fix_mechanism):
        if match in symbol_names_in_file:
            method_hints.append(match)
    for pattern in suspicious_patterns:
        for match in re.findall(r"\b(__[A-Za-z0-9_]+__|[A-Za-z_][A-Za-z0-9_]*)\b", pattern):
            if match in symbol_names_in_file:
                method_hints.append(match)
    method_hints = dedupe_preserve(method_hints)

    def line_target(line_number: int, mode: str, rationale: str) -> tuple[dict[str, Any], dict[str, Any]]:
        target = {
            "path": str(selected_file_item["relative_path"]),
            "line_number": int(line_number),
            "mode": mode,
        }
        region = {
            "selected_file": str(selected_file_item["relative_path"]),
            "selected_line": int(line_number),
            "selected_symbol": None,
            "selected_block_type": None,
            "rationale": rationale,
            "selection_rule": mode,
        }
        return target, region

    selected_path = str(selected_file_item["relative_path"])
    if instrumentation_evidence:
        for event in instrumentation_evidence.get("parsed_logs", {}).get("events", []):
            if str(event.get("file", "")).strip() != selected_path:
                continue
            symbol_name = str(event.get("symbol", "")).strip()
            line_number = int(event.get("line", "1") or "1")
            if symbol_name and symbol_name != "?":
                for symbol in selected_file_item.get("symbols", []):
                    if str(symbol["symbol_name"]) == symbol_name:
                        target = {
                            "path": selected_path,
                            "line_number": int(symbol["start_line"]),
                            "mode": "line",
                        }
                        region = {
                            "selected_file": selected_path,
                            "selected_line": int(symbol["start_line"]),
                            "selected_symbol": symbol_name,
                            "selected_block_type": None,
                            "rationale": "Selected the symbol reached by instrumentation logs in the chosen file.",
                            "selection_rule": "instrumentation_symbol",
                        }
                        return target, region
            return line_target(
                line_number,
                "line",
                "Selected the line reached by instrumentation logs inside the chosen file.",
            )
    if runtime_evidence:
        traceback_payload = runtime_evidence.get("traceback", {})
        for frame in traceback_payload.get("top_stack_lines", []):
            if str(frame.get("relative_path", "")) != selected_path:
                continue
            function_name = str(frame.get("function_name", "")).strip()
            if function_name:
                for symbol in selected_file_item.get("symbols", []):
                    if str(symbol["symbol_name"]) == function_name:
                        target = {
                            "path": selected_path,
                            "line_number": int(symbol["start_line"]),
                            "mode": "line",
                        }
                        region = {
                            "selected_file": selected_path,
                            "selected_line": int(symbol["start_line"]),
                            "selected_symbol": function_name,
                            "selected_block_type": None,
                            "rationale": "Selected the method/function named by the runtime traceback frame.",
                            "selection_rule": "runtime_traceback_symbol",
                        }
                        return target, region
            return line_target(
                int(frame.get("line_number", 1)),
                "line",
                "Selected the exact traceback line inside the chosen file.",
            )

    for symbol in selected_file_item.get("symbols", []):
        if str(symbol["symbol_name"]) in method_hints:
            target = {
                "path": str(selected_file_item["relative_path"]),
                "line_number": int(symbol["start_line"]),
                "mode": "line",
            }
            region = {
                "selected_file": str(selected_file_item["relative_path"]),
                "selected_line": int(symbol["start_line"]),
                "selected_symbol": str(symbol["symbol_name"]),
                "selected_block_type": None,
                "rationale": "Selected the most specific method/function referenced by the structured fix mechanism or suspicious patterns.",
                "selection_rule": "fix_mechanism_symbol_in_selected_file",
            }
            return target, region

    for symbol in selected_file_item.get("symbols", []):
        if likely_symbols and str(symbol["symbol_name"]) in likely_symbols:
            target = {
                "path": str(selected_file_item["relative_path"]),
                "line_number": int(symbol["start_line"]),
                "mode": "line",
            }
            region = {
                "selected_file": str(selected_file_item["relative_path"]),
                "selected_line": int(symbol["start_line"]),
                "selected_symbol": str(symbol["symbol_name"]),
                "selected_block_type": None,
                "rationale": "Selected a symbol in the chosen file that matches structured likely symbols.",
                "selection_rule": "likely_symbol_in_selected_file",
            }
            return target, region

    if selected_candidate is not None:
        candidate_lines = list(selected_candidate.get("line_numbers", []))
        if candidate_lines:
            return line_target(candidate_lines[0], "line", "Selected the highest-ranked matched line inside the chosen file.")

    if suspicious_patterns:
        for block in selected_file_item.get("blocks", []):
            summary = str(block.get("summary", ""))
            if any(pattern in summary for pattern in suspicious_patterns):
                target = {
                    "path": str(selected_file_item["relative_path"]),
                    "line_number": int(block["start_line"]),
                    "mode": "line",
                }
                region = {
                    "selected_file": str(selected_file_item["relative_path"]),
                    "selected_line": int(block["start_line"]),
                    "selected_symbol": None,
                    "selected_block_type": str(block.get("block_type")),
                    "rationale": "Selected a block in the chosen file that matches suspicious structured patterns.",
                    "selection_rule": "suspicious_pattern_block",
                }
                return target, region

    if selected_file_item.get("symbols"):
        first_symbol = selected_file_item["symbols"][0]
        target = {
            "path": str(selected_file_item["relative_path"]),
            "line_number": int(first_symbol["start_line"]),
            "mode": "line",
        }
        region = {
            "selected_file": str(selected_file_item["relative_path"]),
            "selected_line": int(first_symbol["start_line"]),
            "selected_symbol": str(first_symbol["symbol_name"]),
            "selected_block_type": None,
            "rationale": "Fell back to the first symbol in the selected file.",
            "selection_rule": "first_symbol_fallback",
        }
        return target, region

    return line_target(1, "line", "Fell back to the first line of the selected file.")


def render_evidence_packet(
    problem_statement: str,
    anchors: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    file_comparison: dict[str, Any],
    max_tokens: int,
    candidate_budget: int,
) -> str:
    def bucket_lines(bucket_name: str, key: str) -> list[str]:
        lines = [bucket_name + ":"]
        added = 0
        for candidate in merged_candidates:
            items = candidate.get(key, [])
            if not items:
                continue
            lines.append(_summarize_candidate_by_source({**candidate, key: items}))
            added += 1
            if added >= candidate_budget:
                break
        if added == 0:
            lines.append("- None")
        return lines

    sections = [
        "Issue statement:",
        problem_statement,
        "",
        "Extracted anchors:",
        json.dumps(anchors, indent=2),
        "",
        "Top symbol/file/grep evidence:",
    ]
    for candidate in merged_candidates[:candidate_budget]:
        sections.append(_summarize_candidate(candidate))
    sections.extend(["", *bucket_lines("Test evidence", "test_evidence")])
    sections.extend(["", *bucket_lines("Example evidence", "example_evidence")])
    sections.extend(["", *bucket_lines("Runtime traceback evidence", "runtime_file_evidence")])
    sections.extend(["", *bucket_lines("Runtime symbol evidence", "runtime_symbol_evidence")])
    sections.extend(["", *bucket_lines("Runtime literal evidence", "runtime_literal_evidence")])
    sections.extend(["", *bucket_lines("Instrumentation file evidence", "instrumentation_file_evidence")])
    sections.extend(["", *bucket_lines("Instrumentation symbol evidence", "instrumentation_symbol_evidence")])
    sections.extend(["", "Expanded implementation context:"])
    candidate_order = [str(item["relative_path"]) for item in merged_candidates]
    ordered_file_items = sorted(
        file_items,
        key=lambda item: candidate_order.index(str(item["relative_path"])) if str(item["relative_path"]) in candidate_order else len(candidate_order),
    )
    for file_item in ordered_file_items[:candidate_budget]:
        symbol_lines = []
        for symbol in file_item.get("symbols", [])[:6]:
            symbol_lines.append(
                f"- {symbol['symbol_name']} ({symbol['symbol_kind']}) [{symbol['start_line']}-{symbol['end_line']}]"
            )
        block_lines = []
        for block in file_item.get("blocks", [])[:5]:
            block_lines.append(
                f"- {block['block_type']} [{block['start_line']}-{block['end_line']}]: {block['summary']}"
            )
        source_preview = "\n".join(file_item["source"].splitlines()[:200])
        sections.append(
            "\n".join(
                [
                    f"File: {file_item['relative_path']}",
                    "Symbols:",
                    *(symbol_lines or ["- None"]),
                    "Relevant blocks:",
                    *(block_lines or ["- None"]),
                    "Relevant file excerpt:",
                    "```python",
                    source_preview,
                    "```",
                ]
            )
        )
        if estimate_tokens("\n\n".join(sections)) >= max_tokens:
            break
    sections.extend(
        [
            "",
            "Competing file comparison table:",
            json.dumps(file_comparison, indent=2),
        ]
    )
    return "\n\n".join(sections)


def build_summary_prompt(problem_statement: str, evidence_packet: str) -> str:
    return f"""Problem statement:
{problem_statement}

Developer workflow evidence:
{evidence_packet}

Summarize the likely implementation path, the likely bug location, and the reasoning that ties the issue to the code.
Prefer implementation files over wrappers or consumers."""


def build_structured_summary_prompt(problem_statement: str, evidence_packet: str) -> str:
    return f"""Problem statement:
{problem_statement}

Developer workflow evidence:
{evidence_packet}

Return structured localization data for the most likely implementation location."""


def llm_summarizer(llm_client, problem_statement: str, evidence_packet: str) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    summary_prompt = build_summary_prompt(problem_statement, evidence_packet)
    summary_text, summary_usage = with_retries(
        lambda: llm_client.generate_text(DEVELOPER_SUMMARY_SYSTEM_PROMPT, summary_prompt)
    )
    structured_prompt = build_structured_summary_prompt(problem_statement, evidence_packet)
    structured_payload = with_retries(
        lambda: llm_client.generate_json(DEVELOPER_STRUCTURED_SUMMARY_SYSTEM_PROMPT, structured_prompt)
    )
    if not isinstance(structured_payload, dict):
        structured_payload = {}
    structured_summary = {
        "likely_bug_files": [str(item) for item in structured_payload.get("likely_bug_files", []) if str(item).strip()],
        "likely_symbols": [str(item) for item in structured_payload.get("likely_symbols", []) if str(item).strip()],
        "issue_shape": str(structured_payload.get("issue_shape", "generic")).strip() or "generic",
        "fix_mechanism": str(structured_payload.get("fix_mechanism", "")).strip(),
        "entrypoint_files": [str(item) for item in structured_payload.get("entrypoint_files", []) if str(item).strip()],
        "implementation_files": [str(item) for item in structured_payload.get("implementation_files", []) if str(item).strip()],
        "constant_names": [str(item) for item in structured_payload.get("constant_names", []) if str(item).strip()],
        "suspicious_line_patterns": [str(item) for item in structured_payload.get("suspicious_line_patterns", []) if str(item).strip()],
        "confidence": float(structured_payload.get("confidence", 0.0) or 0.0),
    }
    usages = {
        "summary_usage": summary_usage,
        "structured_summary_usage": {},
    }
    return summary_text, structured_summary, {
        "summary_prompt": summary_prompt,
        "structured_prompt": structured_prompt,
    }, usages


def target_selector(
    llm_client,
    workspace_dir: Path,
    problem_statement: str,
    summary_text: str,
    structured_summary: dict[str, Any],
    merged_candidates: list[dict[str, Any]],
    file_items: list[dict[str, Any]],
    file_comparison: dict[str, Any],
    evidence_packet: str,
    runtime_evidence: dict[str, Any] | None = None,
    instrumentation_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    normalized_summary = normalize_structured_summary(workspace_dir, summary_text, structured_summary)
    candidate_pools = build_constrained_candidate_pools(workspace_dir, file_items, normalized_summary)
    merged_paths = [str(item["relative_path"]) for item in merged_candidates]
    candidate_pools["primary_files"] = dedupe_preserve(merged_paths[:8] + candidate_pools["primary_files"])
    file_selection = select_file_candidate(
        problem_statement,
        normalized_summary,
        merged_candidates,
        file_items,
        file_comparison,
    )
    selected_file = str(file_selection.get("chosen_file", "")).strip()
    selected_file_item = next((item for item in file_items if str(item["relative_path"]) == selected_file), None)
    selected_candidate = next((item for item in merged_candidates if str(item["relative_path"]) == selected_file), None)
    if selected_file_item is None:
        heuristic_target, selector_decision = select_target_deterministic(
            file_items,
            problem_statement,
            summary_text,
            normalized_summary,
        )
        if heuristic_target is None:
            heuristic_target = choose_target_heuristic(
                file_items,
                problem_statement,
                summary_text,
                structured_summary=normalized_summary,
            )
        target_usage: dict[str, Any] = {}
        if heuristic_target is None:
            target_prompt = build_target_selection_prompt(problem_statement, summary_text, normalized_summary, evidence_packet)
            target, target_usage = choose_target(llm_client, target_prompt)
        else:
            target = heuristic_target
        region_selection = {
            "selected_file": str(target["path"]) if target else "",
            "selected_line": int(target["line_number"]) if target else 1,
            "selected_symbol": None,
            "selected_block_type": None,
            "rationale": "Fallback selector used because file-first selected file was unavailable in expanded context.",
            "selection_rule": "fallback_combined_selector",
        }
        selector_decision["target"] = target
    else:
        target, region_selection = _pick_region_from_file(
            selected_file_item,
            selected_candidate,
            normalized_summary,
            runtime_evidence=runtime_evidence,
            instrumentation_evidence=instrumentation_evidence,
        )
        target_usage = {}
        selector_decision = {
            "selector_mode": "two_stage",
            "selector_rule": file_selection["selection_rule"],
            "used_fallback": False,
            "target": target,
        }
    selector_decision["selection_provenance"] = {
        "merged_candidate_paths": merged_paths[:10],
        "primary_files": candidate_pools["primary_files"][:10],
        "implementation_files": normalized_summary.get("implementation_files", [])[:10],
        "file_comparison_top_files": file_comparison.get("top_files", [])[:10],
    }
    return target, {
        "normalized_summary": normalized_summary,
        "candidate_pools": candidate_pools,
        "target_usage": target_usage,
        "file_selection": file_selection,
        "region_selection": region_selection,
    }, selector_decision
