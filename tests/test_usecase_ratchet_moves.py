# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Ratchet move detection (``scripts/usecase_adr_reach.py``).

A function that moved to another path (a module split into a package) is not
growth. Each expectation below is written out by hand from the rule -- same
bare qualified name, same normalized body, unambiguous name-only pairs -- and
not recomputed with the module's own helpers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import usecase_adr_reach as ar  # noqa: E402
import usecase_paths as up  # noqa: E402

BODY_A = "def f(x):\n    return x + 1\n"
BODY_B = "def f(x):\n    return x * 2\n"


def _ratchet(unreached, baseline, target=None, source=None):
    return ar.ratchet_unreached(
        set(unreached),
        baseline,
        ["scenarios"],
        target_hashes=target,
        source_hashes=source,
    )


# -- body digests -----------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "same"),
    [
        # moving the code, reformatting and comments keep the digest
        (BODY_A, "# moved here\n\n\ndef f(x):\n    return (x + 1)\n", True),
        # a docstring is not part of the body
        (BODY_A, 'def f(x):\n    """Doc."""\n    return x + 1\n', True),
        # a changed statement changes the digest
        (BODY_A, BODY_B, False),
        # a changed name inside the body changes the digest
        (BODY_A, "def f(x):\n    return x + 2\n", False),
    ],
)
def test_body_digest_ignores_position_and_docstring_only(
    before: str, after: str, same: bool
) -> None:
    assert (
        ar.function_body_hashes(before)["f"] == ar.function_body_hashes(after)["f"]
    ) is same


def test_body_digest_names_nested_and_method_functions_like_the_inventory() -> None:
    src = (
        "class C:\n"
        "    def m(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner\n"
    )
    assert set(ar.function_body_hashes(src)) == {"C.m", "C.m.<locals>.inner"}


# -- move matching ----------------------------------------------------------


def test_moved_function_with_the_same_body_is_not_new() -> None:
    baseline = ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None)
    result = _ratchet(
        {"new/m.py::f"},
        baseline,
        target={"new/m.py::f": "h1"},
        source={"old/m.py::f": "h1"},
    )
    assert result.grown == []
    assert result.shrunk == []
    assert result.moved == [ar.Move("old/m.py::f", "new/m.py::f", "body")]
    assert result.ok


def test_moved_function_with_a_changed_body_is_new() -> None:
    baseline = ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None)
    result = _ratchet(
        {"new/m.py::f"},
        baseline,
        target={"new/m.py::f": "h2"},
        source={"old/m.py::f": "h1"},
    )
    assert result.grown == ["new/m.py::f"]
    assert result.moved == []
    assert result.shrunk == ["old/m.py::f"]
    assert not result.ok


def test_renamed_function_is_new_not_moved() -> None:
    baseline = ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None)
    result = _ratchet(
        {"old/m.py::g"},
        baseline,
        target={"old/m.py::g": "h1"},
        source={"old/m.py::f": "h1"},
    )
    assert result.grown == ["old/m.py::g"]
    assert result.moved == []


def test_same_name_different_body_in_new_path_is_new() -> None:
    baseline = ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None)
    result = _ratchet(
        {"new/m.py::f"},
        baseline,
        target={"new/m.py::f": "h2"},
        source={"old/m.py::f": "h1"},
    )
    assert result.grown == ["new/m.py::f"]


def test_name_only_pair_is_accepted_when_unambiguous() -> None:
    baseline = ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None)
    result = _ratchet({"new/m.py::f"}, baseline)
    assert result.moved == [ar.Move("old/m.py::f", "new/m.py::f", "name")]
    assert result.grown == []


def test_name_only_pair_is_refused_when_two_candidates_share_the_name() -> None:
    baseline = ar.unreached_baseline_payload(
        {"a/m.py::f", "b/m.py::f"}, ["scenarios"], None
    )
    result = _ratchet({"c/m.py::f"}, baseline)
    assert result.moved == []
    assert result.grown == ["c/m.py::f"]


