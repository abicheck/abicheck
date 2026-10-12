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

"""Self-compare invariant for multi-library packages.

Bug class ``detector.diff_confirmation_precondition`` (self-compare
metamorphic invariant): comparing a
binary-only package against a byte-identical copy reported one
``bundle_intra_dep_signature_unverified`` per intra-package call edge
(75 on oneDAL), turning NO_CHANGE into COMPATIBLE_WITH_RISK. The thin
evidence level is a property each side has on its own, not a change.

The oracle here is independent of the implementation: ``compare(X, X)``
must produce zero bundle findings, for every package shape; and an actual
change to a provider must still surface the uncertainty finding.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.bundle import _compute_resolution_graph
from abicheck.bundle_analysis import analyze_bundle
from abicheck.bundle_models import BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change import LibraryMetadata

# Package shapes: library -> (exports, imports, needed). Every import is an
# intra-package edge, so each shape has at least one consumer/provider pair.
_SHAPES: dict[str, dict[str, tuple[list[str], list[str], list[str]]]] = {
    "single-edge": {
        "libcore.so": (["core_fn"], [], []),
        "libuse.so": (["use_fn"], ["core_fn"], ["libcore.so"]),
    },
    "chain": {
        "liba.so": (["a_fn"], [], []),
        "libb.so": (["b_fn"], ["a_fn"], ["liba.so"]),
        "libc.so": (["c_fn"], ["b_fn"], ["libb.so"]),
    },
    "diamond": {
        "libbase.so": (["base_fn"], [], []),
        "libl.so": (["l_fn"], ["base_fn"], ["libbase.so"]),
        "libr.so": (["r_fn"], ["base_fn"], ["libbase.so"]),
        "libtop.so": (["top_fn"], ["l_fn", "r_fn"], ["libl.so", "libr.so"]),
    },
    "oneDAL-like": {
        "libonedal_core.so": (["core_fn", "dup_fn"], [], []),
        "libonedal.so": (["onedal_fn", "dup_fn"], ["core_fn"], ["libonedal_core.so"]),
        "libonedal_thread.so": (
            ["thr_fn"],
            ["core_fn", "onedal_fn"],
            ["libonedal_core.so", "libonedal.so"],
        ),
        "libonedal_parameters.so": (["par_fn"], ["thr_fn"], ["libonedal_thread.so"]),
    },
}


def _meta(exports, imports, needed, *, code_hash: str = "") -> ElfMetadata:
    return ElfMetadata(
        soname="",
        needed=list(needed),
        symbols=[
            ElfSymbol(name=n, visibility="default", code_hash=code_hash)
            for n in exports
        ],
        imports=[ElfImport(name=n) for n in imports],
    )


def _bundle(shape, *, code_hash: str = "") -> BundleSnapshot:
    metas = {
        lib: _meta(*spec, code_hash=code_hash and f"{code_hash}-{lib}")
        for lib, spec in shape.items()
    }
    libs = {name: Path(f"/fake/{name}") for name in metas}
    return BundleSnapshot(
        root=Path("/fake"),
        libraries=libs,
        metadata=metas,
        resolution=_compute_resolution_graph(libs, metas),
    )


def _evidence(shape) -> dict[str, AbiSnapshot]:
    # Binary-only: ELF_ONLY functions, elf_only_mode -- insufficient type
    # evidence on both sides, the exact state that used to fire.
    return {
        lib: AbiSnapshot(
            library=lib,
            version="1",
            functions=[
                Function(
                    name=n, mangled=n, return_type="?", visibility=Visibility.ELF_ONLY
                )
                for n in exports
            ],
            elf_only_mode=True,
        )
        for lib, (exports, _i, _n) in shape.items()
    }


def _results(shape, *, sha_old: str, sha_new: str) -> list[DiffResult]:
    def _lm(lib: str, sha: str) -> LibraryMetadata | None:
        return LibraryMetadata(path=lib, sha256=sha, size_bytes=1) if sha else None

    return [
        DiffResult(
            old_version="1",
            new_version="1",
            library=lib,
            changes=[],
            verdict=Verdict.NO_CHANGE,
            old_metadata=_lm(lib, sha_old and f"{sha_old}-{lib}"),
            new_metadata=_lm(lib, sha_new and f"{sha_new}-{lib}"),
        )
        for lib in shape
    ]


def _unverified(result) -> list:
    return [
        f
        for f in result.bundle_findings
        if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
    ]


# Whole-file sha256 identity is the identity signal; code hashes are not.
_IDENTITY = {
    "file-sha256": dict(sha=("s", "s"), hash=("", "")),
    "both": dict(sha=("s", "s"), hash=("h", "h")),
}


@pytest.mark.parametrize("shape_name", sorted(_SHAPES))
@pytest.mark.parametrize("identity", sorted(_IDENTITY))
def test_self_compare_is_clean(shape_name: str, identity: str) -> None:
    shape = _SHAPES[shape_name]
    sig = _IDENTITY[identity]
    old = _bundle(shape, code_hash=sig["hash"][0])
    new = _bundle(shape, code_hash=sig["hash"][1])
    result = analyze_bundle(
        old,
        new,
        _results(shape, sha_old=sig["sha"][0], sha_new=sig["sha"][1]),
        old_signature_evidence=_evidence(shape),
        new_signature_evidence=_evidence(shape),
    )
    assert result.bundle_findings == []
    assert result.analysis_errors == []
    assert result.bundle_verdict == Verdict.NO_CHANGE


@pytest.mark.parametrize("shape_name", sorted(_SHAPES))
def test_changed_provider_still_reports_uncertainty(shape_name: str) -> None:
    # Sibling direction: the same thin evidence with a provider that DID
    # change (different sha256, different code hash) keeps the finding.
    shape = _SHAPES[shape_name]
    result = analyze_bundle(
        _bundle(shape, code_hash="old"),
        _bundle(shape, code_hash="new"),
        _results(shape, sha_old="a", sha_new="b"),
        old_signature_evidence=_evidence(shape),
        new_signature_evidence=_evidence(shape),
    )
    edges = {
        (consumer, imp)
        for consumer, (_e, imports, _n) in shape.items()
        for imp in imports
    }
    assert {(f.consumer_library, f.symbol) for f in _unverified(result)} == edges


def test_no_identity_signal_keeps_uncertainty() -> None:
    # Weaker evidence narrows conclusions: with neither sha256 nor code
    # hash, identical-looking snapshots cannot be proven unchanged.
    shape = _SHAPES["chain"]
    result = analyze_bundle(
        _bundle(shape),
        _bundle(shape),
        _results(shape, sha_old="", sha_new=""),
        old_signature_evidence=_evidence(shape),
        new_signature_evidence=_evidence(shape),
    )
    assert len(_unverified(result)) == 2


@pytest.mark.parametrize("shape_name", sorted(_SHAPES))
def test_equal_code_hash_alone_does_not_prove_signature(shape_name: str) -> None:
    # int f(int) -> int f(double) ignoring the argument can compile to the
    # same machine code: equal per-symbol code hashes without file identity
    # must keep the unverified finding.
    shape = _SHAPES[shape_name]
    result = analyze_bundle(
        _bundle(shape, code_hash="h"),
        _bundle(shape, code_hash="h"),
        _results(shape, sha_old="a", sha_new="b"),
        old_signature_evidence=_evidence(shape),
        new_signature_evidence=_evidence(shape),
    )
    edges = {
        (consumer, imp)
        for consumer, (_e, imports, _n) in shape.items()
        for imp in imports
    }
    assert {(f.consumer_library, f.symbol) for f in _unverified(result)} == edges


# ---------------------------------------------------------------------------
# Real ELF libraries through the CLI
# ---------------------------------------------------------------------------

_SOURCES = {
    "libonedal_core.so.1": (
        "int core_fn(int x){return x+1;}\nint dup_fn(void){return 1;}\n",
        [],
    ),
    "libonedal.so.1": (
        "extern int core_fn(int);\nint onedal_fn(int x){return core_fn(x)*2;}\n",
        ["libonedal_core.so.1"],
    ),
    "libonedal_thread.so.1": (
        "extern int core_fn(int); extern int onedal_fn(int);\n"
        "int thr_fn(int x){return core_fn(x)+onedal_fn(x);}\n",
        ["libonedal_core.so.1", "libonedal.so.1"],
    ),
    "libonedal_parameters.so.1": (
        "extern int thr_fn(int);\nint par_fn(int x){return thr_fn(x);}\n",
        ["libonedal_thread.so.1"],
    ),
}


def _build_package(root: Path, *, debug: bool) -> Path:
    lib = root / "lib"
    lib.mkdir(parents=True)
    for name, (src, deps) in _SOURCES.items():
        c = root / f"{name}.c"
        c.write_text(src)
        cmd = ["gcc", "-shared", "-fPIC", "-o", str(lib / name), str(c)]
        cmd += [f"-Wl,-soname,{name}", *(str(lib / d) for d in deps)]
        if debug:
            cmd.insert(1, "-g")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
    return root


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so with -soname")
@pytest.mark.parametrize("debug", [True, False], ids=["dwarf", "stripped-of-dwarf"])
def test_cli_self_compare_of_binary_only_package_is_no_change(
    tmp_path: Path, debug: bool
) -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    from click.testing import CliRunner

    from abicheck.cli import main

    old = _build_package(tmp_path / "old", debug=debug)
    new = tmp_path / "new"
    shutil.copytree(old, new)
    out = tmp_path / "r.md"
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"markdown={out}"]
    )
    assert res.exit_code == 0, res.output
    text = out.read_text()
    assert "bundle_intra_dep_signature_unverified" not in text
    assert "COMPATIBLE_WITH_RISK" not in text
    assert "(0 cross-library findings)" in text
