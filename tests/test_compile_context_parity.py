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

"""compare↔dump↔scan L2 compile-context parity + threading (ADR-037 D3 / ADR-035).

The cross-toolchain + frontend family is defined once in
``cli_options.compile_context_options`` and shared by ``compare`` / ``dump`` /
``scan``; the project ``compile:`` block is folded in by the one shared resolver
(``cli_options.merge_compile_config`` / ``resolve_compile_context``). This guards
that the three commands never drift, that ``scan`` threads the context down to the
header dump, and that ``compare`` now threads its both-sides context to *both*
sides while the per-side ``--old/new-ast-frontend`` override still wins.ADR-061 Phase 4, throughout: patch the owner, not ``abicheck.cli`` -- its lazy ``__getattr__`` means a ``setattr`` there rebinds nothing the caller reads.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.cli import compare_cmd, dump_cmd, main
from abicheck.cli_options import compile_context_options, sided_frontend_explicit
from abicheck.cli_scan import scan_cmd
from abicheck.model import AbiSnapshot
from abicheck.service_scan import CompileContext, ScanRequest

#: The dest names the compile-context family contributes (dump↔scan parity).
#: The ``gcc_*`` dests are deliberately absent: --gcc-options was removed as a
#: CLI flag first, and --gcc-path/--gcc-prefix/--gcc-option followed it once
#: --compiler/--compiler-prefix/--compiler-option superseded them. The internal
#: CompileContext.gcc_* fields survive, but no Click option registers those
#: dests anymore, so they are not *CLI-exposed* dests to check here.
_COMPILE_CONTEXT_DESTS = frozenset(
    {
        "header_backend",
        "compiler_path",
        "compiler_prefix",
        "compiler_option_tokens",
        "sysroot",
        "nostdinc",
    }
)


def _param_dests(cmd: object) -> set[str]:
    return {p.name for p in getattr(cmd, "params", [])}


def test_dump_exposes_full_compile_context_family() -> None:
    assert _COMPILE_CONTEXT_DESTS <= _param_dests(dump_cmd)


def test_scan_exposes_full_compile_context_family() -> None:
    assert _COMPILE_CONTEXT_DESTS <= _param_dests(scan_cmd)


def test_compare_exposes_full_compile_context_family() -> None:
    # ADR-037 D3: compare gained the shared L2 family (it previously had only
    # --ast-frontend inline and no --gcc-*/--sysroot/--nostdinc at all).
    assert _COMPILE_CONTEXT_DESTS <= _param_dests(compare_cmd)


def test_compare_dump_scan_compile_context_does_not_drift() -> None:
    # All three commands expose the *same* compile-context flags — the whole point
    # of sharing one decorator. (A future inline addition to one would break this.)
    compare_ctx = _param_dests(compare_cmd) & _COMPILE_CONTEXT_DESTS
    dump_ctx = _param_dests(dump_cmd) & _COMPILE_CONTEXT_DESTS
    scan_ctx = _param_dests(scan_cmd) & _COMPILE_CONTEXT_DESTS
    assert compare_ctx == dump_ctx == scan_ctx == _COMPILE_CONTEXT_DESTS


def test_compile_context_default_is_empty() -> None:
    assert CompileContext().is_default is True
    assert CompileContext(gcc_options="-DX").is_default is False
    assert CompileContext(frontend="clang").is_default is False


def test_scan_request_carries_compile_context() -> None:
    cc = CompileContext(gcc_options="-DFOO=1", sysroot=Path("/sr"), nostdinc=True)
    req = ScanRequest(binaries=[Path("x.so")], compile=cc)
    assert req.compile is cc
    # Default request has an inert context (call sites can skip threading).
    assert ScanRequest().compile.is_default is True


def test_dump_elf_threads_compile_context_to_dumper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``service._dump_elf`` unpacks the CompileContext into ``dumper.dump``."""
    import abicheck.dumper as dumper_mod
    from abicheck import service

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")

    captured: dict[str, object] = {}

    def _fake_dump(**kwargs: object) -> object:
        captured.update(kwargs)

        class _Snap:
            parsed_with_build_context = False

        return _Snap()

    monkeypatch.setattr(dumper_mod, "dump", _fake_dump)

    cc = CompileContext(
        gcc_path="/opt/g++",
        gcc_prefix="aarch64-linux-gnu-",
        gcc_options="-DFOO=1",
        gcc_option_tokens=("-isystem", "/x"),
        sysroot=tmp_path,
        nostdinc=True,
    )
    service._dump_elf(
        tmp_path / "libfoo.so",
        [header],
        [],
        "1.0",
        "c++",
        compile=cc,
    )
    assert captured["gcc_path"] == "/opt/g++"
    assert captured["gcc_prefix"] == "aarch64-linux-gnu-"
    assert captured["gcc_options"] == "-DFOO=1"
    # The CompileContext tokens thread through and lead; because this request has
    # a -H header *and* an -isystem build context, the inferred header root is
    # appended after as its own -isystem entry (searched below the build's, which
    # is emitted first, but still above the standard system dirs).
    tokens = captured["gcc_option_tokens"]
    assert tokens[:2] == ("-isystem", "/x")  # build context leads
    assert str(tmp_path) in tokens
    assert tokens[tokens.index(str(tmp_path)) - 1] == "-isystem"
    assert tokens.index("/x") < tokens.index(str(tmp_path))
    assert captured["sysroot"] == tmp_path
    assert captured["nostdinc"] is True


