# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Exit-code and error-type contract for build-source embedding.

ADR-061 Phase 3 moves ``embed_build_source`` off the CLI layer. Three
user-visible behaviours ride on that move, and none of them was pinned before:

* a bad ``.abicheck.yml`` is a **usage error** (Click exit 2, mapped to 64 by
  ``cli.main``'s wrapper) -- ADR-043's CLI reset says config errors use 64;
* a corrupt evidence pack is an **operational** failure (exit 1), not a usage
  error -- the invocation was well-formed, the data was not;
* the typed API raises ``SnapshotError`` for the same pack, because a Tier-2
  caller has no ``ClickException`` concept.

Written as characterization tests *before* the move and required to pass
unchanged after it: the whole risk of relocating that translation is that one
of these three silently becomes another.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.serialization import write_snapshot


@pytest.fixture
def snaps(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    write_snapshot(AbiSnapshot("lib.so", "1"), old)
    write_snapshot(AbiSnapshot("lib.so", "2"), new)
    return old, new


@pytest.fixture
def corrupt_pack(tmp_path: Path) -> Path:
    """A directory ``is_pack_dir`` accepts but ``BuildSourcePack.load`` rejects.

    An unparseable ``manifest.json`` is deliberately still "a pack" (see
    ``pack_shape.is_pack_dir``), precisely so the load raises loudly instead of
    the tree being silently collected from as raw sources.
    """
    pack = tmp_path / "corrupt_pack"
    pack.mkdir()
    (pack / "manifest.json").write_text("{not json", encoding="utf-8")
    return pack


@pytest.fixture
def bad_config(tmp_path: Path) -> Path:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("build:\n  query: [not, a, string]\n", encoding="utf-8")
    return cfg


def test_corrupt_pack_is_an_operational_error_not_a_usage_error(
    snaps: tuple[Path, Path], corrupt_pack: Path
) -> None:
    """Exit 1, and specifically not 2/64: the command line was well-formed."""
    old, new = snaps
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--sources", f"old={corrupt_pack}"]
    )

    assert result.exit_code == 1
    assert "Invalid evidence pack" in result.output
    assert str(corrupt_pack) in result.output


def test_bad_build_config_is_a_usage_error(
    snaps: tuple[Path, Path], bad_config: Path
) -> None:
    """Exit 64: ``cli.main``'s wrapper maps Click's usage exit 2 onto it (ADR-043)."""
    old, _ = snaps
    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(old),
            "--sources",
            str(bad_config.parent),
            "--config",
            str(bad_config),
        ],
    )

    assert result.exit_code == 64
    assert "build config" in result.output


def test_usage_errors_map_to_64_at_the_process_boundary() -> None:
    """Pin the mapping the test above rests on, so a change to it is visible here."""
    from abicheck.cli import _EXIT_USAGE_ERROR

    assert _EXIT_USAGE_ERROR == 64
    assert click.UsageError("x").exit_code == 2
    # A non-usage ClickException keeps its own code, which is the 1 above.
    assert click.ClickException("x").exit_code == 1


def test_typed_api_raises_snapshot_error_for_a_corrupt_pack(
    snaps: tuple[Path, Path], corrupt_pack: Path
) -> None:
    """A Tier-2 caller gets ``SnapshotError``, never a Click exception."""
    from abicheck.api_types import CompareRequest, InputSpec
    from abicheck.service_compare_pipeline import run_compare_request

    old, new = snaps
    request = CompareRequest(
        old=InputSpec.of(old, sources=corrupt_pack),
        new=InputSpec.of(new),
        has_sources=True,
    )

    with pytest.raises(SnapshotError) as excinfo:
        run_compare_request(request)

    assert "Invalid evidence pack" in str(excinfo.value)
    assert not isinstance(excinfo.value, click.ClickException)


def test_bad_config_is_a_usage_error_at_the_function_boundary(
    tmp_path: Path, bad_config: Path
) -> None:
    """``embed_build_source`` itself distinguishes usage from operational.

    Pinned at the function boundary as well as through the CLI, because the
    two error classes are what the CLI's 64-vs-1 split is derived from: a
    move that collapsed them would still exit 64 for the config case through
    the path above while silently changing the other.
    """
    from abicheck.cli_buildsource import embed_build_source

    with pytest.raises(click.UsageError) as excinfo:
        embed_build_source(
            AbiSnapshot("lib.so", "1"),
            None,
            bad_config.parent,
            build_config=bad_config,
            quiet=True,
        )

    assert excinfo.value.exit_code == 2
    assert "build.query must be a string" in str(excinfo.value)


