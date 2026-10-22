"""Direct tests for the ``--bundle-facts-out`` collision guards the release
CLI runs before it writes anything.

Each guard rejects exactly the path an export of the same run would
overwrite, names that export in its message, and is a no-op otherwise. The
CLI-level rejection tests exercise these through Click; this module pins
every branch on the guard itself, so a branch the CLI never reaches cannot
go unchecked.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest

from abicheck.frontends.cli.release_compare_request import (
    reject_bundle_facts_out_collision,
    reject_bundle_facts_out_dir_collision,
)


class TestBundleFactsOutCollision:
    def test_no_bundle_facts_path_is_a_no_op(self, tmp_path: Path) -> None:
        same = tmp_path / "report.json"
        assert reject_bundle_facts_out_collision(None, same, same) is None

    @pytest.mark.parametrize(
        ("label", "position"),
        [("--output/-o", "output"), ("--write", "secondary")],
    )
    def test_rejects_the_path_of_each_export(
        self, tmp_path: Path, label: str, position: str
    ) -> None:
        bundle = tmp_path / "facts.json"
        export = tmp_path / "facts.json"
        if position == "output":
            call = (bundle, export)
        else:
            call = (bundle, None, export)
        with pytest.raises(click.UsageError) as excinfo:
            reject_bundle_facts_out_collision(*call)
        assert f"must differ from {label}" in excinfo.value.format_message()

    def test_a_second_secondary_export_is_checked_too(self, tmp_path: Path) -> None:
        """``-o`` is repeatable: the collision must be caught on any of the
        requested artifacts, not only the first."""
        bundle = tmp_path / "facts.json"
        first = tmp_path / "a.json"
        second = tmp_path / "facts.json"
        with pytest.raises(
            click.UsageError, match=re.escape("must differ from --write")
        ):
            reject_bundle_facts_out_collision(bundle, None, first, second)

    def test_distinct_paths_are_accepted(self, tmp_path: Path) -> None:
        assert (
            reject_bundle_facts_out_collision(
                tmp_path / "facts.json",
                tmp_path / "report.json",
                tmp_path / "other.md",
            )
            is None
        )


class TestBundleFactsOutDirCollision:
    def test_no_bundle_facts_path_is_a_no_op(self, tmp_path: Path) -> None:
        assert reject_bundle_facts_out_dir_collision(None, tmp_path, {}) is None

    def test_no_output_dir_is_a_no_op(self, tmp_path: Path) -> None:
        assert (
            reject_bundle_facts_out_dir_collision(
                tmp_path / "facts.json", None, {"libfoo.so": Path("libfoo.so")}
            )
            is None
        )

    def test_rejects_the_per_component_summary(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        with pytest.raises(click.UsageError) as excinfo:
            reject_bundle_facts_out_dir_collision(
                output_dir / "summary.json", output_dir, {}
            )
        message = excinfo.value.format_message()
        assert "per-component export's own summary.json" in message

    def test_rejects_a_per_library_report_and_names_the_library(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        old_map = {"libfoo.so": Path("/release/old/libfoo.so")}
        with pytest.raises(click.UsageError) as excinfo:
            reject_bundle_facts_out_dir_collision(
                output_dir / "libfoo.json", output_dir, old_map
            )
        message = excinfo.value.format_message()
        assert "export's own report for 'libfoo.so'" in message
        assert str(output_dir / "libfoo.json") in message

    def test_distinct_paths_are_accepted(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        assert (
            reject_bundle_facts_out_dir_collision(
                tmp_path / "facts.json",
                output_dir,
                {"libfoo.so": Path("/release/old/libfoo.so")},
            )
            is None
        )
