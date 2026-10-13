# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``FUNC_BECAME_INLINE``/``FUNC_LOST_INLINE`` must not fire on an ``is_inline``
value a pre-fix header backend recorded wrongly (known gap: a pre-fix clang
baseline against a post-fix candidate).

The oracle is the raw inline attribute each snapshot declares, not the
detector's own gate, and every case goes through the public ``compare``
path. The stale-load case is also driven through the real snapshot codec, so
the persisted-marker behaviour is tested rather than assumed.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.snapshot_reliability import FACT_FAMILIES
from abicheck.policy.analysis_assurance_schema_staleness import schema_staleness_status
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_to_dict

_INLINE_KINDS = {ChangeKind.FUNC_BECAME_INLINE, ChangeKind.FUNC_LOST_INLINE}


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


def test_inline_family_is_registered() -> None:
    assert "inline" in FACT_FAMILIES


@pytest.mark.parametrize("producer", ["clang", "castxml"])
@pytest.mark.parametrize("stale_side", ["old", "new"])
def test_stale_side_declines_become_inline(producer: str, stale_side: str) -> None:
    # Truth: the function is inline on both sides. The stale side recorded it
    # as non-inline (the pre-fix defect), so raw comparison reports a change.
    old_inline, new_inline = False, True
    if stale_side == "new":
        old_inline, new_inline = True, False
    old = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=old_inline)],
        ast_producer=producer,
        from_headers=True,
    )
    new = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=new_inline)],
        ast_producer=producer,
        from_headers=True,
    )
    stale = old if stale_side == "old" else new
    stale.stale_fact_families = frozenset({"inline"})
    assert not (_INLINE_KINDS & _kinds(compare(old, new)))


@pytest.mark.parametrize("producer", ["clang", "castxml"])
def test_real_change_with_both_sides_reliable_is_reported(producer: str) -> None:
    old = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=False)],
        ast_producer=producer,
        from_headers=True,
    )
    new = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=True)],
        ast_producer=producer,
        from_headers=True,
    )
    result = compare(old, new)
    assert ChangeKind.FUNC_BECAME_INLINE in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_declined_pair_is_recorded_in_coverage() -> None:
    old = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=False)],
        ast_producer="clang",
        from_headers=True,
    )
    new = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=True)],
        ast_producer="clang",
        from_headers=True,
    )
    old.stale_fact_families = frozenset({"inline"})
    assert not (_INLINE_KINDS & _kinds(compare(old, new)))
    # The coverage record is the assurance judgement for the pair: a declined
    # inline comparison must read as degraded, and say which family.
    status, notes = schema_staleness_status(old, new)
    assert status == "degraded"
    assert any("inline_facts_reliable" in note for note in notes)


def _saved(schema_version: int, *, inline: bool) -> dict[str, object]:
    snap = _snap(
        functions=[_func("compute", "_Z7computei", is_inline=inline)],
        ast_producer="clang",
        from_headers=True,
    )
    d = snapshot_to_dict(snap)
    d["schema_version"] = schema_version
    # A pre-fix writer never persisted the marker, so a legacy document has
    # no such key. Dropping it is what makes this a legacy document at all.
    d.pop("inline_facts_reliable", None)
    return d


@pytest.mark.parametrize("schema_version", [0, 46, 52])
def test_pre_fix_snapshot_loads_stale(schema_version: int) -> None:
    loaded = snapshot_from_dict(_saved(schema_version, inline=False))
    assert "inline" in loaded.stale_fact_families


def test_stale_marker_survives_resave_at_current_schema() -> None:
    # A stale baseline loaded and written back gets the current schema
    # version. The persisted marker must keep it stale, or the wrong fact
    # would become trusted on the second load.
    loaded = snapshot_from_dict(_saved(52, inline=False))
    resaved = snapshot_to_dict(loaded)
    assert resaved["inline_facts_reliable"] is False
    assert "inline" in snapshot_from_dict(resaved).stale_fact_families


def test_current_snapshot_is_never_marked_stale() -> None:
    fresh = snapshot_from_dict(
        snapshot_to_dict(_snap(from_headers=True, ast_producer="clang"))
    )
    assert "inline" not in fresh.stale_fact_families
