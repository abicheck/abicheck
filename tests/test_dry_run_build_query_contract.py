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

"""``dump --dry-run``'s ``build.query`` trust/execution report (ADR-043 D4).

Split out of ``test_dry_run_contract.py`` (which sits at its own 2000-line
AI-readiness hard cap) purely to stay under that cap -- this is a sibling
split of one class from that file, not a differently-scoped test module.
See that file's own module docstring for the shared ``--dry-run`` contract
this whole family of tests pins; this file focuses exclusively on
``TestDumpDryRunBuildQueryTrust``, the largest single class there (CLI
cleanup phase two, PR 3C prerequisite 3).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main


class TestDumpDryRunBuildQueryTrust:
    """CLI cleanup phase two, PR 3C prerequisite 3: ``dump --dry-run`` shows
    the exact argv, cwd, resulting compile-DB path, and why ``build.query``
    will or will not run -- without ever running it (ADR-032 D5 trust
    already enforced by the real path; this only adds dry-run visibility).
    """

    def _write_config(self, sources: Path, *, compile_db: str | None = None) -> Path:
        cfg = sources / ".abicheck.yml"
        body = "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n"
        if compile_db:
            body += f"  compile_db: {compile_db}\n"
        cfg.write_text(body, encoding="utf-8")
        return cfg

    def _explicit_config(
        self, where: Path, query: str, *, compile_db: str | None = None
    ) -> Path:
        """An explicit ``--config`` carrying *query*.

        Written under a non-``.abicheck.yml`` name so it is never *also*
        auto-discovered: several tests below depend on the discovered-config
        path staying absent (or malformed) while an explicit one supplies the
        query. Since PR 3C removed ``--build-query``, an explicit ``--config``
        is the only way to configure a query at all -- these tests used to
        reach for the flag purely because it was the shorter spelling.

        The query is JSON-quoted so a whitespace-only or shell-malformed
        value survives YAML scalar parsing verbatim, which two tests below
        depend on.
        """
        import json

        cfg = where / "explicit-abicheck.yml"
        body = f"build:\n  query: {json.dumps(query)}\n"
        if compile_db:
            body += f"  compile_db: {compile_db}\n"
        cfg.write_text(body, encoding="utf-8")
        return cfg

    def test_auto_discovered_config_will_not_run(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        self._write_config(tmp_path, compile_db="build/compile_commands.json")
        result = CliRunner().invoke(
            main,
            ["dump", "--sources", str(tmp_path), "-H", str(header), "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "Build query (trust):" in result.output
        assert "will NOT run" in result.output
        assert "auto-discovered .abicheck.yml" in result.output
        # Never actually executed -- no build/ directory was created.
        assert not (tmp_path / "build").exists()

    def test_explicit_config_will_run_with_argv_and_cwd(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="build/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Build query (trust):" in result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "argv: ['cmake', '-S', '.', '-B', 'build'," in result.output
        assert f"cwd: {tmp_path}" in result.output
        # No compile DB has been written yet (the query never actually ran),
        # so the literal hint resolves to "no file matches it yet" rather
        # than being printed as if it were already a determined path (Codex
        # review, fresh evidence: `_run_build_query` resolves every
        # `build.compile_db` value -- glob or not -- via `sources.glob(...)`,
        # so a plain relative hint is joined onto `sources` and checked for
        # existence exactly like a glob pattern is).
        assert (
            "resulting compile-DB path: (configured as "
            "'build/compile_commands.json', but no file matches it yet"
        ) in result.output
        # A dry run never actually executes the query.
        assert not (tmp_path / "build").exists()

    def test_no_query_configured_reports_none(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            ["dump", "--sources", str(tmp_path), "-H", str(header), "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "build.query: (none configured)" in result.output

    def test_deterministic_across_repeated_invocations(self, tmp_path: Path) -> None:
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        args = [
            "dump", "--sources", str(tmp_path), "-H", str(header),
            "--config", str(cfg), "--dry-run",
        ]
        runner = CliRunner()
        first = runner.invoke(main, args)
        second = runner.invoke(main, args)
        assert first.exit_code == 0
        assert first.output == second.output

    def test_existing_build_info_compile_db_takes_precedence_over_query(
        self, tmp_path: Path
    ) -> None:
        # Codex review: `_resolve_compile_db`'s own first branch returns an
        # already-resolved --build-info compile database *before ever
        # looking at* cfg.query -- an earlier version of this dry-run report
        # ignored --build-info entirely and claimed a trusted query "will
        # run" even when it would never actually be reached.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text(
            f'[{{"directory": "{tmp_path}", "command": "cc -c {header} -o f.o", '
            f'"file": "{header}"}}]',
            encoding="utf-8",
        )
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--build-info", str(db), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Build query (trust):" in result.output
        assert "will NOT run" in result.output
        assert "--build-info already resolves to a compile database" in result.output
        assert not (tmp_path / "build").exists()

    def test_depth_binary_reports_no_collection_requested(self, tmp_path: Path) -> None:
        # Codex review: --depth binary clears the headers
        # (resolve_dump_collect_context) and resolves collect_mode to "off",
        # so neither real call site (the L2 seed, gated on headers; embed_
        # build_source, gated on collect_mode) ever reaches
        # _resolve_compile_db -- an earlier version of this report still
        # claimed a trusted query "will run" for this combination.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")  # --dry-run never opens/parses it
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--config", str(cfg), "--depth", "binary",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Build query (trust):" in result.output
        assert "will NOT run -- no evidence collection requested" in result.output

    def test_depth_headers_still_reports_will_run(self, tmp_path: Path) -> None:
        # The counterpart to the case above: --depth headers also resolves
        # collect_mode to "off", but -- unlike --depth binary -- it does NOT
        # clear the headers, so the L2 seed still runs (it only gates the
        # zero-config *inferred* query on collect_mode, never the explicit
        # trusted cfg.query branch) and a trusted build.query genuinely does
        # execute for this combination.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--config", str(cfg), "--depth", "headers",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output

    def test_headers_and_active_collect_mode_reports_query_may_run_twice(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: verified against the real (non-dry)
        # CLI with a marker-appending query -- a single `abicheck dump`
        # invocation with headers, a real artifact, and an active (non-"off")
        # collect mode appended to the marker file TWICE, not once. Two
        # independent, non-deduplicated real call sites each reach cfg.query:
        # l2_seed.seed_includes_and_fold_compile_context (gated on headers +
        # a real artifact) and embed_build_source (gated on collect_mode !=
        # "off") -- neither caches or shares its result with the other. The
        # default (non-depth-restricted) collect mode used here reaches both.
        # A LATER Codex review found this could not be reported as an
        # unconditional "WILL RUN TWICE" either: whether the dump even
        # reaches the second call site depends on the intervening header-AST
        # parse succeeding (verified empirically -- a real dump with no
        # castxml on PATH ran the query exactly once, via the L2 seed,
        # before the parse failed and aborted the command) -- so this report
        # now describes reachability rather than a guaranteed count.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")  # --dry-run never opens/parses it
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "RUNS AT LEAST ONCE, AND AGAIN IF THE DUMP REACHES" in result.output
        assert "two independent, non-deduplicated call sites" in result.output
        # A dry run never actually executes the query, once or twice.
        assert not (tmp_path / "build").exists()

    def test_headers_active_collect_mode_and_raw_build_info_reports_maybe_twice(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: with a raw --build-info given (not
        # yet resolving to a compile DB at dry-run time), whether the SECOND
        # real call site (embed_build_source) also runs the query is
        # conditional on whether the FIRST invocation's own query happened
        # to write a compile DB at --build-info's exact path -- verified
        # empirically both ways with two real compiled-library runs of the
        # identical marker-appending query: one marker line when the query
        # also wrote a compile DB into --build-info's directory, two when it
        # did not. An earlier revision of this report claimed the
        # unconditional "WILL RUN TWICE" for this input shape too, which is
        # provably wrong for the query-writes-into-build-info case.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "--build-info", str(build_dir),
                "-H", str(header), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "RUNS AT LEAST ONCE, POSSIBLY TWICE" in result.output
        assert "RUNS AT LEAST ONCE, AND AGAIN IF THE DUMP REACHES" not in result.output
        assert "cannot be determined without" in result.output

    def test_empty_build_info_pack_alone_reports_single_execution_only(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: with ONLY an empty --build-info pack
        # given (no raw --sources at all), embed_build_source's own
        # raw_build_info/raw_sources both collapse to None (pack
        # normalization nulls the former; sources was never given at all),
        # so its dispatch guard (raw_build_info is not None or raw_sources
        # is not None) fails unconditionally -- the second call site NEVER
        # runs, regardless of whether the dump succeeds. An earlier revision
        # reported this as "RUNS AT LEAST ONCE, POSSIBLY TWICE" anyway,
        # since it only checked build_info's raw (pre-pack-normalization)
        # value, not whether embed_build_source's own dispatch is reachable
        # at all.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_io.write(BuildSourcePack(root=pack_dir))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "-H", str(header),
                "--build-info", str(pack_dir),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "POSSIBLY TWICE" not in result.output
        assert "AND AGAIN IF THE DUMP REACHES" not in result.output

    def test_empty_build_info_pack_with_raw_sources_reports_deterministic_double(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: with an empty --build-info
        # BuildSourcePack (no L3 compile units) combined with raw --sources
        # (a real, non-pack tree), the second call site IS reachable (raw
        # --sources satisfies embed_build_source's own dispatch guard) --
        # but the ORIGINAL --build-info is not None, so an earlier revision
        # routed this into the "POSSIBLY TWICE" (short-circuit-possible)
        # branch. That's wrong: embed_build_source's own _resolve_compile_db
        # call receives build_info=None for a pack --build-info (identical
        # pack-normalization to _l2_seed_pack_inputs), so nothing can ever
        # short-circuit the second invocation via --build-info's own path --
        # this is the SAME deterministic shape as no --build-info at all,
        # not a genuine once-or-twice ambiguity.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_io.write(BuildSourcePack(root=pack_dir))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--build-info", str(pack_dir),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "RUNS AT LEAST ONCE, AND AGAIN IF THE DUMP REACHES" in result.output
        assert "POSSIBLY TWICE" not in result.output

    def test_malformed_sources_pack_with_raw_build_info_reports_execution_and_blocks(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: with a raw --build-info directory
        # (not yet resolving to a compile DB) given ALONGSIDE a malformed
        # --sources pack, l2_seed._l2_seed_pack_inputs never even attempts
        # to load the --sources pack in this shape -- it resolves via
        # build_info's own path unaffected, and its own query invocation
        # genuinely runs, BEFORE embed_build_source's own unconditional,
        # later re-attempt at loading this same malformed pack fails and
        # aborts the overall command (exit 1). An earlier revision returned
        # early on `collect_active` alone here, reporting only the blocker
        # with no visibility into the fact that build.query already ran.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        cfg = self._write_config(tmp_path)
        malformed_src_pack = tmp_path / "srcpack"
        malformed_src_pack.mkdir()
        (malformed_src_pack / "manifest.json").write_text("not json{{{", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(malformed_src_pack),
                "--build-info", str(build_dir),
                "-H", str(header), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "could not load --sources pack" in result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "argv:" in result.output
        assert "blocker:" in result.output
        assert "build-source embedding re-attempts this same load later" in result.output
        # A dry run never actually executes the query.
        assert not (build_dir / "compile_commands.json").exists()

    def test_malformed_sources_pack_with_raw_build_info_but_no_headers_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence (commit f1cb9c4): the sibling of the
        # test above, but with NO headers -- so l2_seed_reachable is False
        # and the L2 seed's own invocation never runs at all. The prior
        # revision's "the L2 seed's own query invocation is unaffected by
        # it" reasoning does not apply here: embed_build_source is the ONLY
        # remaining real call site, and it also loads --sources
        # unconditionally (the same malformed pack), so it fails before
        # ever reaching build.query. Confirmed empirically against a real
        # gcc-compiled library and a marker-writing query: the real run
        # fails outright ("Invalid evidence pack"), and the marker is never
        # created -- so this dry run must not claim "will run".
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        cfg = self._write_config(tmp_path)
        malformed_src_pack = tmp_path / "srcpack"
        malformed_src_pack.mkdir()
        (malformed_src_pack / "manifest.json").write_text("not json{{{", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(malformed_src_pack),
                "--build-info", str(build_dir),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "could not load --sources pack" in result.output
        assert "will run" not in result.output
        assert "blocker:" in result.output
        assert "only remaining real call site" in result.output

    def test_malformed_config_inside_pack_with_depth_headers_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence (commit b2e3cf1): a valid --sources
        # pack carrying its own malformed .abicheck.yml, given alongside a
        # raw --build-info directory, headers, and an explicit
        # --build-query -- but under --depth headers, which resolves to
        # collect_mode "off". The prior revision's `raw_operand_present`
        # check alone was insufficient: it makes embed_build_source's
        # *dispatch guard* satisfiable, but that guard is only ever reached
        # when `collect_active` in the first place (embed_build_source is
        # called from perform_elf_dump behind exactly that check). With
        # collect_mode "off", embed_build_source is never invoked
        # regardless of operands, so the L2 seed (which already failed to
        # load this same config) is the only real call site, and it's a
        # silent degrade, not an execution. Confirmed empirically against a
        # real gcc-compiled library and a marker-writing query: the real
        # (non-dry) run exits 0 and the marker is never created, even
        # though the pre-fix dry run reported "will run (trusted --
        # explicit --build-query)".
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        (src_pack / ".abicheck.yml").write_text("build: [not, a, mapping\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--build-info", str(build_dir), "-H", str(header),
                "--depth", "headers",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run" not in result.output
        assert "will NOT run" in result.output
        assert "embed_build_source is unreachable anyway" in result.output
        assert "collect mode 'off'" in result.output

    def test_structurally_malformed_pack_manifest_reports_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence (commit 665e13f): a recognized
        # --sources pack whose manifest.json is structurally malformed in a
        # way that raises TypeError rather than OSError/ValueError --
        # BuildSourceManifest.from_dict's `dict(d.get("source_root", ...))`
        # raises TypeError for a real (JSON null) "source_root": null,
        # confirmed directly against pack_io.load(). The prior
        # `except (OSError, ValueError)` never caught this, so `dump
        # --depth headers --dry-run` crashed with a raw traceback instead
        # of a report. The real (non-dry) run completes successfully under
        # --depth headers: seed_includes_and_fold_compile_context's own
        # broad `except Exception` degrades silently on the identical load
        # failure. Confirmed empirically against a real gcc-compiled
        # library and a marker-writing query: the real run exits 0 with the
        # marker never created.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        manifest_path = src_pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_root"] = None
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--depth", "headers",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        assert "will run" not in result.output
        assert "will NOT run" in result.output

    def test_no_sources_or_build_info_reports_no_collection_attempted(
        self, tmp_path: Path
    ) -> None:
        # Codex review: neither real call site (l2_seed, embed_build_source)
        # is even reachable without --sources/--build-info -- a bare
        # --build-query with neither given can never run, regardless of
        # collect mode or headers.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "will NOT run -- neither --sources nor --build-info was given"
            in result.output
        )

    def test_build_info_pack_with_compile_units_takes_precedence(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a --build-info pack already carrying L3 compile
        # units is folded into collect_inline_pack's base_build BEFORE
        # _resolve_compile_db is even considered -- the query never runs,
        # and this is a case the plain-file/dir _compile_db_at check cannot
        # catch (a pack directory has no top-level compile_commands.json).
        from abicheck.buildsource import BuildSourcePack, pack_io
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        be = BuildEvidence()
        be.compile_units.append(
            CompileUnit(id="cu://api.c", source="api.c", directory=str(tmp_path))
        )
        pack_io.write(BuildSourcePack(root=pack_dir, build_evidence=be))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--build-info", str(pack_dir),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "already carries L3 compile units" in result.output

    def test_build_info_pack_without_compile_units_still_reports_will_run(
        self, tmp_path: Path
    ) -> None:
        # The counterpart: an empty (e.g. source_abi-only) pack does not
        # short-circuit resolution -- --build-info becomes effectively
        # absent and the trusted query still resolves to "will run".
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_io.write(BuildSourcePack(root=pack_dir))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--build-info", str(pack_dir),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output

    def test_build_info_bazel_jsonproto_takes_precedence(self, tmp_path: Path) -> None:
        # Codex review: a pre-captured Bazel aquery/cquery jsonproto
        # --build-info is routed to the adapter before _resolve_compile_db
        # is ever reached, and always bypasses it once recognized --
        # _compile_db_at cannot see this (it's not a compile-DB array).
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        aquery = tmp_path / "aquery.json"
        aquery.write_text('{"actions": [], "artifacts": []}', encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--build-info", str(aquery),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "pre-captured Bazel aquery/cquery jsonproto" in result.output

    def test_sources_pack_with_compile_units_takes_precedence(self, tmp_path: Path) -> None:
        # Codex review: a --sources tree that is itself a pack folds into
        # base_build the same way a --build-info pack does, but only when
        # no --build-info was also given.
        from abicheck.buildsource import BuildSourcePack, pack_io
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        be = BuildEvidence()
        be.compile_units.append(
            CompileUnit(id="cu://api.c", source="api.c", directory=str(src_pack))
        )
        pack_io.write(BuildSourcePack(root=src_pack, build_evidence=be))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "--sources" in result.output
        assert "already carries L3 compile units" in result.output

    def test_empty_sources_pack_reports_process_cwd_not_pack_dir(
        self, tmp_path: Path
    ) -> None:
        # Codex review: _l2_seed_pack_inputs nulls raw_sources whenever
        # --sources is itself a pack, unconditionally -- the real query
        # therefore runs with the process's own cwd, not the pack directory,
        # once resolution falls through the pack-compile-units check (no
        # compile units, headers present).
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))

        cfg = self._explicit_config(tmp_path, "cmake -S . -B build")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--config", str(cfg),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run" in result.output
        assert f"cwd: {src_pack}" not in result.output
        assert f"cwd: {Path.cwd()}" in result.output

    def test_empty_build_query_reports_will_not_run(self, tmp_path: Path) -> None:
        # Codex review: shlex.split() on a whitespace-only build.query
        # returns []; _run_build_query itself checks `if not argv: return
        # None` before invoking anything.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._explicit_config(tmp_path, "   ")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "empty command" in result.output

    def test_empty_build_info_pack_with_no_sources_or_headers_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Codex review: embed_build_source's own raw_build_info/raw_sources
        # both collapse to None unconditionally here (build_info is a pack,
        # no --sources given) -- its dispatch guard fails regardless of
        # collect mode, leaving only the headers-gated L2 seed path.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_io.write(BuildSourcePack(root=pack_dir))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(pack_dir),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "pack with no L3 compile units" in result.output

    def test_malformed_build_info_pack_blocks_dry_run(self, tmp_path: Path) -> None:
        # Codex review: the real (non-dry) run rejects an unloadable pack
        # via cli_buildsource._load_pack_or_raise (a nonzero-exit
        # click.ClickException) -- a dry run must not report exit 0 for the
        # same broken invocation.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(pack_dir),
                "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_malformed_sources_pack_blocks_dry_run(self, tmp_path: Path) -> None:
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        (src_pack / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header),
                "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_malformed_auto_discovered_config_blocks_dry_run(self, tmp_path: Path) -> None:
        # CodeRabbit/Codex review: the real (non-dry) run raises
        # click.UsageError (exit 64) for a malformed .abicheck.yml, but only
        # once `embed_build_source` reaches its own stricter load -- which
        # requires a non-"off" collect mode (verified end-to-end against a
        # real compiled library; see the sibling
        # test_malformed_auto_discovered_config_under_depth_headers_degrades_silently
        # for the "off" counter-case). Default depth resolves to a non-"off"
        # collect mode, so this is the collect_active branch: a real
        # click.UsageError, not a DryRunResult blocker.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        (tmp_path / ".abicheck.yml").write_text("build: [not, a, mapping\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--dry-run",
            ],
        )
        assert result.exit_code == 64, result.output
        assert "cannot parse build config" in result.output

    def test_malformed_auto_discovered_config_under_depth_headers_degrades_silently(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence, verified end-to-end against a real
        # compiled library: under `--depth headers` (collect_mode resolves
        # to "off", but headers stay non-empty), `embed_build_source` bails
        # at its own `if not layers: return` before ever loading the
        # auto-discovered config, and `l2_seed`'s own independent load
        # degrades a parse failure to a silent no-op rather than raising --
        # the real (non-dry) `dump --depth headers` exits 0 for this exact
        # input, only warning. This dry run must not report a blocker or a
        # UsageError for a combination the real run accepts.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        (tmp_path / ".abicheck.yml").write_text("build: [not, a, mapping\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--dry-run",
                "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_explicit_malformed_config_always_raises_usage_error(self, tmp_path: Path) -> None:
        # CodeRabbit/Codex review, fresh evidence, verified end-to-end
        # against the real CLI: an *explicit* --config is validated
        # unconditionally by `cli_options.merge_compile_config` regardless
        # of --sources/--build-info/--depth -- even `dump --config bad.yml
        # --depth binary` with no --sources/--build-info at all exits 64.
        cfg = tmp_path / "bad.yml"
        cfg.write_text("build: [not, a, mapping\n", encoding="utf-8")
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--config", str(cfg), "--dry-run",
                "--depth", "binary",
            ],
        )
        assert result.exit_code == 64, result.output
        assert "cannot parse build config" in result.output

    def test_malformed_pack_under_depth_headers_degrades_silently(self, tmp_path: Path) -> None:
        # Codex review, fresh evidence, verified end-to-end against a real
        # compiled library: under `--depth headers`, a malformed --sources
        # pack is never reached by embed_build_source's own raising load
        # (gated behind the same collect-mode check as the config case
        # above); l2_seed's own load degrades silently, and the real
        # (non-dry) run exits 0.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        (src_pack / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header),
                "--dry-run", "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_malformed_build_info_pack_under_depth_headers_degrades_silently(
        self, tmp_path: Path
    ) -> None:
        # Sibling of test_malformed_pack_under_depth_headers_degrades_silently
        # for --build-info specifically (not --sources): under --depth
        # headers, embed_build_source's own raising load is never reached
        # (collect_active is False), so a malformed --build-info pack
        # degrades to a non-blocking diagnostic rather than result.block().
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        bi_pack = tmp_path / "bipack"
        bi_pack.mkdir()
        (bi_pack / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(bi_pack),
                "-H", str(header),
                "--dry-run", "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_malformed_sources_pack_ignored_under_depth_headers_with_raw_build_info(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `l2_seed._l2_seed_pack_inputs` only
        # attempts `pack_io.load(sources)` when `build_info is
        # None` -- with a raw (non-pack) --build-info given, it nulls
        # `raw_sources` unconditionally but never loads the pack at all, so
        # a malformed --sources pack manifest is genuinely irrelevant: the
        # real query still runs via --build-info's own path. Under
        # --depth headers (collect_active False, so embed_build_source's
        # own unconditional pack-loading is never reached either), an
        # earlier revision unconditionally treated this malformed pack as
        # blocking, reporting "will NOT run" even though the real command
        # succeeds.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        (src_pack / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )
        build_info_dir = tmp_path / "raw_build_info"
        build_info_dir.mkdir()  # no compile_commands.json inside
        cfg = self._explicit_config(tmp_path, "cmake -S . -B build")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--build-info", str(build_info_dir), "-H", str(header),
                "--config", str(cfg),
                "--dry-run", "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output

    def test_sources_pack_no_headers_and_no_build_info_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Reaches the --sources-is-a-pack branch specifically (no
        # --build-info given at all, so the elif chain lands here rather
        # than the --build-info-is-a-pack branch): an empty pack with no
        # headers gives no other route to collect_inline_pack.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "will NOT run -- --sources is a pack with no L3 compile units "
            "and no headers give another path to collect_inline_pack"
        ) in result.output

    def test_malformed_build_query_reports_will_not_run(self, tmp_path: Path) -> None:
        # shlex.split() itself raises ValueError on unbalanced quoting --
        # _run_build_query never attempts to run such a query.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._explicit_config(tmp_path, "cmake 'unterminated")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output
        assert "could not parse as a command" in result.output

    def test_malformed_sources_pack_blocks_even_with_nonpack_build_info(
        self, tmp_path: Path
    ) -> None:
        # Codex review: `embed_build_source` loads bi_pack/src_pack
        # unconditionally and independently -- a malformed --sources pack
        # must block the dry run even when --build-info is a plain,
        # already-resolvable compile database that would otherwise take L3
        # precedence over it.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text("[]", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        (src_pack / "manifest.json").write_text(
            '{"build_source_pack_version": "1.0", "not valid json',
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(compile_db),
                "--sources", str(src_pack),
                "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output
        assert "could not load --sources pack" in result.output

    def test_pe_with_dump_manifest_raises_usage_error(self, tmp_path: Path) -> None:
        # Codex review: dump_cmd's own PE/Mach-O dispatch rejects
        # --dump-manifest outright (ADR-050 D3, click.UsageError, exit 64)
        # before embed_build_source is ever reached, so build.query can
        # never run for this combination regardless of what would
        # otherwise resolve. A first revision of this fix used
        # `result.block()` (exit 1) instead -- the wrong exit-code class
        # (Codex review, fresh evidence).
        pe_path = tmp_path / "lib.dll"
        # Minimal PE signature: "MZ" DOS header magic is enough for
        # binary_utils.detect_binary_format to classify this as PE.
        pe_path.write_bytes(b"MZ" + b"\x00" * 62 + b"\x40\x00\x00\x00" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        manifest_path = tmp_path / "manifest.yml"
        manifest_path.write_text(
            "roots: [api.h]\ntranslation_units:\n  - name: main\n    forced_includes: [api.h]\n",
            encoding="utf-8",
        )

        # --dump-manifest and -H/--header are mutually exclusive (the
        # manifest's own `roots` field declares the public surface), so no
        # headers are passed here.
        result = CliRunner().invoke(
            main,
            [
                "dump", str(pe_path), "--dump-manifest", str(manifest_path),
                "--sources", str(tmp_path),
                "--dry-run",
            ],
        )
        assert result.exit_code == 64, result.output
        assert "--dump-manifest is not yet supported for" in result.output

    def test_malformed_config_validated_even_when_build_info_precedence_wins(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence, verified end-to-end against the real
        # CLI: `embed_build_source` loads and validates the auto-discovered
        # config unconditionally (whenever raw_build_info/raw_sources is
        # present) -- *before* ever calling collect_inline_pack/
        # _resolve_compile_db, which is where "--build-info already resolves
        # to a compile database" precedence is decided. A dry run that
        # returns "will NOT run" for that precedence reason *without* first
        # validating the config would silently skip a real click.UsageError
        # the actual run raises.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text("[]", encoding="utf-8")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        (tmp_path / ".abicheck.yml").write_text("build: [not, a, mapping\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(compile_db),
                "--sources", str(tmp_path), "-H", str(header),
                "--dry-run",
            ],
        )
        assert result.exit_code == 64, result.output
        assert "cannot parse build config" in result.output

    def test_both_operands_empty_packs_no_headers_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: with --build-info an empty pack (no
        # compile units) and --sources ALSO an empty pack, no headers given,
        # the old `sources is None` check (literally testing for absence,
        # not for pack-normalized-to-None) missed that --sources being a
        # pack also nulls raw_sources -- both operands normalize to None in
        # the real run, so embed_build_source's dispatch guard fails and
        # the headerless L2 seed returns immediately; the query can never
        # run.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        bi_pack = tmp_path / "bipack"
        bi_pack.mkdir()
        pack_io.write(BuildSourcePack(root=bi_pack))
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(bi_pack),
                "--sources", str(src_pack),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_compile_db_hint_suppressed_without_raw_source_tree(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: _run_build_query only resolves
        # cfg.compile_db (or --build-compile-db) against a real `sources`
        # tree -- with an empty --sources pack (normalizes to None), it
        # never globs or auto-discovers a compile DB regardless of what's
        # configured, so this module must not promise a specific path.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))

        cfg = self._explicit_config(
            tmp_path, "cmake -S . -B build", compile_db="out/compile_commands.json"
        )
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--config", str(cfg),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run" in result.output
        assert "resulting compile-DB path: out/compile_commands.json" not in result.output
        assert "no --sources tree to resolve it against" in result.output

    def test_explicit_config_query_reachable_via_headers_only_pack_input(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: l2_seed._l2_seed_config loads
        # cfg_path (an explicit --config always short-circuits to it)
        # whenever seed_includes_and_fold_compile_context runs at all --
        # gated only on headers being non-empty, independent of whether
        # --build-info/--sources are packs. A --build-info that is an
        # empty pack (embed_build_source's own dispatch guard fails) must
        # not suppress reading a real, explicit --config's own query.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        bi_pack = tmp_path / "bipack"
        bi_pack.mkdir()
        pack_io.write(BuildSourcePack(root=bi_pack))
        cfg = tmp_path / "config.yml"
        cfg.write_text(
            "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(bi_pack),
                "-H", str(header), "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "build.query: (none configured)" not in result.output

    def test_source_only_dump_headers_do_not_reach_l2_seed(self, tmp_path: Path) -> None:
        # Codex review, fresh evidence, verified end-to-end against the real
        # CLI: with no SO_PATH, dump_cmd dispatches to dump_source_only()
        # (the parallel-baseline flow), which never calls
        # seed_includes_and_fold_compile_context() at all -- a source-only
        # dump has no -H headers seeding L2 (its own docstring: "this
        # path's snapshot starts with no functions/variables at all"). The
        # real `dump --sources ... -H ... --depth headers` (no SO_PATH)
        # exits 1 (requested depth not satisfiable) with the query never
        # run, not "will run".
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--depth", "headers",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_config_discovered_inside_empty_sources_pack_for_l2_seed(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `l2_seed._l2_seed_config` discovers
        # `.abicheck.yml` from the *original*, unnormalized `sources` it is
        # handed (`_resolve_l2_seed_pack_args`/
        # `seed_includes_and_fold_compile_context` pass the raw `sources`
        # parameter straight through), never the pack-nulled
        # `effective_sources` value `embed_build_source` itself discovers
        # from. An empty --sources BuildSourcePack (no L3 compile units)
        # carrying its own .abicheck.yml with a trusted `build.query` is
        # therefore genuinely readable by the real L2-seed path (reachable
        # here via headers + a real artifact) even though
        # `effective_sources` alone (None, since --sources is a pack) would
        # report "(none configured)".
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        (src_pack / ".abicheck.yml").write_text(
            "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        # Not trusted -- sourced only from an auto-discovered .abicheck.yml,
        # never --config/--build-query -- but discovered nonetheless, which
        # is what distinguishes this from the pre-fix "(none configured)".
        assert "build.query: (none configured)" not in result.output
        assert (
            "will NOT run (sourced from an auto-discovered .abicheck.yml"
            in result.output
        )

    def test_glob_compile_db_pre_query_match_is_labeled_provisional(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: an existing glob match at dry-run
        # time is only a PRE-QUERY snapshot -- _run_build_query resolves
        # `sorted(sources.glob(cfg.compile_db))` AFTER the query runs, first
        # lexically-sorted match wins, so a query that creates a
        # lexicographically-earlier match (or removes this one) makes the
        # real run resolve to a genuinely different path. An earlier
        # revision's "the query may still recreate/refresh this file"
        # wording undersold that -- it isn't only refreshed, it can be
        # entirely superseded by a different file.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_a = tmp_path / "build" / "a"
        build_a.mkdir(parents=True)
        existing_db = build_a / "compile_commands.json"
        existing_db.write_text("[]", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="build/*/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "resulting compile-DB path (provisional, pre-query snapshot):" in result.output
        assert "can select a different file than this one" in result.output

    def test_glob_compile_db_hint_resolves_to_existing_match(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `build.compile_db` is a *glob
        # pattern* `_run_build_query` resolves via
        # `sorted(sources.glob(cfg.compile_db))` -- printing the literal
        # pattern as "the resulting compile-DB path" is wrong; an already
        # existing match should be reported as the resolved path, not the
        # unexpanded pattern.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_a = tmp_path / "build" / "a"
        build_a.mkdir(parents=True)
        existing_db = build_a / "compile_commands.json"
        existing_db.write_text("[]", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="build/*/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"resulting compile-DB path (provisional, pre-query snapshot): {existing_db}" in result.output
        assert "build/*/compile_commands.json" in result.output  # pattern noted too
        assert (
            "resulting compile-DB path: build/*/compile_commands.json"
            not in result.output
        )

    def test_glob_compile_db_hint_with_no_match_reports_pattern_only(
        self, tmp_path: Path
    ) -> None:
        # Sibling case: no file matches the glob pattern yet -- the real
        # run's exact resulting path is only known after the query executes
        # and (re)writes it, so dry-run must not claim a specific path.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="build/*/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "no file matches it yet" in result.output
        assert (
            "resulting compile-DB path: build/*/compile_commands.json"
            not in result.output
        )

    def test_literal_compile_db_hint_resolves_against_source_tree(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `_run_build_query` resolves every
        # `build.compile_db` value -- glob-metacharacter-bearing or not --
        # via `sorted(sources.glob(cfg.compile_db))`; a plain relative hint
        # like `build/compile_commands.json` is therefore joined onto
        # `sources` and checked for existence exactly like a real glob
        # pattern is, never printed verbatim as if it were already a
        # determined path. An earlier revision special-cased the
        # glob-metacharacter-free case as "unambiguous, print as-is,"
        # which silently regressed this exact resolution.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        existing_db = build_dir / "compile_commands.json"
        existing_db.write_text("[]", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="build/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"resulting compile-DB path (provisional, pre-query snapshot): {existing_db}" in result.output
        assert (
            "resulting compile-DB path: build/compile_commands.json"
            not in result.output
        )

    def test_autodiscovered_compile_db_match_is_labeled_provisional(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: mirrors the configured-glob fix --
        # _run_build_query executes the arbitrary query BEFORE
        # _autodiscover_compile_db is ever consulted for real, so an
        # already-existing conventional compile DB at dry-run time is only
        # a pre-query snapshot; the query may delete it, create a
        # higher-precedence candidate, or leave it unchanged, and the real
        # run can select a different path or none at all.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        existing_db = tmp_path / "compile_commands.json"
        existing_db.write_text("[]", encoding="utf-8")
        cfg = self._write_config(tmp_path)  # no compile_db configured
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "resulting compile-DB path (provisional, pre-query snapshot):" in result.output
        assert "can select a different path or none at all" in result.output

    def test_no_compile_db_hint_still_resolves_conventional_compile_db(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: even with no build.compile_db
        # configured, _run_build_query still resolves *something* --
        # _autodiscover_compile_db(sources), a pure read-only search over
        # conventional build-dir names. A conventional compile DB already
        # sitting under --sources is therefore a concrete, deterministically
        # resolvable path, not "the query's own default output location".
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        existing_db = tmp_path / "compile_commands.json"
        existing_db.write_text("[]", encoding="utf-8")
        cfg = self._write_config(tmp_path)  # no compile_db configured
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"resulting compile-DB path (provisional, pre-query snapshot): {existing_db}" in result.output
        assert (
            "resulting compile-DB path: (build.compile_db not configured -- "
            "the query's own default output location)"
        ) not in result.output

    def test_no_compile_db_hint_and_no_conventional_db_reports_generic_message(
        self, tmp_path: Path
    ) -> None:
        # Sibling case: nothing configured, nothing already on disk -- the
        # generic "own default output location" message is correct here.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            "resulting compile-DB path: (build.compile_db not "
            "configured, and no conventional compile DB exists yet"
        ) in result.output

    def test_absolute_compile_db_hint_reports_diagnostic_not_traceback(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: Path.glob() raises NotImplementedError
        # for a non-relative pattern (confirmed: Path("/tmp").glob("/tmp/x")
        # raises "Non-relative patterns are unsupported") -- the real
        # _run_build_query's own identical sources.glob(cfg.compile_db) call
        # (reached AFTER the query itself exits 0) has the same, uncaught
        # gap. This module must not crash on a read-only preview even though
        # the real run itself would -- but it must also not claim exit 0
        # ("valid") for an invocation that is genuinely going to crash, not
        # merely produce an unexpected answer (Codex review, fresh evidence,
        # second round: an earlier revision of this fix reported the
        # diagnostic but still exited 0, silently claiming the invocation
        # was valid).
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        absolute_hint = str((tmp_path / "compile_commands.json").resolve())
        cfg = self._write_config(tmp_path, compile_db=absolute_hint)
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--dry-run",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "NotImplementedError" in result.output
        assert "an absolute path" in result.output
        assert "blocker: build.compile_db is configured as an absolute path" in result.output

    def test_malformed_config_inside_sources_pack_degrades_silently_despite_raw_build_info(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `embed_build_source`'s own
        # auto-discovery is `discover_build_config(raw_sources)` -- keyed on
        # `effective_sources` alone, never `build_info` -- so with --sources
        # a pack, `raw_sources` is None regardless of a raw --build-info also
        # being given, and `discover_build_config(None)` always returns None:
        # embed_build_source never even discovers this pack's own malformed
        # .abicheck.yml, let alone fails to load it. Only l2_seed's own
        # pack-rooted discovery (reachable via headers + a real artifact)
        # finds it, and that path always degrades silently -- so the real
        # run proceeds (exit 0), even though a raw, non-pack --build-info is
        # also present. An earlier revision raised click.UsageError (exit 64)
        # here purely because `raw_operand_present` was true via the
        # unrelated --build-info clause.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        (src_pack / ".abicheck.yml").write_text("build: [unterminated\n", encoding="utf-8")
        build_info = tmp_path / "not_a_compile_db.txt"
        build_info.write_text("not a compile database\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--build-info", str(build_info), "-H", str(header), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_malformed_sources_only_pack_config_with_cli_build_query_still_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Codex review (commit 8f57a41), fresh evidence: a PRIOR revision of
        # this same fix (commit f9fd95d, for a DIFFERENT scenario naming a
        # raw --build-info) was over-broadened to fall through to "will run"
        # whenever `effective_sources is None`, regardless of whether a raw
        # --build-info was actually also given. That was wrong: with
        # --sources the SOLE input (no --build-info at all) and it itself a
        # pack, `embed_build_source`'s own dispatch guard
        # (`raw_build_info is not None or raw_sources is not None`) is never
        # satisfied at all -- `raw_sources` is nulled the same way
        # `effective_sources` is, and `raw_build_info` is None since
        # --build-info was never given -- so embed_build_source is entirely
        # UNREACHABLE in this shape, not merely reading a different file.
        # Only the L2 seed's own pack-rooted discovery is reachable here, and
        # it already failed to load this exact malformed config -- so the
        # real run genuinely does NOT execute the CLI-overridden query.
        # Confirmed empirically against the real (non-dry) CLI with a real
        # gcc-compiled library and a marker-writing query: the run completes
        # with "requested evidence layer(s) not collected: L3_build,
        # L4_source_abi" and the marker file is never created.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        (src_pack / ".abicheck.yml").write_text("build: [unterminated\n", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "--sources",
                str(src_pack),
                "-H",
                str(header),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "build.query: will NOT run" in result.output

    def test_malformed_sources_pack_config_with_raw_build_info_and_explicit_config_still_runs(
        self, tmp_path: Path
    ) -> None:
        # The scenario the original finding (commit f9fd95d) actually named:
        # --sources is a valid pack containing a malformed .abicheck.yml, a
        # native artifact plus headers make L2 seeding reachable, a RAW
        # (non-pack) --build-info is ALSO given, AND an explicit
        # --config is supplied. Here embed_build_source's own dispatch
        # guard IS satisfied (`raw_build_info is not None`), so it is
        # genuinely reachable and constructs its config purely from that
        # explicit --config, independently of l2_seed's own (failed)
        # pack-rooted discovery. So the real run genuinely executes the
        # explicitly-configured query -- this dry-run must not report "will
        # NOT run". (Before PR 3C this scenario reached the query through
        # `--build-query`; an explicit `--config` is now the only authorizer,
        # and the reachability reasoning it exercises is unchanged.) A raw, non-pack --build-info naming an EMPTY directory (not
        # a file, and not resolving to any compile_commands.json inside it)
        # is used so the --build-info-already-resolves-to-a-compile-DB
        # precedence branch doesn't independently mask this case --
        # `_compile_db_at()` honors any EXISTING FILE whatever its content
        # ("the user pointed straight at it"), so a raw --build-info file
        # would always short-circuit through that unrelated precedence
        # branch instead of reaching the code path this test targets.
        from abicheck.buildsource import BuildSourcePack, pack_io

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        pack_io.write(BuildSourcePack(root=src_pack))
        (src_pack / ".abicheck.yml").write_text("build: [unterminated\n", encoding="utf-8")
        build_info = tmp_path / "emptybuilddir"
        build_info.mkdir()
        cfg = self._explicit_config(tmp_path, "echo hi")

        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "--sources",
                str(src_pack),
                "--build-info",
                str(build_info),
                "-H",
                str(header),
                "--config",
                str(cfg),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "build.query: will NOT run" not in result.output
        assert "['echo', 'hi']" in result.output
