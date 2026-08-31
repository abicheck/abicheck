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

"""``compare --old-bundle-facts`` early-rejection tests, continued -- split
out of ``test_cli_compare_bundle_facts_rejections.py`` (which itself sits at
the architecture no-growth test-file cap) to keep this addition's tests
together without pushing that file over it.

Every test here proves that an unsupported flag/config-block combination is
rejected *before* any real facts loading/comparison happens, so none of them
need a real gcc-built bundle -- a placeholder OLD_INPUT/NEW_INPUT pair is
enough. See ``test_cli_compare_bundle_facts.py``'s own
``TestCompareOldBundleFacts`` class for the end-to-end, gcc-built-bundle
tests this file deliberately doesn't duplicate, and
``test_cli_compare_bundle_facts_rejections.py`` for the earlier-round
rejection tests this file continues.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestCompareOldBundleFactsEarlyRejections:
    """Continues ``test_cli_compare_bundle_facts_rejections.py``'s class of
    the same name -- see that file's own docstring for why the split."""

    def test_old_side_header_operand_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: normalize_sided_options puts an old=-scoped --header
        # into old_headers_only, but _resolve_new_side_headers_includes only
        # ever reads the new=-scoped/uniform fields -- OLD_FACTS is already
        # a resolved, stored snapshot with no header re-extraction
        # available, so this was silently discarded rather than applied.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_header_dir = tmp_path / "old_headers"
        old_header_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--header",
            f"old={old_header_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--header" in out

    def test_old_side_include_operand_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_include_dir = tmp_path / "old_includes"
        old_include_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include",
            f"old={old_include_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--include" in out

    def test_report_mode_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: every nested per-library report is rendered via
        # reporter.to_json(diff) with no report_mode argument -- always the
        # "full" default, regardless of what was requested.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--report-mode",
            "leaf",
            "--format",
            "json",
        )

        assert code == 64
        assert "--report-mode" in out

    def test_show_filtered_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--show-filtered",
            "--format",
            "json",
        )

        assert code == 64
        assert "--show-filtered" in out

    def test_explicit_jobs_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: compare_release_against_bundle_facts() processes
        # every matched library in a synchronous loop -- an explicit
        # -j/--jobs N request was silently dropped.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--jobs",
            "4",
            "--format",
            "json",
        )

        assert code == 64
        assert "--jobs" in out

    def test_default_jobs_is_not_rejected_by_itself(self, tmp_path: Path) -> None:
        # The silent default (0, "auto-detect") is indistinguishable here
        # from the flag never having been given, so it must not trip the
        # --jobs rejection on its own -- confirmed via a malformed OLD_FACTS
        # document that fails for an unrelated reason first.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("not json")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--jobs",
            "0",
            "--format",
            "json",
        )

        assert code != 64 or "--jobs" not in out

    def test_malformed_package_extraction_failure_is_a_clean_error(
        self, tmp_path: Path
    ) -> None:
        # Codex review: _extract_if_package() is called outside the
        # SnapshotError-to-ClickException boundary, so a recognized-but-
        # malformed package (SnapshotError raised from inside extraction
        # itself, not from the later compare_release_against_bundle_facts()
        # call) leaked a raw Python traceback instead of the normal
        # concise operational error. A .deb (ar archive) with no
        # data.tar.* member reliably reproduces this -- `ar x` succeeds,
        # DebExtractor._deb_extract raises a real SnapshotError.
        ar = shutil.which("ar")
        if ar is None:
            pytest.skip("ar is not available")
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "dummy.txt").write_text("dummy\n")
        fake_deb = tmp_path / "fake.deb"
        subprocess.run(
            [ar, "rcs", str(fake_deb), "dummy.txt"],
            cwd=staging,
            check=True,
            capture_output=True,
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(fake_deb),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code != 0
        assert "Traceback" not in out
        assert "data.tar" in out

    def test_config_source_method_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: source.method (s1-s6) drives run_compare's own
        # _resolve_compare_collect_mode, which this dispatcher never calls
        # -- same root cause as --depth build/source, no channel for L3-L5
        # build/source evidence collection, config-declared or not.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("source:\n  method: s3\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--config",
            str(config_path),
            "--format",
            "json",
        )

        assert code == 64
        assert "source:" in out

    def test_old_side_debug_info_operand_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --debug-info old=... normalizes to debug_info1, but
        # the existing guard checked only debug_info2 -- OLD_FACTS is
        # already a resolved, stored snapshot with nothing left to
        # re-extract debug info from either.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_debug_dir = tmp_path / "old_debug"
        old_debug_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--debug-info",
            f"old={old_debug_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--debug-info" in out

    def test_old_side_devel_pkg_operand_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --devel-pkg old=... normalizes to devel_pkg1, but
        # only devel_pkg2 (the NEW-side scope) was ever read -- OLD_FACTS
        # is already a resolved, stored snapshot with no OLD-side
        # extraction to feed.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_devel_pkg = tmp_path / "old-devel.tar"
        old_devel_pkg.touch()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--devel-pkg",
            f"old={old_devel_pkg}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--devel-pkg" in out
