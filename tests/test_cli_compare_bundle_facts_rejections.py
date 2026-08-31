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

"""``compare --old-bundle-facts`` early-rejection tests -- split out of
``test_cli_compare_bundle_facts.py`` (which sits at the architecture
no-growth test-file cap) to keep this addition's tests together without
pushing that file over it.

Every test here proves that an unsupported flag/config-block combination is
rejected *before* any real facts loading/comparison happens, so none of them
need a real gcc-built bundle -- a placeholder OLD_INPUT/NEW_INPUT pair is
enough. See ``test_cli_compare_bundle_facts.py``'s own
``TestCompareOldBundleFacts`` class for the end-to-end, gcc-built-bundle
tests this file deliberately doesn't duplicate, and
``test_cli_compare_bundle_facts_rejections_more.py`` for this file's own
continuation once it hit the same test-file cap in a later round.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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


class TestCompareOldBundleFactsEarlyRejections:
    """The four Codex-review fixes: checks that must fire before any real
    facts loading/comparison happens, so none of them need a real gcc-built
    bundle -- a placeholder OLD_INPUT/NEW_INPUT pair is enough to prove the
    checks run early, unlike the ``TestCompareOldBundleFacts`` class in
    ``test_cli_compare_bundle_facts.py``.
    """

    def test_dry_run_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--dry-run",
            "--format",
            "json",
        )

        assert code == 64
        assert "--dry-run" in out

    def test_contract_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--contract",
            "public",
            "--format",
            "json",
        )

        assert code == 64
        assert "--contract" in out

    def test_malformed_old_facts_json_is_a_clean_error(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "malformed.json"
        facts_path.write_text("not json{")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code == 1
        assert "Traceback" not in out

    def test_includes_from_config_are_merged_and_forwarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: dispatch() used to re-derive `includes` from the raw
        # CLI kwargs instead of the compile-context-resolved, config-merged
        # list -- so a `.abicheck.yml` `compile.include_dirs` entry was
        # silently dropped for --old-bundle-facts even though it reached
        # every other compare dispatch path. Proven directly by monkeypatching
        # the real dispatch() out and capturing what compare_cmd forwards to
        # it, rather than indirectly through a full gcc-built bundle: what
        # matters here is the kwargs handoff in compare.py, not
        # compare_release_against_bundle_facts's own behavior (already
        # covered by TestCompareOldBundleFacts).
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        extra_include = tmp_path / "extra_include"
        extra_include.mkdir()
        config_path.write_text(f"compile:\n  include_dirs:\n    - {extra_include}\n")

        from abicheck.frontends.cli.commands import compare_bundle_facts

        captured: dict[str, object] = {}

        def _fake_dispatch(*, compile_context: object, **kwargs: object) -> None:
            captured["includes"] = kwargs.get("includes")

        monkeypatch.setattr(compare_bundle_facts, "dispatch", _fake_dispatch)

        code, _out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--config",
            str(config_path),
            "--format",
            "json",
        )

        assert code == 0
        forwarded_includes = captured.get("includes")
        assert forwarded_includes is not None
        assert extra_include in forwarded_includes  # type: ignore[operator]

    def test_severity_preset_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --severity-preset drives run_compare's
        # _resolve_compare_config, which this dispatcher never calls --
        # compare_release_against_bundle_facts() has no severity parameter,
        # so the run always exited through the legacy verdict mapping
        # regardless of what was requested.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--severity-preset",
            "strict",
            "--format",
            "json",
        )

        assert code == 64
        assert "--severity-preset" in out

    def test_exit_code_scheme_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--exit-code-scheme",
            "severity",
            "--format",
            "json",
        )

        assert code == 64
        assert "--exit-code-scheme" in out

    def test_pack_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --pack drives run_compare's pack-application path,
        # which this dispatcher never calls.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        pack_path = tmp_path / "pack.yaml"
        pack_path.write_text("id: x\nversion: 1\nkind: policy\nassignments: {}\n")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--pack",
            str(pack_path),
            "--format",
            "json",
        )

        assert code == 64
        assert "--pack" in out

    def test_no_scope_public_headers_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --no-scope-public-headers has no channel into
        # compare_release_against_bundle_facts() -- the driver always scopes
        # to the public surface via service.compare_snapshots's own default.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--no-scope-public-headers",
            "--format",
            "json",
        )

        assert code == 64
        assert "--no-scope-public-headers" in out

    def test_debug_info_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: this driver resolves NEW-side ELF/DWARF facts
        # directly from the binary itself and has no debug-dir parameter to
        # forward a --debug-info package's extracted contents to.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        debug_pkg = tmp_path / "libreal-dbg.tar"
        debug_pkg.write_bytes(b"")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--debug-info",
            f"new={debug_pkg}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--debug-info" in out

    def test_write_secondary_output_unsupported_format_is_rejected(
        self, tmp_path: Path
    ) -> None:
        # Codex review: --write sarif=... was accepted but this dispatcher
        # only ever renders json/markdown, so the secondary artifact was
        # silently never written.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--write",
            f"sarif={tmp_path / 'out.sarif'}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--write" in out

    def test_depth_build_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --depth build/source collect L3-L5 evidence from
        # --sources/--build-info, which this dispatcher never reads on
        # either side.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--depth",
            "build",
            "--format",
            "json",
        )

        assert code == 64
        assert "--depth" in out

    def test_depth_source_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--depth",
            "source",
            "--format",
            "json",
        )

        assert code == 64
        assert "--depth" in out

    def test_no_bundle_analysis_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: compare_release_against_bundle_facts() has no
        # parameter to skip compare_bundle_from_facts's cross-library
        # analysis, so --no-bundle-analysis was silently accepted and
        # ignored.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--no-bundle-analysis",
            "--format",
            "json",
        )

        assert code == 64
        assert "--no-bundle-analysis" in out

    def test_depth_binary_clears_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: run_compare's own --depth binary clears every header
        # operand (_normalize_compare_options) so the run stays pure L0/L1
        # evidence -- this dispatcher independently re-derives `headers`
        # from the same raw kwargs, so without the fix it silently kept a
        # given --header and ran L2 extraction anyway. Proven by
        # monkeypatching compare_release_against_bundle_facts and capturing
        # what dispatch() forwards to it, rather than through a real gcc
        # build: what matters here is whether `headers` reaches the call
        # cleared, not the L2 extraction behavior itself (already covered
        # elsewhere).
        import abicheck.bundle_side_input as bundle_side_input

        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        header_file = tmp_path / "foo.h"
        header_file.write_text("")

        captured: dict[str, object] = {}

        def _fake_compare(*args: object, **kwargs: object) -> None:
            captured["headers"] = kwargs.get("headers")
            raise ValueError("stop-here")

        monkeypatch.setattr(
            bundle_side_input, "compare_release_against_bundle_facts", _fake_compare
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--depth",
            "binary",
            "--header",
            f"new={header_file}",
            "--format",
            "json",
        )

        assert code == 1, out
        assert captured["headers"] is None

    def test_output_dir_writes_per_library_reports(self, tmp_path: Path) -> None:
        # Codex review: --output-dir is a release-style artifact request
        # (one {library}.json per matched library, mirroring the live
        # release fan-out's own layout) that this dispatcher only ever
        # accepted, never fulfilled.
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
        lib_report = output_dir / "libreal.so.json"
        assert lib_report.exists()
        payload = json.loads(lib_report.read_text())
        assert payload["library"] == "libreal.so"

    def test_extraction_failure_does_not_leak_temp_dir(self, tmp_path: Path) -> None:
        # Codex review: a malformed archive (a real recognized extension,
        # bad content) raised from inside _extract_if_package *after*
        # make_temp_dir() had already recorded the directory -- when
        # extraction sat outside the try/finally, that directory was never
        # cleaned up even without --keep-extracted.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        malformed_archive = tmp_path / "release.tar.gz"
        malformed_archive.write_bytes(b"not a real gzip/tar stream")

        before = set(Path(tempfile.gettempdir()).iterdir())

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(malformed_archive),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code != 0, out
        after = set(Path(tempfile.gettempdir()).iterdir())
        leaked = {p for p in (after - before) if p.name.startswith("abicheck_pkg_")}
        assert not leaked, f"leaked extraction temp dir(s): {leaked}"

    def test_config_severity_block_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: an explicit --config is consumed only for its
        # compile: block (resolve_compile_context) -- a severity: block
        # would otherwise be silently unapplied, the same silent-divergence
        # bug --severity-preset is rejected for as an explicit CLI flag.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("severity:\n  preset: strict\n")

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
        assert "severity:" in out

    def test_config_scope_block_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("scope:\n  public: false\n")

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
        assert "scope:" in out

    def test_config_with_only_compile_block_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A compile:-only config must NOT be rejected -- only the
        # non-compile blocks (severity/scope/suppression/exit_code_scheme)
        # are unsupported here.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("compile:\n  std: c++17\n")

        from abicheck.frontends.cli.commands import compare_bundle_facts

        def _fake_dispatch(*, compile_context: object, **kwargs: object) -> None:
            pass

        monkeypatch.setattr(compare_bundle_facts, "dispatch", _fake_dispatch)

        code, _out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--config",
            str(config_path),
            "--format",
            "json",
        )

        assert code == 0

    def test_sources_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: this driver never reads --sources/--build-info on
        # either side, so inline build/source evidence was silently
        # dropped.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--sources",
            f"new={src_dir}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--sources" in out

    def test_used_by_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: this short-circuit bypasses
        # _reject_flags_unsupported_for_set_inputs, so a single-pair-only
        # flag like --used-by was accepted but never consumed.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        app_path = tmp_path / "app"
        app_path.write_bytes(b"")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--used-by",
            str(app_path),
            "--format",
            "json",
        )

        assert code == 64
        assert "--used-by" in out

    def test_require_complete_analysis_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--require-complete-analysis",
            "--format",
            "json",
        )

        assert code == 64
        assert "--require-complete-analysis" in out

    def test_dwarf_only_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --dwarf-only/--debug-format/--debuginfod/
        # --debug-root select or locate NEW-side debug info, but
        # compare_release_against_bundle_facts()'s per-library
        # service.resolve_input() call is never given any of them.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--dwarf-only",
            "--format",
            "json",
        )

        assert code == 64
        assert "--dwarf-only" in out

    def test_pattern_verdicts_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: pattern-verdict modulation and surface-metric
        # findings are both computed inside service.compare_snapshots(),
        # but the per-library call here never passes pattern_verdicts/
        # surface_metrics -- always False regardless of the flag.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--pattern-verdicts",
            "--format",
            "json",
        )

        assert code == 64
        assert "--pattern-verdicts" in out

    def test_config_debug_block_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  dwarf_only: true\n")

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
        assert "debug:" in out

    def test_auto_discovered_config_severity_block_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: with no explicit --config at all, run_compare's own
        # cfg_path still falls back to the cwd-upward auto-discovered
        # .abicheck.yml (discover_project_config()) -- compare.py's
        # --old-bundle-facts branch previously never did this, so an
        # auto-discovered config's severity:/scope:/etc. blocks (and even
        # its compile: block) were silently invisible to this mode.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (tmp_path / ".abicheck.yml").write_text("severity:\n  preset: strict\n")
        monkeypatch.chdir(tmp_path)

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code == 64
        assert "severity:" in out

    def test_probe_matrix_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: the ordinary single-pair/release paths fold
        # --probe-matrix's build-configuration-drift findings into the
        # comparison and verdict, but this dispatch never loads or
        # forwards probe_matrix_old/probe_matrix_new.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_matrix = tmp_path / "old_matrix.json"
        old_matrix.write_text("{}")
        new_matrix = tmp_path / "new_matrix.json"
        new_matrix.write_text("{}")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--probe-matrix",
            f"old={old_matrix}",
            "--probe-matrix",
            f"new={new_matrix}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--probe-matrix" in out

    def test_post_manifest_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --post-manifest's public_surface_allowlist is
        # applied via a parameter compare_release_against_bundle_facts()
        # doesn't have.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        manifest_path = tmp_path / "post_manifest.json"
        manifest_path.write_text("{}")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--post-manifest",
            str(manifest_path),
            "--format",
            "json",
        )

        assert code == 64
        assert "--post-manifest" in out

    def test_config_scope_collapse_versioned_symbols_is_rejected(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: BuildConfig's scope: block parses
        # public/collapse_versioned_symbols/public_symbols as three
        # independent fields -- a config setting only
        # collapse_versioned_symbols (scope_public left None) previously
        # passed the config check unrejected.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("scope:\n  collapse_versioned_symbols: true\n")

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
        assert "scope:" in out

    def test_config_scope_public_symbols_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("scope:\n  public_symbols:\n    - foo\n")

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
        assert "scope:" in out

    def test_pdb_path_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --pdb-path has no channel into
        # compare_release_against_bundle_facts()'s per-library
        # service.resolve_input() call -- a NEW-side PE DLL would always
        # fall back to binary-only extraction regardless of what was given.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        pdb_path = tmp_path / "new.pdb"
        pdb_path.write_text("")

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--pdb-path",
            f"new={pdb_path}",
            "--format",
            "json",
        )

        assert code == 64
        assert "--pdb-path" in out

    def test_follow_deps_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: --follow-deps's DT_NEEDED dependency-graph walk has
        # no parameter on compare_release_against_bundle_facts() either.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--follow-deps",
            "--format",
            "json",
        )

        assert code == 64
        assert "--follow-deps" in out

    def test_show_only_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: every nested per-library report is rendered via
        # reporter.to_json(diff) with no show_only argument -- the filter
        # was accepted but every change stayed in the output regardless.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--show-only",
            "breaking",
            "--format",
            "json",
        )

        assert code == 64
        assert "--show-only" in out

    def test_config_scope_show_redundant_is_rejected(self, tmp_path: Path) -> None:
        # Codex review: BuildConfig's scope: block parses show_redundant as
        # a fourth independent field -- a config setting only that field
        # (every other scope field left at its default) previously passed
        # this check unrejected even though this driver's own JSON
        # rendering never re-merges redundant_changes.
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("scope:\n  show_redundant: true\n")

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
        assert "scope:" in out
