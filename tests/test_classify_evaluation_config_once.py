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

"""One Semantic Pipeline sub-phase 4B: ``classify_compare_pair`` resolves the
ADR-049 D7 ``CompatibilityEvaluationConfig`` exactly once and every consumer
reads that one object.

Before this slice the pair's ``ResolvedExecutionContext.evaluation_config``
was always ``None`` on a real run, and the gate receipt re-derived its own
copy from the request. The contract stated here, across a spread of request
shapes (severity presets, contract modes, scoping, pack forwarding):

* the resolver runs once per classification;
* ``CompareResult.resolved_execution_context.evaluation_config`` *is* the
  object ``DiffResult.evaluation_config`` carries (identity, not equality --
  two equal copies would still be two representations);
* that object equals what an **independent** call to the public resolver
  produces for the same request and loaded inputs, so the single resolution
  did not drop or alter any namespace or provenance entry the old
  re-derivation would have produced.
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot
from abicheck.service import CompareRequest, InputSpec


def _request(tmp_path, **kwargs) -> CompareRequest:
    from abicheck.serialization import snapshot_to_json

    old_p = tmp_path / "old.abi.json"
    new_p = tmp_path / "new.abi.json"
    old_p.write_text(
        snapshot_to_json(AbiSnapshot(library="libt.so", version="1")), encoding="utf-8"
    )
    new_p.write_text(
        snapshot_to_json(AbiSnapshot(library="libt.so", version="2")), encoding="utf-8"
    )
    return CompareRequest(
        old=InputSpec.of(str(old_p)), new=InputSpec.of(str(new_p)), **kwargs
    )


def _pack_overrides():
    from abicheck.model.change_catalog.kinds import ChangeKind
    from abicheck.model.change_catalog.registry import Verdict

    return ((ChangeKind.FUNC_REMOVED, Verdict.API_BREAK),)


VARIANTS = {
    "defaults": {},
    "severity-default": {"severity_preset": "default"},
    "severity-strict": {"severity_preset": "strict"},
    "no-scope": {"scope_public": False},
    "contract-exports": {"contract_evaluation": True, "contract_mode": "exports"},
    "contract-public-strict": {
        "contract_evaluation": True,
        "contract_mode": "public",
        "severity_preset": "strict",
    },
    "pack-forwarded": {"pack_policy_overrides": "PACK"},
    "pack-namespaces": {"pack_internal_namespaces": ("detail",)},
}


def _kwargs(spec: dict) -> dict:
    return {k: (_pack_overrides() if v == "PACK" else v) for k, v in spec.items()}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_one_resolution_read_by_every_consumer(tmp_path, monkeypatch, name):
    import abicheck.compatibility_evaluation_frontend as frontend
    from abicheck.service_compare_pipeline import (
        classify_compare_pair,
        resolve_compare_request,
    )
    from abicheck.workflows.compare_gate_receipt import (
        resolve_request_evaluation_config,
    )
    from abicheck.workflows.compare_policy import load_suppression_and_policy

    request = _request(tmp_path, **_kwargs(VARIANTS[name]))
    pair = resolve_compare_request(request)
    assert pair.resolved_execution_context.evaluation_config is None

    real = frontend.compatibility_config_from_compare_request
    calls: list[object] = []

    def _counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(
        frontend, "compatibility_config_from_compare_request", _counting
    )
    result = classify_compare_pair(request, pair)
    monkeypatch.setattr(frontend, "compatibility_config_from_compare_request", real)

    assert len(calls) == 1, f"{name}: resolved {len(calls)} times"
    context_config = result.resolved_execution_context.evaluation_config
    # F2 route parity: the diff carries the config only under contract
    # evaluation or forwarded packs; otherwise it stays on the baseline tier.
    stamped = (
        result.diff.contract_context is not None
        or bool(request.pack_policy_overrides)
        or request.pack_internal_namespaces is not None
    )
    assert result.diff.evaluation_config is (context_config if stamped else None)

    # Independent oracle: the public resolver over the same loaded inputs
    # (pack folding reproduced through the same public helper the CLI uses).
    suppression, pf = load_suppression_and_policy(
        request.suppress, request.policy, request.policy_file_path
    )
    if request.pack_policy_overrides or request.pack_internal_namespaces is not None:
        from abicheck.pack_application import PackApplication, policy_file_with_packs

        pf = policy_file_with_packs(
            pf,
            PackApplication(
                policy_overrides=dict(request.pack_policy_overrides or {}),
                internal_namespaces=request.pack_internal_namespaces,
            ),
            base_policy=request.policy,
        )
    expected = resolve_request_evaluation_config(request, pf, suppression)
    assert context_config == expected, name
    assert dict(context_config.provenance) == dict(expected.provenance), name


def test_the_variants_resolve_to_different_configs(tmp_path):
    """Vacuity guard on the oracle: if every variant resolved to the same
    config, equality with the oracle would hold for a resolver that ignored
    the request entirely."""
    from abicheck.service_compare_pipeline import (
        classify_compare_pair,
        resolve_compare_request,
    )

    seen = []
    for i, name in enumerate(sorted(VARIANTS)):
        sub = tmp_path / str(i)
        sub.mkdir()
        request = _request(sub, **_kwargs(VARIANTS[name]))
        cfg = classify_compare_pair(
            request, resolve_compare_request(request)
        ).resolved_execution_context.evaluation_config
        if all(cfg != other for other in seen):
            seen.append(cfg)
    assert len(seen) >= 5, len(seen)


def test_a_hand_built_pair_without_context_gets_none(tmp_path):
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_compare_pipeline import (
        ResolvedComparePair,
        classify_compare_pair,
    )

    request = _request(tmp_path)
    ev = SideEvidence(headers=[], compile=None, collect_mode="off", dump_manifest=None)
    snap = AbiSnapshot(library="libt.so", version="1")
    pair = ResolvedComparePair(
        old=snap,
        new=snap,
        old_fmt="elf",
        new_fmt="elf",
        old_evidence=ev,
        new_evidence=ev,
    )
    result = classify_compare_pair(request, pair)
    assert result.resolved_execution_context is None
    # A plain request stays on the baseline digest tier (F2 route parity).
    assert result.diff.evaluation_config is None


def test_returned_context_reports_the_depth_classification_used(tmp_path):
    """A pair resolved under one depth and classified under another must not
    return a context -- or a resolution digest -- for the depth it never used
    (CodeRabbit review, PR #1415)."""
    import dataclasses

    from abicheck.service_compare_pipeline import (
        classify_compare_pair,
        resolve_compare_request,
    )

    resolved_under = _request(tmp_path, depth="binary")
    pair = resolve_compare_request(resolved_under)
    classified_under = dataclasses.replace(resolved_under, depth=None)
    result = classify_compare_pair(classified_under, pair)
    ctx = result.resolved_execution_context
    assert ctx.requested_depth == result.diff.requested_depth
    same = classify_compare_pair(resolved_under, pair).resolved_execution_context
    assert same.requested_depth == "binary"
    assert ctx.requested_depth != same.requested_depth
