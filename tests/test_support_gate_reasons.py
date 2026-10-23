"""A disabled capability must say why, and the reason must be true of the inputs.

The coverage line ``Detector 'X' disabled: <reason>`` is the only place a
reader learns that a check did not run. Field report: two ELF ``.so`` files
dumped with headers got "fingerprint_renames disabled: requires ELF metadata in
elf_only_mode". Both sides had ELF metadata; the precondition that actually
failed was ``elf_only_mode``, and the message named the one that held.

Each support gate is therefore run over every pair of input formats below, and
each precondition its refusal reason *names* is checked against the snapshots.
A reason that cites a precondition the inputs satisfy fails here, whichever
gate wrote it.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import product

import pytest

from abicheck.checker import compare
from abicheck.compare.debug_layout_view import debug_layout_view
from abicheck.detector_registry import registry
from abicheck.diff_symbols_renames import _fingerprint_renames_support
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, RecordType
from abicheck.model.debug_evidence import debug_info_evidence
from abicheck.model.kabi_facts import KabiMetadata
from abicheck.model.macho_facts import MachoMetadata
from abicheck.model.pe_facts import PeMetadata
from abicheck.model.python_facts import PythonApiSurface, PythonExtMetadata
from abicheck.model.sycl_facts import SyclMetadata

Predicate = Callable[[AbiSnapshot, AbiSnapshot], bool]

# Input formats, one side each. Every format is paired with every other, so a
# refusal is judged against both an absent and a present counterpart.
_FORMATS: dict[str, Callable[[], dict[str, object]]] = {
    "bare_headers": lambda: {},
    "elf": lambda: {"elf": ElfMetadata()},
    "elf_only": lambda: {"elf": ElfMetadata(), "elf_only_mode": True},
    "elf_with_types": lambda: {
        "elf": ElfMetadata(),
        "types": [RecordType(name="Base", kind="class")],
    },
    "pe": lambda: {"pe": PeMetadata()},
    "macho": lambda: {"macho": MachoMetadata()},
    "sycl": lambda: {"sycl": SyclMetadata()},
    "kabi": lambda: {"kabi": KabiMetadata()},
    "python_ext": lambda: {"python_ext": PythonExtMetadata()},
    "python_api": lambda: {"python_api": PythonApiSurface()},
    "everything": lambda: {
        "elf": ElfMetadata(),
        "types": [RecordType(name="Base", kind="class")],
        "pe": PeMetadata(),
        "macho": MachoMetadata(),
        "sycl": SyclMetadata(),
        "kabi": KabiMetadata(),
        "python_ext": PythonExtMetadata(),
        "python_api": PythonApiSurface(),
    },
}


def _side(fmt: str, version: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfmt.so", version=version, from_headers=True, **_FORMATS[fmt]()
    )


def _debug(snap: AbiSnapshot) -> bool:
    return debug_info_evidence(snap).basic


# Each refusal-reason fragment and the fact it asserts about the inputs. A
# reason that contains the fragment is only honest when the predicate holds.
_PRECONDITIONS: tuple[tuple[str, Predicate], ...] = (
    ("missing ELF metadata", lambda o, n: o.elf is None or n.elf is None),
    ("missing PE metadata", lambda o, n: o.pe is None or n.pe is None),
    ("missing Mach-O metadata", lambda o, n: o.macho is None or n.macho is None),
    (
        "missing SYCL runtime (PI plugin) metadata",
        lambda o, n: o.sycl is None or n.sycl is None,
    ),
    (
        "missing CPython extension metadata",
        lambda o, n: o.python_ext is None or n.python_ext is None,
    ),
    (
        "missing Module.symvers",
        lambda o, n: o.kabi is None or n.kabi is None,
    ),
    ("missing old Python API surface", lambda o, n: o.python_api is None),
    (
        "missing Python API surface",
        lambda o, n: o.python_api is None or n.python_api is None,
    ),
    (
        "neither side was dumped in elf_only_mode",
        lambda o, n: not (o.elf_only_mode or n.elf_only_mode),
    ),
    (
        "a side was dumped in elf_only_mode",
        lambda o, n: o.elf_only_mode or n.elf_only_mode,
    ),
    (
        "missing DWARF/header type metadata on one side",
        lambda o, n: not o.declarations.types or not n.declarations.types,
    ),
    (
        "no DWARF debug info on either side",
        lambda o, n: not _debug(o) and not _debug(n),
    ),
    ("old snapshot has no DWARF debug info", lambda o, n: not _debug(o)),
    (
        "neither snapshot has a DWARF-vs-header-AST layout coherence mismatch",
        lambda o, n: (
            o.dwarf_layout_coherence != "mismatch"
            and n.dwarf_layout_coherence != "mismatch"
        ),
    ),
    (
        "missing DWARF advanced metadata",
        lambda o, n: (
            not (
                debug_layout_view(o).advanced_collected
                and debug_layout_view(n).advanced_collected
            )
        ),
    ),
)


def _gated_detectors() -> list[tuple[str, Callable[..., tuple[bool, str | None]]]]:
    registry.ensure_loaded()
    # The registry keeps its support gates private; the test reads them to run
    # each one directly, which is the only place a reason can be checked.
    return [
        (entry.name, entry.support_fn)
        for entry in registry._detectors  # noqa: SLF001
        if entry.support_fn is not None
    ]


_GATES = _gated_detectors()


def test_the_gate_table_covers_the_registered_gates() -> None:
    # Guards against this test passing vacuously because the registry was not
    # loaded: the codebase registers well over ten gated detectors today.
    assert len(_GATES) >= 10
    assert "fingerprint_renames" in {name for name, _ in _GATES}


@pytest.mark.parametrize(
    ("old_format", "new_format"),
    list(product(sorted(_FORMATS), repeat=2)),
    ids=lambda fmt: fmt,
)
def test_every_refusal_reason_names_only_preconditions_the_inputs_fail(
    old_format: str, new_format: str
) -> None:
    old = _side(old_format, "1.0")
    new = _side(new_format, "2.0")
    for name, support_fn in _GATES:
        enabled, reason = support_fn(old, new)
        if enabled:
            continue
        assert reason, f"{name} refused {old_format}/{new_format} with no reason"
        matched = [(text, holds) for text, holds in _PRECONDITIONS if text in reason]
        assert matched, (
            f"{name} refused {old_format}/{new_format} with {reason!r}, which names "
            "no precondition this test knows; add it to _PRECONDITIONS if it is real"
        )
        for text, holds in matched:
            assert holds(old, new), (
                f"{name} on {old_format}/{new_format} says {text!r}, but the inputs "
                "satisfy that precondition"
            )


# ── The field report, through the public comparison ─────────────────────────


def _fingerprint_coverage(old: AbiSnapshot, new: AbiSnapshot):
    result = compare(old, new)
    (row,) = [d for d in result.detector_results if d.name == "fingerprint_renames"]
    return row


def test_two_elf_inputs_dumped_with_headers_name_elf_only_mode_not_missing_elf() -> (
    None
):
    row = _fingerprint_coverage(_side("elf", "1.0"), _side("elf", "2.0"))
    assert row.enabled is False
    assert row.coverage_gap == (
        "neither side was dumped in elf_only_mode (both carry headers or debug "
        "info); fingerprint rename matching runs only on stripped, symbol-only dumps"
    )
    assert "missing ELF" not in row.coverage_gap


@pytest.mark.parametrize(
    ("old_format", "new_format"),
    [("elf_only", "elf"), ("elf", "elf_only"), ("elf_only", "elf_only")],
)
def test_one_stripped_side_enables_the_gate(old_format: str, new_format: str) -> None:
    row = _fingerprint_coverage(_side(old_format, "1.0"), _side(new_format, "2.0"))
    assert row.enabled is True
    assert row.coverage_gap is None


@pytest.mark.parametrize(
    ("old_format", "new_format", "missing_side"),
    [
        ("bare_headers", "elf", "old"),
        ("elf", "bare_headers", "new"),
        ("bare_headers", "bare_headers", "both"),
    ],
)
def test_missing_elf_names_the_side_that_lacks_it(
    old_format: str, new_format: str, missing_side: str
) -> None:
    enabled, reason = _fingerprint_renames_support(
        _side(old_format, "1.0"), _side(new_format, "2.0")
    )
    assert enabled is False
    expected = (
        "missing ELF metadata on both sides"
        if missing_side == "both"
        else f"missing ELF metadata on the {missing_side} side"
    )
    assert reason == expected


def test_elf_only_side_with_types_is_not_called_missing_type_metadata() -> None:
    """vtable_layout: a stripped side that still carries types must be refused
    for being elf_only, not for lacking type metadata it actually has."""
    from abicheck.diff_vtable_layout import _vtable_layout_support

    stripped_with_types = AbiSnapshot(
        library="libfmt.so",
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(),
        elf_only_mode=True,
        types=[RecordType(name="Base", kind="class")],
    )
    enabled, reason = _vtable_layout_support(
        stripped_with_types, _side("elf_with_types", "2.0")
    )
    assert enabled is False
    assert reason is not None
    assert reason.startswith("a side was dumped in elf_only_mode")
    assert "missing" not in reason
