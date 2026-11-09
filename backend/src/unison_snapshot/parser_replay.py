"""One lifecycle for source-bound parser checkpoints.

Format adapters choose the parser from the original and validate their proof.
Only this module decides whether a success/failure is current. A parser change,
missing proof or conflicting terminal artifacts reopens the archived input;
an old success is never an alternative current result.
"""
from dataclasses import dataclass, field
import json
import hashlib
import ast
from pathlib import Path
from typing import Callable, Mapping


def read_checkpoint(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


@dataclass(frozen=True)
class ParserTarget:
    version: str
    binding: Mapping[str, object] = field(default_factory=dict)
    required: Mapping[str, type] = field(default_factory=dict)
    records: Mapping[str, Mapping[str, type]] = field(default_factory=dict)

    def accepts(self, result: dict | None, *, validate: Callable | None = None) -> bool:
        if (not isinstance(result, dict) or result.get("parser_version") != self.version
                or any(result.get(key) != value for key, value in self.binding.items())
                or any(type(result.get(key)) is not kind for key, kind in self.required.items())
                or any(not isinstance(result.get(key), list) or any(
                    not isinstance(row, dict) or any(type(row.get(column)) is not kind
                        for column, kind in columns.items()) for row in result[key])
                    for key, columns in self.records.items())):
            return False
        return validate is None or validate(result) is not False

    def select(self, result: dict | None, failure: dict | None = None, *,
               dependencies=(), validate: Callable | None = None,
               failure_target=None, resumable: Callable | None = None) -> str:
        # Historical failures do not supersede a valid current success. A
        # contradictory current failure does, so the source is tried again.
        failure_target = failure_target or self
        current_failure = failure_target.accepts(failure)
        current_result = self.accepts(result, validate=validate)
        if current_result and all(dependencies) and not current_failure:
            return "reuse"
        if not current_result and current_failure and not (resumable and resumable(failure)):
            return "failure"
        return "parse"

    def load(self, path: Path, *, dependencies=(), validate: Callable | None = None):
        result = read_checkpoint(path)
        return result if self.select(result, dependencies=dependencies, validate=validate) == "reuse" else None


def require_complete(expected, current, *, label):
    """Incomplete parser upgrades stop assembly; they cannot retain stale facts."""
    missing, unexpected = set(expected) - set(current), set(current) - set(expected)
    if missing or unexpected:
        raise ValueError(f"{label}: {len(missing)} current parser results missing; "
                         f"{len(unexpected)} unexpected results")


def suite_version(*versions):
    """Failure checkpoints reopen when any parser in a routing suite changes."""
    return 'parser-suite-' + hashlib.sha256(json.dumps(sorted(versions)).encode()).hexdigest()[:20]


def suite_from_modules(*modules):
    """Read declared format versions without importing mutually dependent parsers."""
    versions = []
    for module in modules:
        tree = ast.parse(Path(__file__).with_name(module + '.py').read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                    'VERSION' in target.id for target in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    versions.append(module + ':' + node.value.value)
    return suite_version(*versions)
