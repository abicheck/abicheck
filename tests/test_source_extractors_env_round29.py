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

"""Sibling split for round 29's Codex review findings on
:mod:`abicheck.buildsource.source_extractors._argv` (this test-tree has no
``__init__.py``, so sibling test modules do not import from one another --
every split follows the established self-contained convention):

1. Finding A -- ``_fold_chdir_into_operands``'s known-path-flag vocabulary
   was missing ``-cxx-isystem``/``/imsvc``/``/external:I``, three include
   option families ``header_utils._INCLUDE_FLAG_PREFIXES`` already
   classifies as include-search flags elsewhere.
2. Finding B -- ``pick_compiler_binary``/``header_compile_context.
   _derived_gcc_path`` unconditionally degraded a PATH-cleared bare driver
   token to the language-based ``gcc``/``g++`` fallback, when a genuinely
   PATH-less ``execvp`` still searches the platform's own default path
   (``os.defpath``).
3. Finding C -- ``_fold_chdir_into_operands`` corrupted a response-file
   token (``@args.rsp``) by rebasing the WHOLE token including its leading
   ``@`` sigil, producing ``build/@args.rsp`` instead of ``@build/args.rsp``.
4. Finding F -- ``_skip_env_prefix`` did not recognize GNU short-option
   ATTACHMENT (``-Cbuild``, DIR attached directly to ``-C`` with no space)
   or CLUSTERING (``-iv``, multiple no-operand short flags grouped).
"""

from __future__ import annotations

from abicheck.buildsource.source_extractors._argv import (
    env_path_cleared_for_bare_token,
    pick_compiler_binary,
    resolve_bare_token_with_default_path,
    strip_launchers,
)

# -- Finding A: rebase every supported include-option family ---------------


def test_chdir_folds_into_attached_imsvc() -> None:
    """``env -C build clang-cl /imsvcinclude ...`` -- the attached MSVC/
    clang-cl ``/imsvc`` system-include flag must be rebased onto the
    effective ``-C`` directory the same way ``-I``/``-isystem`` already
    are."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "/imsvcinclude", "/c", "x.cc"]
    )
    assert result == ["clang-cl", "/imsvcbuild/include", "/c", "build/x.cc"]


def test_chdir_folds_into_attached_external_i() -> None:
    """The attached clang-cl ``/external:I`` include flag is rebased too."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "/external:Iinclude", "/c", "x.cc"]
    )
    assert result == ["clang-cl", "/external:Ibuild/include", "/c", "build/x.cc"]


def test_chdir_folds_into_attached_cxx_isystem() -> None:
    """The attached GNU/Clang ``-cxx-isystem`` C++-only system-include flag
    is rebased too."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-cxx-isysteminclude", "-c", "x.cc"]
    )
    assert result == ["clang", "-cxx-isystembuild/include", "-c", "build/x.cc"]


def test_chdir_folds_into_separate_cxx_isystem() -> None:
    """The separate-token form (``-cxx-isystem include``, two tokens) is
    also recognized, mirroring the already-established ``-isystem``
    treatment."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-cxx-isystem", "include", "-c", "x.cc"]
    )
    assert result == ["clang", "-cxx-isystem", "build/include", "-c", "build/x.cc"]


