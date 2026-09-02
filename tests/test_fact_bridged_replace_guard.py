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

"""No ``dataclasses.replace()`` may write a ``Fact[T]``-bridged legacy field.

``model/fact.py`` documents the trap: ``replace(obj, deprecated=x)`` carries
``obj``'s *already-resolved* ``deprecated_fact`` forward, and
``__post_init__``'s bridge resolves that disagreement in the **sibling's**
favour — so the write is silently reverted, and the pair is left claiming a
confirmed fact that contradicts the caller's intent.
``replace_with_fact_sync()`` exists to close it at the one call site that
still knows what the caller meant.

**Why a scan rather than another fixed test.** ADR-063 Phase 5's case-(a)
conversions turned five existing merge paths into instances of this trap.
The first sweep found four of them by grepping for the field names — and
missed `dumper_hybrid._merge_enum_type`, because it spells the call
``replace(e, **updates)``, where no field name appears in the source at all.
A Codex review round caught it. That is the mechanism failing, not one
oversight: the next batch of conversions makes some *other* existing
``replace()`` newly unsafe, and a name-based grep cannot see the
``**kwargs`` form at all. So the rule is executable here, over both
spellings, per AGENTS.md's "fix the cause, not the instance".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from abicheck.model.fact_registry import FACT_REGISTRY

_PKG = Path(__file__).resolve().parent.parent / "abicheck"

#: Every legacy field that has a ``<field>_fact`` sibling, from the registry
#: itself — so a newly converted field is policed the moment it is
#: registered, with no second list to update.
_BRIDGED_FIELDS: frozenset[str] = frozenset(
    entry.field for entry in FACT_REGISTRY.entries.values()
)

#: ``replace(obj, <field>=...)`` sites whose *receiver* is an owner that
#: does not bridge that field, keyed ``(file, function, field)``. The scan
#: matches by field NAME, because a receiver's dataclass is a local or loop
#: variable at every one of these sites and this repo does not carry a type
#: inferencer; the same field name is bridged on one owner and plain on
#: another (``access`` on ``Variable`` but not ``TypeField``; ``default`` on
#: ``TypeField`` but not ``Param``; ``alignment_bits`` on ``Variable`` but
#: not ``RecordType``). Each entry names the real receiver type and why the
#: call is safe -- a reviewed claim, not a mute button, and the stale-entry
#: test below deletes it the moment the call site goes away.
_NAMED_CALL_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("abicheck/dwarf_snapshot.py", "_flatten_anonymous_member", "access"): (
        "Receiver is a TypeField; only Variable.access is bridged."
    ),
    ("abicheck/tu_merge.py", "_merge_functions", "default"): (
        "Receiver is a Param; only TypeField.default is bridged."
    ),
    ("abicheck/tu_merge.py", "_merge_types", "alignment_bits"): (
        "Receiver is a RecordType; only Variable.alignment_bits is bridged."
    ),
    ("abicheck/tu_merge_provenance.py", "_blank_provenance", "deprecated"): (
        "Passes every blanked field's own `<field>_fact` through `**extra`, "
        "derived from the blanked-field list."
    ),
    ("abicheck/tu_merge_provenance.py", "_blank_provenance", "source_header"): (
        "Same `**extra` sibling-blanking as `deprecated` above."
    ),
    (
        "abicheck/tu_merge_provenance.py",
        "_with_more_public_provenance",
        "source_header",
    ): (
        "Followed immediately by an explicit "
        "`replace(merged, source_header_fact=other.source_header_fact)` in "
        "the same function -- the sibling is swapped in a second step this "
        "scan cannot see from one call node."
    ),
}

#: ``replace(obj, **mapping)`` call sites reviewed and found safe, with the
#: reason each is safe. Allowlist-and-shrink, the same convention
#: ``IMPORT_CYCLE_ALLOWLIST``/``KNOWN_UNMIGRATED_READERS`` use: an entry
#: here is a claim someone checked, not a way to silence the scan.
_STARRED_CALL_ALLOWLIST: dict[tuple[str, str], str] = {
    ("abicheck/model/fact.py", "replace_with_fact_sync"): (
        "This *is* the fact-syncing wrapper — it derives each bridged "
        "field's sibling into the same call."
    ),
    ("abicheck/api_types.py", "replace"): (
        "CompareRequest/DumpRequest.replace(): typed request objects, no "
        "Fact[T]-bridged field on either."
    ),
    ("abicheck/pack_application.py", "apply_to_compare_config"): (
        "Replaces on SeverityConfig, which carries no Fact[T] field."
    ),
    ("abicheck/qualified_name_segments_walk.py", "_walk_rewrite_strings"): (
        "Generic closure-marker walk. It rewrites a mutable dataclass by "
        "setattr and recurses into the Fact sibling itself (see that "
        "module's own is_fact_value_field branch), so both halves are "
        "rewritten together; the `replace()` here only runs for a FROZEN "
        "dataclass, and no Fact[T]-bearing model dataclass is frozen."
    ),
    ("abicheck/extract/semantic_ir_merge.py", "_merge_entity"): (
        "ADR-063 Phase 6's SemanticEntity holds its Fact[...] fields "
        "DIRECTLY -- there is no legacy/sibling pair on it, so there is no "
        "bridge to revert: `updates` maps a field name to the Fact object "
        "itself. (This entry is the guard's first catch on code it did not "
        "grow up with: the call arrived from main while this PR was open, "
        "and the scan flagged it for review rather than letting a `**` "
        "spelling through unseen.)"
    ),
    ("abicheck/tu_merge_provenance.py", "_blank_provenance"): (
        "Passes each blanked field's own `<field>_fact` explicitly, "
        "derived from the blanked-field list rather than named one by one."
    ),
}


def _module_paths() -> list[Path]:
    return sorted(_PKG.rglob("*.py"))


def _rel(path: Path) -> str:
    """Repo-relative path with **forward slashes on every platform**.

    ``str(Path)`` renders ``abicheck\\tu_merge.py`` on Windows, which matches
    no key in either allowlist below — so every allowlisted call site read as
    unreviewed and all four tests failed there while passing on Linux (real
    CI failure, `unit-tests (windows-latest, 3.13)`). The allowlists are
    written with POSIX separators because that is how this repo names files
    everywhere else, so the normalization belongs here rather than in
    twelve hand-written keys.
    """
    return path.relative_to(_PKG.parent).as_posix()


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str:
    best = "<module>"
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(candidate, "end_lineno", None)
        if end is None or not hasattr(node, "lineno"):
            continue
        if candidate.lineno <= node.lineno <= end:
            best = candidate.name
    return best


def _is_replace_call(node: ast.AST) -> bool:
    """A ``replace(...)`` or ``<mod>.replace(...)`` call, but not our wrapper."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "replace"
    if isinstance(func, ast.Attribute):
        return func.attr == "replace" and not isinstance(func.value, ast.Attribute)
    return False


