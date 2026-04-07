from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


RUNTIME_SENSITIVE_TOKENS = (
    "autoreload",
    "dev server",
    "runserver",
    "request",
    "response",
    "manage.py",
    "management command",
    "template",
    "render",
    "select_related",
    "filteredrelation",
    "compiler",
    "traceback",
    "warning",
    "runtime",
    "orm",
    "query",
)

SAFE_COMMAND_PREFIXES = {
    "python",
    "python3",
    "pytest",
    "py.test",
    "./manage.py",
    "pylint",
    "django-admin",
    "sphinx-build",
    "sphinx-quickstart",
}


def runtime_gate(
    problem_statement: str,
    static_result: dict[str, Any],
    gate_threshold: float = 0.6,
    runtime_only_on_failures: bool = True,
) -> dict[str, Any]:
    lowered = problem_statement.lower()
    reasons: list[str] = []
    score = 0.0

    failure_taxonomy = str(static_result.get("failure_taxonomy", "")).strip()
    if failure_taxonomy in {
        "deterministic candidate discovery missed correct file",
        "comparison preferred wrong file despite good evidence",
        "issue likely requires runtime execution/reproduction",
    }:
        reasons.append(f"failure_taxonomy:{failure_taxonomy}")
        score += 0.45

    if runtime_only_on_failures and static_result.get("semantic_localization_match"):
        return {
            "should_run": False,
            "reasons": ["static_localization_already_succeeded"],
            "gate_confidence": 0.0,
            "expected_runtime_value": "low",
        }

    if not static_result.get("merged_candidate_top3_contains_gold_file", True):
        reasons.append("weak_top3_candidate_confidence")
        score += 0.2
    if not static_result.get("merged_candidate_top5_contains_gold_file", True):
        reasons.append("weak_top5_candidate_confidence")
        score += 0.25
    if float(static_result.get("file_selection_confidence", 0.0) or 0.0) < 0.55:
        reasons.append("low_file_selection_confidence")
        score += 0.15

    sensitive_hits = [token for token in RUNTIME_SENSITIVE_TOKENS if token in lowered]
    if sensitive_hits:
        reasons.append(f"runtime_sensitive_issue_shape:{','.join(sensitive_hits[:5])}")
        score += 0.25

    if "traceback" in lowered or "exception" in lowered or "stack trace" in lowered:
        reasons.append("traceback_like_issue_text")
        score += 0.2

    if re.search(r"```(?:bash|shell|console|sh)?\n[^`]*(python|pytest|manage\.py)", problem_statement, flags=re.IGNORECASE):
        reasons.append("explicit_command_in_issue")
        score += 0.25

    score = min(score, 1.0)
    expected_runtime_value = "high" if score >= 0.75 else "medium" if score >= gate_threshold else "low"
    return {
        "should_run": score >= gate_threshold,
        "reasons": reasons or ["no_runtime_signal"],
        "gate_confidence": round(score, 4),
        "expected_runtime_value": expected_runtime_value,
    }


