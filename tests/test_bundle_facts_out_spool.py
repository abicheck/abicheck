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

"""``--bundle-facts-out`` keeps completed OLD members on disk, not resident.

Known gap "Multi-library compare memory: --bundle-facts-out still pins OLD
snapshots per member" (closed here). Each OLD member snapshot is encoded
into a content-addressed temp-dir spool (``storage.bundle_facts_spool``) as
its comparison finishes -- a stranded library as it is resolved -- and the
document is written from the spool, one blob at a time, in either form.

The bug class is *a completed member's graph retained until a late
consumer*, and the guards are the two halves that class needs:

* **Identical bytes**, against an oracle that is not the new path: the
  pre-existing eager writer, ``save_bundle_facts(capture_bundle_facts(...))``
  over the same snapshots held live, for the JSON document (plain and
  compressed) and the G40 archive, over several document shapes, and for
  *every* order the members could have finished in (an exhaustive domain of
  4! orders).
* **Bounded retention**, observed on the mechanism rather than the output:
  a ``weakref`` proves each member snapshot is reclaimable once spooled, and
  the write's ``tracemalloc`` peak does not grow with the member count.
"""

from __future__ import annotations

import gc
import gzip
import itertools
import json
import pickle
import random
import sys
import tracemalloc
import weakref
from pathlib import Path

import pytest

from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.checker_types import DiffResult
from abicheck.errors import ReleaseOperandUsageError
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.model.release_surface import PublicObligation, ReleasePublicSurface
from abicheck.serialization import load_bundle_facts, save_bundle_facts
from abicheck.storage.bundle_facts_spool import BlobSpool, SpooledBlob
from abicheck.storage.json_stream import LazyItems, PreEncoded, iter_json_indented
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts
from abicheck.workflows.release_bundle import (
    bundle_facts_out_format,
    write_bundle_facts_out,
)
from abicheck.workflows.release_scope import StrandedLibraryResolution
from abicheck.workflows.release_snapshot_retention import (
    release_old_member_snapshots,
    resolve_snapshot_retention,
    stash_member_evidence,
)

_NAMES = ("liba", "libb", "libc", "libd")


def _snap(lib: str, n: int = 12, salt: str = "") -> AbiSnapshot:
    """A member with text that exercises every escape the splice must keep:
    a quote, a backslash, an embedded newline, non-ASCII and a lone
    surrogate (``json`` writes all of them as escapes)."""
    odd = f'q"b\\n\nlé中\ud800{salt}'
    return AbiSnapshot(
        library=f"{lib}.so",
        version="1.0",
        functions=[
            Function(
                name=f"{lib}_fn{i}{odd if i == 0 else ''}",
                mangled=f"_Z{lib}fn{i}{salt}",
                return_type="int",
                params=[Param(name="a", type="const char *")],
                visibility=Visibility.PUBLIC,
            )
            for i in range(n)
        ],
        variables=[Variable(name=f"{lib}_v", mangled=f"{lib}_v", type="double")],
    )


def _release_dir(tmp_path: Path, names: tuple[str, ...]) -> dict[str, Path]:
    """``old_map`` over real files: each key a canonical ``.so`` name, each
    file versioned with an alias symlink, so alias/filename capture runs."""
    root = tmp_path / "old"
    root.mkdir(parents=True, exist_ok=True)
    old_map: dict[str, Path] = {}
    for name in names:
        real = root / f"{name}.so.1"
        real.write_bytes(b"\x7fELF-not-really")
        (root / f"{name}.so").symlink_to(real.name)
        old_map[f"{name}.so"] = real
    return old_map


def _surface() -> ReleasePublicSurface:
    return ReleasePublicSurface(
        acquisition_key="k",
        side="old",
        obligations=(PublicObligation(symbol="_Zf", name="f", entity="function"),),
        declared_symbols=frozenset({"_Zf"}),
    )


#: Document shapes: which optional blocks the capture carries, and how many
#: members are paired versus stranded (resolved at write time).
_SHAPES = {
    "all-paired": dict(paired=4, manifest=False, surface=False, degraded=False),
    "half-stranded": dict(paired=2, manifest=False, surface=False, degraded=False),
    "all-stranded": dict(paired=0, manifest=False, surface=False, degraded=False),
    "manifest+surface": dict(paired=3, manifest=True, surface=True, degraded=False),
    "degraded-stranded": dict(paired=2, manifest=True, surface=False, degraded=True),
}


