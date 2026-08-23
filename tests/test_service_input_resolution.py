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

"""Regression coverage for ``service_input_resolution.py``'s
``resolve_side_snapshot``/``_resolve_side_snapshot_impl`` -- the per-input
primitive ``compare``'s implicit-dump operand and ``dump``'s typed
``DumpRequest``/``run_dump_request`` API share.

Split out as its own file (rather than extending
``tests/test_header_compile_context.py``, where the sibling
``public_include_search_dirs`` coverage already lives) because that file
sits at the AI-readiness 2000-line hard cap -- see this repo's own
``AGENTS.md`` "Files that are large" convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_resolve_side_snapshot_folds_compiler_option_include_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review, fresh evidence: an ``InputSpec.compile.gcc_option_
    tokens`` include-search operand (the typed-API equivalent of
    ``--compiler-option -I<dir>``) is exactly as explicit as
    ``side.includes``, but was never folded into
    ``public_include_search_dirs`` -- a directory reached only through such
    an operand stayed ``PRIVATE_HEADER`` even though the caller named it
    explicitly, just not via ``InputSpec.includes``."""
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_scan import CompileContext

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    explicit_dir = tmp_path / "explicit"
    compiler_option_dir = tmp_path / "compiler-option"

    captured: dict[str, object] = {}

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="lib", version="1.0", from_headers=False)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    cc = CompileContext(gcc_option_tokens=("-I", str(compiler_option_dir)))
    side = InputSpec(path=so, version="1.0", includes=(explicit_dir,), compile=cc)
    evidence = SideEvidence(
        headers=[], compile=None, collect_mode="off", dump_manifest=None
    )
    sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    passed = captured["public_include_search_dirs"]
    assert explicit_dir in passed
    assert compiler_option_dir in passed


def test_resolve_side_snapshot_suppresses_public_include_search_dirs_for_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review, fresh evidence: `side.compile`'s tokens are global,
    applied to every TU regardless of the manifest -- unconditionally
    folding them into `public_include_search_dirs` would collide with
    `dump()`'s own manifest mutual-exclusivity check, turning a previously-
    working `dump_manifest` + explicit compile-context combination into a
    usage error. Must be suppressed (`None`) whenever a manifest is given."""
    from abicheck import service_input_resolution as sir
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_scan import CompileContext

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF" + b"\x00" * 100)
    compiler_option_dir = tmp_path / "compiler-option"

    captured: dict[str, object] = {}

    def _fake_resolve_input(*args: object, **kwargs: object) -> AbiSnapshot:
        captured.update(kwargs)
        return AbiSnapshot(library="lib", version="1.0", from_headers=False)

    import abicheck.service as service_mod

    monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

    cc = CompileContext(gcc_option_tokens=("-I", str(compiler_option_dir)))
    side = InputSpec(path=so, version="1.0", compile=cc)
    manifest = object()  # only truthiness/identity matters to this call site
    evidence = SideEvidence(
        headers=[], compile=None, collect_mode="off", dump_manifest=manifest
    )
    sir.resolve_side_snapshot(
        side,
        evidence,
        lang="c++",
        header_backend="auto",
        fmt="elf",
        public_headers=[],
        public_header_dirs=[],
    )
    assert captured["public_include_search_dirs"] is None
