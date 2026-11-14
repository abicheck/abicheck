"""struct <-> class with the same members is compatible; its consequences are not.

ADR-011 amendment (2026-11-14). The class-key alone changes neither layout,
Itanium mangling nor source validity, so ``source_level_kind_changed`` must
contribute COMPATIBLE under every built-in policy. Its real consequences keep
their own grades: a changed default access stays an API break, and MSVC's
class-key mangling (``U`` struct vs ``V`` class) surfaces as a BREAKING PE
export removal. The oracle is the C++/MSVC rule, stated per case below.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, compute_verdict
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.registry import Verdict
from abicheck.pe_metadata import PeExport, PeMetadata


def _change(kind: ChangeKind) -> Change:
    return Change(kind=kind, symbol="KSK", description="d")


@pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
def test_class_key_change_alone_is_compatible(policy: str) -> None:
    assert (
        compute_verdict([_change(ChangeKind.SOURCE_LEVEL_KIND_CHANGED)], policy=policy)
        == Verdict.COMPATIBLE
    )


def test_access_consequence_keeps_its_own_grade() -> None:
    changes = [
        _change(ChangeKind.SOURCE_LEVEL_KIND_CHANGED),
        _change(ChangeKind.FIELD_ACCESS_CHANGED),
    ]
    assert compute_verdict(changes, policy="strict_abi") == Verdict.API_BREAK


@pytest.mark.parametrize(
    ("old_key", "new_key"), [("struct", "class"), ("class", "struct")]
)
def test_msvc_class_key_mangling_is_a_pe_break(old_key: str, new_key: str) -> None:
    code = {"struct": "U", "class": "V"}

    def snap(key: str) -> AbiSnapshot:
        export = f"?use@@YAHPEA{code[key]}KSK@@@Z"
        return AbiSnapshot(
            library="k.dll",
            version="1",
            functions=[
                Function(
                    name="use",
                    mangled=export,
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            types=[RecordType(name="KSK", kind=key, size_bits=32)],
            pe=PeMetadata(exports=[PeExport(name=export)]),
        )

    result = compare(snap(old_key), snap(new_key))
    assert result.verdict == Verdict.BREAKING
    removed = [c.symbol for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
    assert removed == [f"?use@@YAHPEA{code[old_key]}KSK@@@Z"]
