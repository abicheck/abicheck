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
runs — so `compare <dir> <dir>` reproduces a `compare-release <dir> <dir>` run.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_resolve import _looks_like_application, classify_compare_operand
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(version: str = "1.0", funcs: list[Function] | None = None,
          library: str = "libfoo.so") -> AbiSnapshot:
    if funcs is None:
        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int",
                          visibility=Visibility.PUBLIC)]
    return AbiSnapshot(library=library, version=version, functions=funcs, from_headers=True)


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def test_source_is_pack_detects_manifest(tmp_path: Path) -> None:
    """A `collect` pack (manifest.json present) is distinguished from a raw tree."""
    from abicheck.cli import _source_is_pack

    tree = tmp_path / "checkout"
    tree.mkdir()
    (tree / "main.c").write_text("int main(void){return 0;}\n")
    assert not _source_is_pack(tree)

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text("{}")
    assert _source_is_pack(pack)


def test_compare_source_tree_on_snapshot_input_is_ignored(tmp_path: Path) -> None:
    """A raw --old-sources tree on a snapshot input can't be embedded (you can't
    re-dump a snapshot), so compare warns and still produces a verdict."""
    old, new = _breaking_pair()
    old_f = _write_snap(tmp_path / "old.json", old)
    new_f = _write_snap(tmp_path / "new.json", new)
    tree = tmp_path / "src"
    tree.mkdir()  # no manifest.json → looks like a raw source checkout
    result = CliRunner().invoke(
        main, ["compare", str(old_f), str(new_f), "--old-sources", str(tree)]
    )
    out = (result.output or "") + (result.stderr or "")
    assert "ignored" in out, out
    assert result.exit_code in (0, 2, 4), out


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap("1.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
        Function(name="bar", mangled="_Z3barv", return_type="void", visibility=Visibility.PUBLIC),
    ], library=lib)
    new = _snap("2.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
    ], library=lib)
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
    struct.pack_into("<H", hdr, 16, 3)   # e_type = ET_DYN
    struct.pack_into("<H", hdr, 18, 0x3e)  # e_machine = x86-64
    struct.pack_into("<Q", hdr, 32, e_phoff)   # e_phoff
    struct.pack_into("<H", hdr, 54, e_phentsize)  # e_phentsize
    struct.pack_into("<H", hdr, 56, e_phnum)   # e_phnum
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
        pkg = tmp_path / "foo.tar.gz"
        pkg.write_bytes(b"\x1f\x8b\x08\x00")  # gzip magic; name suffix triggers detection
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

    def test_exit_code_scheme_rejected_on_set_inputs(self, tmp_path: Path) -> None:
        # --exit-code-scheme can't be honoured by the release fan-out, so it is
        # rejected rather than silently ignored (ADR-037 D12).
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out, err = _invoke(
            "compare", str(old_dir), str(new_dir), "--exit-code-scheme", "legacy"
        )
        assert code != 0
        assert "--exit-code-scheme is not supported" in (out + err)

    def test_app_operand_rejected_with_hint(self, tmp_path: Path) -> None:
        app = _make_pie_executable(tmp_path / "myapp")
        new = _write_snap(tmp_path / "new.json", _snap())
        code, out, err = _invoke("compare", str(app), str(new))
        msg = out + err
        assert code != 0
        assert "appcompat" in msg

    def test_set_only_flags_warn_on_single_file(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--dso-only"]
        )
        # The flag is ignored (single-file path), with a warning on stderr.
        assert result.exit_code == 4
        assert "only apply to directory/package" in (result.stderr or "")

    def test_explicit_jobs_zero_still_warns_on_single_file(self, tmp_path: Path) -> None:
        # `--jobs 0` is the default value, but passing it explicitly is still a
        # set-input flag the single-file path can't use, so it must warn.
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--jobs", "0"]
        )
        assert "-j/--jobs" in (result.stderr or "")


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
            main, ["compare-release", str(old_dir), str(new_dir), "--format", "json"]
        )
        cmp = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--format", "json"]
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
            "compare", str(old_dir), str(new_dir),
            "--output-dir", str(out_dir), "--format", "json",
        )
        assert code == 4
        # Per-library reports were written under --output-dir (two-level output).
        assert out_dir.is_dir()
        assert list(out_dir.glob("*.json"))


# ── alias smoke ────────────────────────────────────────────────────────────────

class TestDeprecatedAliases:
    def test_compare_release_still_works_and_warns(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = CliRunner().invoke(main, ["compare-release", str(old_dir), str(new_dir)])
        assert result.exit_code == 0
        assert "deprecated" in (result.stderr or "")

