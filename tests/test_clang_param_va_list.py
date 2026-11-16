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
3. The gates the new fact needs at the detector: header-tier, "clang"-
   producer-ONLY (this fact is NOT symmetric across backends the way
   ``is_restrict`` is — castxml never populates it, and "hybrid" is
   excluded entirely, not merely reliability-gated, since a hybrid merge
   keeps castxml's verbatim params for every matched function — see
   ``diff_param_qualifiers.param_va_list_changes``'s docstring), and the
   pre-v23 legacy-baseline flag.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.extract.headers.clang.qualifiers import (
    _clang_param_is_va_list,
    builtin_va_list_decl_ids,
)
from abicheck.model import AbiSnapshot, Fact, Function, Param, Visibility
from tests._legacy_snapshot import as_legacy_baseline


def _param_node(
    qual_type: str, desugared: str | None = None, alias_id: str | None = None
) -> dict:
    """A ``ParmVarDecl``-shaped node carrying just the keys the predicate reads.

    ``alias_id`` is the ``typeAliasDeclId`` clang gives a typedef-named
    parameter: the id of the typedef declaration it names."""
    type_obj: dict[str, str] = {"qualType": qual_type}
    if desugared is not None:
        type_obj["desugaredQualType"] = desugared
    if alias_id is not None:
        type_obj["typeAliasDeclId"] = alias_id
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

    **Also requires x86-64 Linux specifically** — unlike ``is_restrict``
    (portable across every clang target), ``_clang_param_is_va_list`` is
    verified ONLY for the x86-64 System V ABI's decayed pointer spelling
    (its own docstring is explicit about this). Clang desugars ``va_list``
    completely differently elsewhere — e.g. a bare ``char *`` on the
    Windows MSVC target, ``struct __va_list`` on AArch64 Linux (Codex
    review, fresh evidence) — so asserting the x86-64 spellings
    unconditionally would genuinely FAIL (not just skip) on this repo's own
    macOS/Windows unit-test CI lanes wherever clang happens to be present.
    """

    def _parse(self, tmp_path: Path, source: str, cpp: bool) -> dict:
        if sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"):
            pytest.skip(
                "va_list spellings verified for x86-64 Linux only "
                f"(platform={sys.platform!r}, machine={platform.machine()!r})"
            )
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
    ``False``) with a clang side (real values) must not fire — the
    detector requires ``ast_producer == "clang"`` on both sides, not merely
    "both header-parsed", and NOT "hybrid" either (see the class below)."""

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


class TestParamVaListHybridExcluded:
    """``"hybrid"`` is excluded from the producer gate entirely, unlike
    ``param_restrict``'s (Codex review, fresh evidence). A hybrid merge
    keeps castxml's own ``params`` verbatim for every MATCHED function
    (``dumper_hybrid._merge_functions``), and castxml has never populated
    ``is_va_list`` at all — so a matched function's param reads a
    permanent, version-independent ``False``, unlike ``is_restrict`` where
    castxml IS a real producer and a matched function's castxml-verbatim
    param still carries a genuine answer.

    The concrete failure mode: the SAME function's parser coverage differs
    between the old and new snapshot — clang-only-appended (real value) in
    one, matched-by-both-and-therefore-blind (always False) in the other —
    which would read a real, unchanged ``va_list`` parameter as
    added/removed purely from that coverage shift."""

    def test_two_hybrid_sides_never_fire_even_when_reliable(self) -> None:
        """Positive control that this is a producer exclusion, not merely
        the reliability flag happening to be unset."""
        old = _snap(
            ast_producer="hybrid",
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="hybrid",
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_hybrid_vs_clang_never_fires(self) -> None:
        old = _snap(ast_producer="hybrid", functions=[_func(False)])
        new = _snap(ast_producer="clang", functions=[_func(True)])
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_coverage_shift_across_hybrid_snapshots_does_not_fire(self) -> None:
        """The scenario Codex's review named directly: a function that was
        clang-only-appended (real True) in the old hybrid snapshot becomes
        castxml-matched-and-therefore-blind (False) in the new one — a
        parser-coverage artifact, not a real qualifier change. Without the
        producer exclusion this reads as PARAM_LOST_VA_LIST."""
        old = _snap(ast_producer="hybrid", functions=[_func(True)])
        new = _snap(ast_producer="hybrid", functions=[_func(False)])
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(compare(old, new))


def _func_with_fact(fact: Fact[bool]) -> Function:
    return Function(
        name="logv",
        mangled="logv",
        return_type="void",
        params=[Param(name="ap", type="va_list", is_va_list_fact=fact)],
        visibility=Visibility.PUBLIC,
    )


class TestParamVaListFactStatusGating:
    """ADR-063 Phase 5B: even within an already producer-gated (clang-only,
    reliable) comparison, a single parameter's own ``is_va_list_fact`` can
    still be incomplete (e.g. that one parameter's extraction failed) —
    the snapshot-level ``clang_va_list_facts_reliable`` flag says the
    producer is trustworthy when it ran, not that it ran for every single
    parameter. ``param_va_list_changes`` must decline that one parameter
    rather than read its missing evidence as "confirmed not va_list"."""

    def test_not_collected_on_new_side_does_not_fabricate_became_va_list(
        self,
    ) -> None:
        old = _snap(
            ast_producer="clang", functions=[_func_with_fact(Fact.present(False))]
        )
        new = _snap(
            ast_producer="clang",
            functions=[_func_with_fact(Fact.not_collected())],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_failed_on_old_side_does_not_fabricate_lost_va_list(self) -> None:
        old = _snap(
            ast_producer="clang",
            functions=[_func_with_fact(Fact.failed("parse error"))],
        )
        new = _snap(
            ast_producer="clang", functions=[_func_with_fact(Fact.present(False))]
        )
        assert ChangeKind.PARAM_LOST_VA_LIST not in _kinds(compare(old, new))

    def test_both_sides_confirmed_present_still_compares_normally(self) -> None:
        """Behavior-preserving control: a fully-evidenced pair still fires."""
        old = _snap(
            ast_producer="clang", functions=[_func_with_fact(Fact.present(False))]
        )
        new = _snap(
            ast_producer="clang", functions=[_func_with_fact(Fact.present(True))]
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(compare(old, new))


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
    """A persisted pre-v23 clang baseline reads ``is_va_list=False``
    for EVERY parameter, so comparing it against a fresh dump of unchanged
    headers would report every ``va_list`` parameter as newly added."""

    def test_reproduces_without_the_flag(self) -> None:
        old = _snap(
            ast_producer="clang",
            functions=[_func(False)],
        )
        new = _snap(
            ast_producer="clang",
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST in _kinds(compare(old, new))

    def test_legacy_clang_baseline_suppresses_the_finding(self) -> None:
        old = as_legacy_baseline(
            _snap(
                ast_producer="clang",
                functions=[_func(False)],
            ),
            22,
        )
        new = _snap(
            ast_producer="clang",
            functions=[_func(True)],
        )
        assert ChangeKind.PARAM_BECAME_VA_LIST not in _kinds(compare(old, new))

    def test_unreliable_new_side_is_also_suppressed(self) -> None:
        old = _snap(
            ast_producer="clang",
            functions=[_func(True)],
        )
        new = as_legacy_baseline(
            _snap(
                ast_producer="clang",
                functions=[_func(False)],
            ),
            22,
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


class TestClangParamIsVaListPointerTarget:
    """The i386-style spelling: the parse keeps the typedef name and links it
    to its declaration, and desugars to a plain pointer (``__builtin_va_list``
    is ``char *``). Recognition is structural: the link must reach
    ``__builtin_va_list`` (:func:`builtin_va_list_decl_ids`), not just match a
    name."""

    BUILTIN, VA_LIST, MY_VA_LIST, USER_VA_LIST = "0xB", "0xV", "0xM", "0xU"
    IDS = frozenset({BUILTIN, VA_LIST, MY_VA_LIST})

    @pytest.mark.parametrize(
        ("qual_type", "desugared", "alias_id", "expected"),
        [
            ("va_list", "char *", "0xV", True),
            ("const va_list", "char *const", "0xV", True),
            ("volatile va_list", "char *volatile", "0xV", True),
            # A user alias of the libc va_list reaches __builtin_va_list too.
            ("my_va_list", "char *", "0xM", True),
            # A user typedef named va_list that does not reach __builtin_va_list.
            ("va_list", "char *", "0xU", False),
            # The same name over a non-pointer desugaring is not a pointer va_list.
            ("va_list", "int", "0xV", False),
            # A plain pointer with no typedef link is not one either.
            ("char *", "char *", None, False),
            # A name with no link in the set.
            ("va_list", "char *", None, False),
        ],
    )
    def test_name_path_spellings(
        self, qual_type: str, desugared: str, alias_id: str | None, expected: bool
    ) -> None:
        node = _param_node(qual_type, desugared, alias_id)
        assert _clang_param_is_va_list(node, self.IDS) is expected

    def test_builtin_va_list_decl_ids_follows_the_chain(self) -> None:
        def typedef(name: str, decl_id: str, under: str | None = None) -> dict:
            type_obj: dict[str, str] = {"qualType": name}
            if under is not None:
                type_obj["typeAliasDeclId"] = under
            return {
                "kind": "TypedefDecl",
                "name": name,
                "id": decl_id,
                "type": type_obj,
            }

        root = {
            "kind": "TranslationUnitDecl",
            "inner": [
                typedef("__builtin_va_list", "0xB"),
                typedef("va_list", "0xV", "0xB"),
                typedef("my_va_list", "0xM", "0xV"),
                typedef("va_list", "0xU"),  # user `typedef char *va_list;`
                typedef("my_fake", "0xF"),
            ],
        }
        assert builtin_va_list_decl_ids(root) == frozenset({"0xB", "0xV", "0xM"})

    def test_builtin_va_list_decl_ids_is_bounded_on_a_cycle(self) -> None:
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "name": "a",
                    "id": "0xA",
                    "type": {"qualType": "a", "typeAliasDeclId": "0xA"},
                }
            ],
        }
        assert builtin_va_list_decl_ids(root) == frozenset()

    def test_live_m32_parse_recognises_va_list(self, tmp_path: Path) -> None:
        import json
        import subprocess

        if shutil.which("clang") is None:
            pytest.skip("clang not installed")
        probe = subprocess.run(
            ["clang", "-m32", "-fsyntax-only", "-x", "c", "-"],
            input="int x;\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            pytest.skip("clang cannot target -m32 on this host")

        def ast_of(path: Path) -> dict:
            out = subprocess.run(
                [
                    "clang",
                    "-m32",
                    "-x",
                    "c",
                    "-fsyntax-only",
                    "-Xclang",
                    "-ast-dump=json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            return json.loads(out)

        def params_of(root: dict) -> dict[str, list[dict]]:
            params: dict[str, list[dict]] = {}

            def walk(node: object, fn: str | None) -> None:
                if isinstance(node, dict):
                    if node.get("kind") == "FunctionDecl":
                        fn = str(node.get("name", ""))
                    if node.get("kind") == "ParmVarDecl" and fn is not None:
                        params.setdefault(fn, []).append(node)
                    for value in node.values():
                        walk(value, fn)
                elif isinstance(node, list):
                    for item in node:
                        walk(item, fn)

            walk(root, None)
            return params

        header = tmp_path / "v.h"
        header.write_text(
            "#include <stdarg.h>\n"
            "void plain(int n, va_list ap);\n"
            "void qualified(int n, const va_list ap);\n"
            "void not_va_list(int n, char *p);\n"
        )
        root = ast_of(header)
        ids = builtin_va_list_decl_ids(root)
        params = params_of(root)
        expected = {"plain": True, "qualified": True, "not_va_list": False}
        for fn, want in expected.items():
            target = next(p for p in params[fn] if p.get("name") != "n")
            assert _clang_param_is_va_list(target, ids) is want, fn

        # A user typedef named va_list, with no libc header, is not recognised.
        user = tmp_path / "user.h"
        user.write_text("typedef char *va_list;\nvoid user_alias(int n, va_list ap);\n")
        user_root = ast_of(user)
        user_ids = builtin_va_list_decl_ids(user_root)
        target = next(
            p for p in params_of(user_root)["user_alias"] if p.get("name") != "n"
        )
        assert _clang_param_is_va_list(target, user_ids) is False


def test_live_m32_cxx_namespace_and_using_alias_are_structural(tmp_path: Path) -> None:
    """A typedef inside a namespace and a C++ ``using`` alias both link to the
    builtin ``va_list`` and are recognised; a namespaced user typedef to a plain
    pointer is not. Parsed live with ``clang++ -m32`` (clang 18 on this host)."""
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not installed")
    probe = subprocess.run(
        ["clang++", "-m32", "-fsyntax-only", "-x", "c++", "-"],
        input="int x;\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("clang++ cannot target -m32 on this host")
    header = tmp_path / "ns.h"
    header.write_text(
        "#include <stdarg.h>\n"
        "namespace api { typedef __builtin_va_list Args; void log(Args ap); }\n"
        "using Args2 = __builtin_va_list;\n"
        "void log2(Args2 ap);\n"
        "namespace api { typedef char *Fake; void f(Fake ap); }\n"
    )
    out = subprocess.run(
        [
            "clang++",
            "-m32",
            "-x",
            "c++",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    root = json.loads(out)
    ids = builtin_va_list_decl_ids(root)
    params: dict[str, dict] = {}

    def walk(node: object, fn: str | None) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "FunctionDecl":
                fn = str(node.get("name", ""))
            if node.get("kind") == "ParmVarDecl" and fn is not None:
                params[fn] = node
            for value in node.values():
                walk(value, fn)
        elif isinstance(node, list):
            for item in node:
                walk(item, fn)

    walk(root, None)
    expected = {"log": True, "log2": True, "f": False}
    for fn, want in expected.items():
        assert fn in params, f"clang emitted no ParmVarDecl for {fn}"
        assert _clang_param_is_va_list(params[fn], ids) is want, fn


class TestMalformedAliasNodesAreIgnored:
    """A declaration container may hold a non-object child, and an alias
    declaration may lack a string id or a string underlying link. Neither may
    crash the index or make a declaration count as reaching the builtin."""

    def test_non_object_children_in_a_container_are_skipped(self) -> None:
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [
                "stray-string",
                None,
                {
                    "kind": "TypedefDecl",
                    "id": "0xB",
                    "name": "__builtin_va_list",
                    "type": {"qualType": "char *"},
                },
                {
                    "kind": "TypedefDecl",
                    "id": "0xV",
                    "name": "va_list",
                    "type": {"qualType": "__builtin_va_list", "typeAliasDeclId": "0xB"},
                },
            ],
        }
        assert builtin_va_list_decl_ids(root) == frozenset({"0xB", "0xV"})

    def test_alias_without_a_string_id_is_not_indexed(self) -> None:
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "id": 7,
                    "name": "__builtin_va_list",
                    "type": {"qualType": "char *"},
                },
                {
                    "kind": "TypedefDecl",
                    "name": "__builtin_va_list",
                    "type": {"qualType": "char *"},
                },
            ],
        }
        assert builtin_va_list_decl_ids(root) == frozenset()

    def test_non_string_underlying_link_ends_the_chain(self) -> None:
        # A typeAliasDeclId that is not a string is no link: the typedef is
        # indexed under its own name, which is not __builtin_va_list, so the
        # chain stops and the declaration is excluded.
        root = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "id": "0xV",
                    "name": "va_list",
                    "type": {"qualType": "char *", "typeAliasDeclId": 42},
                },
            ],
        }
        assert builtin_va_list_decl_ids(root) == frozenset()

    @pytest.mark.parametrize("alias_id", [42, None, ["0xB"]])
    def test_non_string_param_alias_id_is_never_a_va_list_link(
        self, alias_id: object
    ) -> None:
        node = {
            "kind": "ParmVarDecl",
            "name": "ap",
            "type": {
                "qualType": "va_list",
                "desugaredQualType": "char *",
                "typeAliasDeclId": alias_id,
            },
        }
        assert _clang_param_is_va_list(node, frozenset({"0xB", "0xV"})) is False
