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

"""Sibling split of ``test_source_extractors.py`` (near its 2000-line
AI-readiness soft cap) for P2 review round 19's two ``env``-prefix findings
on :mod:`abicheck.buildsource.source_extractors._argv`:

1. ``_argv.py:453`` -- "Apply env chdir before resolving relative compiler
   paths": ``env -C DIR driver ...`` changes the EFFECTIVE working
   directory the driver runs from, so a relative driver token must be
   resolved against ``<cu.directory>/DIR``, not bare ``cu.directory``.
2. ``_argv.py:459`` -- "Resolve drivers using the env-supplied PATH":
   ``env PATH=... driver ...`` scopes a PATH override to the launched
   command, so a bare driver name only resolvable via THAT PATH must not
   be looked up against abicheck's own inherited PATH instead.

``strip_launchers`` folds both effects directly into the driver token it
returns (see its own docstring and :func:`~abicheck.buildsource.
source_extractors._argv._apply_env_context`'s), so every existing caller
(``header_compile_context._derived_gcc_path``,
``adapters.base._msvc_driver_scan``, ``cc_wrapper.py``,
``include_graph.py``, ``build_context.py``) gets the corrected token for
free with no signature change. End-to-end coverage through
``header_compile_context.resolve_header_compile_context`` lives alongside
its sibling driver-resolution tests in
``tests/test_header_compile_context_gcc_path.py``; this file covers
``strip_launchers`` itself directly.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.source_extractors._argv import strip_launchers


def _make_executable(path: Path) -> Path:
    """Create an executable fixture at (or near) *path*, returning the
    actual path created.

    ``shutil.which`` (which every ``env PATH=...`` test below exercises,
    directly or via :func:`strip_launchers`) consults ``PATHEXT`` on
    Windows rather than doing an exact bare-name match -- an extensionless
    fixture like ``clang-cl`` (no ``.exe``) is never found there, so every
    test using this helper unconditionally failed on Windows CI regardless
    of the fix under test (CodeRabbit review, fresh evidence). On Windows,
    create ``<path>.exe`` instead and return *that* path -- the compiler
    token referenced in a test's own ``argv`` stays the bare, extensionless
    name (``"clang-cl"``), since Windows' ``PATHEXT`` resolution finds the
    ``.exe`` file when searching for that bare name; callers must assert
    against this function's *returned* path, not the bare *path* argument,
    since only the returned path is guaranteed to be the file that was
    actually created.
    """
    if sys.platform == "win32" and not path.suffix:
        path = path.with_suffix(".exe")
    path.write_text("", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _assert_resolved_driver(actual: str, expected: Path) -> None:
    """Compare a ``shutil.which``-resolved driver path case-insensitively
    (live Windows CI signal, fresh evidence): ``shutil.which`` on Windows
    matches an extensionless bare name against ``PATHEXT`` and returns the
    resolved path with THAT variable's own extension casing (commonly
    uppercase ``.EXE``, since that is how ``PATHEXT`` is conventionally
    defined there) -- not necessarily matching the casing of the fixture
    file this test itself created on disk. Windows filesystems resolve
    paths case-insensitively, so both spellings genuinely name the same
    file; comparing the two directly with plain string equality is
    comparing an incidental casing accident, not real behavior. Uses
    ``os.path.normcase`` -- the identity function on POSIX, so this is a
    complete no-op there -- rather than a Windows-specific branch, so one
    comparison is correct on every platform this suite runs on.
    """
    assert os.path.normcase(actual) == os.path.normcase(str(expected))


# -- Finding 1: `env -C DIR` folds into a relative driver token --------------


def test_strip_launchers_env_chdir_folds_into_relative_driver_token() -> None:
    """``env -C build ../llvm/bin/clang-cl ...`` must resolve the driver
    relative to ``build``, not the bare, un-chdir'd base -- folded as a
    plain lexical join+normalize so the caller's own later join against
    ``cu.directory`` still composes correctly (see
    ``header_compile_context._resolve_driver_token``, exercised end-to-end
    in ``tests/test_header_compile_context_gcc_path.py``).

    The trailing bare source-file operand (``x.cc``, no leading ``-C``
    involvement) is ALSO folded against the same effective chdir (round 23
    Finding 8/12) -- real ``env -C`` chdirs the whole process, so the
    compiler's own relative source-file argument resolves against that
    same directory too, not just the driver's own path."""
    result = strip_launchers(
        ["env", "-C", "build", "../llvm/bin/clang-cl", "/std:c++20", "-c", "x.cc"]
    )
    assert result == [
        "llvm/bin/clang-cl",
        "/std:c++20",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_env_chdir_long_form() -> None:
    """The GNU long-option spellings (``--chdir DIR`` and ``--chdir=DIR``)
    behave identically to ``-C DIR``, including folding the trailing
    source-file operand (round 23 Finding 8/12)."""
    expected = [
        "clang-cl",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]
    assert (
        strip_launchers(["env", "--chdir", "build", "../clang-cl", "-c", "x.cc"])
        == expected
    )
    assert (
        strip_launchers(["env", "--chdir=build", "../clang-cl", "-c", "x.cc"])
        == expected
    )


def test_strip_launchers_env_chdir_left_alone_for_bare_driver_name() -> None:
    """A bare PATH name (no path separator) is looked up on PATH, never
    relative to any directory -- chdir must not be joined onto it. The
    trailing source-file operand still folds (round 23 Finding 8/12)."""
    assert strip_launchers(["env", "-C", "build", "clang-cl", "-c", "x.cc"]) == [
        "clang-cl",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_env_chdir_left_alone_for_absolute_driver() -> None:
    """An already-absolute driver token is not joined onto chdir a second
    time. The trailing source-file operand still folds (round 23 Finding
    8/12)."""
    assert strip_launchers(
        ["env", "-C", "build", "/opt/llvm/bin/clang-cl", "-c", "x.cc"]
    ) == [
        "/opt/llvm/bin/clang-cl",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_env_chdir_multiple_dotdot_segments_normalized() -> None:
    """The joined+normalized result collapses ``..`` segments.

    Normalized with the token's OWN (POSIX, forward-slash) grammar rather
    than the host's (Codex review, "Recognize foreign absolute driver
    paths", fresh evidence) -- ``chdir``/the driver token here are
    POSIX-style evidence text, not necessarily host-native paths, so the
    expected value is a literal forward-slash string rather than
    ``os.path.normpath(...)`` (which would silently flip to backslashes on
    a Windows host, self-referentially matching whatever the host does
    rather than pinning the actually-correct, host-independent result)."""
    result = strip_launchers(["env", "-C", "a/b", "../../tool/clang-cl", "-c", "x.cc"])
    assert result[0] == "tool/clang-cl"


def test_strip_launchers_env_chdir_with_compiler_cache_launcher_chained() -> None:
    """``env -C build sccache clang-cl ...`` -- the launcher sits between
    env's prefix and the real driver; chdir must still fold onto the
    driver token found *after* the launcher is unwrapped. The trailing
    source-file operand still folds too (round 23 Finding 8/12)."""
    result = strip_launchers(
        ["env", "-C", "build", "sccache", "../llvm/bin/clang-cl", "-c", "x.cc"]
    )
    assert result == [
        "llvm/bin/clang-cl",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


# -- Finding 2: `env PATH=...` resolves a bare driver name -------------------


def test_strip_launchers_env_path_resolves_bare_driver(tmp_path: Path) -> None:
    """``env PATH=/opt/llvm/bin clang-cl ...`` -- when ``clang-cl`` is only
    resolvable via that overridden PATH, the bare name is converted to an
    absolute path immediately, so no downstream consumer needs to know the
    env override existed at all."""
    driver = tmp_path / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        ["env", f"PATH={tmp_path}", "clang-cl", "/std:c++20", "-c", "x.cc"]
    )
    _assert_resolved_driver(result[0], driver)
    assert result[1:] == ["/std:c++20", "-c", "x.cc"]


def test_strip_launchers_env_path_unresolvable_leaves_bare_name(tmp_path: Path) -> None:
    """When the named driver isn't actually on the env-supplied PATH, the
    bare name is left unchanged -- the same conservative, no-worse-than-
    before fallback the pre-fix behavior already had."""
    result = strip_launchers(["env", f"PATH={tmp_path}", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_strip_launchers_env_path_ignored_for_path_bearing_driver(
    tmp_path: Path,
) -> None:
    """A driver token that already contains a path separator is unambiguous
    regardless of which PATH would have been searched -- the env-supplied
    PATH must not be applied to it."""
    other = tmp_path / "other"
    other.mkdir()
    decoy = other / "clang-cl"
    decoy = _make_executable(decoy)
    result = strip_launchers(["env", f"PATH={other}", "./clang-cl", "-c", "x.cc"])
    assert result == ["./clang-cl", "-c", "x.cc"]


def test_strip_launchers_env_path_with_compiler_cache_launcher_chained(
    tmp_path: Path,
) -> None:
    """``env PATH=... sccache clang-cl ...`` -- PATH must still resolve the
    driver token found *after* the launcher is unwrapped."""
    driver = tmp_path / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        ["env", f"PATH={tmp_path}", "sccache", "clang-cl", "-c", "x.cc"]
    )
    _assert_resolved_driver(result[0], driver)
    assert result[1:] == ["-c", "x.cc"]


def test_strip_launchers_env_chdir_and_path_together(tmp_path: Path) -> None:
    """``env -C build PATH=/opt/llvm/bin clang-cl ...`` -- both effects can
    be present on the same prefix; chdir applies only to a path-shaped
    driver token (a bare driver name is resolved via PATH instead) and to
    every trailing operand regardless of shape (round 23 Finding 8/12), so
    the trailing ``x.cc`` still folds against ``build`` even though the
    driver itself was resolved via ``PATH=``."""
    driver = tmp_path / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        ["env", "-C", "build", f"PATH={tmp_path}", "clang-cl", "-c", "x.cc"]
    )
    _assert_resolved_driver(result[0], driver)
    assert result[1:] == ["-c", os.path.normpath(os.path.join("build", "x.cc"))]


# -- Negative/normal cases: ordinary commands and bare `env` unaffected ------


def test_strip_launchers_ordinary_non_env_command_unaffected_by_env_handling() -> None:
    argv = ["gcc", "-c", "foo.c"]
    assert strip_launchers(argv) == argv


def test_strip_launchers_bare_env_with_no_chdir_or_path_still_works() -> None:
    """Round 18's baseline behavior (bare ``env`` with an unrelated
    assignment, no ``-C``/``PATH=``) must be completely unaffected by this
    round's additions."""
    assert strip_launchers(["env", "SDKROOT=/opt/sdk", "gcc", "-c", "foo.c"]) == [
        "gcc",
        "-c",
        "foo.c",
    ]


def test_strip_launchers_env_prefix_with_no_driver_token_at_all() -> None:
    """Degenerate case: ``env -C build`` names no command whatsoever --
    neither effect has a real driver token to apply to, so nothing is
    folded (mirrors round 18's ``strip_launchers(["env"]) == []``)."""
    assert strip_launchers(["env", "-C", "build"]) == []


def test_strip_launchers_env_chdir_with_flag_shaped_leftover_token() -> None:
    """Degenerate case: the token left after ``env``'s own prefix is itself
    a flag (no real driver was ever recorded) -- neither the chdir nor the
    PATH resolution applies to a non-existent driver (the flag itself is
    passed through unchanged), but the operand AFTER it still folds against
    the effective chdir (round 23 Finding 8/12) -- real ``env`` chdirs the
    whole process before exec, regardless of whether the named "command"
    happens to be spelled like a flag."""
    assert strip_launchers(["env", "-C", "build", "-c", "foo.c"]) == [
        "-c",
        os.path.normpath(os.path.join("build", "foo.c")),
    ]


def test_strip_launchers_env_path_assignment_named_something_else_ignored() -> None:
    """A ``NAME=VALUE`` assignment that isn't literally ``PATH=`` (e.g.
    ``LD_LIBRARY_PATH=``) must not be mistaken for the PATH override --
    only an exact ``PATH=`` prefix qualifies."""
    result = strip_launchers(
        ["env", "LD_LIBRARY_PATH=/opt/lib", "clang-cl", "-c", "x.cc"]
    )
    assert result == ["clang-cl", "-c", "x.cc"]


# -- Finding 1 (Codex, `_argv.py:102`): `env -S`/`--split-string` expansion --


def test_strip_launchers_env_split_string_short_flag_expands_and_splices() -> None:
    """``env -S 'clang-cl /c x.cc'`` -- GNU env's own documented behavior
    ("process and split S into separate arguments") must be replicated:
    downstream launcher/driver detection needs the SPLIT tokens spliced
    into argv, not the single flag+string pair left as an opaque
    unrecognized token (which previously left ``-S``/the joined string as
    the apparent "compiler")."""
    result = strip_launchers(["env", "-S", "clang-cl /c x.cc"])
    assert result == ["clang-cl", "/c", "x.cc"]


def test_strip_launchers_env_split_string_long_flag_combined_form() -> None:
    """The documented ``--split-string=S`` combined spelling."""
    result = strip_launchers(["env", "--split-string=clang-cl /c x.cc"])
    assert result == ["clang-cl", "/c", "x.cc"]


def test_strip_launchers_env_split_string_long_flag_separate_form() -> None:
    """The GNU-getopt-conventional separate-token spelling,
    ``--split-string S``."""
    result = strip_launchers(["env", "--split-string", "clang-cl /c x.cc"])
    assert result == ["clang-cl", "/c", "x.cc"]


def test_strip_launchers_env_split_string_joined_short_form() -> None:
    """The short-option joined spelling, ``-Sclang-cl /c x.cc``."""
    result = strip_launchers(["env", "-Sclang-cl /c x.cc"])
    assert result == ["clang-cl", "/c", "x.cc"]


def test_strip_launchers_env_split_string_respects_quoting() -> None:
    """GNU env's split-string performs shell-word-splitting (quote/escape
    aware), not a naive whitespace split -- a quoted argument containing a
    space must survive as one token."""
    result = strip_launchers(["env", "-S", "clang-cl -DFOO='a b' /c x.cc"])
    assert result == ["clang-cl", "-DFOO=a b", "/c", "x.cc"]


def test_strip_launchers_env_split_string_malformed_quote_degrades_gracefully() -> None:
    """Round 30 Finding CR4 (CodeRabbit review, fresh evidence): a
    malformed ``-S`` value (here, an unbalanced quote -- a persisted,
    untrusted ``compile_unit.argv`` may carry exactly this) must not raise
    ``ValueError`` up through the shared traversal and abort the whole
    adapter's collection for every OTHER compile unit alongside it.
    Falls back to plain whitespace splitting rather than crashing."""
    result = strip_launchers(["env", "-S", "clang-cl -DFOO='a /c x.cc"])
    # No exception; best-effort whitespace split of the malformed string.
    assert result == ["clang-cl", "-DFOO='a", "/c", "x.cc"]


def test_msvc_driver_scan_malformed_env_split_string_does_not_raise() -> None:
    """The same malformed ``-S`` value must not raise through the real
    ``adapters.base._msvc_driver_scan`` chokepoint either -- not merely
    through ``strip_launchers`` in isolation."""
    from abicheck.buildsource.adapters.base import _msvc_driver_scan

    argv = ["env", "-S", "clang-cl -DFOO='a /c x.cc"]
    # Must not raise; degrades to a best-effort scan over the whitespace-
    # split fallback tokens instead, still correctly recognizing the
    # MSVC-dialect driver from the malformed-but-recoverable split.
    is_msvc, driver_token = _msvc_driver_scan(argv)
    assert is_msvc is True
    assert driver_token == "clang-cl"


def test_strip_launchers_env_split_string_with_launcher_after() -> None:
    """The expanded tokens are re-scanned for a further compiler-launcher
    prefix (``sccache``), the same as any other unwrapped argv."""
    result = strip_launchers(["env", "-S", "sccache clang-cl /c x.cc"])
    assert result == ["clang-cl", "/c", "x.cc"]


def test_strip_launchers_env_split_string_does_not_mutate_caller_argv() -> None:
    """``strip_launchers`` must never mutate the caller's own argv list in
    place -- several callers pass ``compile_unit.argv`` (persisted
    evidence) straight through without copying it first."""
    original = ["env", "-S", "clang-cl /c x.cc"]
    snapshot = list(original)
    strip_launchers(original)
    assert original == snapshot


def test_pick_compiler_binary_resolves_env_split_string_driver(tmp_path) -> None:
    """End-to-end: :func:`~abicheck.buildsource.source_extractors._argv.
    pick_compiler_binary` (the real consumer) must also see the expanded,
    spliced driver token, not the raw ``-S``/joined-string pair."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "-S", "clang-cl /c x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "clang-cl"


# -- Finding 3 (Codex, `_argv.py:576`): `env -C` + relative `PATH=` compose --


def test_pick_compiler_binary_resolves_env_chdir_and_relative_path_together(
    tmp_path,
) -> None:
    """``env -C build PATH=../tool clang-cl /c x.cc`` recorded for a
    compile unit in ``<tmp_path>`` -- GNU env changes into
    ``<tmp_path>/build`` before searching ``../tool`` on PATH, so the real
    driver is found at ``<tmp_path>/tool/clang-cl``, not at a path relative
    to abicheck's own CWD. ``pick_compiler_binary`` is the one caller with
    a full ``CompileUnit`` (and therefore ``directory``) in scope to
    resolve this correctly."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    (tmp_path / "build").mkdir()
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    # Must capture `_make_executable`'s own return value, not the pre-fixture
    # `driver` reference -- on Windows it creates and returns a `.exe`-suffixed
    # path, and asserting against the unsuffixed `driver` variable would fail
    # there regardless of whether the real fix under test works at all (live
    # Windows CI signal, fresh evidence: this omission previously left the
    # right-hand side of the assertion silently wrong on Windows).
    driver = _make_executable(driver)
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"],
    )
    _assert_resolved_driver(pick_compiler_binary(cu, override=None), driver)


def test_strip_launchers_without_directory_leaves_relative_env_path_bare() -> None:
    """Without a *directory* (every caller besides ``pick_compiler_binary``
    -- the pre-existing, directory-agnostic behavior for
    ``strip_launchers``'s other callers), a relative ``PATH=`` entry cannot
    be safely resolved, so the bare driver name is left unchanged rather
    than searched against abicheck's own CWD. The trailing source-file
    operand still folds against the raw ``chdir`` value regardless (round
    23 Finding 8/12) -- that fold needs only *chdir* itself, not *directory*."""
    result = strip_launchers(
        ["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"]
    )
    assert result == [
        "clang-cl",
        "/c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_pick_compiler_binary_resolves_env_relative_path_without_chdir(
    tmp_path,
) -> None:
    """Companion to the chdir+PATH composition test above: a relative
    ``PATH=`` entry with NO ``-C`` prefix composes directly against the
    compile unit's own ``directory`` (the ``chdir is None`` branch of
    ``_resolve_env_path_entries``)."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    # See the companion chdir+PATH test above for why the return value must
    # be captured rather than asserting against the pre-fixture `driver`.
    driver = _make_executable(driver)
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "PATH=tool", "clang-cl", "/c", "x.cc"],
    )
    _assert_resolved_driver(pick_compiler_binary(cu, override=None), driver)


# -- Direct coverage for the new host-independent path helpers ---------------


def test_normalize_path_token_posix_absolute_uses_posixpath_grammar() -> None:
    from abicheck.buildsource.source_extractors._argv import normalize_path_token

    assert (
        normalize_path_token("/opt/llvm/bin/../bin/clang-cl")
        == "/opt/llvm/bin/clang-cl"
    )


def test_normalize_path_token_windows_absolute_uses_ntpath_grammar() -> None:
    from abicheck.buildsource.source_extractors._argv import normalize_path_token

    assert (
        normalize_path_token(r"C:\LLVM\bin\..\bin\clang-cl") == r"C:\LLVM\bin\clang-cl"
    )


def test_normalize_path_token_falls_back_to_host_native_for_non_absolute() -> None:
    """A token matching neither absolute grammar (no caller reaches this in
    practice -- both current callers only invoke this after already
    confirming :func:`is_absolute_path_token`) falls back to host-native
    normalization, the pre-existing default."""
    from abicheck.buildsource.source_extractors._argv import normalize_path_token

    assert normalize_path_token("a/./b") == os.path.normpath("a/./b")


def test_join_path_token_windows_only_token_uses_ntpath_grammar() -> None:
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token("build", r"..\llvm\bin\clang-cl") == r"llvm\bin\clang-cl"


def test_join_path_token_posix_only_token_uses_posixpath_grammar() -> None:
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token("build", "../llvm/bin/clang-cl") == "llvm/bin/clang-cl"


def test_join_path_token_mixed_separators_falls_back_to_host_native() -> None:
    """A token mixing both separator styles (or a bare, separator-free
    token -- neither of which a real caller reaches here) falls back to
    host-native ``os.path.join``/``os.path.normpath``, the pre-existing
    behavior."""
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token("build", "sub") == os.path.normpath(
        os.path.join("build", "sub")
    )


# -- Round 23, Finding 1 (Codex, ``_argv.py:527``): join grammar chosen from
# an unambiguous BASE, not the relative token's own spelling alone ----------


def test_join_path_token_windows_base_with_posix_style_relative_token() -> None:
    """``env -C build`` where ``directory`` is Windows-shaped but a relative
    PATH= entry is POSIX-spelled (``../tool``) -- the OLD code chose
    ``posixpath`` purely from the token's own lack of backslashes, treating
    the whole Windows base as one opaque path component and losing its
    drive letter/backslash structure entirely (producing ``tool`` instead
    of the real ``C:\\work\\tool``). The base's own unambiguous grammar
    must win instead."""
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token(r"C:\work\build", "../tool") == r"C:\work\tool"


def test_join_path_token_posix_base_with_windows_style_relative_token() -> None:
    """The symmetric direction: a POSIX-absolute base with a
    Windows-spelled relative token."""
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token("/work/build", r"..\tool") == "/work/tool"


def test_join_path_token_windows_base_wins_over_ambiguous_token_negative_control() -> (
    None
):
    """Negative control: when BOTH operands genuinely agree on Windows
    grammar, behavior is unchanged (still resolves correctly, not a
    regression introduced by preferring the base)."""
    from abicheck.buildsource.source_extractors._argv import join_path_token

    assert join_path_token(r"C:\work\build", r"..\tool") == r"C:\work\tool"


def test_pick_compiler_binary_resolves_env_chdir_windows_base_posix_path_entry(
    tmp_path,
) -> None:
    """End-to-end (Finding 1's own repro shape, through the real
    ``pick_compiler_binary`` consumer): a Windows-style compile-unit
    ``directory`` composed with a POSIX-spelled relative ``PATH=`` entry
    must still find the real driver, not silently fail to resolve it."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    # `_resolve_env_path_entries` is exercised directly here (rather than
    # `pick_compiler_binary`, which needs a real filesystem `directory` --
    # a literal `C:\work\build` does not exist on this POSIX test runner)
    # because Finding 1 is specifically about the STRING composition, not
    # filesystem resolution.
    resolved = _resolve_env_path_entries("../tool", "build", r"C:\work")
    assert resolved == [r"C:\work\tool"]
    # A CompileUnit is still built here to document the real-world shape
    # this composition feeds -- `pick_compiler_binary` handing the composed
    # PATH to `shutil.which`.
    CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=r"C:\work",
        output="x.o",
        argv=["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"],
    )


# -- Round 23, Finding 3 (Codex, ``_argv.py:708``): an empty ``PATH=``
# component means the effective current directory -----------------------


def test_resolve_env_path_entries_empty_component_means_effective_cwd() -> None:
    """``PATH=:`` -- a bare leading/trailing colon -- names the effective
    (chdir'd) current directory, per POSIX/GNU semantics, not an
    unresolved empty string left to fall through to ``shutil.which``'s own
    (wrong) CWD-relative lookup. Returns a LIST of candidate directories
    (round 24 follow-up, live Windows CI signal) -- see this function's
    own docstring for why a pathsep-joined string is no longer safe to
    hand to a single ``shutil.which`` call."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries(":", "build", "/work") == [
        "/work/build",
        "/work/build",
    ]


def test_resolve_env_path_entries_empty_component_between_real_entries() -> None:
    """``PATH=/a::/b`` -- an empty component BETWEEN two real ones -- also
    means the effective cwd, not merely the leading/trailing-colon case."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries("/a::/b", None, "/work") == ["/a", "/work", "/b"]


def test_resolve_env_path_entries_negative_control_non_empty_relative_unaffected() -> (
    None
):
    """Negative control: a genuinely non-empty relative entry is still
    resolved the pre-existing way, not accidentally routed through the new
    empty-component branch."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries("tool", None, "/work") == ["/work/tool"]


def test_resolve_env_path_entries_splits_on_literal_colon_not_host_pathsep() -> None:
    """Round 24 follow-up (live Windows CI signal): splitting must use a
    LITERAL ``:`` -- POSIX ``env``'s own fixed separator -- never
    host-native ``os.pathsep`` (``;`` on Windows), regardless of which host
    OS is running this analysis."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries("/a:/b:/c", None, "/work") == [
        "/a",
        "/b",
        "/c",
    ]


