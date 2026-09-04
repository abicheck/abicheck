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

"""Unit test for :func:`abicheck.bundle_facts.compare_bundle_from_facts`'s
*old_signature_evidence* parameter (Codex review, PR #1060, round 6).

Split out of ``test_bundle_facts.py`` -- that file sits at its architecture
debt-no-growth line cap (CLAUDE.md "move responsibility instead of raising
the baseline"), and this is genuinely new coverage for a new parameter, not
a fixup to an existing test there. Mirrors that file's own
``TestCompareBundleFromFactsPhase4Parity`` fixture style (``_meta``,
``_snapshot``, ``_diff``) rather than importing its private test helpers
across files.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.bundle import _compute_resolution_graph
from abicheck.bundle_facts import capture_bundle_facts, compare_bundle_from_facts
from abicheck.bundle_models import BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility


def _meta(
    *,
    soname: str,
    exports: list[str] | None = None,
    needed: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    return ElfMetadata(
        soname=soname,
        needed=needed or [],
        symbols=[ElfSymbol(name=n, visibility="default") for n in exports or []],
        imports=[ElfImport(name=n) for n in imports or []],
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


def _diff(library: str, *, verdict: Verdict) -> DiffResult:
    return DiffResult(
        old_version="old", new_version="new", library=library, changes=[], verdict=verdict
    )


def _typed_function(version_marker: str) -> Function:
    # A fully typed (non-elf_only) declaration: real type evidence, with
    # both tri-state fields (is_variadic/contract_attributes) positively
    # determined rather than left at their "not captured" None default --
    # bundle_signature_evidence._symbol_evidence_sufficient() treats either
    # field left at None as insufficient evidence, the same as an ELF-only
    # declaration with no corroboration at all.
    return Function(
        name="core_fn",
        mangled="core_fn",
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_variadic=False,
        contract_attributes=[],
    )


def _elf_only_evidence(version: str) -> dict[str, AbiSnapshot]:
    # elf_only_mode=True, ELF_ONLY visibility -- no header evidence at all
    # for this symbol, so find_unverified_signature_findings() cannot
    # confirm or deny the signature actually agrees between old and new.
    fn = Function(
        name="core_fn", mangled="core_fn", return_type="?", visibility=Visibility.ELF_ONLY
    )
    return {
        "libcore.so": AbiSnapshot(
            library="libcore.so", version=version, functions=[fn], elf_only_mode=True
        )
    }


def _typed_evidence(version: str) -> dict[str, AbiSnapshot]:
    return {
        "libcore.so": AbiSnapshot(
            library="libcore.so", version=version, functions=[_typed_function(version)]
        )
    }


def test_old_signature_evidence_override_reaches_the_gate() -> None:
    """*old_signature_evidence*, when given, must override the default
    (*old_facts.per_library_snapshots*), the same way *new_signature_
    evidence* already overrides its own default (no evidence at all).
    Without a real override parameter, a caller that already
    depth-projected its own old-side evidence
    (``workflows.bundle_stored_pair_compare.compare_stored_bundle_facts_
    pair``) had no way to make the gate see anything but the raw,
    unprojected facts."""
    metadata = {
        "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
        "libconsumer.so": _meta(
            soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
        ),
    }
    new_snapshot = _snapshot(metadata)
    per_lib_results = [
        _diff("libcore.so", verdict=Verdict.NO_CHANGE),
        _diff("libconsumer.so", verdict=Verdict.NO_CHANGE),
    ]
    new_evidence = _typed_evidence("new")

    # The facts document's own per_library_snapshots also carries a fully
    # typed (non-elf_only) libcore.so, matching new_evidence -- the default
    # old-side evidence lets the gate confirm the signature agrees, so no
    # finding.
    facts_snapshots = {
        "libcore.so": AbiSnapshot(
            library="libcore.so",
            version="old",
            elf=metadata["libcore.so"],
            functions=[_typed_function("old")],
        ),
        "libconsumer.so": AbiSnapshot(
            library="libconsumer.so", version="old", elf=metadata["libconsumer.so"]
        ),
    }
    facts = capture_bundle_facts(facts_snapshots)

    baseline = compare_bundle_from_facts(
        facts, new_snapshot, per_lib_results, new_signature_evidence=new_evidence
    )
    assert not any(
        f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
        for f in baseline.bundle_findings
    )

    # An explicit old_signature_evidence override -- elf_only, no header
    # evidence -- must reach the gate and change the outcome even though
    # facts.per_library_snapshots itself (still fully typed) is untouched.
    overridden = compare_bundle_from_facts(
        facts,
        new_snapshot,
        per_lib_results,
        new_signature_evidence=new_evidence,
        old_signature_evidence=_elf_only_evidence("old"),
    )
    assert any(
        f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
        for f in overridden.bundle_findings
    )
