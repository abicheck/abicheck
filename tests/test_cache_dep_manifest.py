# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``extract.cache_dep_manifest``: depfile parsing, manifest verification,
sidecars, recordings, and the cost bound that is the perf regression's guard.

Bug class ``cache.key_identity_must_equal_parse_input_content``. The cost
test pins the reason this module exists: validating a cache entry must
scale with the files the parse read, not with the system include tree
(the profiled regression digested ~4,400 files per key derivation).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.extract import cache_dep_manifest as m, cache_input_digest
from abicheck.storage import cache_integrity


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_input_digest, "_FILE_MEMO", {})
    monkeypatch.setattr(cache_input_digest, "_DIRTY", {})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a.o: a.h b.h\n", ["a.h", "b.h"]),
        ("a.o: a.h \\\n  /x/b.h \\\n  /y/c\n", ["a.h", "/x/b.h", "/y/c"]),
        ("a.o: a.h \\\r\n  b.h\r\n", ["a.h", "b.h"]),
        ("a.o: /dir\\ with\\ space/h.h other.h\n", ["/dir with space/h.h", "other.h"]),
        ("a.o: cost$$.h hash\\#.h\n", ["cost$.h", "hash#.h"]),
        ("a.o: C:\\inc\\x.h D:/y.h\n", ["C:\\inc\\x.h", "D:/y.h"]),
        ("C:\\out\\a.o: C:\\inc\\x.h\n", ["C:\\inc\\x.h"]),
        ("a.o: a.h b.h\nb.o: b.h c.h\n", ["a.h", "b.h", "c.h"]),
        ("a.o:\n", []),
        ("", []),
    ],
)
def test_parse_depfile(text: str, expected: list[str]) -> None:
    assert m.parse_depfile(text) == expected


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")
def test_parse_depfile_matches_real_clang_output(tmp_path: Path) -> None:
    """Oracle: the files the test itself created and included, under a
    directory whose name needs escaping."""
    inc = tmp_path / "dir with space"
    inc.mkdir()
    (inc / "one.h").write_text('#include "two.h"\n')
    (inc / "two.h").write_text("int two;\n")
    main = tmp_path / "main.h"
    main.write_text(f'#include "{inc / "one.h"}"\n')
    dep = tmp_path / "out.d"
    subprocess.run(
        ["clang", "-x", "c", "-fsyntax-only", *m.depfile_args(dep), str(main)],
        check=True,
    )
    got = m.read_depfile(dep)
    assert got is not None
    assert {os.path.realpath(p) for p in got} == {
        os.path.realpath(p) for p in (main, inc / "one.h", inc / "two.h")
    }


def _files(root: Path, n: int) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = root / f"h{i}.h"
        p.write_text(f"int h{i};\n")
        out.append(p)
    return out


def test_verify_tracks_content_presence_and_shadowing(tmp_path: Path) -> None:
    early, late = tmp_path / "early", tmp_path / "late"
    early.mkdir()
    a, b = _files(late, 2)
    built = m.build_manifest([str(a), str(b)], search_dirs=[str(early), str(late)])
    assert built is not None
    assert built.absent == {str(early / "h0.h"), str(early / "h1.h")}
    assert m.verify(built)
    os.utime(a, ns=(1, 1))  # touch only
    assert m.verify(built)
    (early / "h1.h").write_text("shadow\n")
    assert not m.verify(built)
    (early / "h1.h").unlink()
    a.write_text("changed\n")
    assert not m.verify(built)
    a.unlink()
    assert not m.verify(built)
    assert not m.verify(None)


def test_build_refuses_a_dependency_modified_after_the_parse_started(
    tmp_path: Path,
) -> None:
    (a,) = _files(tmp_path, 1)
    st = a.stat()
    assert m.build_manifest([str(a)], started_ns=st.st_mtime_ns) is None
    assert m.build_manifest([str(a)], started_ns=st.st_ctime_ns + 1) is not None
    assert m.build_manifest([str(tmp_path / "gone.h")]) is None


