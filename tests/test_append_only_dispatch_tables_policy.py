"""Built-in ``append_only_dispatch_tables`` policy (issue #1709).

Dispatch / function-pointer tables (Level Zero ``ze_*_dditable_t``, Vulkan
``VkLayer*DispatchTable``, OpenCL ``cl_icd_dispatch``) only grow by appending
members. Under the opt-in profile a tail append and its size growth are
compatible, but removing, reordering, inserting or retyping an existing
member still breaks, a non-matching type is unaffected, and without the
profile nothing changes. The oracle is the layout contract itself: a change is
compatible iff every old member keeps its name, type and offset.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.policy_file import PolicyFile, builtin_policy_path
from abicheck.reporter import to_json

_PTR_BITS = 64

#: (old members, new members) -> is the new layout an append-only extension?
_EDITS: dict[str, tuple[list[str], list[str]]] = {
    "append-one": (["a", "b"], ["a", "b", "c"]),
    "append-two": (["a", "b"], ["a", "b", "c", "d"]),
    "remove-tail": (["a", "b"], ["a"]),
    "remove-head": (["a", "b"], ["b"]),
    "reorder": (["a", "b"], ["b", "a"]),
    "reorder-and-append": (["a", "b"], ["b", "a", "c"]),
    "insert-middle": (["a", "b"], ["a", "x", "b"]),
    "insert-head": (["a", "b"], ["x", "a", "b"]),
}

_MATCHING = [
    "ze_global_dditable_t",
    "ze_device_dditable_t",
    "zet_metric_dditable_t",
    "zes_device_dditable_t",
    # DWARF names a typedef'd struct by its tag: ``_ze_..._dditable_t``.
    "_ze_global_dditable_t",
    "_zes_driver_dditable_t",
    # Top-level aggregates have no middle component.
    "ze_dditable_t",
    "zes_dditable_t",
    "_ze_dditable_t",
    "_zes_dditable_t",
    "VkLayerDispatchTable",
    "VkLayerInstanceDispatchTable",
    "cl_icd_dispatch",
]
_NON_MATCHING = [
    "my_struct_t",
    "ze_device_properties_t",
    "_ze_device_properties_t",
    "__ze_dditable_t",
    "ze_dditable",
    "VkDeviceCreateInfo",
]


def _is_append_only(old: list[str], new: list[str]) -> bool:
    return new[: len(old)] == old


def _snap(name: str, members: list[str], *, retype: str | None = None) -> AbiSnapshot:
    fields = [
        TypeField(
            name=m,
            type="int (*)(void)" if m == retype else "void *",
            offset_bits=_PTR_BITS * i,
        )
        for i, m in enumerate(members)
    ]
    rec = RecordType(
        name=name, kind="struct", size_bits=_PTR_BITS * len(members), fields=fields
    )
    getter = Function(
        name="getProcAddrTable",
        mangled="getProcAddrTable",
        return_type="int",
        params=[Param(name="table", type=f"{name} *")],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(
        library="libdisp.so.1", version="1", functions=[getter], types=[rec]
    )


@pytest.fixture(scope="module")
def policy() -> PolicyFile:
    path = builtin_policy_path("append_only_dispatch_tables")
    assert path is not None
    return PolicyFile.load(path)


@pytest.mark.parametrize("name", _MATCHING)
@pytest.mark.parametrize("edit", sorted(_EDITS))
def test_matching_table_compatible_iff_append_only(
    name: str, edit: str, policy: PolicyFile
) -> None:
    old, new = _EDITS[edit]
    result = compare(_snap(name, old), _snap(name, new), policy_file=policy)
    expected = Verdict.COMPATIBLE if _is_append_only(old, new) else Verdict.BREAKING
    assert result.verdict == expected
    # Record before disposing: the size/field findings are kept, not deleted.
    if _is_append_only(old, new):
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds


@pytest.mark.parametrize("name", _MATCHING)
def test_retyping_an_existing_member_still_breaks(
    name: str, policy: PolicyFile
) -> None:
    result = compare(
        _snap(name, ["a", "b"]),
        _snap(name, ["a", "b", "c"], retype="b"),
        policy_file=policy,
    )
    assert result.verdict == Verdict.BREAKING
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in {c.kind for c in result.changes}


@pytest.mark.parametrize("name", _NON_MATCHING)
@pytest.mark.parametrize("edit", sorted(_EDITS))
def test_non_matching_type_unaffected(name: str, edit: str, policy: PolicyFile) -> None:
    old, new = _EDITS[edit]
    with_policy = compare(_snap(name, old), _snap(name, new), policy_file=policy)
    without = compare(_snap(name, old), _snap(name, new))
    assert with_policy.verdict == without.verdict == Verdict.BREAKING


@pytest.mark.parametrize("name", _MATCHING)
def test_without_policy_append_still_breaks(name: str) -> None:
    result = compare(_snap(name, ["a", "b"]), _snap(name, ["a", "b", "c"]))
    assert result.verdict == Verdict.BREAKING


def test_disposition_visible_in_json_report(policy: PolicyFile) -> None:
    name = "ze_global_dditable_t"
    result = compare(
        _snap(name, ["a", "b"]), _snap(name, ["a", "b", "c"]), policy_file=policy
    )
    doc = json.loads(to_json(result))
    size = [c for c in doc["changes"] if c["kind"] == "type_size_changed"]
    assert len(size) == 1
    reclassified = json.dumps(size[0].get("reclassified_by"))
    assert "append-only dispatch table" in reclassified
    applied = {
        r["rule_id"]: r["changed_count"]
        for r in doc["disposition_audit"]["reclassifications"]
    }
    assert applied["append-only-dispatch-table-size-grew"] == 1
