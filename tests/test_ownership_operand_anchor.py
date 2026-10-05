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

"""Recorded ownership rules are a function of the rules, not of disk location.

Bug class: a project config made the config's directory the anchor every
root was recorded against -- including each side's own ``-H`` roots -- so
``-H old=inst-2021.14/include -H new=inst-2021.15/include --config ci.yml``
recorded two different rules and warned "classified under different
ownership rules" (or, with ownership narrowing, refused the comparison),
even for a config that stated only ``compile.std``.

The property: two sides whose header trees have the same *relative* layout
record identical rules wherever each tree sits -- under the project root,
nested deeper, or outside it -- under every config shape (none, no roots,
dependency roots, private headers, config-stated public dirs); and two
sides whose layouts genuinely differ do not. The oracle is the generated
layout itself.
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.extract.ownership import (
    OWNER_TARGET,
    DeclarationSite,
    classify,
    resolve_ownership_rules,
)
from abicheck.extract.ownership_stamp import recorded_rules
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules
from abicheck.workflows.ownership_request import (
    ownership_request_from_config,
    with_target_roots,
)

_LAYOUTS = (
    ("include",),
    ("include", "gen/include"),
    ("include/oneapi", "include/oneapi/ccl"),
    ("api", "src/public", "third/x"),
)

_CONFIGS = {
    "none": None,
    "no_roots": SimpleNamespace(ownership=None, public_header_dirs=()),
    "dependency": SimpleNamespace(
        ownership=OwnershipRules(
            dependencies=(DependencyRoots("dep", ("deps/dep/include",)),)
        ),
        public_header_dirs=(),
    ),
    "private_headers": SimpleNamespace(
        ownership=OwnershipRules(private_headers=("*/detail/*",)),
        public_header_dirs=(),
    ),
    "config_public_dir": SimpleNamespace(
        ownership=None, public_header_dirs=("cfgpub",)
    ),
}


def _tree(base: Path, layout: tuple[str, ...]) -> list[Path]:
    roots = [base / rel for rel in layout]
    for r in roots:
        r.mkdir(parents=True, exist_ok=True)
    return roots


def _recorded(project: Path, config: str, roots: list[Path]) -> OwnershipRules:
    cfg = _CONFIGS[config]
    request = ownership_request_from_config(cfg, project) if cfg is not None else None
    return recorded_rules(with_target_roots(request, roots, ()))


def _bases(tmp_path: Path, rng: random.Random) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    (project / "deps" / "dep" / "include").mkdir(parents=True, exist_ok=True)
    (project / "cfgpub").mkdir(parents=True, exist_ok=True)
    spots = [
        project / "inst-2021.14",
        project / "inst-2021.15",
        project / "nested" / "deeper" / "v2",
        tmp_path / "outside" / "release",
        tmp_path / "elsewhere",
    ]
    a, b = rng.sample(spots, 2)
    return project, a, b


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("config", sorted(_CONFIGS))
def test_same_layout_records_the_same_rules_wherever_it_sits(
    tmp_path: Path, seed: int, config: str
) -> None:
    rng = random.Random(seed)
    layout = rng.choice(_LAYOUTS)
    project, base_a, base_b = _bases(tmp_path, rng)
    old = _recorded(project, config, _tree(base_a, layout))
    new = _recorded(project, config, _tree(base_b, layout))
    assert old == new, (base_a, base_b, layout)


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("config", sorted(_CONFIGS))
def test_different_layouts_still_record_different_rules(
    tmp_path: Path, seed: int, config: str
) -> None:
    """Discrimination: the anchor cannot collapse every layout to one rule."""
    rng = random.Random(seed)
    layout_a, layout_b = rng.sample([lay for lay in _LAYOUTS if len(lay) > 1], 2)
    project, base_a, base_b = _bases(tmp_path, rng)
    old = _recorded(project, config, _tree(base_a, layout_a))
    new = _recorded(project, config, _tree(base_b, layout_b))
    assert old != new


@pytest.mark.parametrize("config", sorted(_CONFIGS))
def test_classification_and_rule_ids_follow_the_recorded_form(
    tmp_path: Path, config: str
) -> None:
    """Matching runs on the absolute roots, labels on the recorded spelling:
    a header under either side's own tree is target-owned under one rule id."""
    project = tmp_path / "project"
    (project / "deps" / "dep" / "include").mkdir(parents=True)
    (project / "cfgpub").mkdir(parents=True)
    decisions = []
    for side in ("inst-2021.14", "inst-2021.15"):
        (root,) = _tree(project / side, ("include",))
        cfg = _CONFIGS[config]
        request = ownership_request_from_config(cfg, project) if cfg else None
        full = with_target_roots(request, [root], ())
        resolved = resolve_ownership_rules(
            full.rules, full.project_root or os.getcwd(), labels=recorded_rules(full)
        )
        decisions.append(
            classify(
                DeclarationSite(path=str(root / "ccl.hpp"), qualified_name="ccl::x"),
                resolved,
            )
        )
    assert decisions[0] == decisions[1]
    assert decisions[0].owner == OWNER_TARGET
    assert str(project) not in decisions[0].rule_id


def test_a_root_the_config_states_stays_project_relative(tmp_path: Path) -> None:
    """A ``-H`` naming the config's own public dir is the config's root."""
    project = tmp_path / "project"
    (project / "cfgpub").mkdir(parents=True)
    rules = _recorded(project, "config_public_dir", [project / "cfgpub"])
    assert rules.target_roots == ("cfgpub",)


def test_a_config_root_and_an_operand_root_never_share_a_label(tmp_path: Path) -> None:
    """The config states the project directory itself; the operand names one
    other tree. Both would read "." -- they must stay two rules."""
    project = tmp_path / "project"
    project.mkdir()
    cfg = SimpleNamespace(ownership=None, public_header_dirs=(".",))
    (operand,) = _tree(tmp_path / "elsewhere", ("include",))
    request = ownership_request_from_config(cfg, project)
    rules = recorded_rules(with_target_roots(request, [operand], ()))
    assert len(set(rules.target_roots)) == 2, rules.target_roots
    assert "." in rules.target_roots
