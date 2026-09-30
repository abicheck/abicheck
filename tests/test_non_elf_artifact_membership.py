# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-062 A1.8 -- non-ELF artifact membership.

A PE/Mach-O/Python-visible/header-only library is a first-class package
member: it round-trips through the package format under its real
`ArtifactRef.kind`, appears in the package listing, and -- because
bundle-level resolution is an ELF-only capability -- is reported as an
explicit, named "resolution not applicable" fact rather than silently
dropped. Parametrized over every non-ELF kind (the bug class is "a producer
or resolver that assumes ELF"), not only the one the plan names.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.frontends.cli.commands.compare_bundle_facts_scope import (
    json_scope_fields,
    scope_terms_for,
)
from abicheck.model.bundle_facts import BundleFacts
from abicheck.model.python_facts import PythonApiSurface
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_store import (
    DirectoryObjectStore,
    read_project_manifest,
    write_project_manifest,
)
from abicheck.serialization import snapshot_to_dict
from abicheck.storage.bundle_facts_package import (
    read_bundle_facts_package,
    write_bundle_facts_package,
)
from abicheck.storage.import_v1 import artifact_kind_for_document
from abicheck.storage.package import InMemoryObjectStore
from abicheck.workflows.bundle_facts_capture import (
    artifact_kind_of_snapshot,
    bundle_snapshot_from_facts,
    capture_bundle_facts,
)
from abicheck.workflows.bundle_facts_compare import compare_bundle_from_facts


def _non_elf_snapshot(kind: str, name: str) -> AbiSnapshot:
    if kind == "pe":
        return AbiSnapshot(library=name, version="1", platform="pe")
    if kind == "macho":
        return AbiSnapshot(library=name, version="1", platform="macho")
    if kind == "python":
        return AbiSnapshot(
            library=name, version="1", python_api=PythonApiSurface(module_name="m")
        )
    if kind == "header_only":
        return AbiSnapshot(library=name, version="1", header_only=True)
    raise AssertionError(kind)


NON_ELF_KINDS = ("pe", "macho", "python", "header_only")


def _elf_snapshot() -> AbiSnapshot:
    meta = ElfMetadata(
        soname="libcore.so.1",
        needed=["libc.so.6"],
        symbols=[ElfSymbol(name="core_add", visibility="default")],
        imports=[ElfImport(name="malloc")],
    )
    return AbiSnapshot(library="libcore.so", version="1", platform="elf", elf=meta)


def _mixed_facts(*kinds: str) -> BundleFacts:
    snaps = {"libcore.so": _elf_snapshot()}
    for kind in kinds:
        snaps[f"member-{kind}"] = _non_elf_snapshot(kind, f"member-{kind}")
    return capture_bundle_facts(snaps)


@pytest.mark.parametrize("kind", NON_ELF_KINDS)
def test_package_records_real_kind_and_round_trips(kind: str) -> None:
    facts = _mixed_facts(kind)
    store = InMemoryObjectStore()
    manifest = write_bundle_facts_package(facts, store=store)

    kinds = {a.native_identity["library_name"]: a.kind for a in manifest.artifact_refs}
    assert kinds == {"libcore.so": "elf", f"member-{kind}": kind}

    round_tripped = read_bundle_facts_package(manifest, store=store)
    assert sorted(round_tripped.per_library_snapshots) == sorted(
        facts.per_library_snapshots
    )
    member = round_tripped.per_library_snapshots[f"member-{kind}"]
    assert artifact_kind_of_snapshot(member) == kind


def test_all_non_elf_kinds_listed_through_directory_store(tmp_path: Path) -> None:
    facts = _mixed_facts(*NON_ELF_KINDS)
    manifest = write_bundle_facts_package(facts, store=DirectoryObjectStore(tmp_path))
    write_project_manifest(tmp_path, manifest)

    listed = read_project_manifest(tmp_path)
    kinds = {a.native_identity["library_name"]: a.kind for a in listed.artifact_refs}
    assert kinds == {"libcore.so": "elf"} | {f"member-{k}": k for k in NON_ELF_KINDS}
    back = read_bundle_facts_package(listed, store=DirectoryObjectStore(tmp_path))
    assert set(back.per_library_snapshots) == set(kinds)


