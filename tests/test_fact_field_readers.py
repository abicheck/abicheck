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

"""Unit-test mirror of the ``fact-field-readers`` AI-readiness check
(``scripts/fact_field_readers.py``, registered by
``scripts/check_ai_readiness.py``) — ADR-063 Phase 0's "widened, non-glob
AI-readiness check" (``docs/contribute/plans/one-semantic-pipeline.md``).

The check ERRORs if a function outside ``EXEMPT_FUNCTIONS`` reads one of
``RecordType.bases``/``virtual_bases``/``vtable`` or ``Param.is_va_list``
directly (an ``ast.Load``), without the site being a previously reviewed
``KNOWN_UNMIGRATED_READERS`` baseline entry — the same allowlist-and-shrink
design ``ENGINE_CLI_BOUNDARY_ALLOWLIST``/``IMPORT_CYCLE_ALLOWLIST`` already
use. Exemption and the baseline key are both *function*-scoped, not
module- or file-scoped (a Codex review round found a whole-module exemption
hid two genuine decision functions living inside otherwise-exempt producer
modules) — see the gate's own docstring for that history. This file pins
that the real repository has no *unlisted* violation and that the
detection logic itself actually catches a direct read.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.fact_field_readers import (  # noqa: E402
    EXEMPT_FUNCTIONS,
    FACT_BRIDGED_ATTRS,
    KNOWN_UNMIGRATED_READERS,
    check_fact_field_readers,
    unmigrated_fact_reader_sites,
)


def test_no_unlisted_violation_in_real_repo() -> None:
    """The real repository has zero *unlisted* unmigrated readers.

    A pre-existing one must be named in ``KNOWN_UNMIGRATED_READERS``
    (reviewed, not silently accumulating) — this pins that the check itself
    is clean against the actual tree, not just against a synthetic fixture.
    """
    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert errors == [], "Unlisted Fact-bridged-field readers:\n" + "\n".join(errors)


def test_baseline_entries_are_real_sites() -> None:
    """Every ``KNOWN_UNMIGRATED_READERS`` entry must still name a real,
    currently-existing read — an entry that no longer matches anything is
    dead weight the baseline should have shrunk (the reader was migrated,
    or the code moved/was deleted), not a permanent grandfather clause."""
    import scripts.fact_field_readers as gate

    seen: set[str] = set()
    for path in sorted(gate.PKG.rglob("*.py")):
        rel = gate._rel(path)
        try:
            tree = ast.parse(gate._read(path), filename=rel)
        except SyntaxError:
            continue
        source = gate._read(path)
        for key, _lineno, _attr, qualname in unmigrated_fact_reader_sites(
            tree, rel, source
        ):
            if f"{rel}::{qualname}" in EXEMPT_FUNCTIONS:
                continue
            seen.add(key)
    stale = KNOWN_UNMIGRATED_READERS - seen
    assert stale == set(), f"Stale baseline entries (no longer a real read): {stale}"


def test_exempt_functions_are_real_sites() -> None:
    """Every ``EXEMPT_FUNCTIONS`` entry must still name a function that
    actually contains a `Fact`-bridged read today — a stale entry (the
    function was deleted, renamed, or no longer touches these fields at
    all) is silently exempting nothing, which reads as coverage that isn't
    there."""
    import scripts.fact_field_readers as gate

    covering: set[str] = set()
    for path in sorted(gate.PKG.rglob("*.py")):
        rel = gate._rel(path)
        try:
            tree = ast.parse(gate._read(path), filename=rel)
        except SyntaxError:
            continue
        source = gate._read(path)
        for _key, _lineno, _attr, qualname in unmigrated_fact_reader_sites(
            tree, rel, source
        ):
            covering.add(f"{rel}::{qualname}")
    stale = EXEMPT_FUNCTIONS - covering
    assert stale == set(), (
        f"Stale EXEMPT_FUNCTIONS entries (cover no real read): {stale}"
    )


class TestUnmigratedFactReaderSites:
    """Direct tests on the AST-walking primitive, independent of the real
    repository tree — pins the detection logic's own contract."""

    def test_detects_a_direct_read_of_each_bridged_attr(self) -> None:
        src = (
            "def f(rec, p):\n"
            "    a = rec.bases\n"
            "    b = rec.virtual_bases\n"
            "    c = rec.vtable\n"
            "    d = p.is_va_list\n"
            "    e = rec.vptr_offset_bits\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py")
        attrs = {attr for _key, _lineno, attr, _qualname in sites}
        assert attrs == FACT_BRIDGED_ATTRS
        assert all(qualname == "f" for _k, _l, _a, qualname in sites)

    def test_detects_a_getattr_call_naming_a_bridged_attr(self) -> None:
        """A dynamic `getattr(obj, "vtable", ...)` read is a real
        equivalent of `obj.vtable` -- `ast.Attribute`-only matching misses
        it entirely (Codex review: `diff_cpp_patterns._is_empty_record`
        reads `vtable` exactly this way)."""
        src = 'def f(rec):\n    return getattr(rec, "vtable", None) or []\n'
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert [key for key, _l, _a, _q in sites] == [
            'x.py::f::vtable::getattr(rec, "vtable", None)::1'
        ]

    def test_detects_a_structural_pattern_match_naming_a_bridged_attr(
        self,
    ) -> None:
        """`case RecordType(bases=[]):` reads `bases` via `ast.MatchClass.
        kwd_attrs`, not an `ast.Attribute` or a `getattr()` call -- invisible
        to both other branches (Codex review: this exact shape was found
        undetected)."""
        src = (
            "def f(rec):\n"
            "    match rec:\n"
            "        case RecordType(bases=[]):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py", src)
        assert [key for key, _l, _a, _q in sites] == [
            "x.py::f::bases::RecordType(bases=[])::1"
        ]

    def test_ignores_a_getattr_call_with_a_non_matching_or_dynamic_name(self) -> None:
        src = (
            "def f(rec, attr):\n"
            '    a = getattr(rec, "size_bits", None)\n'
            "    b = getattr(rec, attr, None)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py") == []

    def test_ignores_an_assignment_target(self) -> None:
        """A `Store` context (`rec.vtable = []`, the legacy-schema backfill
        shape `storage/fact_codec.py` uses) is writing the field, not
        reading it as if unambiguous — not the failure mode this check
        exists to catch."""
        src = "def f(rec):\n    rec.vtable = []\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py") == []

    def test_ignores_an_unrelated_attribute_name(self) -> None:
        src = "def f(rec):\n    return rec.size_bits\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py") == []

    def test_module_level_read_gets_the_module_qualname(self) -> None:
        src = "REC = None\nX = REC.bases\n"
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py")
        assert [qualname for _k, _l, _a, qualname in sites] == ["<module>"]

    def test_method_gets_a_class_qualified_name(self) -> None:
        src = "class C:\n    def m(self, rec):\n        return rec.bases\n"
        tree = ast.parse(src, filename="x.py")
        sites = unmigrated_fact_reader_sites(tree, "x.py")
        assert [qualname for _k, _l, _a, qualname in sites] == ["C.m"]

    def test_occurrence_numbering_is_scoped_to_the_enclosing_function(self) -> None:
        """Two reads of the same attribute in the same function get
        distinct, top-to-bottom-ordered occurrence numbers *within that
        function* — not merely within the file. A second, unrelated
        function reading the same attribute starts its own count at 1
        rather than continuing the first function's count, which is what
        keeps a migrated-and-replaced read from silently inheriting an
        unrelated new read's key (Codex review — see the gate's own
        docstring)."""
        src = (
            "def f(rec):\n"
            "    a = rec.bases\n"
            "    b = rec.bases\n"
            "def g(rec):\n"
            "    c = rec.bases\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::bases::rec.bases::1",
            "x.py::f::bases::rec.bases::2",
            "x.py::g::bases::rec.bases::1",
        ]

    def test_two_different_attrs_on_the_same_line_each_get_their_own_key(self) -> None:
        src = "def f(rec):\n    return rec.bases, rec.vtable\n"
        tree = ast.parse(src, filename="x.py")
        keys = {
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        }
        assert keys == {
            "x.py::f::bases::rec.bases::1",
            "x.py::f::vtable::rec.vtable::1",
        }

    def test_two_different_reads_on_the_same_line_get_distinct_keys(self) -> None:
        """`if not p_old.is_va_list and p_new.is_va_list:` -- two textually
        different reads of the same attribute, same line, same function
        (the exact `diff_param_qualifiers.py` shape from the Codex review
        that motivated including source text in the key at all)."""
        src = (
            "def f(p_old, p_new):\n"
            "    return not p_old.is_va_list and p_new.is_va_list\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::is_va_list::p_old.is_va_list::1",
            "x.py::f::is_va_list::p_new.is_va_list::1",
        ]