def _named_violations() -> list[tuple[str, int, str, list[str]]]:
    """``replace(obj, <bridged>=...)`` without the matching ``<bridged>_fact``."""
    found: list[tuple[str, int, str, list[str]]] = []
    for path in _module_paths():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - the package always parses
            continue
        for node in ast.walk(tree):
            if not _is_replace_call(node):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            unsynced = sorted(
                name
                for name in kwargs & _BRIDGED_FIELDS
                if f"{name}_fact" not in kwargs
            )
            if not unsynced:
                continue
            rel = _rel(path)
            func = _enclosing_function(tree, node)
            unreviewed = [
                name
                for name in unsynced
                if (rel, func, name) not in _NAMED_CALL_ALLOWLIST
            ]
            if unreviewed:
                found.append((rel, node.lineno, func, unreviewed))
    return found


def _starred_calls() -> list[tuple[str, int, str]]:
    """``replace(obj, **mapping)`` — the spelling a name-based grep can't see."""
    found: list[tuple[str, int, str]] = []
    for path in _module_paths():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not _is_replace_call(node):
                continue
            if any(kw.arg is None for kw in node.keywords):
                rel = _rel(path)
                found.append((rel, node.lineno, _enclosing_function(tree, node)))
    return found


class TestNoUnsyncedReplace:
    def test_no_replace_writes_a_bridged_field_without_its_fact_sibling(self) -> None:
        violations = _named_violations()
        assert not violations, (
            "dataclasses.replace() writes a Fact[T]-bridged legacy field "
            "without its sibling — the bridge will silently revert it. Use "
            f"replace_with_fact_sync(): {violations}"
        )

    def test_every_starred_replace_is_reviewed(self) -> None:
        unreviewed = [
            call
            for call in _starred_calls()
            if (call[0], call[2]) not in _STARRED_CALL_ALLOWLIST
        ]
        assert not unreviewed, (
            "replace(obj, **mapping) reaches a dataclass this scan cannot "
            "inspect by keyword name — the exact spelling that hid "
            "_merge_enum_type's reverted backfill. Either use "
            "replace_with_fact_sync() or add a reviewed reason to "
            f"_STARRED_CALL_ALLOWLIST: {unreviewed}"
        )

    def test_the_allowlist_has_no_stale_entries(self) -> None:
        live = {(path, func) for path, _line, func in _starred_calls()}
        stale = sorted(set(_STARRED_CALL_ALLOWLIST) - live)
        assert not stale, f"allowlist entries naming no real call site: {stale}"

    def test_the_named_allowlist_has_no_stale_entries(self) -> None:
        live: set[tuple[str, str, str]] = set()
        for path in _module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = _rel(path)
            for node in ast.walk(tree):
                if not _is_replace_call(node):
                    continue
                kwargs = {kw.arg for kw in node.keywords if kw.arg}
                func = _enclosing_function(tree, node)
                for name in kwargs & _BRIDGED_FIELDS:
                    if f"{name}_fact" not in kwargs:
                        live.add((rel, func, name))
        stale = sorted(set(_NAMED_CALL_ALLOWLIST) - live)
        assert not stale, (
            "allowlisted call sites that no longer exist (or that now sync "
            f"their sibling) — shrink the list: {stale}"
        )


