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

"""Contract of the name-heuristic registration API and catalogue
(design-hardening Phase 5). The H4 harness tests the registry against the
code base; this module tests that a malformed registration is rejected at
construction and that every :func:`registry_problems` rule actually fires.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from abicheck.model.name_heuristics import (
    LazyFactInput,
    NameHeuristic,
    NameHeuristicEffect,
    SeverityRaisingNameHeuristic,
    StructuralFact,
)
from abicheck.policy.name_heuristics import (
    HEURISTIC_OWNER_MODULES,
    name_heuristic_registry,
    registry_problems,
)

_OWNER = "abicheck.compare.naming_conventions"
_FACT = StructuralFact("compare.probe.holds", bool)


def _lowering(**kw: Any) -> NameHeuristic:
    args: dict[str, Any] = {
        "effect": NameHeuristicEffect.LOWER_CONFIDENCE,
        "confirmed_by": None,
        "owner": _OWNER,
        "description": "probe",
        "matcher": bool,
        "helpers": (),
        "vocabularies": (),
        "patterns": (),
    }
    args.update(kw)
    return NameHeuristic("probe", **args)


def _raising(**kw: Any) -> SeverityRaisingNameHeuristic:
    args: dict[str, Any] = {
        "fact": _FACT,
        "raises": ("FUNC_REMOVED",),
        "owner": _OWNER,
        "description": "probe",
        "matcher": bool,
        "helpers": (),
        "vocabularies": (),
        "patterns": (),
    }
    args.update(kw)
    return SeverityRaisingNameHeuristic("probe_raise", **args)


# -- construction rejects a malformed registration ---------------------------


@pytest.mark.parametrize(
    ("kw", "error"),
    [
        ({"owner": "not_a_module"}, ValueError),
        ({"description": "  "}, ValueError),
        ({"matcher": "not callable"}, TypeError),
        ({"helpers": ("not callable",)}, TypeError),
        ({"patterns": ("raw string",)}, TypeError),
        ({"confirmed_by": bool}, TypeError),
    ],
)
def test_lowering_construction_rejects(
    kw: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        _lowering(**kw)


@pytest.mark.parametrize("bad_id", ["Probe", "probe-id", "1probe", ""])
def test_heuristic_id_must_be_snake_case(bad_id: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        NameHeuristic(
            bad_id,
            effect=NameHeuristicEffect.LOWER_CONFIDENCE,
            confirmed_by=None,
            owner=_OWNER,
            description="probe",
            matcher=bool,
            helpers=(),
            vocabularies=(),
            patterns=(),
        )


@pytest.mark.parametrize("raises", [(), ("",), (1,)])
def test_raising_must_name_its_kinds(raises: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="ChangeKind"):
        _raising(raises=raises)


def test_structural_fact_check_must_be_callable() -> None:
    with pytest.raises(TypeError, match="callable"):
        StructuralFact("compare.probe.holds", "not callable")  # type: ignore[arg-type]
    assert repr(_FACT) == "StructuralFact('compare.probe.holds')"


def test_lowering_handle_queries() -> None:
    h = _lowering(matcher=lambda s, **kw: s.upper() if s else "")
    assert h.matches("x") and not h.matches("")
    assert h.apply("ab") == "AB"
    assert h.effect_name == "lower_confidence"
    assert "probe" in repr(h) and _OWNER in repr(h)
    with pytest.raises(TypeError, match="no confirming fact"):
        h.confirmed("x", True)
    confirmed = _lowering(matcher=bool, confirmed_by=_FACT)
    # Exhaustive truth table: name AND fact.
    for name in ("x", ""):
        for fact in (True, False):
            assert confirmed.confirmed(name, fact) is (bool(name) and fact)


def test_raising_handle_reports_its_effect_and_reads_a_lazy_input_once() -> None:
    h = _raising()
    assert h.effect_name == "raise_severity"
    calls: list[int] = []
    lazy = LazyFactInput(lambda: calls.append(1) or True)
    assert h.confirmed("x", lazy) is True
    assert lazy.value() is True
    assert calls == [1]


# -- every catalogue rule fires ---------------------------------------------


def _real() -> dict[str, Any]:
    return dict(name_heuristic_registry())


def test_the_real_catalogue_is_clean() -> None:
    assert registry_problems() == []


def test_problem_registered_under_a_different_id() -> None:
    reg = _real()
    reg["alias"] = reg["enum_sentinel"]
    assert any("registered under a different id" in p for p in registry_problems(reg))


def test_problem_unlisted_owner() -> None:
    reg = _real()
    reg["probe"] = _lowering(owner="abicheck.somewhere_else")
    assert any("not in HEURISTIC_OWNER_MODULES" in p for p in registry_problems(reg))


def test_problem_listed_owner_registers_nothing() -> None:
    reg = {k: h for k, h in _real().items() if h.owner != HEURISTIC_OWNER_MODULES[0]}
    assert any("registers nothing" in p for p in registry_problems(reg))


def test_problem_vocabulary_is_not_a_string_collection() -> None:
    reg = _real()
    reg["probe"] = _lowering(vocabularies=("re",))  # a module attribute, not strings
    assert any("not a string collection" in p for p in registry_problems(reg))


def test_problem_pattern_not_compiled() -> None:
    reg = _real()
    h = _lowering(patterns=(re.compile("x"),))
    object.__setattr__(h, "patterns", ("x",))
    reg["probe"] = h
    assert any("pattern is not compiled" in p for p in registry_problems(reg))


@pytest.mark.parametrize("make", [_lowering, _raising])
def test_problem_unknown_change_kind(make: Any) -> None:
    reg = _real()
    h = make(
        **(
            {"lowers_from": ("NOT_A_KIND",)}
            if make is _lowering
            else {"raises": ("NOT_A_KIND",)}
        )
    )
    reg[h.id] = h
    assert any("unknown ChangeKind" in p for p in registry_problems(reg))


def test_problem_raising_fact_not_callable() -> None:
    reg = _real()
    h = _raising()
    object.__setattr__(h, "fact", SimpleNamespace(check=None))
    reg[h.id] = h
    assert any("fact is not callable" in p for p in registry_problems(reg))


def test_problem_lowering_effect_outside_the_vocabulary() -> None:
    reg = _real()
    h = _lowering()
    object.__setattr__(h, "effect", SimpleNamespace(value="raise_severity"))
    reg["probe"] = h
    assert any("neither lowering nor review" in p for p in registry_problems(reg))