def test_header_only_member_without_binary_section_reads_back() -> None:
    import dataclasses

    facts = _mixed_facts("header_only")
    store = InMemoryObjectStore()
    manifest = write_bundle_facts_package(facts, store=store)
    refs = tuple(
        dataclasses.replace(
            a, sections={k: v for k, v in a.sections.items() if k != "binary"}
        )
        if a.kind == "header_only"
        else a
        for a in manifest.artifact_refs
    )
    stripped = dataclasses.replace(manifest, artifact_refs=refs)
    back = read_bundle_facts_package(stripped, store=store)
    assert back.per_library_snapshots["member-header_only"].header_only is True


@pytest.mark.parametrize(
    "kinds",
    [(k,) for k in NON_ELF_KINDS] + [NON_ELF_KINDS],
    ids=lambda ks: "+".join(ks),
)
def test_resolution_runs_on_elf_and_names_non_elf_members(
    kinds: tuple[str, ...],
) -> None:
    baseline = bundle_snapshot_from_facts(
        capture_bundle_facts({"libcore.so": _elf_snapshot()})
    )
    mixed = bundle_snapshot_from_facts(_mixed_facts(*kinds))

    # The ELF member's resolution is unchanged by non-ELF membership.
    assert mixed.resolution == baseline.resolution
    assert set(mixed.metadata) == {"libcore.so"}
    # ...and every non-ELF member is a named fact, not a silent gap.
    assert mixed.resolution_not_applicable == {f"member-{k}": k for k in kinds}
    assert baseline.resolution_not_applicable == {}


def test_comparison_result_and_report_carry_the_named_fact() -> None:
    old_facts = _mixed_facts(*NON_ELF_KINDS)
    new_snapshot = bundle_snapshot_from_facts(_mixed_facts("pe"))
    result = compare_bundle_from_facts(old_facts, new_snapshot, [])

    expected = {f"member-{k}": k for k in NON_ELF_KINDS}
    assert result.resolution_not_applicable_members == expected

    fields = json_scope_fields(scope_terms_for(result, {}), {}, result)
    assert fields["resolution_not_applicable_members"] == {
        name: {"artifact_kind": kind, "resolution": "not_applicable"}
        for name, kind in sorted(expected.items())
    }


def test_pure_elf_report_has_no_not_applicable_key() -> None:
    facts = capture_bundle_facts({"libcore.so": _elf_snapshot()})
    result = compare_bundle_from_facts(facts, bundle_snapshot_from_facts(facts), [])
    assert result.resolution_not_applicable_members == {}
    fields = json_scope_fields(scope_terms_for(result, {}), {}, result)
    assert "resolution_not_applicable_members" not in fields


def _oracle(header_only: bool, platform: str | None, python: bool) -> str:
    # Independent statement of the documented precedence table.
    table = [
        (header_only, "header_only"),
        (platform is not None, platform or ""),
        (python, "python"),
    ]
    return next((kind for hit, kind in table if hit), "elf")


@pytest.mark.parametrize(
    ("header_only", "platform", "python"),
    list(itertools.product([False, True], [None, "elf", "pe", "macho"], [False, True])),
)
def test_kind_rule_agrees_for_documents_and_live_snapshots(
    header_only: bool, platform: str | None, python: bool
) -> None:
    snap = AbiSnapshot(
        library="x",
        version="1",
        platform=platform,
        header_only=header_only,
        python_api=PythonApiSurface(module_name="m") if python else None,
    )
    expected = _oracle(header_only, platform, python)
    assert artifact_kind_of_snapshot(snap) == expected
    assert artifact_kind_for_document(snapshot_to_dict(snap)) == expected
