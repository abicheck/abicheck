"""Tests for the environment-derived header-backend plan (extract/headers/backend_plan.py).

The plan is the one reader of the AST-frontend environment flags. Consumers
take a plan, so most cases here build one directly; the drift checks prove
the legacy names in ``toolchain`` and ``header_ast_backend`` still answer
from that same reader.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.dumper import _castxml_fallback_reason
from abicheck.errors import SnapshotError, UnsupportedCastxmlVersionError
from abicheck.extract.header_ast_backend import _resolve_header_backend
from abicheck.extract.headers import toolchain
from abicheck.extract.headers.backend_plan import (
    PINNABLE_BACKENDS,
    BackendPlan,
    ast_frontend_pin,
    auto_selected,
)

_ENV_VARS = (
    "ABICHECK_AST_FRONTEND",
    "ABICHECK_ALLOW_AST_FALLBACK",
    "ABICHECK_ALLOW_UNSUPPORTED_CASTXML",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_default_plan_is_unpinned_and_opts_out_of_fallback(clean_env) -> None:
    plan = BackendPlan.from_environment()
    assert plan == BackendPlan(
        ast_frontend_pin="",
        ast_fallback_enabled=False,
        allow_unsupported_castxml=False,
    )
    assert plan.auto_selected("auto")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        (" TRUE ", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("off", False),
        ("maybe", False),
    ],
)
def test_flags_are_read_through_the_shared_parser(
    clean_env, raw: str, expected: bool
) -> None:
    clean_env.setenv("ABICHECK_ALLOW_AST_FALLBACK", raw)
    clean_env.setenv("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", raw)
    plan = BackendPlan.from_environment()
    assert plan.ast_fallback_enabled is expected
    assert plan.allow_unsupported_castxml is expected


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("", ""), ("  CastXML ", "castxml"), ("Clang", "clang"), ("bogus", "bogus")],
)
def test_pin_is_normalized(clean_env, raw: str, normalized: str) -> None:
    clean_env.setenv("ABICHECK_AST_FRONTEND", raw)
    assert ast_frontend_pin() == normalized
    assert BackendPlan.from_environment().ast_frontend_pin == normalized


@pytest.mark.parametrize(
    ("backend", "pin", "expected"),
    [
        ("auto", "", True),
        (None, "", True),
        ("  AUTO ", "", True),
        ("auto", "bogus", True),  # an unknown pin is not a pin
        ("auto", "castxml", False),
        ("auto", "clang", False),
        ("auto", "hybrid", False),
        ("castxml", "", False),
        ("clang", "", False),
    ],
)
def test_auto_selected_is_unpinned_auto(backend, pin, expected) -> None:
    assert auto_selected(backend, pin) is expected
    assert (
        BackendPlan(ast_frontend_pin=pin).auto_selected(backend or "auto") is expected
    )


def test_pinnable_backends_match_the_frontend_vocabulary() -> None:
    assert frozenset({"castxml", "clang", "hybrid"}) == PINNABLE_BACKENDS


def test_plan_is_frozen() -> None:
    plan = BackendPlan()
    with pytest.raises(AttributeError):
        plan.ast_fallback_enabled = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("pin", "fallback", "unsupported"),
    list(
        itertools.product(
            ["", "castxml", "clang", "hybrid", "junk"],
            ["", "0", "1"],
            ["", "0", "1"],
        )
    ),
)
def test_legacy_readers_agree_with_the_plan(
    clean_env, pin: str, fallback: str, unsupported: str
) -> None:
    """Every legacy reader answers from the same plan, across the whole env grid."""
    clean_env.setenv("ABICHECK_AST_FRONTEND", pin)
    clean_env.setenv("ABICHECK_ALLOW_AST_FALLBACK", fallback)
    clean_env.setenv("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", unsupported)

    plan = BackendPlan.from_environment()
    assert toolchain._ast_fallback_enabled() is plan.ast_fallback_enabled
    assert (
        toolchain._allow_unsupported_castxml_enabled() is plan.allow_unsupported_castxml
    )
    for backend in ("auto", "castxml", "clang", "hybrid", None):
        assert toolchain._auto_ast_fallback_eligible(backend) is plan.auto_selected(
            backend or "auto"
        )
    # Backend resolution honours the same pin the plan reads.
    if pin in PINNABLE_BACKENDS:
        assert _resolve_header_backend(None) == pin
    else:
        assert _resolve_header_backend(None) == "castxml"


def test_disabled_plan_refuses_fallback_without_probing_clang(clean_env) -> None:
    """A plan with fallback off raises the annotated error. It never reaches the
    clang probe, so no environment and no patching is needed."""
    exc = UnsupportedCastxmlVersionError("castxml 0.6 is outside the supported range")
    plan = BackendPlan(ast_fallback_enabled=False)
    with pytest.raises(UnsupportedCastxmlVersionError, match="fallback is disabled"):
        _castxml_fallback_reason(
            exc,
            auto_selected=True,
            compiler="c++",
            gcc_path=None,
            gcc_prefix=None,
            plan=plan,
        )


def test_non_auto_request_never_falls_back(clean_env) -> None:
    exc = SnapshotError("castxml: toolchain version mismatch")
    reason = _castxml_fallback_reason(
        exc,
        auto_selected=False,
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        plan=BackendPlan(ast_fallback_enabled=True),
    )
    assert reason is None
