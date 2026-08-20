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

"""Sibling split for round 29's Codex review Finding E on
:mod:`abicheck.buildsource.source_extractors.clang` -- kept out of
``tests/test_source_extractors_clang.py`` (already near this repo's
AI-readiness soft/hard cap) rather than growing that file further, matching
this repo's own established sibling-split convention.

Finding: ``CompileUnit.include_paths``/``system_include_paths`` are already
STRUCTURED, absolute paths, resolved by the producing adapter against the
compile unit's own recorded ``directory`` -- before any leading ``env -C
DIR`` prefix in ``argv`` is considered. ``extract()``'s own effective-cwd
fix (round 26 Finding 10) only corrects the REPLAY subprocess's cwd, not
these two already-absolute structured fields, so ``build_clang_command()``
still emitted the wrong absolute ``-I`` for a unit recorded under an
``env -C`` prefix.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors.clang import build_clang_command


def test_build_clang_command_rebases_include_paths_under_env_chdir() -> None:
    """``directory=/work``, ``argv=['env', '-C', 'build', 'clang',
    '-Iinclude', ...]``: the real compiler searched ``/work/build/include``,
    not the structured field's own pre-chdir ``/work/include``."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-Iinclude", "-c", "x.cc"],
        include_paths=["/work/include"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert "-I" in cmd
    assert cmd[cmd.index("-I") + 1] == "/work/build/include"


def test_build_clang_command_rebases_system_include_paths_under_env_chdir() -> None:
    """The sibling ``system_include_paths`` field is rebased identically."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-isystem", "sysinc", "-c", "x.cc"],
        system_include_paths=["/work/sysinc"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert "-isystem" in cmd
    assert cmd[cmd.index("-isystem") + 1] == "/work/build/sysinc"


def test_build_clang_command_rebases_multiple_nested_include_paths() -> None:
    """A deeper, nested include path under the compile-unit directory
    rebases correctly too, confirming this is a real prefix substitution
    and not merely a single-path-component special case."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-c", "x.cc"],
        include_paths=["/work/include/nested/deep"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/build/include/nested/deep"


def test_build_clang_command_explicit_absolute_include_not_rebased_under_env_chdir() -> (
    None
):
    """Round 30 Finding 2 (Codex review, fresh evidence): an EXPLICITLY
    absolute ``-I/work/include`` operand must NOT move under ``env -C
    build`` -- an absolute path is absolute regardless of cwd -- even
    though it string-prefix-matches ``compile_unit.directory`` exactly the
    same way a relative ``-Iinclude`` operand the adapter already resolved
    to that same absolute string would. Without
    ``explicit_absolute_include_paths`` recording which is which, both look
    identical to ``_rebase_structured_path`` and the explicit path was
    incorrectly rewritten to ``/work/build/include`` -- the exact
    corruption this field exists to prevent."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-I/work/include", "-c", "x.cc"],
        include_paths=["/work/include"],
        explicit_absolute_include_paths=["/work/include"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/include"


def test_build_clang_command_relative_include_still_rebased_under_env_chdir() -> None:
    """Regression guard for the round-29 fix itself: a genuinely RELATIVE
    ``-Iinclude`` operand (nothing recorded in
    ``explicit_absolute_include_paths``) is still correctly rebased onto
    the effective ``env -C`` directory, unaffected by Finding 2's fix."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-Iinclude", "-c", "x.cc"],
        include_paths=["/work/include"],
        explicit_absolute_include_paths=[],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/build/include"


def test_build_clang_command_mixed_explicit_and_relative_includes_resolve_independently() -> (
    None
):
    """A single compile unit carrying BOTH an explicitly absolute include
    and a relative-then-joined one (same absolute prefix, so both would
    string-match ``old_base`` identically) must resolve each independently
    -- the explicit one stays put, the derived one rebases."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=[
            "env",
            "-C",
            "build",
            "clang",
            "-I/work/vendor",
            "-Ilocal",
            "-c",
            "x.cc",
        ],
        include_paths=["/work/vendor", "/work/local"],
        explicit_absolute_include_paths=["/work/vendor"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    idxs = [i for i, tok in enumerate(cmd) if tok == "-I"]
    values = [cmd[i + 1] for i in idxs]
    assert "/work/vendor" in values  # explicit -- unmoved
    assert "/work/build/local" in values  # relative -- rebased


def test_build_clang_command_negative_control_no_env_chdir_unaffected() -> None:
    """Negative control: with NO ``env -C`` prefix at all, the structured
    include path is emitted completely unchanged -- confirming this fix
    did not regress the ordinary, already-correct case."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["clang", "-Iinclude", "-c", "x.cc"],
        include_paths=["/work/include"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/include"


def test_build_clang_command_negative_control_path_outside_directory_unaffected() -> (
    None
):
    """Negative control: an absolute include path that is NOT anchored
    under the compile unit's own ``directory`` at all (e.g. a sysroot
    include dir the real build recorded verbatim) has no matching prefix
    and is left completely unchanged -- it was never relative to the
    pre-chdir directory to begin with."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-c", "x.cc"],
        include_paths=["/opt/sdk/include"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/opt/sdk/include"


def test_build_clang_command_negative_control_directory_itself_as_include() -> None:
    """Edge case: the include path IS exactly the compile unit's own
    ``directory`` (``-I.`` resolved to the bare directory itself) -- the
    exact-match branch rebases it to the bare effective directory, not a
    trailing-separator-corrupted string."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-c", "x.cc"],
        include_paths=["/work"],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/build"
