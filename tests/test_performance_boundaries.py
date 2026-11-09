# SPDX-License-Identifier: Apache-2.0
"""Performance shortcuts preserve public values and mutable ownership."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from hypothesis import given, strategies as st

from abicheck.frontends.cli.registry import (
    ROOT_COMMANDS,
    CommandSpec,
    LazyGroup,
    _help_command,
)
from abicheck.report.document import ReportDocument
from abicheck.report.render_json import render_json
from abicheck.storage.json_strings import parse_document

JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(), children, max_size=4)
    ),
    max_leaves=30,
)


@given(JSON_VALUES, st.sampled_from([None, 0, 1, 2, 4]))
def test_frozen_render_matches_standard_json(value: object, indent: int | None) -> None:
    mapping = {"payload": value, "unicode": "данные"}
    document = ReportDocument.from_mapping(mapping)
    assert render_json(document, indent=indent) == json.dumps(mapping, indent=indent)
    assert document.to_mapping() == mapping


def test_render_does_not_build_a_mutable_document_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ReportDocument.from_mapping({"records": [{"name": "f"}]})

    def forbidden(*args: object) -> None:
        pytest.fail("rendering must not thaw the whole document")

    monkeypatch.setattr(ReportDocument, "to_mapping", forbidden)
    assert json.loads(render_json(document)) == {"records": [{"name": "f"}]}


@given(JSON_VALUES)
def test_string_pool_preserves_json_values(value: object) -> None:
    text = json.dumps(value)
    assert parse_document(text, pool_strings=True) == json.loads(text)


def test_string_pool_shares_only_immutable_values() -> None:
    text = '[{"s":"repeated declaration spelling","items":[1]}, {"s":"repeated declaration spelling","items":[1]}]'
    result = parse_document(text, pool_strings=True)
    assert result[0]["s"] is result[1]["s"]
    result[0]["items"].append(2)
    result[0]["new"] = True
    assert result[1] == {"s": "repeated declaration spelling", "items": [1]}


@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}',
        '{"x":-0.0}',
        '{"x":123456789012345678901234567890}',
        '{"future":{"nested":[null,true,"é"]}}',
    ],
)
def test_pool_preserves_standard_json_edge_cases(text: str) -> None:
    assert json.dumps(parse_document(text, pool_strings=True)) == json.dumps(
        json.loads(text)
    )


@pytest.mark.parametrize("args", [["--version"], ["--help"]])
@pytest.mark.known_gap_canary
def test_cold_cli_does_not_import_analysis_pipeline(args: list[str]) -> None:
    code = """
import sys
from click.testing import CliRunner
from abicheck.cli import main
result = CliRunner().invoke(main, ARGS)
assert result.exit_code == 0, result.exception
for name in ["abicheck.checker", "abicheck.workflows.compare_finalize", "abicheck.frontends.cli.commands.compare", "abicheck.frontends.cli.commands.dump"]:
    assert name not in sys.modules, name
