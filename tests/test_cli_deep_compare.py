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

"""Unit tests for the ``deep-compare`` orchestrator (G21.9, cli_max.py).

These exercise the orchestration glue without a compiler: snapshot inputs pass
straight through to ``compare`` (a native binary would need castxml; covered by
the integration lane). They verify the no-evidence guard, the snapshot
pass-through + ignored-evidence warning, and that ``compare``'s verdict exit
code propagates unchanged.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import abicheck.cli_max as cli_max
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _snap(tmp_path: Path, name: str, *, with_foo: bool) -> Path:
    snap = AbiSnapshot(library="libfoo.so", version="1", from_headers=True)
    if with_foo:
        snap.functions.append(
            Function(name="foo", mangled="foo", return_type="void", is_extern_c=True)
        )
    out = tmp_path / name
    save_snapshot(snap, out)
    return out


def test_deep_compare_deep_depth_requires_evidence(tmp_path: Path) -> None:
    # A depth that collects L3-L5 with no source/build inputs is refused and
    # points at --depth headers / plain `compare` rather than a silent no-op.
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    res = CliRunner().invoke(main, ["deep-compare", str(old), str(new), "--depth", "full"])
    assert res.exit_code != 0
    assert "collects L3-L5 evidence but no sources" in res.output


def test_deep_compare_headers_depth_no_evidence_ok(tmp_path: Path) -> None:
    # --depth headers resolves to off (L2-only / plain compare), so it stays
    # usable without any --sources/--build-info (Codex review).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    res = CliRunner().invoke(main, ["deep-compare", str(old), str(new), "--depth", "headers"])
    assert res.exit_code == 0, res.output


def test_deep_compare_snapshot_passthrough_compatible(tmp_path: Path) -> None:
    # Snapshot inputs can't be re-dumped; --sources is reported as ignored and
    # the two identical surfaces compare as compatible (exit 0).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    srcs = tmp_path / "src"
    srcs.mkdir()
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--sources", str(srcs), "--depth", "headers"]
    )
    assert res.exit_code == 0, res.output
    # Both sides warned as snapshot pass-through with ignored evidence flags.
    assert res.output.count("is a snapshot") == 2


def test_deep_compare_propagates_break_exit_code(tmp_path: Path) -> None:
    # A removed exported function is a binary ABI break; deep-compare must surface
    # compare's verdict exit code unchanged (4 = BREAKING in the legacy scheme).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=False)
    srcs = tmp_path / "src"
    srcs.mkdir()
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--sources", str(srcs)]
    )
    assert res.exit_code == 4, (res.exit_code, res.output)


def test_deep_compare_threads_resolved_collect_mode(tmp_path: Path, monkeypatch) -> None:
    # Regression (Codex): the per-side dump must receive the SAME resolved
    # collect mode compare reports — bare --sources is off (not dump_cmd's
    # source-target default); --depth full is graph-full.
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    srcs = tmp_path / "src"
    srcs.mkdir()

    seen: list[str] = []

    def _spy(ctx, **kw):  # noqa: ANN001, ANN003
        seen.append(kw["collect_mode"])
        return kw["input_path"]  # pass the snapshot through to compare

    monkeypatch.setattr(cli_max, "_prepare_side", _spy)

    seen.clear()
    res = CliRunner().invoke(main, ["deep-compare", str(old), str(new), "--sources", str(srcs)])
    assert res.exit_code == 0, res.output
    assert seen == ["off", "off"]

    seen.clear()
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--sources", str(srcs), "--depth", "full"]
    )
    assert res.exit_code == 0, res.output
    assert seen == ["graph-full", "graph-full"]


class _FakeCtx:
    """Records ctx.invoke calls and materializes the requested output file."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, cmd, **kwargs) -> None:  # noqa: ANN001
        self.calls.append({"cmd": cmd, **kwargs})
        out = kwargs.get("output")
        if out is not None:
            Path(out).write_text("{}", encoding="utf-8")


