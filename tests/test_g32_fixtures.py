"""G32 Phase 0: assert the raw ADR-050/G32 fixtures under tests/fixtures/g32/
are present, non-empty, and structurally sane. No production code reads
these yet (Phase 0 is fixture capture only) -- see
tests/fixtures/g32/README.md for what each fixture is for and, for the
DPC++ multi-document capture, why it is not present yet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_G32_DIR = Path(__file__).parent / "fixtures" / "g32"

_EXPECTED_FILES = [
    "README.md",
    "plain_clang/header.h",
    "plain_clang/ast_dump.json",
    "odr_safe/tu_a.h",
    "odr_safe/tu_b.h",
    "odr_conflict/tu_a.h",
    "odr_conflict/tu_b.h",
    "stl_noise/public.h",
    "scope_drift/old/a.h",
    "scope_drift/old/b.h",
    "scope_drift/new/a.h",
    "scope_drift/new/b.h",
    "scope_drift/new/c.h",
]


@pytest.mark.parametrize("relpath", _EXPECTED_FILES)
def test_fixture_exists_and_nonempty(relpath: str) -> None:
    path = _G32_DIR / relpath
    assert path.is_file(), f"missing G32 fixture: {relpath}"
    assert path.stat().st_size > 0, f"empty G32 fixture: {relpath}"
    # Every fixture must at least be readable text (the AST capture is
    # UTF-8 JSON too); a decode failure means the file isn't what it claims.
    path.read_text(encoding="utf-8")


def test_plain_clang_ast_dump_is_single_json_document() -> None:
    """Contrast case for Phase D's future multi-document stream parser: this
    capture is one ordinary `clang -ast-dump=json` invocation and must parse
    as exactly one JSON value, unlike the (not yet captured) DPC++
    host+device concatenated stream."""
    data = json.loads((_G32_DIR / "plain_clang" / "ast_dump.json").read_text())
    assert data["kind"] == "TranslationUnitDecl"


def test_odr_safe_pair_is_forward_decl_then_definition() -> None:
    tu_a = (_G32_DIR / "odr_safe" / "tu_a.h").read_text()
    tu_b = (_G32_DIR / "odr_safe" / "tu_b.h").read_text()
    assert "struct Point;" in tu_a
    assert "struct Point {" not in tu_a
    assert "struct Point {" in tu_b


def test_odr_conflict_pair_disagrees_on_return_type() -> None:
    tu_a = (_G32_DIR / "odr_conflict" / "tu_a.h").read_text()
    tu_b = (_G32_DIR / "odr_conflict" / "tu_b.h").read_text()
    assert "int compute(int value);" in tu_a
    assert "double compute(int value);" in tu_b


def test_stl_noise_header_declares_public_function_by_value() -> None:
    header = (_G32_DIR / "stl_noise" / "public.h").read_text()
    assert "std::vector<int>" in header
    assert "sum_all" in header


def test_scope_drift_new_side_is_old_side_plus_one_extra_tu() -> None:
    old_headers = {p.name for p in (_G32_DIR / "scope_drift" / "old").glob("*.h")}
    new_headers = {p.name for p in (_G32_DIR / "scope_drift" / "new").glob("*.h")}
    assert old_headers == {"a.h", "b.h"}
    assert new_headers == old_headers | {"c.h"}
    # The shared TUs must be byte-identical -- the only real difference
    # between the two sides is the presence of the extra TU, not their content.
    for name in old_headers:
        old_text = (_G32_DIR / "scope_drift" / "old" / name).read_text()
        new_text = (_G32_DIR / "scope_drift" / "new" / name).read_text()
        assert old_text == new_text, (
            f"{name} unexpectedly differs between old/ and new/"
        )
