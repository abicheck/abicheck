# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the typed compare fold in ``workflows.compare.configuration``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click
import pytest

from abicheck.workflows.compare.configuration import (
    fold_compare_collect_inputs,
    split_compile_context_frontend,
)

HEADERS = (Path("a.h"),)
OLD_ONLY = (Path("old.h"),)
NEW_ONLY = (Path("new.h"),)


def _fold(depth: str | None) -> tuple[str, tuple, tuple, tuple]:
    return fold_compare_collect_inputs(
        depth=depth,
        source_method=None,
        headers=HEADERS,
        old_headers_only=OLD_ONLY,
        new_headers_only=NEW_ONLY,
        old_sources=None,
        new_sources=None,
        old_build_info=None,
        new_build_info=None,
    )


def test_binary_depth_clears_every_header_root() -> None:
    _, headers, old_only, new_only = _fold("binary")
    assert (headers, old_only, new_only) == ((), (), ())


@pytest.mark.parametrize("depth", [None, "headers", "full"])
def test_non_binary_depth_keeps_header_roots(depth: str | None) -> None:
    _, headers, old_only, new_only = _fold(depth)
    assert (headers, old_only, new_only) == (HEADERS, OLD_ONLY, NEW_ONLY)


@dataclass(frozen=True)
class _Ctx:
    frontend: str
    includes: tuple[str, ...]


@pytest.mark.parametrize("frontend", ["castxml", "clang", "auto"])
def test_side_context_is_neutralized_and_backend_is_preserved(frontend: str) -> None:
    ctx = _Ctx(frontend=frontend, includes=("-Iinc",))
    backend, side = split_compile_context_frontend(ctx)
    assert backend == frontend
    assert side.frontend == "auto"
    # Only the frontend is neutralized; every other field survives unchanged.
    assert side.includes == ("-Iinc",)
    assert ctx.frontend == frontend


# --- Manifest preflight: the CLI adapter and the workflow functions -------


def test_load_dump_manifests_loads_only_the_sides_given(monkeypatch) -> None:
    from abicheck.workflows.compare.manifests import load_compare_dump_manifests

    loaded: list[Path] = []

    def fake_load(path: Path) -> str:
        loaded.append(path)
        return f"manifest:{path.name}"

    monkeypatch.setattr("abicheck.workflows.extraction.load_manifest", fake_load)
    old_path, new_path = Path("old.yml"), Path("new.yml")

    assert load_compare_dump_manifests(None, None) == (None, None)
    assert load_compare_dump_manifests(old_path, None) == ("manifest:old.yml", None)
    assert load_compare_dump_manifests(None, new_path) == (None, "manifest:new.yml")
    assert load_compare_dump_manifests(old_path, new_path) == (
        "manifest:old.yml",
        "manifest:new.yml",
    )
    assert loaded == [old_path, new_path, old_path, new_path]


def test_validate_pack_manifests_forwards_the_contract(monkeypatch) -> None:
    from abicheck.workflows.compare.manifests import validate_compare_pack_manifests

    seen: dict[str, object] = {}

    def fake_validate(pack_paths, *, policy_file_path, contract_evaluation) -> None:
        seen.update(
            pack_paths=pack_paths,
            policy_file_path=policy_file_path,
            contract_evaluation=contract_evaluation,
        )

    monkeypatch.setattr(
        "abicheck.workflows.pack_resolution.validate_pack_manifests", fake_validate
    )
    validate_compare_pack_manifests(
        ("p.yml",), policy_file_path=Path("pol.yml"), contract_evaluation=True
    )
    assert seen == {
        "pack_paths": ("p.yml",),
        "policy_file_path": Path("pol.yml"),
        "contract_evaluation": True,
    }


def _preflight(**overrides):
    from abicheck.cli_compare_helpers import _preflight_manifests_and_audit

    kwargs = dict(
        old_dump_manifest=None,
        new_dump_manifest=None,
        pack_paths=(),
        policy_file_path=None,
        contract_evaluation=False,
    )
    kwargs.update(overrides)
    with click.Context(click.Command("compare")):
        return _preflight_manifests_and_audit(**kwargs)


