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

"""G31 Phase C continued — ``Param.is_va_list`` on the direct-clang L2 backend.

No backend populated this fact at all before schema v23: castxml never has,
and ``dumper_clang.py`` left every parameter at the model default ``False``.
``va_list`` is itself a typedef to an array-of-one-struct
(``__builtin_va_list`` on x86-64 System V, the one ABI verified here), so a
``va_list`` PARAMETER decays to a pointer the moment it crosses a function
boundary — there is no dedicated AST node or keyword, only the decayed
pointer's own printed spelling, verified against real clang 18 output.

Three layers are covered here, mirroring ``test_clang_param_restrict.py``:

1. The extraction predicate itself, over spellings real clang emits.
2. The same predicate against a LIVE ``clang -ast-dump=json`` tree.
3. The gates the new fact needs at the detector: header-tier, clang/hybrid-
   producer-only (this fact is NOT symmetric across backends the way
   ``is_restrict`` is — castxml never populates it), and the pre-v23
   legacy-baseline flag.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.dumper_clang_qualifiers import _clang_param_is_va_list
from abicheck.model import AbiSnapshot, Function, Param, Visibility


def _param_node(qual_type: str, desugared: str | None = None) -> dict:
    """A ``ParmVarDecl``-shaped node carrying just the keys the predicate reads."""
    type_obj: dict[str, str] = {"qualType": qual_type}
    if desugared is not None:
        type_obj["desugaredQualType"] = desugared
    return {"kind": "ParmVarDecl", "name": "ap", "type": type_obj}


class TestClangParamIsVaList:
    """The predicate, over the x86-64 System V spellings clang emits (see the
    live test below for confirmation these ARE clang's real spellings)."""

    @pytest.mark.parametrize(
        ("qual_type", "expected"),
        [
            # The plain C spelling, in both C and C++ mode (C++ drops the
            # `struct` keyword clang keeps in C).
            ("struct __va_list_tag *", True),
            ("__va_list_tag *", True),
            # A top-level `const`/`volatile` on the decayed pointee — what
            # `const va_list ap`/`volatile va_list ap` desugar to.
            ("const struct __va_list_tag *", True),
            ("volatile struct __va_list_tag *", True),
            ("const volatile struct __va_list_tag *", True),
            ("const __va_list_tag *", True),
            # A pointer TO a va_list (two stars once decayed) is a plain
            # pointer parameter forwarding a caller's own va_list, not a
            # va_list parameter itself.
            ("struct __va_list_tag **", False),
            ("__va_list_tag **", False),
            # An unrelated struct sharing no name with the va_list tag.
            ("struct S *", False),
            ("int *", False),
            ("int", False),
            # No pointer at all.
            ("struct __va_list_tag", False),
            # A type merely NAMED similarly must not match.
            ("struct __va_list_tag_like *", False),
        ],
    )
    def test_spellings(self, qual_type: str, expected: bool) -> None:
        assert _clang_param_is_va_list(_param_node(qual_type)) is expected

    def test_typedef_indirection_is_followed(self) -> None:
        """A parameter declared through a further user typedef of
        ``va_list`` still resolves through ``desugaredQualType``."""
        assert (
            _clang_param_is_va_list(_param_node("my_va_list", "struct __va_list_tag *"))
            is True
        )

    def test_missing_type_key_is_false(self) -> None:
        """A malformed/absent node must degrade to "not va_list", never raise."""
        assert _clang_param_is_va_list({"kind": "ParmVarDecl"}) is False


class TestClangParamIsVaListAgainstRealClang:
    """The spellings above, confirmed against a live clang AST dump.

    Deliberately NOT marked ``integration`` — see
    ``test_clang_param_restrict.py``'s identical class docstring for why:
    that marker's Linux gate requires castxml, which this predicate does not
    need. Each test self-skips on its own real requirement instead.
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
            #include <stdarg.h>
            typedef va_list my_va_list;
            void plain(int n, va_list ap);
            void via_typedef(int n, my_va_list ap);
            void qualified(int n, const va_list ap);
            void variadic(int n, ...);
            void not_va_list(int n, int *p);
            """,
            cpp=False,
        )
        params = self._params_by_function(root)
        expected = {
            "plain": True,
            "via_typedef": True,
            "qualified": True,
            "not_va_list": False,
        }
        for fn, want in expected.items():
            assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
            # `n` is the first param for every function above; the va_list
            # (or its stand-in) is always the second.
            target = next(p for p in params[fn] if p.get("name") != "n")
            assert _clang_param_is_va_list(target) is want, fn
        # `...` produces no ParmVarDecl at all — nothing to assert False on,
        # but confirms the variadic marker doesn't masquerade as one.
        assert len(params["variadic"]) == 1

    def test_cpp_spellings(self, tmp_path: Path) -> None:
        root = self._parse(
            tmp_path,
            """
            #include <cstdarg>
            void plain(int n, va_list ap);
            void not_va_list(int n, int *p);
            """,
            cpp=True,
        )
        params = self._params_by_function(root)
        for fn, want in (("plain", True), ("not_va_list", False)):
            assert fn in params, f"clang++ emitted no ParmVarDecl for {fn}"
            target = next(p for p in params[fn] if p.get("name") != "n")
            assert _clang_param_is_va_list(target) is want, fn
        # C++ drops the `struct` keyword clang keeps in C — confirming the
        # regex's `struct` optionality is load-bearing, not incidental.
        plain_va = next(p for p in params["plain"] if p.get("name") != "n")
        assert (plain_va.get("type") or {}).get("qualType") == "__va_list_tag *"

    def test_pointer_to_va_list_is_not_matched(self, tmp_path: Path) -> None:
        """A ``va_list *`` parameter forwards a caller's own already-decayed
        va_list by reference (``vsnprintf``-style wrappers) — a plain
        pointer parameter, not a va_list parameter itself."""
        root = self._parse(
            tmp_path,
            "#include <stdarg.h>\nvoid f(va_list *app);\n",
            cpp=False,
        )
        params = self._params_by_function(root)
        assert _clang_param_is_va_list(params["f"][0]) is False
        # Unlike the plain-`va_list` parameter case, clang does not desugar
        # this spelling at all (no `desugaredQualType` key) — the bare alias
        # `"va_list *"` is what `_clang_param_is_va_list` actually sees, and
        # it correctly finds no `__va_list_tag` in it.
        assert (params["f"][0].get("type") or {}).get("qualType") == "va_list *"


