"""A release member's request is the parent with its delta applied, and every
field of that request reaches the member's typed ``CompareRequest``
(design-hardening plan, Phase 2 / F2, #1391's class).

The oracle is the dataclass itself, not a hand-written field list: a field
added to the request must be mapped by :func:`member_compare_request`, or the
member refuses to build rather than silently dropping it.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from abicheck import service_compare_pipeline
from abicheck.workflows.release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_compare_request,
    member_request,
)

_DELTA = {f.name for f in dataclasses.fields(MemberDelta)}


def _delta(i: int = 0) -> MemberDelta:
    return MemberDelta(
        old_input=Path(f"old{i}.so"),
        new_input=Path(f"new{i}.so"),
        old_pdb_path=Path(f"old{i}.debug"),
    )


def test_every_request_field_is_a_run_compare_keyword() -> None:
    # The public keyword API must still accept every release field, so a
    # release member never carries a setting a single-pair caller cannot pass.
    params = set(inspect.signature(service_compare_pipeline.run_compare).parameters)
    fields = {f.name for f in dataclasses.fields(ReleaseMemberCompareRequest)}
    assert fields <= params, fields - params


@pytest.mark.parametrize("member", range(4))
def test_member_differs_from_parent_only_in_delta_fields(member: int) -> None:
    parent = ReleaseMemberCompareRequest(
        old_version="1",
        new_version="2",
        lang="c",
        lang_explicit=True,
        suppress=Path("s.yml"),
        exclude_headers=("x/*",),
        contract_mode="public",
    )
    child = member_request(parent, _delta(member))
    for f in dataclasses.fields(parent):
        if f.name in _DELTA:
            continue
        assert getattr(child, f.name) is getattr(parent, f.name), f.name
    assert {n for n in _DELTA if getattr(child, n) != getattr(parent, n)} <= _DELTA


def test_a_field_without_a_mapping_is_refused_not_dropped() -> None:
    """Add a field to the request type with no mapping: building the member's
    request must fail and name it, rather than compare without it."""
    extended = dataclasses.make_dataclass(
        "ExtendedRequest",
        [("synthetic_setting", str, dataclasses.field(default="unset"))],
        bases=(ReleaseMemberCompareRequest,),
        frozen=True,
    )
    parent = extended(synthetic_setting="stated-by-caller", old_version="1")
    with pytest.raises(TypeError, match="synthetic_setting"):
        member_compare_request(member_request(parent, _delta(0)))


def test_parent_request_cannot_be_compared() -> None:
    with pytest.raises(ValueError, match="MemberDelta"):
        member_compare_request(ReleaseMemberCompareRequest())
