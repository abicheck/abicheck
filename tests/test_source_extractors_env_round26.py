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

"""Sibling split of ``test_source_extractors_env.py`` (near its 1500-line
AI-readiness soft cap) for round 26's two Codex review findings on
:mod:`abicheck.buildsource.source_extractors._argv`:

1. Finding 1 -- ``pick_compiler_binary`` returned a still-relative,
   ``env -C``-composed driver token WITHOUT joining it onto the compile
   unit's own ``directory`` (unlike ``header_compile_context.
   _derived_gcc_path``, which already did this for its own driver
   resolution) -- a caller resolving the token from a different effective
   cwd (e.g. ``CastxmlSourceExtractor``, which runs from the effective
   ``-C`` cwd per earlier rounds' chdir-cwd fixes) resolved it a second,
   wrong time.
2. Finding 3 -- nested ``env`` wrappers had no way to represent "PATH was
   just cleared to empty" (``-i``/``-u PATH``) as distinct from "no PATH
   opinion stated, inherit whatever came before" -- so an outer ``PATH=``
   override incorrectly survived through an inner ``env -i``/``env -u
   PATH``, contradicting real GNU ``env -i``'s documented "start with an
   empty environment" behavior.

``_make_executable``/``_assert_resolved_driver`` below are duplicated from
``test_source_extractors_env.py`` (not imported -- this test tree has no
``__init__.py``, so sibling test modules do not import from one another;
every other sibling split in this suite follows the same self-contained
convention) rather than sharing a fixture module, keeping this file a
complete, independently-runnable unit.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from abicheck.buildsource.source_extractors._argv import strip_launchers


def _make_executable(path: Path) -> Path:
    """Create an executable fixture at (or near) *path*, returning the
    actual path created. See ``test_source_extractors_env.py``'s identical
    helper for the full Windows-``PATHEXT`` rationale.
    """
    if sys.platform == "win32" and not path.suffix:
        path = path.with_suffix(".exe")
    path.write_text("", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _assert_resolved_driver(actual: str, expected: Path) -> None:
    """Compare a ``shutil.which``-resolved driver path case-insensitively.
    See ``test_source_extractors_env.py``'s identical helper for the full
    live-Windows-CI rationale.
    """
    assert os.path.normcase(actual) == os.path.normcase(str(expected))


# -- Round 26, Finding 1 (Codex): `pick_compiler_binary` must join a still- --
# -- relative `env -C`-composed driver token onto the compile unit's own  --
# -- `directory`, not return it unjoined                                  --


def test_pick_compiler_binary_resolves_env_chdir_relative_driver_path(
    tmp_path,
) -> None:
    """``env -C build ../tool/clang++ ...`` recorded for a compile unit in
    ``<tmp_path>``: GNU ``env -C build`` chdirs to ``<tmp_path>/build``
    FIRST, and only then resolves the now-relative ``../tool/clang++`` from
    there -- landing back at ``<tmp_path>/tool/clang++``, not
    ``<tmp_path>/build/tool/clang++``.

    ``strip_launchers``/``_apply_env_context`` fold the ``-C build`` value
    into the driver token (``join_path_token("build", "../tool/clang++")``
    == ``"tool/clang++"``), but that result is still RELATIVE to the
    compile unit's own ``directory`` -- before this fix,
    ``pick_compiler_binary`` returned it unjoined, so a caller resolving it
    from a different effective cwd (e.g. ``CastxmlSourceExtractor``, which
    runs from the effective ``-C`` cwd per the round-23/24 chdir-cwd fixes)
    would resolve it a SECOND, wrong time.
    """
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    (tmp_path / "build").mkdir()
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang++"
    driver = _make_executable(driver)
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory=str(tmp_path),
        output="x.o",
        argv=["env", "-C", "build", "../tool/clang++", "-c", "x.cc"],
    )
    result = pick_compiler_binary(cu, override=None)
    assert os.path.normcase(result) == os.path.normcase(
        os.path.normpath(str(tool_dir / driver.name))
    )


def test_pick_compiler_binary_resolves_plain_relative_driver_without_env_too() -> None:
    """A plain relative driver token with NO ``env`` prefix at all is now
    ALSO joined onto ``compile_unit.directory`` -- the missing join this
    fix closes applied to ANY relative driver token this function returns,
    not merely the narrower ``env -C``-composed one round 26 Finding 1
    reported (AGENTS.md "Fix the cause, not the instance"). Mirrors
    ``header_compile_context._derived_gcc_path``'s already-established
    identical treatment of this exact shape (see
    ``test_resolve_derives_gcc_path_resolves_relative_driver_against_cu_directory``
    in ``test_header_compile_context_gcc_path.py``) -- ``pick_compiler_binary``
    was the one caller inconsistent with it."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["../llvm/bin/clang-cl", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == os.path.normpath(
        "/work/../llvm/bin/clang-cl"
    )


