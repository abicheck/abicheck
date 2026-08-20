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
        include_paths_explicit=[False],
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
        system_include_paths_explicit=[False],
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
        include_paths_explicit=[False],
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
    ``include_paths_explicit`` recording which is which, both look
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
        include_paths_explicit=[True],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/include"


def test_build_clang_command_relative_include_still_rebased_under_env_chdir() -> None:
    """Regression guard for the round-29 fix itself: a genuinely RELATIVE
    ``-Iinclude`` operand (nothing recorded in
    ``include_paths_explicit``) is still correctly rebased onto
    the effective ``env -C`` directory, unaffected by Finding 2's fix."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-Iinclude", "-c", "x.cc"],
        include_paths=["/work/include"],
        include_paths_explicit=[False],
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
        include_paths_explicit=[True, False],
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
        include_paths_explicit=[False],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/build"


# -- Round 31 Finding 1 (Codex review, fresh evidence): provenance tracked
# per LIST POSITION, not by shared value identity -----------------------


def test_build_clang_command_same_string_occurring_twice_resolves_independently() -> (
    None
):
    """Two DIFFERENT ``include_paths`` occurrences that normalize to the
    IDENTICAL final string -- one genuinely relative (``-Iinclude``,
    resolves to ``/work/include``), one genuinely explicit
    (``-I/work/include``) -- must not collapse onto one shared answer. A
    value-keyed ``set[str]`` of "explicitly absolute strings" cannot tell
    the two apart at all (both entries equal ``"/work/include"``); only
    position-aligned ``include_paths_explicit`` can. The first occurrence
    (index 0, derived) rebases; the second (index 1, explicit) does not."""
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
            "-Iinclude",
            "-I/work/include",
            "-c",
            "x.cc",
        ],
        include_paths=["/work/include", "/work/include"],
        include_paths_explicit=[False, True],
    )
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    idxs = [i for i, tok in enumerate(cmd) if tok == "-I"]
    values = [cmd[i + 1] for i in idxs]
    assert values == ["/work/build/include", "/work/include"]


# -- Round 31 Finding 3 (Codex review, fresh evidence): a legacy pack with
# the provenance field absent must degrade to "unknown, do not rebase" --


def test_build_clang_command_legacy_pack_without_provenance_field_not_rebased() -> (
    None
):
    """A ``CompileUnit`` loaded from a pack persisted before
    ``include_paths_explicit``/``system_include_paths_explicit`` existed
    (``from_dict`` on a dict lacking those keys) has both lists come back
    empty while ``include_paths`` is non-empty -- a length mismatch
    ``explicit_or_unknown()`` treats as "unknown provenance" and degrades
    to "do not rebase" (never to "derived", which would newly rebase a
    path an older abicheck version always left untouched, silently
    changing that old, previously-correct pack's replay semantics)."""
    legacy_dict = {
        "id": "cu://x.cc",
        "source": "x.cc",
        "language": "CXX",
        "directory": "/work",
        "output": "x.o",
        "argv": ["env", "-C", "build", "clang", "-I/work/include", "-c", "x.cc"],
        "include_paths": ["/work/include"],
        # No "include_paths_explicit" key at all -- simulates a pack
        # persisted before round 30/31.
    }
    cu = CompileUnit.from_dict(legacy_dict)
    assert cu.include_paths_explicit == []
    cmd = build_clang_command(cu, Path("/work/x.cc"), clang_bin="clang")
    # Old, pre-round-30 behavior: never rebased at all.
    assert cmd[cmd.index("-I") + 1] == "/work/include"


def test_build_clang_command_modern_pack_with_empty_provenance_list_still_rebases() -> (
    None
):
    """Negative control distinguishing "legacy, field truly absent" from
    "modern, field present and correctly empty": a unit with ZERO
    ``include_paths`` has an empty ``include_paths_explicit`` too by
    construction (nothing to record provenance for), which must not be
    misread as "unknown" -- there is nothing to rebase either way, so the
    degradation is unobservable, but a unit that DOES have includes and a
    correctly-populated (all-``False``) explicit list must still rebase
    normally, not fall back to "unknown" just because the round-trip
    happened to produce an all-``False`` list."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "clang", "-Iinclude", "-c", "x.cc"],
        include_paths=["/work/include"],
        include_paths_explicit=[False],
    )
    round_tripped = CompileUnit.from_dict(cu.to_dict())
    assert round_tripped.include_paths_explicit == [False]
    cmd = build_clang_command(round_tripped, Path("/work/x.cc"), clang_bin="clang")
    assert cmd[cmd.index("-I") + 1] == "/work/build/include"
