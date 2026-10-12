# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The compare-settings resolver is Click-free, and the CLI still reports it the same way.

``workflows.compare_configuration`` raises :class:`CompareSettingsError`
where the CLI-layer copies used to raise ``click.UsageError``. Each case below
runs twice, through the typed function and through a real ``compare``
invocation. The CLI must exit 64 and print the typed error's own message, so
moving the resolver out of the CLI changes neither the exit code nor the
text. The ``--no-baseline`` audit, which resolves the same settings through
its own call site, is checked too.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows import compare_configuration as cc

ROOT = Path(__file__).resolve().parent.parent


def _snapshots(tmp_path: Path) -> tuple[Path, Path]:
    paths = []
    for version in ("1.0", "2.0"):
        snap = AbiSnapshot(
            library="libtest.so",
            version=version,
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        path = tmp_path / f"v{version}.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        paths.append(path)
    return paths[0], paths[1]


def _bad_source_method(tmp_path: Path) -> tuple[list[str], Callable[[], object]]:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("source:\n  method: not-a-method\n", encoding="utf-8")
    return (
        ["--config", str(cfg)],
        lambda: cc.resolve_compare_collect_mode(
            None, "not-a-method", None, None, None, None
        ),
    )


def _malformed_config(tmp_path: Path) -> tuple[list[str], Callable[[], object]]:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("scope: [unterminated\n", encoding="utf-8")
    return (
        ["--config", str(cfg)],
        lambda: cc.load_and_resolve_compare_config(cfg, severity_preset=None),
    )


def _missing_required_file(tmp_path: Path) -> tuple[list[str], Callable[[], object]]:
    missing = tmp_path / "absent.txt"
    return (
        ["--required-symbol", f"@{missing}"],
        lambda: cc.load_required_symbols((f"@{missing}",)),
    )


def _two_required_files(tmp_path: Path) -> tuple[list[str], Callable[[], object]]:
    one, two = tmp_path / "one.txt", tmp_path / "two.txt"
    one.write_text("a\n", encoding="utf-8")
    two.write_text("b\n", encoding="utf-8")
    return (
        ["--required-symbol", f"@{one}", "--required-symbol", f"@{two}"],
        lambda: cc.load_required_symbols((f"@{one}", f"@{two}")),
    )


_CASES = {
    "invalid source.method": _bad_source_method,
    "malformed .abicheck.yml": _malformed_config,
    "missing @FILE": _missing_required_file,
    "two @FILE values": _two_required_files,
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_cli_reports_the_typed_error_as_exit_64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.chdir(tmp_path)
    flags, typed_call = _CASES[case](tmp_path)
    with pytest.raises(cc.CompareSettingsError) as excinfo:
        typed_call()
    message = str(excinfo.value)
    assert message

    old, new = _snapshots(tmp_path)
    result = CliRunner().invoke(main, ["compare", str(old), str(new), *flags])
    assert result.exit_code == 64, result.output
    # Click may wrap a long message; compare it with whitespace normalized.
    assert " ".join(message.split()) in " ".join(result.output.split())


def test_no_baseline_audit_reports_invalid_source_method_as_exit_64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    flags, typed_call = _bad_source_method(tmp_path)
    with pytest.raises(cc.CompareSettingsError) as excinfo:
        typed_call()
    _, new = _snapshots(tmp_path)
    result = CliRunner().invoke(main, ["compare", "--no-baseline", str(new), *flags])
    assert result.exit_code == 64, result.output
    assert " ".join(str(excinfo.value).split()) in " ".join(result.output.split())


def test_resolver_module_does_not_import_click() -> None:
    tree = ast.parse(
        (ROOT / "abicheck/workflows/compare_configuration.py").read_text(
            encoding="utf-8"
        )
    )
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "click" not in imported
