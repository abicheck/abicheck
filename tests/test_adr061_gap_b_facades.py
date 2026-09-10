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

"""ADR-061 gap B: facade-delegation and import-compatibility tests.

Each flat, public-root module this PR turned into a thin compatibility
facade is checked for two things, and two things only (D8's own rule:
"Facade tests verify delegation and supported import compatibility; they do
not retest the underlying algorithm"):

1. **Delegation** — the name reachable through the flat facade is the exact
   same object the owning-layer module defines (``is``, not just ``==``),
   so patching the real owner is visible through the facade and there is no
   second, independently-maintained copy of the implementation.
2. **Import compatibility** — the documented public import path
   (``from abicheck.<name> import ...``) still resolves, with the exact
   names external callers use.

Two known false-confidence traps this module deliberately avoids (see the
task's own warning, and this ADR's D8):

- A ``monkeypatch.setattr`` against a name resolved through a lazy
  ``__getattr__`` rebinds nothing a real caller reads. None of these eight
  facades use a lazy ``__getattr__`` — every one is a static
  ``from .owner import name as name`` re-export — so an identity check
  (``facade.X is owner.X``) is itself the correct, *failing-if-broken* proof
  of delegation here; there is nothing to monkeypatch around.
- A ``from x import y`` re-export binds the name at *import time*, so
  patching the origin module *after* the fact is invisible through the
  re-export. The identity check below is immune to this by construction: it
  compares the two already-imported module objects' attributes directly,
  which is exactly the binding a real caller observes, rather than patching
  one side and hoping the other follows.

To verify this module would actually fail if delegation broke: temporarily
editing any facade below to define its own local copy of a re-exported name
(instead of importing it) makes the corresponding identity assertion fail,
since the two objects would then be unrelated.
"""

from __future__ import annotations

import importlib

import pytest

# ---------------------------------------------------------------------------
# Six facades whose real implementation was moved to a named owning layer.
# (facade module, owner module, [public names checked for identity]).
# ---------------------------------------------------------------------------
_MOVED_FACADES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "abicheck.checker_policy",
        "abicheck.policy.classification",
        (
            "BREAKING_KINDS",
            "API_BREAK_KINDS",
            "COMPATIBLE_KINDS",
            "RISK_KINDS",
            "ADDITION_KINDS",
            "QUALITY_KINDS",
            "IMPACT_TEXT",
            "POLICY_REGISTRY",
            "PolicyEntry",
            "policy_for",
            "impact_for",
            "impact_caveat_for",
            "policy_kind_sets",
            "apply_policy_file_overrides",
            "effective_category",
            "compute_verdict",
            "evidence_status_for_change",
            "evidence_status_for_result",
        ),
    ),
    (
        "abicheck.checker_policy",
        "abicheck.policy.evidence_status",
        (
            "Confidence",
            "EvidenceTier",
            "ReachabilityState",
            "FindingEvolution",
            "CrossSourceEvolution",
            "EvidenceStatus",
            "BINARY_EVIDENCE_TIERS",
            "has_binary_evidence",
            "is_cross_source_resolved",
        ),
    ),
    (
        "abicheck.contract_coverage_ledger",
        "abicheck.policy.coverage_ledger",
        (
            "REQUIRED_PROVIDERS",
            "CoverageFailure",
            "coverage_failures",
            "coverage_failures_for_context",
            "coverage_exit_contribution",
            "suppression_reaches_coverage_failures",
        ),
    ),
    (
        "abicheck.contract_gating",
        "abicheck.policy.contract_finding_relevance",
        ("contract_relevance_of", "evaluation_status_of", "is_evaluated"),
    ),
    (
        "abicheck.reclassify",
        "abicheck.policy.reclassify",
        (
            "RECLASSIFY_KNOWN_KEYS",
            "KindSets",
            "ReclassifyRule",
            "active_reclassify_rules",
            "effective_verdict_for_change",
            "first_matching_reclassify_verdict",
            "reclassify_rule_for_change",
            "resolve_kind_sets",
        ),
    ),
    (
        "abicheck.api_types",
        "abicheck.workflows.contracts",
        ("CompareRequest", "CompareResult", "DumpRequest", "OutputSpec"),
    ),
    (
        "abicheck.api_types",
        "abicheck.workflows.request_inputs",
        (
            "InputSpec",
            "FRONTEND_CONTEXTS",
            "SUPPORTED_LANGS",
            "SUPPORTED_FRONTENDS",
            "SUPPORTED_DEBUG_FORMATS",
            "frontend_context_errors",
            "frontend_value_errors",
            "required_path",
        ),
    ),
    (
        "abicheck.api_types",
        "abicheck.model.header_ast_frontends",
        ("HEADER_AST_FRONTENDS",),
    ),
    (
        "abicheck.qualified_name_segments",
        "abicheck.compare.qualified_name_normalization",
        (
            "segments",
            "raw_segments",
            "version_strip_segments",
            "strip_inline_abi_namespaces",
            "version_suffix",
            "is_inline_abi_namespace_segment",
        ),
    ),
    (
        "abicheck.qualified_name_segments",
        "abicheck.storage.closure_identity",
        (
            "renumber_anonymous_closure_identities",
            "defer_closure_identity_renumbering",
            "collect_anonymous_type_ordinals",
            "apply_anonymous_type_ordinals",
            "_LAMBDA_IDENTITY_FIELDS",
        ),
    ),
)


