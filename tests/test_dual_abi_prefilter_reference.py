# SPDX-License-Identifier: Apache-2.0
"""The cxx11 prefilter is an optimization: it must never change a finding.

``_diff_glibcxx_dual_abi`` skips its demangling when no churned mangled name
contains ``cxx11``. Reference mode
(``ABICHECK_REFERENCE_MODE=1``) disables that skip. Over generated churn sets
(string flips, tag-only changes, user-namespace ``__cxx11`` moves, unchanged and
removed-only functions, both directions) the optimized and reference findings
must be equal. (The ABI-tag detector no longer demangles at all: see its module
docstring.)
"""

from __future__ import annotations

import random

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.execution_cache import REFERENCE_MODE_ENV_VAR

_SEEDS = range(40)
_STRING_NEW = "_Z{n}{name}RKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE"


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions,
        variables=[],
        types=[],
        enums=[],
        typedefs={},
    )


def _generated_pair(seed: int) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Old and new snapshots for one seed: a random mix of churn families, with a
    random direction per seed (legacy->cxx11 or cxx11->legacy)."""
    rng = random.Random(seed)
    forward = rng.random() < 0.5
    old: list[Function] = []
    new: list[Function] = []
    for i in range(rng.randint(1, 10)):
        name = f"fn{i}"
        n = len(name)
        family = rng.choice(["string", "tag", "userns", "same", "removed"])
        if family == "string":
            legacy = f"_Z{n}{name}RKSs"
            cxx11 = _STRING_NEW.format(n=n, name=name)
            a, b = (legacy, cxx11) if forward else (cxx11, legacy)
            old.append(_fn(name, a))
            new.append(_fn(name, b))
        elif family == "tag":
            plain = f"_Z{n}{name}v"
            tagged = f"_Z{n}{name}B5cxx11v"
            a, b = (plain, tagged) if forward else (tagged, plain)
            old.append(_fn(name, a))
            new.append(_fn(name, b))
        elif family == "userns":
            old.append(_fn(name, f"_ZN3api{n}{name}Ev"))
            new.append(_fn(name, f"_ZN3api7__cxx11{n}{name}Ev"))
        elif family == "same":
            both = f"_Z{n}{name}v"
            old.append(_fn(name, both))
            new.append(_fn(name, both))
        else:
            old.append(_fn(name, f"_Z{n}{name}v"))
    return _snap(old), _snap(new)


def _findings(old: AbiSnapshot, new: AbiSnapshot, monkeypatch, reference: bool):
    if reference:
        monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    else:
        monkeypatch.delenv(REFERENCE_MODE_ENV_VAR, raising=False)
    result = compare(old, new)
    return sorted(
        (c.kind.value, c.symbol, c.description or "", c.old_value, c.new_value)
        for c in result.changes
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_prefilter_findings_equal_the_unfiltered_path(seed, monkeypatch):
    old, new = _generated_pair(seed)
    optimized = _findings(old, new, monkeypatch, reference=False)
    reference = _findings(old, new, monkeypatch, reference=True)
    assert optimized == reference


def test_generated_sets_exercise_both_detectors(monkeypatch):
    """Engagement: the equality above must not be vacuous. Across the seeds the
    generated sets produce both a dual-ABI flip finding and an ABI-tag finding,
    and a no-cxx11 set (which the prefilter skips) produces neither."""
    kinds: set[str] = set()
    for seed in _SEEDS:
        old, new = _generated_pair(seed)
        kinds.update(k for k, *_ in _findings(old, new, monkeypatch, reference=True))
    assert "glibcxx_dual_abi_flip_detected" in kinds
    assert "abi_tag_changed" in kinds
    plain = _snap([_fn("p", "_Z1pv")])
    plain_new = _snap([_fn("p", "_Z1pv"), _fn("q", "_Z1qv")])
    skipped = {k for k, *_ in _findings(plain, plain_new, monkeypatch, reference=False)}
    assert skipped.isdisjoint({"glibcxx_dual_abi_flip_detected", "abi_tag_changed"})
