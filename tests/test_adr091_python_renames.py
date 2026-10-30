"""ADR-091 Phase 4 (Python): the renamed typed-API spellings and the R2/R10
CLI-vs-typed equivalence at the resolved request.

A renamed keyword or field is renamed outright (ADR-091 D7's no-transition
amendment): the old spelling raises ``TypeError``, the new one is accepted.
``policy`` and ``contract`` are compared at the resolved request, since the CLI
and the typed API reach a comparison through different routes.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
from pathlib import Path
from typing import Any

import click
import pytest

from abicheck import service
from abicheck.cli_options import _resolve_policy_operand
from abicheck.errors import ValidationError
from abicheck.frontends.cli.options.contract import resolve_contract_domain
from abicheck.frontends.cli.options.params import BUILTIN_POLICY_PROFILES
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import (
    CompareRequest,
    ContractDomain,
    DumpRequest,
)

#: old spelling -> (owner, new spelling). Every entry raises TypeError when the
#: old name is passed, and binds when the new one is.
_RENAMED_KEYWORDS: list[tuple[str, str, str]] = [
    ("run_compare", "old_version", "old_label"),
    ("run_compare", "new_version", "new_label"),
    ("run_compare", "policy_file_path", "policy"),
    ("run_compare", "acknowledgments_path", "acknowledgments"),
    ("run_compare", "force_public_symbols", "public_symbols"),
    ("run_compare", "enable_debuginfod", "debuginfod"),
    ("run_compare", "include_dependencies", "include_system_declarations"),
    ("run_compare", "contract_evaluation", "contract"),
    ("run_compare", "contract_mode", "contract"),
    ("run_dump", "enable_debuginfod", "debuginfod"),
    ("run_dump", "include_dependencies", "include_system_declarations"),
    ("run_dump", "header_backend", "frontend"),
]

#: Request dataclass field renames: owner -> (old, new).
_RENAMED_FIELDS: list[tuple[type, str, str]] = [
    (CompareRequest, "policy_file_path", "policy"),
    (CompareRequest, "acknowledgments_path", "acknowledgments"),
    (CompareRequest, "force_public_symbols", "public_symbols"),
    (CompareRequest, "enable_debuginfod", "debuginfod"),
    (CompareRequest, "follow_dependencies", "follow_deps"),
    (CompareRequest, "contract_evaluation", "contract"),
    (CompareRequest, "contract_mode", "contract"),
    (DumpRequest, "enable_debuginfod", "debuginfod"),
    (DumpRequest, "follow_dependencies", "follow_deps"),
    (InputSpec, "include_dependencies", "include_system_declarations"),
]

_FUNCTIONS = {"run_compare": service.run_compare, "run_dump": service.run_dump}


@pytest.mark.parametrize(("fn", "old", "new"), _RENAMED_KEYWORDS)
def test_old_keyword_is_refused_and_new_keyword_is_accepted(
    fn: str, old: str, new: str
) -> None:
    signature = inspect.signature(_FUNCTIONS[fn])
    with pytest.raises(TypeError):
        signature.bind_partial(**{old: None})
    signature.bind_partial(**{new: None})


@pytest.mark.parametrize(("owner", "old", "new"), _RENAMED_FIELDS)
def test_old_field_is_refused_and_new_field_is_declared(
    owner: type, old: str, new: str
) -> None:
    names = {f.name for f in dataclasses.fields(owner)}
    assert old not in names
    assert new in names


def test_old_request_keyword_is_refused_at_construction() -> None:
    with pytest.raises(TypeError):
        CompareRequest(  # type: ignore[call-arg]
            old=InputSpec(path=Path("a.so")),
            new=InputSpec(path=Path("b.so")),
            policy_file_path=None,
        )


def test_new_request_keyword_is_accepted_at_construction() -> None:
    request = CompareRequest(
        old=InputSpec(path=Path("a.so")),
        new=InputSpec(path=Path("b.so")),
        public_symbols=frozenset({"f"}),
        debuginfod=True,
        follow_deps=True,
    )
    assert request.public_symbols == frozenset({"f"})
    assert request.debuginfod is True
    assert request.follow_deps is True


def test_the_contract_value_set_is_the_cli_choice_set() -> None:
    # ADR-091 R10: the typed value set is the CLI's `--contract` choices, `auto` included.
    assert typing.get_args(ContractDomain) == ("public", "exports", "all", "auto")


# --- R2: `--policy NAME|PATH` vs the typed `policy` ------------------------


def _cli_policy(value: Any) -> tuple[str, Path | None]:
    """The CLI's resolution of ``--policy VALUE`` (its own click callback)."""
    ctx = click.Context(click.Command("compare"))
    param = click.Option(["--policy"])
    profile = _resolve_policy_operand(ctx, param, value)
    return profile, ctx.params["policy_file_path"]


def _typed_policy(value: Any) -> tuple[str, Path | None]:
    request = CompareRequest(
        old=InputSpec(path=Path("a.so")),
        new=InputSpec(path=Path("b.so")),
        policy=value,
    )
    return request.policy_profile, request.policy_document


@pytest.mark.parametrize("profile", sorted(BUILTIN_POLICY_PROFILES))
def test_policy_profile_name_resolves_the_same_on_both_routes(profile: str) -> None:
    assert _cli_policy(profile) == _typed_policy(profile) == (profile, None)


def test_policy_document_path_resolves_the_same_on_both_routes(tmp_path: Path) -> None:
    document = tmp_path / "policy.yaml"
    document.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
    expected = ("strict_abi", document)
    assert _cli_policy(str(document)) == expected
    assert _typed_policy(document) == expected
    assert _typed_policy(str(document)) == expected


def test_packaged_builtin_document_resolves_the_same_on_both_routes() -> None:
    cli_profile, cli_document = _cli_policy("security")
    typed_profile, typed_document = _typed_policy("security")
    assert cli_profile == typed_profile == "strict_abi"
    assert cli_document == typed_document
    assert cli_document is not None and cli_document.is_file()


def test_an_unknown_policy_is_refused_on_both_routes() -> None:
    with pytest.raises(click.BadParameter):
        _cli_policy("no-such-policy-anywhere")
    with pytest.raises(ValidationError, match="unknown policy"):
        _typed_policy("no-such-policy-anywhere")


# --- R10: `--contract` vs the typed `contract` ------------------------------


def _cli_contract(value: Any) -> Any:
    ctx = click.Context(click.Command("compare"))
    return resolve_contract_domain(value, ctx)


@pytest.mark.parametrize("value", ["public", "exports", "all", "auto", None])
def test_contract_resolves_the_same_on_both_routes(value: Any) -> None:
    request = CompareRequest(
        old=InputSpec(path=Path("a.so")),
        new=InputSpec(path=Path("b.so")),
        contract=value,
    )
    assert request.contract == _cli_contract(value)


def test_typed_auto_is_the_no_domain_spelling() -> None:
    # `--contract auto` states no domain, which is the typed `None`.
    assert _cli_contract("auto") is None
    assert (
        CompareRequest(
            old=InputSpec(path=Path("a.so")),
            new=InputSpec(path=Path("b.so")),
            contract="auto",
        ).contract
        is None
    )


def test_a_contract_outside_the_value_set_is_refused() -> None:
    request = CompareRequest(
        old=InputSpec(path=Path("a.so")),
        new=InputSpec(path=Path("b.so")),
        contract="bogus",  # type: ignore[arg-type]
    )
    assert any("unsupported contract 'bogus'" in e for e in request.validation_errors())
