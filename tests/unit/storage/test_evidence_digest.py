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

"""ADR-062 D16: the `evidence` digest role.

Oracle: ``checker.compare`` itself, never the digest's own list of what it
sorts or drops. For every permutation of every unordered list, and for a
difference in each volatile capture field, ``compare`` reports no change
AND the evidence digest is equal; for a set of independent real changes
(semantically ordered lists among them) the evidence digest differs. The
`publication` digest keeps its meaning throughout.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import itertools
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, RecordType, Variable, Visibility
from abicheck.model.declarations import Param
from abicheck.model.elf_facts import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model.entities import EnumMember, EnumType, TypeField
from abicheck.storage.evidence_digest import (
    UNORDERED_LISTS,
    VOLATILE_CAPTURE_FIELDS,
    evidence_digest_of_document,
)
from abicheck.storage.package import ArtifactRef
from abicheck.storage.snapshot_digest_cache import digest_scope
from abicheck.storage.snapshot_encode import (
    snapshot_content_digest,
    snapshot_to_dict,
    snapshot_to_json,
)
from abicheck.storage.snapshot_evidence import (
    evidence_identity_digests,
    snapshot_evidence_digest,
    stored_evidence_digest,
)


def _fn(name: str, *params: str, ret: str = "int") -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _base() -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="1.0",
        created_at="2026-01-01T00:00:00Z",
        functions=[_fn("a", "int"), _fn("b", "int", "char*"), _fn("c")],
        variables=[
            Variable(name=n, mangled=n, type="int", visibility=Visibility.PUBLIC)
            for n in ("va", "vb", "vc")
        ],
        types=[
            RecordType(
                name=n,
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(name="x", type="int", offset_bits=0),
                    TypeField(name="y", type="int", offset_bits=32),
                ],
            )
            for n in ("S", "T", "U")
        ],
        enums=[
            EnumType(name=n, members=[EnumMember("A", 0), EnumMember("B", 1)])
            for n in ("E1", "E2", "E3")
        ],
        elf=ElfMetadata(
            soname="libx.so.1",
            needed=["libc.so.6", "libm.so.6"],
            symbols=[ElfSymbol(name=n) for n in ("_Z1av", "_Z1bv", "_Z1cv")],
            imports=[ElfImport(name=n) for n in ("malloc", "free", "puts")],
        ),
    )


def _holder(snap: AbiSnapshot, container: str | None) -> object:
    return snap.declarations if container is None else getattr(snap, container)


def _permuted(
    snap: AbiSnapshot, container: str | None, name: str, order: tuple[int, ...]
) -> AbiSnapshot:
    out = copy.deepcopy(snap)
    items = getattr(_holder(out, container), name)
    items[:] = [items[i] for i in order]
    out._func_by_mangled = out._var_by_mangled = out._type_by_name = None
    return out


def _changes(old: AbiSnapshot, new: AbiSnapshot) -> list[object]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return list(compare(old, new).changes)


_CASES = [
    (container, name, order)
    for (container, name) in UNORDERED_LISTS
    for order in itertools.permutations(range(3))
    if order != (0, 1, 2)
]


@pytest.mark.parametrize(("container", "name", "order"), _CASES)
def test_every_permutation_of_an_unordered_list_is_the_same_evidence(
    container: str | None, name: str, order: tuple[int, ...]
) -> None:
    old = _base()
    new = _permuted(old, container, name, order)
    assert len(getattr(_holder(new, container), name)) == 3
    assert _changes(old, new) == []
    assert snapshot_evidence_digest(old) == snapshot_evidence_digest(new)


@pytest.mark.parametrize("field", sorted(VOLATILE_CAPTURE_FIELDS))
def test_volatile_capture_metadata_is_not_evidence(field: str) -> None:
    value: object = (
        123.0
        if field == "source_mtime"
        else True
        if field == "source_mtime_epoch"
        else "other"
    )
    old = _base()
    new = dataclasses.replace(copy.deepcopy(old), **{field: value})
    assert _changes(old, new) == []
    assert snapshot_evidence_digest(old) == snapshot_evidence_digest(new)
    if field == "created_at":
        # The `publication` digest keeps its meaning (D16: the created_at
        # probe is that digest working as designed).
        assert snapshot_content_digest(old) != snapshot_content_digest(new)


def _param_retyped(s: AbiSnapshot) -> None:
    s.declarations.functions[1].params[0].type = "long"


def _params_reordered(s: AbiSnapshot) -> None:
    p = s.declarations.functions[1].params
    p[:] = p[::-1]


def _fields_reordered(s: AbiSnapshot) -> None:
    f = s.declarations.types[0].fields
    f[:] = f[::-1]


def _enum_members_reordered(s: AbiSnapshot) -> None:
    m = s.declarations.enums[0].members
    m[:] = m[::-1]


def _needed_reordered(s: AbiSnapshot) -> None:
    assert s.elf is not None
    s.elf.needed[:] = s.elf.needed[::-1]


def _function_removed(s: AbiSnapshot) -> None:
    del s.declarations.functions[2]


def _version_relabelled(s: AbiSnapshot) -> None:
    s.version = "2.0"


_REAL_CHANGES: dict[str, Callable[[AbiSnapshot], None]] = {
    "param_retyped": _param_retyped,
    "params_reordered": _params_reordered,
    "fields_reordered": _fields_reordered,
    "enum_members_reordered": _enum_members_reordered,
    "needed_reordered": _needed_reordered,
    "function_removed": _function_removed,
    "version_relabelled": _version_relabelled,
}


@pytest.mark.parametrize("change", sorted(_REAL_CHANGES))
def test_a_real_change_changes_the_evidence_digest(change: str) -> None:
    old = _base()
    new = copy.deepcopy(old)
    _REAL_CHANGES[change](new)
    new._func_by_mangled = new._var_by_mangled = new._type_by_name = None
    assert snapshot_evidence_digest(old) != snapshot_evidence_digest(new)
    assert evidence_identity_digests(old, new) in ((None, None),) or (
        len(set(evidence_identity_digests(old, new))) == 2
    )


def test_duplicate_identities_keep_producer_order() -> None:
    # `AbiSnapshot.index()` is first-wins, so the order of two records with
    # one identity can change a comparison; it must stay in the digest.
    old = _base()
    old.declarations.functions.append(_fn("a", "int", ret="long"))
    new = copy.deepcopy(old)
    fns = new.declarations.functions
    fns[0], fns[-1] = fns[-1], fns[0]
    assert snapshot_evidence_digest(old) != snapshot_evidence_digest(new)


def test_document_is_not_mutated_and_digest_is_d5_shaped() -> None:
    doc = snapshot_to_dict(_base())
    before = copy.deepcopy(doc)
    digest = evidence_digest_of_document(doc)
    assert doc == before
    assert digest.startswith("sha256:")


def test_publication_digest_is_unchanged() -> None:
    snap = _base()
    assert (
        snapshot_content_digest(snap)
        == hashlib.sha256(snapshot_to_json(snap).encode()).hexdigest()
    )


def test_memo_is_keyed_by_role() -> None:
    snap = _base()
    with digest_scope():
        pub = snapshot_content_digest(snap)
        ev = snapshot_evidence_digest(snap)
        assert pub != ev
        assert snapshot_content_digest(snap) == pub
        assert snapshot_evidence_digest(snap) == ev


# ── persistence (ArtifactRef.evidence_digest) ───────────────────────────────


def test_artifact_ref_without_the_field_keeps_its_exact_record() -> None:
    ref = ArtifactRef(artifact_id="a", variant_id="v", kind="elf")
    assert "evidence_digest" not in ref.to_dict()
    assert ArtifactRef.from_dict(ref.to_dict()) == ref


def test_artifact_ref_round_trips_and_refuses_a_malformed_digest() -> None:
    digest = evidence_digest_of_document(snapshot_to_dict(_base()))
    ref = ArtifactRef(
        artifact_id="a", variant_id="v", kind="elf", evidence_digest=digest
    )
    assert ArtifactRef.from_dict(ref.to_dict()).evidence_digest == digest
    with pytest.raises(ValueError):
        ArtifactRef(
            artifact_id="a", variant_id="v", kind="elf", evidence_digest="sha256:ab"
        )


def test_stored_package_carries_and_reloads_the_evidence_digest(tmp_path: Path) -> None:
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
    from abicheck.project_snapshot_store import read_artifact_ref
    from abicheck.serialization import SCHEMA_VERSION
    from abicheck.workflows.project_package_input import resolve_project_package

    roots = []
    for i, snap in enumerate(
        (_base(), _permuted(_base(), None, "functions", (2, 1, 0)))
    ):
        snap = dataclasses.replace(snap, created_at=f"2026-0{i + 1}-01T00:00:00Z")
        root = tmp_path / f"pkg{i}"
        write_legacy_snapshot_package(
            snapshot_to_dict(snap),
            root,
            artifact_id="libx",
            max_known_schema_version=SCHEMA_VERSION,
        )
        stored = read_artifact_ref(root, "libx").evidence_digest
        assert stored == snapshot_evidence_digest(snap)
        roots.append(root)
    old, new = (resolve_project_package(r) for r in roots)
    assert stored_evidence_digest(old) == stored_evidence_digest(new) is not None
    # The persisted digest describes exactly the stored document. (It need
    # not equal a digest of the *decoded* snapshot: load-time backfill may
    # add facts, which is why the consumer uses it only against another
    # persisted digest.)
    from abicheck.project_snapshot_legacy import read_legacy_snapshot_document

    assert stored_evidence_digest(old) == evidence_digest_of_document(
        read_legacy_snapshot_document(roots[0])
    )
    # A replace()d copy no longer claims the stored digest.
    assert stored_evidence_digest(dataclasses.replace(old)) is None
    a, b = evidence_identity_digests(old, new)
    assert a is not None and a == b


# ── the consumer, through the public CLI ────────────────────────────────────


@pytest.mark.parametrize("variant", ["created_at", "reordered", "real_change"])
def test_cli_same_snapshot_note_answers_the_evidence_question(
    tmp_path: Path, variant: str
) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    old = _base()
    if variant == "created_at":
        new = dataclasses.replace(copy.deepcopy(old), created_at="2027-01-01T00:00:00Z")
    elif variant == "reordered":
        new = _permuted(old, None, "functions", (2, 0, 1))
    else:
        new = copy.deepcopy(old)
        _param_retyped(new)
    paths = tmp_path / "old.abi.json", tmp_path / "new.abi.json"
    save_snapshot(old, paths[0])
    save_snapshot(new, paths[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = CliRunner().invoke(main, ["compare", str(paths[0]), str(paths[1])])
    assert result.exit_code in (0, 2, 4), result.output
    noted = "evidence is byte-identical" in result.output
    assert noted is (variant != "real_change")


def test_record_count_prefilter_decides_without_serializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abicheck.storage.snapshot_evidence as se

    old = _base()
    new = copy.deepcopy(old)
    _function_removed(new)
    monkeypatch.setattr(
        se, "_uncached_snapshot_evidence_digest", lambda s: pytest.fail("serialized")
    )
    assert evidence_identity_digests(old, new) == (None, None)
