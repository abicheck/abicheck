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
            'x.py::f::vtable::getattr(rec, "vtable", None) or []::'
            'getattr(rec, "vtable", None)::1'
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
            "x.py::f::bases::RecordType(bases=[])::RecordType(bases=[])::1"
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
            "x.py::f::bases::rec.bases::rec.bases::1",
            "x.py::f::bases::rec.bases::rec.bases::2",
            "x.py::g::bases::rec.bases::rec.bases::1",
        ]

    def test_two_different_attrs_on_the_same_line_each_get_their_own_key(self) -> None:
        src = "def f(rec):\n    return rec.bases, rec.vtable\n"
        tree = ast.parse(src, filename="x.py")
        keys = {
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        }
        assert keys == {
            "x.py::f::bases::rec.bases, rec.vtable::rec.bases::1",
            "x.py::f::vtable::rec.bases, rec.vtable::rec.vtable::1",
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
            "x.py::f::is_va_list::not p_old.is_va_list and p_new.is_va_list::"
            "p_old.is_va_list::1",
            "x.py::f::is_va_list::not p_old.is_va_list and p_new.is_va_list::"
            "p_new.is_va_list::1",
        ]

    def test_two_different_call_sites_with_identical_bare_text_get_distinct_keys(
        self,
    ) -> None:
        """`old_decision(rec.bases)` and, elsewhere in the same function,
        `keep(rec.bases)` -- two DIFFERENT call sites sharing the identical
        bare `rec.bases` spelling. Before including the outermost containing
        expression in the key, these differed only by occurrence ordinal,
        which a later migration (removing one, adding an unrelated third
        read) could silently reassign onto the wrong site (Codex review,
        third round, fresh evidence). The outer-expression component alone
        already tells them apart, with no ordinal involved."""
        src = "def f(rec):\n    old_decision(rec.bases)\n    keep(rec.bases)\n"
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::bases::old_decision(rec.bases)::rec.bases::1",
            "x.py::f::bases::keep(rec.bases)::rec.bases::1",
        ]

    def test_key_does_not_pull_in_a_compound_statements_body(self) -> None:
        """The outermost containing expression must stop at the statement
        boundary -- for `if <test-with-a-read>: <body>`, the key's
        outer-expression component is the test alone, not the whole `if`
        block, so an edit to unrelated code in the body doesn't silently
        invalidate an already-reviewed baseline entry."""
        src = (
            "def f(rec):\n"
            "    if rec.bases:\n"
            "        do_something_unrelated()\n"
            "        do_more_unrelated_things()\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ["x.py::f::bases::rec.bases::rec.bases::1"]

    def test_detects_a_positional_structural_pattern_on_a_bridged_class(
        self,
    ) -> None:
        """`case RecordType(_, _, _, _, _, []):` reads a field positionally
        via `cls.__match_args__`, not `MatchClass.kwd_attrs` -- the earlier
        keyword-only fix's own `kwd_attrs` loop sees an empty list here and
        reports nothing (Codex review, fresh evidence). Since resolving
        which position maps to which field needs real `__match_args__`
        introspection this scan can't do, any non-empty positional pattern
        on a known bridged class is reported unconditionally."""
        src = (
            "def f(rec):\n"
            "    match rec:\n"
            "        case RecordType(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RecordType(_, _, _, _, _, [])::"
            "RecordType(_, _, _, _, _, [])::1"
        ]

    def test_ignores_a_positional_pattern_on_an_unrelated_class(self) -> None:
        src = (
            "def f(rec):\n"
            "    match rec:\n"
            "        case Unrelated(_, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_positional_pattern_through_an_import_alias(self) -> None:
        """`from abicheck.model import RecordType as RT` then `case
        RT(_, _, _, _, _, []):` names the identical class, but a bare
        `node.cls.id in FACT_BRIDGED_CLASS_NAMES` check rejects `"RT"`
        outright (Codex review: an import alias must not defeat the
        positional-pattern fix)."""
        src = (
            "from abicheck.model import RecordType as RT\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case RT(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RT(_, _, _, _, _, [])::RT(_, _, _, _, _, [])::1"
        ]

    def test_ignores_an_import_alias_of_an_unrelated_class(self) -> None:
        src = (
            "from somewhere import Unrelated as U\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case U(_, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_positional_pattern_through_a_local_class_alias(self) -> None:
        """`RT = RecordType` then `case RT(_, _, _, _, _, []):` -- a plain
        local assignment, not an `import ... as` (Codex review: aliasing
        must not require the `import` spelling to be recognized)."""
        src = (
            "RT = RecordType\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case RT(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RT(_, _, _, _, _, [])::RT(_, _, _, _, _, [])::1"
        ]

    def test_ignores_a_local_alias_of_an_unrelated_class(self) -> None:
        src = (
            "U = Unrelated\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case U(_, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_positional_pattern_through_an_annotated_local_alias(
        self,
    ) -> None:
        """`RT: type = RecordType` then `case RT(_, _, _, _, _, []):` -- an
        ordinary annotated assignment, not a bare `ast.Assign` (Codex
        review, fresh evidence: adding a type annotation to an already-
        recognized alias assignment must not bypass the reader gate)."""
        src = (
            "RT: type = RecordType\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case RT(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RT(_, _, _, _, _, [])::RT(_, _, _, _, _, [])::1"
        ]

    def test_detects_a_positional_pattern_through_a_qualified_class_alias(
        self,
    ) -> None:
        """`import abicheck.model as model; RT = model.RecordType` then
        `case RT(_, _, _, _, _, []):` -- combining two already-supported
        forms (a qualified class reference and an assignment alias) in a
        way neither alone catches (Codex review, fresh evidence): the
        assignment's own value is `model.RecordType`, an `ast.Attribute`,
        not a bare `ast.Name`."""
        src = (
            "import abicheck.model as model\n"
            "RT = model.RecordType\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case RT(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RT(_, _, _, _, _, [])::RT(_, _, _, _, _, [])::1"
        ]

    def test_detects_a_positional_pattern_through_a_chained_class_alias(
        self,
    ) -> None:
        """`RT = Alias = RecordType` then `case RT(_, _, _, _, _, []):` --
        an ordinary chained assignment, not a single-target `ast.Assign`
        (Codex review, fresh evidence: the single-target restriction
        wrongly excluded this shape too)."""
        src = (
            "RT = Alias = RecordType\n"
            "def f(rec):\n"
            "    match rec:\n"
            "        case RT(_, _, _, _, _, []):\n"
            "            return True\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            "x.py::f::<positional>::RT(_, _, _, _, _, [])::RT(_, _, _, _, _, [])::1"
        ]

    def test_detects_a_qualified_builtins_getattr_call(self) -> None:
        """`import builtins; builtins.getattr(rec, "bases")` is the
        identical read as bare `getattr(rec, "bases")` (Codex review:
        qualifying the call through the `builtins` module must not defeat
        the scan)."""
        src = 'import builtins\ndef f(rec):\n    return builtins.getattr(rec, "bases", None)\n'
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::builtins.getattr(rec, "bases", None)::'
            'builtins.getattr(rec, "bases", None)::1'
        ]

    def test_detects_a_call_through_an_assigned_alias_of_builtins(self) -> None:
        """`import builtins; b = builtins` then `b.getattr(rec, "bases")`
        -- a plain assignment alias of the `builtins` module itself, not
        just an `import ... as` one (Codex review, fresh evidence:
        `builtins_names` was only ever populated from a real `import`
        statement)."""
        src = (
            "import builtins\n"
            "b = builtins\n"
            "def f(rec):\n"
            '    return b.getattr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::b.getattr(rec, "bases")::b.getattr(rec, "bases")::1'
        ]

    def test_detects_an_aliased_getattr_import(self) -> None:
        """`from builtins import getattr as read_attr` then
        `read_attr(rec, "vtable", None)`."""
        src = (
            "from builtins import getattr as read_attr\n"
            "def f(rec):\n"
            '    return read_attr(rec, "vtable", None)\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::vtable::read_attr(rec, "vtable", None)::'
            'read_attr(rec, "vtable", None)::1'
        ]

    def test_ignores_a_qualified_call_on_an_unrelated_module(self) -> None:
        src = 'import somewhere\ndef f(rec):\n    return somewhere.getattr(rec, "bases", None)\n'
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []

    def test_detects_a_local_alias_of_the_getattr_builtin(self) -> None:
        """`read_attr = getattr` then `read_attr(rec, "bases")` is the
        identical dynamic read as a bare `getattr(rec, "bases")` call
        (Codex review, fresh evidence: a plain assignment chain to the
        builtin callable bypassed the scan the same way a local class
        alias once did)."""
        src = (
            'def f(rec):\n    read_attr = getattr\n    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_detects_a_chained_alias_of_the_getattr_builtin(self) -> None:
        """`read_attr = getattr; read_attr2 = read_attr` -- the same
        fixed-point chaining `_imported_class_aliases` already does for a
        class alias, applied to the builtin callable."""
        src = (
            "def f(rec):\n"
            "    read_attr = getattr\n"
            "    read_attr2 = read_attr\n"
            '    return read_attr2(rec, "vtable")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::vtable::read_attr2(rec, "vtable")::read_attr2(rec, "vtable")::1'
        ]

    def test_detects_a_chained_assignment_to_the_getattr_builtin(self) -> None:
        """`read1 = read2 = getattr` -- an ordinary chained assignment,
        not a single-target `ast.Assign` (Codex review, fresh evidence:
        the identical gap fixed for the class-alias resolver's own
        `ast.Assign` branch, reached here through the getattr resolver
        instead)."""
        src = (
            'def f(rec):\n    read1 = read2 = getattr\n    return read1(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ['x.py::f::bases::read1(rec, "bases")::read1(rec, "bases")::1']

    def test_detects_a_qualified_assignment_to_the_getattr_builtin(self) -> None:
        """`read_attr = builtins.getattr; read_attr(rec, "bases")` --
        combining the qualified-call recognition with plain-assignment
        chaining (Codex review, fresh evidence): the assignment's own
        value is `builtins.getattr`, not a bare `ast.Name`, so it was
        invisible to a candidate collector that only ever matched an
        `ast.Name` value."""
        src = (
            "import builtins\n"
            "def f(rec):\n"
            "    read_attr = builtins.getattr\n"
            '    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_detects_an_annotated_assignment_to_the_getattr_builtin(self) -> None:
        """`read_attr: Callable[..., object] = getattr; read_attr(rec,
        "bases")` -- an ordinary annotated assignment, not the `ast.
        Assign` the getattr-alias collector already matched (Codex
        review, fresh evidence: adding a type annotation to an
        already-recognized alias assignment must not bypass the reader
        gate, the identical gap the tenth round's fix already closed for
        the class-alias resolver)."""
        src = (
            "from typing import Callable\n"
            "def f(rec):\n"
            "    read_attr: Callable[..., object] = getattr\n"
            '    return read_attr(rec, "bases")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::bases::read_attr(rec, "bases")::read_attr(rec, "bases")::1'
        ]

    def test_detects_an_annotated_qualified_assignment_to_getattr(self) -> None:
        """The annotated form of the *qualified* spelling too: `read_attr:
        object = builtins.getattr`."""
        src = (
            "import builtins\n"
            "def f(rec):\n"
            "    read_attr: object = builtins.getattr\n"
            '    return read_attr(rec, "vtable")\n'
        )
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == [
            'x.py::f::vtable::read_attr(rec, "vtable")::read_attr(rec, "vtable")::1'
        ]

    def test_detects_an_augmented_assignment_to_a_bridged_attr(self) -> None:
        """`rec.bases += inherited` -- Python marks the target `Store`, but
        the operation reads the field's existing value first (Codex
        review, fresh evidence): the Load-only restriction otherwise
        missed this entirely."""
        src = "def f(rec, inherited):\n    rec.bases += inherited\n"
        tree = ast.parse(src, filename="x.py")
        keys = [
            key for key, _l, _a, _q in unmigrated_fact_reader_sites(tree, "x.py", src)
        ]
        assert keys == ["x.py::f::bases::rec.bases::rec.bases::1"]

    def test_ignores_an_ordinary_assignment_to_a_bridged_attr(self) -> None:
        """A plain overwrite (`Store`, not `AugAssign`) is still not a
        read -- this function's own opening paragraph, re-pinned alongside
        the new `AugAssign` branch to confirm it didn't widen the ordinary
        `Store` case too."""
        src = "def f(rec):\n    rec.bases = []\n"
        tree = ast.parse(src, filename="x.py")
        assert unmigrated_fact_reader_sites(tree, "x.py", src) == []


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
        frozenset({"abicheck/a_new_reader.py::f::bases::rec.bases::rec.bases::1"}),
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