def test_pick_compiler_binary_relative_driver_empty_directory_left_unjoined() -> None:
    """Negative control: an empty/falsy ``compile_unit.directory`` (no real
    base to join against) leaves a relative driver token unchanged --
    exactly ``_resolve_driver_token``'s own treatment of this degenerate
    case."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="",
        output="x.o",
        argv=["../llvm/bin/clang-cl", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "../llvm/bin/clang-cl"


def test_pick_compiler_binary_bare_driver_name_unaffected_by_directory() -> None:
    """A bare, PATH-searched driver name (no path separator) is left
    unchanged regardless of ``directory`` -- joining it would be wrong,
    since a bare name is resolved by ``PATH`` lookup, never relative to any
    directory."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang-cl", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "clang-cl"


def test_pick_compiler_binary_absolute_driver_via_env_chdir_unaffected() -> None:
    """An already-ABSOLUTE driver token following ``env -C`` must never be
    joined onto ``directory`` a second time -- only normalized."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors._argv import pick_compiler_binary

    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "/opt/llvm/bin/../bin/clang++", "-c", "x.cc"],
    )
    assert pick_compiler_binary(cu, override=None) == "/opt/llvm/bin/clang++"


# -- Round 26, Finding 3 (Codex): nested `env -i`/`env -u PATH` must clear --
# -- an outer-layer `PATH=` override, not silently let it survive through --


def test_nested_env_dash_i_clears_outer_path(tmp_path) -> None:
    """``env PATH=<tool> /usr/bin/env -i clang-cl ...`` -- real GNU
    ``env -i`` starts the wrapped command with a COMPLETELY EMPTY
    environment (confirmed via ``env --help``: ``-i,
    --ignore-environment`` -- "start with an empty environment"), so the
    OUTER ``PATH=<tool>`` assignment must NOT carry through to the INNER
    ``env -i``'s own driver resolution. Before this fix, only an explicit
    new ``PATH=`` assignment updated the tracked PATH state -- ``-i`` had
    no way to represent "PATH was just cleared", so the outer PATH
    incorrectly survived and resolved the bare driver name anyway."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "-i",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    # The outer PATH must NOT have resolved the inner, -i-wrapped driver --
    # it stays the bare, unresolved name.
    assert result[0] == "clang-cl"


def test_nested_env_dash_u_path_clears_outer_path(tmp_path) -> None:
    """Companion to the ``-i`` case: ``env -u PATH`` (POSIX "unset one
    variable", here targeting ``PATH`` specifically) must clear an outer
    ``PATH=`` override identically to ``-i`` -- unsetting any OTHER
    variable must not."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "-u",
            "PATH",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    assert result[0] == "clang-cl"


def test_nested_env_unset_equals_path_joined_form_clears_outer_path(tmp_path) -> None:
    """The joined ``--unset=PATH`` spelling of the same case above."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "--unset=PATH",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    assert result[0] == "clang-cl"


def test_nested_env_bare_dash_alias_clears_outer_path(tmp_path) -> None:
    """The deprecated bare ``-`` alias for ``-i`` must clear PATH
    identically -- it is documented as exactly that flag's equivalent."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "-",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    assert result[0] == "clang-cl"


def test_nested_env_unset_unrelated_variable_does_not_clear_outer_path(
    tmp_path,
) -> None:
    """Negative control: unsetting a variable OTHER than ``PATH`` must not
    clear the tracked ``PATH=`` state at all."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "-u",
            "SOMEOTHERVAR",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    _assert_resolved_driver(result[0], driver)


def test_nested_env_plain_no_i_no_u_path_still_inherits_outer_path(
    tmp_path,
) -> None:
    """Negative control (Codex's explicitly requested case): a plain nested
    ``env`` WITHOUT ``-i``/``-u PATH`` -- carrying only an unrelated
    ``NAME=VALUE`` assignment -- must still correctly INHERIT the outer
    ``PATH=`` override, exactly as it did before this fix. This is the
    proof that the fix only affects the two explicit-clearing shapes, not
    ordinary nested-``env`` PATH inheritance in general."""
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    driver = _make_executable(driver)
    result = strip_launchers(
        [
            "env",
            f"PATH={tool_dir}",
            "/usr/bin/env",
            "FOO=1",
            "clang-cl",
            "/c",
            "x.cc",
        ]
    )
    _assert_resolved_driver(result[0], driver)


def test_env_dash_i_without_nesting_still_recognized_as_no_operand_flag() -> None:
    """Negative control: a single, non-nested ``env -i clang-cl ...`` (no
    outer PATH= to clear at all) is unaffected -- ``-i`` is still just
    skipped, the driver token is found normally, and there is nothing for
    the new PATH-clearing state to interfere with."""
    result = strip_launchers(["env", "-i", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]


def test_env_dash_u_path_without_nesting_still_finds_driver() -> None:
    """Same negative control for ``-u PATH`` with no outer PATH= present."""
    result = strip_launchers(["env", "-u", "PATH", "clang-cl", "-c", "x.cc"])
    assert result == ["clang-cl", "-c", "x.cc"]
