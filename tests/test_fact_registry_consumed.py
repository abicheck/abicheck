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

"""ADR-063 5B: a ``CONSUMED`` fact names detectors that really read it.

Two halves: ``FactDefinition`` refuses a lifecycle/``consumed_by`` mismatch,
and direction 9 of ``scripts/fact_registry_completeness.py`` resolves every
named detector by AST. The oracle for the second half is independent of the
checker: each production consumer's source is scanned here with a plain
substring test, and the checker is also driven with deliberately wrong
fields/targets (negative controls) so a checker that accepted anything
would fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.model.fact_registry import FACT_REGISTRY
from abicheck.model.fact_registry_schema import (
    LIFECYCLE_ORDER,
    FactDefinition,
    FactLifecycle,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.fact_registry_completeness import consumer_problems  # noqa: E402

_CONSUMED = [e for e in FACT_REGISTRY.entries.values() if e.consumed_by]


def _entry(lifecycle: FactLifecycle, consumed_by: tuple[str, ...]) -> FactDefinition:
    return FactDefinition(
        owner="RecordType",
        field="bases",
        value_type="list[str]",
        producing_backends=("castxml",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=lifecycle,
        consumed_by=consumed_by,
    )


@pytest.mark.parametrize("lifecycle", LIFECYCLE_ORDER)
@pytest.mark.parametrize("named", [False, True])
def test_consumed_by_required_exactly_from_consumed_upward(
    lifecycle: FactLifecycle, named: bool
) -> None:
    consumed = LIFECYCLE_ORDER.index(lifecycle) >= LIFECYCLE_ORDER.index(
        FactLifecycle.CONSUMED
    )
    targets = ("abicheck.compare.base_class_diff:diff_bases",) if named else ()
    if consumed == named:
        assert _entry(lifecycle, targets).consumed_by == targets
    else:
        with pytest.raises(ValueError, match="consumed_by"):
            _entry(lifecycle, targets)


def test_the_five_named_5b_families_are_consumed() -> None:
    ids = {e.id for e in _CONSUMED}
    assert {
        "RecordType.bases",
        "RecordType.virtual_bases",
        "RecordType.vtable",
        "RecordType.vptr_offset_bits",
        "Param.is_va_list",
    } <= ids


@pytest.mark.parametrize("entry", _CONSUMED, ids=lambda e: e.id)
def test_every_production_consumer_resolves(entry: FactDefinition) -> None:
    assert consumer_problems(entry.field, entry.consumed_by, _REPO_ROOT) == []
    for target in entry.consumed_by:
        module, _, func = target.partition(":")
        text = (_REPO_ROOT / (module.replace(".", "/") + ".py")).read_text()
        assert f"def {func}(" in text
        assert f"{entry.field}_fact" in text or f'"{entry.field}"' in text


@pytest.mark.parametrize("entry", _CONSUMED, ids=lambda e: e.id)
def test_a_consumer_that_does_not_read_the_field_is_rejected(
    entry: FactDefinition,
) -> None:
    problems = consumer_problems("no_such_field", entry.consumed_by, _REPO_ROOT)
    assert len(problems) == len(entry.consumed_by)
    assert all("never reads no_such_field_fact" in p for p in problems)


@pytest.mark.parametrize(
    ("target", "needle"),
    [
        ("abicheck.compare.no_such_module:diff_bases", "does not exist"),
        ("abicheck.compare.base_class_diff:no_such_function", "no function"),
        ("abicheck.compare.base_class_diff", "is not 'module:function'"),
        ("abicheck.diff_symbols:_diff_func_deprecated", "never reads bases_fact"),
    ],
)
def test_broken_targets_are_rejected(target: str, needle: str) -> None:
    problems = consumer_problems("bases", (target,), _REPO_ROOT)
    assert len(problems) == 1
    assert needle in problems[0]


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        # Only a same-named method remains: not the module-level function.
        (
            "class C:\n    def diff_x(self, a, b):\n"
            "        return compare_facts(a.bases_fact, b.bases_fact, [])\n",
            "no function",
        ),
        # Only a nested function remains.
        (
            "def outer():\n    def diff_x(a, b):\n"
            "        return compare_facts(a.bases_fact, b.bases_fact, [])\n",
            "no function",
        ),
        # A value-only read never branches on availability.
        (
            "def diff_x(a, b):\n    return a.bases_fact.value != b.bases_fact.value\n",
            "never branches on its availability",
        ),
        # Status-aware readers are accepted.
        (
            "def diff_x(a, b):\n    return compare_facts(a.bases_fact, b.bases_fact, [])\n",
            None,
        ),
        ("def diff_x(a):\n    return a.bases_fact.status\n", None),
    ],
)
def test_consumer_shape_rules(tmp_path: Path, source: str, needle: str | None) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text(source)
    problems = consumer_problems("bases", ("pkg.mod:diff_x",), tmp_path)
    if needle is None:
        assert problems == []
    else:
        assert len(problems) == 1 and needle in problems[0]
