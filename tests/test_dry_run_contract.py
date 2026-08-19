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

"""Shared ``--dry-run`` contract behavior tests (ADR-043 D4).

``dump``, ``compare``, ``scan``, ``deps tree``, and ``deps compare`` all share
one ``DryRunResult`` model/renderer (``abicheck/dry_run.py``). This module
pins the cross-command contract behaviorally: deterministic output, no file
written, ``-o/--output`` rejected, and an exit code drawn only from
``{0, 1, 64}`` — never a verdict code (``2``/``4``). ``scan --dry-run`` has
its own dedicated coverage in ``test_cli_scan.py``/``test_scan_estimate.py``;
this file focuses on the three commands (``dump``, ``compare``, ``deps
tree``/``deps compare``) that previously had none.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

_CONTRACT_FOOTER = "Dry run only -- no analysis performed, nothing written."


def _write_snapshot(path: Path, version: str = "1.0") -> None:
    snap = AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=[
            Function(
                name="f", mangled="_Zf", return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


class TestDumpDryRun:
    def test_writes_nothing_and_exits_zero(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "-o", str(out)]
        )
        # --dry-run + -o is a usage error (mutually exclusive), not a silent
        # no-write success — confirms the rejection wires up on `dump` too.
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        runner = CliRunner()
        first = runner.invoke(main, ["dump", str(snap), "--dry-run"])
        second = runner.invoke(main, ["dump", str(snap), "--dry-run"])
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: dump" in first.output

    def test_depth_source_with_no_evidence_input_blocks(self, tmp_path: Path) -> None:
        # Codex review: the real (non-dry) run's check_requested_depth_satisfied
        # strict gate now hard-fails `--depth source`/`--depth build` with no
        # way to reach that depth, but a --dry-run used to exit 0 for the
        # identical inputs (only a soft "would carry only L0-L2 data"
        # warning) -- silently accepting a baseline invocation that the real
        # run would then reject. --depth source has no path but --sources/
        # --build-info (a -p/--compile-db only ever supplies "build"
        # context), so this is cheaply, deterministically known to fail
        # without running anything.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "--depth", "source"]
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_depth_build_with_no_evidence_input_blocks(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "--depth", "build"]
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_depth_build_with_matching_compile_db_does_not_block(
        self, tmp_path: Path
    ) -> None:
        # External review: loading the compilation database --build-info
        # resolves to and checking whether it
        # matches the resolved headers is cheap, deterministic, read-only
        # resolution -- not "real work out of scope for a dry run". A
        # genuinely matching compile database is a definite pass, with no
        # warning at all (it does supply real "build" evidence).
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps([{
                "directory": str(tmp_path),
                "command": f"cc -c {header} -o f.o",
                "file": str(header),
            }]),
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "build",
                "-H", str(header), "--build-info", str(db),
            ],
        )
        assert result.exit_code == 0, result.output
        # render_dump_dry_run's own compile-db-aware warning (distinct from
        # resolve_dump_collect_context's separate, unconditional
        # --sources/--build-info-absence echo, which still fires here and
        # is not what this test targets).
        assert "would carry only L0-L2 data." not in result.output

    def test_depth_build_dry_run_reports_compile_db_as_the_l3_source(
        self, tmp_path: Path
    ) -> None:
        # AC-007 (Codex): --build-info *is* the L3 build source, and the real
        # run reads a compilation database off it, so the dry-run report must
        # describe that (its L3 source), not claim "no --build-info given".
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps([{
                "directory": str(tmp_path),
                "command": f"cc -c {header} -o f.o",
                "file": str(header),
            }]),
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "build",
                "-H", str(header), "--build-info", str(db),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "--build-info resolved to a compilation database" in result.output
        assert "no --sources/--build-info given -- L0-L2 only" not in result.output

    def test_depth_build_with_unmatched_compile_db_blocks(self, tmp_path: Path) -> None:
        # The sibling case: an empty/non-matching compile database is now a
        # *definite* verdict (the same load+match the real run performs),
        # not a soft "possibly satisfiable" warning -- the real run's
        # strict depth gate would certainly reject this.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "build",
                "-H", str(header), "--build-info", str(db),
            ],
        )
        assert result.exit_code == 1, result.output
        assert "no entry matching the resolved headers" in result.output

    def test_build_info_without_headers_is_not_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        # The removed -p/--compile-db flag was header-only input: it existed
        # solely to give the header parse a compile context, so using it
        # without -H was a UsageError (exit 64). --build-info is not that
        # flag -- it is the general build/source input, legitimate on its own
        # -- so the same combination must now be accepted, and the compile
        # database simply never gets derived from it (compile_db_from_build_info
        # answers None with no headers to match against). The dry run and the
        # real run agree on that, which is what this pins.
        so = tmp_path / "libfoo.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 60)
        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["dump", str(so), "--dry-run", "--build-info", str(db)]
        )
        assert result.exit_code == 0, result.output
        assert "requires -H/--header" not in result.output
        assert f"--build-info: {db}" in result.output
        # No headers -> no compile database is read off --build-info, so the
        # L2 compile-context line must not claim one.
        assert "resolved to a compilation database" not in result.output

    def test_compile_db_from_build_info_needs_headers(self, tmp_path: Path) -> None:
        # The primitive behind the test above, stated directly: the same
        # --build-info answers a real database once headers are present and
        # None when they are not, so the dry run cannot report a compile
        # context the real run would not build.
        from abicheck.cli_dump_helpers import compile_db_from_build_info

        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        assert compile_db_from_build_info(db, ()) is None
        assert compile_db_from_build_info(db, (header,)) == db
        assert compile_db_from_build_info(None, (header,)) is None
        # A directory operand resolves through its own compile_commands.json.
        assert compile_db_from_build_info(tmp_path, (header,)) == db

    def test_a_filter_that_would_scope_only_l2_is_refused(
        self, tmp_path: Path
    ) -> None:
        """--compile-db-filter parameterizes the header parse only, and L3
        collection reads the same database through an adapter with no filter
        of its own -- so a monorepo database would embed build facts for
        translation units the filter excludes.

        The removed -p/--compile-db spelling refused this outright
        (`resolve_compile_db_l3_reuse` declined to promote a filtered header
        database to L3). Folding the operand into --build-info removed that
        decision point, turning a loud refusal into a silent unfiltered
        collection (Codex review); this pins the refusal.
        """
        from abicheck.cli_dump_helpers import compile_db_filter_scope_error

        db = tmp_path / "compile_commands.json"
        assert compile_db_filter_scope_error("src/**", db, "source-target")
        # Every way the combination cannot arise answers None.
        assert compile_db_filter_scope_error(None, db, "source-target") is None
        assert compile_db_filter_scope_error("src/**", None, "source-target") is None
        assert compile_db_filter_scope_error("src/**", db, "off") is None

    def test_the_filter_refusal_reaches_the_cli(self, tmp_path: Path) -> None:
        so = tmp_path / "libfoo.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 60)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps([{
                "directory": str(tmp_path),
                "command": f"cc -c {header} -o f.o",
                "file": str(header),
            }]),
            encoding="utf-8",
        )
        args = [
            "dump", str(so), "--dry-run", "-H", str(header),
            "--build-info", str(db), "--depth", "build",
        ]
        refused = CliRunner().invoke(main, [*args, "--compile-db-filter", "src/**"])
        assert refused.exit_code == 64, refused.output
        assert "--compile-db-filter scopes the L2 header parse only" in refused.output
        # Without the filter the identical invocation is accepted, so the
        # refusal is scoped to the combination and not to --build-info.
        allowed = CliRunner().invoke(main, args)
        assert allowed.exit_code == 0, allowed.output

    def test_compile_db_from_build_info_rejects_a_bazel_jsonproto(
        self, tmp_path: Path
    ) -> None:
        """--build-info also takes a Bazel aquery/cquery jsonproto, which the
        Bazel adapter routes -- but only if this does not first claim it as a
        compile database. Treating any file as one handed such a run to
        `load_compile_db()`, which rejects a JSON object outright, so
        `--build-info aquery.json -H api.h` failed before the adapter ran
        (Codex review)."""
        from abicheck.cli_dump_helpers import compile_db_from_build_info

        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        aquery = tmp_path / "aquery.json"
        aquery.write_text('{"actions": [], "targets": []}', encoding="utf-8")
        assert compile_db_from_build_info(aquery, (header,)) is None

        cquery = tmp_path / "cquery.json"
        cquery.write_text('{"results": []}', encoding="utf-8")
        assert compile_db_from_build_info(cquery, (header,)) is None

        # And the same one level down, for a build directory whose
        # compile_commands.json is not the array the loader requires.
        nested = tmp_path / "build"
        nested.mkdir()
        (nested / "compile_commands.json").write_text("{}", encoding="utf-8")
        assert compile_db_from_build_info(nested, (header,)) is None

    def test_debug_format_against_pe_binary_is_usage_error(self, tmp_path: Path) -> None:
        # --debug-format (and the legacy --dwarf/--btf/--ctf flags) is only
        # meaningful for ELF; the real run raises BadParameter (exit 64) for
        # a PE/Mach-O binary. Dry-run previously never checked this either,
        # and an earlier fix wrongly downgraded it to a blocker (exit 1) --
        # see the sibling compile-db test above (CodeRabbit review).
        pe = tmp_path / "foo.dll"
        pe.write_bytes(b"MZ" + b"\x00" * 60)
        result = CliRunner().invoke(
            main, ["dump", str(pe), "--dry-run", "--debug-format", "dwarf"]
        )
        assert result.exit_code == 64, result.output
        assert "Usage:" in result.output
        assert "only supported for ELF binaries" in result.output

    def test_depth_source_with_build_info_but_no_sources_blocks(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a raw --build-info compile database supplies L3
        # "build" context only -- L4 source-ABI replay only ever runs over
        # a --sources tree (buildsource.inline._run_inline_source_abi
        # returns (None, []) whenever `sources` is None). The blocker below
        # used to be nested under a "sources AND build_info both absent"
        # warn condition, so --depth source with --build-info given (but no
        # --sources) fell through untouched and the dry run exited 0 even
        # though the real dump's strict depth gate would raise.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "-H", str(header), "--build-info", str(db),
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output
        assert "no --sources was given" in result.output

    def test_depth_source_with_prebuilt_pack_build_info_does_not_block(
        self, tmp_path: Path
    ) -> None:
        # Codex review, second finding: a raw compile-DB --build-info never
        # carries L4 facts, but a *pack-shaped* --build-info (e.g. from a
        # previous `collect` or the abicheck-cc wrapper) can carry its own
        # source_abi -- cli_buildsource.embed_build_source's _combine_packs
        # falls back to that pack's source_abi when no --sources pack is
        # given, so --depth source --build-info <pack> (no --sources) can
        # genuinely succeed for real. The blocker above must not fire for
        # this case -- unlike a raw compile database, checked via
        # buildsource.inline.is_pack_dir (cheap manifest-shape read).
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        surface = SourceAbiSurface()
        surface.coverage["compile_units_selected"] = 1
        surface.coverage["compile_units_parsed"] = 1
        BuildSourcePack(root=pack_dir, source_abi=surface).write()
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "--build-info", str(pack_dir),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_depth_source_with_inputs_pack_build_info_does_not_block(
        self, tmp_path: Path
    ) -> None:
        # Codex review, second finding on this signal: embed_build_source
        # recognizes TWO pack-shaped --build-info directory kinds --
        # BuildSourcePack (is_pack_dir) and the Flow-2 abicheck_inputs/
        # protocol (_is_inputs_pack_dir, ADR-035 D5) -- either can carry its
        # own L4 source_abi and satisfy --depth source with no --sources.
        # Checking only is_pack_dir missed this second kind.
        import json

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        inputs_dir = tmp_path / "abicheck_inputs"
        inputs_dir.mkdir()
        (inputs_dir / "manifest.json").write_text(
            json.dumps({"kind": "abicheck_inputs", "version": 1}),
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "--build-info", str(inputs_dir),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_depth_source_with_pack_build_info_warns_not_verified(
        self, tmp_path: Path
    ) -> None:
        # CodeRabbit review: pack *shape* alone (is_pack_dir) does not prove
        # the pack's manifest actually carries usable L4 source_abi facts --
        # a manifest-only/empty pack is exactly as unsatisfiable as a raw
        # compile database, but previously produced no signal at all here.
        # A dry run must not load the pack to verify (real I/O), so this
        # stays a soft warning -- "possibly satisfiable" -- not a blocker,
        # mirroring the sibling --depth build/some-compile-database warning.
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        surface = SourceAbiSurface()
        surface.coverage["compile_units_selected"] = 1
        surface.coverage["compile_units_parsed"] = 1
        BuildSourcePack(root=pack_dir, source_abi=surface).write()
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "--build-info", str(pack_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "does not load the pack to verify" in result.output


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

    def test_explicit_cli_build_query_overrides_config_and_will_run(
        self, tmp_path: Path
    ) -> None:
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--build-query", "make -n -k", "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --build-query)" in result.output
        assert "argv: ['make', '-n', '-k']" in result.output
        assert (
            "resulting compile-DB path: (build.compile_db not configured"
            in result.output
        )

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

    def test_cli_build_query_still_reports_configs_compile_db_hint(
        self, tmp_path: Path
    ) -> None:
        # Codex review: an explicit CLI --build-query overrides only
        # `cfg.query`, never `cfg.compile_db` (mirroring `cli_buildsource.
        # py`'s own dataclasses.replace) -- an earlier version of this
        # function skipped loading the config entirely once a CLI query was
        # given, silently dropping the config's own build.compile_db hint.
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path, compile_db="out/compile_commands.json")
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--config", str(cfg), "--build-query", "make -n -k", "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --config)" in result.output
        assert "argv: ['make', '-n', '-k']" in result.output
        # No compile DB has been written yet -- resolved the same way a glob
        # pattern is (Codex review, fresh evidence), not printed as a
        # determined path.
        assert (
            "resulting compile-DB path: (configured as 'out/compile_commands.json', "
            "but no file matches it yet"
        ) in result.output

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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        BuildSourcePack(root=pack_dir).write()

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
                "--build-query", "touch marker", "--dry-run",
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
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
        from abicheck.buildsource.pack import BuildSourcePack

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
        BuildSourcePack(root=pack_dir, build_evidence=be).write()

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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        cfg = self._write_config(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        BuildSourcePack(root=pack_dir).write()

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
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
        from abicheck.buildsource.pack import BuildSourcePack

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
        BuildSourcePack(root=src_pack, build_evidence=be).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--build-query", "cmake -S . -B build",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--build-query", "cmake -S . -B build",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run" in result.output
        assert f"cwd: {src_pack}" not in result.output

    def test_empty_build_query_reports_will_not_run(self, tmp_path: Path) -> None:
        # Codex review: shlex.split() on a whitespace-only build.query
        # returns []; _run_build_query itself checks `if not argv: return
        # None` before invoking anything.
        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(tmp_path),
                "-H", str(header), "--build-query", "   ", "--dry-run",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        BuildSourcePack(root=pack_dir).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(pack_dir),
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "-H", str(header), "--build-query", "cmake -S . -B build",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "-H", str(header), "--build-query", "cmake -S . -B build",
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
                "-H", str(header), "--build-query", "cmake -S . -B build",
                "--dry-run", "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will NOT run" in result.output

    def test_malformed_sources_pack_ignored_under_depth_headers_with_raw_build_info(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: `l2_seed._l2_seed_pack_inputs` only
        # attempts `BuildSourcePack.load(sources)` when `build_info is
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

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--build-info", str(build_info_dir), "-H", str(header),
                "--build-query", "cmake -S . -B build",
                "--dry-run", "--depth", "headers",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "will run (trusted -- explicit --build-query)" in result.output

    def test_sources_pack_no_headers_and_no_build_info_reports_will_not_run(
        self, tmp_path: Path
    ) -> None:
        # Reaches the --sources-is-a-pack branch specifically (no
        # --build-info given at all, so the elif chain lands here rather
        # than the --build-info-is-a-pack branch): an empty pack with no
        # headers gives no other route to collect_inline_pack.
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "--build-query", "cmake -S . -B build", "--dry-run",
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
        result = CliRunner().invoke(
            main,
            [
                "dump", "--sources", str(tmp_path), "-H", str(header),
                "--build-query", "cmake 'unterminated", "--dry-run",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
                "--build-query", "cmake -S . -B build", "--dry-run",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        bi_pack = tmp_path / "bipack"
        bi_pack.mkdir()
        BuildSourcePack(root=bi_pack).write()
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--build-info", str(bi_pack),
                "--sources", str(src_pack),
                "--build-query", "cmake -S . -B build", "--dry-run",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path), "--sources", str(src_pack),
                "-H", str(header), "--build-query", "cmake -S . -B build",
                "--build-compile-db", "out/compile_commands.json",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        bi_pack = tmp_path / "bipack"
        bi_pack.mkdir()
        BuildSourcePack(root=bi_pack).write()
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
                "--build-query", "cmake -S . -B build", "--depth", "headers",
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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()
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
        assert f"resulting compile-DB path: {existing_db}" in result.output
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
        assert f"resulting compile-DB path: {existing_db}" in result.output
        assert (
            "resulting compile-DB path: build/compile_commands.json"
            not in result.output
        )

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
        assert f"resulting compile-DB path: {existing_db}" in result.output
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
        # has the same, uncaught gap. This module must not crash on a
        # read-only preview even though the real run itself would.
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
        assert result.exit_code == 0, result.output
        assert "NotImplementedError" in result.output
        assert "an absolute path" in result.output

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
        from abicheck.buildsource.pack import BuildSourcePack

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"")
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        src_pack = tmp_path / "srcpack"
        src_pack.mkdir()
        BuildSourcePack(root=src_pack).write()
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


class TestCompareDryRun:
    def test_rejects_output_flag(self, tmp_path: Path) -> None:
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--dry-run", "-o", str(out)],
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_never_a_verdict_exit_code(self, tmp_path: Path) -> None:
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        runner = CliRunner()
        first = runner.invoke(main, ["compare", str(old), str(new), "--dry-run"])
        second = runner.invoke(main, ["compare", str(old), str(new), "--dry-run"])
        assert first.exit_code in (0, 1, 64)
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: compare" in first.output

    def test_reports_effective_depth_not_just_raw_requested(self, tmp_path: Path) -> None:
        # Regression (CLI-audit P1/P2): a dry run must report the *effective*
        # depth the real run will use, not just echo back the raw --depth
        # string. With no --depth given but a raw --sources tree, the real
        # run now infers "source" (see TestResolveCompareCollectMode) --
        # the dry run must show that inference, not "requested depth: (not
        # given)" alone with no indication of what will actually happen.
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        tree = tmp_path / "src"
        tree.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old), str(new), "--dry-run",
                "--sources", "old=" + str(tree),
            ],
        )
        assert result.exit_code == 0
        assert "requested depth: (not given)" in result.output
        assert "effective depth: source" in result.output
        assert "inferred" in result.output


class TestDepsTreeDryRun:
    def test_writes_nothing_and_rejects_output(self, tmp_path: Path) -> None:
        binary = tmp_path / "libfoo.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main, ["deps", "tree", str(binary), "--dry-run", "-o", str(out)]
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        binary = tmp_path / "libfoo.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        runner = CliRunner()
        first = runner.invoke(main, ["deps", "tree", str(binary), "--dry-run"])
        second = runner.invoke(main, ["deps", "tree", str(binary), "--dry-run"])
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: deps tree" in first.output

    def test_non_elf_binary_rejected_even_under_dry_run(self, tmp_path: Path) -> None:
        # Regression: the ELF-format check used to run *after* the dry-run
        # emit, so `deps tree --dry-run` on a non-ELF file reported "ok" for
        # an input the real run immediately rejects (post-merge PR #566
        # review). The dry run must agree with the real run.
        not_elf = tmp_path / "not-a-lib.so"
        not_elf.write_bytes(b"not an elf at all")
        result = CliRunner().invoke(main, ["deps", "tree", str(not_elf), "--dry-run"])
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output
        assert _CONTRACT_FOOTER not in result.output


class TestDepsCompareDryRun:
    def test_writes_nothing_and_rejects_output(self, tmp_path: Path) -> None:
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "usr/bin/myapp",
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run", "-o", str(out),
            ],
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        args = [
            "deps", "compare", "usr/bin/myapp",
            "--old-root", str(old_root), "--new-root", str(new_root), "--dry-run",
        ]
        runner = CliRunner()
        first = runner.invoke(main, args)
        second = runner.invoke(main, args)
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: deps compare" in first.output

    def test_same_root_is_a_usage_error_even_under_dry_run(self, tmp_path: Path) -> None:
        # The no-op-comparison guard fires before the dry-run branch — a dry
        # run still catches a plainly-useless invocation (exit 64, not a
        # silent "would compare nothing" report).
        root = tmp_path / "same-root"
        root.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "usr/bin/myapp",
                "--old-root", str(root), "--new-root", str(root), "--dry-run",
            ],
        )
        assert result.exit_code == 64

    def test_non_elf_binary_rejected_even_under_dry_run(self, tmp_path: Path) -> None:
        # Regression: the per-root ELF-format check used to run *after* the
        # dry-run emit, so `deps compare --dry-run` could report "ok" for a
        # binary that isn't ELF in either root even though the real run
        # immediately rejects it (post-merge PR #566 review).
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        rel = Path("usr/bin/myapp")
        (old_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (old_root / rel).write_bytes(b"not an elf at all")
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", str(rel),
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output
        assert _CONTRACT_FOOTER not in result.output

    def test_absolute_binary_resolved_under_sysroot_not_host(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): `root / binary` (pathlib) drops
        # `root` entirely when `binary` is absolute, so an absolute BINARY
        # argument used to silently escape old-root/new-root and resolve
        # against the host filesystem instead. The dry-run's displayed
        # resolved paths must stay under the sysroots.
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "/usr/bin/myapp",
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"old resolved path: {old_root / 'usr/bin/myapp'}" in result.output
        assert f"new resolved path: {new_root / 'usr/bin/myapp'}" in result.output
        # The raw argument is echoed under "Inputs" -- only the *resolved*
        # paths must stay confined to the sysroots.
        assert "resolved path: /usr/bin/myapp" not in result.output
