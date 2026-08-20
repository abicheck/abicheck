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

"""Sibling split for round 31's CodeRabbit Finding C on
:mod:`abicheck.buildsource.source_extractors.castxml` (kept out of
``tests/test_source_extractors.py``, already near this repo's AI-readiness
soft/hard cap, rather than growing that file further -- matching this
repo's own established sibling-split convention).

Finding C: ``build_castxml_command()`` is the CastXML-backend sibling of
round 30 Finding 2 / round 31 Finding 1 (already fixed for
``clang.py``'s ``_clang_context_args()``) -- when ``env -C build`` is
present, a structured include path recorded as ``/work/include``
(relative-resolved from raw ``directory=/work``) was passed UNCHANGED
into the castxml command. Setting the castxml subprocess's own ``cwd`` to
the effective directory (an earlier round) does not by itself fix this:
the include path string itself was still wrong regardless of which
directory castxml is launched from.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors.castxml import build_castxml_command


def test_build_castxml_command_rebases_include_paths_under_env_chdir() -> None:
    """``directory=/work``, ``argv=['env', '-C', 'build', 'gcc',
    '-Iinclude', ...]``: the real compiler searched ``/work/build/include``,
    not the structured field's own pre-chdir ``/work/include``."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "gcc", "-Iinclude", "-c", "x.c"],
        include_paths=["/work/include"],
        include_paths_explicit=[False],
    )
    cmd = build_castxml_command(cu, Path("/work/x.c"), Path("/work/out.xml"))
    assert cmd[cmd.index("-I") + 1] == "/work/build/include"


def test_build_castxml_command_rebases_system_include_paths_under_env_chdir() -> None:
    """The sibling ``system_include_paths`` field is rebased identically."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "gcc", "-isystem", "sysinc", "-c", "x.c"],
        system_include_paths=["/work/sysinc"],
        system_include_paths_explicit=[False],
    )
    cmd = build_castxml_command(cu, Path("/work/x.c"), Path("/work/out.xml"))
    assert cmd[cmd.index("-isystem") + 1] == "/work/build/sysinc"


def test_build_castxml_command_explicit_absolute_include_not_rebased_under_env_chdir() -> (
    None
):
    """An EXPLICITLY absolute ``-I/work/include`` operand must NOT move
    under ``env -C build`` -- mirroring ``clang.py``'s identical fix
    (round 30 Finding 2 / round 31 Finding 1) for the exact same
    structured-path-provenance ambiguity."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=["env", "-C", "build", "gcc", "-I/work/include", "-c", "x.c"],
        include_paths=["/work/include"],
        include_paths_explicit=[True],
    )
    cmd = build_castxml_command(cu, Path("/work/x.c"), Path("/work/out.xml"))
    assert cmd[cmd.index("-I") + 1] == "/work/include"


def test_build_castxml_command_same_string_twice_resolves_independently() -> None:
    """Round 31 Finding 1's per-occurrence provenance also protects the
    castxml backend: two ``include_paths`` entries normalizing to the
    identical string, one relative-then-joined and one genuinely
    explicit, resolve independently rather than collapsing."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=[
            "env",
            "-C",
            "build",
            "gcc",
            "-Iinclude",
            "-I/work/include",
            "-c",
            "x.c",
        ],
        include_paths=["/work/include", "/work/include"],
        include_paths_explicit=[False, True],
    )
    cmd = build_castxml_command(cu, Path("/work/x.c"), Path("/work/out.xml"))
    idxs = [i for i, tok in enumerate(cmd) if tok == "-I"]
    values = [cmd[i + 1] for i in idxs]
    assert values == ["/work/build/include", "/work/include"]


def test_build_castxml_command_negative_control_no_env_chdir_unaffected() -> None:
    """Negative control: with NO ``env -C`` prefix at all, the structured
    include path is emitted completely unchanged."""
    cu = CompileUnit(
        id="cu://x.cc",
        source="x.c",
        language="C",
        directory="/work",
        output="x.o",
        argv=["gcc", "-Iinclude", "-c", "x.c"],
        include_paths=["/work/include"],
    )
    cmd = build_castxml_command(cu, Path("/work/x.c"), Path("/work/out.xml"))
    assert cmd[cmd.index("-I") + 1] == "/work/include"
