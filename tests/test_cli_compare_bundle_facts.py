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

"""Tests for ``compare --old-bundle-facts`` (frontends/cli/commands/compare_bundle_facts.py).

Exercises the real CLI entry point end to end -- unlike
``TestCompareReleaseAgainstBundleFacts`` in ``test_bundle_side_input.py``,
which calls ``compare_release_against_bundle_facts`` directly as a Python
API and never touches Click parsing/dispatch at all. This closes the gap
that function's own docstring names: "reachable from a real Python caller
today; it is not reachable from ``abicheck compare ...`` yet."

``--bundle-facts-library-manifest``'s own tests (``TestBundleFactsLibraryManifest``)
live in the sibling ``test_cli_compare_bundle_facts_manifest.py`` -- split out
once this file crossed the architecture gate's 1200-line test-file cap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.cli import main
from abicheck.cli_resolve import _resolve_input
from abicheck.serialization import save_bundle_facts


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _build_so(tmp_path: Path, name: str, body: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc is not available")
    src = tmp_path / f"{name}.c"
    src.write_text(body)
    out = tmp_path / name
    res = subprocess.run(
        [
            gcc,
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            str(src),
            "-o",
            str(out),
            f"-Wl,-soname,{name}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"gcc failed: {res.stderr}")
    return out


def _write_old_facts(tmp_path: Path, old_dir: Path, so_path: Path, key: str) -> Path:
    old_snapshot = _resolve_input(
        so_path, [], [], "old", "c++", include_dependencies=True
    )
    facts = capture_bundle_facts({key: old_snapshot})
    facts_path = tmp_path / "old.bundlefacts.json"
    save_bundle_facts(facts, facts_path)
    return facts_path


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses the GNU ld flag -Wl,-soname; ELF/Linux-only bundle analysis "
    "(matches TestCompareReleaseAgainstBundleFacts's identical guard).",
)
@pytest.mark.integration
class TestCompareOldBundleFacts:
    def test_breaking_change_detected_through_the_cli(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        _build_so(
            new_dir,
            "libreal.so",
            "int add(int a, int b, int c) { return a + b + c; }\n",
        )
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "json",
        )

        assert code == 4, out
        payload = json.loads(out)
        assert payload["mode"] == "bundle_facts"
        assert payload["verdict"] == "BREAKING"
        lib = payload["libraries"]["libreal.so"]
        assert lib["verdict"] == "BREAKING"
        assert any(c["kind"] == "func_params_changed" for c in lib["changes"])

    def test_unchanged_library_is_no_change(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "json",
        )

        assert code == 0, out
        payload = json.loads(out)
        assert payload["verdict"] == "NO_CHANGE"

    def test_markdown_format_renders(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "markdown",
        )

        assert code == 0, out
        assert "Bundle-facts comparison" in out
        assert "libreal.so" in out

    def test_suppress_flows_through_to_the_per_library_diff(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        _build_so(
            new_dir,
            "libreal.so",
            "int add(int a, int b, int c) { return a + b + c; }\n",
        )
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        suppress_path = tmp_path / "suppress.yaml"
        suppress_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - change_kind: func_params_changed\n"
            "    symbol: add\n"
            '    reason: "test suppression"\n'
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--suppress",
            str(suppress_path),
            "--format",
            "json",
        )

        assert code == 0, out
        payload = json.loads(out)
        assert payload["verdict"] == "NO_CHANGE"
        lib = payload["libraries"]["libreal.so"]
        assert lib["suppression"]["suppressed_count"] == 1

    def test_max_json_object_nodes_override_is_honored(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--max-json-object-nodes",
            "1",
            "--format",
            "json",
        )

        # A real facts document has more than one JSON container; a budget
        # of 1 must reject it with a clean CLI error, not a raw traceback.
        assert code != 0
        assert "JSON containers" in out
        assert "Traceback" not in out

    def test_format_sarif_is_rejected(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "sarif",
        )

        assert code == 64
        assert "not available" in out

    def test_fail_on_removed_library_is_rejected(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--fail-on-removed-library",
            "--format",
            "json",
        )

        assert code == 64
        assert "--fail-on-removed-library" in out

    def test_bundle_facts_out_together_with_old_bundle_facts_is_rejected(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--bundle-facts-out",
            str(tmp_path / "out.json"),
            "--format",
            "json",
        )

        assert code == 64
        assert "--bundle-facts-out" in out

    def test_new_input_package_is_extracted(self, tmp_path: Path) -> None:
        # Codex review: NEW_INPUT's own help text promises "a live release
        # directory/package", but compare_release_against_bundle_facts()
        # treated any non-directory path as a single library file --
        # a package archive silently produced zero matches instead of the
        # shared library inside it. Packs a real gcc-built .so into a
        # .tar.gz and points --old-bundle-facts's NEW_INPUT at the archive
        # itself, proving the breaking change is still detected once the
        # archive is extracted first.
        import tarfile

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        _build_so(
            new_dir,
            "libreal.so",
            "int add(int a, int b, int c) { return a + b + c; }\n",
        )
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        archive_path = tmp_path / "release.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(new_dir / "libreal.so", arcname="libreal.so")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(archive_path),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "json",
        )

        assert code == 4, out
        payload = json.loads(out)
        assert payload["verdict"] == "BREAKING"
        lib = payload["libraries"]["libreal.so"]
        assert lib["verdict"] == "BREAKING"
        assert any(c["kind"] == "func_params_changed" for c in lib["changes"])

    def test_write_secondary_output_renders_and_writes(self, tmp_path: Path) -> None:
        # Codex review: --write FORMAT=PATH was accepted but this dispatcher
        # never read secondary_fmt/secondary_output, so the command exited
        # successfully without ever creating the promised second artifact.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        secondary_path = tmp_path / "secondary.md"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--write",
            f"markdown={secondary_path}",
            "--format",
            "json",
        )

        assert code == 0, out
        json.loads(out)
        assert secondary_path.exists()
        assert "Bundle-facts comparison" in secondary_path.read_text()

    def test_zero_matched_libraries_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: an empty NEW_INPUT means nothing matches any key in
        # OLD_FACTS's per_library_snapshots, so compare_release_against_
        # bundle_facts() returns an empty per_library list -- previously
        # this scored NO_CHANGE (exit 0), reporting a clean bill of health
        # for a comparison that never actually ran.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code == 1, out
        assert "matched" in out
        assert "Traceback" not in out

    def test_output_dir_sanitizes_a_malicious_library_name(
        self, tmp_path: Path
    ) -> None:
        # Codex review: DiffResult.library is copied from the OLD
        # snapshot's own `library` field (checker.py: `library=old.library`)
        # -- a value independent of the per_library_snapshots dict key used
        # for matching, and fully attacker-controlled in a hand-crafted
        # OLD_FACTS document. An unsanitized f"{diff.library}.json" could
        # escape --output-dir entirely.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)

        old_snapshot = _resolve_input(
            old_dir / "libreal.so", [], [], "old", "c++", include_dependencies=True
        )
        # The dict key ("libreal.so") is what matches the real NEW-side
        # canonical key; the snapshot's own `.library` attribute is a
        # separate, independently-controlled field -- tampered here to
        # simulate a malicious OLD_FACTS document.
        old_snapshot.library = "../../evil"
        facts = capture_bundle_facts({"libreal.so": old_snapshot})
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)
        output_dir = tmp_path / "out_dir"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        )

        assert code == 0, out
        assert not (tmp_path / "evil.json").exists()
        assert (output_dir / "evil.json").exists()

    def test_output_dir_collision_with_primary_output_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Codex review: --output-dir's per-library filename
        (`{diff.library}.json`) is known up front from OLD_FACTS -- when it
        collides with -o/--output's own path, the primary report (written
        first) was silently clobbered by the per-library write (written
        second) with no signal either artifact was lost. Reject the
        collision before any artifact is written instead."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        output_dir = tmp_path / "reports"
        colliding_output = output_dir / "libreal.so.json"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--output-dir",
            str(output_dir),
            "-o",
            str(colliding_output),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "collide" in out
        assert not colliding_output.exists()

    def test_output_dir_itself_colliding_with_primary_output_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: the per-library-filename collision
        check above only catches a collision with a *generated child*
        report path -- it misses the more direct case where --output-dir
        itself names the same, previously nonexistent path as -o/--output.
        Both Click options happily accept that combination; without this
        check, the primary write creates a *file* at that path and the
        following `output_dir.mkdir(...)` then raises a raw
        FileExistsError instead of a clean usage error."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        same_path = tmp_path / "shared-path"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--output-dir",
            str(same_path),
            "-o",
            str(same_path),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "collide" in out.lower() or "same path" in out.lower()
        assert not same_path.exists()

    def test_output_dir_that_is_already_an_existing_file_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: an --output-dir that already
        exists as a regular file -- entirely unrelated to -o/--output/
        --write -- names neither reserved path, so the collision checks
        above don't catch it; `output_dir.mkdir(parents=True,
        exist_ok=True)` still raises a raw FileExistsError for it
        (`exist_ok=True` only tolerates an existing *directory*), after the
        primary report has already been written."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        # A pre-existing regular file at the path --output-dir will name --
        # not related to -o/--output at all.
        preexisting_file = tmp_path / "not-a-directory"
        preexisting_file.write_text("leftover from an earlier run\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--output-dir",
            str(preexisting_file),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "not a directory" in out.lower()
        assert preexisting_file.read_text() == "leftover from an earlier run\n"

    def test_output_dir_mkdir_failure_is_a_clean_error_not_a_raw_traceback(
        self, tmp_path: Path
    ) -> None:
        """--output-dir naming a path whose *parent* is an existing regular
        file (not output_dir itself, which the previous test already
        covers) makes `output_dir.mkdir(parents=True, exist_ok=True)`
        raise `NotADirectoryError` -- a case the explicit-file precondition
        check above cannot catch, since it only inspects output_dir itself,
        not its ancestors. Mirrors _safe_write_output's own OSError ->
        ClickException translation for the identical mkdir operation."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        blocking_file = tmp_path / "blocking-file"
        blocking_file.write_text("not a directory\n")
        output_dir = blocking_file / "reports"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        )

        assert code == 1, out
        assert "Cannot create" in out
        assert "Traceback" not in out

    def test_output_dir_mkdir_failure_leaves_no_partial_primary_report(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: the previous test above proves the
        failure is clean, but not that it's *complete* -- with -o/--output
        also given, the primary report used to be rendered and written
        (_safe_write_output(Path(output), text)) before --output-dir's own
        mkdir ran, so a command that ultimately fails with exit 1 still left
        a seemingly valid summary.json on disk. --output-dir's directory is
        now created (and validated) before any report is written, so a
        precondition failure here must leave no artifact behind at all."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        blocking_file = tmp_path / "blocking-file"
        blocking_file.write_text("not a directory\n")
        output_dir = blocking_file / "reports"
        summary_path = tmp_path / "summary.json"

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "-o",
            str(summary_path),
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        )

        assert code == 1, out
        assert "Cannot create" in out
        assert "Traceback" not in out
        assert not summary_path.exists(), (
            "primary report was written before --output-dir's own "
            "precondition failure -- a partial artifact was left behind "
            "despite the overall command failing"
        )


