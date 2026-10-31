"""ADR-062 D18 step 2: a lazily loaded snapshot is the eagerly loaded one.

The oracle throughout is the eager decoder (``eager_section_decode``), never
a helper of the lazy path: every lazy load is compared against the eager
load of the same file on equality, saved bytes, the ``publication`` and
``evidence`` digests and ``compare`` results, under every access order and
under concurrent first access. A second family of tests pins *what stays
undecoded*: every detector whose declared sections
(``compare/section_readers.DETECTOR_SECTIONS``) avoid the deferred ones
leaves the declaration store pending.
"""

from __future__ import annotations

import copy
import dataclasses
import gzip
import itertools
import json
import pickle
import tempfile
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker import compare
from abicheck.compare.detection_memo import detection_memo_scope
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.detector_registry import registry
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolBinding
from abicheck.model.lazy_graph import is_graph_decoded
from abicheck.serialization import load_snapshot, snapshot_from_dict, snapshot_to_json
from abicheck.storage import fact_backfill, snapshot_lazy_decode
from abicheck.storage.evidence_digest import evidence_digest_of_document
from abicheck.storage.snapshot_codec import write_snapshot
from abicheck.storage.snapshot_encode import snapshot_content_digest, snapshot_to_dict
from abicheck.storage.snapshot_lazy_decode import (
    LEGACY_BACKFILL_FLOOR,
    eager_section_decode,
)


def is_ir_pending(snap: AbiSnapshot) -> bool:
    """Whether *snap*'s declaration store is still undecoded."""
    return not is_graph_decoded(snap, "semantic_ir")


_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_CORPUS = sorted(
    [
        *_FIXTURES.glob("action/action_test_v*.json"),
        _FIXTURES / "contract_context_v1" / "old.json",
        _FIXTURES / "contract_context_v1" / "new.json",
        *_FIXTURES.glob("header_graph/*.json"),
        *_FIXTURES.glob("schema/v*.json"),
        *_FIXTURES.glob("toolchain_drift/*.json"),
        *_FIXTURES.glob("generations/gen*/corpus.json.gz"),
    ]
)
_PAIRS = [
    (
        _FIXTURES / "contract_context_v1" / "old.json",
        _FIXTURES / "contract_context_v1" / "new.json",
    ),
    *(
        (old, old.with_name(old.name.replace("__v1", "__v2")))
        for old in sorted(_FIXTURES.glob("header_graph/*__v1.json"))
    ),
]

#: Sections whose decoded content lives in the deferred cell: the store's
#: own sections, the IR section, debug layout and the per-entity half of
#: ``extraction_scope`` (stored in ``layout``).
_DEFERRED_SECTIONS = frozenset(
    {"declarations", "types", "semantic_ir", "debug", "layout"}
)


def _quiet_load(path: Path, *, eager: bool = False) -> AbiSnapshot:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if eager:
            with eager_section_decode():
                return load_snapshot(path)
        return load_snapshot(path)


def _observed(snap: AbiSnapshot) -> tuple[str, str, str]:
    """What a user can observe of a snapshot: saved bytes and both digests."""
    return (
        snapshot_to_json(snap),
        snapshot_content_digest(snap),
        evidence_digest_of_document(snapshot_to_dict(snap)),
    )


def _diff_signature(old: AbiSnapshot, new: AbiSnapshot) -> tuple[Any, ...]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = compare(old, new)
    return (
        result.verdict,
        sorted((c.kind.value, c.symbol, c.description) for c in result.changes),
    )


# -- the gate -----------------------------------------------------------------


