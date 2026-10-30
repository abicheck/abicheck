"""``--set PATH=VALUE`` and ``config_overrides`` (ADR-091 D2/D4, Phase 2).

Both spellings must resolve to the same effective config. The CLI parses
text into the mapping and the typed API takes the mapping; from there one
validator and one loader apply it. These tests check the resolved config
and the resolved request, not a mock of either path.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

from abicheck.buildsource.build_config import (
    KEY_TABLE,
    BuildConfig,
    load_build_config,
)
from abicheck.buildsource.build_config_overrides import (
    CORE_CONFIG_FLAGS,
    apply_config_overrides,
)
from abicheck.cli import main
from abicheck.frontends.cli.options.config_set import parse_set_assignments
from abicheck.frontends.cli.project_config import resolve_project_compare_config
from abicheck.workflows.compare.execute import _with_config_overrides
from abicheck.workflows.compare_configuration import (
    CompareSettingsError,
    load_and_resolve_compare_config,
)
from abicheck.workflows.service_requests import CompareRequest, InputSpec


def _write_config(directory: Path, text: str) -> Path:
    path = directory / ".abicheck.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _request(**kwargs: object) -> CompareRequest:
    return CompareRequest(
        old=InputSpec(path=Path("old.so")),
        new=InputSpec(path=Path("new.so")),
        **kwargs,  # type: ignore[arg-type]
    )


# ── CLI parsing: PATH=VALUE through the one schema ──────────────────────────


def test_set_values_are_yaml_typed() -> None:
    overrides = parse_set_assignments(
        (
            "scope.show_redundant=true",
            "compile.std=c++17",
            "scope.public_symbols=[foo, bar]",
        )
    )
    assert overrides == {
        "scope.show_redundant": True,
        "compile.std": "c++17",
        "scope.public_symbols": ["foo", "bar"],
    }


def test_a_later_repeat_of_the_same_path_wins() -> None:
    overrides = parse_set_assignments(
        ("scope.show_redundant=true", "scope.show_redundant=false")
    )
    assert overrides == {"scope.show_redundant": False}


@pytest.mark.parametrize(
    ("item", "named"),
    [
        ("compile.std", "PATH=VALUE"),  # no '='
        ("=c++17", "PATH=VALUE"),  # empty PATH
        ("bogus.key=1", "bogus.key"),  # unknown path
        ("compile.std=[c++", "compile.std"),  # not YAML
        ("severity.addition=[a]", "severity.addition"),  # type mismatch
        ("scope.show_redundant=3", "scope.show_redundant"),  # bool given an int
    ],
)
def test_malformed_unknown_or_mistyped_set_is_a_usage_error(
    item: str, named: str
) -> None:
    with pytest.raises(click.UsageError) as excinfo:
        parse_set_assignments((item,))
    assert named in excinfo.value.format_message()


@pytest.mark.parametrize(
    ("argv_item", "key"),
    [("bogus.key=1", "bogus.key"), ("severity.addition=[a]", "severity.addition")],
)
def test_set_errors_exit_64_through_the_cli(argv_item: str, key: str) -> None:
    # The operands are real files, so the --set check is what fails.
    operand = Path(__file__)
    result = CliRunner().invoke(
        main,
        ["compare", str(operand), str(operand), "--dry-run", "--set", argv_item],
    )
    assert result.exit_code == 64
    assert key in result.output


def test_a_valid_set_reaches_the_dry_run_cleanly() -> None:
    operand = Path(__file__)
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(operand),
            str(operand),
            "--dry-run",
            "--set",
            "severity.addition=error",
        ],
    )
    assert result.exit_code == 0, result.output


# ── core options keep one spelling ──────────────────────────────────────────


@pytest.mark.parametrize(("path", "flag"), sorted(CORE_CONFIG_FLAGS.items()))
def test_a_core_option_is_refused_and_the_error_names_its_flag(
    path: str, flag: str
) -> None:
    with pytest.raises(click.UsageError) as excinfo:
        parse_set_assignments((f"{path}=x",))
    message = excinfo.value.format_message()
    assert path in message
    assert flag in message


def test_core_refusal_also_applies_to_the_typed_api() -> None:
    with pytest.raises(CompareSettingsError, match="--severity-preset"):
        _with_config_overrides(_request(config_overrides={"severity.preset": "strict"}))


def test_core_set_keys_are_exactly_the_registry_core_config_members() -> None:
    """The refusal list must match ADR-091 D4's core tier in the registry."""
    registry = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "architecture"
            / "option-vocabulary.yaml"
        ).read_text(encoding="utf-8")
    )
    core_config = {
        path
        for concept in registry["concepts"]
        if concept["tier"] == "core"
        for path in concept.get("config", [])
    }
    assert set(CORE_CONFIG_FLAGS) == core_config


