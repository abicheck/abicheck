# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-074 end-to-end: a real compiler, a real header AST, a real snapshot.

These exist because a unit test over the token tail proves abicheck *emits*
``-D`` -- not that the declaration behind the macro actually reaches the
model. Every assertion below is about declarations present in a snapshot
produced by a real CastXML/Clang parse of a guarded header, plus the
comparison behaviour across two macro contexts.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.serialization import load_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF-scoped: builds a real .so with the host gcc",
    ),
]

_HAVE_GCC = shutil.which("gcc") is not None
_HAVE_CASTXML = shutil.which("castxml") is not None
_HAVE_CLANG = shutil.which("clang") is not None

#: A guarded surface with three independently-reachable states, so one
#: fixture covers "absent without the macro", "present with it", and
#: "a value selects between two declarations".
_HEADER = """
#ifndef GUARDED_H
#define GUARDED_H
#ifdef __cplusplus
extern "C" {
#endif
int guarded_always(int);
#ifdef FEATURE_API
int guarded_expert(__EXPERT_PARAMS__);
#endif
#if defined(MODE) && MODE == 2
int guarded_mode_two(void);
#else
int guarded_mode_other(void);
#endif
#ifdef __cplusplus
}
#endif
#endif
"""

_SOURCE = """
int guarded_always(int x) { return x; }
int guarded_expert(int x) { return x + 1; }
int guarded_mode_two(void) { return 2; }
int guarded_mode_other(void) { return 0; }
"""


def _build(root: Path, *, expert_params: str = "int") -> tuple[Path, Path]:
    """A real .so plus an include dir holding the guarded header."""
    include = root / "include"
    include.mkdir(parents=True, exist_ok=True)
    (include / "guarded.h").write_text(
        _HEADER.replace("__EXPERT_PARAMS__", expert_params)
    )
    src = root / "guarded.c"
    src.write_text(_SOURCE)
    so = root / "libguarded.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    return so, include


def _dump(tmp_path: Path, so: Path, include: Path, *args: str, out: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["dump", str(so), "-H", str(include), "-o", str(out), *args],
    )
    assert result.exit_code == 0, result.output


def _guarded_names(snapshot_path: Path) -> set[str]:
    snap = load_snapshot(str(snapshot_path))
    return {f.name for f in snap.functions if f.name.startswith("guarded_")}


@pytest.mark.skipif(not (_HAVE_GCC and _HAVE_CASTXML), reason="needs gcc + castxml")
class TestCastxmlBackend:
    def test_declaration_is_absent_without_the_macro_and_present_with_it(
        self, tmp_path: Path
    ) -> None:
        so, include = _build(tmp_path)
        off, on = tmp_path / "off.json", tmp_path / "on.json"
        _dump(tmp_path, so, include, out=off)
        _dump(tmp_path, so, include, "-DFEATURE_API", out=on)
        assert "guarded_expert" not in _guarded_names(off)
        assert "guarded_expert" in _guarded_names(on)
        assert "guarded_always" in _guarded_names(off)

    def test_a_macro_value_selects_between_two_declarations(
        self, tmp_path: Path
    ) -> None:
        so, include = _build(tmp_path)
        two, other = tmp_path / "two.json", tmp_path / "other.json"
        _dump(tmp_path, so, include, "-DMODE=2", out=two)
        _dump(tmp_path, so, include, "-DMODE=1", out=other)
        assert "guarded_mode_two" in _guarded_names(two)
        assert "guarded_mode_other" not in _guarded_names(two)
        assert "guarded_mode_other" in _guarded_names(other)
        assert "guarded_mode_two" not in _guarded_names(other)

    def test_the_definition_is_recorded_in_snapshot_provenance(
        self, tmp_path: Path
    ) -> None:
        so, include = _build(tmp_path)
        out = tmp_path / "s.json"
        _dump(tmp_path, so, include, "-DFEATURE_API", "-DMODE=2", out=out)
        args = load_snapshot(str(out)).ast_compile_args
        assert "-DFEATURE_API" in args
        assert "-DMODE=2" in args

    def test_config_defines_and_cli_define_merge_by_name_end_to_end(
        self, tmp_path: Path
    ) -> None:
        so, include = _build(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  defines:\n    - FEATURE_API\n    - MODE=1\n"
        )
        out = tmp_path / "merged.json"
        _dump(
            tmp_path,
            so,
            include,
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-DMODE=2",
            out=out,
        )
        names = _guarded_names(out)
        # FEATURE_API survives from the config; MODE is overridden by the CLI.
        assert "guarded_expert" in names
        assert "guarded_mode_two" in names
        assert "guarded_mode_other" not in names

    def test_dry_run_reports_the_same_effective_defines_the_real_run_uses(
        self, tmp_path: Path
    ) -> None:
        so, include = _build(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  defines:\n    - FEATURE_API\n    - MODE=1\n"
        )
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so),
                "-H",
                str(include),
                "--config",
                str(tmp_path / ".abicheck.yml"),
                "-DMODE=2",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "defines: FEATURE_API, MODE=2" in result.output
        # And the real run's own provenance agrees, token for token.
        out = tmp_path / "real.json"
        _dump(
            tmp_path,
            so,
            include,
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-DMODE=2",
            out=out,
        )
        args = load_snapshot(str(out)).ast_compile_args
        assert [a for a in args if a.startswith("-D")] == ["-DFEATURE_API", "-DMODE=2"]


