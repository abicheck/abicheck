"""A release member inherits every request field by construction
(design-hardening plan, Phase 2 / F2, #1391's class).

The oracle is the dataclass itself, not a hand-written field list: a field
added to the request reaches ``service.run_compare`` for every member
without any further edit, and only ``MemberDelta``'s fields differ.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import pytest

from abicheck import cli_compare_release_pairwise as pairwise, service_compare_pipeline
from abicheck.workflows.release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_request,
    run_compare_kwargs,
)

_DELTA = {f.name for f in dataclasses.fields(MemberDelta)}


def _delta(i: int = 0) -> MemberDelta:
    return MemberDelta(
        old_input=Path(f"old{i}.so"),
        new_input=Path(f"new{i}.so"),
        old_pdb_path=Path(f"old{i}.debug"),
    )


def test_every_request_field_is_a_run_compare_keyword() -> None:
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


def test_synthetic_new_field_reaches_every_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add a field to the request type; nothing else changes, and each
    member's ``service.run_compare`` call receives it."""
    extended = dataclasses.make_dataclass(
        "ExtendedRequest",
        [("synthetic_setting", str, dataclasses.field(default="unset"))],
        bases=(ReleaseMemberCompareRequest,),
        frozen=True,
    )
    parent = extended(synthetic_setting="stated-by-caller", old_version="1")
    seen: list[dict[str, Any]] = []

    def fake_run_compare(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise RuntimeError("stop after capture")

    import abicheck.workflows.member_compare as service

    monkeypatch.setattr(service, "run_compare", fake_run_compare)
    monkeypatch.setattr(pairwise, "_normalize_binary_input", lambda p: (p, None))
    for i in range(3):
        with pytest.raises(RuntimeError):
            pairwise._run_compare_pair(member_request(parent, _delta(i)))
    assert [k["synthetic_setting"] for k in seen] == ["stated-by-caller"] * 3
    assert [k["old_input"] for k in seen] == [Path(f"old{i}.so") for i in range(3)]
    assert all(k["old_version"] == "1" for k in seen)


def test_parent_request_cannot_be_run() -> None:
    with pytest.raises(ValueError, match="MemberDelta"):
        run_compare_kwargs(ReleaseMemberCompareRequest())
