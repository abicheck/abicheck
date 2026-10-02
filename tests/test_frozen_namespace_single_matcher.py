# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Frozen-namespace demotion and escalation answer through one matcher.

Bug class: two pipeline steps deciding "is this finding in a frozen
namespace" from different candidate names. ``DemoteUnreachableInternalChurn``
looked only at the root type name; ``EscalateFrozenNamespaceViolations``
looks at the symbol, the causing type and the qualified name. A finding
whose type is internal but whose *cause* is frozen was demoted out of
surface first, so escalation never saw it and the frozen contract was not
enforced. The oracle is the escalation step itself, run on a copy of the
input: every finding it would tag must survive demotion.
"""

from __future__ import annotations

import copy
import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot
from abicheck.post_processing import (
    DemoteUnreachableInternalChurn,
    EscalateFrozenNamespaceViolations,
)
from abicheck.post_processing_context import PipelineContext

_PATTERNS = (["**::frozen::*"], ["lib::detail::r1"], ["**::detail::r1::*", "x::*"])
_SYMBOLS = ("lib::detail::Impl", "lib::detail::r1::Impl", "lib::frozen::detail::Impl")
_CAUSES = (None, "lib::frozen::Base", "lib::detail::r1::Node", "lib::other::T")
_QUALIFIED = (None, "lib::frozen::detail::Impl", "lib::api::Handle")


def _ctx(patterns: list[str]) -> PipelineContext:
    return PipelineContext(
        old=AbiSnapshot(library="l", version="1"),
        new=AbiSnapshot(library="l", version="2"),
        frozen_namespaces=list(patterns),
    )


@pytest.mark.parametrize("patterns", _PATTERNS)
def test_a_finding_escalation_would_tag_is_never_demoted(patterns) -> None:
    for symbol, cause, qualified in itertools.product(_SYMBOLS, _CAUSES, _QUALIFIED):
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol=symbol,
            description="size changed",
            caused_by_type=cause,
            qualified_name=qualified,
        )
        probe = copy.deepcopy(change)
        EscalateFrozenNamespaceViolations().run([probe], _ctx(patterns))
        would_tag = probe.frozen_namespace_violation is not None

        ctx = _ctx(patterns)
        kept = DemoteUnreachableInternalChurn().run([change], ctx)
        if would_tag:
            assert kept == [change], (symbol, cause, qualified, patterns)
        else:
            # Internal, unreachable and not frozen: demoted, recorded in the ledger.
            assert kept == [] and ctx.out_of_surface == [change]
