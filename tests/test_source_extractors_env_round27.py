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

"""Sibling split, round 27's Codex review Finding 1: continuing round 26's
Finding 3 (the ``_EnvPathCleared``/``_ENV_PATH_CLEARED`` sentinel) into the
downstream compiler-path RESOLUTION consumers (``pick_compiler_binary``,
``header_compile_context._derived_gcc_path``) -- not just the internal
PATH-tracking traversal round 26 fixed.

Round 26 correctly told apart "PATH was explicitly cleared" (``env -i``/
``env -u PATH``) from "no opinion, inherit whatever came before" WITHIN
``_traverse_env_and_launcher_prefix``'s own loop -- but that distinction was
then discarded the instant the function returned: every caller only ever
saw ``env_path is None`` for BOTH states, indistinguishable from "no ``env``
prefix ever mentioned PATH at all, inherit abicheck's own inherited process
PATH". Concretely: ``strip_launchers(["env", "-i", "cc", ...])`` correctly
returns the bare, unresolved token ``"cc"`` -- but nothing stopped a
downstream consumer from then resolving that bare ``"cc"`` against
abicheck's OWN inherited ``PATH``, which the real recorded command --
genuinely PATH-less under ``env -i`` -- could never have done that way (with
no inherited ``PATH``, a real PATH-less ``execvp`` still searches the
platform's PATH-absent default search path -- ``confstr(_CS_PATH)`` on
POSIX, modeled here by :data:`os.defpath` -- round 29 follow-up corrected
this module's resolution to search THAT instead of returning outright
unresolvable; it just can never search abicheck's own, unrelated inherited
``PATH``, which is the one thing this round's fix actually closes).
"""

from __future__ import annotations

from abicheck.buildsource.source_extractors._argv import (
    env_path_cleared_for_bare_token,
    pick_compiler_binary,
    strip_launchers,
)

# -- env_path_cleared_for_bare_token: the shared predicate ------------------