def test_check_reports_a_new_unlisted_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real, unlisted read in a throwaway `abicheck/`-shaped
    tree fails the gate — pins that the check function itself (not just the
    AST primitive) actually enforces the baseline, against a real file on
    disk rather than only an in-memory `ast.Module`."""
    import scripts.fact_field_readers as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_reader.py").write_text("def f(rec):\n    return rec.bases\n")

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert len(errors) == 1
    assert "a_new_reader.py:2" in errors[0]
    assert "bases" in errors[0]


def test_check_is_silent_for_a_baselined_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the test above: the identical read, at the
    identical stable key, produces no finding once it's in the baseline —
    the gate must not fire on every known, reviewed site forever."""
    import scripts.fact_field_readers as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_reader.py").write_text("def f(rec):\n    return rec.bases\n")

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(
        gate,
        "KNOWN_UNMIGRATED_READERS",
        frozenset({"abicheck/a_new_reader.py::f::bases::rec.bases::1"}),
    )

    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert errors == []


def test_check_respects_exempt_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read inside a function named in `EXEMPT_FUNCTIONS` is silent even
    though it is neither in `KNOWN_UNMIGRATED_READERS` nor migrated — pins
    that exemption is checked independently of the baseline, and that it is
    scoped to the *function*, not the whole file (a sibling function in the
    same file, reading the same attribute, still fails the gate)."""
    import scripts.fact_field_readers as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "producer.py").write_text(
        "def _construct(rec):\n"
        "    return rec.bases\n"
        "def _decide(rec):\n"
        "    return rec.bases\n"
    )

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(
        gate, "EXEMPT_FUNCTIONS", frozenset({"abicheck/producer.py::_construct"})
    )
    monkeypatch.setattr(gate, "KNOWN_UNMIGRATED_READERS", frozenset())

    findings = Findings()
    check_fact_field_readers(findings)
    errors = [m for c, m in findings.errors if c == "fact-field-readers"]
    assert len(errors) == 1
    assert "producer.py:4" in errors[0]
