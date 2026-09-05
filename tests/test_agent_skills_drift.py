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
from abicheck.checker_policy import ChangeKind
from abicheck.cli import main as cli_main

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "skills-src"

#: The files the generator actually publishes into an installed skill — each
#: skill's `SKILL.md`, its own `references/`, and the shared fragments. This
#: gate protects what ships to an agent, so `skills-src/CLAUDE.md` (the
#: contributor contract, never published) is deliberately out of scope: it
#: discusses repository internals like `latest_release` that are not report
#: fields, and scanning it would generate false positives indefinitely.
SKILL_FILES = sorted(
    path
    for path in SRC.rglob("*.md")
    if path.parent != SRC  # skills-src/CLAUDE.md and any future sibling doc
)

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


def _commands(
    cmd: click.Command, path: tuple[str, ...] = ()
) -> dict[tuple[str, ...], click.Command]:
    out = {path: cmd}
    for name, sub in getattr(cmd, "commands", {}).items():
        out.update(_commands(sub, (*path, name)))
    return out


CLI_TREE = _walk(cli_main)
COMMAND_OBJECTS = _commands(cli_main)
ALL_OPTIONS = {opt for opts in CLI_TREE.values() for opt in opts}
COMMAND_PATHS = {path for path in CLI_TREE if path}


def _takes_a_value(command: click.Command, token: str) -> bool:
    """True if `token` is an option that consumes the following argv word."""
    for source in (command, cli_main):
        for param in source.params:
            if token in (*param.opts, *param.secondary_opts):
                return not getattr(param, "is_flag", False) and not isinstance(
                    param, click.Argument
                )
    return False


def _operands(command: click.Command, words: list[str]) -> list[str]:
    """The positional operands in `words`, with option values removed.

    An option's *value* looks exactly like an operand (`--instantiation-manifest
    t.json`), so counting bare words would badly miscount. Click knows which
    options consume a following word; ask it rather than guessing.
    """
    out: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word.startswith("-"):
            if "=" not in word and _takes_a_value(command, word):
                index += 1
        else:
            out.append(word)
        index += 1
    return out


def _required_argument_names(command: click.Command) -> list[str]:
    return [
        param.name or "<arg>"
        for param in command.params
        if isinstance(param, click.Argument) and param.required
    ]


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


