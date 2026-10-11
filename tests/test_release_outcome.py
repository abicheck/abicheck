"""`policy.release_outcome` -- the one release verdict/outcome fold.

Bug class: the release verdict fold was spelled in five places (the
fan-out's rollup, the global-only fold, the compatibility fold, the
bundle-facts sentinel precedence, the consumer rank), each with its own copy
of the ordering. Any copy drifting lets one report surface disagree with
another about the same release. The oracle below is written independently
of the module: a literal ordering list and Python's ``max`` (first maximal
element wins a tie), checked exhaustively over a small verdict domain plus
named sibling cases (sentinels, ERROR masking, NO_CHANGE floor vs not-run,
not_comparable, the empty release).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.release_outcome import (
    RELEASE_OPERATIONAL_SENTINELS,
    RELEASE_VERDICT_ORDER,
    apply_removed_member_floor,
    compatibility_rank,
    consumer_verdict_rank,
    fold_release_outcome,
    release_global_verdicts,
    worse_release_verdict,
)

# --- independent oracle ------------------------------------------------------

_ORDER = [
    "NO_CHANGE",
    "COMPATIBLE",
    "COMPATIBLE_WITH_RISK",
    "API_BREAK",
    "BREAKING",
    "ERROR",
    "not_comparable",
]
_SENTINELS = {"ERROR", "not_comparable", "unsupported", "failed"}
_DOMAIN = [*_ORDER, "unsupported", "failed"]


def _rank(v: str) -> int:
    return _ORDER.index(v) if v in _ORDER else 0


def _worst(values: list[str]) -> str | None:
    return max(values, key=_rank) if values else None


def _oracle(
    members: list[str], globals_: list[str | None], ran: bool, removed: bool
) -> tuple[str, str, str | None]:
    present = [g for g in globals_ if g is not None]
    global_v = _worst(["NO_CHANGE", *present]) or "NO_CHANGE"
    reported = _worst(["NO_CHANGE", *members, *present]) or "NO_CHANGE"
    if removed and _rank(reported) < _rank("COMPATIBLE_WITH_RISK"):
        reported = "COMPATIBLE_WITH_RISK"
    real = [m for m in members if m not in _SENTINELS]
    if ran and global_v not in _SENTINELS:
        real.append(global_v)
    return reported, global_v, _worst(real)


# --- ordering ----------------------------------------------------------------


def test_the_one_ordering_matches_the_oracle() -> None:
    assert sorted(RELEASE_VERDICT_ORDER, key=RELEASE_VERDICT_ORDER.__getitem__) == (
        _ORDER
    )
    assert set(RELEASE_OPERATIONAL_SENTINELS) == _SENTINELS


@pytest.mark.parametrize(("a", "b"), list(itertools.product(_DOMAIN, repeat=2)))
def test_worse_release_verdict_is_strict_and_keeps_ties(a: str, b: str) -> None:
    assert worse_release_verdict(a, b) == (b if _rank(b) > _rank(a) else a)


# --- the fold, exhaustively over a small domain -------------------------------


@pytest.mark.parametrize("n_members", [0, 1, 2])
def test_fold_matches_oracle_exhaustively(n_members: int) -> None:
    glob_domain: list[str | None] = [None, *_DOMAIN]
    for members in itertools.product(_DOMAIN, repeat=n_members):
        for bundle, matrix in itertools.product(glob_domain, repeat=2):
            for ran in (True, False):
                for removed in (True, False):
                    out = fold_release_outcome(
                        members,
                        (bundle, matrix),
                        global_ran=ran,
                        removed_members=["lib.so"] if removed else [],
                    )
                    expected = _oracle(list(members), [bundle, matrix], ran, removed)
                    assert (
                        out.reported_verdict,
                        out.global_verdict,
                        out.compatibility,
                    ) == expected, (members, bundle, matrix, ran, removed)
                    assert out.ran is ran


# --- named sibling cases of the bug class --------------------------------------


@pytest.mark.parametrize(
    ("members", "globals_", "ran", "reported", "global_v", "compat"),
    [
        # Empty release: nothing reported, nothing observed.
        ([], [], None, "NO_CHANGE", "NO_CHANGE", None),
        # ERROR masking: the reported verdict says ERROR, compatibility
        # still carries the real BREAKING it would otherwise lose.
        (["BREAKING", "ERROR"], [], None, "ERROR", "NO_CHANGE", "BREAKING"),
        # not_comparable outranks ERROR.
        (["ERROR", "not_comparable"], [], None, "not_comparable", "NO_CHANGE", None),
        # Only sentinels: compatibility stays unknown, never NO_CHANGE.
        (["unsupported", "failed"], [], None, "NO_CHANGE", "NO_CHANGE", None),
        # NO_CHANGE floor vs not-run: a vacuous bundle NO_CHANGE that did
        # not genuinely run is not a completed comparison ...
        (["ERROR"], ["NO_CHANGE", None], False, "ERROR", "NO_CHANGE", None),
        # ... but the same value from a bundle that ran is one.
        (["ERROR"], ["NO_CHANGE", None], True, "ERROR", "NO_CHANGE", "NO_CHANGE"),
        # The global verdict is never masked by a member sentinel.
        (
            ["not_comparable"],
            [None, "API_BREAK"],
            None,
            "not_comparable",
            "API_BREAK",
            "API_BREAK",
        ),
        # Worse of bundle and matrix wins.
        ([], ["COMPATIBLE", "BREAKING"], None, "BREAKING", "BREAKING", "BREAKING"),
    ],
)
def test_sibling_cases(
    members: list[str],
    globals_: list[str | None],
    ran: bool | None,
    reported: str,
    global_v: str,
    compat: str | None,
) -> None:
    out = fold_release_outcome(members, globals_, global_ran=ran)
    assert (out.reported_verdict, out.global_verdict, out.compatibility) == (
        reported,
        global_v,
        compat,
    )


@pytest.mark.parametrize("verdict", _DOMAIN)
def test_removed_member_floor_never_lowers(verdict: str) -> None:
    floored = apply_removed_member_floor(verdict, removed=True)
    assert _rank(floored) == max(_rank(verdict), _rank("COMPATIBLE_WITH_RISK"))
    assert apply_removed_member_floor(verdict, removed=False) == verdict


@pytest.mark.parametrize(
    ("nc", "failed", "verdict", "expected"),
    [
        (
            nc,
            failed,
            v.value,
            "not_comparable" if nc else "ERROR" if failed else v.value,
        )
        for nc, failed, v in itertools.product((True, False), (True, False), Verdict)
    ],
)
def test_bundle_facts_sentinel_precedence(
    nc: bool, failed: bool, verdict: str, expected: str
) -> None:
    """compare-bundle-facts' reported verdict: not_comparable > ERROR > verdict."""
    sentinels = [*(["not_comparable"] if nc else []), *(["ERROR"] if failed else [])]
    assert fold_release_outcome(sentinels, [verdict]).reported_verdict == expected


