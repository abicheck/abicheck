"""ADR-050 D2 — the comparability gate's fingerprint-authenticity check, across
BOTH key sets :func:`compute_extraction_contract` may have hashed over.

Split out of ``test_comparability_gate.py`` (which was itself split out of
``test_comparability.py`` for the same reason): that file crossed the
file-size hard cap once these landed. Scope here is exactly the
extended-key-set cases -- ``frontend_context_kind`` (ADR-050 D5) and
``translation_units`` (D6) -- plus the guard that accepting either key set did
not weaken the forgery check.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.comparability import (
    ComparabilityMismatch,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.model import AbiSnapshot, ExtractionContract


def _snap(contract: ExtractionContract | None, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", contract=contract, **kwargs)


# ---------------------------------------------------------------------------
# Fingerprint authenticity across BOTH key sets a producer may have used
# (CodeRabbit review, PR #697). compute_extraction_contract hashes over the
# extended key set whenever the dump carried the extra field --
# frontend_context_kind for a DPC++-capable frontend (ADR-050 D5),
# translation_units for a --dump-manifest dump (D6) -- so a gate that only
# ever recomputed over the base set declared every such contract inauthentic
# and refused before any carve-out ran.
# ---------------------------------------------------------------------------


def test_frontend_context_contract_reproduces_its_own_profile_fingerprint():
    """A real DPC++ contract is authentic, so the gate reaches the carve-outs
    and names the field that actually differs."""
    old = _snap(
        compute_extraction_contract(
            compiler_family="clang",
            l2_frontend_ran=True,
            frontend_context_kind="host",
            language_standard="c++17",
        )
    )
    new = _snap(
        compute_extraction_contract(
            compiler_family="clang",
            l2_frontend_ran=True,
            frontend_context_kind="host",
            language_standard="c++20",
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
    # The real cause, not "profile_fields do not reproduce its own
    # profile_fingerprint".
    assert "language_standard" in result.reason
    assert "do not reproduce" not in result.reason


def test_differing_frontend_context_kind_is_not_reported_as_unrecognized():
    """frontend_context_kind is a field this build knows about. A host-vs-device
    pair is still not comparable -- no carve-out claims that field -- but it is
    named, not reported as a key this version does not recognize."""
    old = _snap(
        compute_extraction_contract(
            compiler_family="clang", l2_frontend_ran=True, frontend_context_kind="host"
        )
    )
    new = _snap(
        compute_extraction_contract(
            compiler_family="clang",
            l2_frontend_ran=True,
            frontend_context_kind="device",
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
    assert "frontend_context_kind" in result.reason
    assert "does not recognize" not in result.reason


def test_manifest_contract_reproduces_its_own_scope_fingerprint():
    """A --dump-manifest contract growing its declared header set additively is
    comparable, the same as the non-manifest case."""
    old = _snap(
        compute_extraction_contract(
            public_header_paths=[Path("/proj/a.h"), Path("/proj/b.h")],
            manifest_tu_scope='["tu1"]',
        )
    )
    new = _snap(
        compute_extraction_contract(
            public_header_paths=[
                Path("/proj/a.h"),
                Path("/proj/b.h"),
                Path("/proj/c.h"),
            ],
            manifest_tu_scope='["tu1"]',
        )
    )
    assert check_contracts_comparable(old, new, diagnostic=True) is None


def test_fabricated_fingerprint_still_rejected_under_both_key_sets():
    """Accepting either key set must not accept an arbitrary hash: the
    authenticity check is still what catches a stale/fabricated fingerprint."""
    real = compute_extraction_contract(
        compiler_family="clang", l2_frontend_ran=True, frontend_context_kind="host"
    )
    assert real.profile_fingerprint is not None
    old = _snap(
        ExtractionContract(
            profile_fingerprint="sha256:" + "0" * 64,
            profile_fields=dict(real.profile_fields),
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="sha256:" + "1" * 64,
            profile_fields=dict(real.profile_fields),
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
    assert "do not reproduce" in result.reason