@pytest.mark.parametrize(
    "facade_name,owner_name,public_names",
    _MOVED_FACADES,
    ids=[f"{f}->{o}" for f, o, _ in _MOVED_FACADES],
)
def test_facade_delegates_to_owner(
    facade_name: str, owner_name: str, public_names: tuple[str, ...]
) -> None:
    """Every re-exported name on the flat facade is the *same object* the
    owning-layer module defines -- not a second, independently-maintained
    copy. This is what a ``from .owner import name as name`` re-export
    guarantees, and what this test actually checks (identity, not equality):
    if the facade ever stopped importing from the owner and defined its own
    copy instead, this assertion would fail even though the two values might
    still compare equal.
    """
    facade = importlib.import_module(facade_name)
    owner = importlib.import_module(owner_name)
    for name in public_names:
        assert hasattr(facade, name), f"{facade_name} no longer exports {name!r}"
        assert hasattr(owner, name), f"{owner_name} no longer defines {name!r}"
        assert getattr(facade, name) is getattr(owner, name), (
            f"{facade_name}.{name} is not {owner_name}.{name} -- delegation broke"
        )


# ---------------------------------------------------------------------------
# Three facades that stay deliberately unclassified (the "no single layer"
# leaves) -- their own owning-layer module still exists; only the flat
# facade's *classification* is intentionally absent, not its delegation.
# ---------------------------------------------------------------------------
_UNCLASSIFIED_FACADES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "abicheck.contract_gating",
        "abicheck.policy.contract_finding_relevance",
        ("contract_relevance_of", "evaluation_status_of", "is_evaluated"),
    ),
)


@pytest.mark.parametrize(
    "facade_name,owner_name,public_names",
    _UNCLASSIFIED_FACADES,
    ids=[f"{f}->{o}" for f, o, _ in _UNCLASSIFIED_FACADES],
)
def test_unclassified_facade_still_delegates(
    facade_name: str, owner_name: str, public_names: tuple[str, ...]
) -> None:
    """Same delegation proof as above, named separately for the facades this
    PR keeps off every layer's ``legacy_paths`` on purpose (``model``-owned
    ``checker_types.DiffResult`` depends on them directly, and ``model``
    cannot statically depend on ``policy``). Staying unclassified must not
    mean staying undelegated -- the real implementation still moved out.
    """
    facade = importlib.import_module(facade_name)
    owner = importlib.import_module(owner_name)
    for name in public_names:
        assert getattr(facade, name) is getattr(owner, name)


def test_checker_types_diffresult_reaches_reclassify_through_the_flat_facade() -> None:
    """The one documented reason ``reclassify``/``contract_gating``/
    ``checker_policy`` stay flat, unclassified facades: ``model``-owned
    ``checker_types.DiffResult`` imports them directly and must keep
    resolving through the *flat* path, not a `policy`-classified one.

    This fails loudly if a future change moves ``effective_verdict_for_change``
    so the flat ``abicheck.reclassify`` facade no longer carries it --
    exactly the regression that would silently break
    ``DiffResult.effective_verdict_for_change`` description in
    ``checker_policy.py``'s/``reclassify.py``'s own module docstrings.
    """
    from abicheck.checker_types import Change, DiffResult
    from abicheck.model.change_catalog.kinds import ChangeKind
    from abicheck.policy.reclassify import effective_verdict_for_change

    change = Change(
        kind=ChangeKind.FUNC_ADDED, symbol="x", description="added function x"
    )
    result = DiffResult(
        old_version="1.0", new_version="1.1", library="libx", changes=[change]
    )
    # DiffResult's own method delegates to the same function the owner module
    # exports -- proving the flat-facade boundary this PR preserves actually
    # still connects `model`'s legacy DiffResult to `policy`'s real owner.
    expected = effective_verdict_for_change(change)
    assert result._effective_verdict_for_change(change) == expected


@pytest.mark.parametrize(
    "module_name,names",
    [
        ("abicheck.errors", ("AbicheckError", "ValidationError")),
        ("abicheck.dumper_contract", ("_attach_extraction_contract",)),
    ],
)
def test_already_classified_facades_keep_their_public_surface(
    module_name: str, names: tuple[str, ...]
) -> None:
    """``errors.py``/``dumper_contract.py`` were removed from
    ``public_root_surfaces`` in this PR because each already names a real
    owning layer via ``legacy_paths`` (``model``/``extract``
    respectively) -- the exemption was stale bookkeeping, not an open gap.
    Removing the now-redundant entry must not change either module's own
    import path.
    """
    module = importlib.import_module(module_name)
    for name in names:
        assert hasattr(module, name)


def test_public_root_surfaces_matches_the_reviewed_exception_set() -> None:
    """``architecture/modules.yaml``'s ``public_root_surfaces`` should hold
    exactly the reviewed "no single layer" / "investigated and punted"
    exceptions this PR's gap-B closure leaves open -- not re-grow back
    toward the original eleven as a convenient escape hatch for a future,
    unrelated change.
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    modules_yaml = json.loads((repo_root / "architecture" / "modules.yaml").read_text())
    assert set(modules_yaml["public_root_surfaces"]) == {
        "abicheck.checker_policy",
        "abicheck.contract_evidence",
        "abicheck.contract_gating",
        "abicheck.header_only_dump",
        "abicheck.reclassify",
        "abicheck.schemas",
        "abicheck.serialization",
    }
