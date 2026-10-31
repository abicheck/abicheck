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

"""The compile context does not depend on what the old side is (ADR-091 D4).

The overlay that used to classify a ``--sources`` tree's ``compile:`` block as
pair-wide or single-sided, keyed on whether the old side was a live binary or
a stored snapshot, is gone. The compile context now reaches the CLI as
``--set compile.*`` pairs, and the CLI owns how a stored snapshot and a live
binary are compared. The action's obligation is therefore that the same
inputs forward the same pairs whichever kind the old side is, and that an
audit-only run (no old side) still forwards them. Each case is compared with
the same inputs under the other operand kind, never against a constant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_action_compile_set_forwarding import _run
from test_action_run_sh_set_input import _set_pairs

_CONTEXT = {"INPUT_COMPILER": "/usr/bin/g++", "INPUT_SYSROOT": "/opt/sysroot"}


def _compile_pairs(argv: list[str]) -> list[str]:
    return [value for value in _set_pairs(argv) if value.startswith("compile.")]


def _old_operand(tmp_path: Path, kind: str) -> str:
    """A live binary (``.so``) or a stored snapshot (``.json``) on disk."""
    name = "old.so" if kind == "binary" else "old.json"
    path = tmp_path / name
    path.write_text("{}" if kind != "binary" else "", encoding="utf-8")
    return str(path)


def _fresh(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("kind", ["binary", "snapshot"])
def test_compile_pairs_do_not_depend_on_the_old_operand_kind(
    tmp_path, kind: str
) -> None:
    """Whichever kind the old side is, the same inputs forward the same pairs."""
    baseline = _compile_pairs(
        _run(_fresh(tmp_path, "reference"), "compare", **_CONTEXT)
    )
    argv = _run(
        _fresh(tmp_path, kind),
        "compare",
        INPUT_OLD_LIBRARY=_old_operand(tmp_path, kind),
        **_CONTEXT,
    )
    assert _compile_pairs(argv) == baseline
    assert 'compile.compiler="/usr/bin/g++"' in _compile_pairs(argv)
    assert 'compile.sysroot="/opt/sysroot"' in _compile_pairs(argv)


@pytest.mark.parametrize("kind", ["binary", "snapshot"])
def test_set_line_for_the_compile_context_is_not_dropped_by_either_kind(
    tmp_path, kind: str
) -> None:
    argv = _run(
        _fresh(tmp_path, kind + "-set"),
        "compare",
        INPUT_OLD_LIBRARY=_old_operand(tmp_path, kind),
        INPUT_SET="scope.show_redundant=true\n",
        **_CONTEXT,
    )
    assert "scope.show_redundant=true" in _set_pairs(argv)
    assert 'compile.compiler="/usr/bin/g++"' in _compile_pairs(argv)


def test_audit_only_compare_still_forwards_the_compile_context(tmp_path) -> None:
    """With no old side the run is the audit-only shape (``--no-baseline``);
    the compile context still reaches that single audited artifact."""
    argv = _run(tmp_path, "compare", INPUT_OLD_LIBRARY="", **_CONTEXT)
    assert "--no-baseline" in argv
    assert 'compile.compiler="/usr/bin/g++"' in _compile_pairs(argv)
