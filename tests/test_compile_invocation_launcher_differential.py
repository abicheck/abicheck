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
"""Launcher and ``env``-prefix rows for the compile-invocation differential.

``test_compile_invocation_differential.py`` pins the general corpus; this file
pins the launcher cases the plan's Phase 3 slice 3 moved into
:mod:`~abicheck.buildsource.source_extractors.compile_invocation`: ccache
(with a ``KEY=VALUE`` config override), distcc, sccache (MSVC driver), chained
launchers, nested ``env -C`` and ``env -i`` prefixes. The golden was recorded
from ``origin/main`` before the move, so a byte-identical render proves the
move changed no replayed argv.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_compile_invocation_differential import _render_unit

from abicheck.buildsource.adapters.compile_db import CompileDbAdapter

_DATA = Path(__file__).parent / "data" / "compile_invocation"
_CORPUS = _DATA / "launcher_compile_commands.json"
_GOLDEN = _DATA / "launcher_clang_argv_golden.json"


def render_launcher_corpus() -> dict[str, dict[str, object]]:
    """Render every launcher compile unit, keyed by its ``source`` spelling."""
    evidence = CompileDbAdapter(_CORPUS).collect()
    return {cu.source: _render_unit(cu) for cu in evidence.compile_units}


def test_launcher_corpus_matches_pre_change_golden() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert render_launcher_corpus() == golden


def test_launcher_rows_drop_every_launcher_token() -> None:
    rows = render_launcher_corpus()
    assert len(rows) == 7
    for source, row in rows.items():
        for key, argv in row.items():
            if key.startswith("ast[") or key.startswith("macro["):
                assert not {"ccache", "distcc", "sccache", "env"} & set(argv), (
                    source,
                    key,
                )


def test_nested_env_chdir_composes_onto_the_recorded_directory() -> None:
    row = render_launcher_corpus()["/work/src/env_nested.cpp"]
    assert row["run_directory"] == "/work/rel/build/sub"
