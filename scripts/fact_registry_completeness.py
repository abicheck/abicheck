#!/usr/bin/env python3
"""Real, repo-wide scan enforcing ADR-063 D7's fact/capability registry
(`abicheck/model/fact_registry.py`, Phase 5,
docs/contribute/plans/one-semantic-pipeline.md).

A leaf module imported by ``check_ai_readiness.py``, mirroring
``fact_field_readers.py``'s own extraction/registration pattern
(``check_ai_readiness.py`` is already past this repo's 2000-line
AI-readiness hard cap and only stays green through ``LARGE_FILE_ALLOWLIST``,
not a license to keep growing it).

**What this checks, and why each direction matters.** The Phase 5 plan's
own Tests section states three directions a completeness check must cover,
each closing a different way a registry could go silently stale:

1. Every ``Fact[T]``-typed model field (a ``<field>_fact`` sibling on a
   ``@dataclass`` under ``abicheck/model/``) has **exactly one**
   ``FactDefinition`` entry in ``FACT_REGISTRY`` — a converted field with no
   registry entry is exactly the "shipped the type, skipped the
   declaration" gap D7 exists to close.
2. Every registry entry names a real, still-existing ``<field>_fact``
   sibling on its declared ``owner`` — a stale entry (the field was
   renamed or removed) is caught here rather than silently describing
   nothing.
3. Every field that is *eligible* for conversion (per
   ``fact_registry.REFERENCE_FLAG_COVERAGE``'s case-(a) flag-backed
   inventory, or this scan's own case-(b) tri-state-annotation-plus-
   documented-ambiguity heuristic) but not yet converted must be named,
   explicitly, in ``fact_registry.KNOWN_UNCONVERTED_ELIGIBLE_FACTS`` — the
   identical allowlist-and-shrink discipline
   ``fact_field_readers.KNOWN_UNMIGRATED_READERS``/
   ``check_ai_readiness.IMPORT_CYCLE_ALLOWLIST`` already establish
   elsewhere in this codebase. A field the scan finds eligible but that is
   in *neither* set fails outright — the "field nobody has touched yet"
   case the plan's Design section names as the actual failure mode
   Phase 0's own history (PR #753's missing registry entry, invisible to
   every test that only audits fields already known to the registry) shows
   a registry-only check cannot catch. The allowlist itself must also stay
   honest: an entry naming a field that no longer exists, or that has
   since been converted, is a stale baseline entry and fails too — shrink
   it rather than leaving dead weight (the same rule
   ``IMPORT_CYCLE_ALLOWLIST`` states for itself in AGENTS.md).
4. Every registry entry with ``persisted=True`` is actually reachable from
   both ``storage/fact_codec.py``'s encode path (``encode_fact_fields``)
   and its decode path (``decode_record_facts``/a same-shaped
   ``decode_fact(...)`` call in ``serialization.py`` for the one
   non-``RecordType``-owned field, ``Param.is_va_list_fact``) — closing a
   real gap a Codex review round found directions 1-2 above miss entirely:
   they only compare model attribute *names* against registry keys, so a
   registry entry claiming ``persisted=True`` with no matching encode/
   decode wiring at all would still pass. Textual (the exact ``fact_attr``
   string literal must appear in the combined source of both files), not a
   full data-flow proof — the same "no type inference, textual signal"
   stance this scan's own case-(b) heuristic already takes — but real: it
   fails on the exact scenario the review named (a new ``Fact[T]``
   sibling + registry entry landing with no ``fact_codec.py`` change).

**Case (b) heuristic, stated precisely.** A field is a case-(b) candidate
when its annotation textually contains ``| None`` (or ``Optional[``) *and*
one of a fixed set of marker phrases appears in a comment line
immediately preceding its declaration (within the same class body) — the
same textual signal ``docs/AGENTS.md``'s own in-progress-status sweep and
``fact_field_readers.py``'s own scan both already rely on for "no type
inference, pure textual/structural signal, verified against real
call sites" scans in this codebase. This is deliberately narrower than
"every ``Optional`` field in ``model/``" — an ``Optional`` field with no
adjacent backend/schema-dependence comment is not claimed eligible here,
since nothing about its own declaration states an availability ambiguity
(the plan's own worked examples — ``RecordType.is_final``,
``Function.contract_attributes``, ``Variable.alignment_bits`` — all carry
exactly this kind of comment). A plain enum-typed field with no
``Optional`` shape (e.g. ``Variable.access``) is never found by this
heuristic at all — it can only be flagged via case (a)'s flag-backed
inventory, which is exactly why ``REFERENCE_FLAG_COVERAGE`` is checked
first and independently of the annotation-shape heuristic below.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"
MODEL_DIR = PKG / "model"

#: Marker phrases this codebase already uses, verbatim, to document a
#: field's own backend/schema-dependent tri-state meaning (drawn directly
#: from the real comments on ``RecordType.is_final``/
#: ``Function.contract_attributes``/``Variable.alignment_bits`` and every
#: sibling field the Phase 5 plan's own inventory names) — case-insensitive
#: substring match against the comment lines immediately preceding a
#: field's declaration.
_CASE_B_MARKERS: tuple[str, ...] = (
    "tri-state",
    "does not know",
    "could not determine",
    "not captured",
    "not collected",
    "dumper/loader",
    "older snapshot",
    "legacy snapshot",
)


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        # scan_model_dataclasses()/_model_fact_siblings() accept any
        # model_dir for testability (see tests/test_fact_registry_
        # completeness.py's synthetic-fixture tests) -- a path outside
        # ROOT (a tmp_path fixture) has no repo-relative spelling.
        return p.as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _annotation_text(node: ast.AnnAssign) -> str:
    try:
        return ast.unparse(node.annotation)
    except Exception:  # noqa: BLE001 - best-effort only
        return ""


def _is_optional_annotation(text: str) -> bool:
    return "| None" in text or "Optional[" in text


def _preceding_comment_text(lines: list[str], lineno: int, lookback: int = 40) -> str:
    """Every ``#``-comment line immediately above ``lineno`` (1-indexed), joined.

    Stops at the first non-comment, non-blank line — a field's own
    documenting comment sits directly above it with nothing else between,
    the same assumption every hand-authored field comment in this codebase
    follows (verified against every real case (b) field this module's own
    docstring names).
    """
    collected: list[str] = []
    i = lineno - 2  # 0-indexed line above the field's own (1-indexed) line
    steps = 0
    while i >= 0 and steps < lookback:
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            collected.append(stripped)
        elif not stripped:
            i -= 1
            steps += 1
            continue
        else:
            break
        i -= 1
        steps += 1
    return " ".join(reversed(collected)).lower()


def _dataclass_field_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for stmt in class_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names


def scan_model_dataclasses(
    model_dir: Path = MODEL_DIR,
) -> dict[tuple[str, str], tuple[str, int]]:
    """Every case-(b) candidate ``(owner, field)`` found under ``model/``.

    Returns ``{(owner, field): (rel_path, lineno)}`` — a field whose own
    annotation is tri-state-shaped (``X | None``/``Optional[...]``) *and*
    whose immediately-preceding comment names one of ``_CASE_B_MARKERS``,
    scanned across every ``@dataclass`` in every ``model/*.py`` module (not
    only ``entities.py``/``declarations.py`` — the plan's own review-found
    correction: eligibility isn't restricted to any one filename pattern).
    """
    found: dict[tuple[str, str], tuple[str, int]] = {}
    for path in sorted(model_dir.glob("*.py")):
        rel = _rel(path)
        source = _read(path)
        if not source:
            continue
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
                )
                for d in node.decorator_list
            ):
                continue
            owner = node.name
            field_names = _dataclass_field_names(node)
            for stmt in node.body:
                if not (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                ):
                    continue
                field_name = stmt.target.id
                if field_name.endswith("_fact"):
                    continue
                if f"{field_name}_fact" in field_names:
                    continue  # already converted
                annotation = _annotation_text(stmt)
                if not _is_optional_annotation(annotation):
                    continue
                comment = _preceding_comment_text(lines, stmt.lineno)
                if any(marker in comment for marker in _CASE_B_MARKERS):
                    found[(owner, field_name)] = (rel, stmt.lineno)
    return found


def _model_fact_siblings() -> dict[tuple[str, str], str]:
    """Every real ``<field>_fact`` sibling declared under ``model/``.

    Returns ``{(owner, field): rel_path}`` (``field`` without the
    ``_fact`` suffix, matching ``FactDefinition.field``) — the ground
    truth "already converted" set, read via AST rather than importing
    every dataclass's live fields, so this stays independent of whether
    ``abicheck`` happens to import cleanly in the running environment.
    """
    siblings: dict[tuple[str, str], str] = {}
    for path in sorted(MODEL_DIR.glob("*.py")):
        rel = _rel(path)
        source = _read(path)
        if not source:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
                )
                for d in node.decorator_list
            ):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id.endswith("_fact")
                ):
                    field_name = stmt.target.id[: -len("_fact")]
                    siblings[(node.name, field_name)] = rel
    return siblings


#: Files that together carry every existing encode/decode wiring for a
#: persisted ``Fact[T]`` sibling — ``storage/fact_codec.py`` owns the
#: ``RecordType``-shaped ones (``_TYPE_FACT_KEYS``/``decode_record_facts``)
#: and the one hardcoded ``Param.is_va_list_fact`` encode line;
#: ``serialization.py`` owns that same field's ``decode_fact(...)`` call
#: site (Codex review: direction 4 below).
_PERSISTENCE_WIRING_FILES: tuple[Path, ...] = (
    PKG / "storage" / "fact_codec.py",
    PKG / "serialization.py",
)


def _persisted_fact_attr_occurrences() -> dict[str, int]:
    """``{fact_attr: count}`` — how many times each ``"<fact_attr>"`` string
    literal appears across :data:`_PERSISTENCE_WIRING_FILES`.

    Purely textual (a quoted-string count, not a data-flow proof) — the
    same "no type inference" stance this module's own case-(b) heuristic
    already takes. A field with real encode + decode wiring appears at
    least twice (verified against every one of today's six persisted
    entries, each with three or four occurrences); a field with none of
    that wiring appears zero or one times.
    """
    combined = "\n".join(_read(p) for p in _PERSISTENCE_WIRING_FILES)
    counts: dict[str, int] = {}
    for match in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]*_fact)"', combined):
        name = match.group(1)
        counts[name] = counts.get(name, 0) + 1
    return counts


def check_fact_registry_completeness(f: Findings) -> None:
    """ERROR on any of the three directions this module's docstring states.

    Imports ``abicheck.model.fact_registry`` directly rather than
    re-parsing its data via AST — that module is a dependency-free leaf
    (D10) with no install-order concern the way a heavier ``abicheck``
    import might carry elsewhere in this file.
    """
    try:
        from abicheck.model.fact_registry import (
            FACT_REGISTRY,
            KNOWN_UNCONVERTED_ELIGIBLE_FACTS,
            REFERENCE_FLAG_COVERAGE,
        )
    except ImportError as exc:
        f.err(
            "fact-registry-completeness",
            f"could not import abicheck.model.fact_registry: {exc} — "
            "run `pip install -e .` first",
        )
        return

    siblings = _model_fact_siblings()  # {(owner, field): rel_path}

    # Direction 1 + 2: every Fact[T]-typed field <-> exactly one registry entry.
    registry_keys = {(e.owner, e.field) for e in FACT_REGISTRY.entries.values()}
    for owner_field, rel in siblings.items():
        if owner_field not in registry_keys:
            owner, field_name = owner_field
            f.err(
                "fact-registry-completeness",
                f"{rel}: {owner}.{field_name}_fact has no FACT_REGISTRY "
                f"entry in abicheck/model/fact_registry.py — every "
                f"Fact[T]-typed model field needs exactly one "
                f"FactDefinition (ADR-063 D7)",
            )
    for entry in FACT_REGISTRY.entries.values():
        key = (entry.owner, entry.field)
        if key not in siblings:
            f.err(
                "fact-registry-completeness",
                f"FACT_REGISTRY entry {entry.id!r} names "
                f"{entry.owner}.{entry.fact_attr}, but no such field exists "
                f"under abicheck/model/ — stale registry entry (renamed or "
                f"removed field)",
            )

    # Direction 3: eligible-but-unconverted fields must be tracked, not silent.
    case_a: set[tuple[str, str]] = set()
    for pairs in REFERENCE_FLAG_COVERAGE.values():
        case_a.update(pairs)
    case_b = scan_model_dataclasses()

    eligible_unconverted = {p for p in case_a if p not in siblings} | {
        p for p in case_b if p not in siblings
    }

    for owner, field_name in sorted(eligible_unconverted):
        if (owner, field_name) not in KNOWN_UNCONVERTED_ELIGIBLE_FACTS:
            f.err(
                "fact-registry-completeness",
                f"{owner}.{field_name} is availability-ambiguous "
                f"(flag-backed or documented tri-state) but has no "
                f"{field_name}_fact sibling and is not named in "
                f"fact_registry.KNOWN_UNCONVERTED_ELIGIBLE_FACTS — either "
                f"convert it (ADR-063 D7/Phase 5) or add it to that "
                f"allowlist as a reviewed, tracked gap",
            )

    # KNOWN_UNCONVERTED_ELIGIBLE_FACTS must shrink-only: no stale entries.
    for owner, field_name in sorted(KNOWN_UNCONVERTED_ELIGIBLE_FACTS):
        if (owner, field_name) in siblings:
            f.err(
                "fact-registry-completeness",
                f"{owner}.{field_name} is listed in "
                f"KNOWN_UNCONVERTED_ELIGIBLE_FACTS but already has a "
                f"{field_name}_fact sibling — remove the stale allowlist "
                f"entry (shrink-only, per this repo's allowlist "
                f"convention)",
            )

    # Direction 4: a persisted entry must actually be wired into
    # storage/fact_codec.py's (or serialization.py's) encode/decode path.
    occurrences = _persisted_fact_attr_occurrences()
    for entry in FACT_REGISTRY.entries.values():
        if not entry.persisted:
            continue
        count = occurrences.get(entry.fact_attr, 0)
        if count < 2:
            f.err(
                "fact-registry-completeness",
                f"{entry.id}: registered with persisted=True, but "
                f"{entry.fact_attr!r} appears only {count} time(s) across "
                f"storage/fact_codec.py + serialization.py — a real "
                f"encode path AND a real decode path both need to "
                f"reference it (see storage/fact_codec.py's "
                f"_TYPE_FACT_KEYS/decode_record_facts, or the "
                f"Param.is_va_list_fact pattern for a non-RecordType "
                f"owner) or this snapshot field silently fails to "
                f"round-trip",
            )


if __name__ == "__main__":

    class _CLIFindings:
        def __init__(self) -> None:
            self.errors: list[tuple[str, str]] = []
            self.warnings: list[tuple[str, str]] = []

        def err(self, check: str, msg: str) -> None:
            self.errors.append((check, msg))

        def warn(self, check: str, msg: str) -> None:
            self.warnings.append((check, msg))

    findings = _CLIFindings()
    check_fact_registry_completeness(findings)
    for check, msg in findings.errors:
        print(f"ERROR [{check}] {msg}")
    for check, msg in findings.warnings:
        print(f"WARN  [{check}] {msg}")
    sys.exit(1 if findings.errors else 0)
