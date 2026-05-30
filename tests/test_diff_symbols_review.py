# Copyright 2026 Nikolay Petrov
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

"""PR #256 CodeRabbit review regression tests.

Finding A — extern-C fallback must use a multimap and NOT mis-pair a
  removed/added function with an unrelated same-named C++ sibling.

Finding B — _detect_newly_deleted_functions must NOT emit FUNC_DELETED for
  hidden/internal (non-ABI-visible) functions.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import AbiSnapshot, Function, Visibility

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(functions: list[Function] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions or [],
    )


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Finding A — multimap extern-C fallback
# ---------------------------------------------------------------------------

class TestExternCFallbackMultimap:
    """Extern-C name fallback must NOT mis-pair when a same-named C++ sibling exists."""

    def test_extern_c_removed_with_cpp_sibling_is_not_mis_paired(self) -> None:
        """An extern-C function removed from old snapshot must NOT be matched to an
        unrelated same-named C++ overload in new snapshot.

        Scenario:
          old: extern "C" void foo(void)  mangled=foo        (removed)
               void foo(int)              mangled=_Z3fooi     (unchanged)
          new: void foo(int)              mangled=_Z3fooi     (same C++ overload)
               void foo(float)            mangled=_Z3foof     (new C++ overload, same name)

        The old extern-C foo has no mangled-name match in new_map, and the name
        "foo" maps to TWO new candidates (neither is extern-C), so the multimap
        filter must NOT fall back and must instead emit FUNC_REMOVED.
        """
        old_extern_c = _func("foo", "foo", is_extern_c=True)
        old_cpp_int = _func("foo", "_Z3fooi", is_extern_c=False)
        new_cpp_int = _func("foo", "_Z3fooi", is_extern_c=False)
        new_cpp_float = _func("foo", "_Z3foof", is_extern_c=False)

        r = compare(
            _snap(functions=[old_extern_c, old_cpp_int]),
            _snap(functions=[new_cpp_int, new_cpp_float]),
        )
        # The extern-C foo (mangled="foo") must be reported removed,
        # not silently matched to the new C++ overload.
        assert ChangeKind.FUNC_REMOVED in _kinds(r)
        # The new C++ overload (float) must be reported added.
        assert ChangeKind.FUNC_ADDED in _kinds(r)
        # Ensure the removed symbol is the extern-C one.
        removed = [c for c in r.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert any(c.symbol == "foo" for c in removed), (
            "extern-C 'foo' must be the removed symbol, not a C++ overload"
        )

    def test_extern_c_removal_with_unique_extern_c_peer_is_still_matched(self) -> None:
        """When old is C++ and new side has EXACTLY ONE extern-C candidate, fallback fires.

        Scenario:
          old: void bar(void)  mangled=_Z3barv  is_extern_c=False
          new: void bar(void)  mangled=bar      is_extern_c=True

        The mangled names differ (C++ vs C linkage), but new has exactly one
        extern-C candidate for the name "bar", so the fallback should pair them
        and emit FUNC_LANGUAGE_LINKAGE_CHANGED rather than FUNC_REMOVED + FUNC_ADDED.
        """
        f_old = _func("bar", "_Z3barv", is_extern_c=False)
        f_new = _func("bar", "bar", is_extern_c=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        # Linkage changed — must be detected via fallback matching.
        assert ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED in _kinds(r)
        # Must NOT be reported as a simple remove+add pair.
        assert ChangeKind.FUNC_REMOVED not in _kinds(r)
        assert ChangeKind.FUNC_ADDED not in _kinds(r)

    def test_old_extern_c_with_unique_new_candidate_is_matched(self) -> None:
        """Old side is extern "C"; new side has exactly one same-named function.

        The old extern-C function can match any single same-named new candidate
        regardless of whether that candidate is also extern-C, because there is
        no ambiguity when there is only one option.
        """
        f_old = _func("init", "init", is_extern_c=True)
        f_new = _func("init", "init", is_extern_c=True, return_type="int")

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        # Return type change must be detected via fallback matching.
        assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(r)
        assert ChangeKind.FUNC_REMOVED not in _kinds(r)

    def test_no_fallback_when_multiple_candidates_and_old_is_cpp(self) -> None:
        """Multiple same-named new candidates and old is C++ linkage — no fallback.

        When old is NOT extern-C and there are multiple new candidates with the
        same plain name but none is extern-C, the fallback must not fire,
        producing FUNC_REMOVED for the old function.
        """
        f_old = _func("process", "_Z7processv", is_extern_c=False)
        f_new_a = _func("process", "_Z7processi", is_extern_c=False)
        f_new_b = _func("process", "_Z7processf", is_extern_c=False)

        r = compare(
            _snap(functions=[f_old]),
            _snap(functions=[f_new_a, f_new_b]),
        )
        # Old C++ function has no mangled match and no unique extern-C peer —
        # must be reported removed.
        assert ChangeKind.FUNC_REMOVED in _kinds(r)


# ---------------------------------------------------------------------------
# Finding B — visibility gate in _detect_newly_deleted_functions
# ---------------------------------------------------------------------------

class TestNewlyDeletedVisibilityGate:
    """_detect_newly_deleted_functions must not report HIDDEN/internal functions."""

    def test_hidden_deleted_function_does_not_emit_func_deleted(self) -> None:
        """A hidden function that gains = delete must NOT produce FUNC_DELETED.

        Hidden functions are not part of the public ABI surface; callers cannot
        reference them, so a = delete marker on them cannot be an ABI break.
        """
        f_old = _func("internal_helper", "_Z15internal_helperv", visibility=Visibility.HIDDEN)
        f_new = _func("internal_helper", "_Z15internal_helperv",
                       visibility=Visibility.HIDDEN, is_deleted=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        kinds = _kinds(r)

        assert ChangeKind.FUNC_DELETED not in kinds, (
            "FUNC_DELETED must not fire for a hidden (non-ABI-visible) function"
        )
        assert ChangeKind.FUNC_DELETED_DWARF not in kinds

    def test_public_deleted_function_still_emits_func_deleted(self) -> None:
        """Regression guard: a PUBLIC function gaining = delete MUST emit FUNC_DELETED."""
        f_old = _func("api_fn", "_Z5api_fnv", visibility=Visibility.PUBLIC)
        f_new = _func("api_fn", "_Z5api_fnv", visibility=Visibility.PUBLIC, is_deleted=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))

        assert ChangeKind.FUNC_DELETED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_elf_only_deleted_function_emits_func_deleted(self) -> None:
        """ELF_ONLY visibility is part of the public ABI surface; = delete must be reported."""
        f_old = _func("elf_fn", "_Z6elf_fnv", visibility=Visibility.ELF_ONLY)
        f_new = _func("elf_fn", "_Z6elf_fnv", visibility=Visibility.ELF_ONLY, is_deleted=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))

        assert ChangeKind.FUNC_DELETED in _kinds(r)
        assert r.verdict == Verdict.BREAKING

    def test_hidden_deleted_already_in_old_no_change(self) -> None:
        """A hidden function that was already deleted in old must not emit anything."""
        f_old = _func("priv", "_Z4privv", visibility=Visibility.HIDDEN, is_deleted=True)
        f_new = _func("priv", "_Z4privv", visibility=Visibility.HIDDEN, is_deleted=True)

        r = compare(_snap(functions=[f_old]), _snap(functions=[f_new]))
        kinds = _kinds(r)

        assert ChangeKind.FUNC_DELETED not in kinds
        assert ChangeKind.FUNC_DELETED_DWARF not in kinds
