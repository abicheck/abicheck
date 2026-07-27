"""G32 Phase 0: assert the raw ADR-050/G32 fixtures under tests/fixtures/g32/
are present, non-empty, and structurally sane. No production code reads
these yet (Phase 0 is fixture capture only) -- see
tests/fixtures/g32/README.md for what each fixture is for, including the
real DPC++ multi-document capture Phase D's stream parser is designed
against."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_G32_DIR = Path(__file__).parent / "fixtures" / "g32"

_EXPECTED_FILES = [
    "README.md",
    "plain_clang/header.h",
    "plain_clang/ast_dump.json",
    "dpcpp/header.h",
    "dpcpp/ast_dump.json",
    "dpcpp/compiler_invocation.log",
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


def test_dpcpp_ast_dump_is_two_concatenated_json_documents() -> None:
    """Contrast case for `test_plain_clang_ast_dump_is_single_json_document`:
    a real `icpx -fsycl` capture is genuinely TWO back-to-back
    `TranslationUnitDecl` JSON documents with no separator between them
    (`...}{...`), the exact document-boundary-detection problem Phase D's
    `sycl_context.py` decoder exists to solve. Uses `json.JSONDecoder.raw_decode`
    (real streaming decode) rather than a naive bracket/string split, matching
    what the production decoder must do -- a bracket counter would already be
    wrong here in general (braces inside a JSON string value), even though
    this particular capture's string values happen not to contain one."""
    raw = (_G32_DIR / "dpcpp" / "ast_dump.json").read_text()
    decoder = json.JSONDecoder()
    docs = []
    pos = 0
    while pos < len(raw):
        pos += len(raw[pos:]) - len(raw[pos:].lstrip())  # skip inter-doc whitespace
        if pos >= len(raw):
            break
        doc, end = decoder.raw_decode(raw, pos)
        docs.append(doc)
        if len(docs) == 1:
            # The inter-doc whitespace skip above must not mask a genuine
            # separator: the fixture's defining property is that the two
            # documents are truly back-to-back with nothing between them
            # (CodeRabbit review) -- a newline-delimited stream would still
            # pass without this assertion.
            assert raw[end] == "{", "documents must be directly concatenated"
        pos = end
    assert len(docs) == 2
    assert all(doc["kind"] == "TranslationUnitDecl" for doc in docs)
    # Both passes still see the header's own declarations.
    for doc in docs:
        names = {child.get("name") for child in doc["inner"]}
        assert "Point" in names
        assert "add" in names


def test_dpcpp_compiler_invocation_log_correlates_kind_and_target() -> None:
    """The raw AST JSON alone carries no host/device `kind` -- it's ordinary
    clang AST-dump JSON, oblivious to the driver-level SYCL split. The
    correlating `-cc1 ... -triple <T> ... -fsycl-is-(host|device)` lines live
    on stderr (`compiler_invocation.log`), in the same order as their
    stdout documents: first line is the device pass, second is host."""
    log = (_G32_DIR / "dpcpp" / "compiler_invocation.log").read_text()
    # " -cc1 " alone also matches the unrelated "clang -cc1 version ..." banner
    # line printed after each real invocation; only the real invocation line
    # also names a -triple.
    cc1_lines = [
        line for line in log.splitlines() if " -cc1 " in line and "-triple" in line
    ]
    assert len(cc1_lines) == 2
    assert "-fsycl-is-device" in cc1_lines[0]
    assert "-triple spir64-unknown-unknown" in cc1_lines[0]
    assert "-fsycl-is-host" in cc1_lines[1]
    assert "-triple x86_64-unknown-linux-gnu" in cc1_lines[1]


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
