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

"""Bug class ``storage.out_of_band_snapshot_reader_envelope_drift``.

ADR-062/ADR-063 Phase 8 made a real ``abicheck dump`` write
``storage.sectioned_document``'s envelope, so every field that used to sit at
a snapshot document's top level now lives under ``sections.<kind>.payload``.
Consumers *inside* ``abicheck/`` go through ``snapshot_from_dict``, which
reads either shape. The consumers that broke are the ones *outside* it, which
hand-parse the JSON file a ``dump`` wrote:

* ``contrib/abicheck-clang-plugin/tests/scan_flow.py`` read
  ``baseline.get("build_source")`` and failed the whole ``clang-plugin`` CI
  lane with "merged baseline has no embedded build_source payload" against a
  dump that had in fact folded the plugin pack correctly;
* ``tests/validate_examples.py``'s ``_embedded_present_layers`` read the same
  key and silently answered "no L3/L4/L5 layer present" for every real
  dump-written snapshot — a checker for silent evidence degradation,
  silently degraded.

Both are one class, not two incidents: *an out-of-band reader that indexes a
dump-written snapshot's formerly-top-level key without unwrapping the
envelope reads ``None``, and nothing tells it apart from "the field really is
absent."* The tests below state that class two ways.

**Behavioural half** (:class:`TestReadersAgreeOnBothEnvelopeShapes`): every
registered out-of-band reader must return the *same* answer for the sectioned
and flat encodings of the same snapshot, over a range of payloads rather than
the one that was reported. The oracle is the flat encoding — the shape these
readers were originally written against and which is independently correct —
never the unwrap helper the fix itself uses.

**Structural half** (:class:`TestNoUnguardedOutOfBandReader`): an AST scan for
a *new* out-of-band reader that indexes a snapshot document by a
formerly-top-level key with no unwrap in the same function. The behavioural
half can only cover readers someone remembered to register; this half is what
makes the class closed, the way ``changekind-partition`` closes its own.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource import (
    BuildSourceManifest,
    BuildSourcePack,
    LayerCoverage,
)
from abicheck.buildsource.model import CoverageStatus
from abicheck.model import AbiSnapshot
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.sectioned_document import to_sectioned_document

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Snapshot payloads under test
# --------------------------------------------------------------------------
#: Layer sets to build a pack for. Deliberately more than the one the incident
#: reported (`L4_source_abi` alone, via the clang plugin): a reader that
#: happens to work for one layer combination and not another is the same bug.
_LAYER_CASES: tuple[tuple[str, ...], ...] = (
    (),
    ("L3_build",),
    ("L4_source_abi",),
    ("L5_source_graph",),
    ("L3_build", "L4_source_abi"),
    ("L4_source_abi", "L5_source_graph"),
    ("L3_build", "L4_source_abi", "L5_source_graph"),
)


def _snapshot_with_layers(layers: tuple[str, ...]) -> AbiSnapshot:
    """An `AbiSnapshot` whose embedded pack reports *layers* as present."""
    pack = BuildSourcePack.empty(Path(""))
    pack.manifest = BuildSourceManifest(
        coverage=[
            LayerCoverage(layer=layer, status=CoverageStatus.PRESENT)
            for layer in layers
        ]
    )
    return AbiSnapshot(library="libwidget.so", version="1.0", build_source=pack)


def _both_encodings(snap: AbiSnapshot) -> tuple[dict[str, Any], dict[str, Any]]:
    """(flat, sectioned) encodings of the same snapshot.

    The flat document is the oracle: it is what these readers were written
    against and what they demonstrably answered correctly before Phase 8.
    """
    flat = snapshot_to_dict(snap)
    sectioned = to_sectioned_document(flat, max_known_schema_version=SCHEMA_VERSION)
    assert "sections" in sectioned, "fixture is not actually the envelope shape"
    assert "build_source" not in sectioned, (
        "fixture no longer reproduces the bug: the envelope must NOT expose "
        "build_source at top level, or a reader missing its unwrap would pass "
        "by accident"
    )
    return flat, sectioned


# --------------------------------------------------------------------------
# The registered out-of-band readers
# --------------------------------------------------------------------------
def _read_via_validate_examples(path: Path) -> Any:
    from tests.validate_examples import _embedded_present_layers

    return _embedded_present_layers(path)


def _read_via_scan_flow(path: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_scan_flow_under_test",
        REPO_ROOT / "contrib" / "abicheck-clang-plugin" / "tests" / "scan_flow.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = json.loads(path.read_text(encoding="utf-8"))
    unwrapped = module._unwrap_snapshot_envelope(raw)
    # Exactly the expression the lane asserts on, so this test fails for the
    # same reason the lane does rather than for a paraphrase of it.
    return bool(unwrapped.get("build_source") or {})


def _read_via_action_build_manifest(path: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_action_build_manifest_under_test",
        REPO_ROOT / "actions" / "baseline" / "build_manifest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The production reader itself, not a re-implementation of its unwrap:
    # a stand-in that repeats the fix would pass no matter what the module
    # does, which is exactly the tautology this class is about.
    meta = module._read_snapshot_meta(path)
    return {k: v for k, v in meta.items() if k != "sha256"}


#: name -> reader. A reader takes the path of a snapshot document on disk and
#: returns whatever it derives from it; the invariant is only that the two
#: encodings agree, so the return type is the reader's own business.
_READERS: dict[str, Callable[[Path], Any]] = {
    "validate_examples._embedded_present_layers": _read_via_validate_examples,
    "scan_flow.build_source_presence": _read_via_scan_flow,
    # Covers more than build_source: this one returns library/version/git_*/
    # dump_provenance too, so several of the moved keys are exercised
    # behaviourally rather than only by the AST scan below.
    "actions/baseline/build_manifest._read_snapshot_meta": (
        _read_via_action_build_manifest
    ),
}


class TestReadersAgreeOnBothEnvelopeShapes:
    """The class invariant, executable."""

    @pytest.mark.parametrize("reader_name", sorted(_READERS))
    @pytest.mark.parametrize(
        "layers", _LAYER_CASES, ids=lambda ls: "+".join(ls) or "none"
    )
    def test_sectioned_answers_the_same_as_flat(
        self, reader_name: str, layers: tuple[str, ...], tmp_path: Path
    ) -> None:
        reader = _READERS[reader_name]
        flat, sectioned = _both_encodings(_snapshot_with_layers(layers))

        flat_path = tmp_path / "flat.json"
        flat_path.write_text(json.dumps(flat), encoding="utf-8")
        sectioned_path = tmp_path / "sectioned.json"
        sectioned_path.write_text(json.dumps(sectioned), encoding="utf-8")

        assert reader(sectioned_path) == reader(flat_path), (
            f"{reader_name} reads the sectioned envelope differently from the "
            f"equivalent flat document (layers={layers or 'none'}) — it is "
            "indexing a formerly-top-level key without unwrapping"
        )

    @pytest.mark.parametrize(
        "layers", _LAYER_CASES, ids=lambda ls: "+".join(ls) or "none"
    )
    def test_present_layers_are_actually_reported(
        self, layers: tuple[str, ...], tmp_path: Path
    ) -> None:
        """Agreement alone is satisfiable by two readers that both see nothing.

        The independent oracle here is *the input* — the layer set the pack was
        built from — not anything the production code computes.
        """
        _, sectioned = _both_encodings(_snapshot_with_layers(layers))
        path = tmp_path / "sectioned.json"
        path.write_text(json.dumps(sectioned), encoding="utf-8")

        expected = {
            {"L3_build": "L3", "L4_source_abi": "L4", "L5_source_graph": "L5"}[layer]
            for layer in layers
        }
        assert _read_via_validate_examples(path) == expected


# --------------------------------------------------------------------------
# Structural half
# --------------------------------------------------------------------------
#: Keys the Phase 8 envelope moved off the top level. A hand-parse of one of
#: these against a snapshot document is what the class is about.
_MOVED_KEYS = frozenset(
    {
        "build_source",
        "dump_provenance",
        "declarations",
        "types",
        "enums",
    }
)

#: Trees whose files read snapshot documents from outside `abicheck/`.
_SCANNED_ROOTS = ("actions", "contrib", "scripts", "validation", "tests")

#: Sites that read one of `_MOVED_KEYS` off a dict that is NOT a snapshot
#: document some `abicheck dump` wrote (a hand-built fixture, a pack dict, a
#: report), so the envelope never applies. Entries are either a whole file or
#: one ``path::function``; each is a reviewed decision, not a mute.
_NOT_SNAPSHOT_DOCUMENT_READERS = frozenset(
    {
        # Writes the flat document itself and reads back its own bytes to
        # prove the *storage* layer preserves an unknown top-level key across
        # encodings. Never sees an envelope, by construction.
        "tests/test_snapshot_compression.py::"
        "test_dump_provenance_survives_load_save_round_trip",
        # Unit tests asserting on `snapshot_to_dict()`'s own in-memory flat
        # output — the function under test returns the flat shape by
        # definition, so there is no envelope to unwrap.
        "tests/test_build_source_pack.py",
        "tests/test_baseline_set.py",
        "tests/test_inputs_pack.py",
        "tests/test_surface_graph_codec.py",
        "tests/test_baseline_manifest.py",
        "tests/test_dump_write_after_resolve_time_embed.py",
        # This file: its whole job is to exercise both encodings.
        "tests/test_snapshot_envelope_out_of_band_readers.py",
    }
)

_UNWRAP_NAMES = frozenset(
    {"is_sectioned_document", "from_sectioned_document", "_unwrap_snapshot_envelope"}
)


def _moved_key_receivers(node: ast.AST) -> set[tuple[str, str]]:
    """``(receiver, key)`` for each formerly-top-level key *node* indexes.

    The *receiver* is what is being indexed, which is the half that matters:
    the question is not whether an unwrap happens somewhere in the function
    but whether *this* access reads an unwrapped document. A receiver that
    is not a plain name is reported as ``"<expr>"`` so it can never be
    matched against the set of unwrapped names — conservative by design.
    """
    found: set[tuple[str, str]] = set()

    def receiver(value: ast.expr) -> str:
        return value.id if isinstance(value, ast.Name) else "<expr>"

    for child in ast.walk(node):
        if (
            isinstance(child, ast.Subscript)
            and isinstance(child.slice, ast.Constant)
            and child.slice.value in _MOVED_KEYS
        ):
            found.add((receiver(child.value), str(child.slice.value)))
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and child.args[0].value in _MOVED_KEYS
        ):
            found.add((receiver(child.func.value), str(child.args[0].value)))
    return found


def _calls_unwrap(node: ast.AST) -> bool:
    """Whether evaluating *node* runs an unwrap."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in _UNWRAP_NAMES:
                return True
            if isinstance(func, ast.Attribute) and func.attr in _UNWRAP_NAMES:
                return True
    return False


