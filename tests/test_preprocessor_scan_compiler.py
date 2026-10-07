# SPDX-License-Identifier: Apache-2.0
"""The S2 preprocessor pre-scan runs the compiler the project configured.

Bug class: a setting the run resolves and one consumer never receives.
``.abicheck.yml``'s ``compile.compiler`` (the former ``--compiler`` /
``--compiler-prefix``) selects the compiler L4 source replay runs, but the
pre-scan ``compare()`` runs over the same side's compile units always shelled
out to a bare ``clang++``: with ``icpx`` or a cross prefix it probed with the
host compiler, or reported "clang++ not found" on a host that has only the
configured one. ``resolve_source_frontend_clang_bin``'s ``fallback="clang++"``
existed for exactly this caller, and nothing passed it once ``scan`` went.

Oracle: the selection rules as the option documents them, written out here --
a clang-family ``--compiler`` is used unless it is a CL-mode driver (the
pre-scan passes GNU-mode flags only); otherwise a ``--compiler-prefix`` whose
prefixed ``clang++`` is on PATH; otherwise ``clang++``. And agreement with L4:
wherever L4 got the configured compiler itself, the pre-scan gets it too.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.buildsource import embed as embed_mod
from abicheck.buildsource.source_inputs import SourceReadLicence
from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.service import InputSpec
from abicheck.service_compare_evidence import SideEvidence
from abicheck.workflows import pattern_preprocessor_scan as scan_mod
from abicheck.workflows.artifact.execute import embed_side_build_source

_ON_PATH_PREFIX = "aarch64-linux-gnu-"
_COMPILERS = (
    None,
    "/opt/intel/bin/icpx",
    "/usr/bin/clang-18",
    "/usr/bin/gcc",
    "/usr/bin/clang-cl",
    "/opt/intel/bin/dpcpp-cl",
)
_PREFIXES = (None, _ON_PATH_PREFIX, "missing-")
_CLANG_FAMILY_GNU = {"/opt/intel/bin/icpx", "/usr/bin/clang-18"}


def _expected(compiler: str | None, prefix: str | None) -> str:
    if compiler in _CLANG_FAMILY_GNU:
        return compiler
    if prefix == _ON_PATH_PREFIX:
        return f"{prefix}clang++"
    return "clang++"


@pytest.fixture
def stamped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import abicheck.dumper_clang as dumper_clang

    monkeypatch.setattr(
        dumper_clang.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name.startswith(_ON_PATH_PREFIX) else None,
    )
    l4: dict[str, str] = {}

    def fake_embed(snap, **kwargs):
        l4["clang_bin"] = kwargs["clang_bin"]

    monkeypatch.setattr(embed_mod, "embed_build_source", fake_embed)
    src = tmp_path / "src"
    src.mkdir()

    def run(compiler: str | None, prefix: str | None) -> tuple[AbiSnapshot, str]:
        snap = AbiSnapshot(library="lib", version="1.0")
        evidence = SideEvidence(
            headers=[],
            compile=CompileContext(gcc_path=compiler, gcc_prefix=prefix),
            collect_mode="source-target",
            dump_manifest=None,
        )
        embed_side_build_source(
            snap,
            InputSpec(path=tmp_path / "lib.so", sources=src),
            evidence,
            header_backend="auto",
            public_headers=[],
            public_header_dirs=[],
        )
        return snap, l4["clang_bin"]

    return run


@pytest.mark.parametrize(
    ("compiler", "prefix"), list(itertools.product(_COMPILERS, _PREFIXES))
)
def test_the_side_records_the_configured_preprocessor(
    stamped, compiler: str | None, prefix: str | None
) -> None:
    snap, l4_bin = stamped(compiler, prefix)
    assert snap.live_preprocessor_clang_bin == _expected(compiler, prefix)
    if l4_bin == compiler:
        assert compiler is not None
        cl_mode = compiler.endswith("-cl")
        assert (snap.live_preprocessor_clang_bin == compiler) is not cl_mode


@pytest.mark.parametrize("clang_bin", ["/opt/intel/bin/icpx", None])
def test_the_pre_scan_runs_the_recorded_preprocessor(
    monkeypatch: pytest.MonkeyPatch, clang_bin: str | None
) -> None:
    seen: dict[str, str] = {}

    def fake_collect(build, public_headers, *, clang_bin):
        seen["clang_bin"] = clang_bin
        return scan_mod.PreprocessorFactsResult(ran=False, skipped_reason="test")

    monkeypatch.setattr(scan_mod, "collect_preprocessor_facts", fake_collect)
    snap = AbiSnapshot(library="lib", version="1.0")
    snap.live_preprocessor_clang_bin = clang_bin
    live = SourceReadLicence.live_extraction()
    scan_mod._run_preprocessor_scan_for(snap, live, live)
    assert seen["clang_bin"] == (clang_bin or "clang++")


def test_a_stored_snapshot_never_carries_one(tmp_path: Path) -> None:
    snap = AbiSnapshot(library="lib", version="1.0")
    snap.live_preprocessor_clang_bin = "/opt/intel/bin/icpx"
    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    assert "live_preprocessor_clang_bin" not in path.read_text(encoding="utf-8")
    assert load_snapshot(path).live_preprocessor_clang_bin is None


def test_the_oracle_reaches_every_outcome() -> None:
    outcomes = {_expected(c, p) for c, p in itertools.product(_COMPILERS, _PREFIXES)}
    assert outcomes == {*_CLANG_FAMILY_GNU, f"{_ON_PATH_PREFIX}clang++", "clang++"}