def _inputs(tmp_path: Path, shape: str):
    spec = _SHAPES[shape]
    old_map = _release_dir(tmp_path, _NAMES)
    snaps = {f"{n}.so": _snap(n, salt=n) for n in _NAMES}
    paired = [f"{n}.so" for n in _NAMES[: spec["paired"]]]
    stranded_failure = "ELF-only stand-in: no headers" if spec["degraded"] else None
    by_path = {path: key for key, path in old_map.items()}

    def resolve(path: Path) -> StrandedLibraryResolution:
        return StrandedLibraryResolution(
            snapshot=snaps[by_path[path]], failure=stranded_failure
        )

    manifest = (
        InstantiationManifest(entries=(ManifestEntry(symbol="_Zliba_fn0"),))
        if spec["manifest"]
        else None
    )
    surface = _surface() if spec["surface"] else None
    # The eager oracle: the snapshots held live, through the pre-existing
    # capture and writer -- never through anything this change added.
    degraded = (
        {k: stranded_failure for k in old_map if k not in paired}
        if stranded_failure
        else {}
    )
    eager = capture_bundle_facts(
        {
            **{k: snaps[k] for k in paired},
            **{k: snaps[k] for k in old_map if k not in paired},
        },
        manifest=manifest,
        library_paths=dict(old_map),
        degraded_members=degraded,
        inventory_complete=True,
        public_surface=surface,
    )
    return old_map, snaps, paired, resolve, manifest, surface, eager


def _pairs(old_map, snaps, keys):
    """``(member file name, snapshot)`` -- the pair shape the comparison
    hands over (``release_old_member_snapshots``)."""
    return [(old_map[k].name, snaps[k]) for k in keys]


def _write_spooled(path, old_map, pairs, resolve, manifest, surface, spool=None):
    write_bundle_facts_out(
        path,
        pairs,
        None,
        old_map,
        resolve_stranded_library=resolve,
        resolved_manifest=manifest,
        inventory_complete=True,
        public_surface=surface,
        spool=spool,
    )


_FORMS = {
    # suffix -> the oracle's save_bundle_facts(format=...)
    ".json": "json",
    ".json.gz": "json",
    ".zip": "archive",
}