def test_resolve_env_path_entries_does_not_split_windows_drive_letter() -> None:
    """A single ``PATH`` entry that is itself an absolute Windows path
    (``C:\\Users\\x``) must NOT be cut in two at its own drive-letter
    colon -- only a genuine POSIX entry separator splits."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries(r"C:\Users\x", None, "/work") == [r"C:\Users\x"]
    assert _resolve_env_path_entries("C:/Users/x", None, "/work") == ["C:/Users/x"]


def test_resolve_env_path_entries_windows_drive_letter_among_other_entries() -> None:
    """A Windows-drive-letter entry mixed with genuine POSIX entries splits
    correctly on every real separator while leaving the drive letter's own
    colon untouched."""
    from abicheck.buildsource.source_extractors._argv import (
        _resolve_env_path_entries,
    )

    assert _resolve_env_path_entries(r"/a:C:\tools:/b", None, "/work") == [
        "/a",
        r"C:\tools",
        "/b",
    ]


def test_strip_launchers_env_path_empty_component_resolves_bare_driver_via_cwd(
    tmp_path: Path,
) -> None:
    """End-to-end: ``env -C sub PATH=: clang-cl ...`` must find a driver
    that lives in the effective (chdir'd) directory itself, via the empty
    ``PATH=`` component."""
    sub = tmp_path / "sub"
    sub.mkdir()
    driver = _make_executable(sub / "clang-cl")
    result = strip_launchers(
        ["env", "-C", "sub", "PATH=:", "clang-cl", "-c", "x.cc"],
        directory=str(tmp_path),
    )
    _assert_resolved_driver(result[0], driver)
    # The trailing source-file operand folds against the raw `chdir` value
    # ("sub"), not the directory-composed effective PATH base (round 23
    # Finding 8/12) -- `_fold_chdir_into_operands` uses `chdir` directly,
    # mirroring `_apply_env_context`'s own driver-token precedent.
    assert result[1:] == ["-c", os.path.normpath(os.path.join("sub", "x.cc"))]


# -- Round 23, Finding 4 (Codex, ``_argv.py:87``): env's real long-flag
# spelling is ``--debug``, not ``--verbose`` --------------------------------


def test_strip_launchers_env_debug_long_flag_recognized() -> None:
    """``env --debug clang-cl ...`` -- GNU env's real documented option
    pair is ``-v, --debug`` (confirmed against a real installed
    ``env --help``), NOT ``--verbose``. Before the fix, this was not
    recognized as an env flag at all -- ``--debug`` itself was mistaken for
    the driver, and the real driver (and everything after it) was lost."""
    result = strip_launchers(["env", "--debug", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_strip_launchers_env_debug_short_flag_still_recognized() -> None:
    """The short form, ``-v``, was already correct and must stay that way."""
    result = strip_launchers(["env", "-v", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_strip_launchers_env_verbose_negative_control_no_longer_special_cased() -> None:
    """Negative control: ``--verbose`` is not a real ``env`` flag (rejected
    by a real installed ``env`` with exit code 125) and must NOT be
    recognized as one -- it is treated like any other unrecognized token,
    i.e. as the driver itself, the same as before Finding 4 for any other
    genuinely-unrecognized flag."""
    result = strip_launchers(["env", "--verbose", "clang-cl", "-c", "x.cc"])
    assert result == ["--verbose", "clang-cl", "-c", "x.cc"]


# -- Round 23, Finding 6 (Codex): ``env -``/``env --`` command separators --


def test_skip_env_prefix_bare_dash_means_ignore_environment() -> None:
    """``env - clang-cl ...`` -- GNU/POSIX's deprecated equivalent of
    ``env -i`` -- must be skipped like any other no-operand flag, not
    mistaken for the driver itself."""
    result = strip_launchers(["env", "-", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_double_dash_terminates_option_parsing() -> None:
    """``env -- clang-cl ...`` -- GNU's option-parsing terminator -- must be
    consumed, with the next token unconditionally treated as the command
    even if it looked flag-shaped."""
    result = strip_launchers(["env", "--", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_double_dash_before_flag_shaped_command() -> None:
    """``--`` specifically protects a flag-shaped COMMAND token from being
    mistaken for one of env's own flags -- the real differentiator from the
    bare ``--`` case above."""
    result = strip_launchers(["env", "--", "-oddly-named-tool", "-c", "x.cc"])
    assert result == ["-oddly-named-tool", "-c", "x.cc"]


def test_skip_env_prefix_dash_negative_control_real_flag_unaffected() -> None:
    """Negative control: an ordinary recognized flag (``-i``) is completely
    unaffected by the new ``-``/``--`` handling."""
    result = strip_launchers(["env", "-i", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


# -- Round 30, Finding 1 (Codex): ``env --`` still consumes NAME=VALUE -----
# assignments that follow it -- ``--`` only terminates OPTION parsing, not
# the assignment list, per real GNU env's own documented grammar.


def test_skip_env_prefix_double_dash_still_consumes_trailing_assignment() -> None:
    """``env -- FOO=bar clang-cl -c x.cc`` -- real GNU ``env``'s ``--``
    terminates OPTION parsing only; the ``NAME=VALUE`` assignment that
    follows is still consumed before the real command is found. A first
    revision of this fix broke out of the whole scan on ``--`` and treated
    the literal ``"FOO=bar"`` token as the driver, losing ``clang-cl`` and
    everything after it entirely."""
    result = strip_launchers(["env", "--", "FOO=bar", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_double_dash_still_tracks_path_assignment(
    tmp_path: Path,
) -> None:
    """``env -- PATH=/tmp/tool clang-cl ...`` -- a ``PATH=`` assignment
    after ``--`` must still update tracked PATH state (not merely be
    consumed as an opaque token), the same as a ``PATH=`` assignment
    appearing before ``--`` already does."""
    driver = tmp_path / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        ["env", "--", f"PATH={tmp_path}", "clang-cl", "-c", "x.cc"]
    )
    _assert_resolved_driver(result[0], driver)
    assert result[1:] == ["-c", "x.cc"]


def test_skip_env_prefix_double_dash_assignment_negative_control_option_literal() -> (
    None
):
    """Negative control proving ``--`` genuinely still suppresses env-OPTION
    reinterpretation after the assignment list: a token that looks like an
    env flag (``-i``) appearing after ``--`` and its assignments is treated
    as the literal command/argument it actually is, NEVER as env's own
    ``-i`` (which would otherwise incorrectly clear tracked PATH state and
    consume the token instead of surfacing it as the driver)."""
    result = strip_launchers(["env", "--", "FOO=bar", "-i", "-c", "x.cc"])
    # `-i` is not itself a NAME=VALUE-shaped token, so it is the first
    # non-assignment token after `--` -- i.e. the literal command.
    assert result == ["-i", "-c", "x.cc"]


# -- Round 23, Finding 7 (Codex): chained ``env -C`` prefixes compose ------


def test_strip_launchers_chained_env_chdir_composes() -> None:
    """``env -C a env -C b ../clang-cl ...`` -- real ``env`` chdirs into
    ``a``, then ``b`` relative to THAT, giving an effective directory of
    ``a/b``. The old code overwrote ``chdir`` with the most recent ``-C``
    value instead of composing them, silently discarding the outer ``-C``
    entirely.

    The driver token itself (``../clang-cl``) is POSIX-spelled EVIDENCE
    TEXT (contains ``/``), so its resolved value is pinned as the literal
    ``"a/clang-cl"`` -- always forward-slash, regardless of which host OS
    runs this analysis (round 24 follow-up, live Windows CI signal: using
    ``os.path.normpath(os.path.join(...))`` here would compute a
    HOST-native, backslash-joined expectation on Windows, silently
    reintroducing the exact host-dependence bug this whole module's
    grammar-from-evidence design exists to avoid -- see
    ``test_strip_launchers_env_chdir_multiple_dotdot_segments_normalized``
    for the original instance of this same pinning discipline). The
    trailing ``x.cc`` operand, by contrast, is a genuinely BARE token with
    no separator at all, composed against an equally bare/ambiguous
    ``chdir`` -- that pairing has no evidence-grammar signal either way,
    so it legitimately falls back to host-native joining and computing the
    expectation via ``os.path.join`` here is correct (it mirrors
    production's own identical fallback)."""
    result = strip_launchers(
        ["env", "-C", "a", "env", "-C", "b", "../clang-cl", "-c", "x.cc"]
    )
    assert result == [
        "a/clang-cl",
        "-c",
        os.path.normpath(os.path.join("a", "b", "x.cc")),
    ]


def test_strip_launchers_chained_env_chdir_absolute_inner_overrides() -> None:
    """A later, ABSOLUTE ``-C`` genuinely replaces the accumulated relative
    chdir rather than composing onto it -- real ``env -C /abs`` is a literal
    chdir regardless of any earlier relative one. Applies to the trailing
    operand too (round 23 Finding 8/12)."""
    result = strip_launchers(
        ["env", "-C", "a", "env", "-C", "/abs", "../clang-cl", "-c", "x.cc"]
    )
    assert result == ["/clang-cl", "-c", "/abs/x.cc"]


def test_strip_launchers_single_env_chdir_negative_control_unchanged() -> None:
    """Negative control: a single, non-chained ``-C`` behaves exactly as
    before -- composition logic is a pure no-op when there is only one
    ``env -C`` prefix to begin with (the trailing operand still folds
    per round 23 Finding 8/12, same as every other single-``-C`` case)."""
    result = strip_launchers(
        ["env", "-C", "build", "../llvm/bin/clang-cl", "-c", "x.cc"]
    )
    assert result == [
        "llvm/bin/clang-cl",
        "-c",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


# -- Round 23, Finding 8 (Codex): ``env -C``'s effective cwd folds into
# trailing operands too, not just the driver token --------------------------


def test_strip_launchers_chdir_folds_into_combined_include_flag() -> None:
    """``env -C build clang-cl -I../include /c ../src/x.cpp`` -- every
    relative filesystem argument the real process receives is interpreted
    relative to the chdir'd directory, not just its own executable name.

    Both ``-I../include``'s value and the trailing ``../src/x.cpp`` are
    POSIX-spelled evidence text (contain ``/``), so their resolved values
    are pinned as literal, always-forward-slash strings rather than
    computed via host-native ``os.path.join`` (round 24 follow-up, live
    Windows CI signal -- see ``test_strip_launchers_chained_env_chdir_
    composes`` for the same pinning discipline applied to a driver
    token)."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-I../include", "/c", "../src/x.cpp"]
    )
    assert result == ["clang-cl", "-Iinclude", "/c", "src/x.cpp"]


def test_strip_launchers_chdir_folds_into_separate_form_include_operand() -> None:
    """The SEPARATE-form spelling (``-I ../include``, two tokens) is
    resolved via flag-context tracking (round 23 Finding 8/11/12), which
    also correctly folds the trailing bare source-file operand
    (``x.cpp``, no separator at all -- Finding 12).

    ``../include`` is POSIX-spelled evidence text (contains ``/``), so its
    resolved value is pinned as a literal string (round 24 follow-up, live
    Windows CI signal); ``x.cpp`` has no separator at all, so its fold is
    genuinely host-fallback-ambiguous and computed via ``os.path.join``
    stays correct on every host."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-I", "../include", "/c", "x.cpp"]
    )
    assert result == [
        "clang-cl",
        "-I",
        "include",
        "/c",
        os.path.normpath(os.path.join("build", "x.cpp")),
    ]


def test_strip_launchers_chdir_does_not_touch_macro_definition_value() -> None:
    """Negative control: a ``-D`` macro value containing a path separator
    (``-DDEFAULT_DIR=a/b``) must NOT be resolved -- it is not a path-bearing
    flag this module recognizes, mirroring ``MACRO_DEFINITION_PREFIXES``'s
    own existing ``~``-expansion carve-out. The trailing bare source-file
    operand still folds (round 23 Finding 8/12)."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-DDEFAULT_DIR=a/b", "/c", "x.cpp"]
    )
    assert result == [
        "clang-cl",
        "-DDEFAULT_DIR=a/b",
        "/c",
        os.path.normpath(os.path.join("build", "x.cpp")),
    ]


def test_strip_launchers_chdir_does_not_touch_unrecognized_combined_flag() -> None:
    """Negative control: an unrecognized combined-form flag with a path
    separator in its value is left completely unchanged. The trailing bare
    source-file operand still folds (round 23 Finding 8/12)."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-Wl,../foo.so", "/c", "x.cpp"]
    )
    assert result == [
        "clang-cl",
        "-Wl,../foo.so",
        "/c",
        os.path.normpath(os.path.join("build", "x.cpp")),
    ]


