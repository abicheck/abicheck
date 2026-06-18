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

"""dump↔scan L2 compile-context flag parity + threading (ADR-037 D3 / ADR-035).

The cross-toolchain + frontend family is defined once in
``cli_options.compile_context_options`` and shared by ``dump`` and ``scan``; this
guards that they never drift, and that ``scan`` actually threads the context down
to the header dump (so a ``scan`` of oneTBB-style headers gets the same build
context ``dump`` would use).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.cli import dump_cmd
from abicheck.cli_scan import scan_cmd
from abicheck.service_scan import CompileContext, ScanRequest

#: The dest names the compile-context family contributes (dump↔scan parity).
_COMPILE_CONTEXT_DESTS = frozenset(
    {
        "header_backend",
        "gcc_path",
        "gcc_prefix",
        "gcc_options",
        "gcc_option_tokens",
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


def test_dump_and_scan_compile_context_does_not_drift() -> None:
    # Both commands expose the *same* compile-context flags — the whole point of
    # sharing one decorator. (A future inline addition to one would break this.)
    dump_ctx = _param_dests(dump_cmd) & _COMPILE_CONTEXT_DESTS
    scan_ctx = _param_dests(scan_cmd) & _COMPILE_CONTEXT_DESTS
    assert dump_ctx == scan_ctx == _COMPILE_CONTEXT_DESTS


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
    assert captured["gcc_option_tokens"] == ("-isystem", "/x")
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


def test_buildconfig_rejects_bad_compile_frontend() -> None:
    import pytest as _pytest

    from abicheck.buildsource.inline import BuildConfig

    with _pytest.raises(ValueError, match="compile.frontend"):
        BuildConfig.from_dict({"compile": {"frontend": "gcc"}})


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


def test_merge_compile_config_uses_config_when_cli_unset(tmp_path: Path) -> None:
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  std: c++20\n  defines: [A, B=2]\n  frontend: clang\n")
    merged, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert merged.frontend == "clang"
    # std + defines synthesized into gcc_options when the user gave none.
    assert merged.gcc_options == "-std=c++20 -DA -DB=2"


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

    merged, includes = _merge_compile_config(
        CompileContext(), (), None, sources=src
    )
    assert merged.gcc_options == "-std=c++20"
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
    assert merged.gcc_options == "-std=c++23"  # explicit --config wins


def test_probe_gnu_system_includes_mocked(monkeypatch, tmp_path: Path) -> None:
    # Cover the subprocess probe body without a real compiler: only *existing*
    # dirs survive the filter, in search order.
    from abicheck import dumper_sysinc

    real = tmp_path / "inc"
    real.mkdir()
    missing = tmp_path / "gone"  # never created

    class _P:
        stderr = "ignored"

    monkeypatch.setattr(dumper_sysinc.subprocess, "run", lambda *a, **k: _P())
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

    monkeypatch.setattr(dumper_sysinc.subprocess, "run", _boom)
    assert dumper_sysinc._probe_gnu_system_includes("g++", cpp=True) == []