def test_typed_api_raises_snapshot_error_for_a_bad_config(
    snaps: tuple[Path, Path], bad_config: Path
) -> None:
    """The typed API flattens *both* error classes onto ``SnapshotError``.

    Today that happens because ``service_input_resolution`` catches
    ``ClickException``, which ``UsageError`` is a subclass of. Pinned so a
    move that raises a distinct typed error for the config case cannot change
    what a Tier-2 caller has to catch.

    Driven through ``embed_side_build_source`` rather than ``CompareRequest``
    because ``InputSpec`` carries no ``build_config`` field -- this path is
    reachable from Tier-2 only through that function's own keyword.
    """
    from abicheck.api_types import InputSpec
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import embed_side_build_source

    old, _ = snaps
    with pytest.raises(SnapshotError) as excinfo:
        embed_side_build_source(
            AbiSnapshot("lib.so", "1"),
            InputSpec.of(old, sources=bad_config.parent),
            SideEvidence(
                headers=[],
                compile=None,
                collect_mode="source-target",
                dump_manifest=None,
            ),
            "auto",
            [],
            [],
            build_config=bad_config,
        )

    assert "build.query must be a string" in str(excinfo.value)


def test_inputs_pack_validation_failure_is_also_operational(tmp_path: Path) -> None:
    """The Flow-2 loader shares the classic loader's exit-1 contract."""
    from abicheck.buildsource.inputs_pack import INPUTS_KIND

    pack = tmp_path / "inputs"
    pack.mkdir()
    (pack / "manifest.json").write_text(json.dumps({"kind": INPUTS_KIND}))

    from abicheck.cli_buildsource_helpers import _load_inputs_pack_or_raise

    def _boom(_path: Path) -> object:
        class _Report:
            errors = ["deliberately invalid"]
            warnings: list[str] = []

        return _Report()

    import abicheck.buildsource.inputs_validate as iv

    original = iv.validate_inputs_pack
    iv.validate_inputs_pack = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(click.ClickException) as excinfo:
            _load_inputs_pack_or_raise(pack)
    finally:
        iv.validate_inputs_pack = original  # type: ignore[assignment]

    assert excinfo.value.exit_code == 1
    assert "Invalid abicheck_inputs/ pack" in str(excinfo.value)


class TestMalformedPackErrorsReachTheTypedContract:
    """What a Tier-2 caller must catch when embedding fails.

    Before ADR-061 Phase 3 this class asserted that a ``click.ClickException``
    from the CLI-layer ``embed_build_source`` was translated to
    ``SnapshotError``. The engine now owns that function and raises the typed
    errors directly, so there is no CLI exception left to translate -- but the
    *caller-visible* contract is unchanged, and that is what is pinned here:
    both error classes still arrive as ``SnapshotError``.
    """

    def test_snapshot_error_propagates_unchanged(
        self, snaps: tuple[Path, Path], tmp_path: Path, monkeypatch
    ):
        """An invalid pack is already a ``SnapshotError``; nothing rewraps it."""
        from abicheck import service
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.buildsource import embed as embed_mod
        from abicheck.errors import SnapshotError as _SnapshotError

        def _boom(snap, *args, **kwargs):
            raise _SnapshotError("build pack is malformed")

        monkeypatch.setattr(embed_mod, "embed_build_source", _boom)
        snap_path, _ = snaps
        sources = tmp_path / "src"
        sources.mkdir()
        with pytest.raises(SnapshotError, match="build pack is malformed"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path, sources=sources))
            )

    def test_validation_error_is_flattened_to_snapshot_error(
        self, snaps: tuple[Path, Path], tmp_path: Path, monkeypatch
    ):
        """A usage-class error is flattened, so callers still catch one type.

        The engine keeps ``ValidationError`` distinct because the CLI derives
        exit 64 from it; this surface has always presented a single error type
        to its callers, and widening that would be a breaking API change.
        """
        from abicheck import service
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.buildsource import embed as embed_mod
        from abicheck.errors import ValidationError

        def _boom(snap, *args, **kwargs):
            raise ValidationError("build.query must be a string")

        monkeypatch.setattr(embed_mod, "embed_build_source", _boom)
        snap_path, _ = snaps
        sources = tmp_path / "src"
        sources.mkdir()
        with pytest.raises(SnapshotError, match="build.query must be a string"):
            service.run_dump_request(
                DumpRequest(input=InputSpec(path=snap_path, sources=sources))
            )
