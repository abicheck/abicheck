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


# -- Finding 1: `env -C DIR` folds into a relative driver token --------------


def test_strip_launchers_env_chdir_folds_into_relative_driver_token() -> None:
    """``env -C build ../llvm/bin/clang-cl ...`` must resolve the driver
    relative to ``build``, not the bare, un-chdir'd base -- folded as a
    plain lexical join+normalize so the caller's own later join against
    ``cu.directory`` still composes correctly (see
    ``header_compile_context._resolve_driver_token``, exercised end-to-end
    in ``tests/test_header_compile_context_gcc_path.py``)."""
    result = strip_launchers(
        ["env", "-C", "build", "../llvm/bin/clang-cl", "/std:c++20", "-c", "x.cc"]
    )
    assert result == ["llvm/bin/clang-cl", "/std:c++20", "-c", "x.cc"]


def test_strip_launchers_env_chdir_long_form() -> None:
    """The GNU long-option spellings (``--chdir DIR`` and ``--chdir=DIR``)
    behave identically to ``-C DIR``."""
    assert strip_launchers(
        ["env", "--chdir", "build", "../clang-cl", "-c", "x.cc"]
    ) == [
        "clang-cl",
        "-c",
        "x.cc",
    ]
    assert strip_launchers(["env", "--chdir=build", "../clang-cl", "-c", "x.cc"]) == [
        "clang-cl",
        "-c",
        "x.cc",
    ]


def test_strip_launchers_env_chdir_left_alone_for_bare_driver_name() -> None:
    """A bare PATH name (no path separator) is looked up on PATH, never
    relative to any directory -- chdir must not be joined onto it."""
    assert strip_launchers(["env", "-C", "build", "clang-cl", "-c", "x.cc"]) == [
        "clang-cl",
        "-c",
        "x.cc",
    ]


def test_strip_launchers_env_chdir_left_alone_for_absolute_driver() -> None:
    """An already-absolute driver token is not joined onto chdir a second
    time."""
    assert strip_launchers(
        ["env", "-C", "build", "/opt/llvm/bin/clang-cl", "-c", "x.cc"]
    ) == ["/opt/llvm/bin/clang-cl", "-c", "x.cc"]


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
    driver token found *after* the launcher is unwrapped."""
    result = strip_launchers(
        ["env", "-C", "build", "sccache", "../llvm/bin/clang-cl", "-c", "x.cc"]
    )
    assert result == ["llvm/bin/clang-cl", "-c", "x.cc"]


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
    assert result == [str(driver), "/std:c++20", "-c", "x.cc"]


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
    assert result == [str(driver), "-c", "x.cc"]


def test_strip_launchers_env_chdir_and_path_together(tmp_path: Path) -> None:
    """``env -C build PATH=/opt/llvm/bin clang-cl ...`` -- both effects can
    be present on the same prefix; chdir applies only to a path-shaped
    token and PATH only to a bare one, so for a single bare driver name
    only the PATH resolution actually changes anything."""
    driver = tmp_path / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        ["env", "-C", "build", f"PATH={tmp_path}", "clang-cl", "-c", "x.cc"]
    )
    assert result == [str(driver), "-c", "x.cc"]


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
    PATH resolution applies to a non-existent driver, and the flag is
    passed through unchanged."""
    assert strip_launchers(["env", "-C", "build", "-c", "foo.c"]) == [
        "-c",
        "foo.c",
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
    _make_executable(driver)
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == str(driver)


def test_strip_launchers_without_directory_leaves_relative_env_path_bare() -> None:
    """Without a *directory* (every caller besides ``pick_compiler_binary``
    -- the pre-existing, directory-agnostic behavior for
    ``strip_launchers``'s other callers), a relative ``PATH=`` entry cannot
    be safely resolved, so the bare driver name is left unchanged rather
    than searched against abicheck's own CWD."""
    result = strip_launchers(
        ["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"]
    )
    assert result == ["clang-cl", "/c", "x.cc"]


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
    _make_executable(driver)
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "PATH=tool", "clang-cl", "/c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == str(driver)


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
