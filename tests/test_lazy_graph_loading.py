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

"""Lazy graph-section loading (evidence-entity-model Phase 5a;
storage-format-v2 Phase 2, A2.1).

The contract: a stored snapshot's graph is decoded on first access, once,
and every observable behavior -- equality, aliasing, pickling, save bytes,
errors -- is what eager decoding gives. Each "eager" configuration below is
produced by *forcing* the access before the operation under test, and each
test asserts which configuration actually ran (``is_graph_decoded``), per
AGENTS.md's "a differential test must prove both of its configurations
actually ran".
"""

from __future__ import annotations

import copy
import json
import pickle
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.lazy_graph import PendingGraph, is_graph_decoded
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import load_snapshot, save_snapshot, snapshot_from_dict
from abicheck.storage.graph_table_codec import decode_graph_table
from abicheck.storage.sectioned_document import from_sectioned_document
from abicheck.storage.snapshot_encode import snapshot_to_json


def _graph(tag: str = "") -> SourceGraphSummary:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="header:///inc/a.h", kind="header", label="/inc/a.h"))
    g.add_node(
        GraphNode(
            id=f"decl://f{tag}", kind="source_decl", label=f"f{tag}", attrs={"n": 1}
        )
    )
    g.add_edge(
        GraphEdge(src="header:///inc/a.h", dst=f"decl://f{tag}", kind="SOURCE_DECLARES")
    )
    g.add_edge(
        GraphEdge(
            src=f"decl://f{tag}",
            dst="decl://g",
            kind="DECL_HAS_TYPE",
            attrs={"role": "param"},
        )
    )
    g.extractor_passes["header_type_graph"] = True
    return g.finalize()


def _snapshot(tag: str = "") -> AbiSnapshot:
    graph = _graph(tag)
    snap = AbiSnapshot(library="libfoo.so", version="1.0", surface_graph=graph)
    snap.build_source = BuildSourcePack(root=Path(""), source_graph=graph)
    return snap


def _stored(tmp_path: Path, tag: str = "", name: str = "s.json") -> Path:
    path = tmp_path / name
    save_snapshot(_snapshot(tag), path)
    return path


# ── the PendingGraph primitive ───────────────────────────────────────────


class TestPendingGraphContract:
    def test_decoder_runs_once_under_concurrent_first_access(self) -> None:
        calls = []
        barrier = threading.Barrier(8)

        def decode() -> object:
            calls.append(1)
            return object()

        cell = PendingGraph(decode)
        results: list[object] = []

        def reader() -> None:
            barrier.wait()
            results.append(cell.resolve())

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1
        assert len({id(r) for r in results}) == 1

    @pytest.mark.parametrize(
        "error", [ValueError("bad"), KeyError("id"), TypeError("x")]
    )
    def test_failure_raises_on_every_access_never_empty(self, error: Exception) -> None:
        def decode() -> object:
            raise error

        cell = PendingGraph(decode)
        for _ in range(3):
            with pytest.raises(type(error)):
                cell.resolve()
        assert not cell.decoded

    def test_pickle_carries_the_decoded_value_not_the_decoder(self) -> None:
        cell = PendingGraph(lambda: {"v": 1})
        clone = pickle.loads(pickle.dumps(cell))  # nosec B301 - round-trips bytes this test just produced
        assert clone.decoded and clone.resolve() == {"v": 1}


# ── snapshot-level behavior ──────────────────────────────────────────────


