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

"""Tests for the stored/stored ``compare`` operand shape (CLI cleanup phase
two, PR I): both OLD_INPUT and NEW_INPUT classify as stored ``BundleFacts``
documents.

Companion to ``test_cli_compare_bundle_facts.py`` (stored OLD_INPUT / live
NEW_INPUT) and ``test_cli_compare_bundle_facts_rejections*.py`` (that
shape's own early-rejection tests) -- this file covers the newer shape
those predate: routing (``compare_bundle_operand_dispatch.py``), the
NEW-side-specific rejections (``compare_bundle_facts_rejections.
_reject_new_side_extraction_options_for_stored_pair``), and one real
end-to-end comparison through the actual CLI entry point. No gcc/castxml
needed anywhere here: both sides are already resolved, stored documents, so
``compare_stored_bundle_facts_pair``
(``workflows/bundle_stored_pair_compare.py``) reads no binaries and parses
no header AST on either side -- see that function's own docstring. Its own
direct-call unit tests (including the variant-fingerprint mismatch check)
live in ``tests/test_bundle_stored_pair_compare.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_bundle_facts

#: Carries the marker so operand classification routes it to
#: compare_bundle_facts.dispatch() -- see test_cli_compare_bundle_facts_
#: rejections_more.py's identical constant docstring.
_STUB_BUNDLE_FACTS_JSON = (
    '{"artifact_type": "abicheck.bundle-facts", "schema_version": 2, '
    '"per_library_snapshots": {}}'
)


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _write_stub(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(_STUB_BUNDLE_FACTS_JSON)
    return path


def _write_facts(
    tmp_path: Path,
    name: str,
    version: str,
    visibility: Visibility,
    *,
    variant_fingerprint: str | None = None,
) -> Path:
    fn = Function(
        name="core_fn", mangled="core_fn", return_type="int", visibility=visibility
    )
    snapshot = AbiSnapshot(
        library="libcore.so",
        version=version,
        elf=ElfMetadata(
            soname="libcore.so",
            symbols=[ElfSymbol(name="core_fn", visibility="default")],
        ),
        functions=[fn],
    )
    kwargs = (
        {}
        if variant_fingerprint is None
        else {"variant_fingerprint": variant_fingerprint}
    )
    facts = capture_bundle_facts({"libcore.so": snapshot}, **kwargs)
    path = tmp_path / name
    save_bundle_facts(facts, path)
    return path


class TestBundleCompareOperandRouting:
    def test_stored_stored_pair_with_no_matching_library_fails_loudly(
        self, tmp_path: Path
    ) -> None:
        """Both sides classify as stored and reach the driver (no early
        rejection) -- proven by getting past every option check straight to
        the driver's own zero-pair D7 outcome, not a click.UsageError about
        an unsupported flag."""
        old_path = _write_stub(tmp_path, "old.bundlefacts.json")
        new_path = _write_stub(tmp_path, "new.bundlefacts.json")

        code, out = _invoke("compare", str(old_path), str(new_path), "--format", "json")

        assert code != 64
        # ADR-065 D7 (Codex review, thirtieth round): a zero-pair run with a
        # scope record renders like the fan-out's and exits 1, never a clean 0.
        assert code == 1
        document = json.loads(out[out.index("{") :])
        assert document["run_outcome"]["operational"] == "no_comparison_completed"
        assert document["comparison_scope"]["no_comparison_completed"] is True

    def test_live_old_input_against_stored_new_input_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The one operand shape with no driver yet (live/stored) --
        compare_bundle_operand_dispatch.resolve_bundle_compare_dispatch's
        own remaining UsageError."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_path = _write_stub(tmp_path, "new.bundlefacts.json")

        code, out = _invoke("compare", str(old_dir), str(new_path), "--format", "json")

        assert code == 64
        assert "not yet supported" in out


class TestStoredPairEarlyRejections:
    """The NEW-side mirror of the OLD-side extraction-only rejections
    already covered by test_cli_compare_bundle_facts_rejections*.py --
    each below proves the flag is rejected *before* any real facts loading,
    the same "reject rather than silently diverge" bar every check in
    compare_bundle_facts_rejections.py is held to."""

    def _both_stored(self, tmp_path: Path) -> tuple[Path, Path]:
        return (
            _write_stub(tmp_path, "old.bundlefacts.json"),
            _write_stub(tmp_path, "new.bundlefacts.json"),
        )

    def test_new_side_header_operand_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)
        header_dir = tmp_path / "headers"
        header_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--header",
            f"new={header_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--header" in out

    def test_uniform_include_operand_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)
        include_dir = tmp_path / "includes"
        include_dir.mkdir()

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--include", str(include_dir)
        )

        assert code == 64
        assert "--include" in out

    def test_explicit_ast_frontend_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--ast-frontend", "clang"
        )

        assert code == 64
        assert "--ast-frontend" in out

    def test_devel_pkg_new_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)
        devel_dir = tmp_path / "devel"
        devel_dir.mkdir()

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--devel-pkg", f"new={devel_dir}"
        )

        assert code == 64
        assert "--devel-pkg" in out

    def test_bundle_facts_library_manifest_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text("libraries: {}\n")

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--bundle-facts-library-manifest",
            str(manifest_path),
        )

        assert code == 64
        assert "--bundle-facts-library-manifest" in out

    def test_include_private_dso_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--include-private-dso"
        )

        assert code == 64
        assert "--include-private-dso" in out

    def test_dso_only_is_rejected(self, tmp_path: Path) -> None:
        """A persisted BundleFacts document carries no per-library
        executable/library distinction to filter by, unlike the live
        release fan-out's own old/new map filtering for this flag (Codex
        review, PR #1060, round 7)."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path), "--dso-only")

        assert code == 64
        assert "--dso-only" in out

    def test_keep_extracted_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path), "--keep-extracted")

        assert code == 64
        assert "--keep-extracted" in out

    def test_explicit_new_version_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--version", "new=2.0"
        )

        assert code == 64
        assert "--version" in out

    def test_explicit_old_version_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--version", "old=1.0"
        )

        assert code == 64
        assert "--version" in out

    def test_include_system_declarations_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--include-system-declarations"
        )

        assert code == 64
        assert "--include-system-declarations" in out

    def test_depth_binary_is_not_rejected(self, tmp_path: Path) -> None:
        """--depth binary is genuinely supported for stored/stored (both
        sides are projected via policy.depth_projection.
        project_pair_to_depth() before diffing) -- see
        TestStoredPairEndToEnd for the actual projection behavior."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--depth", "binary"
        )

        assert code != 64
        assert "no comparison completed" in out  # reached the driver (D7)

    def test_depth_headers_is_not_rejected(self, tmp_path: Path) -> None:
        """--depth headers is the other value reachable for stored/stored
        (Codex review, PR #1060, round 5: fresh evidence that this specific
        value still slipped past the earlier --depth binary-only rejection
        fix) -- both sides are projected via policy.depth_projection.
        project_pair_to_depth() before diffing, same as --depth binary
        above."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--depth", "headers"
        )

        assert code != 64
        assert "no comparison completed" in out  # reached the driver (D7)

    def test_compiler_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--compiler", "clang++"
        )

        assert code == 64
        assert "--compiler" in out

    def test_explicit_lang_is_rejected(self, tmp_path: Path) -> None:
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path), "--lang", "c")

        assert code == 64
        assert "--lang" in out

    def test_allow_ast_frontend_fallback_is_rejected(self, tmp_path: Path) -> None:
        """--allow-ast-frontend-fallback is expose_value=False (Codex
        review, PR #1060, round 11) -- it never reaches kwargs at all, so
        reject_unsupported_options() (kwargs-only) can never see it. Must
        still be rejected via ctx.get_parameter_source(), not silently
        accepted with no effect."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--allow-ast-frontend-fallback"
        )

        assert code == 64
        assert "--allow-ast-frontend-fallback" in out

    def test_allow_unsupported_castxml_is_rejected(self, tmp_path: Path) -> None:
        """The expose_value=False sibling of the above (Codex review, PR
        #1060, round 11)."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--allow-unsupported-castxml"
        )

        assert code == 64
        assert "--allow-unsupported-castxml" in out

    def test_default_invocation_is_not_rejected_by_any_new_side_check(
        self, tmp_path: Path
    ) -> None:
        """The untouched Click defaults (header_backend="auto",
        new_version="new", include_dependencies=False, ...) must not
        themselves trip any of the checks above -- proven the same way as
        the routing test: reaching the "nothing was compared" failure, not
        a click.UsageError."""
        old_path, new_path = self._both_stored(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path))

        assert code != 64
        assert "no comparison completed" in out  # reached the driver (D7)

    def test_ambient_project_config_include_dirs_is_not_treated_as_explicit_include(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ordinary project's own ``.abicheck.yml`` `compile:
        include_dirs:` must not be folded into ``kwargs["includes"]`` and
        then rejected as if the user had passed an explicit ``--include``
        -- Codex review, PR #1060: resolve_compile_context's own config
        merge ran unconditionally, so any project with a compile config
        block could never run a stored/stored comparison at all."""
        old_path, new_path = self._both_stored(tmp_path)
        (tmp_path / ".abicheck.yml").write_text("compile:\n  include_dirs: [include]\n")
        monkeypatch.chdir(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path))

        assert code != 64
        assert "no comparison completed" in out  # reached the driver (D7)

    def test_explicit_config_with_compile_block_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Unlike an ambient, auto-discovered config (silently unused,
        above), an *explicitly* given ``--config`` whose compile: block
        would have no effect on a stored/stored comparison must be
        rejected, not silently ignored -- the user asked for it by name
        (Codex review, PR #1060, fresh evidence)."""
        old_path, new_path = self._both_stored(tmp_path)
        config_path = tmp_path / "custom.yml"
        config_path.write_text("compile:\n  include_dirs: [include]\n")

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--config", str(config_path)
        )

        assert code == 64
        assert "compile:" in out

    def test_explicit_config_with_no_compile_block_is_not_rejected(
        self, tmp_path: Path
    ) -> None:
        old_path, new_path = self._both_stored(tmp_path)
        config_path = tmp_path / "custom.yml"
        config_path.write_text("{}\n")

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--config", str(config_path)
        )

        assert code != 64
        assert "no comparison completed" in out  # reached the driver (D7)

    def test_ambient_malformed_config_is_rejected_not_a_raw_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CodeRabbit review raised this as a suspected gap: an *ambient*
        (auto-discovered, not explicit --config) .abicheck.yml is
        deliberately unchecked for its compile: block here (silently
        unused, above), and PR J's own bundle: read (dispatch()'s
        _bundle_cfg load) consumes this SAME file for a stored/stored
        comparison too -- so the concern was that a malformed one might
        reach that unguarded load_build_config_with_digest() call as a
        raw, untranslated ValueError (operational exit 1) instead of a
        clean usage error. Verified against current code: dispatch()'s own
        reject_unsupported_options() already resolves and parses
        kwargs["config"] (by then already auto-discovered by
        resolve_dispatch_compile_context()) unconditionally, translating
        a parse failure to click.UsageError -- before either new_is_stored
        branch, and before _bundle_cfg's own read. This test pins that
        existing behavior directly (it was previously exercised only
        incidentally, never asserted for the stored/stored + ambient-config
        combination) rather than being a new fix."""
        old_path, new_path = self._both_stored(tmp_path)
        (tmp_path / ".abicheck.yml").write_text("bundle:\n  system_providers: 123\n")
        monkeypatch.chdir(tmp_path)

        code, out = _invoke("compare", str(old_path), str(new_path))

        assert code == 64, out
        assert "cannot parse build config" in out

    def test_explicit_malformed_config_is_rejected(self, tmp_path: Path) -> None:
        """The explicit-`--config` sibling of the ambient case above --
        already covered indirectly by test_explicit_config_with_compile_
        block_is_rejected's happy-parse path, pinned directly here so both
        the ambient and explicit routes into reject_unsupported_options'
        parse-or-raise have their own regression test."""
        old_path, new_path = self._both_stored(tmp_path)
        config_path = tmp_path / "custom.yml"
        config_path.write_text("bundle:\n  system_providers: 123\n")

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--config", str(config_path)
        )

        assert code == 64, out
        assert "cannot parse build config" in out


class TestStoredPairEndToEnd:
    def test_visibility_change_is_detected_through_the_real_cli(
        self, tmp_path: Path
    ) -> None:
        old_path = _write_facts(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = _write_facts(
            tmp_path, "new.bundlefacts.json", "new", Visibility.HIDDEN
        )

        code, out = _invoke("compare", str(old_path), str(new_path), "--format", "json")

        data = json.loads(out)
        assert data["mode"] == "bundle_facts"
        assert data["new_is_stored"] is True
        assert data["new_dir"] == str(new_path)
        assert "libcore.so" in data["libraries"]
        assert code != 0

    def test_unchanged_pair_is_a_clean_exit(self, tmp_path: Path) -> None:
        old_path = _write_facts(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = _write_facts(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        code, out = _invoke("compare", str(old_path), str(new_path), "--format", "json")

        data = json.loads(out)
        assert data["verdict"] == "NO_CHANGE"
        assert code == 0

    def test_markdown_output_labels_new_side_as_stored_facts(
        self, tmp_path: Path
    ) -> None:
        old_path = _write_facts(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = _write_facts(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        _code, out = _invoke(
            "compare", str(old_path), str(new_path), "--format", "markdown"
        )

        assert "NEW (stored facts)" in out

    def test_mismatched_variant_is_refused_through_the_real_cli(
        self, tmp_path: Path
    ) -> None:
        old_path = _write_facts(
            tmp_path,
            "old.bundlefacts.json",
            "old",
            Visibility.PUBLIC,
            variant_fingerprint="cpu",
        )
        new_path = _write_facts(
            tmp_path,
            "new.bundlefacts.json",
            "new",
            Visibility.PUBLIC,
            variant_fingerprint="sycl",
        )

        code, out = _invoke("compare", str(old_path), str(new_path), "--format", "json")

        assert code != 0
        assert code != 64  # a real ValueError, not a usage error
        assert "different build variants" in out

    def test_depth_binary_over_binary_only_evidence_is_a_clean_exit(
        self, tmp_path: Path
    ) -> None:
        """The floor half of the depth contract (Codex review, PR #1060,
        round 6): --depth binary over two stored documents that genuinely
        only carry binary-level evidence must succeed, not be rejected by
        the new ``enforce_requested_depth`` floor check -- that check must
        only reject a depth the resolved evidence falls *short* of, never
        one it already meets."""
        old_path = _write_facts(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = _write_facts(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--depth",
            "binary",
            "--format",
            "json",
        )

        assert code == 0
        assert json.loads(out)["verdict"] == "NO_CHANGE"

    def test_depth_headers_over_binary_only_evidence_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The floor half of the depth contract (Codex review, PR #1060,
        round 6): --depth headers over two stored documents that only
        reached binary-level evidence (no ``from_headers``) must fail
        loudly -- ``enforce_requested_depth`` -- rather than silently
        report ``NO_CHANGE`` as if headers-level evidence had genuinely
        backed the comparison."""
        old_path = _write_facts(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = _write_facts(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        code, out = _invoke(
            "compare", str(old_path), str(new_path), "--depth", "headers"
        )

        assert code != 0
        assert code != 64  # a real ValidationError, not a Click usage error
        assert "evidence depth" in out

    def test_incomparable_dependency_scope_exits_16_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        """A matched pair whose extraction contracts disagree
        (``dependency_scope`` "filtered" vs "full") makes
        ``compare_snapshots()`` raise ``ScopeMismatchError`` from inside
        ``compare_stored_bundle_facts_pair()``'s per-library loop -- neither
        a ``ValueError``/``TypeError``/``SnapshotError``, so the dispatcher's
        generic exception clause previously never saw it and let it surface
        as an unhandled traceback (exit 1, no output) instead of native
        ``compare``'s own established not-comparable response (exit 16)
        (Codex review, PR #1060, round 12)."""
        old_snapshot = AbiSnapshot(
            library="libcore.so",
            version="old",
            elf=ElfMetadata(
                soname="libcore.so",
                symbols=[ElfSymbol(name="core_fn", visibility="default")],
            ),
            functions=[
                Function(
                    name="core_fn",
                    mangled="core_fn",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
            dependency_scope="filtered",
        )
        new_snapshot = AbiSnapshot(
            library="libcore.so",
            version="new",
            elf=ElfMetadata(
                soname="libcore.so",
                symbols=[ElfSymbol(name="core_fn", visibility="default")],
            ),
            functions=[
                Function(
                    name="core_fn",
                    mangled="core_fn",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
            dependency_scope="full",
        )
        old_path = tmp_path / "old.bundlefacts.json"
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(capture_bundle_facts({"libcore.so": old_snapshot}), old_path)
        save_bundle_facts(capture_bundle_facts({"libcore.so": new_snapshot}), new_path)

        code, out = _invoke("compare", str(old_path), str(new_path))

        assert code == 16
        assert "not comparable" in out

    def test_incomparable_dependency_scope_emits_json_when_requested(
        self, tmp_path: Path
    ) -> None:
        """The JSON half of the same refusal (Codex review, PR #1060, round
        14): ``exit_bundle_facts_not_comparable`` previously wrote only the
        stderr message and exited, even under ``--format json`` -- unlike
        native ``compare``'s own ``_report_not_comparable``, which still
        emits the requested JSON envelope. A stored-pair refusal must do the
        same instead of silently dropping the requested report format."""
        old_snapshot = AbiSnapshot(
            library="libcore.so",
            version="old",
            elf=ElfMetadata(
                soname="libcore.so",
                symbols=[ElfSymbol(name="core_fn", visibility="default")],
            ),
            functions=[
                Function(
                    name="core_fn",
                    mangled="core_fn",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
            dependency_scope="filtered",
        )
        new_snapshot = AbiSnapshot(
            library="libcore.so",
            version="new",
            elf=ElfMetadata(
                soname="libcore.so",
                symbols=[ElfSymbol(name="core_fn", visibility="default")],
            ),
            functions=[
                Function(
                    name="core_fn",
                    mangled="core_fn",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
            dependency_scope="full",
        )
        old_path = tmp_path / "old.bundlefacts.json"
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(capture_bundle_facts({"libcore.so": old_snapshot}), old_path)
        save_bundle_facts(capture_bundle_facts({"libcore.so": new_snapshot}), new_path)

        code, out = _invoke("compare", str(old_path), str(new_path), "--format", "json")

        assert code == 16
        assert "not comparable" in out
        start = out.index("{")
        document = json.loads(out[start:])
        assert document["mode"] == "bundle_facts"
        # Since ADR-065 S2 the refusal is recorded per member on the
        # ordinary document (the fan-out's own shape) rather than a
        # separate envelope: the run's verdict is `not_comparable`, the
        # operational axis says so, and the member carries its kind.
        assert document["verdict"] == "not_comparable"
        assert document["run_outcome"]["operational"] == "not_comparable"
        assert document["run_outcome"]["compatibility"] is None
        member = document["not_comparable_members"]["libcore.so"]
        assert member["kind"] == "scope_mismatch"
        assert "message" in member
        assert document["comparison_scope"]["counts"]["failed"] == 1
