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

"""G31 Phase C — ``Param.is_restrict`` on the direct-clang L2 backend.

castxml was this fact's only producer from the day it shipped
(``dumper_castxml._resolve_cv_restrict``), while ``dumper_clang.py`` left
every parameter at the model default ``False``. Because
``diff_symbols._diff_param_restrict`` compares the two bools directly — with
no producer gate to decline on, unlike ``deprecated``/``is_scoped`` before
this phase — a castxml-vs-clang comparison of UNCHANGED headers reported
``PARAM_RESTRICT_CHANGED`` for every ``restrict``-qualified parameter.

Three layers are covered here:

1. The extraction predicate itself, over the type spellings real clang
   emits (pure; runs everywhere).
2. The same predicate against a LIVE ``clang -ast-dump=json`` tree, so the
   spellings asserted in (1) are the ones clang actually produces rather
   than ones this test made up (self-skipping, not ``integration`` — see
   that class's own docstring for why).
3. The two gates the new fact needs at the detector: a header-tier gate
   (DWARF/PDB/symbol paths never populate it at all) and the pre-v22
   legacy-baseline flag, each with a positive control confirming the
   suppression is not merely masking an already-inert case.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.dumper_clang import _clang_param_is_restrict
from abicheck.dumper_clang_qualifiers import _declarator_group
from abicheck.model import AbiSnapshot, Fact, Function, Param, Visibility


def _param_node(qual_type: str, desugared: str | None = None) -> dict:
    """A ``ParmVarDecl``-shaped node carrying just the keys the predicate reads."""
    type_obj: dict[str, str] = {"qualType": qual_type}
    if desugared is not None:
        type_obj["desugaredQualType"] = desugared
    return {"kind": "ParmVarDecl", "name": "p", "type": type_obj}


class TestClangParamIsRestrict:
    """The predicate, over the spellings clang emits (see the live test below
    for the confirmation that these ARE clang's spellings)."""

    @pytest.mark.parametrize(
        ("qual_type", "expected"),
        [
            # C spelling, the ordinary case.
            ("int *restrict", True),
            ("char *restrict", True),
            # C++ spellings. clang normalizes `__restrict__` to `__restrict`.
            ("int *__restrict", True),
            ("int *__restrict__", True),
            # Qualifier on the TOP-LEVEL pointer, alongside another qualifier.
            ("int *const restrict", True),
            ("int **restrict", True),
            # A REFERENCE to a restrict-qualified pointer. castxml stops at
            # the outer ReferenceType and reports False, so matching on the
            # qualifier still visible in clang's spelling would disagree
            # across backends on an unchanged header (Codex review).
            ("int *__restrict &", False),
            ("int *__restrict &&", False),
            ("int *&", False),
            ("int *(&)[3]", False),
            # Pointer whose POINTEE is restrict-qualified: the parameter
            # itself is a plain pointer. Scanning the whole spelling (rather
            # than only the part after the last top-level `*`) would wrongly
            # call this True.
            ("int *restrict *", False),
            # No qualifier at all.
            ("int *", False),
            ("int", False),
            # A type merely NAMED like the keyword must not match.
            ("restrict_like *", False),
            ("struct restrict_like *", False),
            # ── Parenthesized declarators ─────────────────────────────
            # C's declarator precedence puts the parameter's own `*` inside
            # parentheses whenever the pointee is an array or a function, so
            # NOTHING at depth 0 describes the parameter. Both review rounds
            # on this predicate landed here, one per direction.
            #
            # A CALLBACK parameter: the `restrict` belongs to the callback's
            # ARGUMENT. Scanning the whole spelling said True, against
            # castxml's False (Codex review, round 1).
            ("void (*)(int *restrict)", False),
            ("void (*)(int *)", False),
            ("void (*)(int)", False),
            # A pointer to an ARRAY: a legal, genuinely restrict-qualified
            # object pointer that castxml does see. Requiring a depth-0 `*`
            # said False (Codex review, round 2).
            ("int (*restrict)[3]", True),
            ("int (*)[3]", False),
            # Same, one indirection out: the qualifier is on the parameter's
            # own (outer) pointer...
            ("int (**restrict)[3]", True),
            # ...versus on the inner one, where the parameter itself is
            # plain — the parenthesized twin of `int *restrict *`.
            ("int (*restrict *)[3]", False),
            # A pointer to an array OF POINTERS: the leading depth-0 `*`
            # belongs to the element type, not the parameter.
            ("int *(*restrict)[3]", True),
            # The case neither review round named, and the one that shows
            # why "declarator group wins" is the rule rather than "prefer
            # depth 0": a function pointer with a pointer RETURN type has a
            # depth-0 `*` (from `int *`), so searching after it would scan
            # the callback's parameter list and match its `restrict`.
            ("int *(*)(int *restrict)", False),
            # ── Nested declarators: the INNERMOST group is the parameter's
            # own. These two differ only in which level carries the
            # qualifier, so taking the outer group answers both alike and
            # gets the second wrong (Codex review, round 4).
            #
            # `int (*(*restrict p)[3])[2]` — p IS the restrict pointer.
            ("int (*(*restrict)[3])[2]", True),
            # `int (*restrict (*p)[3])[2]` — p is a PLAIN pointer; the
            # qualifier belongs to the array's element type.
            ("int (*restrict (*)[3])[2]", False),
            # An expression operand is not a declarator: `decltype(`'s span
            # opens with a dereference, not a pointer declarator, and
            # treating it as the group searches `*gp + 0` and misses the
            # real qualifier (Codex review, round 4).
            ("decltype(*gp + 0) *__restrict", True),
            ("decltype(*gp + 0) *", False),
            # ...and clang prints `typeof` SPACED, whatever the source
            # wrote, so "directly follows an identifier character" never
            # recognized it — it caught `decltype(` only by luck of
            # spacing (Codex review, round 5).
            ("typeof (*gp) *restrict", True),
            ("typeof (*gp) *", False),
            ("typeof(*gp) *__restrict", True),
        ],
    )
    def test_spellings(self, qual_type: str, expected: bool) -> None:
        assert _clang_param_is_restrict(_param_node(qual_type)) is expected

    def test_a_non_pointer_spelling_is_never_restrict(self) -> None:
        """No pointer anywhere means no qualification to find — and notably
        NOT the "search the whole spelling" fallback `_field_own_cv_source`
        uses for a const/volatile field, which is what let a callback
        argument's qualifier leak in."""
        assert _clang_param_is_restrict(_param_node("struct S")) is False
        assert _clang_param_is_restrict(_param_node("int &")) is False


class TestDeclaratorGroup:
    """`_declarator_group` — which parenthesized group holds the declared
    entity's own pointer, as opposed to a parameter list or a pointee."""

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            # A parenthesized pointer declarator, array and function pointee.
            ("int (*restrict)[3]", "*restrict"),
            ("void (*)(int *restrict)", "*"),
            ("int *(*restrict)[3]", "*restrict"),
            ("int (*restrict *)[3]", "*restrict *"),
            # A function pointer's trailing PARAMETER LIST is not one: its
            # contents start with a type name, not `*`.
            ("void (int *restrict)", None),
            # An ordinary declarator has no group at all — depth 0 is where
            # the answer lives.
            ("int *restrict", None),
            ("int", None),
            # A template argument list is not a declarator group either
            # (nor is a `(` nested inside one at depth > 0).
            ("std::function<void(int *)>", None),
            # A nested declarator resolves to the INNERMOST group, which is
            # the declared entity's own pointer.
            ("int (*(*restrict)[3])[2]", "*restrict"),
            ("void (*(*restrict)[3])(int)", "*restrict"),
            ("int (*restrict (*)[3])[2]", "*"),
            # A call-like expression operand is not a declarator group, even
            # though its span begins with `*`.
            ("decltype(*gp + 0) *__restrict", None),
            ("typeof(*gp) *__restrict", None),
            # The spaced form clang actually prints. A whitespace-skipping
            # "follows an identifier" rule would classify this correctly
            # but break the declarator rows above, which read as
            # `<identifier><space>(` too — the keyword itself is the only
            # discriminator (Codex review, round 5).
            ("typeof (*gp) *restrict", None),
            ("__typeof__ (*gp) *restrict", None),
        ],
    )
    def test_group(self, spelling: str, expected: str | None) -> None:
        assert _declarator_group(spelling) == expected

    def test_whitespace_after_the_open_paren(self) -> None:
        """clang emits no space there today, but the scan tolerates one
        rather than silently misclassifying the group if it ever does."""
        assert _declarator_group("int ( *restrict)[3]") == " *restrict"

    def test_nested_group_resolves_to_the_outer_pointer(self) -> None:
        """The nested-group spellings above answer True end to end: the
        qualifier sits on the outermost `*`, which is the parameter's own."""
        assert _clang_param_is_restrict(_param_node("int (*(*restrict)[3])[2]")) is True

    def test_typedef_indirection_is_followed(self) -> None:
        """A parameter declared through ``typedef int *restrict rptr;``
        renders ``qualType`` as the bare alias — the real qualification is
        only in ``desugaredQualType``. castxml follows its own ``Typedef``
        chain for the identical reason, so reading the sugared spelling
        alone would make the two backends disagree on the same source."""
        assert _clang_param_is_restrict(_param_node("rptr", "int *restrict")) is True

    def test_typedef_to_unqualified_pointer_stays_false(self) -> None:
        assert _clang_param_is_restrict(_param_node("iptr", "int *")) is False

    def test_missing_type_key_is_false(self) -> None:
        """A malformed/absent node must degrade to "not restrict", never raise."""
        assert _clang_param_is_restrict({"kind": "ParmVarDecl"}) is False