def _unwrapped_names(node: ast.AST) -> set[str]:
    """Names within *node* bound to the *result* of an unwrap.

    Merely naming an unwrap somewhere in the function is not enough, and
    treating it as enough is what CodeRabbit caught: a reader can call
    ``_unwrap_snapshot_envelope(raw)`` and then still index ``raw``, and the
    scan would wave it through while sectioned snapshots kept reading as
    missing data.

    Both real shapes bind the result: ``x = unwrap(raw)`` (as
    ``scan_flow.py`` does) and the in-place ``raw = from_sectioned_document(
    raw)`` rebind under an ``is_sectioned_document`` guard (as
    ``actions/baseline/build_manifest.py`` does), which makes ``raw`` itself
    safe from that point. A conditional expression
    (``from_sectioned_document(d) if is_sectioned_document(d) else d``)
    counts too, since every branch yields an unwrapped document.
    """
    bound: set[str] = set()
    for child in ast.walk(node):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(child, ast.Assign):
            targets, value = list(child.targets), child.value
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [child.target], child.value
        elif isinstance(child, ast.NamedExpr):
            targets, value = [child.target], child.value
        if value is None or not _calls_unwrap(value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


def _reads_a_file(node: ast.AST) -> bool:
    """Whether *node* looks like it parses a document off disk itself."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in {
            "read_text",
            "read_bytes",
            "read_snapshot_bytes",
        }:
            return True
        if isinstance(child, ast.Name) and child.id in {"read_snapshot_bytes", "open"}:
            return True
    return False


class TestNoUnguardedOutOfBandReader:
    """What keeps the class closed against a *new* reader."""

    def test_every_file_reading_a_snapshot_document_unwraps_the_envelope(self) -> None:
        offenders: list[str] = []
        for root in _SCANNED_ROOTS:
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in _NOT_SNAPSHOT_DOCUMENT_READERS or "__pycache__" in rel:
                    continue
                seen_in_file = rel
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (OSError, SyntaxError):
                    continue
                for func in ast.walk(tree):
                    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    accesses = _moved_key_receivers(func)
                    if not accesses:
                        continue
                    if not _reads_a_file(func):
                        # Operating on an already-unwrapped/in-memory dict
                        # handed in by a caller; not this class.
                        continue
                    site = f"{seen_in_file}::{func.name}"
                    if site in _NOT_SNAPSHOT_DOCUMENT_READERS:
                        continue
                    unwrapped = _unwrapped_names(func)
                    unguarded = sorted(
                        f"{recv}[{key!r}]"
                        for recv, key in accesses
                        if recv not in unwrapped
                    )
                    if unguarded:
                        offenders.append(f"{site} -> {', '.join(unguarded)}")

        assert not offenders, (
            "these functions parse a snapshot document off disk and index a "
            "key the ADR-063 Phase 8 envelope moved under sections/, without "
            "unwrapping it first — they will read None for every real "
            f"`abicheck dump` output: {offenders}"
        )