class TestTheScanActuallyCatchesTheBug:
    """The scan must fail on the real defect, not pass vacuously."""

    @pytest.mark.parametrize(
        "source,detector,expected",
        [
            (
                "from dataclasses import replace\n"
                "def f(e, v):\n"
                "    return replace(e, deprecated=v)\n",
                "named",
                True,
            ),
            (
                "from dataclasses import replace\n"
                "def f(e, v):\n"
                "    return replace(e, deprecated=v, deprecated_fact=v2)\n",
                "named",
                False,
            ),
            (
                "from dataclasses import replace\n"
                "def f(e, updates):\n"
                "    return replace(e, **updates)\n",
                "starred",
                True,
            ),
        ],
    )
    def test_detectors_flag_the_shapes_they_claim_to(
        self, source: str, detector: str, expected: bool, tmp_path: Path
    ) -> None:
        tree = ast.parse(source)
        flagged = False
        for node in ast.walk(tree):
            if not _is_replace_call(node):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if detector == "named":
                flagged |= bool(
                    {
                        name
                        for name in kwargs & _BRIDGED_FIELDS
                        if f"{name}_fact" not in kwargs
                    }
                )
            else:
                flagged |= any(kw.arg is None for kw in node.keywords)
        assert flagged is expected

    def test_the_real_merge_enum_type_call_site_is_no_longer_a_bare_replace(
        self,
    ) -> None:
        # The specific regression, kept alongside the general rule: this is
        # the call site whose `**updates` spelling the first sweep missed.
        source = (_PKG / "dumper_hybrid.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_merge_enum_type":
                calls = {
                    getattr(c.func, "id", None)
                    for c in ast.walk(node)
                    if isinstance(c, ast.Call)
                }
                assert "replace_with_fact_sync" in calls
                assert "replace" not in calls
                return
        raise AssertionError("_merge_enum_type not found in dumper_hybrid.py")