def _func(is_va_list: bool) -> Function:
    return Function(
        name="logv",
        mangled="logv",
        return_type="void",
        params=[Param(name="ap", type="va_list", is_va_list=is_va_list)],
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


class TestParamVaListProducerGate:
    """Unlike ``is_restrict``, this fact is NOT symmetric across backends:
    castxml has never populated it, so pairing a castxml side (always
    ``False``) with a clang/hybrid side (real values) must not fire — the
    detector requires ``ast_producer in ("clang", "hybrid")`` on both sides,
    not merely "both header-parsed"."""

    def test_castxml_vs_clang_does_not_manufacture_a_finding(self) -> None:
        """The dangerous asymmetric pairing: castxml's blanket False against
        clang's real True must not read as an addition."""
        old = _snap(ast_producer="castxml", functions=[_func(False)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_two_clang_sides_compare(self) -> None:
        """Positive control: a genuine clang-vs-clang transition still fires."""
        old = _snap(ast_producer="clang", functions=[_func(False)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(compare(old, new))

    def test_two_castxml_sides_never_fire(self) -> None:
        """Both always False on castxml — no finding, and correctly so (not
        merely coincidentally suppressed by the producer gate)."""
        old = _snap(ast_producer="castxml", functions=[_func(False)])
        new = _snap(ast_producer="castxml", functions=[_func(False)])
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))


class TestParamVaListHeaderTierGate:
    """``Param.is_va_list`` is populated by the direct-clang backend alone —
    DWARF, PDB, and the symbol-table paths never set it."""

    def test_dwarf_side_does_not_manufacture_a_removal(self) -> None:
        old = _snap(ast_producer="clang", functions=[_func(True)])
        new = _snap(from_headers=False, functions=[_func(False)])
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(compare(old, new))

    def test_inferred_header_awareness_is_not_enough(self) -> None:
        old = _snap(ast_producer="clang", functions=[_func(True)])
        new = _snap(ast_producer="clang", functions=[_func(False)])
        new.from_headers_inferred = True
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(compare(old, new))


class TestLegacyClangBaselineSuppression:
    """A persisted pre-v23 clang/hybrid baseline reads ``is_va_list=False``
    for EVERY parameter, so comparing it against a fresh dump of unchanged
    headers would report every ``va_list`` parameter as newly added."""

    def test_reproduces_without_the_flag(self) -> None:
        old = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=True,
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=True,
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(compare(old, new))

    def test_legacy_clang_baseline_suppresses_the_finding(self) -> None:
        old = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=False,
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=True,
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_unreliable_new_side_is_also_suppressed(self) -> None:
        old = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=True,
            functions=[_func(True)],
        )
        new = _snap(
            ast_producer="clang",
            clang_va_list_facts_reliable=False,
            functions=[_func(False)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))


class TestCrossBackendFalsePositiveClosed:
    """The whole-verdict-level version of the producer gate above."""

    def test_castxml_vs_clang_unchanged_header_is_no_change(self) -> None:
        old = _snap(ast_producer="castxml", functions=[_func(False)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        result = compare(old, new)
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(result)
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(result)
        assert result.verdict == Verdict.NO_CHANGE
