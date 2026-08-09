# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tool/API drift gate for the published Agent Skills (ADR-058 / G36 P0.7).

Every abicheck command, option, and report-JSON field a skill's prose names is
checked against the **live** Click command tree and the **live** compare-report
JSON Schema — the same introspection `scripts/gen_cli_reference.py` uses. A
renamed or removed flag or field fails here, with the offending file and line,
instead of silently rotting inside a published skill.

Scanned across every skill source: `SKILL.md`, every skill-specific
`references/*.md`, and every `shared/*.md` fragment. Restricting the field
half to one fragment would leave the field references in the others (and the
CLI half's own citations) unprotected.

**Scope — syntactic drift only.** A command, flag, or field that keeps its
name while its meaning changes is not caught here, and nothing in this plan
closes that; re-reading the affected skill on that command's normal review
cadence is the only mitigation today.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest

import abicheck.schemas as schemas
from abicheck.cli import main as cli_main

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "skills-src"

SKILL_FILES = sorted(SRC.rglob("*.md"))

_INLINE_RE = re.compile(r"`([^`\n]+)`")
_LONG_OPT_RE = re.compile(r"^--[a-z][a-z0-9-]*$")

#: Escape hatch for a long option that is legitimately not abicheck's —
#: compiler/linker vocabulary a skill quotes when explaining a build profile.
#: Deliberately empty today: no skill currently names one, and an empty set
#: means a newly introduced foreign flag fails loudly with a message pointing
#: here, rather than being silently tolerated by a pre-broadened allowlist.
FOREIGN_LONG_OPTIONS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Live CLI introspection (same mechanism as scripts/gen_cli_reference.py)
# ---------------------------------------------------------------------------


def _walk(
    cmd: click.Command, path: tuple[str, ...] = ()
) -> dict[tuple[str, ...], set[str]]:
    """Every command path in the live tree, mapped to its own option strings."""
    out: dict[tuple[str, ...], set[str]] = {
        path: {
            opt
            # secondary_opts carries the `--no-x` half of a Click flag pair
            # (`--scope-public-headers/--no-scope-public-headers`), which a
            # skill legitimately names and `params.opts` alone does not list.
            for param in cmd.params
            for opt in (*param.opts, *param.secondary_opts)
            if opt.startswith("-")
        }
    }
    for name, sub in getattr(cmd, "commands", {}).items():
        out.update(_walk(sub, (*path, name)))
    return out


CLI_TREE = _walk(cli_main)
ALL_OPTIONS = {opt for opts in CLI_TREE.values() for opt in opts}
COMMAND_PATHS = {path for path in CLI_TREE if path}


def _abicheck_invocations(text: str) -> list[tuple[int, str]]:
    """Every `abicheck ...` shell invocation, with its 1-based start line.

    Joins backslash line continuations so a multi-line command reads as one.
    """
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("abicheck "):
            start = index + 1
            parts = [stripped]
            while parts[-1].endswith("\\") and index + 1 < len(lines):
                index += 1
                parts.append(lines[index].strip())
            out.append((start, " ".join(part.rstrip("\\").strip() for part in parts)))
        index += 1
    return out


def _fenced_and_inline_invocations(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    found = _abicheck_invocations(text)
    for match in _INLINE_RE.finditer(text):
        token = match.group(1).strip()
        if token.startswith("abicheck "):
            line = text.count("\n", 0, match.start()) + 1
            found.append((line, token))
    return found


@pytest.mark.parametrize(
    "path", SKILL_FILES, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_cited_command_path_exists(path: Path):
    offenders: list[str] = []
    for line, invocation in _fenced_and_inline_invocations(path):
        words = [w for w in invocation.split()[1:] if not w.startswith("-")]
        if not words:
            continue
        # Longest matching real command path wins; the remaining words are
        # operands (OLD/NEW/a file), not commands.
        candidate = tuple(words[:2])
        if candidate in COMMAND_PATHS:
            continue
        if (words[0],) in COMMAND_PATHS:
            continue
        offenders.append(
            f"{path.relative_to(REPO)}:{line}: `abicheck {' '.join(words[:2])}` "
            "is not a command on the live CLI"
        )
    assert offenders == []


@pytest.mark.parametrize(
    "path", SKILL_FILES, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_option_used_in_an_invocation_exists_on_that_command(path: Path):
    offenders: list[str] = []
    for line, invocation in _fenced_and_inline_invocations(path):
        words = invocation.split()[1:]
        positional = [w for w in words if not w.startswith("-")]
        command: tuple[str, ...] = ()
        for size in (2, 1):
            candidate = tuple(positional[:size])
            if candidate in COMMAND_PATHS:
                command = candidate
                break
        allowed = CLI_TREE.get(command, set()) | CLI_TREE[()]
        for word in words:
            token = word.split("=", 1)[0]
            if not token.startswith("--") or not _LONG_OPT_RE.match(token):
                continue
            if token in FOREIGN_LONG_OPTIONS:
                continue
            if token not in allowed:
                offenders.append(
                    f"{path.relative_to(REPO)}:{line}: {token} is not an option "
                    f"of `abicheck {' '.join(command) or '<root>'}`"
                )
    assert offenders == []


@pytest.mark.parametrize(
    "path", SKILL_FILES, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_bare_long_option_mentioned_in_prose_exists(path: Path):
    """A flag named in prose (inline code, outside a full invocation) rots the
    same way one inside a command does."""
    offenders: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in _INLINE_RE.finditer(text):
        token = match.group(1).strip()
        if not _LONG_OPT_RE.match(token) or token in FOREIGN_LONG_OPTIONS:
            continue
        if token not in ALL_OPTIONS:
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(REPO)}:{line}: {token} is not an option on "
                "the live CLI"
            )
    assert offenders == []


# ---------------------------------------------------------------------------
# Live report-schema introspection
# ---------------------------------------------------------------------------

_SCHEMA = schemas.load_compare_report_schema()


def _deref(node: dict) -> dict:
    ref = node.get("$ref")
    if not ref:
        return node
    assert ref.startswith("#/$defs/"), ref
    return _SCHEMA["$defs"][ref[len("#/$defs/") :]]


def _all_property_names(node: object, seen: set[int] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if id(node) in seen or not isinstance(node, dict):
        return set()
    seen.add(id(node))
    names: set[str] = set()
    properties = node.get("properties")
    if isinstance(properties, dict):
        names |= set(properties)
    for value in node.values():
        if isinstance(value, dict):
            names |= _all_property_names(value, seen)
        elif isinstance(value, list):
            for item in value:
                names |= _all_property_names(item, seen)
    return names


def _all_enum_values(node: object, seen: set[int] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if id(node) in seen or not isinstance(node, dict):
        return set()
    seen.add(id(node))
    values = {v for v in node.get("enum", []) if isinstance(v, str)}
    for value in node.values():
        if isinstance(value, dict):
            values |= _all_enum_values(value, seen)
        elif isinstance(value, list):
            for item in value:
                values |= _all_enum_values(item, seen)
    return values


SCHEMA_PROPERTY_NAMES = _all_property_names(_SCHEMA)
SCHEMA_ENUM_VALUES = _all_enum_values(_SCHEMA)

#: A candidate field-reference token: snake_case, optionally dotted, with an
#: optional `[]` marking an array hop (`changes[].kind`). Only tokens carrying
#: a `_`, a `.`, or `[]` are considered — a bare English word in inline code
#: is prose, not a field reference.
_FIELD_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(\[\])?(\.[a-z][a-z0-9_]*(\[\])?)*$")


def _resolve_path(path: str) -> bool:
    """True if a dotted path resolves through the live schema."""
    node: dict = _SCHEMA
    for raw in path.split("."):
        segment = raw.removesuffix("[]")
        node = _deref(node)
        properties = node.get("properties")
        if not isinstance(properties, dict) or segment not in properties:
            return False
        node = _deref(properties[segment])
        if raw.endswith("[]"):
            items = node.get("items")
            if not isinstance(items, dict):
                return False
            node = _deref(items)
    return True


@pytest.mark.parametrize(
    "path", SKILL_FILES, ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_every_cited_report_field_still_exists(path: Path):
    """A dotted path must resolve through the schema; a bare token must be a
    real property name or a real enum value somewhere in it.

    Enum values are checked deliberately, not incidentally: `profile_mismatch`,
    `scope_mismatch`, and the `evidence_tier` ladder are exactly the strings a
    skill's decision tree branches on, so a renamed enum member is the same
    class of silent rot as a renamed field.
    """
    offenders: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in _INLINE_RE.finditer(text):
        token = match.group(1).strip()
        if not _FIELD_TOKEN_RE.match(token):
            continue
        if "_" not in token and "." not in token and "[]" not in token:
            continue
        if "." in token or "[]" in token:
            ok = _resolve_path(token)
        else:
            ok = token in SCHEMA_PROPERTY_NAMES or token in SCHEMA_ENUM_VALUES
        if not ok:
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(REPO)}:{line}: {token!r} is not a field or "
                "enum value in the live compare-report schema"
            )
    assert offenders == []


def test_the_drift_check_actually_has_teeth():
    """Negative control: the checks above pass trivially if the extraction
    finds nothing. Assert each half really did resolve real citations."""
    commands = sum(len(_fenced_and_inline_invocations(p)) for p in SKILL_FILES)
    assert commands >= 5, "no abicheck invocations were extracted from the skills"
    assert _resolve_path("changes[].kind")
    assert not _resolve_path("changes[].no_such_field")
    assert "evidence_tier" in SCHEMA_PROPERTY_NAMES
    assert "profile_mismatch" in SCHEMA_ENUM_VALUES
    assert "--used-by" in ALL_OPTIONS
    assert "--no-scope-public-headers" in ALL_OPTIONS  # a Click flag pair's other half
    assert ("project", "validate") in COMMAND_PATHS
