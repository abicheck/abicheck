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


@pytest.mark.integration
class TestBundleFactsLibraryManifest:
    """``--bundle-facts-library-manifest`` (G38 Phase 17): per-library
    header/include/compile-context overrides for a mixed-toolchain bundle."""

    def test_rejected_without_old_bundle_facts(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        old.write_text("{}")
        new = tmp_path / "new.json"
        new.write_text("{}")
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("{}")

        code, out = _invoke(
            "compare",
            str(old),
            str(new),
            "--bundle-facts-library-manifest",
            str(manifest),
        )

        assert code == 64, out
        assert "--old-bundle-facts" in out

    def test_unknown_library_name_is_rejected(self, tmp_path: Path) -> None:
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
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("typo_lib.so:\n  headers: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "typo_lib.so" in out
        assert "not a library in this bundle" in out

    def test_malformed_manifest_is_a_clean_error(self, tmp_path: Path) -> None:
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
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so: [not, a, mapping]\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "must be a mapping" in out

    def test_unhashable_yaml_key_is_a_clean_usage_error(self, tmp_path: Path) -> None:
        """Codex review, fresh evidence: a syntactically valid YAML mapping
        can use a non-scalar (list) node as a key (``? [a, b]\\n: 1``),
        which raises a raw, untranslated ``TypeError`` inside the
        duplicate-key-checking loader -- confirm it now surfaces as the
        same clean exit-64 usage error every other malformed manifest here
        produces, all the way through the real CLI."""
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
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so:\n  ? [a, b]\n  : 1\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "invalid YAML" in out

    def test_per_library_headers_reach_compare_release_against_bundle_facts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direct kwarg-forwarding check, mirroring
        ``TestCompareReleaseAgainstBundleFactsResolutionUnit``'s style in
        ``test_bundle_side_input.py`` -- proves the manifest's parsed maps
        reach the real Python-API entry point unchanged, without needing a
        second toolchain to prove a resulting finding differs (Phase 17's
        own "Testing bar" text; the end-to-end ABI-difference case is left
        to the Python-API layer's own ``TestCompareReleaseAgainstBundleFacts``,
        which already covers real per-library header/compile forwarding)."""
        import abicheck.bundle_side_input as bundle_side_input_mod

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
        header_dir = tmp_path / "libreal_headers"
        header_dir.mkdir()
        (header_dir / "libreal.h").write_text("int add(int a, int b);\n")
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(f"libreal.so:\n  headers:\n    - {header_dir}\n")

        captured: dict[str, object] = {}
        real = bundle_side_input_mod.compare_release_against_bundle_facts

        def _spy(*args: object, **kwargs: object):
            captured.update(kwargs)
            return real(*args, **kwargs)

        # dispatch() resolves this function via importlib.import_module at
        # call time (see its own docstring for why) rather than a static
        # import, so patching the attribute on the real module it looks up
        # is what actually takes effect here.
        monkeypatch.setattr(
            bundle_side_input_mod, "compare_release_against_bundle_facts", _spy
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 0, out
        assert captured["per_library_headers"] == {"libreal.so": [header_dir]}
        assert captured["per_library_includes"] == {}
        assert captured["per_library_compile"] == {}

    def test_depth_binary_clears_manifest_headers_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review: --depth binary already clears the uniform
        --header/--new-header operand (see dispatch()'s own comment) so the
        comparison stays pure L0/L1 with no L2 header AST -- but a
        manifest-supplied per-library header root was not cleared alongside
        it, so that one library would still run L2 extraction and report
        findings outside the requested depth."""
        import abicheck.bundle_side_input as bundle_side_input_mod

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
        header_dir = tmp_path / "libreal_headers"
        header_dir.mkdir()
        (header_dir / "libreal.h").write_text("int add(int a, int b);\n")
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(f"libreal.so:\n  headers:\n    - {header_dir}\n")

        captured: dict[str, object] = {}
        real = bundle_side_input_mod.compare_release_against_bundle_facts

        def _spy(*args: object, **kwargs: object):
            captured.update(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(
            bundle_side_input_mod, "compare_release_against_bundle_facts", _spy
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--depth",
            "binary",
            "--format",
            "json",
        )

        assert code == 0, out
        assert captured["headers"] is None
        assert captured["per_library_headers"] == {}
        assert captured["per_library_includes"] == {}
        assert captured["per_library_compile"] == {}

    def test_duplicate_library_key_is_rejected(self, tmp_path: Path) -> None:
        """Codex review: plain ``yaml.safe_load()`` silently keeps only the
        last value of a repeated mapping key, so a manifest with two entries
        for the same library would silently forward the last one's overrides
        with no signal the first was ever written. The manifest loader now
        uses the same duplicate-checking YAML loader ``--dump-manifest``
        uses, which raises instead."""
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
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so:\n  headers: []\nlibreal.so:\n  includes: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "duplicate key" in out
        assert "libreal.so" in out

    def test_relative_manifest_paths_anchor_to_the_manifest_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review: a manifest is a portable document -- a relative
        ``headers``/``includes``/``sysroot`` path inside it must resolve
        against the manifest file's own directory, not whatever directory
        the ``compare`` process happens to be launched from."""
        import abicheck.bundle_side_input as bundle_side_input_mod

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        manifest_dir = tmp_path / "manifest_dir"
        old_dir.mkdir()
        new_dir.mkdir()
        manifest_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        header_dir = manifest_dir / "headers"
        header_dir.mkdir()
        (header_dir / "libreal.h").write_text("int add(int a, int b);\n")
        manifest = manifest_dir / "manifest.yaml"
        manifest.write_text("libreal.so:\n  headers:\n    - headers\n")

        captured: dict[str, object] = {}
        real = bundle_side_input_mod.compare_release_against_bundle_facts

        def _spy(*args: object, **kwargs: object):
            captured.update(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(
            bundle_side_input_mod, "compare_release_against_bundle_facts", _spy
        )
        # Launch from a directory that is neither the manifest's own
        # directory nor contains a same-named "headers" subdirectory --
        # proving resolution anchors to the manifest file, not the process's
        # current working directory.
        monkeypatch.chdir(old_dir)

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 0, out
        assert captured["per_library_headers"] == {
            "libreal.so": [header_dir.resolve()]
        }
