"""Unit cells for the recorded binary SHA-256 (ADR-090 D1(c) identity).

The end-to-end cells are ``tests/test_unconfirmed_binary_identity.py``; these
pin the edges that route never reaches with a real binary (a linker script's
target is covered there, with a gcc-built ``.so``): what counts as a
binary operand, how the fact is written, dropped and restored, and what the
evidence digest leaves out. The oracle is ``hashlib`` over the file bytes and
the literal input tables, never the helpers under test.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from abicheck.extract import binary_identity
from abicheck.extract.binary_identity import binary_sha256, record_binary_sha256
from abicheck.model import AbiSnapshot
from abicheck.storage.extension_fact_catalog import (
    BINARY_SHA256,
    VOLATILE_CAPTURE_FACTS,
    evidence_header_extras,
)
from abicheck.storage.extension_facts import (
    apply_model_extension_facts,
    sync_model_extension_facts,
)
from abicheck.storage.wire_extensions import WireExtensions, extensions_of
from abicheck.workflows.run_dump_scope import wrap_run_dump_with_dependency_scope

_PE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "containers"
    / "sources"
    / "corpus_pe.dll"
)
_DIGEST = hashlib.sha256(b"x").hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- what a binary operand is ---------------------------------------------


def test_a_binary_is_hashed_by_its_bytes(tmp_path: Path) -> None:
    dll = Path(shutil.copy(_PE, tmp_path / "a.dll"))
    assert binary_sha256(dll) == _sha(dll)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda d: d, id="directory"),
        pytest.param(lambda d: d / "missing.so", id="missing"),
        pytest.param(
            lambda d: (d / "s.json").write_text("{}") and d / "s.json", id="json"
        ),
        pytest.param(
            lambda d: (d / "n.txt").write_text("not a binary") and d / "n.txt",
            id="text",
        ),
    ],
)
def test_anything_else_has_no_binary_identity(tmp_path: Path, make: Any) -> None:
    path = make(tmp_path)
    assert binary_sha256(path) is None
    snap = AbiSnapshot(library="x", version="1", binary_sha256=_DIGEST)
    record_binary_sha256(snap, path)
    assert snap.binary_sha256 == _DIGEST  # a non-binary leaves the record alone


def test_an_unreadable_binary_has_no_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dll = Path(shutil.copy(_PE, tmp_path / "a.dll"))

    def unreadable(_path: Any) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr("abicheck.extract.cache_input_digest.file_digest", unreadable)
    assert binary_sha256(dll) is None
    snap = AbiSnapshot(library="x", version="1")
    binary_identity.record_binary_sha256(snap, dll)
    assert snap.binary_sha256 is None


def test_the_tier1_wrapper_stamps_only_a_path_argument(tmp_path: Path) -> None:
    dll = Path(shutil.copy(_PE, tmp_path / "a.dll"))

    def uncached(path: Any, binary_fmt: str = "pe") -> AbiSnapshot:
        return AbiSnapshot(library="x", version="1")

    run = wrap_run_dump_with_dependency_scope(uncached)
    assert run(dll).binary_sha256 == _sha(dll)
    assert run(str(dll)).binary_sha256 == _sha(dll)
    assert run(None).binary_sha256 is None


# --- the fact on the wire --------------------------------------------------


def _stored(snap: AbiSnapshot) -> dict[str, Any]:
    ext = extensions_of(snap)
    if ext is None:
        return {}
    return dict(ext.header_extras.get("binary", {}).get("extensions", {}))


def test_sync_writes_then_drops_the_fact() -> None:
    snap = AbiSnapshot(library="x", version="1", binary_sha256=_DIGEST)
    sync_model_extension_facts(snap)
    assert _stored(snap)[BINARY_SHA256.name] == _DIGEST
    snap.binary_sha256 = None
    sync_model_extension_facts(snap)
    assert BINARY_SHA256.name not in _stored(snap)


def test_a_blocked_binary_section_is_left_alone() -> None:
    snap = AbiSnapshot(library="x", version="1", binary_sha256=_DIGEST)
    snap.unreadable_sections = [("binary", "newer section version")]
    sync_model_extension_facts(snap)
    assert BINARY_SHA256.name not in _stored(snap)


@pytest.mark.parametrize(
    ("stored", "restored"),
    [
        (_DIGEST, _DIGEST),
        ("NOT-A-DIGEST", None),  # undecodable reads as unknown, never refused
        (7, None),
    ],
)
def test_apply_restores_only_a_valid_digest(
    stored: object, restored: str | None
) -> None:
    snap = AbiSnapshot(library="x", version="1")
    ext = WireExtensions(
        header_extras={"binary": {"extensions": {BINARY_SHA256.name: stored}}}
    )
    from abicheck.storage.wire_extensions import attach

    attach(snap, ext)
    apply_model_extension_facts(snap)
    assert snap.binary_sha256 == restored


# --- the evidence digest leaves it out --------------------------------------


_OTHER = {"abicheck.export_read": {}}
_CASES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("empty", {}, {}),
    ("only_the_hash", {"binary": {"extensions": {BINARY_SHA256.name: _DIGEST}}}, {}),
    (
        "hash_and_another_fact",
        {"binary": {"extensions": {BINARY_SHA256.name: _DIGEST, **_OTHER}}},
        {"binary": {"extensions": dict(_OTHER)}},
    ),
    (
        "hash_and_a_must_understand_list",
        {
            "binary": {
                "extensions": {BINARY_SHA256.name: _DIGEST},
                "must_understand": ["abicheck.x"],
            }
        },
        {"binary": {"must_understand": ["abicheck.x"]}},
    ),
    (
        "another_section_untouched",
        {"debug": {"extensions": dict(_OTHER)}},
        {"debug": {"extensions": dict(_OTHER)}},
    ),
    (
        "non_mapping_extensions_kept_verbatim",
        {"binary": {"extensions": "opaque"}},
        {"binary": {"extensions": "opaque"}},
    ),
]


@pytest.mark.parametrize(
    ("extras", "expected"),
    [pytest.param(e, x, id=name) for name, e, x in _CASES],
)
def test_evidence_header_extras_drops_only_capture_facts(
    extras: dict[str, Any], expected: dict[str, Any]
) -> None:
    assert VOLATILE_CAPTURE_FACTS == {BINARY_SHA256.name}
    assert evidence_header_extras(extras) == expected


def test_an_extension_set_holding_only_the_hash_has_an_empty_evidence_form() -> None:
    only = WireExtensions(
        header_extras={"binary": {"extensions": {BINARY_SHA256.name: _DIGEST}}}
    )
    assert only.evidence_form() == {}
    other = WireExtensions(header_extras={"binary": {"extensions": dict(_OTHER)}})
    both = WireExtensions(
        header_extras={
            "binary": {"extensions": {BINARY_SHA256.name: _DIGEST, **_OTHER}}
        }
    )
    assert both.evidence_form() == other.evidence_form() != {}


# --- a non-regular live operand has no identity --------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_a_fifo_operand_has_no_identity(tmp_path: Path) -> None:
    """``compare <(cat a.so) <(cat b.so)`` hands abicheck two pipes. A pipe has
    no stable bytes to hash, so it carries no identity at all -- never a shared
    marker that would prove two different pipes one input -- and nothing reads
    it here (the dump that reads it is the one consumer)."""
    from abicheck.workflows.input_resolution import collect_metadata

    fifos = []
    for name in ("a", "b"):
        fifo = tmp_path / f"{name}.fifo"
        os.mkfifo(fifo)
        fifos.append(fifo)
    assert [collect_metadata(f) for f in fifos] == [None, None]
    assert [binary_sha256(f) for f in fifos] == [None, None]


def test_a_regular_binary_operand_is_identified_by_its_bytes(tmp_path: Path) -> None:
    from abicheck.workflows.input_resolution import collect_metadata

    dll = Path(shutil.copy(_PE, tmp_path / "a.dll"))
    meta = collect_metadata(dll)
    assert meta is not None
    assert (meta.sha256, meta.size_bytes) == (_sha(dll), dll.stat().st_size)