class TestLoadedSnapshotIsLazy:
    def test_load_does_not_decode(self, tmp_path: Path) -> None:
        snap = load_snapshot(_stored(tmp_path))
        assert not is_graph_decoded(snap, "surface_graph")
        assert snap.build_source is not None
        assert not is_graph_decoded(snap.build_source, "source_graph")

    def test_first_access_matches_eager_decode(self, tmp_path: Path) -> None:
        path = _stored(tmp_path)
        snap = load_snapshot(path)
        graph = snap.surface_graph
        assert is_graph_decoded(snap, "surface_graph")
        expected = SourceGraphSummary.from_dict(_graph().to_dict())
        assert isinstance(graph, SourceGraphSummary)
        assert graph.to_dict() == expected.to_dict()

    def test_alias_resolves_to_the_identical_object(self, tmp_path: Path) -> None:
        snap = load_snapshot(_stored(tmp_path))
        assert snap.build_source is not None
        # Access the alias first: the other path must still see the same object.
        via_pack = snap.build_source.source_graph
        assert via_pack is snap.surface_graph

    @pytest.mark.parametrize("first_access", ["none", "surface", "pack"])
    def test_equality_matches_an_eagerly_decoded_load(
        self, tmp_path: Path, first_access: str
    ) -> None:
        path = _stored(tmp_path)
        lazy = load_snapshot(path)
        if first_access == "surface":
            _ = lazy.surface_graph
        elif first_access == "pack":
            assert lazy.build_source is not None
            _ = lazy.build_source.source_graph
        eager = load_snapshot(path)
        _ = eager.surface_graph
        assert is_graph_decoded(eager, "surface_graph")
        assert lazy == eager
        different = load_snapshot(_stored(tmp_path, tag="X", name="other.json"))
        assert lazy != different

    def test_pickle_round_trip_resolves(self, tmp_path: Path) -> None:
        lazy = load_snapshot(_stored(tmp_path))
        clone = pickle.loads(pickle.dumps(lazy))  # nosec B301 - round-trips bytes this test just produced
        assert is_graph_decoded(clone, "surface_graph")
        assert clone.build_source is not None
        assert clone.build_source.source_graph is clone.surface_graph
        assert clone == lazy

    @pytest.mark.parametrize("decode_first", [False, True])
    def test_deepcopy_keeps_laziness_alias_and_independence(
        self, tmp_path: Path, decode_first: bool
    ) -> None:
        lazy = load_snapshot(_stored(tmp_path))
        if decode_first:
            _ = lazy.surface_graph
        clone = copy.deepcopy(lazy)
        assert is_graph_decoded(clone, "surface_graph") is decode_first
        assert clone.build_source is not None
        assert clone.build_source.source_graph is clone.surface_graph
        assert clone == lazy
        assert clone.surface_graph is not lazy.surface_graph

    def test_shallow_copy_shares_the_cell(self, tmp_path: Path) -> None:
        lazy = load_snapshot(_stored(tmp_path))
        shallow = copy.copy(lazy)
        assert shallow.surface_graph is lazy.surface_graph

    def test_dump_load_save_is_byte_identical_lazy_or_eager(
        self, tmp_path: Path
    ) -> None:
        path = _stored(tmp_path)
        lazy, eager = load_snapshot(path), load_snapshot(path)
        _ = eager.surface_graph
        assert not is_graph_decoded(lazy, "surface_graph")
        lazy_bytes = snapshot_to_json(lazy)
        eager_bytes = snapshot_to_json(eager)
        assert lazy_bytes == eager_bytes == path.read_text(encoding="utf-8")

    def test_setting_the_attribute_replaces_the_pending_cell(
        self, tmp_path: Path
    ) -> None:
        snap = load_snapshot(_stored(tmp_path))
        snap.surface_graph = None
        assert snap.surface_graph is None
        # The pack's own alias is independent of the rebind, as before.
        assert snap.build_source is not None
        assert isinstance(snap.build_source.source_graph, SourceGraphSummary)


# ── a corrupt graph section ──────────────────────────────────────────────


def _corrupt(path: Path, mutate) -> dict:  # type: ignore[no-untyped-def]
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc["sections"]["graph"])
    return doc


_CORRUPTIONS = {
    "payload_not_a_mapping": lambda s: s["payload"].__setitem__("surface_graph", []),
    "wrong_section_kind": lambda s: s.__setitem__("section_kind", "types"),
    "extra_payload_key": lambda s: s["payload"].__setitem__("junk", 1),
    "node_id_out_of_range": lambda s: s["payload"]["surface_graph"]["nodes"][
        "id"
    ].__setitem__(0, 10**6),
}


class TestCorruptSection:
    @pytest.mark.parametrize("case", sorted(_CORRUPTIONS))
    def test_error_surfaces_at_access_and_matches_eager(
        self, tmp_path: Path, case: str
    ) -> None:
        doc = _corrupt(_stored(tmp_path), _CORRUPTIONS[case])
        # The eager reference: the same document decoded without deferral.
        with pytest.raises(Exception) as eager:
            flat = from_sectioned_document(doc, defer_graph=False)
            decode_graph_table(flat["surface_graph"])
        snap = snapshot_from_dict(doc)  # loading itself succeeds
        for _ in range(2):  # never degrades into an empty/None graph
            with pytest.raises(type(eager.value)) as lazy:
                _ = snap.surface_graph
            assert str(lazy.value) == str(eager.value)


