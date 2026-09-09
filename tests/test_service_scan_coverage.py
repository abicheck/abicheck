# Copyright 2026 Nikolay Petrov
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

"""Coverage-closing unit tests for :mod:`abicheck.service_scan`.

Targets the error/fallback branches the existing ``test_scan_estimate.py``
happy-path cases don't reach: header-input edge cases and the compile-DB /
source-tree / pack TU counters' failure paths. Default lane — no compiler.

ADR-068 Phase 4 retired this module's request/result types along with
``run_scan``/``run_scan_set`` and their killable subprocess harness, so the
``_scan_subprocess_worker``/``_kill_process_tree``/``_descendant_pgids`` cases
that used to live here went with the code they covered; what remains is the
dry-run cost model's own error handling.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from abicheck.api_types import InputSpec
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import ValidationError
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json
from abicheck.service_scan import (
    _count_compile_db_tus,
    _count_pack_tus,
    _count_source_tus,
    estimate_scan,
    expand_header_inputs,
)

# The _kill_process_tree group-termination logic is POSIX-only: it relies on
# os.getpgid/os.getpgrp/os.killpg and signal.SIGKILL, none of which exist on
# Windows (there the production code hits AttributeError and degrades to a plain
# terminate()). These tests assert the POSIX escalation path, so skip them off
# POSIX rather than forcing an unreachable branch on Windows.
_posix_process_groups = pytest.mark.skipif(
    not hasattr(os, "getpgid"),
    reason="POSIX process-group termination (os.getpgid/killpg, signal.SIGKILL) is POSIX-only",
)


@pytest.fixture
def snap_path(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
    )
    p = tmp_path / "new.abi.json"
    p.write_text(snapshot_to_json(snap), encoding="utf-8")
    return p


# ── expand_header_inputs: the "exists but not a file/dir" branch (line 82) ────


def test_expand_header_inputs_rejects_non_file_non_dir(tmp_path: Path) -> None:
    # A FIFO exists but is neither a regular file nor a directory, so it falls
    # through to the final guard rather than being accepted as a header.
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("os.mkfifo unavailable on this platform")
    with pytest.raises(ValidationError, match="neither file nor directory"):
        expand_header_inputs([fifo])


# ── _count_compile_db_tus: the malformed-input branches (290/291/293/304) ─────


def test_count_compile_db_tus_invalid_json_returns_zero(tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("{ this is not valid json ", encoding="utf-8")
    assert _count_compile_db_tus(cdb) == 0


def test_count_compile_db_tus_missing_file_returns_zero(tmp_path: Path) -> None:
    # OSError read failure is folded into the same zero-return guard.
    assert _count_compile_db_tus(tmp_path / "does-not-exist.json") == 0


def test_count_compile_db_tus_non_list_returns_zero(tmp_path: Path) -> None:
    # A well-formed JSON object (not the expected array) is not a compile DB.
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps({"file": "a.cpp"}), encoding="utf-8")
    assert _count_compile_db_tus(cdb) == 0


def test_count_compile_db_tus_skips_bad_entries(tmp_path: Path) -> None:
    # Non-dict entries and dicts without a truthy `file` key are skipped; only the
    # one valid entry counts.
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                "not-a-dict",
                {"directory": "/x"},  # no `file`
                {"file": ""},  # empty `file` is falsy
                {"file": "real.cpp", "directory": "/x"},
            ]
        ),
        encoding="utf-8",
    )
    assert _count_compile_db_tus(cdb) == 1


# ── _count_source_tus: file vs. directory counting (lines 339-345) ────────────


def test_count_source_tus_single_source_file(tmp_path: Path) -> None:
    src = tmp_path / "one.cpp"
    src.write_text("int one(){return 0;}\n", encoding="utf-8")
    assert _count_source_tus(src) == 1


def test_count_source_tus_single_non_source_file(tmp_path: Path) -> None:
    doc = tmp_path / "readme.txt"
    doc.write_text("hello\n", encoding="utf-8")
    assert _count_source_tus(doc) == 0


def test_count_source_tus_directory_recursion(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("//\n", encoding="utf-8")
    (tmp_path / "b.c").write_text("//\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.mm").write_text("//\n", encoding="utf-8")
    (sub / "notes.md").write_text("//\n", encoding="utf-8")  # ignored
    assert _count_source_tus(tmp_path) == 3


def test_estimate_counts_source_tree_without_compile_db(
    snap_path: Path, tmp_path: Path
) -> None:
    # A --sources tree with no compile DB (and no pack/Bazel build info) falls
    # through to the counted-source-files provenance in _estimate_total_tus.
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "x.cpp").write_text("//\n", encoding="utf-8")
    (tree / "y.cpp").write_text("//\n", encoding="utf-8")
    est = estimate_scan(InputSpec.of(snap_path, sources=tree), mode="baseline")
    l3 = next(e for e in est if e.layer == "L3_build")
    assert l3.tus == 2
    assert l3.note == "counted source files (no compile DB)"


# ── _count_pack_tus: the best-effort guard swallows a bad pack (403/404) ───────


def test_count_pack_tus_non_directory_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "plain.txt"
    f.write_text("x\n", encoding="utf-8")
    assert _count_pack_tus(f) is None


def test_count_pack_tus_swallows_load_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory that is_pack_dir accepts but whose load blows up must not raise
    # mid-estimate: the guard returns None so the caller falls back to other
    # counters.
    pack = tmp_path / "pack"
    pack.mkdir()

    def _boom(_p: Path) -> bool:
        raise RuntimeError("corrupt manifest")

    monkeypatch.setattr("abicheck.buildsource.inline.is_pack_dir", _boom)
    assert _count_pack_tus(pack) is None


def test_count_pack_tus_not_a_pack_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = tmp_path / "buildtree"
    plain.mkdir()
    monkeypatch.setattr("abicheck.buildsource.inline.is_pack_dir", lambda _p: False)
    assert _count_pack_tus(plain) is None


# ── _layers_from_coverage: defensive int coercion (lines 700-710) ─────────────


class TestServiceScanDefinesNoRequestOrResultType:
    """ADR-068 Phase 4's definition of done, as an executable check.

    `CompareRequest` -> `CompareResult` is the one typed request/result
    contract. This module's own `ScanRequest`/`ScanResult`/`ScanArtifactResult`/
    `ScanSetResult`/`Budget`/`LayerResult` are deleted, and so are the entry
    points that consumed them. Stated structurally -- over every public name
    the module and the `abicheck.service` facade actually expose -- rather than
    as a list of the six names that happened to exist, so a *new* scan-shaped
    request or result type fails here too.
    """

    def _public_names(self, module) -> set[str]:
        return {n for n in dir(module) if not n.startswith("_")}

    def test_no_request_or_result_dataclass_is_defined(self) -> None:
        import dataclasses
        import inspect

        import abicheck.service_scan as service_scan

        offenders = sorted(
            name
            for name in self._public_names(service_scan)
            if inspect.isclass(getattr(service_scan, name))
            and getattr(service_scan, name).__module__ == service_scan.__name__
            and dataclasses.is_dataclass(getattr(service_scan, name))
            and name.endswith(("Request", "Result"))
        )
        assert offenders == []

    def test_the_retired_names_do_not_resolve_on_either_surface(self) -> None:
        import abicheck.service as service
        import abicheck.service_scan as service_scan

        retired = {
            "ScanRequest",
            "ScanResult",
            "ScanArtifactResult",
            "ScanSetResult",
            "Budget",
            "LayerResult",
            "run_scan",
            "run_audit",
            "run_scan_set",
            "run_scan_subprocess",
            "run_scan_set_subprocess",
        }
        assert not retired & self._public_names(service_scan)
        assert not retired & self._public_names(service)
        assert not retired & set(service.__all__)

    def test_the_cost_model_is_what_remains(self) -> None:
        """The module is not merely emptied -- `estimate_scan` still ships,
        and now takes the canonical `InputSpec` instead of a request."""
        import inspect

        import abicheck.service_scan as service_scan

        params = inspect.signature(service_scan.estimate_scan).parameters
        first = next(iter(params.values()))
        assert first.annotation == "InputSpec"
        assert first.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

