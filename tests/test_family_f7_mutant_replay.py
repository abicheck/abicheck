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

"""H7 -- historical-bug mutants replayed as real source patches (family F7).

Plan: ``docs/contribute/plans/defect-family-harnesses.md`` section H7. A
harness that stops killing its seed mutants is broken, whatever its pass
rate. Each mutant in ``tests/_family_f7_catalog.py``'s ``MUTANTS`` is a
unified diff under ``tests/regressions/mutants/<family>/`` putting one
historical bug back into ``abicheck/``.

Default lane (fast): every patch still applies to the current tree (a
refactor that makes one stale fails here and the patch must be refreshed),
the catalogue and the patch directory agree, every family with a harness has
at least two mutants the harness kills, and every named harness node exists.

``slow`` lane: each mutant is applied to a private copy of the repository
and the named harness nodes are run there in a nested pytest session, which
must fail on at least one of them; an unpatched control copy must pass the
same nodes. A mutant the harness does not catch is a real harness gap: it is
recorded on the catalogue entry (``harness_gap``) and its replay is a strict
xfail, so closing the gap turns it into an XPASS that forces the entry update.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _family_f7_catalog as cat  # noqa: E402

_PARAMS = [pytest.param(m, id=m.name) for m in cat.MUTANTS]


def _patched_files(patch: Path) -> list[str]:
    return re.findall(
        r"^\+\+\+ b/(\S+)", patch.read_text(encoding="utf-8"), re.MULTILINE
    )


def _harness_functions(module: str) -> set[str]:
    tree = ast.parse((cat.REPO_ROOT / module).read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    }


# ── default lane ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mutant", _PARAMS)
def test_patch_applies_cleanly_to_current_tree(mutant: cat.Mutant) -> None:
    res = cat.apply_patch(cat.REPO_ROOT, mutant.patch_path, check_only=True)
    assert res.returncode == 0, (
        f"H7 mutant {mutant.name} is stale -- refresh {mutant.patch}: {res.stderr}"
    )


@pytest.mark.parametrize("mutant", _PARAMS)
def test_patch_is_not_already_applied(mutant: cat.Mutant) -> None:
    # A reverse-apply succeeding would mean the tree already carries the bug.
    res = cat.apply_patch(
        cat.REPO_ROOT, mutant.patch_path, check_only=True, reverse=True
    )
    assert res.returncode != 0, (
        f"{mutant.name}: the current tree already contains the mutant"
    )


@pytest.mark.parametrize("mutant", _PARAMS)
def test_patch_only_touches_product_source(mutant: cat.Mutant) -> None:
    files = _patched_files(mutant.patch_path)
    assert files, mutant.patch
    assert all(f.startswith("abicheck/") and f.endswith(".py") for f in files), files
    assert mutant.patch.startswith(f"{mutant.family}/"), mutant.patch


def test_catalogue_and_patch_directory_agree() -> None:
    on_disk = {
        p.relative_to(cat.MUTANTS_DIR).as_posix()
        for p in cat.MUTANTS_DIR.rglob("*.patch")
    }
    listed = [m.patch for m in cat.MUTANTS]
    assert len(listed) == len(set(listed))
    assert on_disk == set(listed)
    assert len(cat.MUTANTS_BY_NAME) == len(cat.MUTANTS)


@pytest.mark.parametrize("family", sorted(cat.HARNESSES))
def test_every_harnessed_family_has_two_killed_mutants(family: str) -> None:
    mutants = [m for m in cat.MUTANTS if m.family == family]
    killed = [m for m in mutants if not m.harness_gap]
    assert len(mutants) >= 2, family
    assert len(killed) >= 2, (family, [m.name for m in mutants])
    assert all(m.prs for m in mutants)


@pytest.mark.parametrize("mutant", _PARAMS)
def test_expected_failing_nodes_exist(mutant: cat.Mutant) -> None:
    assert mutant.must_fail
    harness = cat.HARNESSES[mutant.family]
    names = _harness_functions(harness)
    for node in mutant.must_fail:
        module, _, func = node.partition("::")
        assert module == harness, node
        assert func in names, f"{node} does not exist in {harness}"


@pytest.mark.parametrize("mutant", _PARAMS)
def test_gap_mutants_state_a_reason(mutant: cat.Mutant) -> None:
    assert mutant.harness_gap == "" or len(mutant.harness_gap) > 40
    assert mutant.summary


def test_failed_node_matching() -> None:
    node = "tests/x.py::test_a"
    assert _node_failed(node, ["tests/x.py::test_a[p-1]"])
    assert _node_failed(node, ["tests/x.py::test_a"])
    assert not _node_failed(node, ["tests/x.py::test_ab"])
    assert not _node_failed(node, [])


# ── slow lane: real replay ──────────────────────────────────────────────────


def _node_failed(node: str, failed: list[str]) -> bool:
    return any(f == node or f.startswith(node + "[") for f in failed)


def _replay(
    mutant: cat.Mutant | None, tmp_path: Path, nodes: tuple[str, ...]
) -> tuple[int, list[str], str]:
    root = cat.copy_repo(tmp_path / "repo")
    if mutant is not None:
        res = cat.apply_patch(root, mutant.patch_path, check_only=False)
        assert res.returncode == 0, res.stderr
    # Prove the nested session imports the copy, not the editable install.
    assert (
        cat.imported_abicheck_path(root)
        == (root / "abicheck" / "__init__.py").resolve()
    )
    return cat.run_nodes(root, nodes, tmp_path / "nested-basetemp")


def _slow_params() -> list[object]:
    out: list[object] = []
    for m in cat.MUTANTS:
        marks = [pytest.mark.slow]
        if m.harness_gap:
            marks.append(
                pytest.mark.xfail(
                    strict=True, raises=AssertionError, reason=m.harness_gap
                )
            )
        out.append(pytest.param(m, id=m.name, marks=marks))
    return out


@pytest.mark.parametrize("mutant", _slow_params())
def test_harness_kills_replayed_mutant(mutant: cat.Mutant, tmp_path: Path) -> None:
    rc, failed, output = _replay(mutant, tmp_path, mutant.must_fail)
    assert (
        "error during collection" not in output and "found no collectors" not in output
    ), output[-3000:]
    assert rc == 1, (
        f"{mutant.name}: harness passed with the mutant applied (rc={rc})\n{output[-3000:]}"
    )
    assert any(_node_failed(n, failed) for n in mutant.must_fail), (
        failed,
        output[-3000:],
    )


@pytest.mark.slow
def test_unpatched_control_passes_every_named_node(tmp_path: Path) -> None:
    nodes = tuple(sorted({n for m in cat.MUTANTS for n in m.must_fail}))
    rc, failed, output = _replay(None, tmp_path, nodes)
    assert rc == 0 and not failed, output[-3000:]