class TestByteIdentity:
    @pytest.mark.parametrize("shape", sorted(_SHAPES))
    @pytest.mark.parametrize("suffix", sorted(_FORMS))
    def test_spooled_write_equals_the_eager_writer(
        self, tmp_path: Path, shape: str, suffix: str
    ) -> None:
        old_map, snaps, paired, resolve, manifest, surface, eager = _inputs(
            tmp_path, shape
        )
        expected = tmp_path / f"eager{suffix}"
        save_bundle_facts(eager, expected, format=_FORMS[suffix])
        got = tmp_path / f"spooled{suffix}"
        _write_spooled(
            got, old_map, _pairs(old_map, snaps, paired), resolve, manifest, surface
        )
        assert got.read_bytes() == expected.read_bytes()

    @pytest.mark.parametrize("suffix", [".json", ".zip"])
    def test_no_completion_order_reaches_the_bytes(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """Every order the four members could finish in -- the whole domain
        -- spooled member by member, writes the eager document."""
        old_map, snaps, _paired, resolve, manifest, surface, _ = _inputs(
            tmp_path, "manifest+surface"
        )
        keys = list(old_map)
        eager = capture_bundle_facts(
            {k: snaps[k] for k in keys},
            manifest=manifest,
            library_paths=dict(old_map),
            inventory_complete=True,
            public_surface=surface,
        )
        expected = tmp_path / f"eager{suffix}"
        save_bundle_facts(eager, expected, format=_FORMS[suffix])
        want = expected.read_bytes()
        for order in itertools.permutations(keys):
            with BlobSpool(tmp_path) as spool:
                handles = {k: spool.put_snapshot(snaps[k]) for k in order}
                got = tmp_path / f"order{suffix}"
                # Pairs arrive in the release's own (deterministic) order;
                # only the spooling happened in completion order.
                _write_spooled(
                    got,
                    old_map,
                    [(old_map[k].name, handles[k]) for k in keys],
                    resolve,
                    manifest,
                    surface,
                    spool=spool,
                )
            assert got.read_bytes() == want, f"completion order {order} leaked"

    def test_live_and_spooled_pairs_mix(self, tmp_path: Path) -> None:
        """A member whose spooling failed during the run stays live in its
        entry; the writer spools it then, and the bytes are unchanged."""
        old_map, snaps, _p, resolve, manifest, surface, _ = _inputs(
            tmp_path, "all-paired"
        )
        keys = list(old_map)
        expected = tmp_path / "eager.json"
        save_bundle_facts(
            capture_bundle_facts(
                {k: snaps[k] for k in keys},
                library_paths=dict(old_map),
                inventory_complete=True,
            ),
            expected,
        )
        with BlobSpool(tmp_path) as spool:
            pairs = [
                (old_map[k].name, spool.put_snapshot(snaps[k]) if i % 2 else snaps[k])
                for i, k in enumerate(keys)
            ]
            got = tmp_path / "mixed.json"
            _write_spooled(got, old_map, pairs, resolve, None, None, spool=spool)
        assert got.read_bytes() == expected.read_bytes()

    def test_the_zip_form_round_trips_to_the_json_forms_facts(
        self, tmp_path: Path
    ) -> None:
        old_map, snaps, paired, resolve, manifest, surface, _ = _inputs(
            tmp_path, "degraded-stranded"
        )
        pairs = _pairs(old_map, snaps, paired)
        _write_spooled(tmp_path / "b.json", old_map, pairs, resolve, manifest, surface)
        _write_spooled(tmp_path / "b.zip", old_map, pairs, resolve, manifest, surface)
        assert (tmp_path / "b.zip").read_bytes()[:4] == b"PK\x03\x04"
        from_json = load_bundle_facts(tmp_path / "b.json")
        from_zip = load_bundle_facts(tmp_path / "b.zip")
        assert from_zip.degraded_members == from_json.degraded_members != {}
        assert set(from_zip.per_library_snapshots) == set(old_map)
        for key in old_map:
            assert [
                f.name
                for f in from_zip.per_library_snapshots[key].declarations.functions
            ] == [
                f.name
                for f in from_json.per_library_snapshots[key].declarations.functions
            ]


class TestFormSelection:
    @pytest.mark.parametrize(
        ("name", "form"),
        [
            ("b.zip", "archive"),
            ("b.ZIP", "archive"),
            ("b.json", "json"),
            ("b.json.gz", "json"),
            ("b.json.zst", "json"),
            ("b", "json"),
            ("zip", "json"),
            ("b.zip.json", "json"),
        ],
    )
    def test_only_a_zip_suffix_selects_the_archive(self, name: str, form: str) -> None:
        assert bundle_facts_out_format(Path(name)) == form

    def test_a_gz_document_is_gzip(self, tmp_path: Path) -> None:
        old_map, snaps, paired, resolve, *_ = _inputs(tmp_path, "all-paired")
        out = tmp_path / "b.json.gz"
        _write_spooled(
            out, old_map, _pairs(old_map, snaps, paired), resolve, None, None
        )
        assert json.loads(gzip.decompress(out.read_bytes()))["per_library_snapshots"]


class TestRetention:
    def test_a_spooled_member_leaves_no_snapshot_alive(self) -> None:
        """The whole point, observed through ``weakref``: after its member
        is stashed under a spooling retention, nothing keeps the OLD graph
        alive -- not the entry, not the evidence, not the spool."""
        with BlobSpool() as spool:
            retention = resolve_snapshot_retention(
                bundle_facts_out=True, old_spool=spool
            )
            entries = []
            refs = []
            for name in _NAMES:
                old, new = _snap(name), _snap(name)
                refs.append(weakref.ref(old))
                entry: dict[str, object] = {
                    "library": f"{name}.so",
                    "_diff_result": DiffResult(
                        library=f"{name}.so", old_version="1", new_version="2"
                    ),
                }
                stash_member_evidence(entry, f"{name}.so", old, new, retention)
                entries.append(entry)
                del old, new
            gc.collect()
            assert [r() for r in refs] == [None] * len(refs)
            pairs = release_old_member_snapshots(entries)
            assert [name for name, _ in pairs] == [f"{n}.so" for n in _NAMES]
            assert all(type(h) is SpooledBlob for _, h in pairs)
            assert all("_old_snapshot" not in e for e in entries)
            # The bundle layer's evidence is the compact form, as on a run
            # without --bundle-facts-out.
            assert all("_old_bundle_evidence" in e for e in entries)

    def test_without_a_spool_the_snapshot_stays_resident(self) -> None:
        """The complement: the probe above can fail. With no spool the
        retention keeps the live snapshot, and the weakref sees it."""
        retention = resolve_snapshot_retention(bundle_facts_out=True)
        old = _snap("liba")
        ref = weakref.ref(old)
        entry: dict[str, object] = {"library": "liba.so"}
        stash_member_evidence(entry, "liba.so", old, _snap("liba"), retention)
        del old
        gc.collect()
        assert ref() is entry["_old_snapshot"]

    def test_a_member_that_cannot_be_spooled_stays_live(self, tmp_path: Path) -> None:
        """A spool failure never fails the member's comparison: the
        snapshot stays resident and the writer reports it at write time."""
        spool = BlobSpool(tmp_path)
        spool.close()  # its directory is gone: every put fails
        retention = resolve_snapshot_retention(bundle_facts_out=True, old_spool=spool)
        old = _snap("liba")
        entry: dict[str, object] = {"library": "liba.so"}
        stash_member_evidence(entry, "liba.so", old, _snap("liba"), retention)
        assert entry["_old_snapshot"] is old
        assert "_old_snapshot_spooled" not in entry

    def test_stranded_libraries_are_resolved_one_at_a_time(
        self, tmp_path: Path
    ) -> None:
        """When the writer resolves stranded library *k*, every earlier
        stranded snapshot is already spooled and reclaimable."""
        old_map = _release_dir(tmp_path, _NAMES)
        refs: list[weakref.ref[AbiSnapshot]] = []
        alive_at_call: list[int] = []

        def resolve(path: Path) -> StrandedLibraryResolution:
            gc.collect()
            alive_at_call.append(sum(r() is not None for r in refs))
            snap = _snap(path.name)
            refs.append(weakref.ref(snap))
            return StrandedLibraryResolution(snapshot=snap, failure=None)

        _write_spooled(tmp_path / "b.json", old_map, [], resolve, None, None)
        assert alive_at_call == [0, 0, 0, 0]

    def test_write_peak_does_not_grow_with_member_count(self, tmp_path: Path) -> None:
        """Relative, not absolute: writing 8 spooled members peaks well below
        what holding 6 more members' payload would add over writing 2."""

        def peak_for(members: int, suffix: str) -> tuple[int, int]:
            names = tuple(f"lib{i}" for i in range(members))
            old_map = _release_dir(tmp_path / f"{members}{suffix}", names)
            with BlobSpool(tmp_path) as spool:
                handles = [
                    (
                        old_map[f"{n}.so"].name,
                        spool.put_snapshot(_snap(n, n=600, salt=n)),
                    )
                    for n in names
                ]
                payload = sum(h.length for _, h in handles)
                gc.collect()
                tracemalloc.start()
                tracemalloc.reset_peak()
                _write_spooled(
                    tmp_path / f"{members}{suffix}" / f"b{suffix}",
                    old_map,
                    handles,
                    lambda p: pytest.fail(f"{p} is paired, not stranded"),
                    None,
                    None,
                    spool=spool,
                )
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            return peak, payload

        for suffix in (".json", ".zip"):
            small_peak, small_payload = peak_for(2, suffix)
            large_peak, large_payload = peak_for(8, suffix)
            added_payload = large_payload - small_payload
            assert added_payload > 1_000_000, "the fixture must be big enough to see"
            assert large_peak - small_peak < added_payload * 0.25, (
                f"{suffix}: peak grew by {large_peak - small_peak} bytes for "
                f"{added_payload} bytes of extra members -- the write is "
                "holding members again"
            )


class TestSpool:
    def test_a_blob_is_the_archive_payload(self, tmp_path: Path) -> None:
        """Same bytes the G40 writer stores, under the same content hash."""
        import hashlib

        from abicheck.serialization import snapshot_to_dict
        from abicheck.storage.bundle_archive_json_guard import bounded_encode_utf8

        snap = _snap("liba")
        with BlobSpool(tmp_path) as spool:
            blob = spool.put_snapshot(snap)
            payload = bounded_encode_utf8(snapshot_to_dict(snap), 1 << 30)
            assert payload is not None
            assert spool.read_bytes(blob) == payload
            assert blob == SpooledBlob(
                hashlib.sha256(payload).hexdigest(), len(payload)
            )
            assert "".join(spool.iter_text(blob)).encode("ascii") == payload

    def test_the_directory_is_removed_on_exit(self, tmp_path: Path) -> None:
        with BlobSpool(tmp_path) as spool:
            spool.put_bytes(b"x")
            directory = spool.directory
            assert directory.is_dir()
        assert not directory.exists()

    def test_a_truncated_blob_is_refused(self, tmp_path: Path) -> None:
        with BlobSpool(tmp_path) as spool:
            blob = spool.put_bytes(b"0123456789")
            (spool.directory / blob.sha256).write_bytes(b"01234")
            with pytest.raises(OSError, match="spool directory was modified"):
                spool.read_bytes(blob)
            with pytest.raises(OSError, match="spool directory was modified"):
                "".join(spool.iter_text(blob))

    def test_another_process_spools_for_this_one(self, tmp_path: Path) -> None:
        """A member compared in a worker process (the release fan-out forks
        one per member) returns its handle in the entry; this process reads
        the blob through it. A real second interpreter, given the spool and
        returning the handle by pickle, the way an entry crosses back."""
        import subprocess

        child = (
            "import pickle, sys\n"
            "from abicheck.model import AbiSnapshot, Function\n"
            "spool = pickle.load(sys.stdin.buffer)\n"
            "snap = AbiSnapshot(library='liba.so', version='1', functions=["
            "Function(name='f', mangled='_Zf', return_type='int')])\n"
            "pickle.dump(spool.put_snapshot(snap), sys.stdout.buffer)\n"
        )
        with BlobSpool(tmp_path) as spool:
            done = subprocess.run(
                [sys.executable, "-c", child],
                input=pickle.dumps(spool),
                capture_output=True,
                check=True,
            )
            handle = pickle.loads(done.stdout)
            own = spool.put_snapshot(
                AbiSnapshot(
                    library="liba.so",
                    version="1",
                    functions=[Function(name="f", mangled="_Zf", return_type="int")],
                )
            )
            assert handle == own
            assert spool.read_bytes(handle) == spool.read_bytes(own)

    def test_a_failed_write_leaves_no_partial_blob(self, tmp_path: Path) -> None:
        def chunks():
            yield b"part"
            raise RuntimeError("producer failed")

        with BlobSpool(tmp_path) as spool:
            with pytest.raises(RuntimeError):
                spool._write(chunks())
            assert list(spool.directory.iterdir()) == []


class TestPreEncoded:
    """``PreEncoded`` splices ``json.dumps(value, indent=n)`` text at any
    depth, byte-identically to encoding *value* in place -- stated against
    ``json.dumps`` of the whole document as the oracle, over random
    documents."""

    @staticmethod
    def _value(rng: random.Random, depth: int) -> object:
        # Containers only above depth 2, and only one level may be wide
        # (> 64 elements, the encoder's batching threshold), so a document
        # stays small while still reaching both of the encoder's paths.
        kind = rng.randrange(7 if depth < 2 else 4)
        if kind == 0:
            return rng.choice([None, True, False, 0, -3, 1.5, 1e300])
        if kind == 1:
            return "".join(
                rng.choice('ab"\\\n\u00e9\ud800 \t') for _ in range(rng.randrange(6))
            )
        if kind == 2:
            return rng.randrange(-(10**20), 10**20)
        if kind == 3:
            return []
        width = rng.choice((1, 3, 70)) if depth == 0 else rng.randrange(1, 4)
        if kind == 4:
            return [TestPreEncoded._value(rng, depth + 1) for _ in range(width)]
        if kind == 5:
            return {
                f"k{i}\n": TestPreEncoded._value(rng, depth + 1) for i in range(width)
            }
        return {}

    @pytest.mark.parametrize("seed", range(30))
    @pytest.mark.parametrize("indent", [1, 2, 4])
    def test_splice_equals_encoding_in_place(self, seed: int, indent: int) -> None:
        rng = random.Random(seed)
        members = {f"m{i}": self._value(rng, 0) for i in range(rng.randrange(1, 5))}
        document = {"head": self._value(rng, 1), "members": members, "tail": [1, {}]}
        want = json.dumps(document, indent=indent)

        def chunked(text: str):
            def chunks():
                # Random chunk boundaries: the re-padding may not depend on
                # where a newline falls.
                i = 0
                while i < len(text):
                    step = rng.randrange(1, 40)
                    yield text[i : i + step]
                    i += step

            return chunks

        lazy = dict(document)
        lazy["members"] = LazyItems(
            keys=list(members),
            produce=lambda k: PreEncoded(
                chunked(json.dumps(members[k], indent=indent))
            ),
        )
        assert "".join(iter_json_indented(lazy, indent=indent)) == want
        # And nested inside an ordinary container, both narrow and wide.
        for width in (1, 80):
            wrapped = {
                "x": [PreEncoded(chunked(json.dumps(members, indent=indent)))] * width
            }
            assert "".join(iter_json_indented(wrapped, indent=indent)) == json.dumps(
                {"x": [members] * width}, indent=indent
            )


def test_an_unwritable_destination_is_the_usage_error(tmp_path: Path) -> None:
    old_map, snaps, paired, resolve, *_ = _inputs(tmp_path, "all-paired")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    for suffix in (".json", ".zip"):
        with pytest.raises(ReleaseOperandUsageError, match="--bundle-facts-out"):
            _write_spooled(
                blocker / f"b{suffix}",
                old_map,
                _pairs(old_map, snaps, paired),
                resolve,
                None,
                None,
            )


class TestCli:
    """The public workflow: ``compare``/``dump --bundle-facts-out`` over a
    release directory of stored member snapshots."""

    @staticmethod
    def _release(tmp_path: Path) -> tuple[Path, Path]:
        from abicheck.serialization import snapshot_to_json

        sides = {}
        for side, version in (("old", "1"), ("new", "2")):
            root = tmp_path / side
            root.mkdir()
            for name in _NAMES:
                snap = _snap(name, salt=name if side == "old" else f"{name}2")
                snap.version = version
                (root / f"{name}.json").write_text(
                    snapshot_to_json(snap), encoding="utf-8"
                )
            sides[side] = root
        return sides["old"], sides["new"]

    @pytest.mark.parametrize("command", ["compare", "dump"])
    def test_a_zip_path_writes_the_archive_of_the_json_document(
        self, tmp_path: Path, command: str
    ) -> None:
        """Oracle: the pre-existing archive writer applied to the facts the
        same command's JSON document holds. The base revision wrote the
        JSON document under the ``.zip`` name."""
        from click.testing import CliRunner

        from abicheck.cli import main

        old, new = self._release(tmp_path)
        written = {}
        for suffix in (".json", ".zip"):
            out = tmp_path / f"baseline{suffix}"
            argv = (
                ["compare", str(old), str(new)]
                if command == "compare"
                else ["dump", str(old)]
            )
            result = CliRunner().invoke(main, [*argv, "--bundle-facts-out", str(out)])
            # compare: every member's symbols were renamed, an ABI break (4).
            assert result.exit_code == (4 if command == "compare" else 0), result.output
            written[suffix] = out
        zip_bytes = written[".zip"].read_bytes()
        assert zip_bytes[:4] == b"PK\x03\x04"
        oracle = tmp_path / "oracle.zip"
        save_bundle_facts(load_bundle_facts(written[".json"]), oracle, format="archive")
        assert zip_bytes == oracle.read_bytes()
        members = set(load_bundle_facts(written[".zip"]).per_library_snapshots)
        assert len(members) == len(_NAMES)
        assert members == set(load_bundle_facts(written[".json"]).per_library_snapshots)
