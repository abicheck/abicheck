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

"""A stored-bundle-facts-OLD_INPUT ``compare``'s early-rejection tests,
continued -- split out of ``test_cli_compare_bundle_facts_rejections.py``
(which itself sits at the architecture no-growth test-file cap) to keep this
addition's tests together without pushing that file over it.

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

#: See ``test_cli_compare_bundle_facts_rejections.py``'s identical constant
#: docstring: CLI cleanup phase two, PR I's automatic operand classification
#: needs the ``artifact_type`` marker present to route OLD_INPUT to
#: ``compare_bundle_facts.dispatch()`` at all -- a bare ``"{}"`` no longer
#: classifies as bundle facts.
_STUB_BUNDLE_FACTS_JSON = (
    '{"artifact_type": "abicheck.bundle-facts", "schema_version": 2, '
    '"per_library_snapshots": {}}'
)

#: Carries the marker (so it still classifies and reaches dispatch()) but
#: omits the required ``per_library_snapshots`` key, so it fails for an
#: unrelated reason *after* the option checks this file's "default value
#: isn't rejected by itself" tests need to run first -- the same role
#: plain ``"not json"`` played back when the flag forced routing
#: unconditionally.
_MALFORMED_BUT_CLASSIFIABLE_JSON = (
    '{"artifact_type": "abicheck.bundle-facts", "schema_version": 2}'
)


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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_header_dir = tmp_path / "old_headers"
        old_header_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--header",
            f"old={old_header_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--header" in out

    def test_old_side_include_operand_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_include_dir = tmp_path / "old_includes"
        old_include_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--include",
            f"old={old_include_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--include" in out

    def test_old_side_ast_frontend_operand_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: normalize_sided_options puts an old=-scoped
        # --ast-frontend into old_header_backend, but dispatch() only ever
        # reads the new=-scoped/uniform header_backend value -- OLD_FACTS
        # is already a resolved, stored snapshot with no header
        # re-extraction available.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--ast-frontend",
            "old=clang",
            "--format",
            "json",
        )

        assert code == 64
        assert "--ast-frontend" in out

    def test_explicit_demangle_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --demangle is documented to apply to markdown
        # output, but this dispatcher's markdown rendering calls
        # bundle.render_bundle_findings_markdown() directly, which has no
        # demangle parameter at all -- the live release fan-out's own
        # bundle-findings markdown section has this identical pre-existing
        # gap, so implementing it only here would disagree with what that
        # shared renderer already does.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--demangle",
            "--format",
            "markdown",
        )

        assert code == 64
        assert "--demangle" in out

    def test_default_demangle_is_not_rejected_by_itself(self, tmp_path: Path) -> None:
        # The silent default (None, "demangle ON") is left un-rejected,
        # matching the --jobs precedent -- confirmed via a malformed
        # OLD_FACTS document that fails for an unrelated reason first.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_MALFORMED_BUT_CLASSIFIABLE_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--format",
            "markdown",
        )

        assert code == 1, out
        assert "--demangle" not in out

    def test_report_mode_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: every nested per-library report is rendered via
        # reporter.to_json(diff) with no report_mode argument -- always the
        # "full" default, regardless of what was requested.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--report-mode",
            "leaf",
            "--format",
            "json",
        )

        assert code == 64
        assert "--report-mode" in out

    def test_show_filtered_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
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
        facts_path.write_text(_MALFORMED_BUT_CLASSIFIABLE_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--jobs",
            "0",
            "--format",
            "json",
        )

        assert code == 1, out
        assert "--jobs" not in out

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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("source:\n  method: s3\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_debug_dir = tmp_path / "old_debug"
        old_debug_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
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
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_devel_pkg = tmp_path / "old-devel.tar"
        old_devel_pkg.touch()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--devel-pkg",
            f"old={old_devel_pkg}",
            "--format",
            "json",
        )

        assert code == 64

    def test_abicheck_yml_bundle_block_reaches_stored_facts_compare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review, fresh evidence: PR J removed --bundle-system-
        # providers/--bundle-cohort as CLI flags in favor of .abicheck.yml's
        # bundle: block, but this stored-BundleFacts dispatch path derived
        # both settings from the now-removed Click kwargs -- always empty --
        # rather than the resolved config, silently discarding a declared
        # bundle: block instead of honoring or rejecting it. Proven the same
        # way test_depth_binary_clears_headers (sibling file) is:
        # monkeypatch compare_release_against_bundle_facts and capture what
        # dispatch() forwards, rather than a real gcc build.
        import abicheck.bundle_side_input as bundle_side_input

        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            'bundle:\n  system_providers: ["libvendor.so.1"]\n'
            '  cohorts: ["libfoo_"]\n',
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        def _fake_compare(*args: object, **kwargs: object) -> None:
            captured["system_providers"] = kwargs.get("system_providers")
            captured["cohorts"] = kwargs.get("cohorts")
            raise ValueError("stop-here")

        monkeypatch.setattr(
            bundle_side_input, "compare_release_against_bundle_facts", _fake_compare
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--config",
            str(config_path),
            "--format",
            "json",
        )

        assert code == 1, out
        assert captured["system_providers"] == ["libvendor.so.1"]
        assert captured["cohorts"] == ["libfoo_"]

    def test_malformed_auto_discovered_config_exits_64_before_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A malformed config -- auto-discovered or explicit -- is caught by
        # compare.py's own resolve_compile_context call site (a UsageError,
        # exit 64) before dispatch() ever runs: that call site always
        # forwards the resolved path as an *explicit* build_config, so
        # merge_compile_config's auto-discovered-is-best-effort exception
        # never applies here. Proves dispatch()'s own bundle: read has
        # nothing left to validate.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text(_STUB_BUNDLE_FACTS_JSON)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (tmp_path / ".abicheck.yml").write_text(
            "bundle:\n  system_providers: 123\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--format",
            "json",
        )

        assert code == 64, out