def test_chdir_folds_into_separate_imsvc_and_external_i() -> None:
    """The separate-token forms of ``/imsvc``/``/external:I`` are also
    recognized."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "/imsvc", "include", "/c", "x.cc"]
    )
    assert result == ["clang-cl", "/imsvc", "build/include", "/c", "build/x.cc"]
    result2 = strip_launchers(
        ["env", "-C", "build", "clang-cl", "/external:I", "include", "/c", "x.cc"]
    )
    assert result2 == [
        "clang-cl",
        "/external:I",
        "build/include",
        "/c",
        "build/x.cc",
    ]


def test_chdir_does_not_fold_unrelated_flag_starting_with_slash_i() -> None:
    """Negative control: an unrelated MSVC flag that merely starts with
    ``/I`` but is not actually an include flag (none exist in this
    module's own recognized vocabulary) is not accidentally matched --
    confirmed here via a flag genuinely outside every recognized family,
    which must pass through completely unresolved."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "/Zi", "/c", "x.cc"])
    assert result == ["clang-cl", "/Zi", "/c", "build/x.cc"]


# -- Finding B: resolve PATH-less drivers using the platform default -------


def test_resolve_bare_token_with_default_path_finds_real_binary() -> None:
    """A binary genuinely present on :data:`os.defpath` (``/bin``/
    ``/usr/bin`` on POSIX) resolves to its real absolute path."""
    resolved = resolve_bare_token_with_default_path("cat")
    assert resolved is not None
    assert resolved.endswith(("/cat", "\\cat.exe", "\\cat"))


def test_resolve_bare_token_with_default_path_returns_none_when_absent() -> None:
    """Negative control: a name absent from :data:`os.defpath` resolves to
    ``None``, the same conservative default this module uses throughout."""
    assert (
        resolve_bare_token_with_default_path("this-binary-does-not-exist-xyz") is None
    )


def test_pick_compiler_binary_resolves_default_path_driver_under_env_dash_i() -> None:
    """``env -i cat ...``: a genuinely PATH-less ``execvp`` still searches
    the platform's own default path -- ``cat`` (present on ``/bin``/
    ``/usr/bin`` on any POSIX sandbox) resolves to its real absolute path,
    NOT the language-based ``g++``/``gcc`` fallback."""
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "cat", "-c", "x.cc"],
    )
    result = pick_compiler_binary(cu, override=None)
    assert result != "g++"
    assert result.endswith(("/cat", "\\cat.exe", "\\cat"))


def test_pick_compiler_binary_still_falls_back_when_default_path_unresolvable() -> None:
    """Negative control: a bare token absent from BOTH abicheck's own
    inherited ``PATH`` and the platform's default path still degrades to
    the language-based fallback -- the round 27 behavior is preserved for
    the genuinely-unresolvable case, only the resolvable case changed."""
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


def test_derived_gcc_path_resolves_default_path_driver_under_env_dash_i() -> None:
    """The sibling ``header_compile_context._derived_gcc_path`` fix,
    exercised directly: same default-path resolution, not the ``None``
    "not derivable" fallback.

    Uses a bare ``/c`` MSVC-dialect marker (rather than a CL-basename
    driver, which no real ``/bin``/``/usr/bin`` binary on this sandbox is
    named) alongside the ordinary, non-CL-named ``cat`` -- ``_is_msvc_
    command`` detects MSVC dialect via EITHER signal, and
    ``msvc_driver_token`` finding no CL-basename token falls back to
    ``strip_launchers``'s own driver detection, reaching the identical
    PATH-cleared/default-path-resolution code path ``pick_compiler_binary``
    exercises."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "cat", "/c", "x.cc"],
    )
    result = _derived_gcc_path(cu)
    assert result is not None
    assert result.endswith(("/cat", "\\cat.exe", "\\cat"))


def test_derived_gcc_path_none_when_default_path_unresolvable() -> None:
    """Negative control for ``_derived_gcc_path``: an unresolvable-anywhere
    bare token under ``env -i`` still returns ``None`` (the pre-existing
    "not derivable" outcome), not a fabricated path."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-i", "this-compiler-does-not-exist-xyz", "/c", "x.cc"],
    )
    assert _derived_gcc_path(cu) is None


# -- Finding C: preserve response-file sigils while rebasing operands ------


def test_chdir_preserves_response_file_sigil() -> None:
    """``env -C build clang @args.rsp``: the leading ``@`` sigil must be
    preserved and only the PAYLOAD after it rebased -- ``@build/args.rsp``,
    not ``build/@args.rsp`` (a token no driver/consumer recognizes as a
    response file, since the sigil is no longer the first character)."""
    result = strip_launchers(["env", "-C", "build", "clang", "@args.rsp"])
    assert result == ["clang", "@build/args.rsp"]


def test_chdir_preserves_response_file_sigil_nested_path() -> None:
    """The same rebase applies when the response-file payload itself has a
    path separator."""
    result = strip_launchers(["env", "-C", "build", "clang", "@sub/args.rsp"])
    assert result == ["clang", "@build/sub/args.rsp"]