class TestClangParamIsRestrictAgainstRealClang:
    """The spellings above, confirmed against a live clang AST dump.

    Guards the class of bug where a predicate is written against an assumed
    output format and silently answers False for every real input (the same
    failure the G31 Phase C vptr guard hit — see the root AGENTS.md entry).

    Deliberately NOT marked ``integration``, for the reason
    ``test_clang_header_backend_integration.py``'s module comment records:
    that marker's Linux gate requires **castxml**
    (``tests/conftest.py::_integration_skip_reason``), which would skip
    these on exactly the castxml-absent, clang-only host they describe.
    Each test self-skips on its own real requirement instead — two
    ``-fsyntax-only`` invocations over a five-line file, so the fast lane
    pays microseconds where clang exists and skips where it doesn't.
    """

    def _parse(self, tmp_path: Path, source: str, cpp: bool) -> dict:
        binary = "clang++" if cpp else "clang"
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} not installed")
        src = tmp_path / ("t.cpp" if cpp else "t.c")
        src.write_text(source)
        cmd = [binary, "-Xclang", "-ast-dump=json", "-fsyntax-only"]
        if not cpp:
            cmd += ["-x", "c"]
        cmd.append(str(src))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # Skip ONLY for a missing binary (handled above). Once clang is found,
        # every fixture in this file is expected to compile, so a failure here
        # is a regression — a future clang rejecting one of these spellings, or
        # changing the dump flag — and turning it into a skip would make the
        # whole class silently green (CodeRabbit review; the repo's own
        # silent-skip guard exists for exactly this).
        assert proc.returncode == 0, (
            f"{binary} failed on the fixture source: {proc.stderr}"
        )
        assert proc.stdout, f"{binary} produced no AST dump: {proc.stderr}"
        return json.loads(proc.stdout)

    def _params_by_function(self, root: dict) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}

        def walk(node: object, fn: str | None) -> None:
            if not isinstance(node, dict):
                return
            if node.get("kind") == "FunctionDecl":
                fn = str(node.get("name", ""))
                out.setdefault(fn, [])
            if node.get("kind") == "ParmVarDecl" and fn is not None:
                out.setdefault(fn, []).append(node)
            for child in node.get("inner", []) or []:
                walk(child, fn)

        walk(root, None)
        return out

    def test_c_spellings(self, tmp_path: Path) -> None:
        root = self._parse(
            tmp_path,
            """
            typedef int *restrict rptr;
            void via_typedef(rptr p);
            void inner_restrict(int *restrict *p);
            void top_level_restrict(int **restrict p);
            void const_and_restrict(int *const restrict p);
            void plain(int *p);
            """,
            cpp=False,
        )
        params = self._params_by_function(root)
        expected = {
            "via_typedef": True,
            "inner_restrict": False,
            "top_level_restrict": True,
            "const_and_restrict": True,
            "plain": False,
        }
        for fn, want in expected.items():
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(params[fn][0]) is want, fn

    def test_callback_parameters(self, tmp_path: Path) -> None:
        """The Codex-review case, against real output rather than a fixture.

        A callback parameter's own ``*`` sits inside parentheses, so there is
        no depth-0 pointer and the qualifier found in the spelling belongs to
        the callback's ARGUMENT. Reading the whole spelling (the fallback
        `_field_own_cv_source` uses for a const/volatile field) answered True
        here, against castxml's False.
        """
        root = self._parse(
            tmp_path,
            """
            void takes_cb(void (*cb)(int *restrict));
            void takes_cb_plain(void (*cb)(int *));
            """,
            cpp=False,
        )
        params = self._params_by_function(root)
        for fn in ("takes_cb", "takes_cb_plain"):
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(params[fn][0]) is False, fn

        # The spelling the first answer depends on.
        assert (params["takes_cb"][0].get("type") or {}).get(
            "qualType"
        ) == "void (*)(int *restrict)"

    def test_declarator_corpus(self, tmp_path: Path) -> None:
        """Every declarator shape, against the compiler, in one sweep.

        Each function's name declares the expected answer — ``_r`` means the
        PARAMETER ITSELF is restrict-qualified, ``_n`` means it is not — so a
        case cannot be added without saying what it should do. This corpus is
        what actually found the nested-declarator bug that four rounds of
        hand-picked examples had each missed in a different way; the shapes
        below are exactly the ones where a plausible-sounding rule and the C
        declarator rules disagree.
        """
        root = self._parse(
            tmp_path,
            """
            void a_r(int *restrict p);              void a_n(int *p);
            void b_r(int **restrict p);             void b_n(int **p);
            void c_r(int (*restrict p)[3]);         void c_n(int (*p)[3]);
            void d_r(int *(*restrict p)[3]);        void d_n(int *(*p)[3]);
            void e_r(int (*(*restrict p)[3])[2]);   void e_n(int (*(*p)[3])[2]);
            /* p is a plain pointer: the qualifier is on the element type. */
            void f_n(int (*restrict (*p)[3])[2]);
            /* p is a plain pointer: the INNER pointer is qualified. */
            void g_n(int *restrict *p);
            void h_n(int (*restrict *p)[3]);
            /* callback: the qualifier belongs to the callback's argument. */
            void i_n(void (*p)(int *restrict));
            void j_n(int *(*p)(int *restrict));
            """,
            cpp=False,
        )
        params = self._params_by_function(root)
        checked = 0
        for fn, nodes in params.items():
            if not nodes or not fn.endswith(("_r", "_n")):
                continue
            want = fn.endswith("_r")
            spelling = (nodes[0].get("type") or {}).get("qualType")
            assert _clang_param_is_restrict(nodes[0]) is want, f"{fn}: {spelling!r}"
            checked += 1
        assert checked == 15, f"corpus shrank to {checked} parameters"

    def test_type_operator_operands_are_not_declarators(self, tmp_path: Path) -> None:
        """`typeof (E)`/`decltype(E)` wrap an EXPRESSION, not a declarator.

        The point of doing this against the compiler rather than a fixture:
        clang prints `typeof` **spaced** no matter which spelling the source
        used — `typeof(*gp)` comes back as `typeof (*gp)` — so a rule keyed
        on the character immediately before `(` sees a space, calls `(*gp)`
        the declarator group, searches `*gp`, and misses the parameter's own
        qualifier entirely (Codex review, round 5). `decltype(` is printed
        unspaced, which is the only reason the earlier rule passed for it.

        The obvious repair — skip whitespace, then require an identifier —
        is wrong in the other direction: `int (*restrict)[3]` and `void
        (*)(int *restrict)` also read as `<identifier><space>(`, so both
        would be dismissed as expression operands. Only the specific
        keyword separates the two families, which is what makes the closed
        keyword set load-bearing rather than incidental.
        """
        c_root = self._parse(
            tmp_path,
            """
            extern int *gp;
            void spaced(typeof (*gp) *restrict p);
            void unspaced(typeof(*gp) *restrict p);
            void gnu_spelling(__typeof__ (*gp) *restrict p);
            void no_qualifier(typeof (*gp) *p);
            """,
            cpp=False,
        )
        c_params = self._params_by_function(c_root)
        c_expected = {
            "spaced": True,
            "unspaced": True,
            "gnu_spelling": True,
            "no_qualifier": False,
        }
        for fn, want in c_expected.items():
            assert fn in c_params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(c_params[fn][0]) is want, fn

        cpp_root = self._parse(
            tmp_path,
            """
            extern int *gp;
            void decl(decltype(*gp + 0) *__restrict p);
            void decl_plain(decltype(*gp + 0) *p);
            """,
            cpp=True,
        )
        cpp_params = self._params_by_function(cpp_root)
        for fn, want in (("decl", True), ("decl_plain", False)):
            assert fn in cpp_params, f"clang++ emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(cpp_params[fn][0]) is want, fn

    def test_reference_wrappers_match_castxml(self, tmp_path: Path) -> None:
        """castxml's `_resolve_cv_restrict` stops at the outer ReferenceType,
        so a reference to a restrict-qualified pointer is False there. clang
        still prints the qualifier, so matching it would disagree across
        backends on an unchanged header (Codex review)."""
        root = self._parse(
            tmp_path,
            """
            void lref(int *__restrict &p);
            void rref(int *__restrict &&p);
            void plain_ref(int *&p);
            void ptr(int *__restrict p);
            """,
            cpp=True,
        )
        params = self._params_by_function(root)
        expected = {"lref": False, "rref": False, "plain_ref": False, "ptr": True}
        for fn, want in expected.items():
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(params[fn][0]) is want, fn
        # The qualifier really is still in the spelling clang printed — the
        # False above is a deliberate match to castxml, not a parse miss.
        assert "__restrict" in (params["lref"][0].get("type") or {}).get("qualType", "")

    def test_parenthesized_object_pointers(self, tmp_path: Path) -> None:
        """The other half of the declarator story: a pointer to an ARRAY is
        parenthesized exactly like a function pointer, but IS a legal
        restrict-qualified object pointer that castxml sees. Requiring a
        depth-0 pointer (the first fix for the callback case) reported False
        for all of these (Codex review, round 2)."""
        root = self._parse(
            tmp_path,
            """
            void arr_ptr(int (*restrict p)[3]);
            void arr_ptr_plain(int (*p)[3]);
            void outer_restrict(int (**restrict p)[3]);
            void inner_restrict(int (*restrict *p)[3]);
            void arr_of_ptr(int *(*restrict p)[3]);
            void cb_ret_ptr(int *(*cb)(int *restrict));
            """,
            cpp=False,
        )
        params = self._params_by_function(root)
        expected = {
            "arr_ptr": True,
            "arr_ptr_plain": False,
            "outer_restrict": True,
            # The parameter is a plain pointer to a restrict pointer.
            "inner_restrict": False,
            # The leading depth-0 `*` belongs to the array's element type.
            "arr_of_ptr": True,
            # A depth-0 `*` from the RETURN type, with the only `restrict`
            # in the callback's parameter list.
            "cb_ret_ptr": False,
        }
        for fn, want in expected.items():
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(params[fn][0]) is want, fn

    def test_a_restrict_function_pointer_is_not_legal_c(self, tmp_path: Path) -> None:
        """Why answering False for a function-pointer parameter loses nothing.

        C11 6.7.3p2 allows `restrict` only on a pointer to an OBJECT type, so
        there is no legal declaration the depth-0-pointer requirement misses.
        Pinned against the real compiler rather than cited, since the whole
        no-false-negative claim rests on it.
        """
        if shutil.which("clang") is None:
            pytest.skip("clang not installed")
        src = tmp_path / "fnptr.c"
        src.write_text("void f(void (*restrict cb)(int));\n")
        proc = subprocess.run(
            ["clang", "-fsyntax-only", "-x", "c", str(src)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "may not be 'restrict' qualified" in proc.stderr

    def test_cpp_spellings(self, tmp_path: Path) -> None:
        root = self._parse(
            tmp_path,
            """
            struct restrict_like { int x; };
            void underscored(int *__restrict p);
            void double_underscored(int *__restrict__ p);
            void named_like_keyword(restrict_like *p);
            """,
            cpp=True,
        )
        params = self._params_by_function(root)
        expected = {
            "underscored": True,
            "double_underscored": True,
            "named_like_keyword": False,
        }
        for fn, want in expected.items():
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            assert _clang_param_is_restrict(params[fn][0]) is want, fn


def _func(is_restrict: bool) -> Function:
    return Function(
        name="memcopy",
        mangled="memcopy",
        return_type="void",
        params=[Param(name="dst", type="void *", is_restrict=is_restrict)],
        visibility=Visibility.PUBLIC,
    )


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {
        "library": "libtest.so.1",
        "version": "1.0",
        "from_headers": True,
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


class TestCrossBackendFalsePositiveClosed:
    """The bug this extraction closes, stated at the comparison level."""

    def test_castxml_vs_clang_unchanged_header_reports_nothing(self) -> None:
        """Both backends now populate the fact, so the same
        ``restrict``-qualified parameter compares equal across producers.
        Before this phase the clang side read ``False`` unconditionally and
        this pair fired PARAM_RESTRICT_CHANGED."""
        old = _snap(ast_producer="castxml", functions=[_func(True)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        result = compare(old, new)
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(result)
        # Identical surfaces: the pair is not merely non-breaking, it is
        # indistinguishable — which is the point, since the two snapshots
        # describe the same unchanged header through different backends.
        assert result.verdict == Verdict.NO_CHANGE

    def test_real_cross_producer_removal_still_fires(self) -> None:
        """Positive control: closing the false positive must not also
        suppress a genuine cross-producer change."""
        old = _snap(ast_producer="castxml", functions=[_func(True)])
        new = _snap(ast_producer="clang", functions=[_func(False)])
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(compare(old, new))


class TestParamRestrictHeaderTierGate:
    """``Param.is_restrict`` is populated by the header-AST backends alone —
    DWARF, PDB, and the symbol-table paths never set it — so a non-header
    side's ``False`` means "not collected", not "not qualified"."""

    def test_dwarf_side_does_not_manufacture_a_removal(self) -> None:
        old = _snap(ast_producer="castxml", functions=[_func(True)])
        new = _snap(from_headers=False, functions=[_func(False)])
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))

    def test_dwarf_side_does_not_manufacture_an_addition(self) -> None:
        old = _snap(from_headers=False, functions=[_func(False)])
        new = _snap(ast_producer="castxml", functions=[_func(True)])
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))

    def test_inferred_header_awareness_is_not_enough(self) -> None:
        """A legacy snapshot whose header-awareness was only GUESSED is
        excluded the same way ``param_defaults`` excludes it."""
        old = _snap(ast_producer="castxml", functions=[_func(True)])
        new = _snap(functions=[_func(False)])
        new.from_headers_inferred = True
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))

    def test_two_header_sides_still_compare(self) -> None:
        """Positive control for the gate above."""
        old = _snap(ast_producer="castxml", functions=[_func(True)])
        new = _snap(ast_producer="castxml", functions=[_func(False)])
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(compare(old, new))


