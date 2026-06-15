# SPDX-License-Identifier: Apache-2.0
"""Fast-lane validation of the G20 audit / cross-source example corpus.

The G20 cases (143-151, ADR-035) do not fit the catalog's ``v1``/``v2``
binary-diff shape: each demonstrates the single-release *audit* or intra-version
*cross-source* machinery on **one** artifact. Every case ships a committed
:class:`~abicheck.model.AbiSnapshot` fixture (``snapshot.abi.json``, built by
``scripts/gen_g20_fixtures.py``) carrying the L0/L2/L3/L4/L5 provenance a live
``scan --audit`` would dump, so the corpus is validated here **compiler-free**.

Each case's ``ground_truth.json`` entry declares ``expected_crosscheck_kinds``
(the ``run_crosschecks`` findings it must produce) and ``expected_providers``
(the per-check provider-agreement list). This module asserts both, and keeps the
committed fixtures in sync with their generator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._scan_fixtures import crosscheck_surface, load_case_snapshot

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"
_GT = json.loads((_EXAMPLES / "ground_truth.json").read_text())["verdicts"]

#: G20 cases: those declaring a cross-check expectation (the v4 audit corpus).
_G20_CASES = sorted(
    name for name, info in _GT.items() if info.get("expected_crosscheck_kinds")
)


def _kinds(case_name: str, filename: str = "snapshot.abi.json") -> set[str]:
    return set(crosscheck_surface(load_case_snapshot(case_name, filename)).kinds)


def _providers(
    case_name: str, filename: str = "snapshot.abi.json"
) -> dict[str, list[str]]:
    return crosscheck_surface(load_case_snapshot(case_name, filename)).providers


def test_g20_corpus_is_non_empty() -> None:
    # Guards against the discovery query silently matching nothing (which would
    # turn every parametrized assertion below into a vacuous pass).
    assert len(_G20_CASES) >= 9, f"expected the 9 G20 cases, found {_G20_CASES}"


@pytest.mark.parametrize("case_name", _G20_CASES)
def test_case_emits_expected_crosscheck_kinds(case_name: str) -> None:
    info = _GT[case_name]
    snap_path = _EXAMPLES / case_name / "snapshot.abi.json"
    assert snap_path.is_file(), (
        f"{case_name}: missing committed fixture {snap_path.name}"
    )
    got = _kinds(case_name)
    want = set(info["expected_crosscheck_kinds"])
    assert want.issubset(got), (
        f"{case_name}: expected cross-check kinds {sorted(want)} "
        f"not all produced; got {sorted(got)}"
    )


@pytest.mark.parametrize("case_name", _G20_CASES)
def test_case_records_expected_providers(case_name: str) -> None:
    info = _GT[case_name]
    expected = info.get("expected_providers")
    if not expected:
        pytest.skip(f"{case_name}: no expected_providers declared")
    provs = _providers(case_name)
    for check, want in expected.items():
        assert provs.get(check) == want, (
            f"{case_name}: provider list for {check!r} is {provs.get(check)!r}, "
            f"expected {want!r}"
        )


def test_provider_matrix_thin_has_fewer_providers() -> None:
    """case151: the same finding is corroborated by more providers in the rich
    fixture than in the thin one (the §6.8 provider-agreement matrix)."""
    case = "case151_xcheck_provider_matrix"
    check = "private_header_leak"
    rich = _providers(case, "snapshot.abi.json")[check]
    thin = _providers(case, "thin.abi.json")[check]
    # Both still flag the leak; the rich fixture lists strictly more providers.
    assert check in _kinds(case, "thin.abi.json")
    assert set(thin) < set(rich), f"thin {thin} not a strict subset of rich {rich}"
    assert "source_index" in rich and "source_index" not in thin


def test_fixtures_match_generator() -> None:
    """The committed snapshot fixtures must equal what the writer produces."""
    import importlib.util

    path = _REPO / "scripts" / "gen_g20_fixtures.py"
    spec = importlib.util.spec_from_file_location("gen_g20_fixtures", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0, (
        "G20 fixtures drifted; run scripts/gen_g20_fixtures.py"
    )
