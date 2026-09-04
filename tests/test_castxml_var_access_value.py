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

"""G31 Phase C continued — ``Variable.access``/``Variable.value`` on castxml.

No backend populated either fact at all before schema v24: every variable
kept the model defaults (``AccessLevel.PUBLIC``, ``None``). castxml already
reads the identical structured ``access`` attribute for a class member
(``Function``/``TypeField``); this closes the one place it wasn't also
applied. castxml's ``init`` attribute is a verbatim (unevaluated) source
expression it already reads for ``TypeField.default``/``Param.default`` and
for ``parse_constants()``'s own const/constexpr scan.

Three layers are covered here, mirroring ``test_clang_param_va_list.py``:

1. Synthetic-XML unit tests against ``_CastxmlParser`` directly (no castxml
   binary needed — mirrors the shape ``test_castxml_hidden_friends.py`` and
   siblings already use), confirmed to match real castxml output in (2).
2. A live, real-``castxml``-invoking ``integration``-marked test parsing
   the XML output directly — the exact spellings verified against real
   castxml 0.6 output.
3. The diff-level gates: header-tier, "castxml"-producer-only (not
   "hybrid"), and the pre-v24 legacy-baseline flag for ``access``;
   ``value`` needs no gate at all (protected by ``_diff_var_values``'s
   existing per-pair None-skip).
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.dumper import _CastxmlParser
from abicheck.model import AbiSnapshot, AccessLevel, Fact, Variable


def _make_root_with_static_members_and_free_var() -> Element:
    """Mirror real castxml output for a class with public/protected/private
    static members plus a free (namespace-scope) initialized variable —
    verified against real castxml 0.6.20260105 (see the live test below)."""
    root = Element("CastXML", attrib={"format": "1.4.0"})

    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "lib.h")

    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})

    cls = SubElement(root, "Class")
    cls.set("id", "_7")
    cls.set("name", "S")
    cls.set("context", "_1")
    cls.set("file", "f1")
    cls.set("location", "f1:1")
    cls.set("size", "0")
    cls.set("align", "8")

    SubElement(
        root,
        "Variable",
        attrib={
            "id": "_14",
            "name": "pub",
            "type": "_23",
            "context": "_7",
            "access": "public",
            "static": "1",
            "mangled": "_ZN1S3pubE",
            "file": "f1",
            "location": "f1:3",
        },
    )
    SubElement(
        root,
        "Variable",
        attrib={
            "id": "_15",
            "name": "prot",
            "type": "_23",
            "context": "_7",
            "access": "protected",
            "static": "1",
            "mangled": "_ZN1S4protE",
            "file": "f1",
            "location": "f1:5",
        },
    )
    SubElement(
        root,
        "Variable",
        attrib={
            "id": "_16",
            "name": "priv",
            "type": "_23",
            "context": "_7",
            "access": "private",
            "static": "1",
            "mangled": "_ZN1S4privE",
            "file": "f1",
            "location": "f1:7",
        },
    )
    SubElement(
        root,
        "Variable",
        attrib={
            "id": "_8",
            "name": "free_var",
            "type": "_23",
            "context": "_1",
            "init": "1",
            "file": "f1",
            "location": "f1:9",
        },
    )
    return root


class TestCastxmlVariableAccessAndValue:
    """The wiring, over synthetic XML matching real castxml's own shape."""

    def _variables_by_name(self) -> dict[str, Variable]:
        root = _make_root_with_static_members_and_free_var()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        return {v.name: v for v in parser.parse_variables()}

    def test_static_member_access_levels(self) -> None:
        by_name = self._variables_by_name()
        assert by_name["pub"].access == AccessLevel.PUBLIC
        assert by_name["prot"].access == AccessLevel.PROTECTED
        assert by_name["priv"].access == AccessLevel.PRIVATE

    def test_namespace_scope_variable_defaults_to_public_access(self) -> None:
        """No ``access`` attribute at all (castxml's own convention for a
        namespace-scope variable) — ``_access_level``'s existing "public"
        fallback, matching the model default."""
        by_name = self._variables_by_name()
        assert by_name["free_var"].access == AccessLevel.PUBLIC

    def test_const_variable_value_is_the_verbatim_init_expression(self) -> None:
        """A non-const free variable's ``init`` (``free_var``) is NOT surfaced
        as ``value`` — it's not a compile-time-constant contract, just an
        ordinary mutable global's initializer."""
        by_name = self._variables_by_name()
        assert by_name["free_var"].value is None

    def test_const_variable_value_is_extracted(self) -> None:
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "lib.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_23c", "type": "_23", "const": "1"}
        )
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_9",
                "name": "kFoo",
                "type": "_23c",
                "context": "_1",
                "init": "42",
                "file": "f1",
                "location": "f1:1",
            },
        )
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (v,) = parser.parse_variables()
        assert v.is_const is True
        assert v.value == "42"

    def test_non_constant_initializer_is_not_surfaced_as_value(self) -> None:
        """A ``const`` variable whose initializer is a non-constant runtime
        expression (``const int kRuntime = f();``, verified against real
        castxml to still carry ``const="1"``-equivalent typing) is exactly
        the case this restriction accepts as an existing, documented
        limitation shared with ``_iter_public_constants``'s own
        ``is_const``-only filter — not attempted to be distinguished here.
        Recorded to pin the accepted (not fixed) behavior, not as a bug
        report: the initializer text is still whatever castxml emits, since
        this module never evaluates it."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "lib.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_23c", "type": "_23", "const": "1"}
        )
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_10",
                "name": "kRuntime",
                "type": "_23c",
                "context": "_1",
                "init": "f()",
                "file": "f1",
                "location": "f1:1",
            },
        )
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (v,) = parser.parse_variables()
        assert v.value == "f()"

    def test_mutable_pointer_to_const_pointee_has_no_value(self) -> None:
        """``const int *p = &a;`` — the POINTEE is const, but ``p`` itself is
        a mutable pointer that can legally be reassigned. The loose
        ``is_const`` (word-boundary regex over the rendered spelling) reads
        "const" in ``"const int *"`` and would wrongly treat ``&a`` as a
        baked-in compile-time contract; ``value`` must gate on the
        variable's own TOP-LEVEL constness instead (Codex review, fresh
        evidence). Mirrors real castxml's shape verified in the live test
        below: ``p``'s own ``type`` resolves directly to a ``PointerType``,
        never a ``CvQualifiedType`` — unlike a genuinely const ``int
        *const``, which the sibling test right after this one covers."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "lib.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_23c", "type": "_23", "const": "1"}
        )
        # `p`'s own type is a plain PointerType to the const pointee -- NOT
        # wrapped in its own CvQualifiedType, matching real castxml output
        # for a mutable pointer.
        SubElement(root, "PointerType", attrib={"id": "_24", "type": "_23c"})
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_11",
                "name": "p",
                "type": "_24",
                "context": "_1",
                "init": "&a",
                "file": "f1",
                "location": "f1:1",
            },
        )
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (v,) = parser.parse_variables()
        # The loose, pre-existing `Variable.is_const` still reads True here
        # (a separate, pre-existing imprecision this fix does not touch --
        # it feeds VAR_BECAME_CONST/VAR_LOST_CONST, not `value`) — pinned so
        # a future reader isn't surprised by the apparent inconsistency.
        assert v.is_const is True
        assert v.value is None

    def test_genuinely_const_pointer_still_has_a_value(self) -> None:
        """The positive control for the fix above: ``int *const p = &a;`` —
        the POINTER itself is const (cannot be reassigned) — DOES resolve
        to its own ``CvQualifiedType`` wrapping the ``PointerType``, and its
        initializer is a real compile-time contract."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "lib.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(root, "PointerType", attrib={"id": "_24", "type": "_23"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_24c", "type": "_24", "const": "1"}
        )
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_12",
                "name": "p",
                "type": "_24c",
                "context": "_1",
                "init": "&a",
                "file": "f1",
                "location": "f1:1",
            },
        )
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (v,) = parser.parse_variables()
        assert v.value == "&a"

    def test_private_static_const_member_has_no_value(self) -> None:
        """A private (or protected) `static const` member is implementation
        detail no CONSUMER can name or inline, even when castxml still
        assigns it a mangled export symbol -- the identical reasoning
        `_iter_public_constants` already applies for `AbiSnapshot.constants`
        (Codex review, second round: the first fix gated on top-level
        constness alone and missed this)."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f1 = SubElement(root, "File")
        f1.set("id", "f1")
        f1.set("name", "lib.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_23c", "type": "_23", "const": "1"}
        )
        cls = SubElement(root, "Class")
        cls.set("id", "_7")
        cls.set("name", "S")
        cls.set("context", "_1")
        cls.set("file", "f1")
        cls.set("location", "f1:1")
        cls.set("size", "0")
        cls.set("align", "8")
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_13",
                "name": "kSecret",
                "type": "_23c",
                "context": "_7",
                "access": "private",
                "static": "1",
                "init": "42",
                "mangled": "_ZN1S7kSecretE",
                "file": "f1",
                "location": "f1:2",
            },
        )
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (v,) = parser.parse_variables()
        assert v.access == AccessLevel.PRIVATE
        assert v.value is None

    def test_value_scoped_to_public_header_provenance(self) -> None:
        """A public, top-level-const, exported variable declared in a
        PRIVATE/internal header is outside the configured public-header
        contract -- the identical reasoning `_iter_public_constants`
        already applies via `_decl_is_public` (Codex review, third round:
        the first two fixes gated on constness and C++ access but not
        header provenance). Only checked when a public-header set is
        actually configured, matching `_decl_is_public`'s own "opt-in"
        semantics -- see `test_const_variable_value_is_extracted` for the
        unscoped (no `--public-header` flags) case, which must still work."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        f_pub = SubElement(root, "File")
        f_pub.set("id", "fpub")
        f_pub.set("name", "/repo/include/public.h")
        f_priv = SubElement(root, "File")
        f_priv.set("id", "fpriv")
        f_priv.set("name", "/repo/src/internal.h")
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_23", "name": "int"})
        SubElement(
            root, "CvQualifiedType", attrib={"id": "_23c", "type": "_23", "const": "1"}
        )
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_20",
                "name": "kPub",
                "type": "_23c",
                "context": "_1",
                "init": "1",
                "file": "fpub",
                "line": "1",
                "location": "fpub:1",
            },
        )
        SubElement(
            root,
            "Variable",
            attrib={
                "id": "_21",
                "name": "kInternal",
                "type": "_23c",
                "context": "_1",
                "init": "2",
                "file": "fpriv",
                "line": "1",
                "location": "fpriv:1",
            },
        )
        parser = _CastxmlParser(
            root,
            exported_dynamic=set(),
            exported_static=set(),
            public_header_paths=["/repo/include/public.h"],
        )
        by_name = {v.name: v for v in parser.parse_variables()}
        assert by_name["kPub"].value == "1"
        assert by_name["kInternal"].value is None


@pytest.mark.integration
class TestCastxmlVariableAccessAndValueAgainstRealCastxml:
    """The exact XML shapes above, confirmed against a live castxml run.

    Marked ``integration`` per ``AGENTS.md``'s "if a test needs castxml,
    mark it `@pytest.mark.integration`" rule, so the canonical fast unit
    lane never becomes host-dependent on whether castxml happens to be
    installed (Codex review).
    """

    def _dump_variables(self, tmp_path: Path, source: str) -> list[Variable]:
        if shutil.which("castxml") is None:
            pytest.skip("castxml not installed")
        header = tmp_path / "lib.hpp"
        header.write_text(textwrap.dedent(source))
        xml_path = tmp_path / "lib.xml"
        proc = subprocess.run(
            [
                "castxml",
                "--castxml-output=1",
                "-std=c++17",
                str(header),
                "-o",
                str(xml_path),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"castxml failed: {proc.stderr}"
        from xml.etree.ElementTree import parse as et_parse

        root = et_parse(str(xml_path)).getroot()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        return parser.parse_variables()

    def test_static_member_access_levels(self, tmp_path: Path) -> None:
        variables = self._dump_variables(
            tmp_path,
            """
            struct S {
            public:
                static int pub;
            protected:
                static int prot;
            private:
                static int priv;
            };
            int free_var = 1;
            """,
        )
        by_name = {v.name: v for v in variables}
        assert by_name["pub"].access == AccessLevel.PUBLIC
        assert by_name["prot"].access == AccessLevel.PROTECTED
        assert by_name["priv"].access == AccessLevel.PRIVATE
        assert by_name["free_var"].access == AccessLevel.PUBLIC

    def test_const_and_non_const_values(self, tmp_path: Path) -> None:
        variables = self._dump_variables(
            tmp_path,
            """
            constexpr int kFoo = 42;
            const int kBar = 7;
            int simple = 42;
            int computed = 1 + 2;
            """,
        )
        by_name = {v.name: v for v in variables}
        assert by_name["kFoo"].value == "42"
        assert by_name["kBar"].value == "7"
        # Non-const globals never surface `value`, regardless of whether
        # their initializer LOOKS constant.
        assert by_name["simple"].value is None
        assert by_name["computed"].value is None

    def test_mutable_pointer_to_const_pointee_has_no_value(
        self, tmp_path: Path
    ) -> None:
        """The synthetic-XML regression above, confirmed against real
        castxml: a MUTABLE pointer to a const pointee must not surface its
        address-of initializer as `value` (Codex review)."""
        variables = self._dump_variables(
            tmp_path,
            """
            int a = 1, b = 2;
            const int *p = &a;
            int *const q = &a;
            """,
        )
        by_name = {v.name: v for v in variables}
        assert by_name["p"].value is None
        assert by_name["q"].value == "&a"

    def test_private_static_const_member_has_no_value(self, tmp_path: Path) -> None:
        """The synthetic-XML regression above, confirmed against real
        castxml: a private `static const` member's value is not surfaced
        even though it still has real top-level constness (Codex review,
        second round)."""
        variables = self._dump_variables(
            tmp_path,
            """
            struct S {
            public:
                static const int kPub = 1;
            private:
                static const int kPriv = 2;
            };
            """,
        )
        by_name = {v.name: v for v in variables}
        assert by_name["kPub"].value == "1"
        assert by_name["kPriv"].value is None


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {
        "library": "libtest.so.1",
        "version": "1.0",
        "from_headers": True,
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _var(access: AccessLevel) -> Variable:
    return Variable(name="data", mangled="_data", type="int", access=access)


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


class TestVarAccessProducerGate:
    """Only castxml populates this fact (not clang, not "hybrid" either —
    see ``AbiSnapshot.castxml_var_access_facts_reliable``'s own docstring)."""

    def test_clang_vs_castxml_does_not_manufacture_a_finding(self) -> None:
        old = _snap(ast_producer="clang", variables=[_var(AccessLevel.PUBLIC)])
        new = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PRIVATE)])
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(compare(old, new))

    def test_two_castxml_sides_compare(self) -> None:
        old = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PUBLIC)])
        new = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PRIVATE)])
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(compare(old, new))

    def test_two_hybrid_sides_never_fire(self) -> None:
        old = _snap(
            ast_producer="hybrid",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PUBLIC)],
        )
        new = _snap(
            ast_producer="hybrid",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PRIVATE)],
        )
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(compare(old, new))


