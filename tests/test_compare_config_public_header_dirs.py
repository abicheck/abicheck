# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for ADR-068 plan §5 P4's config-sourced public/internal boundary:
``.abicheck.yml``'s ``scope.public_header_dirs`` reaching the native
``compare`` CLI's own snapshot resolution, threaded through
``workflows.public_header_boundary.project_config_public_header_dirs`` ->
``cli_resolve._resolve_compare_snapshots``'s ``config_public_header_dirs``
parameter -> ``InputSpec.public_header_dirs`` (the same field a ``-H``
directory argument already populates, per
``workflows.cross_source_evolution``'s own module docstring).

These tests exercise the *wiring*, not the boundary-derivation machinery
itself (``provenance.apply_provenance``/``workflows.scan_config.
public_provenance_set`` already have their own dedicated test suites) --
the property this file proves is "a `scope.public_header_dirs` entry in a
discovered `.abicheck.yml` reaches the request `compare` actually
resolves", end to end through the real CLI, without needing a real
castxml/gcc toolchain (both sides are pre-built, stored JSON snapshots, so
no header parsing actually runs -- only the request construction is
observed).

``TestDirectoryReleaseCompareReachesConfigDirs`` and
``TestStrandedLibraryInlineInputSpecReachesConfigDirs`` below close the two
gaps CodeRabbit's PR #1138 review found in the original wiring: the
directory/package fan-out (``cli_compare_helpers.run_compare``'s
``_dispatch_release_compare`` dispatch, then
``cli_compare_release_pairwise._run_compare_pair`` ->
``service.run_compare``'s own ``public_header_dirs`` keyword) and the
release fan-out's own "stranded library" fallback (``compare_release_cmd``'s
``_resolve_stranded_library`` closure, which builds a ``DumpRequest``/
``InputSpec`` directly rather than going through
``cli_resolve._resolve_compare_snapshots``) both silently dropped a
project's declared ``scope.public_header_dirs`` even though the identical
library compared alone (a two-file ``compare``) already honoured it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from abicheck.api_types import CompareRequest
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.workflows.public_header_boundary import (
    project_config_public_header_dirs,
)


def _write_snapshot(path: Path) -> Path:
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


class TestProjectConfigPublicHeaderDirsHelper:
    """``workflows.public_header_boundary.project_config_public_header_dirs``
    -- the pure projection from a loaded ``BuildConfig`` (or ``None``) to a
    list of ``Path`` objects."""

    def test_none_config_yields_empty_list(self) -> None:
        assert project_config_public_header_dirs(None) == []

    def test_config_with_no_dirs_yields_empty_list(self) -> None:
        from abicheck.buildsource.build_config import BuildConfig

        cfg = BuildConfig.from_dict({})
        assert project_config_public_header_dirs(cfg) == []

    def test_config_with_dirs_yields_path_list(self) -> None:
        from abicheck.buildsource.build_config import BuildConfig

        cfg = BuildConfig.from_dict(
            {"scope": {"public_header_dirs": ["include", "public/api"]}}
        )
        assert project_config_public_header_dirs(cfg) == [
            Path("include"),
            Path("public/api"),
        ]


class TestConfigPublicHeaderDirsReachesTheResolvedRequest:
    """End-to-end through the real ``compare`` CLI: a discovered
    ``.abicheck.yml``'s ``scope.public_header_dirs`` must reach both sides'
    ``InputSpec.public_header_dirs`` -- the same field a ``-H`` directory
    argument populates -- with no new CLI flag."""

    def _run_with_spy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_yaml: str | None
    ) -> list[CompareRequest]:
        import abicheck.service as service_mod

        seen_requests: list[CompareRequest] = []
        real = service_mod.resolve_compare_request

        def _spy(request: CompareRequest, **kwargs: object) -> object:
            seen_requests.append(request)
            return real(request, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_compare_request", _spy)

        if config_yaml is not None:
            (tmp_path / ".abicheck.yml").write_text(config_yaml, encoding="utf-8")
        old = _write_snapshot(tmp_path / "old.abi.json")
        new = _write_snapshot(tmp_path / "new.abi.json")
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        return seen_requests

    def test_no_config_leaves_public_header_dirs_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._run_with_spy(tmp_path, monkeypatch, config_yaml=None)
        assert len(seen) == 1
        assert seen[0].old.public_header_dirs == ()
        assert seen[0].new.public_header_dirs == ()

    def test_config_with_no_scope_block_leaves_public_header_dirs_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._run_with_spy(
            tmp_path, monkeypatch, config_yaml=yaml.safe_dump({"version": 1})
        )
        assert seen[0].old.public_header_dirs == ()
        assert seen[0].new.public_header_dirs == ()

    def test_config_public_header_dirs_reaches_both_sides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_yaml = yaml.safe_dump(
            {"scope": {"public_header_dirs": ["include/public"]}}
        )
        seen = self._run_with_spy(tmp_path, monkeypatch, config_yaml=config_yaml)
        assert len(seen) == 1
        request = seen[0]
        assert request.old.public_header_dirs == (Path("include/public"),)
        assert request.new.public_header_dirs == (Path("include/public"),)

    def test_multiple_config_dirs_reach_both_sides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_yaml = yaml.safe_dump(
            {"scope": {"public_header_dirs": ["include", "extra/public"]}}
        )
        seen = self._run_with_spy(tmp_path, monkeypatch, config_yaml=config_yaml)
        request = seen[0]
        assert request.old.public_header_dirs == (
            Path("include"),
            Path("extra/public"),
        )
        assert request.new.public_header_dirs == (
            Path("include"),
            Path("extra/public"),
        )


class TestDumpManifestSideNeverGetsConfigDirs:
    """A ``--dump-manifest`` side rejects ``public_header_dirs`` outright
    (it declares its own directory set in the manifest instead --
    ``api_types._side_errors``). ``cli_resolve._resolve_compare_snapshots``
    must never fold the config value into that side, or an unrelated
    ``scope.public_header_dirs`` entry would turn a working
    ``--dump-manifest`` compare into a usage error. Exercised directly
    against the resolution function (not the full CLI): a real
    ``--dump-manifest`` compare needs real header roots on disk to resolve
    without erroring, which is orthogonal to what this test checks --
    ``service.resolve_compare_request`` itself is stubbed out so only the
    assembled :class:`~abicheck.api_types.CompareRequest` is inspected."""

    def test_manifest_side_public_header_dirs_stays_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.cli_resolve as cli_resolve_mod
        from abicheck.workflows.extraction import DumpManifest

        old = _write_snapshot(tmp_path / "old.abi.json")
        new = _write_snapshot(tmp_path / "new.abi.json")
        manifest = DumpManifest(base_dir=tmp_path, roots=(old,))

        seen: list[CompareRequest] = []

        def _fake_resolve(request: CompareRequest, **kwargs: object) -> object:
            seen.append(request)

            class _Pair:
                pass

            pair = _Pair()
            pair.old = AbiSnapshot(library="libfoo.so", version="1.0")
            pair.new = AbiSnapshot(library="libfoo.so", version="1.1")
            return pair

        import abicheck.service as service_mod

        monkeypatch.setattr(service_mod, "resolve_compare_request", _fake_resolve)

        cli_resolve_mod._resolve_compare_snapshots(
            old,
            new,
            None,
            None,
            [],
            [],
            [],
            [],
            "1.0",
            "1.1",
            "C++",
            None,
            None,
            None,
            False,
            None,
            False,
            (),
            "",
            old_dump_manifest=manifest,
            new_dump_manifest=None,
            config_public_header_dirs=[Path("include")],
        )

        assert len(seen) == 1
        request = seen[0]
        # The manifest side never sees the config dirs...
        assert request.old.public_header_dirs == ()
        # ...but the manifest-less sibling side still does (additive, not
        # an all-or-nothing suppression of the whole config value).
        assert request.new.public_header_dirs == (Path("include"),)


class TestDirectoryReleaseCompareReachesConfigDirs:
    """CodeRabbit review, PR #1138, finding 1: a directory/package
    ``compare`` (the multi-library release fan-out) must honour
    ``scope.public_header_dirs`` for every matched pair, the same as a
    bare two-file ``compare`` of the identical library already does.
    Exercised through the real CLI, with a directory of pre-built JSON
    snapshots so no header parsing actually runs -- only the assembled
    :class:`~abicheck.api_types.CompareRequest` reaching
    ``resolve_compare_request`` is observed.
    """

    def test_config_public_header_dirs_reaches_the_matched_pair(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.service_compare_pipeline as pipeline_mod

        seen_requests: list[CompareRequest] = []
        real = pipeline_mod.resolve_compare_request

        def _spy(request: CompareRequest, **kwargs: object) -> object:
            seen_requests.append(request)
            return real(request, **kwargs)

        monkeypatch.setattr(pipeline_mod, "resolve_compare_request", _spy)

        (tmp_path / ".abicheck.yml").write_text(
            yaml.safe_dump({"scope": {"public_header_dirs": ["include/public"]}}),
            encoding="utf-8",
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snapshot(old_dir / "libfoo.json")
        _write_snapshot(new_dir / "libfoo.json")
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        assert len(seen_requests) == 1
        request = seen_requests[0]
        assert request.old.public_header_dirs == (Path("include/public"),)
        assert request.new.public_header_dirs == (Path("include/public"),)


class TestStrandedLibraryInlineInputSpecReachesConfigDirs:
    """CodeRabbit review, PR #1138, finding 1 (second omission site): the
    release fan-out's own "stranded library" fallback
    (``compare_release_cmd``'s ``_resolve_stranded_library`` closure,
    invoked only by ``--bundle-facts-out`` for a library present on one
    side and absent on the other) builds its own ``DumpRequest``/
    ``InputSpec`` directly rather than going through
    ``cli_resolve._resolve_compare_snapshots`` -- so it needed its own,
    separate fix to see ``scope.public_header_dirs`` at all. Mirrors
    ``tests/test_cli_compare_release_stranded_depth.py``'s own capture
    pattern for this same closure.
    """

    def test_stranded_library_input_spec_carries_config_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            yaml.safe_dump({"scope": {"public_header_dirs": ["include/public"]}}),
            encoding="utf-8",
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snapshot(old_dir / "libfoo.json")
        _write_snapshot(new_dir / "libfoo.json")
        # Present only on OLD -- write_bundle_facts_out's
        # resolve_stranded_library callback is what resolves this one.
        stranded = old_dir / "libbroken.so"
        stranded.write_bytes(b"\x7fELF" + b"\x00" * 100)
        monkeypatch.chdir(tmp_path)

        captured: dict[str, object] = {}
        from abicheck.service_dump_pipeline import (
            resolve_dump_request as _real_resolve_dump_request,
        )

        def _fake_resolve_dump_request(request: object) -> object:
            if request.input.path == stranded:  # type: ignore[attr-defined]
                captured["public_header_dirs"] = list(
                    request.input.public_header_dirs  # type: ignore[attr-defined]
                )
            return _real_resolve_dump_request(request)  # type: ignore[arg-type]

        monkeypatch.setattr(
            "abicheck.service_dump_pipeline.resolve_dump_request",
            _fake_resolve_dump_request,
        )

        out_path = tmp_path / "old.bundlefacts.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--bundle-facts-out",
                str(out_path),
            ],
        )
        assert result.exit_code in (0, 8), result.output
        assert captured.get("public_header_dirs") == [Path("include/public")]