class TestRenderJsonCarriesRunOutcome:
    """Codex review, fresh evidence: `_render_json`'s summary previously
    omitted `run_outcome` entirely, an exception to this repo's own "every
    compare/release JSON report carries run_outcome" contract
    (`docs/use/output-formats.md`). Unlike the live directory/package
    release fan-out, `BundleDiffResult.verdict` is always a real `Verdict`
    -- never the "ERROR"/"not_comparable" operational sentinels -- so this
    exercises `_render_json` directly against a hand-built result rather
    than compiling real binaries (no `gcc` dependency needed)."""

    def _result(self, verdict) -> object:
        from abicheck.bundle_models import BundleDiffResult
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult

        changes = (
            [Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="d")]
            if verdict != Verdict.NO_CHANGE
            else []
        )
        diff = DiffResult(
            old_version="old",
            new_version="new",
            library="libfoo.so",
            changes=changes,
            verdict=verdict,
        )
        return BundleDiffResult(
            old_root=Path("/old"), new_root=Path("/new"), per_library=[diff]
        )

    def test_breaking_verdict_gets_a_real_gate(self, tmp_path: Path) -> None:
        from abicheck.change_registry_types import Verdict
        from abicheck.frontends.cli.commands.compare_bundle_facts import _render_json

        out = _render_json(
            self._result(Verdict.BREAKING),
            old_facts_path=tmp_path / "old.bundlefacts.json",
            new_dir=tmp_path / "new",
        )
        payload = json.loads(out)
        run_outcome = payload["run_outcome"]
        assert run_outcome["compatibility"] == "BREAKING"
        assert run_outcome["gate"] == "abi_breaking"
        assert run_outcome["operational"] == "none"

    def test_no_change_verdict_gets_a_clean_gate(self, tmp_path: Path) -> None:
        from abicheck.change_registry_types import Verdict
        from abicheck.frontends.cli.commands.compare_bundle_facts import _render_json

        out = _render_json(
            self._result(Verdict.NO_CHANGE),
            old_facts_path=tmp_path / "old.bundlefacts.json",
            new_dir=tmp_path / "new",
        )
        payload = json.loads(out)
        run_outcome = payload["run_outcome"]
        assert run_outcome["compatibility"] == "NO_CHANGE"
        assert run_outcome["gate"] == "none"