def test_preflight_renders_manifest_error_as_usage_error(monkeypatch) -> None:
    from abicheck.errors import ManifestValidationError

    def bad_load(path: Path) -> None:
        raise ManifestValidationError("bad manifest")

    monkeypatch.setattr("abicheck.workflows.extraction.load_manifest", bad_load)
    with pytest.raises(click.UsageError, match="bad manifest"):
        _preflight(old_dump_manifest=Path("x.yml"))


def test_preflight_renders_pack_error_as_usage_error(monkeypatch) -> None:
    from abicheck.errors import PackManifestError

    def bad_validate(*_args, **_kwargs) -> None:
        raise PackManifestError("bad pack")

    monkeypatch.setattr(
        "abicheck.workflows.pack_resolution.validate_pack_manifests", bad_validate
    )
    with pytest.raises(click.UsageError, match="bad pack"):
        _preflight(pack_paths=("p.yml",))


# --- Configuration fold: the CLI adapter and the workflow function ---------


def _evaluation_config(**overrides):
    from abicheck.cli_compare_helpers import _resolve_evaluation_config

    kwargs = dict(
        headers=(),
        resolved_cfg=object(),
        project_cfg=None,
        cfg_path=None,
        cfg_sha=None,
        config_explicit=False,
        policy="strict_abi",
        policy_file_path=None,
        policy_file=None,
        suppression=None,
        suppress=None,
        symbols_list=None,
        contract_mode=None,
        contract_evaluation=False,
        require_justification=False,
        severity_preset=None,
        pack_paths=(),
        policy_selected_by=None,
        policy_selected_path=None,
        policy_selected_sha=None,
    )
    kwargs.update(overrides)
    with click.Context(click.Command("compare")) as ctx:
        return _resolve_evaluation_config(ctx, **kwargs)


@pytest.mark.parametrize(
    ("error", "expected_type", "expected_hint"),
    [
        ("ConfigurationConflictError", click.UsageError, None),
        ("ProjectPolicyOverridesError", click.BadParameter, "--policy"),
    ],
)
def test_config_wrapper_renders_typed_refusals(
    monkeypatch, error: str, expected_type: type, expected_hint: str | None
) -> None:
    from abicheck.workflows import compare_configuration as cc

    def refuse(*_args, **_kwargs):
        raise getattr(cc, error)("refused")

    monkeypatch.setattr(
        "abicheck.workflows.compare.configuration.resolve_evaluation_configuration",
        refuse,
    )
    with pytest.raises(expected_type, match="refused") as info:
        _evaluation_config()
    if expected_hint is not None:
        assert info.value.param_hint == expected_hint


def _fake_configuration(pack_application):
    from types import SimpleNamespace

    return SimpleNamespace(
        evaluation_config="evaluation",
        policy_file="policy",
        pack_application=pack_application,
    )


def _resolve_with(monkeypatch, pack_application, resolved_cfg="cfg"):
    from abicheck.workflows.compare.configuration import (
        resolve_evaluation_configuration,
    )

    monkeypatch.setattr(
        "abicheck.workflows.compare_input_adapters.cli_configuration_inputs",
        lambda *_a, **_k: ("explicit", "project"),
    )
    monkeypatch.setattr(
        "abicheck.workflows.compare_configuration.resolve_compare_configuration",
        lambda *_a, **_k: _fake_configuration(pack_application),
    )
    return resolve_evaluation_configuration(
        {},
        typed=(),
        resolved_cfg=resolved_cfg,
        project_cfg=None,
        cfg_path=None,
        cfg_sha=None,
        config_explicit=False,
        policy="strict_abi",
        policy_file=None,
        suppression=None,
        suppress=None,
        symbols_list=None,
        contract_evaluation=False,
        policy_selected_by=None,
        policy_selected_path=None,
        policy_selected_sha=None,
    )


def test_config_fold_keeps_resolved_cfg_without_a_pack(monkeypatch) -> None:
    assert _resolve_with(monkeypatch, pack_application=None) == (
        "evaluation",
        "policy",
        "cfg",
    )


def test_config_fold_applies_a_pack_to_resolved_cfg(monkeypatch) -> None:
    monkeypatch.setattr(
        "abicheck.pack_application.apply_to_compare_config",
        lambda cfg, application: f"{cfg}+{application}",
    )
    assert _resolve_with(monkeypatch, pack_application="gate", resolved_cfg="cfg") == (
        "evaluation",
        "policy",
        "cfg+gate",
    )