# ── loader: the file, then the overrides, then the one schema ──────────────


def test_an_override_beats_the_file_and_a_list_replaces_it(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "severity:\n  addition: warning\nscope:\n  public_symbols: [x]\n",
    )
    cfg = load_build_config(
        path,
        config_overrides={
            "severity.addition": "error",
            "scope.public_symbols": ["y"],
        },
    )
    assert cfg.severity_addition == "error"
    assert cfg.public_symbols == ["y"]


def test_overrides_build_the_config_when_there_is_no_file(tmp_path: Path) -> None:
    cfg = load_build_config(
        tmp_path / "absent.yml", config_overrides={"scope.show_redundant": True}
    )
    assert cfg.scope_show_redundant is True


def test_the_file_stays_strict_when_overrides_are_given(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "not_a_key: 1\n")
    with pytest.raises(ValueError, match="not_a_key"):
        load_build_config(path, config_overrides={"scope.show_redundant": True})


def test_no_overrides_is_the_file_unchanged(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "severity:\n  addition: warning\n")
    assert load_build_config(path) == load_build_config(path, config_overrides={})


def test_apply_config_overrides_leaves_the_file_mapping_alone() -> None:
    raw = {"scope": {"show_redundant": False}}
    merged = apply_config_overrides(raw, {"scope.show_redundant": True}, KEY_TABLE)
    assert merged == {"scope": {"show_redundant": True}}
    assert raw == {"scope": {"show_redundant": False}}
    assert BuildConfig.from_dict(merged).scope_show_redundant is True


# ── precedence through the compare resolver ─────────────────────────────────


def test_an_explicit_flag_beats_the_file_and_an_override_beats_the_file(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        "severity:\n  preset: info-only\nscope:\n  collapse_versioned_symbols: true\n",
    )
    _, resolved, _ = load_and_resolve_compare_config(
        path,
        severity_preset="strict",
        config_overrides={"scope.collapse_versioned_symbols": False},
    )
    assert resolved.merged_severity_preset == "strict"  # flag > file
    assert resolved.collapse_versioned_symbols is False  # --set > file


def test_cli_and_typed_api_resolve_the_same_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no ancestor .abicheck.yml to discover
    overrides = parse_set_assignments(
        ("scope.collapse_versioned_symbols=true", "severity.addition=error")
    )

    # The CLI: the value compare_cmd hands the resolver.
    _, _, cli_resolved, _ = resolve_project_compare_config(
        config=None, severity_preset=None, config_overrides=overrides
    )
    # The typed API: the same mapping on the request.
    typed = _with_config_overrides(_request(config_overrides=overrides))

    assert typed.collapse_versioned_symbols == cli_resolved.collapse_versioned_symbols
    assert typed.collapse_versioned_symbols is True
    assert typed.severity_addition == cli_resolved.merged_severity_addition == "error"
    assert typed.scope_public == cli_resolved.scope_public


def test_a_typed_field_set_away_from_its_default_is_kept() -> None:
    request = _request(
        severity_addition="warning",
        config_overrides={"severity.addition": "error"},
    )
    assert _with_config_overrides(request).severity_addition == "warning"


def test_an_override_fills_a_field_left_at_its_default() -> None:
    request = _request(config_overrides={"scope.public": False})
    assert request.scope_public is True  # the default, before resolution
    assert _with_config_overrides(request).scope_public is False


def test_config_overrides_default_is_an_immutable_empty_mapping() -> None:
    request = _request()
    assert dict(request.config_overrides) == {}
    with pytest.raises(TypeError):
        request.config_overrides["scope.show_redundant"] = True  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.config_overrides = {}  # type: ignore[misc]