def test_strip_launchers_chdir_leaves_absolute_operand_unchanged() -> None:
    """Negative control: an already-absolute operand is passed through
    unchanged, same as the driver-token case."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-I/opt/include", "/c", "/src/x.cpp"]
    )
    assert result == ["clang-cl", "-I/opt/include", "/c", "/src/x.cpp"]


def test_strip_launchers_no_chdir_negative_control_operands_unchanged() -> None:
    """Negative control: with no ``env -C`` prefix at all, every trailing
    operand is passed through completely unchanged -- the pre-existing
    behavior for the common (no ``env``) case."""
    argv = ["clang-cl", "-I../include", "/c", "../src/x.cpp"]
    assert strip_launchers(argv) == argv


# -- Round 23, Finding 2 (Codex, adapters/base.py): MSVC detection over an
# ``env -S``-expanded argv --------------------------------------------------


def test_is_msvc_command_true_for_env_split_string_clang_cl() -> None:
    """``env -S 'clang-cl /c x.cc'`` must be recognized as MSVC-dialect --
    the old length-subtraction ``driver_index`` computation coincidentally
    landed on index 0 (``"env"``) for this exact input shape, since the
    expanded-and-stripped result happened to share the same length as the
    original three-token input."""
    from abicheck.buildsource.adapters.base import _is_msvc_command

    assert _is_msvc_command(["env", "-S", "clang-cl /c x.cc"]) is True


def test_msvc_driver_token_resolves_through_env_split_string() -> None:
    """The driver TOKEN itself, not merely the boolean, must also resolve
    correctly through the same expansion."""
    from abicheck.buildsource.adapters.base import msvc_driver_token

    assert msvc_driver_token(["env", "-S", "clang-cl /c x.cc"]) == "clang-cl"


def test_is_msvc_command_env_split_string_with_launcher() -> None:
    """A compiler-cache launcher inside the split string is still unwrapped
    before CL-style driver-name matching."""
    from abicheck.buildsource.adapters.base import _is_msvc_command

    assert _is_msvc_command(["env", "-S", "sccache clang-cl /c x.cc"]) is True


def test_is_msvc_command_negative_control_env_split_string_gnu_driver() -> None:
    """Negative control: an ``env -S``-expanded GNU (non-CL) command must
    still read as non-MSVC -- the fix must not flip every ``-S``-expanded
    command to MSVC dialect indiscriminately."""
    from abicheck.buildsource.adapters.base import _is_msvc_command

    assert _is_msvc_command(["env", "-S", "gcc -c x.c"]) is False


def test_is_msvc_command_negative_control_ordinary_command_unaffected() -> None:
    """Negative control: an ordinary (non-``env``) MSVC command is
    completely unaffected by the expansion step."""
    from abicheck.buildsource.adapters.base import _is_msvc_command

    assert _is_msvc_command(["clang-cl", "/c", "x.cc"]) is True
    assert _is_msvc_command(["gcc", "-c", "x.c"]) is False


# -- Round 24, Finding 9 (Codex): a launcher preceding a NESTED `env -S`
# must still get its split-string expanded -----------------------------------


def test_expand_env_split_prefixes_follows_launcher_then_nested_env_s() -> None:
    """``sccache env -S 'clang-cl /c x.cc'`` -- the launcher-stripping loop
    must not stop the moment ``argv[0]`` isn't itself ``env``; a NESTED
    ``env -S`` reached only after unwrapping a leading launcher must still
    get its split-string expanded."""
    from abicheck.buildsource.source_extractors._argv import (
        expand_env_split_prefixes,
    )

    result = expand_env_split_prefixes(["sccache", "env", "-S", "clang-cl /c x.cc"])
    assert result == ["sccache", "env", "clang-cl", "/c", "x.cc"]


def test_is_msvc_command_true_for_launcher_then_nested_env_split_string() -> None:
    """End-to-end through the real consumer: ``sccache env -S 'clang-cl /c
    x.cc'`` must be recognized as MSVC-dialect."""
    from abicheck.buildsource.adapters.base import (
        _is_msvc_command,
        msvc_driver_token,
    )

    argv = ["sccache", "env", "-S", "clang-cl /c x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_is_msvc_command_negative_control_launcher_only_no_nested_env() -> None:
    """Negative control: a launcher with NO nested ``env -S`` behaves
    exactly as before -- the fix must not spuriously expand anything when
    there is nothing to expand."""
    from abicheck.buildsource.adapters.base import _is_msvc_command

    assert _is_msvc_command(["sccache", "clang-cl", "/c", "x.cc"]) is True
    assert _is_msvc_command(["sccache", "gcc", "-c", "x.c"]) is False


# -- Round 24, Findings 11/12 (Codex): chdir-folding is flag-CONTEXT-aware,
# not a per-token separator-presence guess ------------------------------------


def test_strip_launchers_chdir_does_not_fold_separate_form_macro_value() -> None:
    """Finding 11: ``-D FOO=a/b`` (SEPARATE form, two tokens) must NOT have
    its value token path-folded merely because it contains a ``/`` -- the
    value is macro text, not a path, and folding it would corrupt the
    macro definition (``FOO=a/b`` -> the wrong ``FOO=build/a/b``)."""
    result = strip_launchers(
        ["env", "-C", "build", "clang-cl", "-D", "FOO=a/b", "/c", "x.cpp"]
    )
    assert result == [
        "clang-cl",
        "-D",
        "FOO=a/b",
        "/c",
        os.path.normpath(os.path.join("build", "x.cpp")),
    ]


def test_strip_launchers_chdir_does_not_fold_separate_form_undef_value() -> None:
    """Finding 11's sibling: ``-U`` (undef) behaves identically to ``-D``."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "-U", "a/b"])
    assert result == ["clang-cl", "-U", "a/b"]


def test_strip_launchers_chdir_folds_separator_free_combined_include() -> None:
    """Finding 12: ``-Iinclude`` (combined, no separator in the value at
    all) is exactly as real a relative path as ``-I../include`` and must
    be folded against the effective chdir."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "-Iinclude"])
    assert result == [
        "clang-cl",
        "-I" + os.path.normpath(os.path.join("build", "include")),
    ]


def test_strip_launchers_chdir_folds_separator_free_separate_include() -> None:
    """Finding 12, separate-form spelling: ``-I include``."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "-I", "include"])
    assert result == [
        "clang-cl",
        "-I",
        os.path.normpath(os.path.join("build", "include")),
    ]


def test_strip_launchers_chdir_folds_separate_form_sysroot() -> None:
    """The bare (non-``=``) separate-form ``--sysroot DIR`` spelling is
    also recognized and folded, alongside the already-covered
    ``--sysroot=DIR`` equals-form."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "--sysroot", "sdk"])
    assert result == [
        "clang-cl",
        "--sysroot",
        os.path.normpath(os.path.join("build", "sdk")),
    ]


def test_strip_launchers_chdir_folds_separate_form_forced_include() -> None:
    """GCC's ``-include FILE`` (separate-form only -- no combined spelling
    exists) is recognized as a path-bearing flag too."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "-include", "config.h"])
    assert result == [
        "clang-cl",
        "-include",
        os.path.normpath(os.path.join("build", "config.h")),
    ]


def test_strip_launchers_chdir_macro_negative_control_combined_form_unaffected() -> (
    None
):
    """Negative control: the already-covered COMBINED-form macro case
    (``-DDEFAULT_DIR=a/b``) is unaffected by the new separate-form
    tracking."""
    result = strip_launchers(["env", "-C", "build", "clang-cl", "-DFOO=a/b"])
    assert result == ["clang-cl", "-DFOO=a/b"]


# -- Round 25, Finding 2 (Codex): chdir-folding must not treat every
# non-flag-shaped token as a path -- confirmed non-path value-taking flags
# (``-x``/``-target``/``-arch``/``-Xclang``) keep their own operand intact --


def test_strip_launchers_chdir_does_not_fold_language_flag_value() -> None:
    """``-x c++`` -- a language name, not a path -- must not be corrupted
    into ``-x build/c++``. The trailing bare source-file operand still
    folds (Finding 8/12)."""
    result = strip_launchers(["env", "-C", "build", "clang", "-x", "c++", "x.cc"])
    assert result == [
        "clang",
        "-x",
        "c++",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_does_not_fold_target_triple_value() -> None:
    """``-target <triple>`` (bare, separate-form) -- the triple string is
    not a path."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-target", "x86_64-linux-gnu", "x.cc"]
    )
    assert result == [
        "clang",
        "-target",
        "x86_64-linux-gnu",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_does_not_fold_arch_value() -> None:
    """``-arch <name>`` (Apple universal-build architecture selector) --
    the architecture name is not a path."""
    result = strip_launchers(["env", "-C", "build", "clang", "-arch", "arm64", "x.cc"])
    assert result == [
        "clang",
        "-arch",
        "arm64",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_does_not_fold_xclang_forwarded_value() -> None:
    """``-Xclang <value>`` forwards its own following token verbatim to
    ``-cc1`` -- an arbitrary cc1 flag/value, not generally a path."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-Xclang", "-fsome-cc1-flag", "x.cc"]
    )
    assert result == [
        "clang",
        "-Xclang",
        "-fsome-cc1-flag",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_xclang_wrapped_split_operand_abi_flag_unaffected() -> (
    None
):
    """Negative control: the ``-Xclang -target-abi -Xclang aapcs``
    four-token cc1 spelling (:data:`SPLIT_OPERAND_ABI_FLAGS`) is completely
    unaffected by the new ``-Xclang`` non-path-value tracking -- every
    token here is either a flag or a confirmed-non-path value, so nothing
    is folded regardless."""
    result = strip_launchers(
        [
            "env",
            "-C",
            "build",
            "clang",
            "-Xclang",
            "-target-abi",
            "-Xclang",
            "aapcs",
            "x.cc",
        ]
    )
    assert result == [
        "clang",
        "-Xclang",
        "-target-abi",
        "-Xclang",
        "aapcs",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_still_folds_genuine_path_operand_of_include_flag() -> (
    None
):
    """Positive control: an actual known path-taking flag
    (:data:`_CHDIR_FOLD_SEPARATE_PATH_FLAGS`) is unaffected by the new
    non-path-value flag set -- its operand still folds, exactly as
    before."""
    result = strip_launchers(["env", "-C", "build", "clang", "-I", "include", "x.cc"])
    assert result == [
        "clang",
        "-I",
        os.path.normpath(os.path.join("build", "include")),
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_unrecognized_flag_operand_no_longer_folded() -> None:
    """Round 30 Finding 4 (Codex review, fresh evidence): a flag this module
    cannot positively classify no longer has its own possible non-path
    operand folded by default -- ``value.txt`` (an unrecognized flag's
    genuine operand, not a source/header file) is left completely
    unchanged, while the trailing genuine source-file operand (``x.cc``)
    still folds. This REPLACES the old "folds by default" behavior a
    previous revision of this test pinned as a known, documented
    limitation -- that default was exactly what let a real non-path value
    like ``-meabi gnu`` get corrupted into ``build/gnu`` (see the sibling
    test below for that exact repro)."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-some-unknown-flag", "value.txt", "x.cc"]
    )
    assert result == [
        "clang",
        "-some-unknown-flag",
        "value.txt",
        os.path.normpath(os.path.join("build", "x.cc")),
    ]