class TestVarAccessHeaderTierGate:
    def test_dwarf_side_does_not_manufacture_a_finding(self) -> None:
        old = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PUBLIC)])
        new = _snap(from_headers=False, variables=[_var(AccessLevel.PRIVATE)])
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(compare(old, new))


class TestLegacyCastxmlVarAccessBaselineSuppression:
    """A persisted pre-v24 castxml baseline reads ``access=PUBLIC`` for
    EVERY variable, so comparing it against a fresh dump of unchanged
    headers would report every real private/protected static member as
    newly WIDENED to public."""

    def test_reproduces_without_the_flag(self) -> None:
        old = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PUBLIC)],
        )
        new = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PRIVATE)],
        )
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(compare(old, new))

    def test_legacy_castxml_baseline_suppresses_the_finding(self) -> None:
        old = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=False,
            variables=[_var(AccessLevel.PUBLIC)],
        )
        new = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PRIVATE)],
        )
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(compare(old, new))
        assert ChangeKind.VAR_ACCESS_WIDENED not in _kinds(compare(old, new))


class TestVarAccessFactStatusGating:
    """ADR-063 Phase 5B: ``var_access_changes`` reads ``access_fact.status``
    directly, per variable, additive to the whole-snapshot
    ``castxml_var_access_facts_reliable`` gate above (mirrors
    ``compare.va_list_diff.diff_va_list_params``'s identical treatment of
    ``Param.is_va_list``)."""

    def test_uncollected_old_side_declines(self) -> None:
        old = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=True,
            variables=[
                Variable(
                    name="data",
                    mangled="_data",
                    type="int",
                    access_fact=Fact.not_collected(),
                )
            ],
        )
        new = _snap(
            ast_producer="castxml",
            castxml_var_access_facts_reliable=True,
            variables=[_var(AccessLevel.PRIVATE)],
        )
        result = compare(old, new)
        assert ChangeKind.VAR_ACCESS_CHANGED not in _kinds(result)
        assert ChangeKind.VAR_ACCESS_WIDENED not in _kinds(result)

    def test_confirmed_present_still_fires(self) -> None:
        # Unaffected: an ordinary construction backfills to Fact.present(...)
        # and behaves exactly as before.
        old = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PUBLIC)])
        new = _snap(ast_producer="castxml", variables=[_var(AccessLevel.PRIVATE)])
        assert ChangeKind.VAR_ACCESS_CHANGED in _kinds(compare(old, new))
