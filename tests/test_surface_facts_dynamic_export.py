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

"""``surface_facts.is_dynamically_exported`` over every fact x legacy-enum cell."""

from __future__ import annotations

import itertools

import pytest

from abicheck.model import Fact, Function, Visibility
from abicheck.model.surface_facts import is_dynamically_exported

_FACTS = [
    None,
    Fact.present(True),
    Fact.present(False),
    Fact.partial(True),
    Fact.not_collected(),
    Fact.failed("x"),
]


def _expected(fact: Fact[bool] | None, vis: Visibility, export_only: bool) -> bool:
    if fact is not None:  # a producer answered: confirmed yes only
        return fact.value is True and fact.status.value in ("present", "partial")
    return vis is Visibility.PUBLIC or (vis is Visibility.ELF_ONLY and export_only)


@pytest.mark.parametrize(
    ("fact", "vis", "export_only"),
    list(itertools.product(_FACTS, list(Visibility), [False, True])),
)
def test_matches_truth_table(
    fact: Fact[bool] | None, vis: Visibility, export_only: bool
) -> None:
    decl = Function(
        name="f",
        mangled="f",
        return_type="int",
        visibility=vis,
        binary_exported_fact=fact,
    )
    assert is_dynamically_exported(
        decl, export_table_only_snapshot=export_only
    ) is _expected(fact, vis, export_only)
