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

"""Tests for ``--bundle-facts-library-manifest`` (G38 Phase 17).

Split out of ``test_cli_compare_bundle_facts.py`` once that file crossed
the architecture gate's 1200-line test-file cap (``tests/CLAUDE.md``'s own
documented convention for this situation -- see e.g.
``test_run_plan_gate_policy.py``/``test_mutation_run_scoping.py``'s
identical splits), mirroring the production split between
``frontends/cli/commands/compare_bundle_facts.py`` (CLI dispatch, covered
by ``TestCompareOldBundleFacts`` in the sibling file) and
``workflows/bundle_facts_library_overrides.py`` (the manifest parser this
file's tests exercise end to end via the real CLI).
"""

from __future__ import annotations

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
    "(matches TestCompareOldBundleFacts's identical guard).",
)
@pytest.mark.integration
class TestBundleFactsLibraryManifest:
    """``--bundle-facts-library-manifest`` (G38 Phase 17): per-library
    header/include/compile-context overrides for a mixed-toolchain bundle."""

    def test_manifest_works_when_new_input_is_a_direct_file_not_a_directory(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: compare_release_against_bundle_
        facts() itself supports a direct single-file NEW_INPUT (``if
        new_dir.is_dir(): ... else: new_files = [new_dir]``,
        bundle_side_input.py) -- but known_libraries_for_new_side() (the
        manifest's own pre-validation gate) called discover_shared_
        libraries() unconditionally, which walks a directory tree and
        silently returns no entries for a plain file, rejecting every
        manifest library key as unknown even though the comparison itself
        would succeed fine."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        new_so = _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so:\n  headers: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_so),  # the .so FILE itself, not new_dir
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 0, out

    def test_rejected_when_old_input_is_not_stored_bundle_facts(
        self, tmp_path: Path
    ) -> None:
        # OLD_INPUT carries no artifact_type marker, so CLI cleanup phase
        # two, PR I's automatic classification does not route this to
        # compare_bundle_facts.dispatch() -- --bundle-facts-library-manifest
        # is rejected the ordinary way instead.
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
        assert "--bundle-facts-library-manifest" in out
        assert "stored BundleFacts document" in out

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
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "typo_lib.so" in out
        assert "not a library in this bundle" in out

    def test_manifest_entry_for_a_new_only_library_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Codex review: known_libraries_for_new_side() only knows the
        NEW-side canonical key set -- a library present in NEW_INPUT but
        absent from OLD_FACTS (an added library) passes that check, yet the
        real per-library comparison loop (bundle_side_input.py) only
        iterates keys present in BOTH sides, so the override for it would
        silently never be consulted. Must be rejected instead."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        # "libextra.so" exists only on the NEW side -- an added library, not
        # present in OLD_FACTS's own per_library_snapshots.
        _build_so(new_dir, "libextra.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libextra.so:\n  headers: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "libextra.so" in out

    def test_manifest_key_naming_a_non_selected_version_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: when NEW_INPUT carries more than
        one version of a library (``libreal.so.1`` and ``libreal.so.2``),
        ``build_match_map()`` selects exactly one of them (the higher
        version, ``.2``) -- a manifest key naming the *other*, non-selected
        version (``.1``) canonicalizes to the same bundle key and would
        otherwise silently apply its override to the file it never actually
        named."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        # Two versions of the same canonical library on the NEW side --
        # build_match_map()'s own version-aware tie-break selects ".2".
        _build_so(new_dir, "libreal.so.1", body)
        _build_so(new_dir, "libreal.so.2", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so.1:\n  headers: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 64, out
        assert "libreal.so.1" in out
        assert "libreal.so.2" in out

    def test_manifest_key_naming_the_selected_version_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """The positive-path sibling of the above: naming the version
        build_match_map() actually selected must still work."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so.1", body)
        _build_so(new_dir, "libreal.so.2", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("libreal.so.2:\n  headers: []\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 0, out

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

    def test_depth_binary_ignores_extracted_package_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1 Codex finding: --depth binary's uniform ``headers = []`` clear
        (dispatch()'s own comment above) is undone right below it -- when
        NEW_INPUT is a package archive or ``--devel-pkg new=...`` extraction
        yields a non-None ``header_dir``, ``if not headers: headers =
        [header_dir]`` reassigns the operand back to a non-empty list,
        silently re-enabling L2 header extraction under a depth that
        promises pure L0/L1 evidence with no header AST at all.

        Simulates a package/devel-pkg extraction that discovers a
        ``header_dir`` by monkeypatching ``_extract_if_package`` (the exact
        primitive ``dispatch()`` calls) rather than building a real archive
        fixture -- this is the one thing that varies from the plain
        already-extracted-directory case ``_extract_if_package``'s own
        docstring describes.
        """
        import abicheck.bundle_side_input as bundle_side_input_mod
        import abicheck.cli_compare_release_helpers as cli_compare_release_helpers_mod

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

        header_dir = tmp_path / "extracted_headers"
        header_dir.mkdir()
        (header_dir / "libreal.h").write_text("int add(int a, int b);\n")

        real_extract_if_package = cli_compare_release_helpers_mod._extract_if_package

        def _fake_extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            make_temp_dir,
            is_package,
            detect_extractor,
        ):
            lib_dir, debug_dir, _header_dir, symbols_file = real_extract_if_package(
                input_path,
                debug_pkg,
                devel_pkg,
                make_temp_dir,
                is_package,
                detect_extractor,
            )
            return lib_dir, debug_dir, header_dir, symbols_file

        monkeypatch.setattr(
            cli_compare_release_helpers_mod,
            "_extract_if_package",
            _fake_extract_if_package,
        )

        captured: dict[str, object] = {}
        real_compare = bundle_side_input_mod.compare_release_against_bundle_facts

        def _spy(*args: object, **kwargs: object):
            captured.update(kwargs)
            return real_compare(*args, **kwargs)

        monkeypatch.setattr(
            bundle_side_input_mod, "compare_release_against_bundle_facts", _spy
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--include-system-declarations",
            "--depth",
            "binary",
            "--format",
            "json",
        )

        assert code == 0, out
        assert captured["headers"] is None

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
            "--include-system-declarations",
            "--bundle-facts-library-manifest",
            str(manifest),
            "--format",
            "json",
        )

        assert code == 0, out
        assert captured["per_library_headers"] == {"libreal.so": [header_dir.resolve()]}
