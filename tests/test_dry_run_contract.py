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

    def test_dry_run_rejects_empty_header_directory_like_the_real_run(
        self, tmp_path: Path
    ) -> None:
        # CLI cleanup phase two plan, PR 3C prerequisite 3's own residual
        # "-H directory gap": --dry-run never validated a -H directory, so
        # it could report success for an invocation the real run would
        # reject outright. Both must fail the same way.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        empty_dir = tmp_path / "empty_headers"
        empty_dir.mkdir()
        dry = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "-H", str(empty_dir)]
        )
        real = CliRunner().invoke(main, ["dump", str(snap), "-H", str(empty_dir)])
        assert dry.exit_code == real.exit_code == 1
        assert "Header directory contains no supported header files" in dry.output
        assert "Header directory contains no supported header files" in real.output

    def test_dry_run_does_not_reject_a_useless_header_dir_for_source_only_dump(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: the empty-directory check above must
        # not reach a source-only dump (no SO_PATH) -- that path deliberately
        # treats -H as inert (dump_source_only() never receives `headers` at
        # all, so it only warns, never rejects; a prior hard-rejection
        # attempt was reverted for breaking 20 pre-existing tests exercising
        # exactly this shape). Hard-rejecting an empty -H dir here, before
        # that branch is even reached, would reintroduce the same
        # regression under a different name.
        empty_dir = tmp_path / "empty_headers"
        empty_dir.mkdir()
        result = CliRunner().invoke(
            main,
            ["dump", "--sources", str(tmp_path), "-H", str(empty_dir), "--dry-run"],
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_accepts_a_header_directory_with_real_headers(
        self, tmp_path: Path
    ) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header_dir = tmp_path / "include"
        header_dir.mkdir()
        (header_dir / "api.h").write_text("void f(void);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "-H", str(header_dir)]
        )
        assert result.exit_code == 0, result.output

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
        from abicheck.buildsource import pack_io
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        surface = SourceAbiSurface()
        surface.coverage["compile_units_selected"] = 1
        surface.coverage["compile_units_parsed"] = 1
        pack_io.write(BuildSourcePack(root=pack_dir, source_abi=surface))
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
        from abicheck.buildsource import pack_io
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        surface = SourceAbiSurface()
        surface.coverage["compile_units_selected"] = 1
        surface.coverage["compile_units_parsed"] = 1
        pack_io.write(BuildSourcePack(root=pack_dir, source_abi=surface))
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "--build-info", str(pack_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "does not load the pack to verify" in result.output


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