@pytest.mark.skipif(not (_HAVE_GCC and _HAVE_CLANG), reason="needs gcc + clang")
def test_direct_clang_backend_receives_the_same_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second L2 backend, not just the default one -- ADR-074's matrix
    claims both, so both are probed."""
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    so, include = _build(tmp_path)
    out = tmp_path / "clang.json"
    _dump(tmp_path, so, include, "-DFEATURE_API", "-DMODE=2", out=out)
    names = _guarded_names(out)
    assert {"guarded_expert", "guarded_mode_two"} <= names
    assert "guarded_mode_other" not in names


@pytest.mark.skipif(not (_HAVE_GCC and _HAVE_CASTXML), reason="needs gcc + castxml")
class TestCompare:
    def test_a_break_inside_the_macro_gated_surface_needs_the_macro(
        self, tmp_path: Path
    ) -> None:
        """The motivating scenario: the signature change is invisible without
        -D and a real ABI break with it, with both sides parsed identically."""
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        old_so, old_inc = _build(old_root, expert_params="int")
        new_so, new_inc = _build(new_root, expert_params="int, int")

        base = [
            "compare",
            str(old_so),
            str(new_so),
            "-H",
            f"old={old_inc}",
            "-H",
            f"new={new_inc}",
        ]
        without = CliRunner().invoke(main, base)
        with_macro = CliRunner().invoke(main, [*base, "-DFEATURE_API"])
        assert without.exit_code == 0, without.output
        assert with_macro.exit_code == 4, with_macro.output
        assert "guarded_expert" in with_macro.output

    def test_one_definition_applies_to_both_sides(self, tmp_path: Path) -> None:
        """D1's symmetry, observed rather than assumed: with identical
        sources on both sides, enabling the macro must stay `compatible` --
        an asymmetric application would report the whole guarded surface as
        added or removed."""
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        old_so, old_inc = _build(old_root)
        new_so, new_inc = _build(new_root)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_so),
                str(new_so),
                "-H",
                f"old={old_inc}",
                "-H",
                f"new={new_inc}",
                "-DFEATURE_API",
                "-DMODE=2",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_macro_off_and_macro_on_snapshots_are_not_silently_comparable(
        self, tmp_path: Path
    ) -> None:
        """D5: the macro set is part of extraction identity, so a stored
        ordinary snapshot cannot be diffed against a macro-enabled one as if
        the extraction contracts matched."""
        so, include = _build(tmp_path)
        off, on = tmp_path / "off.json", tmp_path / "on.json"
        _dump(tmp_path, so, include, out=off)
        _dump(tmp_path, so, include, "-DFEATURE_API", out=on)
        result = CliRunner().invoke(main, ["compare", str(off), str(on)])
        assert result.exit_code != 0
        assert "not comparable" in result.output
        assert "macro_ops" in result.output


@pytest.mark.skipif(not (_HAVE_GCC and _HAVE_CASTXML), reason="needs gcc + castxml")
class TestOtherOperandShapes:
    """The shapes ADR-074 D1 names besides a plain two-file compare."""

    def test_the_release_fan_out_honours_the_same_definitions(
        self, tmp_path: Path
    ) -> None:
        """A directory/package compare resolves its own both-sides compile
        context; dropping -D there would make the same input behave
        differently depending on whether the operand was a file or a
        directory."""
        old_root, new_root = tmp_path / "o", tmp_path / "n"
        old_so, old_inc = _build(old_root, expert_params="int")
        new_so, new_inc = _build(new_root, expert_params="int, int")
        old_dir, new_dir = tmp_path / "relold", tmp_path / "relnew"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / old_so.name).write_bytes(old_so.read_bytes())
        (new_dir / new_so.name).write_bytes(new_so.read_bytes())

        base = [
            "compare",
            str(old_dir),
            str(new_dir),
            "-H",
            f"old={old_inc}",
            "-H",
            f"new={new_inc}",
        ]
        without = CliRunner().invoke(main, base)
        with_macro = CliRunner().invoke(main, [*base, "-DFEATURE_API"])
        assert without.exit_code == 0, without.output
        assert with_macro.exit_code == 4, with_macro.output

    def test_a_define_is_inert_on_a_binary_only_dump(self, tmp_path: Path) -> None:
        """No header parse happens, so there is nothing for the macro to do --
        and that is not an error (D1): rejecting it would break the stored /
        binary-only half of an otherwise uniform invocation."""
        so, _include = _build(tmp_path)
        plain, with_macro = tmp_path / "p.json", tmp_path / "m.json"
        result = CliRunner().invoke(
            main, ["dump", str(so), "--depth", "binary", "-o", str(plain)]
        )
        assert result.exit_code == 0, result.output
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so),
                "--depth",
                "binary",
                "-DFEATURE_API",
                "-o",
                str(with_macro),
            ],
        )
        assert result.exit_code == 0, result.output
        # ...and the two snapshots stay comparable, since no header parse
        # recorded a macro context on either side.
        compared = CliRunner().invoke(main, ["compare", str(plain), str(with_macro)])
        assert compared.exit_code == 0, compared.output

    def test_a_define_is_inert_without_any_headers(self, tmp_path: Path) -> None:
        so, _include = _build(tmp_path)
        result = CliRunner().invoke(
            main, ["dump", str(so), "-DFEATURE_API", "-o", str(tmp_path / "s.json")]
        )
        assert result.exit_code == 0, result.output


@pytest.mark.skipif(not (_HAVE_GCC and _HAVE_CASTXML), reason="needs gcc + castxml")
class TestNoBaselineAudit:
    """`compare --no-baseline` runs a real header parse over one artifact, so
    it needs the macro too -- and before ADR-074 it built no CompileContext at
    all (`compile=None`), which meant it silently ignored `.abicheck.yml`'s
    `compile:` block as well. Both are covered here."""

    @staticmethod
    def _audit(so: Path, include: Path, *args: str) -> str:
        result = CliRunner().invoke(
            main, ["compare", str(so), "--no-baseline", "-H", str(include), *args]
        )
        assert result.exit_code in (0, 1, 2, 4), result.output
        return result.output

    def test_the_macro_removes_the_false_accidental_surface_finding(
        self, tmp_path: Path
    ) -> None:
        """Without the macro the guarded declaration is invisible, so a
        genuinely-declared export is reported as accidental ABI surface. That
        finding is an artifact of the missing macro, not a real defect."""
        so, include = _build(tmp_path)
        without = self._audit(so, include)
        with_macro = self._audit(so, include, "-DFEATURE_API")
        assert "guarded_expert" in without
        assert "exported_not_public" in without
        assert "guarded_expert" not in with_macro

    def test_config_defines_reach_the_audit_too(self, tmp_path: Path) -> None:
        so, include = _build(tmp_path)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  defines:\n    - FEATURE_API\n")
        out = self._audit(so, include, "--config", str(cfg))
        assert "guarded_expert" not in out
