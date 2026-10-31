"""ADR-091 Phase 5: the project-stable compare options that moved to `.abicheck.yml`.

`--used-by-manifest`, `--required-symbol` and `--view` are removed outright (exit
64 as unknown options). Their values are `consumers.manifests`,
`consumers.required_symbols` and `output.view`, reachable through `.abicheck.yml`
and `--set PATH=VALUE`. `--define` and `--severity-preset` stay flags; a `--set`
of their config keys is refused as a core option. A config that sets a scoping
or view key is refused, not ignored, on the routes that cannot honour it.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata
from abicheck.serialization import snapshot_to_json

_SONAME = "libfoo.so.1"


def _snapshot(path: Path, version: str) -> Path:
    snap = AbiSnapshot(
        library=_SONAME,
        version=version,
        functions=[],
        from_headers=True,
        elf=ElfMetadata(soname=_SONAME),
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "flag",
    [
        "--view",
        "--used-by-manifest",
        "--required-symbol",
    ],
)
def test_moved_compare_spellings_are_removed(tmp_path: Path, flag: str) -> None:
    old = _snapshot(tmp_path / "old.json", "1.0")
    new = _snapshot(tmp_path / "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old), str(new), flag, "x"])
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output


@pytest.mark.parametrize(
    "assignment",
    [
        "output.view=[show=breaking]",
        "consumers.manifests=[m.json]",
        "consumers.required_symbols=[sym_a]",
    ],
)
def test_moved_keys_are_accepted_by_compare_set(
    tmp_path: Path, assignment: str
) -> None:
    """Each moved key passes --set validation (it fails later only on operands)."""
    from abicheck.frontends.cli.options.config_set import (
        _COMPARE_APPLIED_PATHS,
        parse_set_assignments,
    )

    path = assignment.partition("=")[0]
    assert path in _COMPARE_APPLIED_PATHS
    parse_set_assignments((assignment,))


@pytest.mark.parametrize("assignment", ["severity.preset=default", "compile.defines=[A]"])
def test_core_option_keys_stay_refused_by_set(assignment: str) -> None:
    """severity.preset and compile.defines keep their flags, so --set refuses them."""
    from abicheck.frontends.cli.options.config_set import parse_set_assignments

    with pytest.raises(click.UsageError, match="core option"):
        parse_set_assignments((assignment,))


def test_config_consumer_keys_load_into_the_build_config(tmp_path: Path) -> None:
    from abicheck.buildsource.build_config import load_build_config

    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "consumers:\n"
        "  manifests: [apps/app.json]\n"
        "  required_symbols: [plugin_init]\n"
        "output:\n"
        "  view: [root-cause]\n",
        encoding="utf-8",
    )
    loaded = load_build_config(cfg)
    assert loaded.used_by_manifests == ["apps/app.json"]
    assert loaded.required_symbols == ["plugin_init"]
    assert loaded.output_view == ["root-cause"]


def test_no_baseline_refuses_config_consumer_and_view_keys(tmp_path: Path) -> None:
    old = _snapshot(tmp_path / "old.json", "1.0")
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("output:\n  view: [root-cause]\n", encoding="utf-8")
    result = CliRunner().invoke(
        main, ["compare", str(old), "--no-baseline", "--config", str(cfg)]
    )
    assert result.exit_code == 64, result.output
    assert "output.view is not available with --no-baseline" in result.output


def test_consumer_manifest_read_failure_is_a_usage_error(tmp_path: Path) -> None:
    """A config manifest that cannot be read is a usage error (exit 64), not a traceback."""
    old = _snapshot(tmp_path / "old.json", "1.0")
    new = _snapshot(tmp_path / "new.json", "2.0")
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--set",
            f"consumers.manifests=[{tmp_path / 'missing.json'}]",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "consumers.manifests" in result.output