# ── decoder engagement through the real compare CLI ─────────────────────


class _DecodeSpy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        original = SourceGraphSummary.from_dict.__func__  # type: ignore[attr-defined]

        def spy(cls, d):  # type: ignore[no-untyped-def]
            self.calls += 1
            return original(cls, d)

        monkeypatch.setattr(SourceGraphSummary, "from_dict", classmethod(spy))


def _compare(old: Path, new: Path, *extra: str) -> int:
    from abicheck.cli import main

    result = CliRunner().invoke(main, ["compare", str(old), str(new), *extra])
    # 0/2/4 are verdicts; anything else (64 usage, a crash) means the run
    # never reached the comparison and the spy would observe nothing.
    assert result.exit_code in (0, 1, 2, 4), result.output
    return result.exit_code


class TestCompareDecodesOnlyWhenTheGraphIsRead:
    def test_depth_projected_compare_never_decodes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = _stored(tmp_path, name="old.json")
        new = _stored(tmp_path, tag="2", name="new.json")
        spy = _DecodeSpy(monkeypatch)
        _compare(old, new, "--depth", "binary")
        assert spy.calls == 0

    def test_default_compare_decodes_each_side_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The sibling configuration, in the same module and fixture: proves
        # the spy observes decoding when the compare does read the graph (the
        # L5 source-graph diff, cross-source checks, assurance and digest all
        # do on a default compare -- see evidence-entity-model Phase 5a).
        old = _stored(tmp_path, name="old.json")
        new = _stored(tmp_path, tag="2", name="new.json")
        spy = _DecodeSpy(monkeypatch)
        _compare(old, new)
        assert spy.calls == 2


# ── eager vs. lazy: identical compare JSON on real header-graph pairs ────

_FIXTURES = Path(__file__).parent / "fixtures" / "header_graph"
_CASES = sorted({p.name.split("__")[0] for p in _FIXTURES.glob("*__v1.json")})


def _report(
    tmp_path: Path, case: str, mode: str, monkeypatch: pytest.MonkeyPatch
) -> str:
    from abicheck.storage import snapshot_codec

    original = snapshot_codec.decode_surface_graph
    observed: list[bool] = []

    def decode(d, snap):  # type: ignore[no-untyped-def]
        original(d, snap)
        if mode == "eager":
            _ = snap.surface_graph
            if snap.build_source is not None:
                _ = snap.build_source.source_graph
        observed.append(is_graph_decoded(snap, "surface_graph"))

    monkeypatch.setattr(snapshot_codec, "decode_surface_graph", decode)
    out = tmp_path / f"{case}-{mode}.json"
    _compare(
        _FIXTURES / f"{case}__v1.json",
        _FIXTURES / f"{case}__v2.json",
        "-o",
        f"json={out}",
    )
    monkeypatch.setattr(snapshot_codec, "decode_surface_graph", original)
    # Both sides went through the patched decode, in the intended mode.
    assert observed == [mode == "eager"] * 2
    return out.read_text(encoding="utf-8")


def test_fixture_corpus_is_not_vacuous() -> None:
    assert len(_CASES) >= 4
    for case in _CASES:
        doc = json.loads((_FIXTURES / f"{case}__v1.json").read_text(encoding="utf-8"))
        assert doc["sections"]["graph"]["payload"]["surface_graph"]["edges"]


_VOLATILE = ("duration", "seconds", "timestamp", "generated_at", "elapsed")


def _stable(value):  # type: ignore[no-untyped-def]
    """The report minus wall-clock readings, which differ run to run."""
    if isinstance(value, dict):
        return {
            k: _stable(v)
            for k, v in value.items()
            if not any(token in k for token in _VOLATILE)
        }
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


