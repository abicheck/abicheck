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
    Language,
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


_AXIS_VALUES = {
    "platform": [
        None,
        *[frozenset(c) for r in (1, 2, 3) for c in itertools.combinations(Platform, r)],
    ],
    "language": [
        None,
        *[frozenset(c) for r in (1, 2) for c in itertools.combinations(Language, r)],
    ],
}


def _conditions() -> list[ObservabilityCondition]:
    return [
        ObservabilityCondition(platform=p, language=lang)
        for p in _AXIS_VALUES["platform"]
        for lang in _AXIS_VALUES["language"]
        if p is not None or lang is not None
    ]


def _points(cond: ObservabilityCondition | None) -> set[tuple[Platform, Language]]:
    """Oracle: the explicit set of (platform, language) points a condition admits."""
    return {
        (p, lang)
        for p in Platform
        for lang in Language
        if cond is None
        or (
            (cond.platform is None or p in cond.platform)
            and (cond.language is None or lang in cond.language)
        )
    }


def test_condition_overlap_and_match_agree_with_point_sets() -> None:
    conds = _conditions()
    for a in conds:
        assert a.overlaps(None)
        for b in conds:
            assert a.overlaps(b) is bool(_points(a) & _points(b)), (a, b)
        for p in Platform:
            for lang in Language:
                assert a.matches(p, lang) is ((p, lang) in _points(a))
        # An unstated run axis never hides a kind.
        assert a.matches(None, None)


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"platform": frozenset()}, {"language": frozenset()}],
)
def test_condition_must_restrict_a_nonempty_axis(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="ObservabilityCondition"):
        ObservabilityCondition(**kwargs)  # type: ignore[arg-type]


def test_empty_configuration_is_refused() -> None:
    with pytest.raises(ValueError, match="may not be empty"):
        Observability(frozenset({frozenset()}), rationale="r")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ((), "non-empty tuple"),
        ([observable(("B",))], "non-empty tuple"),
        (("not an observability",), "non-Observability"),
        ((observable(("B",)), observable(("B", "D"), rationale="r")), "disjoint"),
    ],
)
def test_malformed_observable_at_is_refused(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_catalog(_catalog(_meta("a", observable_at=value)))


def test_disjoint_conditions_are_accepted_and_unclassified_is_listed() -> None:
    elf = ObservabilityCondition(platform=frozenset({Platform.ELF}))
    pe = ObservabilityCondition(platform=frozenset({Platform.PE}))
    cat = _catalog(
        _meta(
            "a",
            observable_at=(
                observable(("B",), condition=elf),
                observable(("B", "D"), rationale="pdb", condition=pe),
            ),
        ),
        _meta("b"),
        _meta("c"),
    )
    validate_catalog(cat)
    assert sorted(k for k, e in cat.items() if e.observable_at is UNCLASSIFIED) == [
        "b",
        "c",
    ]
    assert repr(UNCLASSIFIED) == "UNCLASSIFIED"


def test_relations_truthiness_follows_declared_fields() -> None:
    assert not KindRelations()
    for rel in (
        KindRelations(variant_of="x"),
        KindRelations(same_event_as="x"),
        KindRelations(subsumed_by=(Subsumption("x", SubsumptionKey.ENTITY_FIELD),)),
        KindRelations(implies=("x",)),
    ):
        assert rel and rel.targets() == ["x"]


def test_contract_subject_mismatch_is_refused() -> None:
    from abicheck.model.change_catalog.dimensions import ContractSubject

    subjects = list(ContractSubject)
    with pytest.raises(ValueError, match="contract_subject"):
        validate_catalog(
            _catalog(
                _meta(
                    "a",
                    contract_subject=subjects[0],
                    relations=KindRelations(variant_of="b"),
                ),
                _meta("b", contract_subject=subjects[1]),
            )
        )
