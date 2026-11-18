"""The implicit-``this`` extension of E13/E14 (``formal/MEMBER_FRAGMENT.md``, E15).

``formal/lean/AbiLean/MemberFragment.lean`` proves that prepending ``this``
keeps every member-to-member answer of the E13/E14 call models, and when a
static <-> non-static change keeps the call interface. Its export
(``formal/lean/member_fragment_cases.json``) is checked here, case by case,
against the execution oracle's committed observations
(``formal/member_fragment_observed.json``, g++ and clang++) and against the
space and rule enumerated independently with ``itertools``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"
EXPORT = json.loads(
    (FORMAL / "lean" / "member_fragment_cases.json").read_text(encoding="utf-8")
)
OBSERVED = json.loads(
    (FORMAL / "member_fragment_observed.json").read_text(encoding="utf-8")
)
TYPES = ("int", "long", "double", "void *")


def _space() -> list[tuple[str, str, list[str]]]:
    return [
        (o, n, list(ps))
        for o, n in (("static", "instance"), ("instance", "static"))
        for k in range(4)
        for ps in itertools.product(TYPES, repeat=k)
    ]


def test_export_enumerates_the_oracle_space_in_order() -> None:
    assert [r[:3] for r in EXPORT["rows"]] == [list(t) for t in _space()]
    assert len(EXPORT["rows"]) == 170


def test_observed_rows_match_their_checksum() -> None:
    rows = OBSERVED["rows"]
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == OBSERVED["rows_sha256"]


@pytest.mark.parametrize("i", range(170))
def test_lean_equals_execution(i: int) -> None:
    assert EXPORT["rows"][i] == OBSERVED["rows"][i]


@pytest.mark.parametrize(("old", "new", "params"), _space())
def test_lean_equals_independent_rule(old: str, new: str, params: list[str]) -> None:
    # static -> instance inserts `this` (never compatible); instance -> static
    # removes it, which moves every INTEGER argument down one register.
    expected = old == "instance" and all(t == "double" for t in params)
    row = next(r for r in EXPORT["rows"] if r[:3] == [old, new, params])
    assert row[3] is expected
