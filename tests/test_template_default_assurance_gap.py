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

"""ADR-085: a header-only template-default change reads the same on both backends.

Invariant: for every template family (class, function, alias, variable) and every
parameter kind (type, non-type, template-template), a header pair whose
default is added, removed or changed gives the same answer on castxml and
clang: ``template_param_default_changed``, or -- for a default only added to a
class, alias or variable template -- ``NO_CHANGE``, or, for a default no AST
walk can spell, ``partial`` with a named template-defaults gap. A change is
never read as ``NO_CHANGE`` with ``complete`` assurance. The oracle is the
header text table below, written by hand per row, not derived from abicheck.
Both backends read the defaults from the clang AST of the header (castxml
through its clang join); ``tests/test_template_default_header_backends.py``
covers that join's failure path and checks the outcomes against the compiler.

The fast tests drive the assurance rollup with hand-built snapshots; the
``integration`` tests run the public ``abicheck dump``/``compare`` CLI on
castxml and clang.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from abicheck.model import AbiSnapshot, Fact, Function
from abicheck.model.identity import EntityId
from tests._abicheck_subprocess import abicheck_command
from tests._assurance_gap_views import template_default_gap

_GAP = "template-parameter defaults"


def _fn(leaf: str, fact: Fact[Any] | None) -> Function:
    return Function(
        name=leaf,
        mangled="",
        return_type="void",
        template_param_defaults_fact=fact,
        entity_id=EntityId(scope=(), kind="function", leaf_name=leaf, extra=()),
    )


def _snap(*functions: Function) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.ast_producer = "clang"
    snap.declarations.functions.extend(functions)
    return snap


# (fact, the reason the gap note names, or None when it is not a gap)
_FACTS: list[tuple[Fact[Any] | None, str | None]] = [
    (None, None),  # ordinary function
    (Fact.not_collected(), None),  # ordinary function, explicit
    (Fact.present(("int",)), None),  # recorded pattern
    (Fact.present((None,)), None),  # recorded pattern, no default
    (Fact.unsupported("unspelled"), "a default could not be spelled"),
    (Fact.failed("boom"), "their extraction failed"),
]


@pytest.mark.parametrize(("fact", "reason"), _FACTS)
@pytest.mark.parametrize("side", ["old", "new"])
def test_function_pattern_without_recorded_defaults_is_a_named_gap(
    fact: Fact[Any] | None, reason: str | None, side: str
) -> None:
    clean = _snap(_fn("f", Fact.present(("int",))))
    probe = _snap(_fn("f", fact))
    old, new = (probe, clean) if side == "old" else (clean, probe)
    gap, notes = template_default_gap(old, new)
    assert gap is (reason is not None)
    hits = [n for n in notes if n.startswith(_GAP) and "function template" in n]
    assert len(hits) == (0 if reason is None else 1)
    if reason is not None:
        assert f"the {side} snapshot" in hits[0]
        assert f"({reason})" in hits[0]


def test_unspellable_and_failed_patterns_get_separate_notes() -> None:
    snap = _snap(_fn("f", Fact.unsupported("x")), _fn("g", Fact.failed("y")))
    _, notes = template_default_gap(None, snap)
    assert sorted(notes) == sorted(
        [
            n
            for n in notes
            if "(a default could not be spelled): 1 function" in n
            or "(their extraction failed): 1 function" in n
        ]
    )
    assert len(notes) == 2


def test_redeclarations_of_one_unrecorded_pattern_count_once() -> None:
    snap = _snap(
        _fn("f", Fact.unsupported("x")),
        _fn("f", Fact.unsupported("x")),
        _fn("g", Fact.unsupported("x")),
    )
    _, notes = template_default_gap(None, snap)
    (note,) = notes
    assert "2 function template pattern(s)" in note


# --------------------------------------------------------------------------
# Public CLI, real backends.
# --------------------------------------------------------------------------

_HEADER = "#pragma once\nnamespace m {{\ntemplate <class> struct Q {{}};\ntemplate <class> struct R {{}};\n{body}\n}}\n"

# The expected outcome of a row on BOTH backends -- written by hand from the
# C++ rules, not derived from abicheck. Both backends read every family's
# defaults from the clang AST of the header (castxml through its clang join),
# so the same header gives the same answer.
#
# REPORT: ``template_param_default_changed`` (code written against the old
#   header silently gets another type/value, or stops compiling).
# CLEAN: ``NO_CHANGE``, no template-defaults gap -- a default only *added* to a class,
#   alias or variable template: every spelling valid before named the argument
#   and keeps its meaning (case224). A function template's added default is
#   REPORT (it makes the template viable for a call that omits it).
# GAP: ``NO_CHANGE``, ``partial``, the gap named -- a default the AST walk
#   cannot spell (``sizeof(T)``), so the change is not assessed.
REPORT, CLEAN, GAP = "report", "clean", "gap"

# (id, old body, new body, outcome). Every row changes, adds or removes one default.
_CHANGED = [
    (
        "class_type_changed",
        "template <class T, class U = T> struct Box { T t; };",
        "template <class T, class U = int> struct Box { T t; };",
        REPORT,
    ),
    (
        "class_type_added",
        "template <class T, class U> struct Box { T t; };",
        "template <class T, class U = int> struct Box { T t; };",
        CLEAN,
    ),
    (
        "class_type_removed",
        "template <class T, class U = int> struct Box { T t; };",
        "template <class T, class U> struct Box { T t; };",
        REPORT,
    ),
    (
        "class_nontype_changed",
        "template <class T, int N = 1> struct Box { T t; };",
        "template <class T, int N = 2> struct Box { T t; };",
        REPORT,
    ),
    (
        "class_template_template_changed",
        "template <class T, template <class> class C = Q> struct Box { T t; };",
        "template <class T, template <class> class C = R> struct Box { T t; };",
        REPORT,
    ),
    (
        "function_type_changed",
        "template <class T = int> void f() {}",
        "template <class T = char> void f() {}",
        REPORT,
    ),
    (
        "function_nontype_changed",
        "template <class T, int N = 1> void f(T) {}",
        "template <class T, int N = 2> void f(T) {}",
        REPORT,
    ),
    (
        "function_unspelled_changed",
        "template <class T, int N = sizeof(T)> void f(T);",
        "template <class T, int N = sizeof(T) + 1> void f(T);",
        GAP,
    ),
    (
        "alias_type_changed",
        "template <class T, class U = T> using P = Q<U>;",
        "template <class T, class U = int> using P = Q<U>;",
        REPORT,
    ),
    (
        "alias_type_added",
        "template <class T, class U> using P = Q<U>;",
        "template <class T, class U = int> using P = Q<U>;",
        CLEAN,
    ),
    (
        "alias_type_removed",
        "template <class T, class U = int> using P = Q<U>;",
        "template <class T, class U> using P = Q<U>;",
        REPORT,
    ),
    (
        "alias_nontype_changed",
        "template <class T, int N = 1> using P = Q<T>;",
        "template <class T, int N = 2> using P = Q<T>;",
        REPORT,
    ),
    (
        "alias_nontype_added",
        "template <class T, int N> using P = Q<T>;",
        "template <class T, int N = 2> using P = Q<T>;",
        CLEAN,
    ),
    (
        "alias_nontype_removed",
        "template <class T, int N = 2> using P = Q<T>;",
        "template <class T, int N> using P = Q<T>;",
        REPORT,
    ),
    (
        "alias_template_template_changed",
        "template <class T, template <class> class C = Q> using P = Q<C<T>>;",
        "template <class T, template <class> class C = R> using P = Q<C<T>>;",
        REPORT,
    ),
    (
        "alias_template_template_added",
        "template <class T, template <class> class C> using P = Q<C<T>>;",
        "template <class T, template <class> class C = R> using P = Q<C<T>>;",
        CLEAN,
    ),
    (
        "alias_template_template_removed",
        "template <class T, template <class> class C = Q> using P = Q<C<T>>;",
        "template <class T, template <class> class C> using P = Q<C<T>>;",
        REPORT,
    ),
    (
        "variable_type_changed",
        "template <class T, class U = T> constexpr int V = sizeof(U);",
        "template <class T, class U = int> constexpr int V = sizeof(U);",
        REPORT,
    ),
    (
        "variable_type_added",
        "template <class T, class U> constexpr int V = sizeof(U);",
        "template <class T, class U = int> constexpr int V = sizeof(U);",
        CLEAN,
    ),
    (
        "variable_type_removed",
        "template <class T, class U = int> constexpr int V = sizeof(U);",
        "template <class T, class U> constexpr int V = sizeof(U);",
        REPORT,
    ),
    (
        "variable_nontype_changed",
        "template <class T, int N = 1> constexpr int V = sizeof(T);",
        "template <class T, int N = 2> constexpr int V = sizeof(T);",
        REPORT,
    ),
    (
        "variable_nontype_added",
        "template <class T, int N> constexpr int V = sizeof(T);",
        "template <class T, int N = 2> constexpr int V = sizeof(T);",
        CLEAN,
    ),
    (
        "variable_nontype_removed",
        "template <class T, int N = 2> constexpr int V = sizeof(T);",
        "template <class T, int N> constexpr int V = sizeof(T);",
        REPORT,
    ),
    (
        "variable_template_template_changed",
        "template <class T, template <class> class C = Q> constexpr int V = sizeof(C<T>);",
        "template <class T, template <class> class C = R> constexpr int V = sizeof(C<T>);",
        REPORT,
    ),
    (
        "variable_template_template_added",
        "template <class T, template <class> class C> constexpr int V = sizeof(C<T>);",
        "template <class T, template <class> class C = R> constexpr int V = sizeof(C<T>);",
        CLEAN,
    ),
    (
        "variable_template_template_removed",
        "template <class T, template <class> class C = Q> constexpr int V = sizeof(C<T>);",
        "template <class T, template <class> class C> constexpr int V = sizeof(C<T>);",
        REPORT,
    ),
]

_ALIAS_VARIABLE_UNCHANGED = (
    "template <class T, class U = int> using P = Q<U>;\n"
    "template <class T, int N = 2> constexpr int V = N;"
)
_ALIAS_VARIABLE_NO_DEFAULTS = "template <class T> using P = Q<T>;\ntemplate <class T> constexpr int V = sizeof(T);"
_UNCHANGED = "template <class T, class U = int> struct Box { T t; };\ntemplate <class T = int> void f() {}"
_NO_DEFAULTS = "template <class T> struct Box { T t; };\ntemplate <class T> void f() {}"


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")


def _compare(tmp: Path, backend: str, old_body: str, new_body: str) -> dict[str, Any]:
    cfg = tmp / "cfg.yml"
    cfg.write_text(f"compile:\n  frontend: {backend}\n  lang: c++\n")
    snaps = []
    for tag, body in (("old", old_body), ("new", new_body)):
        header = tmp / f"{tag}.h"
        header.write_text(_HEADER.format(body=body))
        out = tmp / f"{tag}.json"
        r = subprocess.run(
            abicheck_command(
                "dump",
                "-H",
                str(header),
                "--config",
                str(cfg),
                "--depth",
                "headers",
                "-o",
                str(out),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        snaps.append(out)
    report = tmp / "report.json"
    subprocess.run(
        abicheck_command(
            "compare",
            str(snaps[0]),
            str(snaps[1]),
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(report.read_text())


def _gap_notes(report: dict[str, Any]) -> list[str]:
    return [n for n in report["analysis_assurance"]["notes"] if n.startswith(_GAP)]


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize(
    ("case", "old", "new", "outcome"), _CHANGED, ids=[c[0] for c in _CHANGED]
)
def test_changed_default_reads_the_same_on_both_backends(
    tmp_path: Path, backend: str, case: str, old: str, new: str, outcome: str
) -> None:
    """Every family and parameter kind: the hand-written outcome, on castxml
    and clang alike. Never ``NO_CHANGE`` with ``complete`` assurance for a
    change that is not a pure addition."""
    _require(backend)
    report = _compare(tmp_path, backend, old, new)
    kinds = {c["kind"] for c in report["changes"]}
    status = report["analysis_assurance"]["status"]
    reported = "template_param_default_changed" in kinds
    assert reported is (outcome == REPORT), (case, report["verdict"], sorted(kinds))
    if outcome == REPORT:
        assert report["verdict"] == "API_BREAK", (case, report["verdict"])
        assert _gap_notes(report) == [], _gap_notes(report)
    elif outcome == CLEAN:
        assert report["verdict"] == "NO_CHANGE", (case, report["verdict"])
        assert _gap_notes(report) == [], _gap_notes(report)
    else:
        assert status == "partial" and _gap_notes(report), report["analysis_assurance"]


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_unchanged_default_reports_no_finding(tmp_path: Path, backend: str) -> None:
    _require(backend)
    report = _compare(tmp_path, backend, _UNCHANGED, _UNCHANGED)
    assert "template_param_default_changed" not in {
        c["kind"] for c in report["changes"]
    }
    assert report["verdict"] == "NO_CHANGE"
    # Both backends recorded every default, so neither names a gap.
    # A castxml header-only dump of a header with no non-template declaration
    # reads ``partial`` for its unresolved scope, so only this axis is checked.
    assert _gap_notes(report) == []


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_templates_without_defaults_name_no_gap(tmp_path: Path, backend: str) -> None:
    _require(backend)
    report = _compare(tmp_path, backend, _NO_DEFAULTS, _NO_DEFAULTS)
    assert report["verdict"] == "NO_CHANGE"
    assert _gap_notes(report) == []


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_unchanged_alias_variable_default_is_no_finding_and_no_gap(
    tmp_path: Path, backend: str
) -> None:
    """Negative control: an unchanged alias/variable default is no finding,
    and, now that both backends record it, no gap either."""
    _require(backend)
    report = _compare(
        tmp_path, backend, _ALIAS_VARIABLE_UNCHANGED, _ALIAS_VARIABLE_UNCHANGED
    )
    assert "template_param_default_changed" not in {
        c["kind"] for c in report["changes"]
    }
    assert report["verdict"] == "NO_CHANGE"
    # A castxml header-only dump of a header with no non-template declaration
    # reads ``partial`` for its unresolved scope, so only this axis is checked.
    assert _gap_notes(report) == []


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_alias_variable_templates_without_defaults_name_no_gap(
    tmp_path: Path, backend: str
) -> None:
    _require(backend)
    report = _compare(
        tmp_path, backend, _ALIAS_VARIABLE_NO_DEFAULTS, _ALIAS_VARIABLE_NO_DEFAULTS
    )
    assert report["verdict"] == "NO_CHANGE"
    assert _gap_notes(report) == []