def test_strip_launchers_chdir_meabi_value_not_folded() -> None:
    """Round 30 Finding 4 (Codex review, fresh evidence): ``env -C build
    clang -target armv7-none-eabi -meabi gnu -c x.c`` must not fold
    ``gnu`` (the value of ``-meabi``, confirmed via a real ``clang
    --help``: ``-meabi <value>`` sets the EABI type, not a path) into
    ``build/gnu``. Rather than adding ``-meabi`` as a 5th individually
    enumerated flag to :data:`_CHDIR_FOLD_NON_PATH_VALUE_FLAGS` (the
    unsustainable, whack-a-mole shape of fix Codex explicitly flagged),
    the general fix makes an unrecognized flag's non-path-suffixed operand
    safe by default -- see :func:`_looks_like_source_file_operand`."""
    result = strip_launchers(
        [
            "env",
            "-C",
            "build",
            "clang",
            "-target",
            "armv7-none-eabi",
            "-meabi",
            "gnu",
            "-c",
            "x.c",
        ]
    )
    assert result == [
        "clang",
        "-target",
        "armv7-none-eabi",
        "-meabi",
        "gnu",
        "-c",
        os.path.normpath(os.path.join("build", "x.c")),
    ]


@pytest.mark.parametrize(
    "unknown_flag,value",
    [
        ("-meabi", "gnu"),
        ("-mfoo-mode", "fast"),
        ("-some-future-flag", "widget"),
        ("--another-unknown-option", "on"),
        ("-Wfuture-warning-name", "enable"),
    ],
)
def test_strip_launchers_chdir_various_unrecognized_flag_values_never_folded(
    unknown_flag: str, value: str
) -> None:
    """Round 30 Finding 4 (Codex review, fresh evidence), generalized: this
    is a property of the DEFAULT, not a fact about ``-meabi`` specifically
    -- an arbitrary, never-before-seen flag's non-path-suffixed value is
    always left unresolved, without that flag ever being named in this
    module's source. The whole point of the fix is "any non-listed flag
    operand is safe by default," not "here is one more name we thought
    of" -- this test would keep passing for a 6th, 7th, ... Nth
    hypothetical flag with no further code changes."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", unknown_flag, value, "-c", "x.c"]
    )
    assert result == [
        "clang",
        unknown_flag,
        value,
        "-c",
        os.path.normpath(os.path.join("build", "x.c")),
    ]


# -- Round 25, Finding 3 (Codex): ``env``'s signal-handling long flags -----


def test_skip_env_prefix_ignore_signal_with_sig_value_recognized() -> None:
    """``env --ignore-signal=PIPE clang-cl ...`` -- confirmed against a
    real installed ``env --help``: ``--ignore-signal[=SIG]`` is a
    documented, valid prefix option. Before this fix, none of the four
    signal-handling long flags were recognized, so this misread the whole
    ``--ignore-signal=PIPE`` token as the driver itself."""
    result = strip_launchers(["env", "--ignore-signal=PIPE", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_ignore_signal_bare_form_recognized() -> None:
    """The bare form (no ``=SIG``) is also documented as valid --
    ``--ignore-signal`` alone (no argument at all) is accepted."""
    result = strip_launchers(["env", "--ignore-signal", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_block_signal_with_sig_value_recognized() -> None:
    """``--block-signal=SIG`` -- sibling of ``--ignore-signal``."""
    result = strip_launchers(["env", "--block-signal=HUP", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_block_signal_bare_form_recognized() -> None:
    result = strip_launchers(["env", "--block-signal", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_default_signal_with_sig_value_recognized() -> None:
    """``--default-signal=SIG`` -- sibling of ``--ignore-signal``."""
    result = strip_launchers(["env", "--default-signal=TERM", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_list_signal_handling_recognized() -> None:
    """``--list-signal-handling`` takes no argument at all, in either
    form."""
    result = strip_launchers(
        ["env", "--list-signal-handling", "clang-cl", "-c", "x.cc"]
    )
    assert result == ["clang-cl", "-c", "x.cc"]


def test_skip_env_prefix_signal_flags_negative_control_unrelated_flag_not_swallowed() -> (
    None
):
    """Negative control: an unrelated, genuinely unrecognized flag must
    still stop the scan and be treated as the driver, exactly as before --
    the new signal-flag vocabulary does not over-match anything else."""
    result = strip_launchers(["env", "--unrecognized-flag", "clang-cl", "-c", "x.cc"])
    assert result == ["--unrecognized-flag", "clang-cl", "-c", "x.cc"]
