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

"""Review fixes for the C++20 concept scan (PR #1934).

Each test pins a bug class with several inputs: one-pass lexing of comments
and literals, provably inactive ``#if 0`` groups, C-mode parses, unreadable
headers, depth projection below header depth, an explicit reset and a
malformed stored fact, and duplicate coverage warnings when assurance is
attached twice to one result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.headers.compile_provenance import _concept_scan
from abicheck.extract.headers.concept_scan import (
    concept_header_scan,
    header_declares_concept_or_requires,
)
from abicheck.model import AbiSnapshot
from abicheck.policy.analysis_assurance._concepts import CONCEPT_GAP_DIMENSION

_CONCEPT = "template <class T> concept C = sizeof(T) > 1;"
_REQUIRES = "template <class T> requires C<T> void f(T);"


@pytest.mark.parametrize("decl", [_CONCEPT, _REQUIRES])
@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('const char* a = "/*";', 'const char* b = "*/";'),
        ('const char* a = "x /* y";', 'const char* b = "*/ z";'),
        ("char a = '/'; const char* s = \"/*\";", 'const char* e = "*/";'),
        ('const char* a = "//";', ""),
    ],
)
def test_comment_delimiters_inside_literals_do_not_hide_a_declaration(
    decl: str, before: str, after: str
) -> None:
    text = f"{before}\n{decl}\n{after}\n"
    assert header_declares_concept_or_requires(text) is True


@pytest.mark.parametrize(
    "text",
    [
        '/* "unterminated */ int x;',
        '// "\nint y;',
        f"/* {_CONCEPT} */",
        f'const char* s = "{_CONCEPT}";',
    ],
)
def test_literals_inside_comments_and_vice_versa_stay_inert(text: str) -> None:
    assert header_declares_concept_or_requires(text) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"#if 0\n{_CONCEPT}\n#endif\n", False),
        (f"#  if 0\n{_REQUIRES}\n#endif\n", False),
        (f"#if 0\n#ifdef X\n#endif\n{_CONCEPT}\n#endif\n", False),
        (f"#if 0\nint x;\n#else\n{_CONCEPT}\n#endif\n", True),
        (f"#if 0\nint x;\n#elif 1\n{_CONCEPT}\n#endif\n", True),
        (f"#if 0\n#endif\n{_REQUIRES}\n", True),
        (f"#if 1\n{_CONCEPT}\n#endif\n", True),
        (f"#ifdef HAVE_CONCEPTS\n{_CONCEPT}\n#endif\n", True),
        (f"/*\n#if 0\n*/\n{_CONCEPT}\n", True),
    ],
)
def test_if0_groups_are_inactive(text: str, expected: bool) -> None:
    assert header_declares_concept_or_requires(text) is expected


@pytest.mark.parametrize("producer", ["castxml", "clang"])
@pytest.mark.parametrize(
    "body", ["int requires(int);", _CONCEPT, "int requires(int x) { return x; }"]
)
def test_c_mode_parses_are_not_scanned(
    tmp_path: Path, producer: str, body: str
) -> None:
    header = tmp_path / "api.h"
    header.write_text(body + "\n", encoding="utf-8")
    assert _concept_scan([header], producer, "c") is None
    # The C++ mode of the same header is still scanned.
    assert _concept_scan([header], producer, "c++") is True


def test_unreadable_header_is_not_a_clean_negative(tmp_path: Path) -> None:
    clean = tmp_path / "clean.h"
    clean.write_text("int f(void);\n", encoding="utf-8")
    positive = tmp_path / "pos.h"
    positive.write_text(_CONCEPT + "\n", encoding="utf-8")
    missing = tmp_path / "gone.h"
    directory = tmp_path / "a_dir.h"
    directory.mkdir()
    assert concept_header_scan([clean]) is False
    # An unreadable header cannot rule a concept out, so it reads as a gap.
    assert concept_header_scan([clean, missing]) is True
    assert concept_header_scan([directory, clean]) is True
    assert concept_header_scan([missing]) is True
    # A positive in a readable header is still reported.
    assert concept_header_scan([missing, positive]) is True
    assert concept_header_scan([positive, missing]) is True


@pytest.mark.parametrize("depth", ["binary"])
@pytest.mark.parametrize("value", [True, False])
def test_projection_below_header_depth_clears_the_scan(depth: str, value: bool) -> None:
    from abicheck.policy.depth_projection import project_snapshot_to_depth

    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_header_scan = value
    out = project_snapshot_to_depth(snap, depth)
    assert out.cxx_concept_header_scan is None
    assert snap.cxx_concept_header_scan is value  # input not mutated


@pytest.mark.parametrize("depth", ["headers", "build", "source"])
def test_projection_at_or_above_header_depth_keeps_the_scan(depth: str) -> None:
    from abicheck.policy.depth_projection import project_snapshot_to_depth

    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_header_scan = True
    assert project_snapshot_to_depth(snap, depth).cxx_concept_header_scan is True


@pytest.mark.parametrize("stored", [True, False])
def test_reset_scan_is_not_resaved(tmp_path: Path, stored: bool) -> None:
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_header_scan = stored
    first = tmp_path / "a.json"
    save_snapshot(snap, first)
    loaded = load_snapshot(first)
    loaded.cxx_concept_header_scan = None
    second = tmp_path / "b.json"
    save_snapshot(loaded, second)
    assert load_snapshot(second).cxx_concept_header_scan is None
    assert "cxx_concept_header_scan" not in second.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, [True], {"v": True}])
def test_malformed_stored_scan_is_refused(bad: object) -> None:
    from abicheck.errors import SnapshotError
    from abicheck.storage.extension_fact_catalog import CXX_CONCEPT_HEADER_SCAN
    from abicheck.storage.extension_facts import apply_model_extension_facts
    from abicheck.storage.wire_extensions import (
        EXTENSIONS_KEY,
        WireExtensions,
        attach,
    )

    snap = AbiSnapshot(library="l", version="1")
    ext = WireExtensions()
    ext.header_extras[CXX_CONCEPT_HEADER_SCAN.section] = {
        EXTENSIONS_KEY: {CXX_CONCEPT_HEADER_SCAN.name: bad}
    }
    attach(snap, ext)
    with pytest.raises(SnapshotError, match="cxx_concept_header_scan"):
        apply_model_extension_facts(snap)


def test_absent_scan_still_reads_as_no_scan() -> None:
    from abicheck.storage.extension_facts import apply_model_extension_facts

    snap = AbiSnapshot(library="l", version="1")
    apply_model_extension_facts(snap)
    assert snap.cxx_concept_header_scan is None


@pytest.mark.parametrize(
    ("old_scan", "new_scan"), [(True, False), (False, True), (True, True)]
)
def test_attaching_assurance_twice_does_not_duplicate_warnings(
    old_scan: bool, new_scan: bool
) -> None:
    from abicheck.checker import compare
    from abicheck.workflows.analysis_assurance_attach import (
        attach_analysis_assurance,
    )

    old, new = (AbiSnapshot(library="l", version=v) for v in ("1", "2"))
    old.cxx_concept_header_scan, new.cxx_concept_header_scan = old_scan, new_scan
    result = compare(old, new)
    first = list(result.coverage_warnings)
    attach_analysis_assurance(result, old, new)
    attach_analysis_assurance(result, old, new)
    assert result.coverage_warnings == first
    concept = [w for w in first if w.startswith(CONCEPT_GAP_DIMENSION)]
    assert len(concept) == [old_scan, new_scan].count(True)
    assert len(first) == len(set(first))


def test_unreadable_header_lowers_comparison_assurance(tmp_path: Path) -> None:
    """Comparison-level oracle: a side whose header could not be read names
    the concept dimension as unobserved; a fully read clean side does not."""
    from types import SimpleNamespace

    from abicheck.policy.analysis_assurance._concepts import concept_constraint_gap

    clean = tmp_path / "clean.h"
    clean.write_text("int f(void);\n", encoding="utf-8")
    missing = tmp_path / "gone.h"
    read_ok = SimpleNamespace(cxx_concept_header_scan=concept_header_scan([clean]))
    read_failed = SimpleNamespace(
        cxx_concept_header_scan=concept_header_scan([clean, missing])
    )
    assert concept_constraint_gap(read_ok, read_ok) == (False, [])  # type: ignore[arg-type]
    gap, notes = concept_constraint_gap(read_ok, read_failed)  # type: ignore[arg-type]
    assert gap and len(notes) == 1 and "new snapshot" in notes[0]
    gap, notes = concept_constraint_gap(read_failed, read_failed)  # type: ignore[arg-type]
    assert gap and len(notes) == 2


def test_unreadable_header_makes_comparison_assurance_partial(tmp_path: Path) -> None:
    """The rolled-up assurance of ``checker.compare``, not only the gap
    helper: with one side's header unreadable the result is ``partial`` and
    names the concept dimension; with every header read it does not. (Bare
    snapshots are already ``partial`` for unrelated layout reasons, so the
    concept note is the discriminator.)"""
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot

    clean = tmp_path / "clean.h"
    clean.write_text("int f(void);\n", encoding="utf-8")
    statuses = {}
    for label, headers in (
        ("read", [clean]),
        ("unreadable", [clean, tmp_path / "gone.h"]),
    ):
        old = AbiSnapshot(library="l", version="1")
        new = AbiSnapshot(library="l", version="2")
        old.cxx_concept_header_scan = concept_header_scan([clean])
        new.cxx_concept_header_scan = concept_header_scan(headers)
        result = compare(old, new)
        statuses[label] = result.analysis_assurance
    assert statuses["unreadable"].status == "partial"
    from abicheck.policy.analysis_assurance._concepts import CONCEPT_GAP_DIMENSION

    def named(label: str) -> bool:
        return any(n.startswith(CONCEPT_GAP_DIMENSION) for n in statuses[label].notes)

    assert not named("read")
    assert named("unreadable")


@pytest.mark.parametrize("other_must_understand", [True, False])
def test_reset_drops_only_the_concept_fact(other_must_understand: bool) -> None:
    """A reset removes the concept fact and its must_understand entry but
    leaves a sibling fact (and its must_understand entry) in the section."""
    from abicheck.storage.extension_fact_catalog import CXX_CONCEPT_HEADER_SCAN
    from abicheck.storage.extension_facts import sync_model_extension_facts
    from abicheck.storage.wire_extensions import (
        EXTENSIONS_KEY,
        MUST_UNDERSTAND_KEY,
        WireExtensions,
        attach,
        extensions_of,
    )

    name = CXX_CONCEPT_HEADER_SCAN.name
    snap = AbiSnapshot(library="l", version="1")
    ext = WireExtensions()
    must = [name, "acme.other"] if other_must_understand else [name]
    ext.header_extras[CXX_CONCEPT_HEADER_SCAN.section] = {
        EXTENSIONS_KEY: {name: True, "acme.other": 1},
        MUST_UNDERSTAND_KEY: must,
    }
    ext.document_must_understand = [name]
    attach(snap, ext)
    snap.cxx_concept_header_scan = None
    sync_model_extension_facts(snap)
    carried = extensions_of(snap)
    assert carried is not None
    extras = carried.header_extras[CXX_CONCEPT_HEADER_SCAN.section]
    assert extras[EXTENSIONS_KEY] == {"acme.other": 1}
    assert extras.get(MUST_UNDERSTAND_KEY, []) == (
        ["acme.other"] if other_must_understand else []
    )
    assert name not in carried.document_must_understand


@pytest.mark.parametrize("toolchain", [None, {}])
def test_empty_toolchain_metadata_still_scans_like_castxml(
    tmp_path: Path, toolchain: dict[str, str] | None
) -> None:
    """The Mach-O and PE dump paths hand over empty toolchain metadata (seen
    on macOS/Windows CI); like ``dumper``/``manifest`` they are castxml's, so
    the producer-gated scans must still run there. No headers, no scan."""
    from abicheck.extract.headers.compile_provenance import _ast_compile_provenance

    header = tmp_path / "api.h"
    header.write_text(_CONCEPT + "\n", encoding="utf-8")
    prov = _ast_compile_provenance([header], None, (), None, ast_toolchain=toolchain)
    assert prov["cxx_concept_header_scan"] is True
    hybrid = _ast_compile_provenance(
        [header], None, (), None, ast_toolchain={"producer": "hybrid"}
    )
    assert hybrid["cxx_concept_header_scan"] is None
    none = _ast_compile_provenance([], None, (), None, ast_toolchain=toolchain)
    assert none["cxx_concept_header_scan"] is None