def test_dump_elf_default_compile_context_is_inert(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No CompileContext → the dumper sees the unchanged defaults (no regression)."""
    import abicheck.dumper as dumper_mod
    from abicheck import service

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    captured: dict[str, object] = {}

    def _fake_dump(**kwargs: object) -> object:
        captured.update(kwargs)
        return type("_S", (), {"parsed_with_build_context": False})()

    monkeypatch.setattr(dumper_mod, "dump", _fake_dump)
    service._dump_elf(tmp_path / "libfoo.so", [header], [], "1.0", "c++")
    assert captured["gcc_path"] is None
    assert captured["gcc_options"] is None
    assert captured["nostdinc"] is False
    assert captured["gcc_option_tokens"] == ()


# ── .abicheck.yml compile: block (ADR-035 D6.1 / ADR-037 D4) ─────────────────


def test_buildconfig_parses_compile_block() -> None:
    from abicheck.buildsource.inline import BuildConfig

    bc = BuildConfig.from_dict(
        {
            "compile": {
                "frontend": "clang",
                "std": "c++20",
                "include_dirs": ["include", "third_party/inc"],
                "defines": ["FOO=1", "BAR"],
                "sysroot": "/opt/sysroot",
                "nostdinc": True,
            }
        }
    )
    assert bc.compile_frontend == "clang"
    assert bc.compile_std == "c++20"
    assert bc.compile_include_dirs == ["include", "third_party/inc"]
    assert bc.compile_defines == ["FOO=1", "BAR"]
    assert bc.compile_sysroot == "/opt/sysroot"
    assert bc.compile_nostdinc is True
    # Round-trips through to_dict.
    assert BuildConfig.from_dict(bc.to_dict()).to_dict() == bc.to_dict()


def test_buildconfig_rejects_compile_std_flag_injection() -> None:
    from abicheck.buildsource.inline import BuildConfig

    with pytest.raises(ValueError, match=r"compile\.std"):
        BuildConfig.from_dict({"compile": {"std": "c++20 -Xclang"}})


def test_buildconfig_rejects_compile_define_flag_injection() -> None:
    from abicheck.buildsource.inline import BuildConfig

    with pytest.raises(ValueError, match=r"compile\.defines"):
        BuildConfig.from_dict(
            {"compile": {"defines": ["SAFE=1 -Xclang -load -Xclang ./evil.so"]}}
        )


def test_buildconfig_rejects_bad_compile_frontend() -> None:
    import pytest as _pytest

    from abicheck.buildsource.inline import BuildConfig

    with _pytest.raises(ValueError, match="compile.frontend"):
        BuildConfig.from_dict({"compile": {"frontend": "gcc"}})


def test_buildconfig_accepts_hybrid_compile_frontend() -> None:
    # G28 Phase 3 (Codex review): docs/reference/config-file.md documents
    # `compile.frontend: hybrid`, and BuildConfig.from_dict feeds the SAME
    # CompileContext.frontend the L2 dump/compare/scan path already resolves
    # "hybrid" through (cli_options._merge_compile_config) -- a config
    # loader that still rejected it would break an otherwise-valid
    # .abicheck.yml for anyone following that reference.
    from abicheck.buildsource.inline import BuildConfig

    bc = BuildConfig.from_dict({"compile": {"frontend": "hybrid"}})
    assert bc.compile_frontend == "hybrid"


def test_merge_compile_config_cli_wins_over_config(tmp_path: Path) -> None:
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "compile:\n"
        "  frontend: castxml\n"
        "  std: c++17\n"
        "  defines: [CFG=1]\n"
        "  include_dirs: [include]\n"
        "  sysroot: /cfg/sysroot\n"
    )
    cli = CompileContext(frontend="clang", gcc_options="-std=c++20 -DCLI=1")
    merged, includes = _merge_compile_config(cli, (), cfg)
    # CLI frontend + gcc_options win; config std/defines are NOT synthesized.
    assert merged.frontend == "clang"
    assert merged.gcc_options == "-std=c++20 -DCLI=1"
    # Config sysroot fills the unset CLI field; include_dirs resolve under cfg dir.
    assert merged.sysroot == Path("/cfg/sysroot")
    assert includes == (tmp_path / "include",)


def test_merge_compile_config_include_dirs_resolve_against_project_root_for_dot_github_config(
    tmp_path: Path,
) -> None:
    """A config discovered under `.github/` (or `.github/abicheck/`) still
    resolves a relative `compile.include_dirs` entry against the project
    root, not against `.github/` itself (Codex review on PR #828 —
    `merge_compile_config` used to resolve against `cfg.parent`, which is
    wrong once a config can live somewhere other than the project root)."""
    from abicheck.cli_scan import _merge_compile_config

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    cfg = github_dir / ".abicheck.yml"
    cfg.write_text("compile:\n  include_dirs: [include]\n", encoding="utf-8")

    _, includes = _merge_compile_config(CompileContext(), (), cfg)
    assert includes == (tmp_path / "include",)
    assert includes != (github_dir / "include",)


def test_merge_compile_config_include_dirs_resolve_against_project_root_for_dot_github_abicheck_config(
    tmp_path: Path,
) -> None:
    from abicheck.cli_scan import _merge_compile_config

    subdir = tmp_path / ".github" / "abicheck"
    subdir.mkdir(parents=True)
    cfg = subdir / ".abicheck.yml"
    cfg.write_text("compile:\n  include_dirs: [include]\n", encoding="utf-8")

    _, includes = _merge_compile_config(CompileContext(), (), cfg)
    assert includes == (tmp_path / "include",)


def test_merge_compile_config_cli_token_wins_over_config_std(tmp_path: Path) -> None:
    """CLI --compiler-option tokens must win over a config-
    synthesized -std=/-D, the same way the now-removed --gcc-options scalar
    used to (Codex review, PR #757): appending config tokens *after* the
    CLI's own gcc_option_tokens silently let `compile.std` override an
    explicit CLI -std= once --gcc-options (which used to suppress config
    synthesis entirely) was removed. Config tokens must come first so a
    compiler's own last-flag-wins semantics still resolve to the CLI value."""
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n  defines: [CFG=1]\n")
    cli = CompileContext(gcc_option_tokens=("-std=c++17",))
    merged, _ = _merge_compile_config(cli, (), cfg)
    assert merged.gcc_options is None
    # config tokens first, CLI token last -- a real compiler resolves the
    # last -std= it sees, so this ordering is what makes the CLI value win.
    assert merged.gcc_option_tokens == ("-std=c++20", "-DCFG=1", "-std=c++17")


def test_merge_compile_config_uses_config_when_cli_unset(tmp_path: Path) -> None:
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n  defines: [A, B=2]\n  frontend: clang\n")
    merged, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert merged.frontend == "clang"
    # std + defines are synthesized as literal argv tokens when the user gave no
    # --gcc-options, so config values cannot inject extra compiler options.
    assert merged.gcc_options is None
    assert merged.gcc_option_tokens == ("-std=c++20", "-DA", "-DB=2")


def test_merge_compile_config_keeps_config_values_literal(tmp_path: Path) -> None:
    from abicheck.cli_scan import _merge_compile_config

    # Each compile config scalar reaches gcc_option_tokens as ONE literal token
    # (`-std=<v>` / `-D<v>`), not shell-split. The define carries embedded quotes
    # (a legal single option atom — a string-valued macro) precisely so this stays
    # a regression test: a token that flowed through shlex-split plumbing would be
    # de-quoted to `-DMSG=hi`, so the verbatim `-DMSG="hi"` below proves the literal
    # path. The values must be single option atoms with no whitespace: PR #471
    # rejects whitespace in compile.std/compile.defines so a config scalar can never
    # expand into multiple compiler arguments (flag injection like
    # `-Xclang -load ./evil.so`) — that rejection is covered by
    # test_buildconfig_rejects_compile_{std,define}_flag_injection.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "compile:\n  std: 'c++20'\n  defines:\n    - 'MSG=\"hi\"'\n",
        encoding="utf-8",
    )
    merged, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert merged.gcc_options is None
    assert merged.gcc_option_tokens == (
        "-std=c++20",
        '-DMSG="hi"',
    )


def test_merge_compile_config_noop_without_path() -> None:
    from abicheck.cli_scan import _merge_compile_config

    cli = CompileContext(gcc_options="-DX")
    merged, includes = _merge_compile_config(cli, (Path("a"),), None)
    assert merged is cli
    assert includes == (Path("a"),)


def test_merge_compile_config_autodiscovers_from_sources(tmp_path: Path) -> None:
    # No explicit --config, but a .abicheck.yml at the --sources root carries a
    # compile: block → honored for L2 (Codex review parity with embed_build_source).
    src = tmp_path / "tree"
    src.mkdir()
    (src / ".abicheck.yml").write_text(
        "compile:\n  std: c++20\n  include_dirs: [include]\n", encoding="utf-8"
    )
    from abicheck.cli_scan import _merge_compile_config

    merged, includes = _merge_compile_config(CompileContext(), (), None, sources=src)
    assert merged.gcc_options is None
    assert merged.gcc_option_tokens == ("-std=c++20",)
    assert includes == (src / "include",)


def test_merge_compile_config_explicit_config_beats_autodiscovery(
    tmp_path: Path,
) -> None:
    src = tmp_path / "tree"
    src.mkdir()
    (src / ".abicheck.yml").write_text("compile:\n  std: c++11\n", encoding="utf-8")
    explicit = tmp_path / "explicit.yml"
    explicit.write_text("compile:\n  std: c++23\n", encoding="utf-8")
    from abicheck.cli_scan import _merge_compile_config

    merged, _ = _merge_compile_config(CompileContext(), (), explicit, sources=src)
    assert merged.gcc_options is None
    assert merged.gcc_option_tokens == ("-std=c++23",)  # explicit --config wins


def test_probe_gnu_system_includes_mocked(monkeypatch, tmp_path: Path) -> None:
    # Cover the subprocess probe body without a real compiler: only *existing*
    # dirs survive the filter, in search order.
    from abicheck import dumper_sysinc

    real = tmp_path / "inc"
    real.mkdir()
    missing = tmp_path / "gone"  # never created

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc,
        "_parse_gnu_include_search_dirs",
        lambda s: [str(missing), str(real)],
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    assert out == [str(real)]


def test_probe_gnu_system_includes_handles_oserror(monkeypatch) -> None:
    from abicheck import dumper_sysinc

    def _boom(*a, **k):
        raise OSError("no compiler")

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", _boom)
    assert dumper_sysinc._probe_gnu_system_includes("g++", cpp=True) == []


def test_probe_gnu_system_includes_degrades_on_deadline_exceeded(monkeypatch) -> None:
    # Codex review (PR #591): the probe is now deadline-bounded via
    # deadline.run_bounded; an exhausted --budget must degrade to [] (same
    # best-effort contract as a missing compiler/timeout), not propagate and
    # abort the whole L2 clang parse over an auxiliary parity probe.
    from abicheck import dumper_sysinc

    def _raise(*a, **k):
        raise dumper_sysinc.deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", _raise)
    assert dumper_sysinc._probe_gnu_system_includes("g++", cpp=True) == []


def test_probe_gnu_system_includes_bounded_by_local_cap_not_full_scan_budget(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 2: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=15 alone did nothing once a generous --budget was active -- a
    hung `g++ -E -v -` could consume the whole remaining scan budget instead
    of this probe's own 15s cap. Mirrors the include-map local-cap fix."""
    from abicheck import deadline, dumper_sysinc

    seen_remaining: list[float | None] = []

    def fake_run(*_a, **_k):
        seen_remaining.append(deadline.remaining())

        class _P:
            stderr = ""

        return _P()

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", fake_run)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)

    assert seen_remaining
    # Bound by the probe's own 15s cap, not the 1800s scan budget.
    assert seen_remaining[0] is not None and seen_remaining[0] <= 15.5


@pytest.mark.parametrize(
    "path,expected",
    [
        # GCC's own compiler resource / builtins dir (GCC_INCLUDE_DIR + fixed).
        ("/usr/lib/gcc/x86_64-linux-gnu/13/include", True),
        ("/usr/lib/gcc/x86_64-linux-gnu/13/include-fixed", True),
        ("/usr/lib64/gcc/x86_64-redhat-linux/12/include", True),
        ("/usr/lib32/gcc/x86_64-linux-gnu/13/include", True),
        ("/usr/libx32/gcc/x86_64-linux-gnu/13/include", True),
        ("/opt/cross/lib/gcc-cross/aarch64-linux-gnu/12/include", True),
        # libstdc++ and libc dirs must be KEPT (not GCC resource dirs).
        ("/usr/include/c++/13", False),
        ("/usr/include/x86_64-linux-gnu/c++/13", False),
        ("/usr/include", False),
        ("/usr/local/include", False),
        ("/usr/include/x86_64-linux-gnu", False),
        # A 'gcc' segment not preceded by an exact multilib dir is not the
        # resource dir: a bare 'gcc' dir, or a 'lib'-prefixed but non-multilib
        # dir like 'libfoo', must not be misclassified (matcher tightened from
        # startswith('lib') to an exact multilib-name set).
        ("/home/gcc/include", False),
        ("/opt/libfoo/gcc/x86_64-linux-gnu/13/include", False),
        # Unresolved paths containing a literal '../' walk-back: GCC and
        # Intel's icpx/icx report search dirs this way. The '..' segments
        # must be normalized away before matching, or a real libstdc++/libc
        # dir that happens to be reached by walking back out of the gcc/
        # version dir is misclassified as the GCC resource dir it walks
        # *through* rather than the dir it actually names.
        ("/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13", False),
        ("/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include", False),
        (
            "/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/x86_64-linux-gnu/c++/13",
            False,
        ),
        # A genuine GCC resource dir is still caught after normalization even
        # when spelled with a redundant './'/'..' that lexically collapses
        # back to the same resource path (not walked all the way out).
        ("/usr/lib/gcc/x86_64-linux-gnu/13/./include", True),
        ("/usr/lib/gcc/x86_64-linux-gnu/13/sub/../include-fixed", True),
        # A real libstdc++ dir merely NESTED inside a lib/gcc/<triple>/<ver>
        # tree (not the resource dir itself) must stay False: a scan for "any
        # multilib+gcc pair" over-matches every descendant of that tree, not
        # just the literal .../include[-fixed] resource dir (Codex review, PR
        # #643, round 3).
        ("/opt/lib/gcc/toolchain/13/include/c++/13", False),
        ("/usr/lib/gcc/x86_64-linux-gnu/13/include/c++/13", False),
        # A trailing segment after 'include'/'include-fixed' breaks the exact
        # end-of-path shape -- this is not the resource dir itself either.
        ("/usr/lib/gcc/x86_64-linux-gnu/13/include/nested", False),
        # Homebrew's packaged GCC nests an extra 'current' alias segment and
        # a second literal 'gcc' segment: its build is configured with
        # --libdir=<prefix>/lib/gcc/current, and GCC's own build script
        # always appends gcc/<target>/<version> beneath whatever libdir it
        # is given (confirmed against a real Homebrew GCC install; Codex
        # review, PR #643, round 6).
        (
            "/opt/homebrew/Cellar/gcc/14.2.0/lib/gcc/current/gcc/"
            "aarch64-apple-darwin23/14/include-fixed",
            True,
        ),
        (
            "/opt/homebrew/Cellar/gcc/14.2.0/lib/gcc/current/gcc/"
            "aarch64-apple-darwin23/14/include",
            True,
        ),
        # The alias segment's value isn't checked -- only its structural
        # position between the two 'gcc' segments -- so a differently-named
        # alias (not literally "current") still matches the same shape.
        (
            "/opt/homebrew/Cellar/gcc/14.2.0/lib/gcc/14/gcc/"
            "aarch64-apple-darwin23/14/include",
            True,
        ),
        # The nested shape still requires a genuine 'gcc'/'gcc-cross' pair at
        # both positions -- an unrelated intervening segment structure with
        # only one real 'gcc' occurrence must not match.
        (
            "/opt/homebrew/Cellar/gcc/14.2.0/lib/gcc/current/notgcc/"
            "aarch64-apple-darwin23/14/include",
            False,
        ),
    ],
)
def test_is_gnu_compiler_resource_dir(path: str, expected: bool) -> None:
    from abicheck import dumper_sysinc

    assert dumper_sysinc._is_gnu_compiler_resource_dir(path) is expected


def test_probe_gnu_system_includes_drops_gcc_resource_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # The GCC compiler resource dir (lib/gcc/.../include) must not cross over to
    # the clang backend: clang has its own intrinsics headers, and GCC's
    # immintrin.h/ia32intrin.h reference GCC-only __builtin_ia32_* that clang
    # cannot parse. It is dropped even though it exists on disk.
    from abicheck import dumper_sysinc

    libstdcxx = tmp_path / "include" / "c++" / "13"
    libc = tmp_path / "include"
    gcc_res = tmp_path / "lib" / "gcc" / "x86_64-linux-gnu" / "13" / "include"
    for d in (libstdcxx, libc, gcc_res):
        d.mkdir(parents=True, exist_ok=True)

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc,
        "_parse_gnu_include_search_dirs",
        lambda s: [str(libstdcxx), str(gcc_res), str(libc)],
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    assert out == [str(libstdcxx), str(libc)]  # gcc resource dir filtered out


def test_probe_gnu_system_includes_drops_homebrew_nested_gcc_resource_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # Homebrew's packaged GCC reports its resource dir as
    # .../lib/gcc/current/gcc/<triple>/<ver>/include[-fixed] (confirmed
    # against a real Homebrew GCC install; Codex review, PR #643, round 6).
    # This must be dropped like any other GCC resource dir, not kept.
    from abicheck import dumper_sysinc

    libstdcxx = tmp_path / "include" / "c++" / "13"
    homebrew_gcc_res = (
        tmp_path
        / "lib"
        / "gcc"
        / "current"
        / "gcc"
        / "aarch64-apple-darwin23"
        / "14"
        / "include-fixed"
    )
    for d in (libstdcxx, homebrew_gcc_res):
        d.mkdir(parents=True, exist_ok=True)

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc,
        "_parse_gnu_include_search_dirs",
        lambda s: [str(homebrew_gcc_res), str(libstdcxx)],
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    assert out == [str(libstdcxx)]  # Homebrew's nested gcc resource dir filtered out


def test_probe_gnu_system_includes_keeps_unresolved_walk_back_libstdcxx(
    monkeypatch, tmp_path: Path
) -> None:
    # GCC (and Intel's icpx/icx) report the real libstdc++ dir as an unresolved
    # string that walks back out of the versioned gcc/ dir with literal '../'
    # segments (e.g. '.../lib/gcc/<triple>/13/../../../../include/c++/13'),
    # rather than the already-resolved '/usr/include/c++/13'. That must be
    # kept, not dropped as though it were the GCC resource dir it walks
    # *through* on the way there.
    from abicheck import dumper_sysinc

    real_libstdcxx = tmp_path / "include" / "c++" / "13"
    real_libstdcxx.mkdir(parents=True, exist_ok=True)
    (tmp_path / "lib" / "gcc" / "x86_64-linux-gnu" / "13").mkdir(parents=True)
    unresolved_libstdcxx = str(
        tmp_path
        / "lib"
        / "gcc"
        / "x86_64-linux-gnu"
        / "13"
        / ".."
        / ".."
        / ".."
        / ".."
        / "include"
        / "c++"
        / "13"
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc,
        "_parse_gnu_include_search_dirs",
        lambda s: [unresolved_libstdcxx],
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    assert out == [unresolved_libstdcxx]


_SYMLINK_SKIP_REASON = (
    "creates a directory symlink via Path.symlink_to(target_is_directory=True), "
    "which needs SeCreateSymbolicLinkPrivilege/Developer Mode on native Windows "
    "(same rationale as test_runtime_probe.py's symlink-dependent skips)"
)


@pytest.mark.skipif(sys.platform == "win32", reason=_SYMLINK_SKIP_REASON)
def test_probe_gnu_system_includes_resolves_symlink_before_classifying(
    monkeypatch, tmp_path: Path
) -> None:
    # Codex review (PR #643): lexically collapsing '..' is wrong once a
    # symlink sits in the path -- the OS resolves '..' relative to the
    # symlink's *target* directory, not the symlink's own location. Build a
    # case where that distinction changes the classification: the reported
    # path's literal segments walk (lexically) all the way out of lib/gcc to
    # a location with no GCC-resource shape at all, but 'triple/13' is
    # actually a symlink four levels *deeper* than a real version dir would
    # be, so the same four '../' hops physically land back on a sibling
    # '.../13.2.0/include-fixed' -- a real GCC resource dir, not system
    # libstdc++ (the shape-tightening from round 3 of this same review means
    # the landing spot must itself match the full resource-dir shape, not
    # merely sit somewhere under lib/gcc).
    from abicheck import dumper_sysinc

    base = tmp_path / "base"
    real_target = (
        base
        / "lib"
        / "gcc"
        / "x86_64-linux-gnu"
        / "13.2.0"
        / "extra1"
        / "extra2"
        / "extra3"
        / "extra4"
    )
    real_target.mkdir(parents=True)
    symlinked_ver = base / "lib" / "gcc" / "x86_64-linux-gnu" / "13"
    symlinked_ver.symlink_to(real_target, target_is_directory=True)
    # Where the four '../' hops *physically* land, starting from real_target:
    # extra4 -> extra3 -> extra2 -> extra1 -> 13.2.0, then include-fixed.
    physically_resolved = (
        base / "lib" / "gcc" / "x86_64-linux-gnu" / "13.2.0" / "include-fixed"
    )
    physically_resolved.mkdir(parents=True)

    reported = str(symlinked_ver / ".." / ".." / ".." / ".." / "include-fixed")
    # Sanity check the fixture actually exercises the symlink hazard: lexical
    # normpath must disagree with realpath, or this test proves nothing.
    assert os.path.normpath(reported) != os.path.realpath(reported)
    assert os.path.realpath(reported) == str(physically_resolved)
    assert dumper_sysinc._is_gnu_compiler_resource_dir(reported) is False
    assert (
        dumper_sysinc._is_gnu_compiler_resource_dir(os.path.realpath(reported)) is True
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc, "_parse_gnu_include_search_dirs", lambda s: [reported]
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    # Physically this is still inside lib/gcc -- must be dropped, not kept.
    assert out == []


@pytest.mark.skipif(sys.platform == "win32", reason=_SYMLINK_SKIP_REASON)
def test_probe_gnu_system_includes_drops_terminal_symlinked_resource_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # Codex review (PR #643, round 2): the opposite symlink hazard from the
    # walk-back case above. Here the reported path has no '..' at all -- it
    # IS the canonical '.../lib/gcc/<triple>/<ver>/include' resource path --
    # but that exact directory is itself a symlink to storage physically
    # outside any lib/gcc hierarchy (e.g. a distro that stores GCC's
    # intrinsics headers in a shared location). realpath() alone would
    # resolve past the lib/gcc evidence and wrongly classify this as safe to
    # keep, feeding clang GCC's incompatible intrinsics headers. The raw,
    # lexically-normalized path must still be checked and win.
    from abicheck import dumper_sysinc

    base = tmp_path / "base"
    external_storage = base / "external_storage" / "gcc13_include"
    external_storage.mkdir(parents=True)
    canonical_resource_dir = base / "lib" / "gcc" / "x86_64-linux-gnu" / "13"
    canonical_resource_dir.mkdir(parents=True)
    terminal_symlink = canonical_resource_dir / "include"
    terminal_symlink.symlink_to(external_storage, target_is_directory=True)

    reported = str(terminal_symlink)
    # Sanity check the fixture actually exercises the hazard: the raw path
    # lexically matches the resource-dir shape, but its realpath doesn't.
    assert dumper_sysinc._is_gnu_compiler_resource_dir(reported) is True
    assert (
        dumper_sysinc._is_gnu_compiler_resource_dir(os.path.realpath(reported)) is False
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc, "_parse_gnu_include_search_dirs", lambda s: [reported]
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    # This is GCC's own named resource dir regardless of where it's
    # symlinked to -- must be dropped, not kept.
    assert out == []


@pytest.mark.skipif(sys.platform == "win32", reason=_SYMLINK_SKIP_REASON)
def test_probe_gnu_system_includes_drops_aliased_symlink_to_resource_dir(
    monkeypatch, tmp_path: Path
) -> None:
    # Codex review (PR #643, round 8): the mirror case of the terminal-symlink
    # test above. There, the raw path IS the canonical resource-dir name but
    # is symlinked *away* from it; here the raw path is an arbitrary,
    # non-resource-shaped alias (no '..' either) that is symlinked *to* the
    # real resource dir. The raw string alone (round 2's fix) misses this
    # evidence entirely and would wrongly keep GCC's intrinsics headers under
    # the innocuous-looking alias name. Both directions must be checked when
    # there's no '..' to make the lexical form ambiguous.
    from abicheck import dumper_sysinc

    base = tmp_path / "base"
    real_resource_dir = base / "lib" / "gcc" / "x86_64-linux-gnu" / "13" / "include"
    real_resource_dir.mkdir(parents=True)
    alias = base / "opt" / "toolchain" / "include"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(real_resource_dir, target_is_directory=True)

    reported = str(alias)
    # Sanity check the fixture actually exercises the hazard: the raw path
    # does NOT lexically match the resource-dir shape, but its realpath does.
    assert dumper_sysinc._is_gnu_compiler_resource_dir(reported) is False
    assert (
        dumper_sysinc._is_gnu_compiler_resource_dir(os.path.realpath(reported)) is True
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc, "_parse_gnu_include_search_dirs", lambda s: [reported]
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    # This alias resolves to GCC's own resource dir -- must be dropped.
    assert out == []


@pytest.mark.skipif(sys.platform == "win32", reason=_SYMLINK_SKIP_REASON)
def test_probe_gnu_system_includes_keeps_real_dir_via_midpath_symlink(
    monkeypatch, tmp_path: Path
) -> None:
    # Codex review (PR #643, round 7): checking the raw string in *addition
    # to* realpath (an "or", as an earlier version of this fix did) is wrong
    # once '..' is involved, not just insufficient on its own. Here a
    # mid-path symlink component ('hop') sits between the version dir and a
    # trailing '..': lexically this collapses right back to the canonical
    # resource shape, but the compiler's actual open() call resolves through
    # the symlink to a real, unrelated include dir elsewhere. Checking the
    # raw string here would wrongly drop that real include dir -- only
    # realpath is trustworthy once '..' is present.
    from abicheck import dumper_sysinc

    base = tmp_path / "base"
    real_deep = base / "external" / "deep"
    real_deep.mkdir(parents=True)
    real_include = base / "external" / "include"
    real_include.mkdir(parents=True)
    ver_dir = base / "lib" / "gcc" / "x86_64-linux-gnu" / "13"
    ver_dir.mkdir(parents=True)
    hop = ver_dir / "hop"
    hop.symlink_to(real_deep, target_is_directory=True)

    reported = str(hop / ".." / "include")
    # Sanity check the fixture actually exercises the hazard: the raw path
    # lexically matches the resource-dir shape, but its realpath is a real,
    # differently-shaped, unrelated directory.
    assert dumper_sysinc._is_gnu_compiler_resource_dir(reported) is True
    assert os.path.realpath(reported) == str(real_include)
    assert (
        dumper_sysinc._is_gnu_compiler_resource_dir(os.path.realpath(reported)) is False
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc, "_parse_gnu_include_search_dirs", lambda s: [reported]
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    # This is a real, unrelated include dir -- must be kept, not dropped.
    assert out == [reported]


@pytest.mark.skipif(sys.platform == "win32", reason=_SYMLINK_SKIP_REASON)
def test_probe_gnu_system_includes_keeps_libstdcxx_symlinked_under_lib_gcc(
    monkeypatch, tmp_path: Path
) -> None:
    # Codex review (PR #643, round 3): a real libstdc++ dir reported by a
    # plain, no-'..' path (no lib/gcc trace at all in the raw string) can be
    # a symlink whose *target* happens to be physically stored underneath a
    # lib/gcc/<triple>/<ver> tree -- e.g. a distro nesting real std:: headers
    # inside its GCC install rather than GCC's own intrinsics dir. Before the
    # round-3 shape fix, realpath(d) would see the lib/gcc pair anywhere in
    # that resolved path and wrongly drop this real libstdc++ dir; the
    # trailing shape must be checked precisely (ends in a bare
    # include/include-fixed after exactly <triple>/<ver>, not merely
    # "somewhere under lib/gcc").
    from abicheck import dumper_sysinc

    base = tmp_path / "base"
    real_target = (
        base / "opt" / "lib" / "gcc" / "toolchain" / "13" / "include" / "c++" / "13"
    )
    real_target.mkdir(parents=True)
    reported_dir = base / "opt" / "include" / "c++" / "13"
    reported_dir.parent.mkdir(parents=True)
    reported_dir.symlink_to(real_target, target_is_directory=True)

    reported = str(reported_dir)
    # Sanity check the fixture actually exercises the hazard: the realpath
    # does sit under a lib/gcc tree, but not in the exact resource shape.
    assert dumper_sysinc._is_gnu_compiler_resource_dir(reported) is False
    assert (
        dumper_sysinc._is_gnu_compiler_resource_dir(os.path.realpath(reported)) is False
    )

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.deadline, "run_bounded", lambda *a, **k: _P())
    monkeypatch.setattr(
        dumper_sysinc, "_parse_gnu_include_search_dirs", lambda s: [reported]
    )
    out = dumper_sysinc._probe_gnu_system_includes("g++", cpp=True)
    # A real libstdc++ dir -- must be kept, not dropped.
    assert out == [reported]


def test_buildconfig_compile_frontend_case_insensitive() -> None:
    from abicheck.buildsource.inline import BuildConfig

    bc = BuildConfig.from_dict({"compile": {"frontend": "Clang"}})
    assert bc.compile_frontend == "clang"


def test_merge_compile_config_explicit_auto_beats_config(tmp_path: Path) -> None:
    # CLI > config: an explicitly-typed --ast-frontend auto bypasses a pinned
    # config frontend (Codex review).
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: clang\n", encoding="utf-8")
    from abicheck.cli_scan import _merge_compile_config

    # Default 'auto' (not explicit) inherits config 'clang'.
    inherit, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert inherit.frontend == "clang"
    # Explicit 'auto' wins.
    explicit, _ = _merge_compile_config(
        CompileContext(frontend="auto"), (), cfg, frontend_explicit=True
    )
    assert explicit.frontend == "auto"


def test_merge_compile_config_explicit_malformed_fails_loud(tmp_path) -> None:
    # An *explicit* --config (build_config not None) that won't parse must fail
    # loudly, not silently drop the compile: settings (Codex review).
    import click

    from abicheck.cli_scan import _merge_compile_config

    bad = tmp_path / ".abicheck.yml"
    bad.write_text("compile: [unterminated\n", encoding="utf-8")
    with pytest.raises(click.ClickException, match="cannot parse build config"):
        _merge_compile_config(CompileContext(gcc_options="-DX"), (), bad)


def test_merge_compile_config_autodiscovered_malformed_warns(tmp_path, capsys) -> None:
    # An *auto-discovered* config (build_config None, found via --sources) stays
    # best-effort: warn + CLI-only fallback rather than fail the run.
    src = tmp_path / "src"
    src.mkdir()
    (src / ".abicheck.yml").write_text("compile: [unterminated\n", encoding="utf-8")
    cli = CompileContext(gcc_options="-DX")
    merged, _ = _merge_compile_config_autodiscover(cli, src)
    assert merged is cli  # CLI-only fallback
    assert "could not parse auto-discovered" in capsys.readouterr().err


def _merge_compile_config_autodiscover(
    cli: CompileContext, src: Path
) -> tuple[CompileContext, tuple[Path, ...]]:
    from abicheck.cli_scan import _merge_compile_config

    return _merge_compile_config(cli, (), None, sources=src)


def test_try_header_scoped_dump_threads_compile_to_dumper(
    monkeypatch, tmp_path: Path
) -> None:
    # PE/Mach-O native header scoping forwards the compile context to the dumper
    # (Codex review: gcc_options/sysroot must reach PE/Mach-O header parsing).
    import abicheck.dumper as dumper_mod
    from abicheck import service

    header = tmp_path / "h.h"
    header.write_text("int foo(void);\n")
    captured: dict[str, object] = {}

    def _fake_dumper_pe(*args, **kwargs):
        captured.update(kwargs)
        # A snapshot with a PUBLIC-visibility symbol so scoping counts as
        # matched (only `.visibility` is read by _has_matched_public_surface).
        # A real AbiSnapshot, not a bare SimpleNamespace: ADR-050's
        # _attach_extraction_contract (called by _try_header_scoped_dump
        # right after this returns) also reads `.ast_toolchain`/
        # `.from_headers`.
        from abicheck.model import Function, Visibility

        return AbiSnapshot(
            library="x",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            variables=[],
        )

    monkeypatch.setattr(dumper_mod, "_dump_pe", _fake_dumper_pe)
    cc = CompileContext(
        gcc_options="-std=c++20 -DPE",
        gcc_prefix="x-",
        sysroot=tmp_path,
        nostdinc=True,
    )
    snap, reason = service._try_header_scoped_dump(
        "pe", tmp_path / "x.dll", [header], [], "1.0", "c++", compile=cc
    )
    assert reason is None  # matched
    assert captured["gcc_options"] == "-std=c++20 -DPE"
    assert captured["gcc_prefix"] == "x-"
    assert captured["sysroot"] == tmp_path
    assert captured["nostdinc"] is True


def test_merge_compile_config_nostdinc_precedence(tmp_path: Path) -> None:
    # config compile.nostdinc: true is inherited by default, but an explicit
    # --no-nostdinc (nostdinc_explicit, value False) overrides it (Codex review).
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  nostdinc: true\n", encoding="utf-8")
    from abicheck.cli_scan import _merge_compile_config

    # Default (not explicit) inherits config True.
    inherit, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert inherit.nostdinc is True
    # Explicit --no-nostdinc (cli False, explicit) overrides config True.
    override, _ = _merge_compile_config(
        CompileContext(nostdinc=False), (), cfg, nostdinc_explicit=True
    )
    assert override.nostdinc is False
    # Explicit --nostdinc with no config also holds.
    cfg.write_text("compile:\n  std: c++20\n", encoding="utf-8")
    on, _ = _merge_compile_config(
        CompileContext(nostdinc=True), (), cfg, nostdinc_explicit=True
    )
    assert on.nostdinc is True


# ── compare end-to-end threading (ADR-037 D3) ────────────────────────────────


def _two_elf(tmp_path: Path) -> tuple[Path, Path, Path]:
    old_so = tmp_path / "old.so"
    new_so = tmp_path / "new.so"
    old_so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    new_so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")
    return old_so, new_so, header


def _compare_capturing_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra_args: list[str]
) -> list[dict[str, object]]:
    """Invoke ``compare`` on two fake ELFs with ``dumper.dump`` captured per side.

    A header-scoped compare also fires the L0 hard-removal fold-in
    (``fold_l0_hard_removals``, case97 fix), which re-resolves both sides
    symbols-only (``headers=[]``) to recover an ELF-exported function the
    header AST can't see. Only the two *header-bearing* calls are the real
    per-side dumps this helper's callers care about.
    """
    import abicheck.dumper as dumper_mod
    from abicheck.model import AbiSnapshot

    old_so, new_so, header = _two_elf(tmp_path)
    calls: list[dict[str, object]] = []

    def _fake_dump(**kwargs: object) -> object:
        calls.append(kwargs)
        return AbiSnapshot(library="libfoo.so", version="1.0")

    monkeypatch.setattr(dumper_mod, "dump", _fake_dump)
    result = CliRunner().invoke(
        main,
        ["compare", str(old_so), str(new_so), "-H", str(header), *extra_args],
    )
    assert result.exit_code == 0, result.output
    header_calls = [c for c in calls if c.get("headers")]
    assert len(header_calls) == 2
    return header_calls


def test_compare_threads_compile_context_to_both_sides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--compiler* / --sysroot / --nostdinc reach *both* sides' dumper.dump (ADR-037 D3)."""
    sysroot = tmp_path / "sr"
    sysroot.mkdir()
    calls = _compare_capturing_dump(
        monkeypatch,
        tmp_path,
        [
            "--compiler",
            "/opt/g++",
            "--compiler-prefix",
            "aarch64-linux-gnu-",
            "--compiler-option",
            "-DFOO=1",
            "--sysroot",
            str(sysroot),
            "--nostdinc",
        ],
    )
    for c in calls:  # both old and new
        assert c["gcc_path"] == "/opt/g++"
        assert c["gcc_prefix"] == "aarch64-linux-gnu-"
        assert c["gcc_option_tokens"] == ("-DFOO=1",)
        assert c["sysroot"] == sysroot
        assert c["nostdinc"] is True


def test_compare_gcc_context_applies_with_per_side_frontend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The both-sides gcc context applies to both sides even when the frontend
    differs per side — the neutralized compile.frontend must not clobber the
    per-side header_backend (regression guard for the run_dump eff_backend rule)."""
    calls = _compare_capturing_dump(
        monkeypatch,
        tmp_path,
        [
            "--compiler-option",
            "-DBAR=2",
            "--ast-frontend",
            "castxml",
            "--ast-frontend",
            "new=clang",
        ],
    )
    # gcc context on both sides...
    assert all(c["gcc_option_tokens"] == ("-DBAR=2",) for c in calls)
    # ...while the per-side frontend override still wins.
    assert calls[0]["header_backend"] == "castxml"
    assert calls[1]["header_backend"] == "clang"


def test_a_one_sided_frontend_keeps_the_configured_frontend_for_the_other_side(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A side-qualified ``--ast-frontend`` must not discard ``compile.frontend``.

    Click reports one parameter source for the whole ``--ast-frontend``
    parameter, so ``new=castxml`` alone marks it COMMANDLINE while the shared
    value is the synthesized ``"auto"`` nobody typed. Reading the parameter
    source alone handed that default to ``merge_compile_config`` as an explicit
    override, so the old side -- which the user never mentioned -- was parsed
    with ``auto`` instead of the project's configured ``clang`` (Codex review).
    """
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: clang\n", encoding="utf-8")
    calls = _compare_capturing_dump(
        monkeypatch,
        tmp_path,
        ["--config", str(cfg), "--ast-frontend", "new=castxml"],
    )
    # The unqualified side inherits the config; the named side overrides it.
    assert calls[0]["header_backend"] == "clang"
    assert calls[1]["header_backend"] == "castxml"


def test_a_shared_frontend_still_beats_the_configured_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other direction of the same rule, so the fix above cannot be
    satisfied by simply never treating ``--ast-frontend`` as explicit: a
    stated shared value must still win over ``compile.frontend``."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: clang\n", encoding="utf-8")
    calls = _compare_capturing_dump(
        monkeypatch,
        tmp_path,
        ["--config", str(cfg), "--ast-frontend", "castxml"],
    )
    assert all(c["header_backend"] == "castxml" for c in calls)


def test_an_explicit_auto_still_beats_the_configured_frontend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`resolve_compile_context`'s own documented contract: "an
    explicitly-typed value -- even a default-looking ``auto`` -- beats a
    pinned config one". The fix must keep that true for a value the user
    really did type, and only stop claiming it for the synthesized one."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: clang\n", encoding="utf-8")
    calls = _compare_capturing_dump(
        monkeypatch,
        tmp_path,
        ["--config", str(cfg), "--ast-frontend", "auto"],
    )
    assert all(c["header_backend"] == "auto" for c in calls)


def test_compare_reads_compile_block_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compare folds the project .abicheck.yml compile: block into its L2 context
    (CLI > config) — std/defines synthesize literal argv tokens for both sides."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n  defines: [FOO=1]\n", encoding="utf-8")
    calls = _compare_capturing_dump(monkeypatch, tmp_path, ["--config", str(cfg)])
    for c in calls:
        assert c["gcc_options"] is None
        assert c["gcc_option_tokens"] == ("-std=c++20", "-DFOO=1")


def test_dump_reads_compile_block_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """dump's ELF path folds the compile: block in via the same shared resolver.

    CLI cleanup phase two, PR C: the real ELF run reaches `dumper.dump`
    via `execute_dump_request`, not the retired `perform_elf_dump` -- patch
    that instead, and assert `-std` reaches its literal gcc option tokens.
    """
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++17\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_dump(**kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="libfoo.so", version="1.0")

    monkeypatch.setattr("abicheck.dumper.dump", _fake_dump)
    result = CliRunner().invoke(
        main, ["dump", str(so), "-H", str(header), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert captured["gcc_options"] is None
    assert captured["gcc_option_tokens"] == ("-std=c++17",)


def test_compare_threads_compile_context_for_set_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Directory/package operands + a compile-context flag must thread the
    resolved CompileContext to the release fan-out, not reject it -- the
    per-library fan-out now threads the L2 context to each pair's header
    dump (fix: whole-product-bundle known-gap entry, AGENTS.md)."""
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod, "_dispatch_release_compare", lambda ctx, **kw: dispatched.update(kw)
    )
    result = CliRunner().invoke(
        main,
        ["compare", str(old_dir), str(new_dir), "--compiler-option", "-DX=1"],
    )
    assert result.exit_code == 0, result.output
    assert dispatched
    compile_context = dispatched["compile_context"]
    assert compile_context.gcc_option_tokens == ("-DX=1",)


@pytest.mark.parametrize(
    ("flag", "value", "attr", "expected"),
    [
        ("--compiler", "/custom/clang", "gcc_path", "/custom/clang"),
        ("--compiler-prefix", "aarch64-linux-gnu-", "gcc_prefix", "aarch64-linux-gnu-"),
        ("--compiler-option", "-DX=1", "gcc_option_tokens", ("-DX=1",)),
    ],
)
def test_compare_threads_compiler_aliases_for_set_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flag: str,
    value: str,
    attr: str,
    expected: object,
) -> None:
    """The --compiler*/--gcc-* aliases (CLI audit PR 2/5) must reach the
    release fan-out's resolved CompileContext exactly like they reach a
    single-pair compare's (test_compare_threads_compile_context_for_set_inputs
    above)."""
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod, "_dispatch_release_compare", lambda ctx, **kw: dispatched.update(kw)
    )
    result = CliRunner().invoke(
        main,
        ["compare", str(old_dir), str(new_dir), flag, value],
    )
    assert result.exit_code == 0, result.output
    compile_context = dispatched["compile_context"]
    assert getattr(compile_context, attr) == expected


@pytest.mark.parametrize(
    "flag",
    ["--ast-frontend"],
)
def test_compare_rejects_sided_ast_frontend_for_set_inputs(
    tmp_path: Path, flag: str
) -> None:
    """A *sided* --ast-frontend old=/new= override still has no
    per-library-pair-within-a-release meaning, so it stays rejected."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    result = CliRunner().invoke(
        main,
        ["compare", str(old_dir), str(new_dir), flag, "old=clang"],
    )
    assert result.exit_code != 0
    assert "--ast-frontend old=" in result.output
    assert "directory/package" in result.output


class _FakeCtx:
    """Minimal click.Context stand-in for sided_frontend_explicit's own
    contract, exercised directly rather than only through a real CLI
    invocation -- see this file's own module docstring on why this guard
    exists."""

    def __init__(
        self,
        source: click.core.ParameterSource,
        header_backend: list[tuple[str, str]],
    ) -> None:
        self._source = source
        self.params = {"header_backend": header_backend}

    def get_parameter_source(self, name: str) -> click.core.ParameterSource:
        assert name == "header_backend"
        return self._source


def test_sided_frontend_explicit_direct() -> None:
    cmdline = click.core.ParameterSource.COMMANDLINE
    default = click.core.ParameterSource.DEFAULT
    # Not COMMANDLINE at all -> False, regardless of the raw value.
    assert sided_frontend_explicit(_FakeCtx(default, [("old", "clang")])) is False
    # A sided pair (old=/new=, not "both") -> True.
    assert sided_frontend_explicit(_FakeCtx(cmdline, [("old", "clang")])) is True
    assert sided_frontend_explicit(_FakeCtx(cmdline, [("new", "clang")])) is True
    # Only a "both" pair -> False (that's _shared_frontend_explicit's case).
    assert sided_frontend_explicit(_FakeCtx(cmdline, [("both", "clang")])) is False
    # An unsided command's plain string (no pair list at all) -> False.
    assert sided_frontend_explicit(_FakeCtx(cmdline, "clang")) is False


def test_compare_set_inputs_without_compile_flags_not_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard fires only on explicitly-passed compile-context flags — a plain
    directory compare still dispatches (no false rejection from the 'auto'
    --ast-frontend default)."""
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    dispatched: dict[str, object] = {}

    def _fake_dispatch(ctx: object, **kwargs: object) -> None:
        dispatched.update(kwargs)

    monkeypatch.setattr(cli_mod, "_dispatch_release_compare", _fake_dispatch)
    result = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
    assert result.exit_code == 0, result.output
    assert dispatched  # the fan-out was reached, not rejected


def test_compare_set_inputs_applies_config_compile_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A directory/package compare in a project with a .abicheck.yml compile:
    block must apply it (not silently drop it, and no longer just warn) --
    the fan-out now threads the L2 context (fix: whole-product-bundle
    known-gap entry, AGENTS.md)."""
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n", encoding="utf-8")

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod,
        "_dispatch_release_compare",
        lambda ctx, **kw: dispatched.update(kw),
    )
    result = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert dispatched
    assert "compile: block is not applied" not in result.output
    compile_context = dispatched["compile_context"]
    assert compile_context.gcc_option_tokens == ("-std=c++20",)


def test_compare_set_inputs_forwards_config_include_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression (Codex review): a directory/package compare in a project
    with a .abicheck.yml compile.include_dirs block must forward the
    merged include dirs (CLI -I roots + config-appended ones) to the
    release fan-out's own `includes=` -- a prior revision resolved the
    merged tuple via resolve_compile_context() but discarded it, dispatching
    the raw, unmerged CLI `includes` instead, so headers requiring the
    configured include root could fail or parse incompletely despite the
    compile: block otherwise being applied.
    """
    import abicheck.frontends.cli.commands.compare as cli_mod

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    include_dir = tmp_path / "vendor_include"
    include_dir.mkdir()
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        f"compile:\n  include_dirs:\n    - {include_dir}\n", encoding="utf-8"
    )

    dispatched: dict[str, object] = {}
    monkeypatch.setattr(
        cli_mod,
        "_dispatch_release_compare",
        lambda ctx, **kw: dispatched.update(kw),
    )
    result = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert dispatched
    assert include_dir in dispatched["includes"]


def test_compare_config_include_dirs_survive_per_side_include(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """config compile.include_dirs apply to BOTH sides even when --old-include
    overrides the both-sides -I for one side (Codex review)."""
    import abicheck.dumper as dumper_mod
    from abicheck.model import AbiSnapshot

    old_so, new_so, header = _two_elf(tmp_path)
    cfg_inc = tmp_path / "cfg_inc"
    cfg_inc.mkdir()
    old_only = tmp_path / "old_inc"
    old_only.mkdir()
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  include_dirs: [cfg_inc]\n", encoding="utf-8")

    calls: list[dict[str, object]] = []

    def _fake_dump(**kwargs: object) -> object:
        calls.append(kwargs)
        return AbiSnapshot(library="libfoo.so", version="1.0")

    monkeypatch.setattr(dumper_mod, "dump", _fake_dump)
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "-H",
            str(header),
            "--config",
            str(cfg),
            "--include",
            "old=" + str(old_only),
        ],
    )
    assert result.exit_code == 0, result.output
    # A header-scoped compare also fires the L0 hard-removal fold-in (case97
    # fix), which re-resolves both sides symbols-only (headers=[]); only the
    # two header-bearing calls are this test's real per-side dumps.
    header_calls = [c for c in calls if c.get("headers")]
    assert len(header_calls) == 2
    old_inc = list(header_calls[0]["extra_includes"])  # type: ignore[arg-type]
    new_inc = list(header_calls[1]["extra_includes"])  # type: ignore[arg-type]
    # Old side: its per-side override AND the config dir (config not dropped).
    assert old_only in old_inc
    assert cfg_inc in old_inc
    # New side: no override → config dir still present.
    assert cfg_inc in new_inc


def _capture_dump_pe(captured: dict[str, object], **kwargs: object) -> AbiSnapshot:
    """Shared fake for `abicheck.service_dump_native._dump_pe` (ADR-063 Phase 1)."""
    captured.update(kwargs)
    return AbiSnapshot(library="foo.dll", version="1.0")


def test_dump_pe_threads_compile_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A PE/Mach-O dump now folds the compile: block into header scoping too — the
    context is resolved before the format dispatch and threaded into the non-ELF
    path (Codex review). Previously --gcc-options were warned-and-ignored there."""
    pe = tmp_path / "foo.dll"
    pe.write_bytes(b"MZ" + b"\x00" * 128)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n  frontend: clang\n", encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "abicheck.service_dump_native._dump_pe",
        lambda *a, **k: _capture_dump_pe(captured, **k),
    )
    result = CliRunner().invoke(
        main, ["dump", str(pe), "-H", str(header), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    cc = captured["compile"]
    assert cc is not None
    assert getattr(cc, "frontend") == "clang"
    assert getattr(cc, "gcc_options") is None
    assert getattr(cc, "gcc_option_tokens") == ("-std=c++20",)
    assert captured["header_backend"] == "clang"
    assert "will be ignored" not in result.output


def test_dump_pe_explicit_gcc_options_no_longer_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pe = tmp_path / "foo.dll"
    pe.write_bytes(b"MZ" + b"\x00" * 128)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "abicheck.service_dump_native._dump_pe",
        lambda *a, **k: _capture_dump_pe(captured, **k),
    )
    result = CliRunner().invoke(
        main, ["dump", str(pe), "-H", str(header), "--compiler-option", "-DPE=1"]
    )
    assert result.exit_code == 0, result.output
    assert "will be ignored" not in result.output
    assert getattr(captured["compile"], "gcc_option_tokens") == ("-DPE=1",)


def test_fallback_flag_is_scoped_to_one_cli_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)

    @click.command()
    @compile_context_options()
    def probe(**_kwargs: object) -> None:
        click.echo(os.environ.get("ABICHECK_ALLOW_AST_FALLBACK", "unset"))

    runner = CliRunner()
    enabled = runner.invoke(probe, ["--allow-ast-frontend-fallback"])
    disabled = runner.invoke(probe, [])

    assert enabled.exit_code == 0
    assert enabled.output.strip() == "1"
    assert disabled.exit_code == 0
    assert disabled.output.strip() == "unset"
    assert "ABICHECK_ALLOW_AST_FALLBACK" not in os.environ