def infer_runtime_command(
    workspace_dir: Path,
    repo_name: str,
    problem_statement: str,
    test_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    pylint_command = _infer_pylint_command(workspace_dir, repo_name, problem_statement)
    if pylint_command is not None:
        return pylint_command

    explicit = _extract_explicit_command(problem_statement)
    if explicit is not None:
        return explicit

    lowered = problem_statement.lower()

    django_test_target = _extract_django_test_target(problem_statement)
    if (workspace_dir / "tests" / "runtests.py").exists() and django_test_target:
        return {
            "mode": "django_test_target",
            "command": [_python_executable(), "tests/runtests.py", django_test_target],
            "cwd": str(workspace_dir),
            "target": django_test_target,
            "reason": "Use the explicit Django test identifier found in the issue text.",
        }
    if (
        "django" in repo_name.lower()
        and "django-admin startproject" in lowered
        and "manage.py runserver" in lowered
        and not (workspace_dir / "manage.py").exists()
    ):
        return {
            "mode": "skip_external_project_django_repro",
            "command": [],
            "cwd": str(workspace_dir),
            "target": "",
            "reason": "Issue repro relies on scaffolding a separate Django project; no safe in-repo manage.py probe was inferred.",
        }
    if (workspace_dir / "manage.py").exists() and "django" in repo_name.lower() and (
        "manage.py" in lowered or "runserver" in lowered or "django-admin" in lowered
    ):
        return {
            "mode": "django_manage_runserver_probe",
            "command": [_python_executable(), "manage.py", "runserver", "--noreload"],
            "cwd": str(workspace_dir),
            "target": "manage.py runserver --noreload",
            "reason": "Issue explicitly mentions Django startup/manage.py behavior; probe the server startup path directly.",
        }

    if test_candidates:
        rel = _select_safe_test_candidate(test_candidates)
        if rel:
            return {
                "mode": "test_candidate",
                "command": ["pytest", "-q", rel],
                "cwd": str(workspace_dir),
                "target": rel,
                "reason": "Use the highest-ranked discovered test file as a narrow runtime probe.",
            }

    if (workspace_dir / "manage.py").exists() and "django" in repo_name.lower():
        return {
            "mode": "django_manage_check",
            "command": [_python_executable(), "manage.py", "check"],
            "cwd": str(workspace_dir),
            "target": "manage.py check",
            "reason": "Django repo with no narrower repro signal; use a safe management-command probe.",
        }

    if _looks_like_pytest_repo(workspace_dir) and _looks_test_like(problem_statement):
        return {
            "mode": "pytest_repo_probe",
            "command": ["pytest", "-q"],
            "cwd": str(workspace_dir),
            "target": "pytest -q",
            "reason": "Issue looks test-like and repo appears pytest-based; use a generic narrow pytest probe.",
        }

    return {
        "mode": "skip",
        "command": [],
        "cwd": str(workspace_dir),
        "target": "",
        "reason": "No safe, narrow repro command could be inferred.",
    }


def run_runtime_command(
    workspace_dir: Path,
    command_spec: dict[str, Any],
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    command = [str(part) for part in command_spec.get("command", []) if str(part).strip()]
    temp_files = command_spec.get("temp_files", []) or []
    if not command:
        return {
            "attempted": False,
            "succeeded": False,
            "timed_out": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": str(command_spec.get("reason", "No runtime command inferred.")),
        }
    created_temp_paths: list[Path] = []
    try:
        for item in temp_files:
            relative_path = str(item.get("relative_path", "")).strip()
            text = str(item.get("text", ""))
            if not relative_path:
                continue
            temp_path = workspace_dir / relative_path
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(text, encoding="utf-8")
            created_temp_paths.append(temp_path)
        completed = subprocess.run(
            command,
            cwd=str(workspace_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, **{str(k): str(v) for k, v in (command_spec.get("env", {}) or {}).items()}},
        )
        return {
            "attempted": True,
            "succeeded": completed.returncode == 0,
            "timed_out": False,
            "exit_code": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": "",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "attempted": True,
            "succeeded": False,
            "timed_out": True,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Runtime command timed out after {timeout_seconds}s.",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "attempted": True,
            "succeeded": False,
            "timed_out": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        for temp_path in reversed(created_temp_paths):
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            parent = temp_path.parent
            while parent != workspace_dir and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def parse_runtime_traceback(workspace_dir: Path, stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    frames: list[dict[str, Any]] = []
    for path_text, line_text, function_name in re.findall(
        r'File "([^"]+)", line (\d+), in ([^\n]+)',
        combined,
    ):
        relative_path = _normalize_runtime_path(workspace_dir, path_text)
        if not relative_path:
            continue
        frames.append(
            {
                "raw_path": path_text,
                "relative_path": relative_path,
                "line_number": int(line_text),
                "function_name": function_name.strip(),
            }
        )

    if not frames:
        for path_text, line_text in re.findall(r"(?m)^([A-Za-z0-9_./-]+\.py):(\d+)", combined):
            relative_path = _normalize_runtime_path(workspace_dir, path_text)
            frames.append(
                {
                    "raw_path": path_text,
                    "relative_path": relative_path,
                    "line_number": int(line_text),
                    "function_name": "",
                }
            )

    exception_type = ""
    exception_message = ""
    for line in reversed([line.strip() for line in combined.splitlines() if line.strip()]):
        match = re.match(r"([A-Za-z_][A-Za-z0-9_.]+):\s*(.*)", line)
        if match:
            exception_type = match.group(1)
            exception_message = match.group(2)
            break

    top_stack_files = dedupe_preserve(
        [str(frame["relative_path"]) for frame in frames if str(frame.get("relative_path", "")).strip()]
    )
    top_stack_lines = [
        {
            "relative_path": str(frame["relative_path"]),
            "line_number": int(frame["line_number"]),
            "function_name": str(frame.get("function_name", "")).strip(),
        }
        for frame in frames
        if str(frame.get("relative_path", "")).strip()
    ]

    return {
        "frames": frames,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "top_stack_files": top_stack_files[:10],
        "top_stack_lines": top_stack_lines[:20],
        "produced_traceback": bool(frames),
    }


def build_runtime_evidence(
    command_spec: dict[str, Any],
    execution: dict[str, Any],
    traceback_payload: dict[str, Any],
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int | None, str]] = set()

    for index, frame in enumerate(traceback_payload.get("frames", [])):
        relative_path = str(frame.get("relative_path", "")).strip()
        if not relative_path:
            continue
        line_number = int(frame.get("line_number", 1))
        function_name = str(frame.get("function_name", "")).strip()
        key = (relative_path, "runtime_traceback", line_number, function_name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        evidence.append(
            {
                "relative_path": relative_path,
                "tool": "runtime_traceback",
                "match_type": "runtime_traceback_frame",
                "anchor": function_name or str(traceback_payload.get("exception_type", "")).strip() or "traceback",
                "symbol_name": function_name or None,
                "line_number": line_number,
                "frame_index": index,
            }
        )
        if traceback_payload.get("exception_type"):
            evidence.append(
                {
                    "relative_path": relative_path,
                    "tool": "runtime_literal",
                    "match_type": "runtime_exception_literal",
                    "anchor": str(traceback_payload.get("exception_type")),
                    "symbol_name": function_name or None,
                    "line_number": line_number,
                }
            )

    target = str(command_spec.get("target", "")).strip()
    if target.endswith(".py"):
        evidence.append(
            {
                "relative_path": target,
                "tool": "runtime_test",
                "match_type": "runtime_test_target",
                "anchor": target,
                "symbol_name": None,
                "line_number": None,
            }
        )

    strong_exception = bool(re.search(r"(Error|Exception|Warning|Failure)$", str(traceback_payload.get("exception_type", "")).strip()))
    exception_type = str(traceback_payload.get("exception_type", "")).strip()
    exception_message = str(traceback_payload.get("exception_message", "")).strip()
    environment_blocker = _is_environment_blocker(exception_type, exception_message)
    useful_signal = bool(
        traceback_payload.get("produced_traceback")
        or traceback_payload.get("top_stack_files")
        or any(item["tool"] != "runtime_test" for item in evidence)
        or strong_exception
    )

    summary = {
        "attempted": bool(execution.get("attempted")),
        "succeeded": bool(execution.get("succeeded")),
        "timed_out": bool(execution.get("timed_out")),
        "exit_code": execution.get("exit_code"),
        "exception_type": exception_type,
        "exception_message": exception_message,
        "top_stack_files": traceback_payload.get("top_stack_files", []),
        "useful_signal": useful_signal,
        "environment_blocker": environment_blocker,
    }
    return {
        "summary": summary,
        "evidence": evidence,
        "traceback": traceback_payload,
    }


def summarize_runtime_attempt(
    gate: dict[str, Any],
    command_spec: dict[str, Any],
    execution: dict[str, Any],
    runtime_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "gate": gate,
        "command_mode": str(command_spec.get("mode", "")),
        "command": command_spec.get("command", []),
        "reason": str(command_spec.get("reason", "")),
        "attempted": bool(execution.get("attempted")),
        "succeeded": bool(execution.get("succeeded")),
        "timed_out": bool(execution.get("timed_out")),
        "exit_code": execution.get("exit_code"),
        "produced_traceback": bool(runtime_evidence.get("traceback", {}).get("produced_traceback")),
        "useful_signal": bool(runtime_evidence.get("summary", {}).get("useful_signal")),
        "top_stack_files": runtime_evidence.get("summary", {}).get("top_stack_files", []),
        "exception_type": runtime_evidence.get("summary", {}).get("exception_type", ""),
        "environment_blocker": bool(runtime_evidence.get("summary", {}).get("environment_blocker")),
    }


def _extract_explicit_command(problem_statement: str) -> dict[str, Any] | None:
    fenced_blocks = re.findall(r"```(?:bash|shell|console|sh)?\n(.*?)```", problem_statement, flags=re.DOTALL | re.IGNORECASE)
    for block in fenced_blocks:
        for raw_line in block.splitlines():
            line = _strip_shell_prompt(raw_line)
            if not line or line.startswith("#"):
                continue
            parsed = _safe_shell_command(line)
            if parsed:
                return {
                    "mode": "explicit_issue_command",
                    "command": parsed,
                    "cwd": "",
                    "target": parsed[-1] if parsed else "",
                    "reason": "Use the explicit repro command provided in the issue.",
                }
    return None


def _safe_shell_command(line: str) -> list[str] | None:
    if any(token in line for token in ("&&", "||", ";", ">", "<", "`")):
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        return None
    if not parts:
        return None
    if parts[0] not in SAFE_COMMAND_PREFIXES and not (
        len(parts) >= 2 and (
            (parts[0].endswith("python") and parts[1] == "manage.py")
            or (parts[0].endswith("python") and parts[1] == "-m")
        )
    ):
        return None
    if parts[0] == "./manage.py":
        parts = [_python_executable(), "manage.py", *parts[1:]]
    if parts[0] == "pylint":
        parts = [_python_executable(), "-m", "pylint", *parts[1:]]
    elif parts[0] == "django-admin":
        parts = [_python_executable(), "-m", "django", *parts[1:]]
    elif parts[0] == "sphinx-build":
        parts = [_python_executable(), "-m", "sphinx", *parts[1:]]
    return parts


def _looks_like_pytest_repo(workspace_dir: Path) -> bool:
    return any((workspace_dir / name).exists() for name in ("pytest.ini", "pyproject.toml", "tox.ini"))


def _looks_test_like(problem_statement: str) -> bool:
    lowered = problem_statement.lower()
    return any(token in lowered for token in ("test", "assert", "traceback", "exception", "fails", "failure"))


def _normalize_runtime_path(workspace_dir: Path, path_text: str) -> str:
    if path_text.startswith("<") and path_text.endswith(">"):
        return ""
    path = Path(path_text)
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(workspace_dir.resolve()))
        except Exception:
            return ""
    candidate = (workspace_dir / path).resolve()
    try:
        return str(candidate.relative_to(workspace_dir.resolve()))
    except Exception:
        if (workspace_dir / path_text).exists():
            return path_text
        return ""


def dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _select_safe_test_candidate(test_candidates: list[dict[str, Any]]) -> str:
    banned_fragments = (
        "settings.py",
        "runtests.py",
        "conftest.py",
        "__init__.py",
    )
    for item in test_candidates:
        rel = str(item.get("relative_path", "")).strip()
        if not rel:
            continue
        lowered = rel.lower()
        if any(fragment in lowered for fragment in banned_fragments):
            continue
        return rel
    return ""


def _extract_django_test_target(problem_statement: str) -> str:
    patterns = [
        r"FAIL:\s+[A-Za-z_][A-Za-z0-9_]*\s+\(([\w.]+)\)",
        r"add to\s+([\w.]+)\.([A-Za-z_][A-Za-z0-9_]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, problem_statement)
        if not match:
            continue
        if len(match.groups()) == 1:
            return str(match.group(1)).strip()
        if len(match.groups()) == 2:
            return f"{match.group(1).strip()}.{match.group(2).strip()}"
    return ""


def _strip_shell_prompt(raw_line: str) -> str:
    line = raw_line.strip()
    if not line:
        return ""
    prompt_match = re.match(r"^(?:\([^)]*\)\s*)?[^$#\n]*[$#]\s+(.*)$", line)
    if prompt_match:
        return prompt_match.group(1).strip()
    if line.startswith("$ "):
        return line[2:].strip()
    return line


def _infer_pylint_command(
    workspace_dir: Path,
    repo_name: str,
    problem_statement: str,
) -> dict[str, Any] | None:
    lowered = problem_statement.lower()
    if "pylint" not in repo_name.lower() and "pylint" not in lowered:
        return None
    if "pylint" not in lowered:
        return None

    config_text = _extract_named_code_block(problem_statement, ".pylintrc")
    if config_text and (workspace_dir / "pylint").exists():
        return {
            "mode": "pylint_cli_with_config",
            "command": [_python_executable(), "-m", "pylint", "--rcfile", ".nli_runtime/.pylintrc", "pylint"],
            "cwd": str(workspace_dir),
            "target": ".nli_runtime/.pylintrc",
            "reason": "Issue provides a Pylint CLI repro and config snippet; run pylint with the extracted temporary rcfile.",
            "temp_files": [
                {
                    "relative_path": ".nli_runtime/.pylintrc",
                    "text": config_text,
                }
            ],
        }
    if (workspace_dir / "pylint").exists():
        return {
            "mode": "pylint_cli",
            "command": [_python_executable(), "-m", "pylint", "pylint"],
            "cwd": str(workspace_dir),
            "target": "python -m pylint pylint",
            "reason": "Issue appears to be a Pylint CLI bug; prefer invoking pylint directly over guessed pytest files.",
        }
    return None


def _extract_named_code_block(problem_statement: str, label: str) -> str:
    pattern = rf"{re.escape(label)}:\s*```[A-Za-z0-9_-]*\r?\n(.*?)```"
    match = re.search(pattern, problem_statement, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip() + "\n"


def _python_executable() -> str:
    return sys.executable or "python3"


def _is_environment_blocker(exception_type: str, exception_message: str) -> bool:
    if exception_type in {"ModuleNotFoundError", "ImportError"}:
        return True
    lowered = exception_message.lower()
    return any(
        token in lowered
        for token in (
            "no module named",
            "cannot import name",
            "command not found",
            "executable file not found",
            "not installed",
        )
    )
