# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""WS-A build-identity comparability axis (ADR-050 amendment).

The invariant, stated over an exhaustive small domain rather than one
reported pair: two snapshots whose L3 build evidence names different build
systems/generators, or different requested root targets, are refused as not
comparable; a pair where only one side records its build system is compared
but bounded (never a clean pass, never a fabricated finding); a pair where
either side carries no build evidence is untouched by this axis.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, Generator, TargetScope
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.checker import compare
from abicheck.comparability import check_contracts_comparable
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.extraction_contract import build_identity_of

# (kind, generator, version) triples per side; () = no generator recorded.
GENERATOR_SETS: list[tuple[tuple[str, str, str], ...]] = [
    (),
    (("generic", "", ""),),
    (("cmake", "Ninja", "3.28"),),
    (("cmake", "Ninja", "3.30"),),  # version-only difference
    (("cmake", "Unix Makefiles", "3.28"),),
    (("bazel", "", "7.1"),),
    (("make", "", ""),),
]
ROOT_SCOPES: list[tuple[str, ...] | None] = [None, ("//lib:a",), ("//lib:a", "//lib:b")]
_ABSENT = "no-evidence"


def _snap(gens, roots, *, version="1.0") -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libx.so",
        version=version,
        functions=[
            Function(
                name="f", mangled="f", return_type="int", visibility=Visibility.PUBLIC
            )
        ],
    )
    if gens == _ABSENT:
        return snap
    evidence = BuildEvidence(
        generators=[Generator(kind=k, generator=g, version=v) for k, g, v in gens],
        target_scope=None if roots is None else TargetScope(requested=list(roots)),
    )
    snap.build_source = BuildSourcePack(root="", build_evidence=evidence)
    return snap


def _oracle(a_gens, a_roots, b_gens, b_roots) -> str:
    """Independent restatement of the documented rule."""
    if a_gens == _ABSENT or b_gens == _ABSENT:
        return "ok"

    def systems(gens):
        return {(k, g) for k, g, _v in gens if k not in ("", "generic")}

    def roots(r):
        return None if not r else frozenset(r)

    if roots(a_roots) != roots(b_roots):
        return "fatal"
    sa, sb = systems(a_gens), systems(b_gens)
    if sa and sb:
        return "ok" if sa == sb else "fatal"
    if bool(sa) != bool(sb):
        return "bounded"
    return "ok"


def _observed(old, new) -> str:
    try:
        mismatch = check_contracts_comparable(old, new)
    except ProfileMismatchError:
        return "fatal"
    if mismatch is None:
        return "ok"
    assert not mismatch.fatal and mismatch.kind == "profile"
    return "bounded"


def test_build_identity_axis_matches_oracle_over_exhaustive_domain():
    sides = [(g, r) for g in [*GENERATOR_SETS, _ABSENT] for r in ROOT_SCOPES]
    disagreements = []
    outcomes = set()
    for (ag, ar), (bg, br) in itertools.product(sides, repeat=2):
        expected = _oracle(ag, ar, bg, br)
        outcomes.add(expected)
        got = _observed(_snap(ag, ar), _snap(bg, br))
        if got != expected:
            disagreements.append(((ag, ar), (bg, br), expected, got))
    # Vacuity guard: the domain exercises every outcome.
    assert outcomes == {"ok", "fatal", "bounded"}
    assert not disagreements, disagreements[:10]


@pytest.mark.parametrize("roots", ROOT_SCOPES)
def test_build_identity_is_symmetric_and_order_insensitive(roots):
    a = _snap((("cmake", "Ninja", "1"), ("ninja", "", "")), roots)
    b = _snap(
        (("ninja", "", "2"), ("cmake", "Ninja", "3")),
        tuple(reversed(roots or ())) or None,
    )
    assert build_identity_of(a) == build_identity_of(b)
    assert check_contracts_comparable(a, b) is None
    assert check_contracts_comparable(b, a) is None


def test_public_compare_refuses_different_build_systems():
    old = _snap((("cmake", "Ninja", ""),), None)
    new = _snap((("bazel", "", ""),), None, version="2.0")
    with pytest.raises(ProfileMismatchError, match="build system differs"):
        compare(old, new)
    # --diagnostic-comparison forces it through, marked untrusted per dimension.
    result = compare(old, new, diagnostic_comparison=True)
    assert result.assurance == "none"
    assert result.comparability_assurance is not None
    assert result.comparability_assurance["source"] == "unverified"
    assert result.comparability_assurance["symbol"] == "trusted"


def test_public_compare_one_sided_build_system_is_bounded_not_clean():
    # A pre-identity baseline (compile DB only, no generator) vs a CMake one.
    old = _snap((), None)
    new = _snap((("cmake", "Ninja", ""),), None, version="2.0")
    result = compare(old, new)
    assert result.assurance is None  # nothing was forced
    assert any("recorded on one side only" in w for w in result.coverage_warnings)
    assert result.comparability_assurance is not None
    assert result.comparability_assurance["layout"] == "unverified"
    # No finding was fabricated from the identity gap.
    assert not result.changes


def test_public_compare_refuses_different_root_targets():
    old = _snap((("bazel", "", ""),), ("//lib:a",))
    new = _snap((("bazel", "", ""),), None, version="2.0")
    with pytest.raises(ProfileMismatchError, match="root targets differ"):
        compare(old, new)


def test_stored_baseline_keeps_its_build_identity(tmp_path):
    """The identity is read off persisted L3 evidence, so a stored baseline
    carries it with no separate field (and an old one, written before this
    axis existed, yields the identical record)."""
    from abicheck.serialization import load_snapshot, save_snapshot

    old = _snap((("cmake", "Ninja", "3.28"),), ("libx",))
    path = tmp_path / "baseline.json"
    save_snapshot(old, path)
    stored = load_snapshot(path)
    assert build_identity_of(stored) == build_identity_of(old)
    new = _snap((("make", "", ""),), ("libx",), version="2.0")
    with pytest.raises(ProfileMismatchError):
        compare(stored, new)