def test_prepare_side_dumps_native_binary(tmp_path: Path, monkeypatch) -> None:
    # The native-binary branch dumps to a temp snapshot via ctx.invoke(dump_cmd)
    # with the source tree + depth threaded through — exercised here without a
    # real compiler by faking the format probe and the invoke.
    binary = tmp_path / "libfoo.so"
    binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(cli_max, "_normalize_binary_input", lambda p: (p, "elf"))
    ctx = _FakeCtx()
    srcs = tmp_path / "src"
    srcs.mkdir()

    inc = tmp_path / "inc"
    inc.mkdir()
    out = cli_max._prepare_side(
        ctx, input_path=binary, headers=(), includes=(inc,), sources=srcs,
        build_info=None, collect_mode="graph-full", version="2", lang="c++",
        header_backend="auto", out_dir=tmp_path, label="old",
    )
    assert out == tmp_path / "old.abi.json"
    assert out.exists()
    assert len(ctx.calls) == 1
    call = ctx.calls[0]
    assert call["cmd"] is cli_max.dump_cmd
    assert call["so_path"] == binary
    assert call["sources"] == srcs
    assert call["includes"] == (inc,)  # include dirs threaded into the dump
    # The resolved collect mode is threaded so the dump embeds exactly the depth
    # compare reports — never dump_cmd's own source-target default (Codex review).
    assert call["collect_mode"] == "graph-full"
    assert "depth" not in call
    assert call["output"] == out


def test_prepare_side_native_deep_without_sources_warns(tmp_path: Path, monkeypatch) -> None:
    # A native side at deep depth with no sources of its own embeds no L3-L5, so
    # the compare would silently miss source/graph findings — warn per side
    # rather than guess inputs (Codex review).
    binary = tmp_path / "libfoo.so"
    binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(cli_max, "_normalize_binary_input", lambda p: (p, "elf"))
    ctx = _FakeCtx()

    captured: list[str] = []
    monkeypatch.setattr(cli_max.click, "echo", lambda *a, **k: captured.append(str(a[0])))
    cli_max._prepare_side(
        ctx, input_path=binary, headers=(), includes=(), sources=None, build_info=None,
        collect_mode="graph-full", version="2", lang="c++",
        header_backend="auto", out_dir=tmp_path, label="new",
    )
    assert len(ctx.calls) == 1  # still dumped (best-effort)
    assert any("embeds no" in m and "--new-sources" in m for m in captured)


def test_prepare_side_snapshot_without_evidence_is_silent(tmp_path: Path, monkeypatch) -> None:
    # A snapshot side with no per-side evidence passes through with no warning
    # (the warning only fires when --*-sources/--*-build-info were given).
    snap = _snap(tmp_path, "s.json", with_foo=True)
    monkeypatch.setattr(cli_max, "_normalize_binary_input", lambda p: (p, None))
    ctx = _FakeCtx()

    captured: list[str] = []
    monkeypatch.setattr(cli_max.click, "echo", lambda *a, **k: captured.append(str(a)))
    out = cli_max._prepare_side(
        ctx, input_path=snap, headers=(), includes=(), sources=None, build_info=None,
        collect_mode="off", version="1", lang="c++",
        header_backend="auto", out_dir=tmp_path, label="new",
    )
    assert out == snap
    assert ctx.calls == []
    assert captured == []  # no ignored-evidence warning


def test_deep_compare_keep_snapshots_dir_for_passthrough(tmp_path: Path) -> None:
    # --keep-snapshots is created even when both inputs pass through (no dumps
    # land in it, but the directory is honoured rather than a temp dir).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    srcs = tmp_path / "src"
    srcs.mkdir()
    keep = tmp_path / "keep"
    res = CliRunner().invoke(
        main,
        ["deep-compare", str(old), str(new), "--sources", str(srcs),
         "--keep-snapshots", str(keep), "--depth", "headers"],
    )
    assert res.exit_code == 0, res.output
    assert keep.is_dir()
