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

"""Every scalar ``compare`` route classifies through one implementation.

``cli_compare_helpers.run_compare`` used to carry its own copy of the whole
classification phase (depth projection, evidence fold, ``compare_snapshots``,
the evidence-contract axes, operand metadata, requested depth, assurance,
evidence depths) beside ``workflows.compare.execute.classify_compare_pair``.
Each route divergence the F2 harness found was then fixed twice, once per
copy. ``workflows.compare_classify.classify_resolved_pair`` is now the only
implementation, and this module pins that through the public workflow:

* **One owner, every route** -- the native CLI, the typed ``CompareRequest``
  API and a one-member directory release each classify a pair through exactly
  one ``classify_resolved_pair`` call, and no route reaches
  ``compare_snapshots`` outside it (a second classification path, the
  regression this guards, would show up as a call outside the core).
* **Same settings in, same classification out** -- for every F2 setting axis
  the CLI and the typed API hand the core the same classification settings
  and the same operands, and the core's answer -- verdict and change set --
  is identical. The oracle is the request fields and the result, compared
  across routes, never the core's own helpers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import abicheck.workflows.compare_classify as classify_mod
import abicheck.workflows.compare_policy as compare_policy_mod

from _family_f2_routes import (  # isort: skip
    AXES,
    AXES_BY_NAME,
    CORPUS,
    ROUTES,
    Axis,
    write_operands,
)

#: The ``CompareRequest`` fields that decide how a resolved pair is
#: classified. Resolution-only fields (headers, debug roots, ...) and the
#: operand path spelling are deliberately absent.
CLASSIFICATION_FIELDS = (
    "policy",
    "scope_public",
    "force_public_symbols",
    "public_surface_allowlist",
    "pattern_verdicts",
    "collapse_versioned_symbols",
    "env_matrix",
    "diagnostic_comparison",
    "contract_evaluation",
    "depth",
    "lang",
    "abi3_floor",
    "strict_suppressions",
    "require_justification",
)


@dataclasses.dataclass
class _Spy:
    """Records every core call and every ``compare_snapshots`` call, and
    whether the latter happened inside the former."""

    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    compare_calls_outside_core: int = 0
    compare_calls_inside_core: int = 0
    _depth: int = 0


def _install_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy()
    real_classify = classify_mod.classify_resolved_pair
    real_compare = compare_policy_mod.compare_snapshots

    def _classify(request: Any, pair: Any, policy: Any, **kw: Any) -> Any:
        spy._depth += 1
        try:
            classified = real_classify(request, pair, policy, **kw)
        except BaseException:
            spy.calls.append({"request": request, "kw": kw, "classified": None})
            raise
        finally:
            spy._depth -= 1
        spy.calls.append({"request": request, "kw": kw, "classified": classified})
        return classified

    def _compare(*args: Any, **kwargs: Any) -> Any:
        if spy._depth:
            spy.compare_calls_inside_core += 1
        else:
            spy.compare_calls_outside_core += 1
        return real_compare(*args, **kwargs)

    monkeypatch.setattr(classify_mod, "classify_resolved_pair", _classify)
    monkeypatch.setattr(compare_policy_mod, "compare_snapshots", _compare)
    return spy


def _run(
    route: str, case: str, axis: Axis, tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> _Spy:
    spy = _install_spy(monkeypatch)
    ops = write_operands(tmp, case)
    run: Callable[..., Any] = ROUTES[route]
    run(ops, axis, tmp)
    monkeypatch.undo()
    return spy


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("case", sorted(CORPUS))
def test_every_route_classifies_through_the_one_core(
    route: str, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _run(route, case, AXES_BY_NAME["default"], tmp_path, monkeypatch)
    assert len(spy.calls) == 1, f"{route}/{case}: {len(spy.calls)} core calls"
    assert spy.compare_calls_outside_core == 0, (
        f"{route}/{case} classified outside workflows.compare_classify"
    )
    assert spy.compare_calls_inside_core == 1


def _classification_settings(request: Any) -> dict[str, Any]:
    settings = {f: getattr(request, f) for f in CLASSIFICATION_FIELDS}
    # The same file reached by both routes; compare it by content, not spelling.
    for f in ("old_probe_matrix", "new_probe_matrix", "suppress"):
        p = getattr(request, f)
        settings[f] = None if p is None else Path(p).read_bytes()
    return settings


def _outcome(call: dict[str, Any]) -> Any:
    classified = call["classified"]
    if classified is None:
        return "raised"
    result = classified.result
    return (
        result.verdict,
        sorted((c.kind.value, c.symbol) for c in result.changes),
        sorted((c.kind.value, c.symbol) for c in result.suppressed_changes),
        result.requested_depth,
        result.evidence_contract_error,
    )


def _operands(call: dict[str, Any]) -> Any:
    operands = call["kw"].get("operands")
    if operands is None:
        operands = classify_mod.ComparedOperands.from_request(call["request"])
    return (
        operands.old_path.resolve(),
        operands.new_path.resolve(),
        operands.old_live,
        operands.new_live,
    )


@pytest.mark.parametrize(
    "axis",
    [
        a.name
        for a in AXES
        if not ({"cli", "api"} & set(a.unsupported))
        and not ({"cli", "api"} & set(a.reported_elsewhere))
    ],
)
@pytest.mark.parametrize("case", ["removal_and_addition", "variable_removed"])
def test_cli_and_typed_api_hand_the_core_the_same_classification(
    axis: str, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spies = {
        route: _run(route, case, AXES_BY_NAME[axis], tmp_path / route, monkeypatch)
        for route in ("cli", "api")
    }
    (cli_call,), (api_call,) = spies["cli"].calls, spies["api"].calls
    assert _classification_settings(cli_call["request"]) == _classification_settings(
        api_call["request"]
    )
    assert _outcome(cli_call) == _outcome(api_call)
    # Operand identity differs only by the tmp directory each route ran in.
    cli_ops, api_ops = _operands(cli_call), _operands(api_call)
    assert [p.name for p in cli_ops[:2]] == [p.name for p in api_ops[:2]]
    assert cli_ops[2:] == api_ops[2:]


def test_the_outcome_oracle_is_not_vacuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different corpus pairs must give two different core outcomes, or
    the parity cell above compares two constants."""
    outs = [
        _outcome(
            _run(
                "cli", case, AXES_BY_NAME["default"], tmp_path / case, monkeypatch
            ).calls[0]
        )
        for case in ("identical", "removal_and_addition")
    ]
    assert outs[0] != outs[1]


def test_out_of_band_packs_attach_to_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ``--build-info``/``--sources`` pack rides on a *copy* of
    its side, before projection, and a side with neither flag is untouched
    -- the resolved snapshot itself is never mutated."""
    import abicheck.cli_buildsource_helpers as helpers
    from abicheck.model import AbiSnapshot
    from tests._fake_compare_resolution import fake_resolved_pair

    sentinel = object()
    seen: list[tuple[Any, Any, Any]] = []

    def _fake_side_pack(build_info: Any, sources: Any, snap: Any) -> Any:
        seen.append((build_info, sources, snap))
        return sentinel

    monkeypatch.setattr(helpers, "_resolve_side_pack", _fake_side_pack)
    old = AbiSnapshot(library="libfoo.so", version="1")
    new = AbiSnapshot(library="libfoo.so", version="2")
    _, pair = fake_resolved_pair(old, new)

    out = helpers._with_out_of_band_packs(pair, None, None, Path("bi"), None)

    assert out.old is old  # no flag: the resolved side itself
    assert out.new is not new and out.new.build_source is sentinel
    assert new.build_source is None  # the resolved snapshot is not mutated
    assert seen == [(Path("bi"), None, new)]  # packs seeded from the unprojected side
