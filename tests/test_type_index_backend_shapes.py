"""The qualified type index (H4) over the record shapes the *debug-info*
backends produce, not only castxml's.

``test_type_index_qualified_identity.py`` covers castxml's shape (bare
``name``, scope in ``qualified_name``). DWARF writes the qualified spelling
into ``name`` and leaves ``qualified_name`` empty; the PDB/BTF/CTF layout path
(``pdb_model.model_types_from_dwarf_metadata``) does the same from the layout
map's qualified keys. One short name in several namespaces must index
without a first-wins warning and resolve each qualified spelling to its own
record, for each producer -- and for a real ``-g`` build.

Oracle: the namespace each fixture record is declared in (and, compiled, the
fixture header's own declarations), never the index's key function.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.entities import RecordType
from abicheck.pdb_model import model_types_from_dwarf_metadata

pytestmark = pytest.mark.model_warning_hygiene

#: Declared scopes sharing the short name ``Parameter``.
SCOPES = ("algo_a", "algo_b", "algo_c", "algo_d::inner", "Holder")


def _dwarf_records(scopes: tuple[str, ...]) -> list[RecordType]:
    # As dwarf_snapshot builds them: qualified spelling in ``name``.
    return [
        RecordType(name=f"{s}::Parameter", kind="struct", size_bits=32) for s in scopes
    ]


def _pdb_records(scopes: tuple[str, ...]) -> list[RecordType]:
    meta = DwarfMetadata(
        structs={
            f"{s}::Parameter": StructLayout(
                name=f"{s}::Parameter",
                byte_size=4 * (i + 1),
                fields=[
                    FieldInfo(name=f"f{i}", type_name="int", byte_offset=0, byte_size=4)
                ],
            )
            for i, s in enumerate(scopes)
        },
        has_dwarf=True,
    )
    records, _ = model_types_from_dwarf_metadata(meta)
    return records


PRODUCERS = {"dwarf": _dwarf_records, "pdb": _pdb_records}


@pytest.mark.parametrize("producer", sorted(PRODUCERS))
@pytest.mark.parametrize("n", range(2, len(SCOPES) + 1))
def test_each_qualified_spelling_resolves_to_its_own_record(
    producer: str, n: int
) -> None:
    scopes = SCOPES[:n]
    records = PRODUCERS[producer](scopes)
    snap = AbiSnapshot(library="l.so", version="1", types=records)
    for scope, rec in zip(scopes, records, strict=True):
        assert snap.type_by_name(f"{scope}::Parameter") is rec
    # No record is declared at global scope, so the bare name has no owner.
    assert snap.type_by_name("Parameter") is None


@pytest.mark.parametrize("producer", sorted(PRODUCERS))
def test_global_twin_owns_the_bare_spelling(producer: str) -> None:
    records = PRODUCERS[producer](("algo_a", "algo_b"))
    glob = RecordType(name="Parameter", kind="struct")
    snap = AbiSnapshot(library="l.so", version="1", types=[*records, glob])
    assert snap.type_by_name("Parameter") is glob
    assert snap.type_by_name("algo_b::Parameter") is records[1]


def test_pdb_layout_sizes_stay_with_their_namespace() -> None:
    """Distinct sizes per scope: a wrong resolution is visible in the value."""
    snap = AbiSnapshot(library="l.dll", version="1", types=_pdb_records(SCOPES))
    sizes = {s: snap.type_by_name(f"{s}::Parameter").size_bits for s in SCOPES}
    assert sizes == {s: 32 * (i + 1) for i, s in enumerate(SCOPES)}


# ── compiled DWARF ────────────────────────────────────────────────────────

_SHAPE = Path(__file__).parent / "field_shapes" / "same_short_name_namespaces"


def _build_g(side: str, out: Path) -> Path:
    out.mkdir()
    lib = out / "libns.so"
    cmd = [
        "g++", "-g", "-shared", "-fPIC", f"-I{_SHAPE / side}",
        str(_SHAPE / side / "api.cpp"), "-o", str(lib),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return lib


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_compiled_dwarf_same_short_name_in_many_scopes(tmp_path: Path) -> None:
    from abicheck.checker import compare
    from abicheck.dumper import dump

    old = dump(_build_g("v1", tmp_path / "v1"), headers=[], version="1")
    new = dump(_build_g("v2", tmp_path / "v2"), headers=[], version="2")
    for scope in SCOPES:
        rec = old.type_by_name(f"{scope}::Parameter")
        assert rec is not None, scope
        assert (rec.qualified_name or rec.name) == f"{scope}::Parameter"
    assert old.type_by_name("Parameter") is None

    changed = {
        c.symbol for c in compare(old, new).changes if "Parameter" in (c.symbol or "")
    }
    # Only algo_b::Parameter grew (the fixture's v2 header adds one field).
    assert changed == {"algo_b::Parameter"}, changed
