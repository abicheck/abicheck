"""E-S2 (docs/contribute/plans/cli-cleanup-phase-two.md, Block 5) --
``ComparabilityMismatch.dimensions``, the per-dimension comparability record
that replaces the previous "carries a single ``kind``" data model.

These are unit tests of the *data layer* only (what
``check_contracts_comparable``'s ``diagnostic=True`` mode attaches to the
returned descriptor) -- not of report/detector consumption, which is E-S2's
own explicitly-deferred next slice (see ``ComparabilityMismatch``'s own
docstring in ``abicheck/comparability.py``).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.comparability import (
    COMPARABILITY_DIMENSIONS,
    ComparabilityMismatch,
    IncludeDir,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.model import AbiSnapshot, ExtractionContract, Function, Visibility


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _snap(contract: ExtractionContract | None, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", contract=contract, **kwargs)


def _scoped_snap(
    version: str, from_headers: bool, dependency_scope: str | None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        from_headers=from_headers,
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
        dependency_scope=dependency_scope,
    )


def test_scope_mismatch_dimensions_are_declaration_only(tmp_path):
    # A genuine, non-additive declared-header-set drift: only the
    # "headers" scope field differs, so only "declaration" is affected --
    # a scope mismatch says nothing about runtime behavior or the binary's
    # own exported-symbol identity.
    a = _write(tmp_path / "v1" / "a.h", "int g(void);\n")
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[a, new_h]))
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "scope"
    assert result.dimensions == frozenset({"declaration"})


def test_profile_mismatch_dimensions_map_to_differing_fields(tmp_path):
    # Only include-directory content differs (a struct field added) --
    # profile_fields "include_sequence" carve-out doesn't apply here (no
    # additive owned-growth shape), so the mismatch is genuinely reported,
    # and its dimensions are exactly what the differing field maps to.
    dep_old = _write(tmp_path / "d1" / "dep.h", "struct Dep { int x; };\n")
    dep_new = _write(tmp_path / "d2" / "dep.h", "struct Dep { int x; int y; };\n")
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d1")],
            depfile_resolved_paths=[dep_old],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d2")],
            depfile_resolved_paths=[dep_new],
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
    # include_sequence is the only PROFILE_FIELD_KEYS entry this pair
    # actually differs on -- and it maps to "declaration" only.
    assert result.dimensions == frozenset({"declaration"})


def test_dependency_scope_mismatch_dimensions_are_declaration_and_layout():
    old = _scoped_snap("1.0", from_headers=True, dependency_scope="filtered")
    new = _scoped_snap("2.0", from_headers=True, dependency_scope="full")
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "dependency_scope"
    assert result.dimensions == frozenset({"declaration", "layout"})


def test_opaque_scope_mismatch_falls_back_to_every_scope_dimension():
    # A fabricated/deserialized contract whose scope_fingerprint doesn't
    # reproduce from its own scope_fields -- nothing here can be attributed
    # to a specific field, so every dimension the scope axis can ever
    # affect is reported unverified (fail-closed, same as `kind`/`reason`).
    old_contract = ExtractionContract(
        profile_fingerprint=None,
        scope_fingerprint="not-a-real-hash-old",
        profile_fields={},
        scope_fields={"headers": '["a.h"]', "public_header_dirs": "[]"},
    )
    new_contract = ExtractionContract(
        profile_fingerprint=None,
        scope_fingerprint="not-a-real-hash-new",
        profile_fields={},
        scope_fields={"headers": '["a.h", "b.h"]', "public_header_dirs": "[]"},
    )
    old = _snap(old_contract)
    new = _snap(new_contract)
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "scope"
    assert result.dimensions == frozenset({"declaration"})


def test_opaque_profile_mismatch_falls_back_to_every_profile_dimension():
    old_contract = ExtractionContract(
        profile_fingerprint="not-a-real-hash-old",
        scope_fingerprint=None,
        profile_fields={"compiler_family": "gcc"},
        scope_fields={},
    )
    new_contract = ExtractionContract(
        profile_fingerprint="not-a-real-hash-new",
        scope_fingerprint=None,
        profile_fields={"compiler_family": "clang"},
        scope_fields={},
    )
    old = _snap(old_contract)
    new = _snap(new_contract)
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
    # Every dimension a recognized profile field can affect -- the
    # unauthenticated fingerprint means no single field can be trusted.
    assert result.dimensions == frozenset({"declaration", "layout", "runtime"})


def test_comparable_pair_produces_no_mismatch_at_all(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "foo.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[new_h]))
    assert check_contracts_comparable(old, new, diagnostic=True) is None


# ---------------------------------------------------------------------------
# Property: whatever this module attaches, it's a real subset of the
# published dimension vocabulary, and never claims authority it doesn't have.
# ---------------------------------------------------------------------------


def test_every_reported_dimension_is_in_the_published_vocabulary(tmp_path):
    a = _write(tmp_path / "v1" / "a.h", "int g(void);\n")
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[a, new_h]))
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.dimensions <= COMPARABILITY_DIMENSIONS
    # ADR-050's contract (scope/profile/dependency-scope fingerprints) never
    # speaks to the binary's own exported-symbol-table identity (L0/L1) or
    # to L4/L5 build-source-graph evidence -- a mismatch here must never
    # claim authority over either.
    assert "symbol" not in result.dimensions
    assert "source" not in result.dimensions
