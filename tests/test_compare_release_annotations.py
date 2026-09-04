"""Tests for the compare-release command's PR E persistence work --

split out of ``test_compare_release.py`` (that file sat at its 2000-line
hard cap): per-library ``annotations`` persistence and ``--write``
FORMAT=PATH support for a directory/package (release) `compare` operand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers (mirrors test_compare_release.py's own) ────────────────────────


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(
        library=library, version=version, functions=funcs, from_headers=True
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Remove a function — always produces BREAKING verdict."""
    old = _snap(
        "1.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    new = _snap(
        "2.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    return old, new


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestReleaseAnnotationsPersistence:
    """CLI cleanup phase two, PR E (release-operand half): each library
    entry in a release JSON report carries its own uncapped ``annotations``
    array (mirroring single-library ``compare --format json``'s top-level
    ``annotations``, schema 2.43), always computed regardless of any flag
    (the CLI's own ``--annotate``/``--annotate-additions`` were later
    removed entirely) and without re-running any library's comparison to
    collect it.
    """

    def test_annotations_present_on_breaking_library(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 4
        data = json.loads(out)
        [lib] = data["libraries"]
        assert lib["annotations"], "expected at least one annotation entry"
        levels = {entry["level"] for entry in lib["annotations"]}
        assert "error" in levels
        for entry in lib["annotations"]:
            assert set(entry) == {"level", "annotation", "always_visible"}
            assert entry["level"] in ("error", "warning", "notice")
            assert entry["annotation"].startswith("::")

    def test_annotations_empty_list_on_no_change(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        [lib] = data["libraries"]
        assert lib["annotations"] == []

    def test_release_annotations_do_not_rerun_comparison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persisted per-library annotations used to require ``--annotate``
        to trigger `_collect_release_extras`, which re-ran
        `_run_compare_pair` for every library a second time purely to
        recover a `DiffResult`. Both the flag and that re-run helper are
        gone (CLI cleanup phase two, PR E): annotations are read straight
        off the primary pass's stashed `entry["_diff_result"]`/
        `entry["_old_snapshot"]`, so `_run_compare_pair` is called exactly
        once per library, unconditionally.
        """
        import abicheck.cli_compare_release_pairwise as release_mod

        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        real_run_compare_pair = release_mod._run_compare_pair
        calls: list[object] = []

        def _counting_run_compare_pair(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return real_run_compare_pair(*args, **kwargs)

        monkeypatch.setattr(
            release_mod, "_run_compare_pair", _counting_run_compare_pair
        )
        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--format", "json"],
        )
        assert result.exit_code == 4
        assert len(calls) == 1, f"expected exactly one compare per library, got {calls}"
        data = json.loads(result.stdout)
        [lib] = data["libraries"]
        assert lib["annotations"]


class TestReleaseWriteSecondaryOutput:
    """CLI cleanup phase two, PR E: `--write FORMAT=PATH` now works for a
    directory/package (release) `compare` operand, the same as it already
    does for a single-pair one -- it used to be rejected outright
    (`--write is not supported for directory/package (release)
    comparisons`).
    """

    def test_write_json_alongside_markdown_primary(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        write_path = tmp_path / "release.json"
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--write",
            f"json={write_path}",
        )
        assert code == 4
        # Primary format (markdown, the default) still went to stdout.
        assert "BREAKING" in out
        assert write_path.is_file()
        data = json.loads(write_path.read_text())
        [lib] = data["libraries"]
        assert lib["verdict"] == "BREAKING"
        assert lib["annotations"]

    def test_write_does_not_rerun_comparison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--write` renders the secondary format from the same
        already-computed per-library results as the primary format --
        `_run_compare_pair` must be called exactly once per library."""
        import abicheck.cli_compare_release_pairwise as release_mod

        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        real_run_compare_pair = release_mod._run_compare_pair
        calls: list[object] = []

        def _counting_run_compare_pair(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return real_run_compare_pair(*args, **kwargs)

        monkeypatch.setattr(
            release_mod, "_run_compare_pair", _counting_run_compare_pair
        )
        write_path = tmp_path / "release.json"
        code, _out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--write",
            f"json={write_path}",
        )
        assert code == 4
        assert len(calls) == 1, f"expected exactly one compare per library, got {calls}"
        assert write_path.is_file()

    def test_write_same_path_as_output_is_rejected(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)

        same_path = tmp_path / "out.json"
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--format",
            "json",
            "-o",
            str(same_path),
            "--write",
            f"json={same_path}",
        )
        assert code == 64
        assert "--write" in out

    def test_write_sarif_rejected_on_release_operand(self, tmp_path: Path) -> None:
        """`compare`'s own `--write` accepts sarif/html/review too (for a
        single-pair operand) -- those must still be rejected for a
        release operand, not silently fall through to markdown."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)

        write_path = tmp_path / "release.sarif"
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--write",
            f"sarif={write_path}",
        )
        assert code == 64
        assert "--write sarif" in out
        assert not write_path.exists()

    def test_write_junit_from_release_uses_diff_pairs(self, tmp_path: Path) -> None:
        """A junit --write must trigger the same diff-pair collection the
        primary --format junit path already does -- covered separately here
        since collect_diff_results is gated on either fmt or secondary_fmt
        being junit."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        write_path = tmp_path / "release.xml"
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--write",
            f"junit={write_path}",
        )
        assert code == 4
        assert write_path.is_file()
        assert "<testsuite" in write_path.read_text()