@pytest.mark.parametrize("case", _CASES)
def test_eager_and_lazy_compare_json_are_identical(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    eager = _report(tmp_path, case, "eager", monkeypatch)
    lazy = _report(tmp_path, case, "lazy", monkeypatch)
    assert _stable(json.loads(eager)) == _stable(json.loads(lazy))
    assert json.loads(lazy)["changes"]  # a real diff, not two empty reports


class TestLazyFieldGuards:
    def test_install_refuses_a_field_with_a_non_none_default(self) -> None:
        from dataclasses import dataclass

        from abicheck.model.lazy_graph import install_lazy_graph_field

        @dataclass
        class Holder:
            graph: object = 1

        with pytest.raises(TypeError, match="must default to None"):
            install_lazy_graph_field(Holder, "graph")

    def test_pending_cell_only_goes_on_a_lazy_field(self) -> None:
        from abicheck.model.lazy_graph import set_pending_graph

        snap = AbiSnapshot(library="l.so", version="1")
        with pytest.raises(TypeError, match="not a lazy graph field"):
            set_pending_graph(snap, "functions", PendingGraph(lambda: None))

    def test_repr_names_the_state_without_decoding(self) -> None:
        calls: list[int] = []
        cell = PendingGraph(lambda: calls.append(1))
        assert repr(cell) == "<PendingGraph pending>"
        cell.resolve()
        assert repr(cell) == "<PendingGraph decoded>" and calls == [1]

    def test_a_resolved_cell_never_runs_a_decoder(self) -> None:
        cell = PendingGraph.resolved("g")
        assert cell.decoded and cell.resolve() == "g"
        from abicheck.model.lazy_graph import _no_decoder

        with pytest.raises(RuntimeError):
            _no_decoder()


def test_a_flat_input_document_mutated_after_load_does_not_change_the_graph() -> None:
    from abicheck.serialization import snapshot_to_dict

    d = snapshot_to_dict(_snapshot())
    snap = snapshot_from_dict(d)
    d["surface_graph"]["strings"][:] = ["mutated"] * len(d["surface_graph"]["strings"])
    graph = snap.surface_graph
    assert isinstance(graph, SourceGraphSummary)
    assert graph.has_node("header:///inc/a.h")


class TestPendingGraphEdgeBranches:
    """The defensive and racing branches of ``PendingGraph``/``LazyGraphField``."""

    def test_a_reader_blocked_behind_a_finishing_decode_reuses_its_value(self) -> None:
        import threading

        from abicheck.model.lazy_graph import PendingGraph

        calls: list[int] = []
        cell = PendingGraph(lambda: calls.append(1) or "decoded")
        real = cell._lock
        waiting = threading.Event()

        class _SignallingLock:
            """Signals once a reader *attempts* the lock, then blocks as usual."""

            def __enter__(self) -> bool:
                if threading.current_thread() is not threading.main_thread():
                    waiting.set()
                return real.__enter__()

            def __exit__(self, *exc: object) -> None:
                real.__exit__(*exc)

        out: list[object] = []
        with real:  # the "other" decoder holds the lock ...
            cell._lock = _SignallingLock()  # type: ignore[assignment]
            reader = threading.Thread(target=lambda: out.append(cell.resolve()))
            reader.start()
            assert waiting.wait(5), "reader never reached the lock"
            assert not cell.decoded  # it passed the unlocked fast path while pending
            cell._value, cell._done = "by-the-other-thread", True  # ... and finishes
        reader.join(5)
        assert out == ["by-the-other-thread"]
        assert calls == []  # the blocked reader did not decode again

    @pytest.mark.parametrize("op", ["resolve", "deepcopy"])
    def test_a_pending_cell_without_a_decoder_raises_not_empty(self, op: str) -> None:
        import copy

        from abicheck.model.lazy_graph import PendingGraph

        cell = PendingGraph(lambda: "x")
        cell._decoder = None  # corrupted state: pending, nothing to run
        with pytest.raises(RuntimeError, match="lost its decoder"):
            cell.resolve() if op == "resolve" else copy.deepcopy(cell)

    def test_descriptor_declared_in_a_class_body_names_itself(self) -> None:
        from abicheck.model.lazy_graph import LazyGraphField, PendingGraph

        class Holder:
            graph = LazyGraphField("placeholder")

        h = Holder()
        h.__dict__["graph"] = PendingGraph(lambda: "g")
        assert Holder.graph is None  # class access: the field default
        assert h.graph == "g"  # __set_name__ rebound it to "graph"
