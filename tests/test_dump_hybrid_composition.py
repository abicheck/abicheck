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

"""The ``hybrid`` header backend composes two single-backend legs
(lane B, stage B1c): no re-entry into ``run_dump``, one castxml leg and one
clang leg of the same request, each pinned to its backend."""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from pathlib import Path

import pytest

from abicheck import service_dump_native
from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot
from abicheck.service import run_dump
from abicheck.workflows.dump.formats import NativeExtractRequest
from tests._dump_format_fakes import fake_format_adapter

FORMATS = ("elf", "pe", "macho")


def _binary(tmp_path: Path) -> Path:
    p = tmp_path / "libx.so"
    p.write_bytes(b"\0" * 16)
    return p


def _hybrid_dump(
    tmp_path: Path, fmt: str, **kwargs: object
) -> list[NativeExtractRequest]:
    header = tmp_path / "api.h"
    header.write_text("int f(void);\n")
    snap = AbiSnapshot(library="libx", version="1")
    with ExitStack() as stack:
        fake = stack.enter_context(fake_format_adapter(fmt, snap))
        run_dump(
            _binary(tmp_path), fmt, headers=[header], header_backend="hybrid", **kwargs
        )
    return fake.requests


@pytest.mark.parametrize("fmt", FORMATS)
def test_hybrid_runs_one_castxml_leg_then_one_clang_leg(
    tmp_path: Path, fmt: str
) -> None:
    requests = _hybrid_dump(tmp_path, fmt)
    assert [r.header_backend for r in requests] == ["castxml", "clang"]
    # The backend is pinned on the compile context too, which outranks the
    # bare header_backend argument, so a leg can never resolve to hybrid.
    assert [r.compile.frontend for r in requests if r.compile] == ["castxml", "clang"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_legs_differ_only_in_the_backend(tmp_path: Path, fmt: str) -> None:
    cc = CompileContext(frontend="auto", gcc_options="-DX=1")
    castxml, clang = _hybrid_dump(
        tmp_path, fmt, version="7", lang="c", lang_explicit=True, compile=cc
    )
    neutral = {"header_backend": "-", "compile": None}
    assert dataclasses.replace(castxml, **neutral) == dataclasses.replace(
        clang, **neutral
    )
    assert castxml.version == "7" and castxml.lang == "c" and castxml.lang_explicit
    # Every other compile-context field survives the pin.
    for leg in (castxml, clang):
        assert leg.compile is not None
        assert leg.compile.gcc_options == "-DX=1"


def test_hybrid_never_re_enters_the_orchestrator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recursive implementation entered ``_run_dump_uncached`` three
    times (outer call plus one per leg); the composition enters it once."""
    calls: list[str] = []
    real = service_dump_native._run_dump_uncached

    def counting(*args: object, **kwargs: object) -> AbiSnapshot:
        calls.append(str(kwargs.get("header_backend")))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service_dump_native, "_run_dump_uncached", counting)
    requests = _hybrid_dump(tmp_path, "elf")
    assert len(requests) == 2
    assert calls == ["hybrid"]
