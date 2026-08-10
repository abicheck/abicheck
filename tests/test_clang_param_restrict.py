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
   than ones this test made up (``integration``).
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
from abicheck.model import AbiSnapshot, Function, Param, Visibility


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
            # Reference to a restrict-qualified pointer.
            ("int *__restrict &", True),
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
        ],
    )
    def test_spellings(self, qual_type: str, expected: bool) -> None:
        assert _clang_param_is_restrict(_param_node(qual_type)) is expected

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
        if proc.returncode != 0 or not proc.stdout:
            pytest.skip("clang did not produce an AST dump")
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
