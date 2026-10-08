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

"""Contract tests for the dump's binary-format seam (lane B, stage B1b).

``workflows.dump.native._run_dump_uncached`` dispatches each format's primary
extraction through ``workflows.dump.formats``. These tests pin the seam
itself: which adapter a format reaches, what request it receives, how
overrides stack, and that every real adapter forwards the full parameter set
of the extractor it wraps.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from abicheck.compile_context import CompileContext
from abicheck.errors import UnsupportedArtifactError
from abicheck.model import AbiSnapshot
from abicheck.service import run_dump
from abicheck.workflows.dump import macho, native, pe
from abicheck.workflows.dump.formats import (
    DEFAULT_ADAPTERS,
    BinaryFormatAdapter,
    NativeExtractRequest,
)
from abicheck.workflows.dump.native import FORMAT_ADAPTERS
from tests._dump_format_fakes import fake_format_adapter

FORMATS = ("elf", "pe", "macho")


def _binary(tmp_path: Path, name: str = "libx.so") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\0" * 16)
    return p


@pytest.mark.parametrize("fmt", FORMATS)
def test_run_dump_reaches_only_the_detected_formats_adapter(
    tmp_path: Path, fmt: str
) -> None:
    snap = AbiSnapshot(library="libx", version="1.0")
    with ExitStack() as stack:
        fakes = {f: stack.enter_context(fake_format_adapter(f, snap)) for f in FORMATS}
        result = run_dump(_binary(tmp_path), fmt, version="1.0")
    assert result.library == "libx"
    assert [f for f, fake in fakes.items() if fake.called] == [fmt]
    assert len(fakes[fmt].requests) == 1


@pytest.mark.parametrize("fmt", FORMATS)
def test_request_carries_the_callers_arguments(tmp_path: Path, fmt: str) -> None:
    path = _binary(tmp_path)
    inc = tmp_path / "inc"
    inc.mkdir()
    pdb = tmp_path / "x.pdb"
    snap = AbiSnapshot(library="libx", version="2.0")
    with fake_format_adapter(fmt, snap) as fake:
        run_dump(
            path,
            fmt,
            includes=[inc],
            version="2.0",
            lang="c",
            lang_explicit=True,
            pdb_path=pdb,
            symbols_only=True,
        )
    req = fake.last
    assert (req.path, req.version, req.lang) == (path, "2.0", "c")
    assert req.headers == []
    assert req.includes == [inc]
    assert req.lang_explicit is True
    assert req.pdb_path == pdb
    assert req.symbols_only is True
    # No explicit -I list given: the caller's includes stand in for it.
    assert req.public_include_search_dirs == [inc]


@pytest.mark.parametrize(
    ("header_backend", "frontend", "expected"),
    [
        ("castxml", None, "castxml"),
        ("castxml", "clang", "clang"),
        ("clang", "AUTO", "clang"),
        ("auto", "auto", "auto"),
    ],
)
def test_request_header_backend_is_the_effective_backend(
    tmp_path: Path, header_backend: str, frontend: str | None, expected: str
) -> None:
    """An explicit, non-"auto" ``compile.frontend`` (any case) wins."""
    compile_ctx = CompileContext(frontend=frontend) if frontend else None
    with fake_format_adapter("pe", AbiSnapshot(library="l", version="1")) as fake:
        run_dump(
            _binary(tmp_path, "x.dll"),
            "pe",
            header_backend=header_backend,
            compile=compile_ctx,
        )
    assert fake.last.header_backend == expected


def test_unknown_format_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedArtifactError, match="wasm"):
        run_dump(_binary(tmp_path), "wasm")


def test_registry_entry_can_add_a_format(tmp_path: Path) -> None:
    """A format with a registered adapter is dumped through it; the fake
    helper removes the entry again afterwards."""
    snap = AbiSnapshot(library="libw", version="1")
    with fake_format_adapter("wasm", snap) as fake:
        assert run_dump(_binary(tmp_path), "wasm").library == "libw"
    assert fake.called
    assert "wasm" not in FORMAT_ADAPTERS
    with pytest.raises(UnsupportedArtifactError):
        run_dump(_binary(tmp_path), "wasm")


def test_fake_helper_restores_the_real_adapter_when_the_block_raises() -> None:
    real = FORMAT_ADAPTERS["pe"]
    with pytest.raises(RuntimeError):
        with fake_format_adapter("pe"):
            raise RuntimeError
    assert FORMAT_ADAPTERS["pe"] is real


def test_registry_is_exactly_the_three_native_formats() -> None:
    assert sorted(FORMAT_ADAPTERS) == ["elf", "macho", "pe"]
    for fmt in ("pe", "macho"):
        assert FORMAT_ADAPTERS[fmt] is DEFAULT_ADAPTERS[fmt]


# ── Real adapters forward their extractor's full parameter set ──────────────

_REAL = [
    ("elf", native, "extract_elf"),
    ("pe", pe, "extract_pe"),
    ("macho", macho, "extract_macho"),
]


@pytest.mark.parametrize(("fmt", "owner", "name"), _REAL)
def test_real_adapter_is_registered_and_satisfies_the_protocol(
    fmt: str, owner: object, name: str
) -> None:
    adapter = FORMAT_ADAPTERS[fmt]
    assert isinstance(adapter, BinaryFormatAdapter)
    assert adapter.format == fmt


@pytest.mark.parametrize(("fmt", "owner", "name"), _REAL)
def test_real_adapter_forwards_every_extractor_parameter(
    monkeypatch: pytest.MonkeyPatch, fmt: str, owner: object, name: str
) -> None:
    """Oracle: the extractor's own signature. Every parameter it accepts
    must arrive, bound to the request field of the same name."""
    extractor = getattr(owner, name)
    params = list(inspect.signature(extractor).parameters)
    # One distinct sentinel per request field, so a swapped pair shows up.
    values: dict[str, Any] = {
        f: object() for f in NativeExtractRequest.__dataclass_fields__
    }
    request = NativeExtractRequest(**values)
    received: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any) -> AbiSnapshot:
        bound = inspect.signature(extractor).bind(*args, **kwargs)
        received.update(bound.arguments)
        return AbiSnapshot(library="l", version="1")

    monkeypatch.setattr(owner, name, capture)
    FORMAT_ADAPTERS[fmt].extract(request)
    assert sorted(received) == sorted(params)
    for param in params:
        assert received[param] is values[param], param