# --- derived rankings ---------------------------------------------------------


@pytest.mark.parametrize(("a", "b"), list(itertools.product(Verdict, repeat=2)))
def test_compatibility_rank_orders_real_verdicts(a: Verdict, b: Verdict) -> None:
    assert (compatibility_rank(a) < compatibility_rank(b)) == (
        _ORDER.index(a.value) < _ORDER.index(b.value)
    )
    assert compatibility_rank(a) == compatibility_rank(a.value)


@pytest.mark.parametrize("bad", sorted(_SENTINELS) + ["bogus"])
def test_compatibility_rank_rejects_non_verdicts(bad: str) -> None:
    with pytest.raises(ValueError):
        compatibility_rank(bad)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("NO_CHANGE", 0),
        ("COMPATIBLE", 0),
        ("COMPATIBLE_WITH_RISK", 1),
        ("API_BREAK", 2),
        ("BREAKING", 3),
        ("ERROR", 0),
        ("not_comparable", 0),
        ("bogus", 0),
        (None, 0),
        (Verdict.BREAKING, 3),
    ],
)
def test_consumer_verdict_rank(verdict: object, expected: int) -> None:
    assert consumer_verdict_rank(verdict) == expected


def test_release_global_verdicts_reads_duck_typed_results() -> None:
    from types import SimpleNamespace

    bundle = SimpleNamespace(bundle_verdict=Verdict.COMPATIBLE)
    matrix = SimpleNamespace(verdict=Verdict.API_BREAK)
    assert release_global_verdicts(None, None) == (None, None)
    assert release_global_verdicts(bundle, matrix) == ("COMPATIBLE", "API_BREAK")