def test_env_dash_i_marks_bare_token_unresolvable() -> None:
    argv = ["env", "-i", "cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is True


def test_env_dash_u_path_marks_bare_token_unresolvable() -> None:
    argv = ["env", "-u", "PATH", "cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is True


def test_env_dash_i_then_real_path_override_un_clears() -> None:
    """A LATER, real ``PATH=`` override after ``-i`` genuinely gives
    ``execvp`` a directory list to search again -- the bare token is no
    longer provably unresolvable."""
    argv = ["env", "-i", "PATH=/opt/tool", "cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is False


def test_no_env_prefix_at_all_leaves_bare_token_normal() -> None:
    """Negative control: an ordinary command with no ``env`` wrapper at all
    is unaffected -- inheriting abicheck's own PATH is exactly correct
    here, since there was never any recorded PATH restriction."""
    argv = ["cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is False


def test_env_with_unrelated_assignment_only_leaves_bare_token_normal() -> None:
    """Negative control: an ``env`` prefix that never mentions PATH at all
    (only an unrelated ``NAME=VALUE``) is the ordinary "no opinion, inherit"
    case -- unaffected by this fix."""
    argv = ["env", "FOO=1", "cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is False


def test_path_shaped_token_is_never_flagged_even_under_env_dash_i() -> None:
    """A token containing a path separator is resolved directly, never via
    a PATH search -- so it is unaffected by ``env -i`` regardless."""
    argv = ["env", "-i", "/opt/tool/cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "/opt/tool/cc") is False


def test_nested_env_dash_i_state_persists_through_a_further_no_opinion_layer() -> None:
    """``env -i`` on the OUTER layer clears the environment the INNER
    ``env`` itself runs under -- a real nested ``env -i env FOO=1 cc ...``
    genuinely starts ``cc`` with no PATH at all, since the inner ``env``
    inherited an empty environment from the outer one and never set its
    own PATH."""
    argv = ["env", "-i", "env", "FOO=1", "cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is True


# -- pick_compiler_binary: must not trust a provably-unresolvable bare ------
# -- driver name --------------------------------------------------------


def test_pick_compiler_binary_falls_back_to_default_under_env_dash_i() -> None:
    """``env -i <unresolvable> ...`` recorded for a C++ compile unit: the
    bare token could never have been found by the real, PATH-less
    invocation -- neither via abicheck's own inherited ``PATH`` NOR via the
    platform's PATH-absent default search path (:data:`os.defpath`, round
    29 follow-up) -- so ``pick_compiler_binary`` must not trust it as
    toolchain identity, degrading to the same language-based ``g++``
    fallback used for a missing/flag-only recorded command.

    Uses a token guaranteed absent from :data:`os.defpath` (round 29
    follow-up, Codex review, fresh evidence) rather than the previous
    ``"cc"`` -- ``resolve_bare_token_with_default_path`` now genuinely
    resolves ``"cc"`` against a real ``/bin/cc``/``/usr/bin/cc`` on a
    typical POSIX sandbox, which is the CORRECT new behavior (see
    ``tests/test_source_extractors_env_round29.py`` for that positive
    case) but means ``"cc"`` is no longer a valid example of an
    UNRESOLVABLE bare token."""
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "this-compiler-does-not-exist-xyz", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "g++"


def test_pick_compiler_binary_falls_back_to_gcc_for_c_language_under_env_dash_i() -> (
    None
):
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.c",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "this-compiler-does-not-exist-xyz", "-c", "x.c"],
    )
    assert pick_compiler_binary(cu, override=None) == "gcc"


def test_pick_compiler_binary_still_trusts_bare_token_without_env_prefix() -> None:
    """Negative control: the ordinary, non-``env``-wrapped case is
    completely unaffected -- a bare recorded compiler name is still
    returned (mirroring ``pick_compiler_binary``'s pre-existing,
    documented behavior of preferring the real build's own recorded
    driver)."""
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["icpx", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "icpx"


def test_pick_compiler_binary_still_trusts_bare_token_when_path_override_present() -> (
    None
):
    """Negative control: ``env -i PATH=/opt/tool cc ...`` -- a real,
    explicit ``PATH=`` override AFTER ``-i`` un-clears PATH, so a bare
    driver name genuinely IS resolvable again (against that specific
    override, per ``_apply_env_context``) and must still be trusted as
    toolchain identity here."""
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "PATH=/opt/tool", "icpx", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "icpx"


def test_pick_compiler_binary_explicit_override_always_wins_even_under_env_dash_i() -> (
    None
):
    """Negative control: an explicit caller-supplied ``override`` always
    wins regardless of any ``env -i`` state -- unaffected by this fix."""
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "cc", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override="clang++") == "clang++"


def test_strip_launchers_itself_still_returns_bare_unresolved_token() -> None:
    """Companion sanity check: ``strip_launchers`` itself is UNCHANGED by
    this fix -- it still correctly leaves the bare token unresolved rather
    than guessing (the fix is entirely in what a caller does with that
    result, not in ``strip_launchers``'s own return value)."""
    assert strip_launchers(["env", "-i", "cc", "-c", "x.c"]) == ["cc", "-c", "x.c"]


# -- header_compile_context._derived_gcc_path: the MSVC/clang-cl sibling ----


def test_derived_gcc_path_returns_none_for_bare_clang_cl_under_env_dash_i() -> None:
    """The MSVC/clang-cl-dialect counterpart: ``env -i clang-cl /std:c++20
    ...`` -- ``clang-cl`` is found bare (no path separator) by
    ``msvc_driver_token``'s own CL-basename scan, but it is exactly as
    unresolvable via a real PATH search as the generic ``cc`` case above,
    so ``_derived_gcc_path`` must return ``None`` rather than trusting it
    (degrading to "no compiler binary derivable", the same outcome as when
    no CL-style token is found at all)."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "clang-cl", "/std:c++20", "/c", "x.cc"],
    )
    assert _derived_gcc_path(cu) is None


def test_derived_gcc_path_still_trusts_clang_cl_without_env_prefix() -> None:
    """Negative control: the ordinary, non-``env``-wrapped MSVC/clang-cl
    case is unaffected."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["clang-cl", "/std:c++20", "/c", "x.cc"],
    )
    assert _derived_gcc_path(cu) == "clang-cl"


def test_derived_gcc_path_still_trusts_clang_cl_when_path_override_present() -> None:
    """Negative control: an explicit ``PATH=`` override after ``env -i``
    un-clears PATH, so the bare ``clang-cl`` token is genuinely resolvable
    again and must still be trusted."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "PATH=/opt/tool", "clang-cl", "/std:c++20", "/c", "x.cc"],
    )
    assert _derived_gcc_path(cu) == "clang-cl"


def test_derived_gcc_path_absolute_clang_cl_path_still_trusted_under_env_dash_i() -> (
    None
):
    """Negative control: an ALREADY path-shaped (absolute) driver token is
    resolved directly, never via a PATH search -- unaffected by ``env
    -i``."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "/opt/llvm/bin/clang-cl", "/std:c++20", "/c", "x.cc"],
    )
    assert _derived_gcc_path(cu) == "/opt/llvm/bin/clang-cl"