def test_allow_unsupported_castxml_flag_is_scoped_to_one_cli_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors test_fallback_flag_is_scoped_to_one_cli_invocation for the
    --allow-unsupported-castxml flag (castxml_policy's version-gate override) —
    previously this had no CLI spelling at all, only its env var (model.py's
    ast_toolchain_supported docstring documented a CLI flag that didn't exist)."""
    monkeypatch.delenv("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", raising=False)

    @click.command()
    @compile_context_options()
    def probe(**_kwargs: object) -> None:
        click.echo(os.environ.get("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", "unset"))

    runner = CliRunner()
    enabled = runner.invoke(probe, ["--allow-unsupported-castxml"])
    disabled = runner.invoke(probe, [])

    assert enabled.exit_code == 0
    assert enabled.output.strip() == "1"
    assert disabled.exit_code == 0
    assert disabled.output.strip() == "unset"
    assert "ABICHECK_ALLOW_UNSUPPORTED_CASTXML" not in os.environ


# ── --compiler/--compiler-prefix/--compiler-option ───────────────────────────
#
# The one cross-toolchain spelling. The former --gcc-path/--gcc-prefix/
# --gcc-option aliases and the merge function that reconciled them are gone;
# what remains to pin is that the surviving flags reach CompileContext's
# internal gcc_* fields through the one choke point compare/dump/scan share
# (resolve_compile_context -- see the module docstring above). This probe
# command exercises that mapping through the real Click parsing path.


@pytest.fixture
def _compile_context_probe():
    from abicheck.cli_options import resolve_compile_context

    @click.command()
    @compile_context_options()
    @click.pass_context
    def probe(ctx: click.Context, **kwargs: object) -> None:
        cc, _includes = resolve_compile_context(
            ctx,
            sysroot=kwargs["sysroot"],  # type: ignore[arg-type]
            nostdinc=kwargs["nostdinc"],  # type: ignore[arg-type]
            header_backend=kwargs["header_backend"],  # type: ignore[arg-type]
            includes=(),
            build_config=None,
            compiler_path=kwargs["compiler_path"],  # type: ignore[arg-type]
            compiler_prefix=kwargs["compiler_prefix"],  # type: ignore[arg-type]
            compiler_option_tokens=kwargs["compiler_option_tokens"],  # type: ignore[arg-type]
        )
        click.echo(
            f"path={cc.gcc_path} prefix={cc.gcc_prefix} tokens={cc.gcc_option_tokens}"
        )

    return probe


def test_compiler_flags_reach_the_compile_context(_compile_context_probe) -> None:
    result = CliRunner().invoke(
        _compile_context_probe,
        ["--compiler", "/usr/bin/clang", "--compiler-prefix", "arm-"],
    )
    assert result.exit_code == 0, result.output
    assert "path=/usr/bin/clang prefix=arm-" in result.output


def test_removed_gcc_spellings_are_rejected(_compile_context_probe) -> None:
    """The legacy aliases are gone outright, not hidden-but-functional: a
    caller still passing one gets a hard usage error naming the flag rather
    than a silently-ignored value."""
    for flag, value in (
        ("--gcc-path", "/usr/bin/gcc"),
        ("--gcc-prefix", "aarch64-linux-gnu-"),
        ("--gcc-option", "-DOLD"),
    ):
        result = CliRunner().invoke(_compile_context_probe, [flag, value])
        assert result.exit_code != 0, (flag, result.output)
        assert "No such option" in result.output, (flag, result.output)


def test_compiler_option_tokens_accumulate_verbatim(_compile_context_probe) -> None:
    """--compiler-option is repeatable and never whitespace-split, so a flag
    and its own spaced operand stay adjacent and in order."""
    result = CliRunner().invoke(
        _compile_context_probe,
        ["--compiler-option", "-include", "--compiler-option", "some header.h"],
    )
    assert result.exit_code == 0, result.output
    assert "tokens=('-include', 'some header.h')" in result.output


def test_neither_compiler_flag_given_no_crash(_compile_context_probe) -> None:
    result = CliRunner().invoke(_compile_context_probe, [])
    assert result.exit_code == 0, result.output
    assert "path=None prefix=None tokens=()" in result.output


def test_a_one_sided_frontend_keeps_the_source_trees_configured_frontend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same rule on the *inline source-tree* path, which had its own copy.

    ``resolve_compile_context`` was fixed to read the shared ``--ast-frontend``
    value's own explicitness, but ``_embed_inline_source_sides`` still asked
    Click for the whole parameter's source -- so ``--ast-frontend new=castxml``
    marked it COMMANDLINE, the synthesized shared ``"auto"`` reached the *old*
    side as an explicit override, and an ``--old-sources`` tree's own
    ``.abicheck.yml`` ``compile.frontend`` was suppressed and frozen at
    ``auto``: a materially different snapshot for the side the user never
    mentioned (Codex review).

    Asserted at the boundary the bug lives on -- what each side is *told* about
    explicitness -- rather than through a full inline dump, so the test states
    the contract rather than one downstream consequence of it.
    """
    import abicheck.frontends.cli.commands.compare as helpers

    old_so, new_so, header = _two_elf(tmp_path)
    src = tmp_path / "srctree"
    src.mkdir()
    (src / ".abicheck.yml").write_text(
        "compile:\n  frontend: clang\n", encoding="utf-8"
    )

    seen: list[dict[str, object]] = []
    real = helpers._embed_inline_source_side

    def _spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return (kwargs["input_path"], kwargs["sources"], kwargs["build_info"])

    monkeypatch.setattr(helpers, "_embed_inline_source_side", _spy)
    assert real is not _spy  # the symbol really was patched, not shadowed
    CliRunner().invoke(
        main,
        [
            "compare", str(old_so), str(new_so), "-H", str(header),
            "--sources", f"old={src}",
            "--ast-frontend", "new=castxml",
        ],
    )
    assert len(seen) == 2, seen
    old_side, new_side = seen
    # The side nobody named must not be told the frontend was stated: that is
    # what lets its own source-tree config still apply.
    assert old_side["frontend_explicit"] is False, old_side
    # ...while the named side keeps its genuine per-side override.
    assert new_side["frontend_explicit"] is True, new_side


def test_a_shared_frontend_is_explicit_for_both_inline_source_sides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The other direction, so the fix above cannot be satisfied by simply
    # never reporting the inline path's shared frontend as explicit.
    import abicheck.frontends.cli.commands.compare as helpers

    old_so, new_so, header = _two_elf(tmp_path)
    src = tmp_path / "srctree2"
    src.mkdir()

    seen: list[dict[str, object]] = []

    def _spy(*args: object, **kwargs: object) -> object:
        seen.append(dict(kwargs))
        return (kwargs["input_path"], kwargs["sources"], kwargs["build_info"])

    monkeypatch.setattr(helpers, "_embed_inline_source_side", _spy)
    res = CliRunner().invoke(
        main,
        [
            "compare", str(old_so), str(new_so), "-H", str(header),
            "--sources", f"old={src}",
            "--ast-frontend", "castxml",
        ],
    )
    assert len(seen) == 2, res.output
    assert all(s["frontend_explicit"] is True for s in seen), seen
