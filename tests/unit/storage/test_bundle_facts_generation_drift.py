"""ADR-062 D2 for `BundleFacts` packages: a stored bundle's extractor/resolver
generation drift is reported through the same ``coverage_warnings`` surface a
``ProjectSnapshot`` package uses -- never refused, never a verdict or exit-code
change.

Bug class: the BundleFacts reader computed ``semantics_differ`` and dropped it,
so a bundle produced under older semantics was compared with no notice at all.
The oracle is the literal notice text, written out here rather than derived
from the implementation's own helpers.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model.bundle_facts import BundleFacts
from abicheck.model.snapshot import AbiSnapshot
from abicheck.storage import versioning
from abicheck.storage.bundle_facts_package import (
    read_bundle_facts_package,
    write_bundle_facts_package,
)
from abicheck.storage.package import InMemoryObjectStore
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

WRITER = 3


def _snap(ret: str, version: str) -> AbiSnapshot:
    """A one-library snapshot whose function returns *ret*."""
    from abicheck.model import Function, Visibility

    return AbiSnapshot(
        library="liba.so",
        version=version,
        platform="elf",
        functions=[
            Function(
                name="f", mangled="_Z1fv", return_type=ret, visibility=Visibility.PUBLIC
            )
        ],
    )


def _facts(ret: str) -> BundleFacts:
    """A captured BundleFacts of one library returning int."""
    return capture_bundle_facts({"liba.so": _snap(ret, "1")})


def _store_with(
    axis: str, stated: int | None, ret: str
) -> tuple[InMemoryObjectStore, object]:
    """Write a bundle whose ``axis`` generation is *stated* (None: unrecorded)."""
    store = InMemoryObjectStore()
    # The source axis is left unstated (0): these cases exercise the
    # extractor and resolver axes only.
    generations = (WRITER, WRITER, 0)
    if stated is None:
        generations = (0, 0, 0)
    elif axis == "resolver":
        generations = (WRITER, stated, 0)
    else:
        generations = (stated, WRITER, 0)
    manifest = write_bundle_facts_package(
        _facts(ret), store=store, generations=generations
    )
    return store, manifest


@pytest.mark.parametrize("axis", ["extractor", "resolver"])
@pytest.mark.parametrize("state", ["same", "older", "newer", "unrecorded"])
@pytest.mark.parametrize("ret", ["int", "long"])  # clean and breaking pair
def test_bundle_notice_matches_the_project_snapshot_surface(
    axis: str, state: str, ret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BundleFacts drift notice matches the ProjectSnapshot surface and never moves the verdict."""
    monkeypatch.setattr(versioning, "EXTRACTOR_GENERATION", WRITER)
    monkeypatch.setattr(versioning, "RESOLVER_GENERATION", WRITER)
    stated: int | None = {
        "same": WRITER,
        "older": WRITER - 1,
        "newer": WRITER + 1,
        "unrecorded": None,
    }[state]
    store, manifest = _store_with(axis, stated, "int")
    old = read_bundle_facts_package(manifest, store=store)
    new_snap = _snap(ret, "2")

    drifts = state in ("older", "newer")
    if drifts:
        reader_at = WRITER
        assert stated is not None
        expected = (
            f"stored bundle facts 'default': package was produced under different "
            f"{axis} semantics ({axis} {stated} vs this build's {reader_at}); "
            "derived results may differ from the original producer's"
        )
        notices = old.per_library_snapshots["liba.so"].load_notices
        assert notices == (expected,)
    else:
        assert old.per_library_snapshots["liba.so"].load_notices == ()

    baseline = compare(_snap_of(_facts("int")), new_snap)
    result = compare(old.per_library_snapshots["liba.so"], new_snap)
    surfaced = [w for w in result.coverage_warnings if "produced under different" in w]
    if drifts:
        assert surfaced == [f"old side: {notice}" for notice in notices]
    else:
        assert surfaced == []
    # Informational only: the verdict is the same as the no-drift run's.
    assert result.verdict == baseline.verdict


def _snap_of(facts: BundleFacts) -> AbiSnapshot:
    """The single per-library snapshot of *facts*."""
    return facts.per_library_snapshots["liba.so"]
