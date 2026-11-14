"""ADR-093: every ChangeKind declares observability, and declared relations are sound.

The oracle for the lattice properties is plain set arithmetic written here,
not the module's own helpers.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.observability import (
    UNCLASSIFIED,
    EvidenceSource,
    KindRelations,
    Observability,
    ObservabilityCondition,
    Platform,
    Subsumption,
    SubsumptionKey,
    observable,
    validate_catalog,
)
from abicheck.model.change_catalog.registry import ChangeKindMeta, Verdict

BASELINE = Path(__file__).parent / "data" / "change_kind_unclassified.json"
ALL_SOURCES = list(EvidenceSource)
ALL_CONFIGS = [
    frozenset(c)
    for r in range(1, len(ALL_SOURCES) + 1)
    for c in itertools.combinations(ALL_SOURCES, r)
]


def test_every_kind_has_a_valid_observable_at() -> None:
    entries = REGISTRY.entries
    assert set(entries) == {k.value for k in ChangeKind}
    for kind, e in entries.items():
        assert e.observable_at is UNCLASSIFIED or (
            isinstance(e.observable_at, tuple) and e.observable_at
        ), kind
        if e.observable_at is not UNCLASSIFIED:
            for obs in e.observable_at:
                alts = list(obs.alternatives)
                assert alts and all(alts), kind
                for a, b in itertools.permutations(alts, 2):
                    assert not a < b, f"{kind}: not an antichain"


def test_unclassified_set_matches_baseline_exactly() -> None:
    """The UNCLASSIFIED set may only shrink, and a new kind may not join it.

    Classifying a kind removes it from the baseline in the same PR; a kind
    added after this baseline must be classified from the start.
    """
    recorded = set(json.loads(BASELINE.read_text())["unclassified"])
    current = {
        k for k, e in REGISTRY.entries.items() if e.observable_at is UNCLASSIFIED
    }
    assert current - recorded == set(), "new UNCLASSIFIED kinds; declare observable_at"
    assert recorded - current == set(), (
        "classified kinds still in the baseline; remove them"
    )


@pytest.mark.parametrize("seed", range(40))
def test_observes_is_monotone_and_exact(seed: int) -> None:
    import random

    rng = random.Random(seed)
    # A random antichain: draw configs, keep the minimal ones.
    drawn = rng.sample(ALL_CONFIGS, rng.randint(1, 5))
    minimal = {c for c in drawn if not any(o < c for o in drawn)}
    obs = Observability(frozenset(minimal), rationale="test")
    for cfg in ALL_CONFIGS:
        expected = any(m <= cfg for m in minimal)  # oracle: subset test
        assert obs.observes(cfg) is expected
        if expected:
            for bigger in ALL_CONFIGS:
                if cfg <= bigger:
                    assert obs.observes(bigger)


@pytest.mark.parametrize(
    "alts",
    [
        [("B",), ("B", "D")],
        [("B", "H"), ("B", "H", "BD")],
        [("B", "D"), ("B", "H"), ("B", "D", "H")],
    ],
)
def test_non_antichain_is_refused(alts: list[tuple[str, ...]]) -> None:
    with pytest.raises(ValueError, match="antichain"):
        observable(*alts, rationale="r")


def test_rationale_required_above_binary_only() -> None:
    observable(("B",))  # {B} needs none
    with pytest.raises(ValueError, match="rationale"):
        observable(("B", "D"))
    with pytest.raises(ValueError, match="empty"):
        Observability(frozenset(), rationale="r")


def _meta(
    kind: str, verdict: Verdict = Verdict.BREAKING, **kw: object
) -> ChangeKindMeta:
    return ChangeKindMeta(kind, verdict, impact="x", **kw)  # type: ignore[arg-type]


def _catalog(*metas: ChangeKindMeta) -> dict[str, ChangeKindMeta]:
    return {m.kind: m for m in metas}


@pytest.mark.parametrize(
    ("metas", "message"),
    [
        ((_meta("a", relations=KindRelations(variant_of="zz")),), "not a kind"),
        ((_meta("a", relations=KindRelations(implies=("a",))),), "itself"),
        (
            (
                _meta("a", relations=KindRelations(same_event_as="b")),
                _meta("b", Verdict.API_BREAK),
            ),
            "default_verdict",
        ),
        (
            (
                _meta("a", relations=KindRelations(variant_of="b")),
                _meta("b", relations=KindRelations(variant_of="c")),
                _meta("c"),
            ),
            "canonical",
        ),
        (
            (
                _meta("a", relations=KindRelations(implies=("b",))),
                _meta("b", relations=KindRelations(implies=("c",))),
                _meta("c", relations=KindRelations(implies=("a",))),
            ),
            "cycle",
        ),
        (
            (
                _meta(
                    "a",
                    observable_at=(
                        observable(
                            ("B",),
                            condition=ObservabilityCondition(
                                platform=frozenset({Platform.ELF})
                            ),
                        ),
                        observable(
                            ("B",),
                            condition=ObservabilityCondition(
                                platform=frozenset({Platform.ELF, Platform.PE})
                            ),
                        ),
                    ),
                ),
            ),
            "disjoint",
        ),
    ],
)
def test_catalog_rejects_unsound_declarations(
    metas: tuple[ChangeKindMeta, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_catalog(_catalog(*metas))


def test_catalog_accepts_sound_declarations() -> None:
    validate_catalog(
        _catalog(
            _meta("a", relations=KindRelations(variant_of="b", implies=("c",))),
            _meta("b", relations=KindRelations(same_event_as="c")),
            _meta(
                "c",
                relations=KindRelations(
                    subsumed_by=(Subsumption("d", SubsumptionKey.ENTITY),)
                ),
            ),
            _meta(
                "d",
                observable_at=(observable(("B", "D"), ("B", "H"), rationale="layout"),),
            ),
        )
    )


def test_unclassified_survives_copy_and_pickle_protocol() -> None:
    import copy

    e = next(iter(REGISTRY.entries.values()))
    assert copy.deepcopy(e).observable_at is UNCLASSIFIED
    # The pickle contract without unpickling data: __reduce__ rebuilds the singleton.
    factory, args = UNCLASSIFIED.__reduce__()
    assert factory(*args) is UNCLASSIFIED