def test_chdir_response_file_negative_control_no_chdir() -> None:
    """Negative control: with no ``env -C`` prefix at all, a response-file
    token is left completely unchanged (nothing to rebase against)."""
    result = strip_launchers(["clang", "@args.rsp"])
    assert result == ["clang", "@args.rsp"]


# -- Finding F: GNU short-option attachment/clustering ----------------------


def test_skip_env_prefix_accepts_attached_short_chdir() -> None:
    """``env -Cbuild clang -c x.c``: GNU ``env``'s ``-C, --chdir=DIR``
    accepts DIR attached directly to the short flag with no space --
    confirmed via a real installed ``env`` (``env -Ctmp pwd`` chdirs into
    ``/tmp``). The real driver and effective cwd must both survive."""
    result = strip_launchers(["env", "-Cbuild", "clang", "-c", "x.c"])
    assert result == ["clang", "-c", "build/x.c"]


def test_skip_env_prefix_accepts_clustered_no_operand_short_flags() -> None:
    """``env -iv cc -c x.c``: GNU short-option CLUSTERING groups multiple
    no-operand short flags (``-i`` + ``-v``) into one token -- the real
    driver must still be found, and the clustered ``-i`` must still clear
    PATH exactly like its unclustered spelling."""
    argv = ["env", "-iv", "cc", "-c", "x.c"]
    result = strip_launchers(argv)
    assert result == ["cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is True


def test_skip_env_prefix_clustered_short_flags_order_independent() -> None:
    """The same cluster spelled in the other order (``-vi``) behaves
    identically -- confirming this is genuine per-character clustering,
    not a single hardcoded two-character spelling."""
    argv = ["env", "-vi", "cc", "-c", "x.c"]
    result = strip_launchers(argv)
    assert result == ["cc", "-c", "x.c"]
    assert env_path_cleared_for_bare_token(argv, "cc") is True


def test_skip_env_prefix_attached_chdir_negative_control_separate_form_unaffected() -> (
    None
):
    """Negative control: the pre-existing separate-token ``-C DIR`` form is
    completely unaffected by the new attached-form recognition."""
    result = strip_launchers(["env", "-C", "build", "clang", "-c", "x.c"])
    assert result == ["clang", "-c", "build/x.c"]


def test_skip_env_prefix_attached_chdir_negative_control_long_chdir_unaffected() -> (
    None
):
    """Negative control: the pre-existing ``--chdir=DIR`` long spelling is
    completely unaffected -- the new ``arg.startswith("-C")`` branch
    explicitly excludes any ``--``-prefixed token."""
    result = strip_launchers(["env", "--chdir=build", "clang", "-c", "x.c"])
    assert result == ["clang", "-c", "build/x.c"]


def test_skip_env_prefix_cluster_negative_control_unrecognized_char() -> None:
    """Negative control: a token that LOOKS like a cluster but contains a
    character outside the recognized clusterable set (e.g. ``-ix``, where
    ``x`` names no known no-operand short flag) does not match the
    clustering branch at all -- it falls through unresolved, the same
    conservative, no-worse-than-before default this module already uses
    for any flag/token shape it cannot positively classify. Also confirms
    the fix did not accidentally start misreading an ordinary bare
    positional operand that happens to start with ``-``."""
    argv = ["env", "-ix", "cc", "-c", "x.c"]
    result = strip_launchers(argv)
    # "-ix" is not a recognized env flag/cluster, so the scan terminates
    # there and "-ix" itself is read as the (bogus) driver token -- the
    # same conservative behavior any other unrecognized flag gets.
    assert result[0] == "-ix"


def test_skip_env_prefix_attached_chdir_does_not_misfire_on_unrelated_flag() -> None:
    """Negative control: an unrelated env flag/token that happens to start
    with the two characters ``-C`` is never possible in GNU env's real
    vocabulary (only ``-C``/``--chdir`` starts that way), so this is
    confirmed via the attached form itself composing correctly end to end
    with a deeper relative path, proving the branch resolves the DIR
    payload rather than merely detecting the flag."""
    result = strip_launchers(["env", "-Cbuild/sub", "clang", "-c", "x.c"])
    assert result == ["clang", "-c", "build/sub/x.c"]
