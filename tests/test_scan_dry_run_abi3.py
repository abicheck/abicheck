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

"""CLI cleanup phase two, PR 5 fresh findings, closed here:

1. Neither ``scan --dry-run`` renderer validated ``--abi3`` applicability, so
   both previewed exit 0 for an invocation the real run would reject with
   ``EVIDENCE_CONTRACT_ERROR`` (exit 1). Closed via a cheap, binary-only
   extension probe (:func:`abicheck.python_ext.detect_python_extension_from_binary`)
   threaded through both dry-run builders.
2. A pinned depth backed only by a query-declaring ``--config`` (no
   ``--sources``/``--build-info``) priced L3/L4/L5 at a confident-looking
   zero TUs. Closed by flagging the total as genuinely unknown instead.

See ``docs/contribute/known-gaps.md`` for the original findings.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.buildsource.scan_levels import EvidenceDepth, SourceMethod
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
)
from abicheck.frontends.cli.artifact_set_dry_run import render_artifact_set_dry_run
from abicheck.frontends.cli.scan_dry_run import render_scan_dry_run
from abicheck.python_ext import detect_python_extension_from_binary
from abicheck.service_scan import ScanRequest, _estimate_total_tus

# ── detect_python_extension_from_binary: the cheap binary-only probe ──


def test_detect_python_extension_from_binary_unknown_format_returns_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / "notabinary.txt"
    path.write_bytes(b"not a binary")
    assert detect_python_extension_from_binary(path) is None


def test_detect_python_extension_from_binary_recognizes_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "foo.cpython-311-x86_64-linux-gnu.so"
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)  # real ELF magic, rest is a stub

    meta = ElfMetadata()
    meta.symbols = [
        ElfSymbol(
            name="PyInit_foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    meta.imports = [ElfImport(name="PyLong_FromLong")]
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda p: meta)

    result = detect_python_extension_from_binary(path)
    assert result is not None
    assert result.is_extension
    assert result.init_symbol == "PyInit_foo"


def test_detect_python_extension_from_binary_none_for_plain_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "libplain.so"
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)

    monkeypatch.setattr(
        "abicheck.elf_metadata.parse_elf_metadata", lambda p: ElfMetadata()
    )

    assert detect_python_extension_from_binary(path) is None


def test_resolve_python_ext_recognizes_json_snapshot(tmp_path: Path) -> None:
    """Codex review: `scan ARTIFACT` also accepts a pre-dumped JSON snapshot
    (no container magic bytes at all), and the real --abi3 run recognizes
    such an input's already-embedded `python_ext` fact directly -- the
    dry-run resolver's binary-only probe alone must not misreport it as "not
    an extension"; the snapshot fallback (`scan_abi3_resolve.
    resolve_python_ext`, not `python_ext.detect_python_extension_from_binary`
    itself -- see that function's own docstring for why the two are split
    across modules) closes the gap."""
    from abicheck.model import AbiSnapshot
    from abicheck.python_ext import PythonExtMetadata
    from abicheck.scan_abi3_resolve import resolve_python_ext
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(
        library="foo.abi3.so",
        version="1.0",
        elf=ElfMetadata(),
        python_ext=PythonExtMetadata(
            module_name="foo",
            init_symbol="PyInit_foo",
            python_major=3,
            cpython_imports=["PyLong_FromLong"],
        ),
    )
    path = tmp_path / "foo.abi.json"
    path.write_text(snapshot_to_json(snap))

    result = resolve_python_ext(path)
    assert result is not None
    assert result.is_extension
    assert result.module_name == "foo"


def test_detect_python_extension_from_binary_none_for_plain_json_snapshot(
    tmp_path: Path,
) -> None:
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(library="libplain.so", version="1.0", elf=ElfMetadata())
    path = tmp_path / "libplain.abi.json"
    path.write_text(snapshot_to_json(snap))

    assert detect_python_extension_from_binary(path) is None


def test_detect_python_extension_from_binary_none_for_malformed_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.abi.json"
    path.write_text("{not valid json")
    assert detect_python_extension_from_binary(path) is None


def test_resolve_python_ext_recognizes_gzip_snapshot(tmp_path: Path) -> None:
    """Codex review, second round: `load_snapshot` transparently decompresses
    gzip/zstd (ADR-059), and `service.resolve_input` accepts such a snapshot
    directly -- the resolver's earlier fix only checked for a raw `{` byte,
    which a compressed snapshot never starts with."""
    from abicheck.model import AbiSnapshot
    from abicheck.python_ext import PythonExtMetadata
    from abicheck.scan_abi3_resolve import resolve_python_ext
    from abicheck.serialization import write_snapshot

    snap = AbiSnapshot(
        library="foo.abi3.so",
        version="1.0",
        elf=ElfMetadata(),
        python_ext=PythonExtMetadata(
            module_name="foo",
            init_symbol="PyInit_foo",
            python_major=3,
            cpython_imports=["PyLong_FromLong"],
        ),
    )
    path = tmp_path / "foo.abi.json.gz"
    write_snapshot(snap, path, compression="gzip")

    result = resolve_python_ext(path)
    assert result is not None
    assert result.is_extension
    assert result.module_name == "foo"


def test_detect_python_extension_from_binary_follows_linker_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review: a GNU ld linker script (`libfoo.so` -> `libfoo.so.1`)
    has no container magic bytes at all -- the real run follows it via
    `service.resolve_input`'s own recursive resolution, so this probe must
    too, or a script pointing at a genuine extension module misreports
    "not an extension"."""
    real_binary = tmp_path / "libfoo.so.1"
    real_binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
    script = tmp_path / "libfoo.so"
    script.write_text("INPUT(libfoo.so.1)\n")

    meta = ElfMetadata()
    meta.symbols = [
        ElfSymbol(
            name="PyInit_foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    monkeypatch.setattr("abicheck.elf_metadata.parse_elf_metadata", lambda p: meta)

    result = detect_python_extension_from_binary(script)
    assert result is not None
    assert result.is_extension


# ── render_scan_dry_run: validates --abi3 instead of previewing exit 0 ──


def _dry_run_kwargs(tmp_path: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = dict(
        artifact=tmp_path / "lib.so",
        against=None,
        sources=None,
        effective_build_info=None,
        changed=[],
        changed_src="none",
        seeded=False,
        depth=None,
        eff_depth_enum=EvidenceDepth.BINARY,
        resolved=SourceMethod.S0,
        collect_mode="off",
        header_backend="auto",
        fmt="text",
    )
    kwargs.update(overrides)
    return kwargs


def test_render_scan_dry_run_blocks_when_not_an_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "abicheck.python_ext.detect_python_extension_from_binary", lambda p: None
    )
    result = render_scan_dry_run(**_dry_run_kwargs(tmp_path, abi3_floor=(3, 9)))
    assert result.exit_code == 1
    assert any(
        "not a recognisable CPython extension module" in b for b in result.blockers
    )
    assert any("--abi3 3.9" in b for b in result.blockers)


def test_render_scan_dry_run_abi3_ok_when_extension_recognized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ext = SimpleNamespace(is_extension=True, cpython_imports=["PyLong_FromLong"])
    monkeypatch.setattr(
        "abicheck.python_ext.detect_python_extension_from_binary", lambda p: fake_ext
    )
    result = render_scan_dry_run(**_dry_run_kwargs(tmp_path, abi3_floor=(3, 9)))
    assert result.exit_code == 0
    assert not result.blockers
    lines = " ".join(result.sections.get("Consumer/contract scoping", []))
    assert "stable-ABI audit: will run" in lines


def test_render_scan_dry_run_no_abi3_check_when_floor_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def _spy(path: Path) -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("abicheck.python_ext.detect_python_extension_from_binary", _spy)
    result = render_scan_dry_run(**_dry_run_kwargs(tmp_path, abi3_floor=None))
    assert result.exit_code == 0
    assert not called


# ── render_artifact_set_dry_run: same precondition, per member ──


def _set_dry_run_result(
    discovered: dict[str, Path], *, abi3_floor: tuple[int, int] | None
) -> object:
    req = SimpleNamespace(
        bundle_system_providers=(),
        depth=None,
        changed_src="none",
        changed_paths=[],
        sources=None,
        build_info=None,
        build_targets=(),
        abi3_floor=abi3_floor,
    )
    return render_artifact_set_dry_run(
        req,
        discovered=discovered,
        explicit=True,
        header_backend="auto",
        fmt="text",
        totals={},
        notes=[],
        blocker=None,
    )


def test_artifact_set_dry_run_blocks_on_non_extension_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = tmp_path / "ext.so"
    bad = tmp_path / "plain.so"
    good.write_bytes(b"")
    bad.write_bytes(b"")
    discovered = {"ext.so": good, "plain.so": bad}

    def _probe(path: Path) -> object | None:
        return SimpleNamespace(is_extension=True) if path == good else None

    monkeypatch.setattr(
        "abicheck.python_ext.detect_python_extension_from_binary", _probe
    )

    result = _set_dry_run_result(discovered, abi3_floor=(3, 9))
    assert result.exit_code == 1
    assert any("plain.so" in b for b in result.blockers)
    assert any("1 of 2 member(s)" in b for b in result.blockers)


def test_artifact_set_dry_run_abi3_ok_when_all_members_qualify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tmp_path / "a.so"
    b = tmp_path / "b.so"
    a.write_bytes(b"")
    b.write_bytes(b"")
    discovered = {"a.so": a, "b.so": b}

    monkeypatch.setattr(
        "abicheck.python_ext.detect_python_extension_from_binary",
        lambda p: SimpleNamespace(is_extension=True),
    )

    result = _set_dry_run_result(discovered, abi3_floor=(3, 9))
    assert result.exit_code == 0
    assert not result.blockers
    lines = " ".join(result.sections.get("Consumer/contract scoping", []))
    assert "stable-ABI audit: will run for all 2 member(s)" in lines


def test_artifact_set_dry_run_skips_abi3_check_when_unset(tmp_path: Path) -> None:
    a = tmp_path / "a.so"
    a.write_bytes(b"")
    result = _set_dry_run_result({"a.so": a}, abi3_floor=None)
    assert result.exit_code == 0


def test_artifact_set_dry_run_shows_unknown_not_zero_for_l3(tmp_path: Path) -> None:
    """Codex review, fresh evidence: the aggregate renderer folded an
    unknown member's (0, 0.0) into the summed L3_build total and rendered a
    confident-looking "0 TU(s) total, ~0.00s" -- the separate [UNKNOWN note
    bullet alone didn't stop that numeric row from misleading a reader."""
    a = tmp_path / "a.so"
    a.write_bytes(b"")
    req = SimpleNamespace(
        bundle_system_providers=(),
        depth="build",
        changed_src="none",
        changed_paths=[],
        sources=None,
        build_info=None,
        build_targets=(),
        abi3_floor=None,
    )
    result = render_artifact_set_dry_run(
        req,
        discovered={"a.so": a},
        explicit=True,
        header_backend="auto",
        fmt="text",
        totals={"L3_build": (0, 0.0)},
        notes=["build.query: .abicheck.yml [UNKNOWN: query-only build.query]"],
        blocker=None,
    )
    lines = result.sections.get("Resolved depth and source scope", [])
    l3_lines = [ln for ln in lines if ln.startswith("L3_build:")]
    assert l3_lines == [
        "L3_build: TU count/cost unknown for at least one member (see notes below)"
    ]
    assert any("understating it" in ln for ln in lines)


def test_artifact_set_dry_run_normal_totals_unaffected(tmp_path: Path) -> None:
    a = tmp_path / "a.so"
    a.write_bytes(b"")
    req = SimpleNamespace(
        bundle_system_providers=(),
        depth="build",
        changed_src="none",
        changed_paths=[],
        sources=None,
        build_info=None,
        build_targets=(),
        abi3_floor=None,
    )
    result = render_artifact_set_dry_run(
        req,
        discovered={"a.so": a},
        explicit=True,
        header_backend="auto",
        fmt="text",
        totals={"L3_build": (5, 1.25)},
        notes=["compile DB: compile_commands.json"],
        blocker=None,
    )
    lines = result.sections.get("Resolved depth and source scope", [])
    l3_lines = [ln for ln in lines if ln.startswith("L3_build:")]
    assert l3_lines == ["L3_build: 5 TU(s) total, ~1.25s -- summed over 1 member(s)"]
    assert not any("understating it" in ln for ln in lines)


# ── _estimate_total_tus: query-only build config prices as unknown, not 0 ──


def test_estimate_total_tus_query_only_config_marks_count_unknown(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        'build:\n  query: "cmake --build . --target compile_commands"\n'
    )
    req = ScanRequest(binaries=[Path("lib.so")], mode="audit", build_config=config_path)
    total, note = _estimate_total_tus(req)
    assert total == 0
    assert "UNKNOWN" in note
    assert "build.query" in note


def test_estimate_total_tus_config_without_query_is_unaffected(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("build:\n  system: cmake\n")
    req = ScanRequest(binaries=[Path("lib.so")], mode="audit", build_config=config_path)
    total, note = _estimate_total_tus(req)
    assert total == 0
    assert "UNKNOWN" not in note
    assert note == "no source tree / compile DB"


def test_render_scan_dry_run_wires_build_config_and_shows_unknown_not_zero(
    tmp_path: Path,
) -> None:
    """End-to-end: the single-binary CLI dry-run path must actually reach
    ``_estimate_total_tus``'s query-only branch (Codex review: an earlier
    revision of this fix left ``render_scan_dry_run``'s own internal
    ``ScanRequest`` without ``build_config`` at all, so the branch was
    unreachable from ``scan --dry-run`` itself), and the rendered preview
    must not report a numeric "0 TU(s), ~0.00s" for a count that is
    genuinely unknown rather than counted as zero.

    ``render_scan_dry_run`` itself takes ``estimates``/``estimate_error`` as
    already-computed data (it must not import ``service_scan`` -- see its
    own docstring), so this test computes them exactly the way
    ``cli_scan.py``'s ``scan_cmd`` does before calling it, to genuinely
    exercise the query-only ``_estimate_total_tus`` branch end to end.
    """
    from abicheck.service_scan import Budget, estimate_scan

    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        'build:\n  query: "cmake --build . --target compile_commands"\n'
    )
    estimate_req = ScanRequest(
        binaries=[tmp_path / "lib.so"],
        mode="pr",
        source_method=SourceMethod.S1.value,
        depth=EvidenceDepth.BUILD.value,
        budget=Budget(total_timeout=None),
        build_config=config_path,
    )
    estimates = estimate_scan(
        estimate_req, resolved_level=(SourceMethod.S1, EvidenceDepth.BUILD)
    )
    result = render_scan_dry_run(
        **_dry_run_kwargs(
            tmp_path,
            depth="build",
            eff_depth_enum=EvidenceDepth.BUILD,
            resolved=SourceMethod.S1,
            collect_mode="build",
            estimates=estimates,
        )
    )
    lines = result.sections.get("Resolved depth and source scope", [])
    l3_lines = [ln for ln in lines if ln.startswith("L3_build:")]
    assert l3_lines == [
        "L3_build: TU count/cost unknown -- build.query: .abicheck.yml "
        "[UNKNOWN: query-only build.query, real run's trusted query "
        "determines the actual count]"
    ]
    assert any("understating it" in ln for ln in lines)


def test_estimate_total_tus_no_build_config_is_unaffected() -> None:
    req = ScanRequest(binaries=[Path("lib.so")], mode="audit")
    total, note = _estimate_total_tus(req)
    assert total == 0
    assert "UNKNOWN" not in note
