"""The resolved-request builder maps every classification input onto the request.

Tests the workflow function directly (no Click). The oracle is the contract in
its docstring: empty forced symbols and an unset allowlist mean ``None``, any
other iterable becomes a ``frozenset``, ``pattern_verdicts`` is always
``True``, and every other field is copied through unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.service import CompareRequest, InputSpec
from abicheck.workflows.compare.invocation import build_resolved_compare_request


def _base() -> CompareRequest:
    return CompareRequest(
        old=InputSpec(path=Path("a.so")), new=InputSpec(path=Path("b.so"))
    )


def _build(base: CompareRequest, **overrides: object) -> CompareRequest:
    kwargs: dict[str, object] = dict(
        policy="strict_abi",
        policy_file_path=None,
        suppress=None,
        strict_suppressions=False,
        require_justification=False,
        scope_public=True,
        force_public=None,
        post_manifest_allowlist=None,
        collapse_versioned_symbols=False,
        env_matrix=None,
        diagnostic_comparison=False,
        require_same_generation=False,
        contract_evaluation=True,
        contract_mode=None,
        depth=None,
        probe_matrix_old=None,
        probe_matrix_new=None,
        abi3_floor=None,
    )
    kwargs.update(overrides)
    return build_resolved_compare_request(base, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("forced", "expected"),
    [
        (None, None),
        ((), None),
        (["foo"], frozenset({"foo"})),
        (("foo", "bar", "foo"), frozenset({"foo", "bar"})),
    ],
)
def test_force_public_symbols(forced: object, expected: frozenset[str] | None) -> None:
    assert _build(_base(), force_public=forced).force_public_symbols == expected


@pytest.mark.parametrize(
    ("allowlist", "expected"),
    [
        (None, None),
        ([], frozenset()),
        (["pp_kernel"], frozenset({"pp_kernel"})),
    ],
)
def test_post_manifest_allowlist(
    allowlist: object, expected: frozenset[str] | None
) -> None:
    built = _build(_base(), post_manifest_allowlist=allowlist)
    assert built.public_surface_allowlist == expected


def test_pattern_verdicts_is_always_on() -> None:
    assert _build(_base()).pattern_verdicts is True


def test_passthrough_fields_are_copied_unchanged() -> None:
    env = object()
    built = _build(
        _base(),
        policy="sdk_vendor",
        policy_file_path=Path("policy.yaml"),
        strict_suppressions=True,
        require_justification=True,
        scope_public=False,
        collapse_versioned_symbols=True,
        env_matrix=env,
        diagnostic_comparison=True,
        require_same_generation=True,
        contract_evaluation=False,
        contract_mode="exports",
        depth="source",
        abi3_floor=(3, 11),
    )
    assert built.policy == "sdk_vendor"
    assert built.policy_file_path == Path("policy.yaml")
    assert built.strict_suppressions is True
    assert built.require_justification is True
    assert built.scope_public is False
    assert built.collapse_versioned_symbols is True
    assert built.env_matrix is env
    assert built.diagnostic_comparison is True
    assert built.require_same_generation is True
    assert built.contract_evaluation is False
    assert built.contract_mode == "exports"
    assert built.depth == "source"
    assert built.abi3_floor == (3, 11)


def test_operands_of_the_resolution_request_are_kept() -> None:
    base = _base()
    built = _build(base)
    assert built.old == base.old
    assert built.new == base.new
