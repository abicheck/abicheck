"""The evidence-adaptive default contract never leaves a finding unscored.

Bug class (ADR-049 Phase 7, #1493): with no ``--contract`` stated, the domain
is picked from the evidence (``public`` / ``exports`` / ``all``). A finding the
picked domain could not place -- a lost ``_ZTI``/``_ZTV`` whose header domain
was incomplete, a removed ELF version node, an L3 build-option flip, an L4/L5
source fact judged by an ``exports`` domain -- read ``UNKNOWN_UNRESOLVED``,
was dropped from the verdict, and the run reported ``NO_CHANGE`` (exit 1)
where the same comparison without headers, or before Phase 7, was BREAKING /
API_BREAK / COMPATIBLE_WITH_RISK.

The invariant, stated over the whole ``ChangeKind`` catalog rather than the
reported kinds: under an evidence-adaptive default, no finding of any kind
ends ``UNKNOWN_UNRESOLVED`` -- the adaptive default drops only what a domain
*proves* out of contract. The oracle is the relevance enum itself; the
negative control (a stated domain, same findings, same surfaces) must still
leave findings unresolved, so the sweep cannot pass vacuously.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.compatibility_evaluation_config import ValueProvenance
from abicheck.contract_pipeline import build_contract_stage
from abicheck.contract_relevance_types import (
    ContractMode,
    ContractRelevance,
    SelectorLayer,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.contract_default_mode import unresolved_default_fallback_applies
from abicheck.post_processing import PipelineContext

_ADAPTIVE = ValueProvenance(
    layer=SelectorLayer.BUILT_IN_DEFAULT,
    source_kind="evidence_adaptive",
    reference="public_header_evidence",
)
_STATED = ValueProvenance(
    layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag", reference="--contract"
)


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(version="1.0", functions=[_fn("pub_a"), _fn("pub_b")], **common),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a")], **common),
    )


def _every_kind() -> list[Change]:
    # A symbol no surface knows: the hardest placement case for every kind.
    return [Change(kind, "unplaceable_entity", f"{kind.value}") for kind in ChangeKind]


def _relevances(mode: str, provenance: ValueProvenance) -> list[tuple[str, object]]:
    old, new = _pair()
    stage = build_contract_stage(
        old,
        new,
        scope_to_public_surface=True,
        force_public_symbols=None,
        pp_ctx=PipelineContext(old=old, new=new),
        contract_mode=mode,
    )
    stage = dataclasses.replace(stage, mode_provenance=provenance, changes=[])
    changes = _every_kind()
    stage.classify(changes)
    return [(c.kind.value, c.contract_relevance) for c in changes]


@pytest.mark.parametrize("mode", ["public", "exports"])
def test_adaptive_default_leaves_no_kind_unresolved(mode: str) -> None:
    unresolved = [
        kind
        for kind, rel in _relevances(mode, _ADAPTIVE)
        if rel is ContractRelevance.UNKNOWN_UNRESOLVED
    ]
    assert unresolved == [], unresolved


@pytest.mark.parametrize("mode", ["public", "exports"])
def test_a_stated_domain_still_reports_what_it_cannot_place(mode: str) -> None:
    """Negative control: the sweep above is not vacuous -- the same findings
    under a *stated* domain do stay unresolved, so the adaptive fallback is
    what resolved them, and a stated domain is never second-guessed."""
    unresolved = [
        kind
        for kind, rel in _relevances(mode, _STATED)
        if rel is ContractRelevance.UNKNOWN_UNRESOLVED
    ]
    assert unresolved, mode


_PROVENANCES = {"adaptive": _ADAPTIVE, "stated": _STATED, "none": None}


def test_unresolved_fallback_rule_over_its_whole_domain() -> None:
    """Exhaustive over mode x provenance x relevance, against a table written
    from the rule's statement: only an unstated, narrower-than-all domain's
    unresolved finding is re-judged."""
    for mode, prov_key, rel in itertools.product(
        ContractMode, _PROVENANCES, ContractRelevance
    ):
        expected = (
            prov_key == "adaptive"
            and mode in (ContractMode.PUBLIC, ContractMode.EXPORTS)
            and rel.name == "UNKNOWN_UNRESOLVED"
        )
        got = unresolved_default_fallback_applies(mode, _PROVENANCES[prov_key], rel)
        assert got is expected, (mode, prov_key, rel)
