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

"""ADR-085 D3: a header-only template-default change is never a silent clean.

Invariant: for every template family (class, function, alias) and every
parameter kind (type, non-type, template-template), a header pair whose
default is added, removed or changed either reports
``template_param_default_changed`` or reads ``partial`` with a named
template-defaults gap. It never reads ``NO_CHANGE`` with ``complete``
assurance. The oracle is the header text table below, written by hand: what
each backend can observe is stated per row, not derived from abicheck.

The fast tests drive the assurance rollup with hand-built snapshots; the
``integration`` tests run the public ``abicheck dump``/``compare`` CLI on
castxml and clang.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.model import AbiSnapshot, Fact, Function
from abicheck.model.identity import EntityId
from abicheck.policy.analysis_assurance._template_defaults import (
    template_default_gap,
)

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


# (fact, counts as an unrecorded function-template pattern?)
_FACTS: list[tuple[Fact[Any] | None, bool]] = [
    (None, False),  # ordinary function
    (Fact.not_collected(), False),  # ordinary function, explicit
    (Fact.present(("int",)), False),  # recorded pattern
    (Fact.present((None,)), False),  # recorded pattern, no default
    (Fact.unsupported("unspelled"), True),
    (Fact.failed("boom"), True),
]


@pytest.mark.parametrize(("fact", "unrecorded"), _FACTS)
@pytest.mark.parametrize("side", ["old", "new"])
def test_function_pattern_without_recorded_defaults_is_a_named_gap(
    fact: Fact[Any] | None, unrecorded: bool, side: str
) -> None:
    clean = _snap(_fn("f", Fact.present(("int",))))
    probe = _snap(_fn("f", fact))
    old, new = (probe, clean) if side == "old" else (clean, probe)
    gap, notes = template_default_gap(old, new)
    assert gap is unrecorded
    hits = [n for n in notes if n.startswith(_GAP) and "function template" in n]
    assert len(hits) == (1 if unrecorded else 0)
    if unrecorded:
        assert f"the {side} snapshot" in hits[0]


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

# (id, old body, new body). Every row changes, adds or removes one default.
_CHANGED = [
    (
        "class_type_changed",
        "template <class T, class U = T> struct Box { T t; };",
        "template <class T, class U = int> struct Box { T t; };",
    ),
    (
        "class_type_added",
        "template <class T, class U> struct Box { T t; };",
        "template <class T, class U = int> struct Box { T t; };",
    ),
    (
        "class_type_removed",
        "template <class T, class U = int> struct Box { T t; };",
        "template <class T, class U> struct Box { T t; };",
    ),
    (
        "class_nontype_changed",
        "template <class T, int N = 1> struct Box { T t; };",
        "template <class T, int N = 2> struct Box { T t; };",
    ),
    (
        "class_template_template_changed",
        "template <class T, template <class> class C = Q> struct Box { T t; };",
        "template <class T, template <class> class C = R> struct Box { T t; };",
    ),
    (
        "function_type_changed",
        "template <class T = int> void f() {}",
        "template <class T = char> void f() {}",
    ),
    (
        "function_nontype_changed",
        "template <class T, int N = 1> void f(T) {}",
        "template <class T, int N = 2> void f(T) {}",
    ),
    (
        "function_unspelled_changed",
        "template <class T, int N = sizeof(T)> void f(T);",
        "template <class T, int N = sizeof(T) + 1> void f(T);",
    ),
    (
        "alias_type_changed",
        "template <class T, class U = T> using P = Q<U>;",
        "template <class T, class U = int> using P = Q<U>;",
    ),
    (
        "variable_type_changed",
        "template <class T, class U = T> constexpr int V = sizeof(U);",
        "template <class T, class U = int> constexpr int V = sizeof(U);",
    ),
]

# Known gap (docs/contribute/known-gaps.md, ADR-085): the clang backend models
# neither alias nor variable templates, so their default change is not seen
# and no gap is named. castxml's header scan covers them. The clang rows of
# these cases are a canary: they pin the gap and fail once it is closed.
_CLANG_UNMODELED = {"alias_type_changed", "variable_type_changed"}
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
            [
                sys.executable,
                "-m",
                "abicheck",
                "dump",
                "-H",
                str(header),
                "--config",
                str(cfg),
                "--depth",
                "headers",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        snaps.append(out)
    report = tmp / "report.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(snaps[0]),
            str(snaps[1]),
            "-o",
            f"json={report}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(report.read_text())


def _gap_notes(report: dict[str, Any]) -> list[str]:
    return [n for n in report["analysis_assurance"]["notes"] if n.startswith(_GAP)]


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize(("case", "old", "new"), _CHANGED, ids=[c[0] for c in _CHANGED])
def test_changed_default_is_never_a_silent_clean(
    tmp_path: Path, backend: str, case: str, old: str, new: str
) -> None:
    _require(backend)
    report = _compare(tmp_path, backend, old, new)
    kinds = {c["kind"] for c in report["changes"]}
    if backend == "clang" and case in _CLANG_UNMODELED:
        assert "template_param_default_changed" not in kinds
        assert _gap_notes(report) == []
        return
    reported = "template_param_default_changed" in kinds
    assert reported or (
        report["analysis_assurance"]["status"] == "partial" and _gap_notes(report)
    ), (case, report["verdict"], report["analysis_assurance"])
    # castxml cannot observe the default: it may only report the gap, never a finding.
    if backend == "castxml":
        assert not reported
        assert _gap_notes(report)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_unchanged_default_reports_no_finding(tmp_path: Path, backend: str) -> None:
    _require(backend)
    report = _compare(tmp_path, backend, _UNCHANGED, _UNCHANGED)
    assert "template_param_default_changed" not in {
        c["kind"] for c in report["changes"]
    }
    assert report["verdict"] == "NO_CHANGE"
    # clang recorded every default, so it names no template-defaults gap.
    if backend == "clang":
        assert _gap_notes(report) == []


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_templates_without_defaults_name_no_gap(tmp_path: Path, backend: str) -> None:
    _require(backend)
    report = _compare(tmp_path, backend, _NO_DEFAULTS, _NO_DEFAULTS)
    assert report["verdict"] == "NO_CHANGE"
    assert _gap_notes(report) == []