def _runnable_invocations(path: Path) -> list[tuple[int, str]]:
    """Only the invocations a reader would actually copy and run.

    A line in a fenced block is a real example. An inline span is usually just
    *naming* a command (`abicheck deps compare`), which carries no operands by
    design — demanding them there would flag correct prose. An inline span
    that carries an option is a one-liner command, so it counts.
    """
    text = path.read_text(encoding="utf-8")
    runnable = list(_abicheck_invocations(text))
    for match in _INLINE_RE.finditer(text):
        token = match.group(1).strip()
        if token.startswith("abicheck ") and any(
            word.startswith("-") for word in token.split()
        ):
            line = text.count("\n", 0, match.start()) + 1
            runnable.append((line, token))
    return runnable


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
def test_every_invocation_supplies_the_required_positional_arguments(path: Path):
    """A command whose required positional argument a skill omits exits 64 for
    the user, exactly as if the flag had been renamed.

    This half of the gate was missing when `aggregate --manifest ... -o ...`
    shipped in a skill without its required `REPORTS_DIR` operand: checking
    only command names and option names cannot see it.
    """
    offenders: list[str] = []
    for line, invocation in _runnable_invocations(path):
        words = invocation.split()[1:]
        command_path: tuple[str, ...] = ()
        bare = [w for w in words if not w.startswith("-")]
        for size in (2, 1):
            if tuple(bare[:size]) in COMMAND_PATHS:
                command_path = tuple(bare[:size])
                break
        if not command_path:
            continue
        command = COMMAND_OBJECTS[command_path]
        operands = _operands(command, words)[len(command_path) :]
        required = _required_argument_names(command)
        if len(operands) < len(required):
            offenders.append(
                f"{path.relative_to(REPO)}:{line}: `abicheck "
                f"{' '.join(command_path)}` requires {len(required)} positional "
                f"argument(s) ({', '.join(required).upper()}) but the example "
                f"supplies {len(operands)}"
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

#: Every live `ChangeKind` value. A skill naming a change kind
#: (`runtime_floor_raised`) is citing real product vocabulary, not a report
#: field — but it rots exactly the same way when a kind is renamed, so it is
#: checked against the live enum rather than waved through by an allowlist.
CHANGE_KIND_VALUES = {kind.value for kind in ChangeKind}

#: snake_case identifiers that belong to another vocabulary entirely — a
#: platform's own binary-format fields — and so are not report-field
#: references despite matching the candidate shape below. Kept explicit and
#: minimal, the same way FOREIGN_LONG_OPTIONS is: the cost of a wrong entry
#: here is a real field reference going unchecked.
#: Extensions that make a dotted token a *filename* rather than a report field
#: path — `summary.json`, `targets.json`, `.abicheck.yml`. Matched as a suffix
#: rather than by name so a newly-cited artifact needs no allowlist entry.
_FILENAME_SUFFIXES = (".json", ".yml", ".yaml", ".md", ".so", ".dylib", ".dll")

NON_REPORT_IDENTIFIERS = frozenset(
    {
        # Mach-O LC_ID_DYLIB load-command fields, discussed by the
        # SONAME/versioning reference because they are the macOS counterpart
        # of an ELF SONAME.
        "compatibility_version",
        "current_version",
        # Illustrative plugin entrypoint symbol names, used throughout the
        # consumer skill's plugin branch. They are symbols a host declares,
        # not report fields — the `_` is what makes them look like one.
        "plugin_init",
        "plugin_shutdown",
    }
)

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
    class of silent rot as a renamed field. `ChangeKind` values are accepted
    the same way and for the same reason — a skill naming `runtime_floor_raised`
    must fail here if that kind is ever renamed.
    """
    offenders: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in _INLINE_RE.finditer(text):
        token = match.group(1).strip()
        if not _FIELD_TOKEN_RE.match(token):
            continue
        if "_" not in token and "." not in token and "[]" not in token:
            continue
        if token in NON_REPORT_IDENTIFIERS:
            continue
        if token.endswith(_FILENAME_SUFFIXES):
            # A filename, not a dotted field path — `summary.json`,
            # `targets.json`, `.abicheck.yml`. The dot that makes it look like
            # a path is a file extension.
            continue
        if "." in token or "[]" in token:
            ok = _resolve_path(token)
        else:
            ok = (
                token in SCHEMA_PROPERTY_NAMES
                or token in SCHEMA_ENUM_VALUES
                or token in CHANGE_KIND_VALUES
            )
        if not ok:
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(REPO)}:{line}: {token!r} is not a field or "
                "enum value in the live compare-report schema, nor a live "
                "ChangeKind"
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
    # The escape hatch must stay narrow: nothing in it may shadow a real
    # report field, or a genuine reference would go unchecked.
    assert NON_REPORT_IDENTIFIERS.isdisjoint(SCHEMA_PROPERTY_NAMES)
    assert "profile_mismatch" in SCHEMA_ENUM_VALUES
    assert "runtime_floor_raised" in CHANGE_KIND_VALUES
    # The three vocabularies must stay distinct sources, not one merged blob:
    # a token accepted only because some *other* registry happens to contain
    # it would defeat the point.
    assert CHANGE_KIND_VALUES - SCHEMA_PROPERTY_NAMES
    assert "--used-by" in ALL_OPTIONS
    assert "--no-scope-public-headers" in ALL_OPTIONS  # a Click flag pair's other half
    assert ("project", "validate") in COMMAND_PATHS
    # The positional-argument check must actually know `aggregate` needs one,
    # and must not miscount an option's value as an operand.
    assert _required_argument_names(COMMAND_OBJECTS[("aggregate",)]) == ["reports_dir"]
    assert _operands(
        COMMAND_OBJECTS[("aggregate",)],
        "aggregate --manifest t.json --format json reports/".split(),
    ) == ["aggregate", "reports/"]


# ---------------------------------------------------------------------------
# Emitted-output drift: a field can exist in the schema and still never appear
# ---------------------------------------------------------------------------

#: Report fields the schema defines but a direct `abicheck compare` never
#: emits — the GitHub Action's `check-target` envelope populates them.
#: Verified empirically by `_real_reports` below, not asserted from docs.
INTEGRATION_ONLY_FIELDS = frozenset(
    {
        "requested_depth",
        "effective_depth",
        "compatibility_verdict",
        "policy_gate_decision",
    }
)


def _sentences(text: str) -> list[str]:
    """Sentences, with Markdown table rows kept whole.

    A table row is one unit — its cells state a single claim together (a
    "which fields" cell and the "present when" cell that qualifies it), so
    splitting per cell would separate a listing from its own explanation.
    Ordinary prose is split on sentence boundaries after unwrapping the hard
    line breaks these fragments are authored with.
    """
    units: list[str] = []
    prose: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            units.append(line)
        else:
            prose.append(line)
    joined = " ".join(prose)
    units.extend(re.split(r"(?<=[.!?])\s+", joined))
    return units


def _emitted_top_level_fields(tmp_path) -> set[str]:
    """Top-level keys a real `compare --format json` emits, unioned across the
    invocations the skills actually document.

    Schema membership is not enough: `test_every_cited_report_field_still_exists`
    passes for a field that exists in `compare_report.schema.json` but that no
    `compare` run ever produces, which is exactly how the skills came to tell
    an agent to read four fields it could never find.
    """
    import json

    from click.testing import CliRunner

    from abicheck.model import AbiSnapshot, Function
    from abicheck.serialization import save_snapshot

    def _snapshot(names):
        snap = AbiSnapshot(library="libdemo.so", version="1.0")
        snap.functions = [
            Function(name=n, mangled=n, return_type="void") for n in names
        ]
        return snap

    old = tmp_path / "old.abi.json"
    new = tmp_path / "new.abi.json"
    save_snapshot(_snapshot(["kept", "removed"]), old)
    save_snapshot(_snapshot(["kept"]), new)

    variants = (
        [],
        ["--severity-preset", "default"],
        ["--contract", "public"],
        ["--report-mode", "root-cause"],
        ["--scope-public-headers"],
    )
    runner = CliRunner()
    emitted: set[str] = set()
    for index, extra in enumerate(variants):
        out = tmp_path / f"r{index}.json"
        runner.invoke(
            cli_main,
            ["compare", str(old), str(new), "--format", "json", "-o", str(out), *extra],
        )
        if out.is_file():
            emitted |= set(json.loads(out.read_text(encoding="utf-8")))
    assert emitted, "no report was produced — the probe itself is broken"
    return emitted


def test_integration_only_fields_really_are_never_emitted(tmp_path):
    """Pins the claim the skills now make, against real output rather than
    prose: these four are schema members a direct `compare` never produces."""
    emitted = _emitted_top_level_fields(tmp_path)
    assert emitted & INTEGRATION_ONLY_FIELDS == set()
    # ...and the fields the skills route agents to instead really are there.
    assert {
        "verdict",
        "evidence_tier",
        "evidence_tiers",
        "coverage_warnings",
    } <= emitted


def test_no_skill_tells_an_agent_to_read_an_integration_only_field(tmp_path):
    """The defect this test exists for: instructing an agent to read a field
    that is always absent makes the instruction unfollowable, and invites
    reading its absence as "nothing to report" — a false green.

    A skill may still *name* such a field to explain that it is Action-only;
    what it may not do is list one among the fields to read. The two are
    separated per *sentence* (and per table row), and only by naming the
    real mechanism — `check-target` or "integration-only". A looser marker
    set was tried first and proved gameable: a neighbouring sentence
    containing the word "never" masked a genuine `effective_depth`
    instruction two lines away.
    """
    emitted = _emitted_top_level_fields(tmp_path)
    never_emitted = INTEGRATION_ONLY_FIELDS - emitted
    markers = ("check-target", "integration-only")
    offenders: list[str] = []
    for path in SKILL_FILES:
        for unit in _sentences(path.read_text(encoding="utf-8")):
            cited = sorted(f for f in never_emitted if f"`{f}`" in unit)
            if cited and not any(m in unit.lower() for m in markers):
                offenders.append(
                    f"{path.relative_to(REPO)}: cites {cited} without naming the "
                    f"check-target envelope that alone emits it — in: {unit.strip()!r}"
                )
    assert offenders == []


def test_cited_aggregate_invocations_satisfy_its_required_option_group(tmp_path):
    """`aggregate` requires one of `--manifest`/`--run-plan`, or an
    explicit `--discovered-only`, and enforces it **in the command body** —
    not in the Click parser. So neither the option-name check nor the
    positional-argument check above can see a violation: both passed while the
    documented example exited 64 with "no expected-target set".

    Running the real invocation is the only thing that catches this class, so
    that is what this does. Scoped to `aggregate` deliberately: it is the one
    command the skills drive that gates on an option *group* rather than on
    individual parameters, and a speculative general mechanism would have to
    execute every cited command against fabricated operands.
    """
    from click.testing import CliRunner

    reports = tmp_path / "reports"
    reports.mkdir()
    runner = CliRunner()
    offenders: list[str] = []

    for path in SKILL_FILES:
        for line, invocation in _runnable_invocations(path):
            words = invocation.split()[1:]
            if not words or words[0] != "aggregate":
                continue
            argv = []
            for word in words:
                # Substitute the real, empty reports dir for the operand; the
                # manifest/expect values stay as written so a missing mode is
                # still a missing mode.
                argv.append(str(reports) if word.endswith("/") else word)
            result = runner.invoke(cli_main, argv)
            output = (result.output or "") + str(result.exception or "")
            if "no expected-target set" in output or "Missing argument" in output:
                offenders.append(
                    f"{path.relative_to(REPO)}:{line}: `{invocation}` fails "
                    f"`aggregate`'s own usage check: {output.strip().splitlines()[-1]}"
                )
    assert offenders == []


#: Options whose value `compare --help` documents as applying to *both* sides
#: unless an `old=`/`new=` prefix scopes it.
_TWO_SIDED_OPTIONS = ("--header", "-H", "--include", "-I")


def test_no_runnable_compare_example_leaves_a_two_sided_option_unscoped():
    """A bare `--header`/`--include` in a `compare` example is a false-green
    mechanism, not a style nit.

    Both options apply to *both* sides unless prefixed. When the two operands
    come from different revisions, an unscoped value parses both artifacts
    against one revision's headers — so a changed signature or layout appears
    identically on each side and cancels out, and the run reports compatible.
    An unscoped `--include` reintroduces the same masking one level down, by
    letting the old header resolve its own `#include`s out of the new tree.

    Enforced mechanically because this class of defect kept reappearing in a
    sibling file after each hand-correction: the same wrong command was
    written several ways across four files, and grepping the phrasing of one
    did not find the others.
    """
    offenders: list[str] = []
    for path in SKILL_FILES:
        for line, invocation in _runnable_invocations(path):
            words = invocation.split()
            if len(words) < 2 or words[1] != "compare":
                continue
            for index, word in enumerate(words):
                if word not in _TWO_SIDED_OPTIONS:
                    continue
                value = words[index + 1] if index + 1 < len(words) else ""
                if not value.startswith(("old=", "new=", "old:", "new:")):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{line}: `{word} {value}` "
                        "applies to both sides — scope it with 'old='/'new='"
                    )
    assert offenders == []