class TestLegacyClangBaselineSuppression:
    """A persisted pre-v22 clang/hybrid baseline reads ``is_restrict=False``
    for EVERY parameter, so comparing it against a fresh dump of unchanged
    headers would report every ``restrict`` as newly added."""

    def test_reproduces_without_the_flag(self) -> None:
        """Establishes the bug is real absent the fix: two sides that both
        claim reliable restrict facts (the pre-flag world) genuinely produce
        the finding for this pair."""
        old = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(compare(old, new))

    def test_legacy_clang_baseline_suppresses_the_finding(self) -> None:
        old = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=False,
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))

    def test_unreliable_new_side_is_also_suppressed(self) -> None:
        """The reverse direction (a fresh baseline compared against an older
        tool's output) reads as a REMOVAL, and is declined the same way."""
        old = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func(True)],
        )
        new = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=False,
            functions=[_func(False)],
        )
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))


def _func_with_restrict_fact(fact: Fact[bool]) -> Function:
    return Function(
        name="memcopy",
        mangled="memcopy",
        return_type="void",
        params=[Param(name="dst", type="void *", is_restrict_fact=fact)],
        visibility=Visibility.PUBLIC,
    )


class TestParamRestrictFactStatusGating:
    """ADR-063 Phase 5B: ``param_restrict_changes`` reads ``is_restrict_
    fact.status`` directly, per parameter, additive to the whole-snapshot
    ``clang_restrict_facts_reliable`` gate above (mirrors
    ``compare.va_list_diff.diff_va_list_params``'s identical treatment of
    the sibling ``Param`` qualifier fact)."""

    def test_uncollected_old_side_declines(self) -> None:
        old = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func_with_restrict_fact(Fact.not_collected())],
        )
        new = _snap(
            ast_producer="clang",
            clang_restrict_facts_reliable=True,
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_RESTRICT_CHANGED not in _kinds(compare(old, new))

    def test_confirmed_present_still_fires(self) -> None:
        # Unaffected: an ordinary construction backfills to Fact.present(...)
        # and behaves exactly as before.
        old = _snap(ast_producer="clang", functions=[_func(False)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        assert ChangeKind.PARAM_RESTRICT_CHANGED in _kinds(compare(old, new))
