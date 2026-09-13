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

"""A detector that compares two sides may not select its populations with a
predicate that depends on contract evidence.

`in_public_surface` and `is_abi_visible` both answer partly from
`in_public_contract`, which is only *established* when that side's run was
given a public-header set. A detector that filters OLD and NEW independently
with either one therefore sees a different population on each side whenever
the two captures were configured differently -- and reads the difference as
a change in the library. That produced a false `func_visibility_changed`, a
false `internal_template_leaks_via_public_api`, a false
`mandatory_template_param_added`, a false `removed_const_overload`, a false
`sycl_overload_set_removed`, a false `tag_type_renamed`, and -- in the other
direction -- silently lost a real `char8t_migration`, `integer_model_changed`
and `abi_tag_changed`.

Each of those was found and fixed one at a time, across eight review rounds.
This test exists because that is the wrong shape of defence: the fix for the
*class* is that a new pairwise detector cannot quietly join raw surfaces
again. `abicheck.compare.template_surface` owns the reconciled counterparts;
a genuinely new exception is added to `_ALLOWED` with its reason, which is a
review decision rather than an edit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "abicheck"

#: The predicates that read `in_public_contract`, directly or through the
#: union -- i.e. the ones whose answer differs between two differently
#: configured captures of the same library.
EVIDENCE_DEPENDENT = frozenset({"in_public_surface", "is_abi_visible"})

#: Reviewed exceptions: a pairwise population selector that may still read
#: raw surfaces, with the reason it is not an instance of this class.
#:
#: Empty, and that is the claim: every such detector in the tree routes
#: through a reconciled surface today. An entry here is a review decision
#: about a specific function, not a way to quiet this test -- and the
#: companion test below rejects one that has gone stale, since an allowance
#: naming a deleted function would silently cover the next function to take
#: that name.
_ALLOWED: dict[tuple[str, str], str] = {}


#: The snapshot collections a detector filters to build a population. The
#: class is about *populations*: a helper that takes two declarations and
#: answers a question about the pair (`deleted_declaration_is_public`, which
#: ORs both sides deliberately) selects nothing and cannot be an instance.
_POPULATIONS = frozenset({"functions", "variables", "types", "enums"})


def _pairwise_functions() -> list[tuple[Path, ast.FunctionDef]]:
    """Every function that reads a population from *both* sides of a
    comparison -- the shape that can disagree about who is in the surface."""
    out: list[tuple[Path, ast.FunctionDef]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            args = [a.arg for a in node.args.args] + [
                a.arg for a in node.args.kwonlyargs
            ]
            if "old" not in args or "new" not in args:
                continue
            read = {
                (sub.value.id, sub.attr)
                for sub in ast.walk(node)
                if isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.attr in _POPULATIONS
            }
            if any(side == "old" for side, _ in read) and any(
                side == "new" for side, _ in read
            ):
                out.append((path, node))
    return out


def _raw_selector_calls(node: ast.FunctionDef) -> set[str]:
    """The evidence-dependent predicates *this* function calls itself.

    Nested functions count: a local `_by_stem(snap)` helper filtering inside
    a pairwise detector is exactly the shape every one of these defects had.
    """
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in EVIDENCE_DEPENDENT
    }


def test_no_pairwise_detector_selects_on_unreconciled_evidence() -> None:
    offenders: list[str] = []
    for path, node in _pairwise_functions():
        used = _raw_selector_calls(node)
        if not used:
            continue
        key = (path.name, node.name)
        if key in _ALLOWED:
            continue
        offenders.append(
            f"{path.relative_to(REPO_ROOT)}::{node.name} calls "
            f"{sorted(used)} on both sides"
        )
    assert not offenders, (
        "these pairwise functions select their populations with an "
        "evidence-dependent predicate, so two differently configured "
        "captures of one library disagree about the population and the "
        "difference is reported as a change; route them through "
        "abicheck.compare.template_surface's reconciled counterparts, or "
        "add a reviewed entry to _ALLOWED saying why this one is not an "
        "instance:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("key", sorted(_ALLOWED))
def test_every_allowance_still_names_a_real_function(key: tuple[str, str]) -> None:
    """An allowance for a function that no longer exists is a stale exemption
    that would silently cover a future function of the same name."""
    filename, function = key
    matches = [
        node
        for path, node in _pairwise_functions()
        if path.name == filename and node.name == function
    ]
    assert matches, f"_ALLOWED names {filename}::{function}, which no longer exists"