def test_backfill_floor_covers_every_rule_the_backfill_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oracle: the rules ``apply_legacy_fact_backfill`` really passes."""
    seen: list[Any] = []

    def capture(d: Any, **kwargs: Any) -> None:
        seen.extend(kwargs["rules"])

    monkeypatch.setattr(fact_backfill, "apply_case_a_fact_backfill", capture)
    fact_backfill.apply_legacy_fact_backfill(
        {}, [], [], 1, True, True, None, evidenced=frozenset()
    )
    assert seen
    assert max(rule.min_schema_version for rule in seen) == LEGACY_BACKFILL_FLOOR


def test_current_documents_load_lazily_and_legacy_ones_eagerly() -> None:
    lazy = {p for p in _CORPUS if is_ir_pending(_quiet_load(p))}
    for path in _CORPUS:
        raw = path.read_bytes()
        doc = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)
        flat = doc.get("sections", {}).get("debug", doc)
        recorded = "from_headers" in json.dumps(flat)
        if path in lazy:
            assert recorded, path
    # Fixtures predating the recorded `from_headers` key stay eager.
    assert not any(p in lazy for p in _FIXTURES.glob("schema/v*.json"))
    assert len(lazy) >= len(_CORPUS) // 2


def test_snapshot_from_dict_without_a_scanned_source_stays_eager() -> None:
    path = _FIXTURES / "contract_context_v1" / "old.json"
    snap = snapshot_from_dict(json.loads(path.read_text()))
    assert not is_ir_pending(snap)


def test_eager_switch_forces_eager() -> None:
    path = _FIXTURES / "contract_context_v1" / "old.json"
    assert is_ir_pending(_quiet_load(path))
    assert not is_ir_pending(_quiet_load(path, eager=True))


# -- differential: the fixture corpus -------------------------------------------


@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_lazy_load_matches_eager_load(path: Path) -> None:
    eager = _quiet_load(path, eager=True)
    assert _quiet_load(path) == eager
    assert _observed(_quiet_load(path)) == _observed(eager)


_COPIES: dict[str, Callable[[AbiSnapshot], AbiSnapshot]] = {
    "copy": copy.copy,
    "deepcopy": copy.deepcopy,
    "pickle": lambda s: pickle.loads(pickle.dumps(s)),
    "replace": lambda s: dataclasses.replace(s),
}


@pytest.mark.parametrize("how", sorted(_COPIES))
@pytest.mark.parametrize("path", [p for p, _ in _PAIRS], ids=lambda p: p.name)
def test_copies_of_an_undecoded_snapshot_match_eager(path: Path, how: str) -> None:
    eager = _quiet_load(path, eager=True)
    lazy = _quiet_load(path)
    assert is_ir_pending(lazy)
    copied = _COPIES[how](lazy)
    assert _observed(copied) == _observed(eager)
    assert _observed(lazy) == _observed(eager)


def test_deepcopy_decodes_independently() -> None:
    path = _FIXTURES / "contract_context_v1" / "old.json"
    lazy = _quiet_load(path)
    twin = copy.deepcopy(lazy)
    assert is_ir_pending(twin)
    first = lazy.declarations.functions
    assert is_ir_pending(twin)
    assert twin.declarations.functions == first
    assert twin.declarations.functions is not first


@pytest.mark.parametrize("old,new", _PAIRS, ids=lambda p: p.name)
def test_compare_results_match_eager(old: Path, new: Path) -> None:
    expected = _diff_signature(
        _quiet_load(old, eager=True), _quiet_load(new, eager=True)
    )
    assert _diff_signature(_quiet_load(old), _quiet_load(new)) == expected
    # Mixed: one side lazy, the other eager.
    assert _diff_signature(_quiet_load(old), _quiet_load(new, eager=True)) == expected


# -- differential: generated snapshots --------------------------------------------


_NAME = st.text(alphabet="abcdefgh", min_size=1, max_size=6)


@st.composite
def _snapshots(draw: Any) -> AbiSnapshot:
    names = draw(st.lists(_NAME, min_size=0, max_size=8, unique=True))
    records = draw(st.lists(_NAME, min_size=0, max_size=4, unique=True))
    funcs = [
        Function(
            name=f"ns::{n}",
            mangled=f"_ZN2ns{len(n)}{n}Ev",
            return_type=draw(st.sampled_from(["int", "void", "long"])),
            params=[
                Param(name=f"p{i}", type="int") for i in range(draw(st.integers(0, 3)))
            ],
            visibility=draw(st.sampled_from([Visibility.PUBLIC, Visibility.HIDDEN])),
        )
        for n in names
    ]
    variables = [
        Variable(name=f"ns::v{n}", mangled=f"_ZN2ns1v{n}E", type="int")
        for n in draw(st.lists(_NAME, max_size=3, unique=True))
    ]
    types = [
        RecordType(
            name=f"ns::{r}",
            kind="struct",
            size_bits=32 * draw(st.integers(1, 4)),
            fields=[TypeField(name="x", type="int", offset_bits=0)],
        )
        for r in records
    ]
    exported = draw(
        st.lists(st.sampled_from([f.mangled for f in funcs] or ["_Z1fv"]), unique=True)
    )
    elf = draw(
        st.sampled_from(
            [
                None,
                ElfMetadata(
                    soname="libg.so",
                    symbols=[
                        ElfSymbol(name=m, binding=SymbolBinding.WEAK) for m in exported
                    ],
                ),
            ]
        )
    )
    return AbiSnapshot(
        library="libg.so",
        version=draw(st.sampled_from(["1", "2"])),
        functions=funcs,
        variables=variables,
        types=types,
        elf=elf,
        from_headers=draw(st.booleans()),
        platform="elf" if elf is not None else None,
    )


def _round_trip(snap: AbiSnapshot, directory: Path, name: str) -> Path:
    path = directory / name
    write_snapshot(snap, path)
    return path


@pytest.mark.slow
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(old=_snapshots(), new=_snapshots())
def test_generated_snapshots_lazy_equals_eager(
    old: AbiSnapshot, new: AbiSnapshot
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_path = _round_trip(old, Path(tmp), "old.json")
        new_path = _round_trip(new, Path(tmp), "new.json")
        eager_old = _quiet_load(old_path, eager=True)
        eager_new = _quiet_load(new_path, eager=True)
        lazy_old, lazy_new = _quiet_load(old_path), _quiet_load(new_path)
        assert is_ir_pending(lazy_old) and is_ir_pending(lazy_new)
        assert _diff_signature(lazy_old, lazy_new) == _diff_signature(
            eager_old, eager_new
        )
        for path, eager in ((old_path, eager_old), (new_path, eager_new)):
            assert _quiet_load(path) == eager
            assert _observed(_quiet_load(path)) == _observed(eager)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(snap=_snapshots())
def test_generated_snapshot_round_trip_fast(snap: AbiSnapshot) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _round_trip(snap, Path(tmp), "s.json")
        assert _observed(_quiet_load(path)) == _observed(_quiet_load(path, eager=True))


def test_deferred_load_steps_run_in_the_cell(tmp_path: Path) -> None:
    """The ELF-binding backfill and the export-absence reconciliation, both
    queued into the cell, give the eager answer."""
    func = Function(name="f", mangled="_Z1fv", return_type="int")
    snap = AbiSnapshot(
        library="l.so",
        version="1",
        functions=[func],
        elf=ElfMetadata(
            soname="l.so", symbols=[ElfSymbol("_Z1fv", SymbolBinding.WEAK)]
        ),
        from_headers=True,
        platform="elf",
    )
    doc = snapshot_to_dict(snap)
    for raw in doc["functions"]:
        raw.pop("elf_binding", None)
        raw.pop("elf_binding_fact", None)
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc))
    eager = _quiet_load(path, eager=True)
    lazy = _quiet_load(path)
    assert is_ir_pending(lazy)
    assert lazy.declarations.functions[0].elf_binding is SymbolBinding.WEAK
    assert eager.declarations.functions[0].elf_binding is SymbolBinding.WEAK
    assert _observed(lazy) == _observed(eager)


# -- failures --------------------------------------------------------------------


def test_a_corrupt_deferred_section_raises_the_eager_error_at_every_access(
    tmp_path: Path,
) -> None:
    source = _FIXTURES / "contract_context_v1" / "old.json"
    doc = json.loads(source.read_text())
    doc["sections"]["declarations"]["payload"]["functions"] = [{"name": 7}]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(Exception) as eager_error:
        _quiet_load(path, eager=True)
    lazy = _quiet_load(path)
    for _ in range(2):  # stays pending: never an empty store on retry
        with pytest.raises(type(eager_error.value)) as lazy_error:
            _ = lazy.declarations
        assert str(lazy_error.value) == str(eager_error.value)
        assert is_ir_pending(lazy)


# -- what stays undecoded ----------------------------------------------------------


def _run_detector(entry: Any, old: AbiSnapshot, new: AbiSnapshot) -> None:
    with detection_memo_scope():
        try:
            if entry.support_fn is not None:
                entry.support_fn(old, new)
            entry.fn(old, new)
        except Exception:  # noqa: BLE001 -- only decoding is observed here
            pass


registry.ensure_loaded()
_ENTRIES = {entry.name: entry for entry in registry._detectors}
_NON_READERS = sorted(
    name
    for name, sections in DETECTOR_SECTIONS.items()
    if not sections & _DEFERRED_SECTIONS
)


def test_every_detector_has_declared_sections() -> None:
    assert set(_ENTRIES) == set(DETECTOR_SECTIONS)
    assert _NON_READERS  # the binary-only detectors exist


@pytest.mark.parametrize("old,new", _PAIRS, ids=lambda p: p.name)
@pytest.mark.parametrize("name", _NON_READERS)
def test_a_detector_reading_no_deferred_section_decodes_none(
    name: str, old: Path, new: Path
) -> None:
    a, b = _quiet_load(old), _quiet_load(new)
    _run_detector(_ENTRIES[name], a, b)
    assert is_ir_pending(a) and is_ir_pending(b)


@pytest.mark.parametrize("size", range(1, len(_NON_READERS) + 1))
def test_every_combination_of_non_readers_decodes_none(size: int) -> None:
    """Exhaustive over subset *sizes* in registration order, and over every
    pair: a run of non-reading detectors never decodes, whatever ran
    before it."""
    old, new = _PAIRS[0]
    combos = list(itertools.combinations(_NON_READERS, size))
    for combo in combos if size <= 2 else combos[:1] + combos[-1:]:
        a, b = _quiet_load(old), _quiet_load(new)
        for name in combo:
            _run_detector(_ENTRIES[name], a, b)
        assert is_ir_pending(a) and is_ir_pending(b), combo


def test_a_detector_reading_declarations_does_decode() -> None:
    old, new = _PAIRS[0]
    a, b = _quiet_load(old), _quiet_load(new)
    _run_detector(_ENTRIES["functions"], a, b)
    assert not is_ir_pending(a)


# -- access order and concurrency ------------------------------------------------------


_ACCESSES: dict[str, Callable[[AbiSnapshot], object]] = {
    "declarations": lambda s: s.declarations.functions,
    "semantic_ir": lambda s: s.semantic_ir,
    "surface_graph": lambda s: s.surface_graph,
    "index": lambda s: s.function_map,
    "encode": snapshot_to_json,
    "assign": lambda s: setattr(s, "semantic_ir", None),
}


@pytest.mark.parametrize("order", list(itertools.permutations(sorted(_ACCESSES), 3)))
def test_any_access_order_gives_the_eager_snapshot(order: tuple[str, ...]) -> None:
    path = _FIXTURES / "header_graph" / "case09_cpp_vtable__v2.json"
    eager = _quiet_load(path, eager=True)
    lazy = _quiet_load(path)
    for name in order:
        _ACCESSES[name](eager)
        _ACCESSES[name](lazy)
    assert _observed(lazy) == _observed(eager)


def test_concurrent_first_access_decodes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _FIXTURES / "contract_context_v1" / "new.json"
    real = snapshot_lazy_decode.decode_declaration_store
    calls: list[int] = []

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(snapshot_lazy_decode, "decode_declaration_store", counting)
    lazy = _quiet_load(path)
    barrier = threading.Barrier(8)
    seen: list[object] = []

    def reader() -> None:
        barrier.wait()
        seen.append(lazy.declarations)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len({id(store) for store in seen}) == 1
    assert _observed(lazy) == _observed(_quiet_load(path, eager=True))