@pytest.mark.parametrize(
    "damage",
    ["missing", "not json", "wrong format", "wrong types", "files not a map"],
)
def test_missing_or_corrupt_sidecar_is_a_miss(tmp_path: Path, damage: str) -> None:
    (a,) = _files(tmp_path / "inc", 1)
    entry = tmp_path / "entry.json"
    manifest = m.build_manifest([str(a)])
    assert manifest is not None and m.write_sidecar(entry, manifest)
    assert m.entry_valid(entry)
    side = m.sidecar_path(entry)
    {
        "missing": side.unlink,
        "not json": lambda: side.write_text("{"),
        "wrong format": lambda: side.write_text('{"format":99,"files":{}}'),
        "wrong types": lambda: side.write_text(
            '{"format":1,"files":{"a":1},"absent":[],"trees":{}}'
        ),
        "files not a map": lambda: side.write_text(
            '{"format":1,"files":[],"absent":[],"trees":{}}'
        ),
    }[damage]()
    assert not m.entry_valid(entry)


def test_eviction_removes_the_manifest_with_its_entry(tmp_path: Path) -> None:
    entry = tmp_path / "k.json"
    entry.write_text("{}")
    assert m.write_sidecar(entry, m.DepManifest())
    cache_integrity.evict(entry)
    assert not m.sidecar_path(entry).exists()


def test_recording_unions_noted_parses_and_unknown_poisons_it(tmp_path: Path) -> None:
    a, b = _files(tmp_path, 2)
    ma = m.build_manifest([str(a)])
    mb = m.build_manifest([str(b)])
    assert ma is not None and mb is not None
    with m.recording() as outer:
        m.note(ma)
        with m.recording() as inner:
            m.note(mb)
    assert inner.manifest() == mb
    union = outer.manifest()
    assert union is not None and set(union.files) == {str(a), str(b)}
    with m.recording() as rec:
        m.note(ma)
        m.note(None)
    assert rec.manifest() is None
    m.note(ma)  # outside any recording: not retained
    assert m._JOURNAL == []


def test_union_refuses_disagreeing_digests() -> None:
    assert m.DepManifest({"/a": "1"}).union(m.DepManifest({"/a": "2"})) is None
    assert (
        m.DepManifest({"/a": "1"}).union(m.DepManifest({}, frozenset({"/a"}))) is None
    )


@pytest.mark.parametrize("tree_size", [200, 3000])
def test_validation_cost_scales_with_the_read_set_not_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree_size: int
) -> None:
    """The perf regression's guard: a parse that read 20 of *tree_size*
    system headers is validated with O(20) stats and O(20) digests, whatever
    the tree's size (the pre-manifest key digested every file in it)."""
    sysdir = tmp_path / "sys"
    files = _files(sysdir, tree_size)
    read = [str(p) for p in files[:20]]
    manifest = m.build_manifest(read, search_dirs=[str(sysdir)])
    assert manifest is not None

    stats = {"n": 0}
    digests = {"n": 0}
    real_stat, real_digest = os.stat, cache_input_digest.file_digest

    def counting_stat(*a, **k):
        stats["n"] += 1
        return real_stat(*a, **k)

    def counting_digest(p):
        digests["n"] += 1
        return real_digest(p)

    monkeypatch.setattr(os, "stat", counting_stat)
    monkeypatch.setattr(m, "file_digest", counting_digest)
    assert m.verify(manifest)
    assert digests["n"] == len(read)
    assert stats["n"] <= 2 * len(read)


@pytest.mark.parametrize("backend", ["clang", "castxml"])
def test_key_derivation_reads_nothing_under_the_system_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Key derivation (run ~3x per dump+attach) touches no file under a
    3,000-file system dir: the dirs enter by spelling, their headers through
    the manifest at lookup (above)."""
    from abicheck.extract.headers.ast_config import _cache_key
    from abicheck.workflows.dump.cache import key as snapshot_key

    sysdir = tmp_path / "sys"
    _files(sysdir, 3000)
    header = tmp_path / "proj" / "api.h"
    header.parent.mkdir()
    header.write_text("int f(void);\n")
    touched: list[str] = []
    real = cache_input_digest.file_digest

    def spy(p):
        touched.append(os.fspath(p))
        return real(p)

    monkeypatch.setattr(cache_input_digest, "file_digest", spy)
    monkeypatch.setattr(
        snapshot_key, "compiler_search_dirs", lambda _cc, cpp: (str(sysdir),)
    )
    _cache_key([header], [], "cc", backend=backend, system_includes=(str(sysdir),))
    snapshot_key.system_include_cache_field(["cc"], cpp=False)
    assert touched == [str(header.resolve())]