""".replace("ARGS", repr(args))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spec", ROOT_COMMANDS, ids=lambda spec: spec.name)
def test_help_metadata_matches_real_command(spec: CommandSpec) -> None:
    import importlib

    metadata = _help_command(spec)
    actual = getattr(importlib.import_module(spec.module), spec.callback)
    assert metadata is not None
    assert metadata.get_short_help_str() == actual.get_short_help_str()
    assert metadata.hidden == actual.hidden
    assert metadata.deprecated == actual.deprecated


def test_lazy_mapping_loads_once_and_respects_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    actual = click.Command("sample", params=[click.Option(["--value"])])
    group = LazyGroup(
        "root", command_specs=(CommandSpec("sample", "example", "command"),)
    )

    def load(name: str) -> SimpleNamespace:
        calls.append(name)
        return SimpleNamespace(command=actual)

    monkeypatch.setattr("abicheck.frontends.cli.registry.importlib.import_module", load)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert (
            list(pool.map(lambda _: group.commands["sample"], range(8))) == [actual] * 8
        )
    assert calls == ["example"]
    assert dict(group.commands)["sample"].params == actual.params
    assert list(group.commands.values()) == [actual]
    replacement = click.Command("replacement")
    group.commands["sample"] = replacement
    assert group.get_command(click.Context(group), "sample") is replacement
    del group.commands["sample"]
    assert group.get_command(click.Context(group), "sample") is None


def test_help_without_source_falls_back_to_real_command() -> None:
    assert _help_command(CommandSpec("missing", "missing.module", "callback")) is None


@pytest.mark.parametrize("profile", ["balanced", "low-memory"])
def test_snapshot_profile_preserves_complete_roundtrip(
    tmp_path: Path, profile: str
) -> None:
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.model.performance import (
        parse_performance_profile,
        performance_profile_scope,
    )
    from abicheck.serialization import load_snapshot, save_snapshot, snapshot_to_dict

    original = AbiSnapshot(
        library="example.so",
        version="1",
        functions=[
            Function(
                name="f", mangled="f", return_type="long", visibility=Visibility.PUBLIC
            )
        ],
    )
    path = tmp_path / "snapshot.json"
    save_snapshot(original, path)
    with performance_profile_scope(parse_performance_profile(profile)):
        restored = load_snapshot(path)
    assert snapshot_to_dict(restored) == snapshot_to_dict(original)


def test_reference_mode_bypasses_string_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABICHECK_REFERENCE_MODE", "1")
    text = (
        '[{"s":"repeated declaration spelling"},{"s":"repeated declaration spelling"}]'
    )
    values = parse_document(text, pool_strings=True)
    assert values == json.loads(text)
    assert values[0]["s"] is not values[1]["s"]


@pytest.mark.parametrize("profile", ["balanced", "low-memory"])
@pytest.mark.parametrize("raises", [False, True])
def test_execution_gc_profile_applies_and_restores(
    monkeypatch: pytest.MonkeyPatch, profile: str, raises: bool
) -> None:
    import gc

    from abicheck.model import AbiSnapshot
    from abicheck.model.performance import (
        parse_performance_profile,
    )
    from abicheck.storage import acyclic_json
    from abicheck.workflows import compare_policy
    from abicheck.workflows.performance import execution_profile_scope

    monkeypatch.setattr(acyclic_json, "_gil_enabled", lambda: True)
    prior = gc.get_threshold()
    expected = acyclic_json.RELAXED_GC_THRESHOLDS if profile == "balanced" else prior
    original = compare_policy.compare
    observed = []

    def core(*args: object, **kwargs: object) -> object:
        observed.append(gc.get_threshold())
        if raises:
            raise RuntimeError("comparison failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(compare_policy, "compare", core)
    old = AbiSnapshot(library="example.so", version="1")
    new = AbiSnapshot(library="example.so", version="2")
    with execution_profile_scope(parse_performance_profile(profile)):
        if raises:
            with pytest.raises(RuntimeError, match="comparison failed"):
                compare_policy.compare_snapshots(old, new)
        else:
            compare_policy.compare_snapshots(old, new)
    assert observed == [expected]
    assert gc.get_threshold() == prior


@pytest.mark.parametrize("profile", ["balanced", "low-memory"])
@pytest.mark.parametrize("report_mode", ["full", "root-cause"])
def test_cli_gc_scope_includes_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, report_mode: str
) -> None:
    import gc
    import importlib

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.model import AbiSnapshot
    from abicheck.report import contract_blocks, diff_report
    from abicheck.serialization import save_snapshot
    from abicheck.storage import acyclic_json

    monkeypatch.setattr(acyclic_json, "_gil_enabled", lambda: True)
    prior = gc.get_threshold()
    expected = acyclic_json.RELAXED_GC_THRESHOLDS if profile == "balanced" else prior
    json_render = importlib.import_module("abicheck.report.render_json")
    original = json_render.render_json
    observed = []

    def render(document: ReportDocument, *, indent: int | None = 2) -> str:
        observed.append(gc.get_threshold())
        return original(document, indent=indent)

    monkeypatch.setattr(diff_report, "render_json", render)
    monkeypatch.setattr(contract_blocks, "render_json", render)
    monkeypatch.setattr(json_render, "render_json", render)
    path = tmp_path / "snapshot.json"
    save_snapshot(AbiSnapshot(library="example.so", version="1"), path)
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(path),
            str(path),
            "--performance-profile",
            profile,
            "--set",
            f'output.view=["{report_mode}"]',
            "-o",
            f"json={tmp_path / 'report.json'}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert observed == [expected]
    assert gc.get_threshold() == prior