def test_name_only_pair_is_refused_when_two_new_functions_share_the_name() -> None:
    baseline = ar.unreached_baseline_payload({"a/m.py::f"}, ["scenarios"], None)
    result = _ratchet({"b/m.py::f", "c/m.py::f"}, baseline)
    assert result.moved == []
    assert result.grown == ["b/m.py::f", "c/m.py::f"]


def test_a_still_unreached_source_does_not_donate_a_move() -> None:
    # a/m.py::f stays unreached, so b/m.py::f is a copy, not a move of it
    baseline = ar.unreached_baseline_payload({"a/m.py::f"}, ["scenarios"], None)
    result = _ratchet(
        {"a/m.py::f", "b/m.py::f"},
        baseline,
        target={"b/m.py::f": "h1"},
        source={"a/m.py::f": "h1"},
    )
    assert result.grown == ["b/m.py::f"]
    assert result.moved == []


def test_two_sibling_moves_pair_one_to_one_by_body() -> None:
    baseline = ar.unreached_baseline_payload(
        {"a/m.py::f", "a/m.py::g"}, ["scenarios"], None
    )
    result = _ratchet(
        {"b/m.py::f", "b/m.py::g"},
        baseline,
        target={"b/m.py::f": "hf", "b/m.py::g": "hg"},
        source={"a/m.py::f": "hf", "a/m.py::g": "hg"},
    )
    assert sorted(result.moved, key=lambda m: m.target) == [
        ar.Move("a/m.py::f", "b/m.py::f", "body"),
        ar.Move("a/m.py::g", "b/m.py::g", "body"),
    ]
    assert result.grown == []


def test_a_baseline_without_body_hashes_still_loads() -> None:
    # the committed format before digests were stored
    old = {"sources": ["scenarios"], "unreached": ["a/m.py::f"], "count": 1}
    assert _ratchet({"b/m.py::f"}, old).moved[0].matched_by == "name"


# -- apply / write-moves ----------------------------------------------------


def test_write_moves_rewrites_only_the_moved_entries() -> None:
    baseline = {
        "sources": ["scenarios"],
        "revision": "abc",
        "count": 3,
        "unreached": ["a/m.py::f", "a/m.py::keep", "a/m.py::gone"],
    }
    result = _ratchet(
        {"b/m.py::f", "a/m.py::keep", "b/m.py::fresh"},
        baseline,
        target={"b/m.py::f": "hf"},
        source={"a/m.py::f": "hf"},
    )
    out = ar.apply_moves(baseline, result, {"b/m.py::f": "hf"})
    assert out["unreached"] == ["a/m.py::gone", "a/m.py::keep", "b/m.py::f"]
    assert out["count"] == 3
    assert out["body_hashes"] == {"b/m.py::f": "hf"}
    assert out["revision"] == "abc"
    assert "b/m.py::fresh" not in out["unreached"]


def test_ratchet_cli_write_moves_then_gates_only_real_growth(tmp_path: Path) -> None:
    rec = {
        "schema_version": up.SCHEMA_VERSION,
        "sources": ["scenarios"],
        "revision": None,
        "failures": {},
        "inventory": {"new/m.py": ["f"], "other/m.py": ["g"]},
        "runs": {},
    }
    rec_path = tmp_path / "rec.json"
    rec_path.write_text(json.dumps(rec))
    base_path = tmp_path / "base.json"
    base_path.write_text(
        json.dumps(ar.unreached_baseline_payload({"old/m.py::f"}, ["scenarios"], None))
    )
    assert (
        up.main(
            ["ratchet", str(rec_path), "--baseline", str(base_path), "--write-moves"]
        )
        == 0
    )
    written = json.loads(base_path.read_text())
    # the name-only pair is rewritten to its new path; the genuinely new
    # other/m.py::g is not added to the baseline
    assert written["unreached"] == ["new/m.py::f"]
    assert written["count"] == 1
    # the new function is still growth, so the strict gate fails on it alone
    assert (
        up.main(["ratchet", str(rec_path), "--baseline", str(base_path), "--strict"])
        == 1
    )
