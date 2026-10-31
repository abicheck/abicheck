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

"""ADR-037 D7 (G22 Phase 4): `compare` input-type dispatch + folded aliases.

`compare` accepts a single .so / snapshot, a directory, or a package, and rejects
an application/PIE operand with a hint at `appcompat`. Directory/package operands
fan out to the same per-library comparison the (now deprecated) `compare-release`
runs — so `compare <dir> <dir>` reproduces a `compare-release <dir> <dir>` run.ADR-061 Phase 4, throughout: patch the owner, not ``abicheck.cli`` -- its lazy ``__getattr__`` means a ``setattr`` there rebinds nothing the caller reads.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from _package_fixtures import _make_tar_mode
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_resolve import _looks_like_application, classify_compare_operand
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows import release_members

#: Placeholder snapshots for CompareResult stubs — these tests only read
#: the diff, but the struct requires both sides.
_SNAP = AbiSnapshot(library="stub", version="0")


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(
        library=library, version=version, functions=funcs, from_headers=True
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def test_header_graph_flag_is_removed(tmp_path: Path) -> None:
    """CLI cleanup H1: --header-graph (a hidden, deprecated no-op — G29 Phase
    A's L2 header-only semantic graph is always attempted regardless) is now
    gone outright rather than silently accepted."""
    old, new = _breaking_pair()
    old_f = _write_snap(tmp_path / "old.json", old)
    new_f = _write_snap(tmp_path / "new.json", new)
    tree = tmp_path / "src"
    tree.mkdir()  # no manifest.json → looks like a raw source checkout
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_f),
            str(new_f),
            "--sources",
            "old=" + str(tree),
            "--header-graph",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output
    assert "--header-graph" in result.output


def test_compare_source_tree_on_snapshot_input_is_ignored(tmp_path: Path) -> None:
    """A raw --old-sources tree on a snapshot input can't be embedded (you can't
    re-dump a snapshot), so compare warns and still produces a verdict."""
    old, new = _breaking_pair()
    old_f = _write_snap(tmp_path / "old.json", old)
    new_f = _write_snap(tmp_path / "new.json", new)
    tree = tmp_path / "src"
    tree.mkdir()  # no manifest.json → looks like a raw source checkout
    result = CliRunner().invoke(
        main, ["compare", str(old_f), str(new_f), "--sources", "old=" + str(tree)]
    )
    out = (result.output or "") + (result.stderr or "")
    assert "ignored" in out, out
    assert result.exit_code in (0, 2, 4), out


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap(
        "1.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    new = _snap(
        "2.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    return old, new


def _make_pie_executable(path: Path) -> Path:
    """Write a minimal ELF64 ET_DYN file carrying a PT_INTERP segment (a PIE app)."""
    e_phoff = 64
    e_phentsize = 56
    e_phnum = 1
    # ELF header (64 bytes): magic, class=2, data=1, then fields; e_type at 16.
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = 2  # ELFCLASS64
    hdr[5] = 1  # little-endian
    hdr[6] = 1  # EV_CURRENT
    struct.pack_into("<H", hdr, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", hdr, 18, 0x3E)  # e_machine = x86-64
    struct.pack_into("<Q", hdr, 32, e_phoff)  # e_phoff
    struct.pack_into("<H", hdr, 54, e_phentsize)  # e_phentsize
    struct.pack_into("<H", hdr, 56, e_phnum)  # e_phnum
    # One program header: PT_INTERP (p_type=3).
    ph = bytearray(e_phentsize)
    struct.pack_into("<I", ph, 0, 3)  # p_type = PT_INTERP
    path.write_bytes(bytes(hdr) + bytes(ph))
    return path


def _elf_header(e_type: int, *, ei_class: int = 2, ei_data: int = 1) -> bytes:
    """Craft a minimal 64-byte ELF header with a given e_type/class/endianness."""
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = ei_class
    hdr[5] = ei_data
    hdr[6] = 1
    order = "<" if ei_data == 1 else ">"
    struct.pack_into(f"{order}H", hdr, 16, e_type)
    return bytes(hdr)


class TestLooksLikeApplication:
    """Direct coverage of the ELF-header guard branches (ADR-037 D7)."""

    def test_et_exec_is_application(self, tmp_path: Path) -> None:
        p = tmp_path / "exe"
        p.write_bytes(_elf_header(2))  # ET_EXEC
        assert _looks_like_application(p) is True

    def test_et_dyn_without_interp_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "lib.so"
        p.write_bytes(_elf_header(3))  # ET_DYN, no program headers → no PT_INTERP
        assert _looks_like_application(p) is False

    def test_et_rel_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "obj.o"
        p.write_bytes(_elf_header(1))  # ET_REL
        assert _looks_like_application(p) is False

    def test_unknown_endianness_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "weird"
        p.write_bytes(_elf_header(2, ei_data=7))  # bogus EI_DATA
        assert _looks_like_application(p) is False

    def test_unknown_class_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "weird2"
        p.write_bytes(_elf_header(2, ei_class=9))  # bogus EI_CLASS
        assert _looks_like_application(p) is False

    def test_truncated_header_is_inconclusive(self, tmp_path: Path) -> None:
        p = tmp_path / "trunc"
        p.write_bytes(b"\x7fELF\x02")  # magic + class byte only, no data byte
        assert _looks_like_application(p) is False

    def test_non_elf_is_not_application(self, tmp_path: Path) -> None:
        p = tmp_path / "text"
        p.write_bytes(b"not an elf at all")
        assert _looks_like_application(p) is False


def _invoke(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output, (result.stderr or "")


# ── classifier ────────────────────────────────────────────────────────────────


class TestClassifier:
    def test_snapshot_is_file(self, tmp_path: Path) -> None:
        p = _write_snap(tmp_path / "libfoo.json", _snap())
        assert classify_compare_operand(p) == "file"

    def test_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "rel"
        d.mkdir()
        assert classify_compare_operand(d) == "directory"

    def test_package(self, tmp_path: Path) -> None:
        pkg = _make_tar_mode(tmp_path / "foo.tar.gz", "w:gz")  # content, not name (7n)
        assert classify_compare_operand(pkg) == "package"

    def test_pie_executable_is_app(self, tmp_path: Path) -> None:
        app = _make_pie_executable(tmp_path / "myapp")
        assert classify_compare_operand(app) == "app"


# ── dispatch ──────────────────────────────────────────────────────────────────


class TestCompareDispatch:
    def test_file_vs_file_snapshot(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        code, out, _ = _invoke("compare", str(old_f), str(new_f))
        assert code == 4
        assert "BREAKING" in out

    def test_dir_vs_dir_fans_out(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out, _ = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        assert "BREAKING" in out

    @pytest.mark.parametrize(
        "flag, expected", [("--include-system-declarations", True), (None, False)]
    )
    def test_include_dependencies_reaches_set_comparison(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        flag: str | None,
        expected: bool,
    ) -> None:
        """Codex review: a directory/package ``compare`` used to drop
        ``--include-system-declarations`` entirely on its way through
        ``_dispatch_release_compare`` -> ``compare_release_members`` ->
        ``compare_member_pair``, so set comparisons stayed unfiltered
        regardless of the flag. Assert it now reaches the per-library engine
        with the same value a single-pair ``compare`` would use."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        seen: list[bool] = []
        real_run_compare_pair = release_members.compare_member_pair

        def _capture(request, *args: object, **kwargs: object) -> object:  # type: ignore[no-untyped-def]
            seen.append(bool(request.include_dependencies))
            return real_run_compare_pair(request, *args, **kwargs)

        monkeypatch.setattr(
            "abicheck.workflows.release_members.compare_member_pair", _capture
        )

        extra = [flag] if flag else []
        code, _, _ = _invoke("compare", str(old_dir), str(new_dir), *extra)
        assert code == 0
        assert seen == [expected]

    def test_dir_input_dispatches_even_when_only_one_side_is_a_set(
        self, tmp_path: Path
    ) -> None:
        # A directory on *one* side is enough to route through the set-input
        # (release) path; the other side is a single snapshot file. The engine
        # matches libraries by stem across sides.
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        new_file = _write_snap(tmp_path / "libfoo.json", _snap())
        code, out, _ = _invoke("compare", str(old_dir), str(new_file))
        assert code == 0
        assert "NO_CHANGE" in out

    def test_exit_code_scheme_flag_no_longer_exists(self, tmp_path: Path) -> None:
        # CLI cleanup phase two PR G2 deleted --exit-code-scheme entirely
        # (the manual algorithm selector), so it now errors as an unknown
        # option (exit 64) on every operand shape, set inputs included --
        # not the release-fan-out-specific "not supported" usage error this
        # test used to check for.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--exit-code-scheme", "legacy"
        )
        assert code == 64
        assert "No such option" in (out + err)

    def test_write_now_supported_on_set_inputs(self, tmp_path: Path) -> None:
        # CLI cleanup phase two, PR E: --write now renders its secondary
        # format from the same already-computed per-library results the
        # release fan-out's primary format uses -- no second per-library
        # DiffResult needed. See test_compare_release.py's
        # TestReleaseWriteSecondaryOutput for the fuller positive coverage
        # (including the no-rerun assertion).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        write_path = tmp_path / "sec.json"
        code, out, err = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            f"json={write_path}",
        )
        assert code == 0
        assert write_path.is_file()
        assert "--write is not supported" not in (out + err)

    def test_config_severity_block_alone_selects_severity_scheme_for_set_inputs(
        self, tmp_path: Path
    ) -> None:
        # CLI cleanup phase two PR G2: there is no manual exit-code-scheme
        # pin any more (the `exit_code_scheme:` config key is gone
        # entirely). A project config's `severity:` block is now the *only*
        # thing that can select the severity-aware scheme, and it does so
        # unconditionally for a directory/package compare too -- a
        # `severity: abi_breaking: warning` config on a BREAKING pair now
        # demotes the exit code, since there is no way left to keep the
        # legacy 0/2/4 mapping while a severity setting is in effect.
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "severity:\n  abi_breaking: warning\n",
            encoding="utf-8",
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        code, out, _ = _invoke(
            "compare", str(old_dir), str(new_dir), "--config", str(cfg), "-o", "json=-"
        )

        assert code == 0
        assert json.loads(out)["verdict"] == "BREAKING"

    def test_config_exit_code_scheme_key_no_longer_exists_for_set_inputs(
        self,
        tmp_path: Path,
    ) -> None:
        """CLI cleanup phase two PR G2 deleted the top-level
        `exit_code_scheme:` config key entirely (it used to force the
        severity-aware scheme even with no `severity:` block/`--severity-*`
        flag anywhere -- Codex review on #549). `BuildConfig` now treats any
        unknown top-level key as a hard parse-time error, so a directory/
        package compare with this key still present fails fast at exit 64
        rather than silently reproducing the old forced-severity behavior."""
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("exit_code_scheme: severity\n", encoding="utf-8")

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        code, out, err = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(cfg),
            "-o",
            "json=-",
        )

        assert code == 64
        assert "exit_code_scheme" in (out + err)

    @pytest.mark.parametrize(
        "flag, value",
        [
            ("--sources", "old=src"),
            ("--build-info", "new=build"),
        ],
    )
    def test_evidence_flags_rejected_on_set_inputs(
        self, tmp_path: Path, flag: str, value: str
    ) -> None:
        # Inline build/source evidence *inputs* can't be threaded through the
        # release fan-out, so they are rejected rather than silently dropped
        # (Codex review).
        #
        # ``--depth source`` used to be a third row here. It is not an input
        # the fan-out would drop -- it is a *rung*, and which rungs a member
        # can reach is answered per member by the same
        # ``enforce_requested_depth`` a single-pair compare runs, not by a
        # front-end allow-list. The whole ladder is covered by
        # ``tests/test_cli_compare_release_depth.py``.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        # sided value like "old=src" — the path follows the side prefix
        side, _, name = value.partition("=")
        (tmp_path / name).mkdir(
            exist_ok=True
        )  # --sources/--build-info need a real path
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), flag, f"{side}={tmp_path / name}"
        )
        assert code != 0
        assert "not supported for directory/package" in (out + err)

    def test_header_graph_flags_are_removed_on_set_inputs(self, tmp_path: Path) -> None:
        """CLI cleanup H1: --header-graph/--header-graph-includes are gone
        outright now, so a directory/package compare rejects them the same
        way any other unknown option is rejected — a usage error, not a
        deprecation note."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--header-graph",
            "--header-graph-includes",
        )
        assert code == 64, (out, err)
        assert "No such option" in (out + err)

    def test_used_by_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # The release fan-out has no per-app scoping; a directory/package
        # compare with --used-by must reject loudly rather than silently run
        # an unscoped release comparison (Codex review).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        app = _make_pie_executable(tmp_path / "myapp")
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--used-by", str(app)
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--used-by" in msg

    def test_used_by_rejected_on_set_inputs_even_with_dry_run(
        self, tmp_path: Path
    ) -> None:
        # Regression: --dry-run used to emit its "ok" report and exit 0/1
        # *before* this rejection ran, so a dry run could report a
        # --used-by + directory/package combination as valid even though the
        # real run immediately rejects it (Codex review / post-merge PR #566
        # review). The dry run must agree with the real run.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        app = _make_pie_executable(tmp_path / "myapp")
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--used-by", str(app), "--dry-run"
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--used-by" in msg
        assert "Dry run only" not in msg

    def test_required_symbol_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--set", 'consumers.required_symbols=["_Z3foov"]'
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--required-symbol" in msg

    def test_diagnostic_comparison_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # ADR-050 D2: the per-library release fan-out doesn't wire the
        # comparability gate's diagnostic escape hatch at all yet -- reject
        # the flag loudly rather than silently accept and ignore it
        # (CodeRabbit review, PR #631).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--diagnostic-comparison"
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "--diagnostic-comparison" in msg

    def test_labeled_include_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # ADR-050 D1: a labeled --include old:LABEL=PATH/new:LABEL=PATH would
        # be silently dropped by the release fan-out (it doesn't thread
        # project_include_labels into its per-library dumps) -- reject it
        # loudly rather than accept a flag whose effect vanishes.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--include",
            "old:support=old/src",
        )
        assert code != 0
        msg = out + err
        assert "not supported for directory/package" in msg
        assert "labeled --include" in msg

    def test_app_operand_rejected_with_hint(self, tmp_path: Path) -> None:
        app = _make_pie_executable(tmp_path / "myapp")
        new = _write_snap(tmp_path / "new.json", _snap())
        code, out, err = _invoke("compare", str(app), str(new))
        msg = out + err
        assert code != 0
        assert "--used-by" in msg

    def test_set_only_flags_warn_on_single_file(self, tmp_path: Path) -> None:
        """Phase 7d (one-comparison-product.md §4.1): --dso-only is gone
        from `compare`'s CLI -- release.dso_only in .abicheck.yml is its
        only source now, still a no-op (with the identical warning) on a
        single-file compare."""
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("release:\n  dso_only: true\n")
        result = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--config", str(cfg)]
        )
        # The setting is ignored (single-file path), with a warning on
        # stderr naming the config key -- not the removed --dso-only flag,
        # which would be a stale, uncopyable diagnostic (Codex review).
        assert result.exit_code == 4
        stderr = result.stderr or ""
        assert "only apply to directory/package" in stderr
        assert "release.dso_only" in stderr
        assert "--dso-only" not in stderr

    def test_a_directory_export_on_a_single_pair_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Plan slice 7m: the per-component export is an *artifact* request,
        so a single pair -- which has no components to fan out to -- is a
        usage error rather than a warn-and-ignore.

        This replaces the same claim about `--max-findings-per-library`,
        which retired outright (it capped a summary a single pair does not
        have). The other set-only knobs still warn: they promise nothing, so
        ignoring one loses nothing, while ignoring an export loses an
        artifact the user asked for -- which is what plan §Non-goals
        forbids.
        """
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        reports = tmp_path / "reports"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_f), str(new_f), "-o", f"json={reports}/"],
        )
        assert result.exit_code == 64, result.output
        assert "per-component export" in result.output
        assert not reports.exists(), "nothing may be written for a rejected export"


# ── parity: compare <dir> <dir> == compare-release <dir> <dir> (summary) ────────


class TestReleaseFanoutParity:
    def test_dir_summary_matches_compare_release(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())

        rel = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "-o", "json=-"]
        )
        cmp = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "-o", "json=-"]
        )
        assert rel.exit_code == cmp.exit_code == 4
        assert json.loads(rel.output) == json.loads(cmp.output)

    def test_output_dir_fanout_preserved(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        out_dir = tmp_path / "reports"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        code, _, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
            "-o",
            f"json={out_dir}/",
        )
        assert code == 4
        # Per-library reports were written under --output-dir (two-level output).
        assert out_dir.is_dir()
        assert list(out_dir.glob("*.json"))


# ── directory comparison (no deprecation: compare-release was removed) ───────


class TestDirectoryComparison:
    def test_compare_directories_runs_release_fanout(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert result.exit_code == 0
        # The standalone compare-release command was removed; no deprecation note.
        assert "deprecated" not in (result.stderr or "")
