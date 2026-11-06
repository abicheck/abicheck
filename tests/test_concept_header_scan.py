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

"""C++20 concepts and requires-clauses: scanned, and named when unmodelled.

Invariant: a castxml or clang run over public headers that declare a concept
or a ``requires`` constraint records the scan; a side that did not model the
constraints (castxml) reads ``partial`` with the concept dimension named,
while a clang side that modelled them names no gap (the CLI test below); a
header with no concept (or one only in a comment, string or macro
definition) names no such gap. The oracle is the hand-written header table
below, with the expected answer stated per row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.extract.headers.compile_provenance import _concept_scan
from abicheck.extract.headers.concept_scan import (
    concept_header_scan,
    header_declares_concept_or_requires,
)
from abicheck.model import AbiSnapshot
from abicheck.policy.analysis_assurance._concepts import (
    CONCEPT_GAP_DIMENSION,
    concept_constraint_gap,
)
from abicheck.policy.analysis_assurance._scope_fallback import SCOPE_FALLBACK_DIMENSION

# (id, header text, declares a concept or requires-clause)
_HEADERS = [
    (
        "concept_definition",
        "template <class T>\nconcept Small = sizeof(T) <= 8;\n",
        True,
    ),
    (
        "requires_clause_on_function",
        "template <class T>\n  requires (sizeof(T) > 1)\nvoid f(T);\n",
        True,
    ),
    (
        "trailing_requires_clause",
        "template <class T> void g(T) requires Small<T>;\n",
        True,
    ),
    (
        "requires_expression_in_class",
        "template <class T> struct S {\n"
        "  static constexpr bool ok = requires (T t) { t.size(); };\n};\n",
        True,
    ),
    (
        "negated_requires",
        "template <class T> requires !std::is_void_v<T> void h();\n",
        True,
    ),
    ("no_concepts", "struct P { int x; };\nint area(P);\n", False),
    (
        "concept_only_in_line_comment",
        "// template <class T> concept Small = sizeof(T) <= 8;\nint f();\n",
        False,
    ),
    (
        "requires_only_in_block_comment",
        "/* template <class T> requires Small<T> void g(); */\nint f();\n",
        False,
    ),
    (
        "concept_only_in_string",
        'const char* doc = "concept Small = requires (T t) { t; }";\n',
        False,
    ),
    (
        "macro_definition_only",
        "#define REQUIRES(x) requires (x)\n#define CONCEPT concept C = true\n"
        "int f();\n",
        False,
    ),
    ("identifier_named_requires", "int requires;\nvoid concept(int);\n", False),
    ("identifier_named_concept", "struct concept_t { int concept; };\n", False),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [(t, e) for _, t, e in _HEADERS],
    ids=[i for i, _, _ in _HEADERS],
)
def test_scan_matches_the_header_table(text: str, expected: bool) -> None:
    assert header_declares_concept_or_requires(text) is expected


@pytest.mark.parametrize("producer", ["castxml", "clang"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [(t, e) for _, t, e in _HEADERS],
    ids=[i for i, _, _ in _HEADERS],
)
def test_both_backends_scan_and_name_the_gap(
    tmp_path: Path, producer: str, text: str, expected: bool
) -> None:
    header = tmp_path / "api.h"
    header.write_text(text)
    scanned = _concept_scan([header], producer)
    assert scanned is expected
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.ast_producer = producer
    snap.cxx_concept_header_scan = scanned
    gap, notes = concept_constraint_gap(None, snap)
    assert gap is expected
    assert [n.startswith(CONCEPT_GAP_DIMENSION) for n in notes] == (
        [True] if expected else []
    )


@pytest.mark.parametrize("producer", ["hybrid", None])
def test_other_producers_run_no_scan(tmp_path: Path, producer: str | None) -> None:
    header = tmp_path / "api.h"
    header.write_text(_HEADERS[0][1])
    assert _concept_scan([header], producer) is None


def test_only_the_given_public_headers_are_read(tmp_path: Path) -> None:
    public = tmp_path / "api.h"
    public.write_text('#include "detail.h"\nint f();\n')
    (tmp_path / "detail.h").write_text(_HEADERS[0][1])
    assert concept_header_scan([public]) is False
    # An unreadable header cannot rule a concept out, so it reads as a gap.
    assert concept_header_scan([public, tmp_path / "missing.h"]) is True


@pytest.mark.parametrize(
    ("old_scan", "new_scan", "sides"),
    [
        (True, True, ["old", "new"]),
        (True, False, ["old"]),
        (None, True, ["new"]),
        (False, None, []),
    ],
)
def test_each_side_is_named(
    old_scan: bool | None, new_scan: bool | None, sides: list[str]
) -> None:
    old, new = (AbiSnapshot(library="l", version=v) for v in ("1", "2"))
    old.cxx_concept_header_scan = old_scan
    new.cxx_concept_header_scan = new_scan
    gap, notes = concept_constraint_gap(old, new)
    assert gap is bool(sides)
    assert [
        s for s in ("old", "new") if any(f" {s} snapshot" in n for n in notes)
    ] == sides


@pytest.mark.parametrize("value", [True, False, None])
def test_scan_round_trips_through_a_saved_snapshot(
    tmp_path: Path, value: bool | None
) -> None:
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_header_scan = value
    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    assert load_snapshot(path).cxx_concept_header_scan is value


@pytest.mark.integration
@pytest.mark.parametrize("frontend", ["castxml", "clang"])
def test_case105_concept_tightening_through_the_cli_per_backend(
    tmp_path: Path, frontend: str
) -> None:
    """Catalog case105 (concept tightened) through the public dump/compare CLI:
    castxml never reads ``complete`` and names the concept dimension on both
    sides; clang detects ``concept_tightened`` and names no concept gap."""
    import shutil

    from tests.test_known_gap_canaries_header_backends import (
        _catalog_sides,
        _compare_libraries,
    )

    for tool in (frontend, "g++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")
    report = _compare_libraries(
        tmp_path,
        frontend=frontend,
        lang="c++",
        sides=_catalog_sides(
            "case105_concept_tightening", (["-std=c++20"], ["-std=c++20"])
        ),
        debug=True,
    )
    assurance = report["analysis_assurance"]
    named = [n for n in assurance["notes"] if n.startswith(CONCEPT_GAP_DIMENSION)]
    diagnostics = {
        "platform": sys.platform,
        "status": assurance.get("status"),
        "notes": assurance.get("notes"),
        "old_scan": _snapshot_scan(tmp_path / "v1.json"),
        "new_scan": _snapshot_scan(tmp_path / "v2.json"),
    }
    fallback = [n for n in assurance["notes"] if n.startswith(SCOPE_FALLBACK_DIMENSION)]
    if frontend == "clang" and not fallback:
        # clang models the concepts: the tightening is detected and no
        # concept gap is named on either side.
        kinds = {c["kind"] for c in report.get("changes", [])}
        assert "concept_tightened" in kinds, diagnostics
        assert named == [], diagnostics
        return
    assert assurance["status"] == "partial", diagnostics
    # Each side names either the concept dimension (headers parsed: Linux/ELF)
    # or the export-table fallback (the header-only template surface matches
    # no Mach-O/PE export, so the headers were discarded) -- never neither.
    assert len(named) + len(fallback) == 2, diagnostics


def _snapshot_scan(path: Path) -> dict[str, object]:
    """The dumped snapshot's concept-scan inputs, for a failure message."""
    from abicheck.serialization import load_snapshot

    if not path.exists():
        return {"missing": str(path)}
    snap = load_snapshot(path)
    return {
        "cxx_concept_header_scan": snap.cxx_concept_header_scan,
        "ast_toolchain": dict(getattr(snap, "ast_toolchain", None) or {}),
        "platform": getattr(snap, "platform", None),
        "from_headers": getattr(snap, "from_headers", None),
        "elf_only_mode": getattr(snap, "elf_only_mode", None),
        "ast_producer": getattr(snap, "ast_producer", None),
        "ast_fallback_reason": getattr(snap, "ast_fallback_reason", None),
        "functions": len(getattr(snap, "functions", None) or ()),
    }
