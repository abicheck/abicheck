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

"""H7 catalogue: historical-bug mutants replayed as real source patches.

Plan: ``docs/contribute/plans/defect-family-harnesses.md`` section H7. Each
entry names a unified diff under ``tests/regressions/mutants/<family>/`` that
puts one historical bug of that defect family back into ``abicheck/``, and the
harness node ids that must fail once it is applied. Unlike the in-process
monkeypatch mutants inside each harness, a patch changes the *source*, so it
also proves the harness reaches the real call site (no alias the monkeypatch
missed) -- and ``git apply --check`` fails loudly when a refactor moves the
code out from under it, which is the signal to refresh the patch.

Also holds the copy/apply/run machinery ``tests/test_family_f7_mutant_replay.py``
drives, kept here so the test module stays declarative.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTANTS_DIR = REPO_ROOT / "tests" / "regressions" / "mutants"

F1 = "tests/test_family_f1_evidence_ablation.py"
F2 = "tests/test_family_f2_route_parity.py"
F3 = "tests/test_family_f3_identity.py"
F5 = "tests/test_family_f5_optimization_reference.py"

#: Family -> the harness module that owns it. A family listed here must carry
#: at least two mutants (plan H7: "one per historical bug in its family").
HARNESSES: dict[str, str] = {"F1": F1, "F2": F2, "F3": F3, "F5": F5}


@dataclass(frozen=True)
class Mutant:
    name: str
    family: str
    prs: tuple[int, ...]
    patch: str  # relative to MUTANTS_DIR
    must_fail: tuple[str, ...]  # harness node ids (function level)
    summary: str
    #: Non-empty when the harness is known NOT to catch this mutant: a real
    #: harness gap, recorded instead of hidden (slow test is strict-xfail).
    harness_gap: str = ""

    @property
    def patch_path(self) -> Path:
        return MUTANTS_DIR / self.patch


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "pre_1384_unknown_export_as_absent",
        "F1",
        (1384, 1385),
        "F1/pre_1384_unknown_export_as_absent.patch",
        (f"{F1}::test_fact_ablation_oracles",),
        "surface_facts.binary_exported reads a not-collected export fact as present(False).",
    ),
    Mutant(
        "pre_1033_fact_collapse",
        "F1",
        (1033,),
        "F1/pre_1033_fact_collapse.patch",
        (f"{F1}::test_fact_ablation_oracles",),
        "bridge_legacy_and_fact turns a non-PRESENT explicit Fact into present(default).",
    ),
    Mutant(
        "pre_1391_release_member_drops_suppress",
        "F2",
        (1391,),
        "F2/pre_1391_release_member_drops_suppress.patch",
        (f"{F2}::test_route_parity",),
        "The release fan-out's per-member run_compare call drops the stated --suppress.",
    ),
    Mutant(
        "pre_1258_typed_api_scope_public_default",
        "F2",
        (1258, 1264),
        "F2/pre_1258_typed_api_scope_public_default.patch",
        (f"{F2}::test_shared_defaults_agree", f"{F2}::test_route_parity"),
        "CompareRequest.scope_public defaults differently from the CLI.",
    ),
    Mutant(
        "pre_1140_macho_underscore_not_stripped",
        "F3",
        (1140, 1156),
        "F3/pre_1140_macho_underscore_not_stripped.patch",
        (f"{F3}::test_macho_underscore_codec_joins_and_separates",),
        "declaration identity keeps Mach-O's extra leading underscore (__Z vs _Z).",
    ),
    Mutant(
        "pre_1355_checkout_path_in_signature_key",
        "F3",
        (1355, 1383),
        "F3/pre_1355_checkout_path_in_signature_key.patch",
        (f"{F3}::test_identity_key_is_invariant_under_path_transform",),
        "signature_key hashes the raw '(lambda at /abs/...)' spelling.",
    ),
    Mutant(
        "pre_1370_missing_c3_ctor_variant",
        "F3",
        (1370,),
        "F3/pre_1370_missing_c3_ctor_variant.patch",
        (f"{F3}::test_itanium_ctor_dtor_variant_codec_joins_and_separates",),
        "The C3 (allocating) constructor variant is not joined to its family.",
    ),
    Mutant(
        "cache_key_drops_binary_content",
        "F5",
        (1340, 1306),
        "F5/cache_key_drops_binary_content.patch",
        (f"{F5}::test_disk_cache_cold_warm_and_fresh_root_agree",),
        "snapshot_cache._cache_key stops hashing the binary, so two binaries share a warm entry.",
        harness_gap=(
            "H5's disk-cache cell never rebuilds a binary in place: its two operands "
            "also differ in the side's version label, which the key still hashes, so "
            "a key that ignores binary content keys them apart anyway. A "
            "same-path/same-label/new-content transform is missing."
        ),
    ),
    Mutant(
        "cache_key_drops_version",
        "F5",
        (1340, 1306),
        "F5/cache_key_drops_version.patch",
        (f"{F5}::test_disk_cache_cold_warm_and_fresh_root_agree",),
        "snapshot_cache._cache_key drops the version input, so a warm entry serves the other side.",
        harness_gap=(
            "Same redundancy as cache_key_drops_binary_content, the other way round: "
            "the two operands' contents differ, so a key missing the version label "
            "still keys them apart. No H5 cell varies exactly one key input."
        ),
    ),
    Mutant(
        "cache_key_drops_side_identity",
        "F5",
        (1340, 1306),
        "F5/cache_key_drops_side_identity.patch",
        (f"{F5}::test_disk_cache_cold_warm_and_fresh_root_agree",),
        "snapshot_cache._cache_key hashes neither the binary nor the version: both sides share one warm entry.",
    ),
    Mutant(
        "pre_1336_threaded_release_drops_member",
        "F5",
        (1336,),
        "F5/pre_1336_threaded_release_drops_member.patch",
        (f"{F5}::test_release_threads_1_matches_threads_8",),
        "The threaded release fan-out drops its last member whenever more than one thread is allowed.",
    ),
    Mutant(
        "release_dispatch_drops_member",
        "F5",
        (1336,),
        "F5/release_dispatch_drops_member.patch",
        (f"{F5}::test_release_threads_1_matches_threads_8",),
        "The pooled release dispatch drops its last member unconditionally.",
        harness_gap=(
            "H5's threads=1 reference arm still reaches _compare_release_parallel "
            "(ABICHECK_MAX_THREADS=1 leaves the release plan's pool_size at 2 for an "
            "8-member release), so both arms share the mutated dispatch and agree; "
            "no H5 cell compares the pooled path against _compare_release_sequential."
        ),
    ),
)

MUTANTS_BY_NAME = {m.name: m for m in MUTANTS}

# ── replay machinery ────────────────────────────────────────────────────────

_COPY_TOP = (
    "abicheck",
    "tests",
    "scripts",
    "examples",
    "catalog",
    "pyproject.toml",
    "conftest.py",
)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache")


def git() -> str:
    exe = shutil.which("git")
    if exe is None:  # pragma: no cover - environment guard
        raise RuntimeError("git not found on PATH")
    return exe


def apply_patch(
    root: Path, patch: Path, *, check_only: bool, reverse: bool = False
) -> subprocess.CompletedProcess[str]:
    argv = [git(), "apply", "-p1"]
    if check_only:
        argv.append("--check")
    if reverse:
        argv.append("--reverse")
    argv.append(str(patch))
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)


def copy_repo(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in _COPY_TOP:
        src = REPO_ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=_IGNORE, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dest / name)
    return dest


def child_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def imported_abicheck_path(root: Path) -> Path:
    """Which ``abicheck`` a child process under :func:`child_env` imports --
    so a replay proves it ran the patched copy, not the editable install."""
    out = subprocess.run(
        [sys.executable, "-c", "import abicheck; print(abicheck.__file__)"],
        cwd=root,
        env=child_env(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip()).resolve()


_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def run_nodes(
    root: Path, node_ids: tuple[str, ...], basetemp: Path
) -> tuple[int, list[str], str]:
    """Run *node_ids* in a nested pytest session inside *root*. Returns the
    exit code, the failed node ids (from ``-rfE``), and the combined output.
    The nested session gets its own ``--basetemp`` (#1396: a nested session
    sharing the parent's temp root deletes it out from under the parent)."""
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-rfE",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "-o",
        "addopts=",
        f"--basetemp={basetemp}",
        "-m",
        "not integration and not libabigail and not abicc",
        *node_ids,
    ]
    proc = subprocess.run(
        argv, cwd=root, env=child_env(root), capture_output=True, text=True, check=False
    )
    output = proc.stdout + proc.stderr
    return proc.returncode, _FAILED_RE.findall(output), output
