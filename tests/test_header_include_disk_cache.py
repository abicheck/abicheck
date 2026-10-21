"""The header-only ``clang -M`` include pass persists its per-header results.

G31 Phase D: a fresh process used to re-run one ``clang -M`` per top-level
header even when nothing had changed. These tests drive the real
:class:`ClangHeaderIncludeExtractor` through a strict scripted runner in
place of ``deadline.run_bounded``, so every probe is counted and an
unexpected one fails the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _strict_process import StrictProcessRunner, proc

import abicheck.buildsource.header_include_disk_cache as disk
import abicheck.buildsource.include_graph as ig
import abicheck.buildsource.include_graph_workers as igw
from abicheck.buildsource.header_graph import ClangHeaderIncludeExtractor
from abicheck.buildsource.header_include_memo import clear_include_memo
from abicheck.model.graph_identity import header_node_id


@pytest.fixture
def cache_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A private on-disk cache, so entries never leak between tests.

    Created beside, not inside, each test's ``tmp_path``: the include search
    directory's mtime is part of every entry's witness, so a cache written
    under it would invalidate its own entries.
    """
    root = tmp_path_factory.mktemp("include-cache")
    monkeypatch.setattr(disk, "include_cache_dir", lambda: root)
    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    return root


def _headers(tmp_path: Path) -> tuple[Path, Path, Path]:
    pub = tmp_path / "pub.h"
    pub.write_text('#include "detail/impl.h"\n')
    impl = tmp_path / "detail" / "impl.h"
    impl.parent.mkdir()
    impl.write_text("struct Impl {};\n")
    other = tmp_path / "other.h"
    other.write_text("void g();\n")
    return pub, impl, other


def _probe(header: Path, *deps: Path):
    """The scripted ``clang -M`` depfile for *header* listing *deps*."""
    stdout = f"{header.name}.o: {header} " + " ".join(str(d) for d in deps)
    return proc(stdout=stdout)


def _extract(headers: list[Path], tmp_path: Path):
    return ClangHeaderIncludeExtractor().extract(
        [str(h) for h in headers], [str(tmp_path)]
    )


def test_second_run_issues_no_clang_dash_m_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    pub, impl, other = _headers(tmp_path)
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
        .expect(argv_contains=[str(other)], returns=_probe(other))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    first, first_diags = _extract([pub, other], tmp_path)
    # Drop the process memo so only the disk entries can serve the second run.
    clear_include_memo()
    second, second_diags = _extract([pub, other], tmp_path)

    assert first_diags == second_diags == []
    assert first == second == {header_node_id(str(pub)): [str(impl)]}
    # Two probes for the cold run and none for the warm one: the script has
    # no expectations left, so any further `clang -M` would have raised.
    runner.assert_exhausted()
    runner.assert_call_count(2)


def test_changing_header_content_invalidates_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    pub, impl, _other = _headers(tmp_path)
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
        .expect(argv_contains=[str(pub)], returns=_probe(pub))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    first, _ = _extract([pub], tmp_path)
    assert first == {header_node_id(str(pub)): [str(impl)]}

    # The header now includes nothing: its content digest changed, so the
    # stale entry is not keyed on and the probe runs again.
    pub.write_text("void f();\n")
    clear_include_memo()
    second, _ = _extract([pub], tmp_path)

    assert second == {}
    runner.assert_exhausted()
    runner.assert_call_count(2)


def test_changing_an_included_file_invalidates_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    # The header's own digest is unchanged, so only the witness over the files
    # its closure reports can notice the edit to impl.h.
    pub, impl, _other = _headers(tmp_path)
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    _extract([pub], tmp_path)
    impl.write_text("struct Impl { int changed; };\n")
    clear_include_memo()
    second, _ = _extract([pub], tmp_path)

    assert second == {header_node_id(str(pub)): [str(impl)]}
    runner.assert_exhausted()
    runner.assert_call_count(2)


def test_a_failed_probe_is_not_stored_and_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    pub, impl, _other = _headers(tmp_path)
    runner = (
        StrictProcessRunner()
        .expect(
            argv_contains=[str(pub)],
            returns=proc(stderr="error: transient", returncode=1),
        )
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    failed, failed_diags = _extract([pub], tmp_path)
    clear_include_memo()
    recovered, recovered_diags = _extract([pub], tmp_path)

    assert failed == {}
    assert failed_diags
    # The failure was not persisted, so the second run probed again and kept
    # the real closure instead of replaying the empty partial result.
    assert recovered == {header_node_id(str(pub)): [str(impl)]}
    assert recovered_diags == []
    runner.assert_exhausted()
    runner.assert_call_count(2)


def test_reference_mode_bypasses_the_disk_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    pub, impl, _other = _headers(tmp_path)
    monkeypatch.setenv("ABICHECK_REFERENCE_MODE", "1")
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
        .expect(argv_contains=[str(pub)], returns=_probe(pub, impl))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    _extract([pub], tmp_path)
    _extract([pub], tmp_path)

    # Every run probes, and nothing was written under the cache root.
    runner.assert_exhausted()
    runner.assert_call_count(2)
    assert not cache_root.exists() or not any(cache_root.iterdir())


def test_a_file_shadowing_an_earlier_search_root_invalidates_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    # -I a -I b: the entry resolves detail/impl.h under b. A new a/detail/impl.h
    # changes a/detail's contents, not a's mtime, so only a witness on the
    # candidate directory a/detail can notice it.
    pub = tmp_path / "pub.h"
    pub.write_text('#include "detail/impl.h"\n')
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_b / "detail").mkdir(parents=True)
    # a/detail already exists, so a later impl.h changes only a/detail's own
    # contents and mtime, not a's.
    (root_a / "detail").mkdir(parents=True)
    (root_a / "detail" / "other.h").write_text("struct O {};\n")
    b_impl = root_b / "detail" / "impl.h"
    b_impl.write_text("struct B {};\n")
    a_impl = root_a / "detail" / "impl.h"
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, b_impl))
        .expect(argv_contains=[str(pub)], returns=_probe(pub, a_impl))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    first, _ = _extract_in([pub], [str(root_a), str(root_b)])
    assert first == {header_node_id(str(pub)): [str(b_impl)]}

    a_impl.write_text("struct A {};\n")
    clear_include_memo()
    second, _ = _extract_in([pub], [str(root_a), str(root_b)])

    assert second == {header_node_id(str(pub)): [str(a_impl)]}
    runner.assert_exhausted()
    runner.assert_call_count(2)


def test_a_new_file_in_the_includers_directory_invalidates_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    # A quoted #include searches the including file's own directory first, so
    # a sub/impl.h created after caching must win over root/impl.h.
    sub = tmp_path / "sub"
    root = tmp_path / "root"
    sub.mkdir()
    root.mkdir()
    pub = sub / "pub.h"
    pub.write_text('#include "impl.h"\n')
    root_impl = root / "impl.h"
    root_impl.write_text("struct R {};\n")
    local_impl = sub / "impl.h"
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(pub)], returns=_probe(pub, root_impl))
        .expect(argv_contains=[str(pub)], returns=_probe(pub, local_impl))
    ).install(monkeypatch, igw.deadline, "run_bounded")

    first, _ = _extract_in([pub], [str(root)])
    assert first == {header_node_id(str(pub)): [str(root_impl)]}

    local_impl.write_text("struct L {};\n")
    clear_include_memo()
    second, _ = _extract_in([pub], [str(root)])

    assert second == {header_node_id(str(pub)): [str(local_impl)]}
    runner.assert_exhausted()
    runner.assert_call_count(2)


def _extract_in(headers: list[Path], includes: list[str]):
    return ClangHeaderIncludeExtractor().extract([str(h) for h in headers], includes)


# -- edge branches: each one is a cache miss or a skipped write, never an error ---

_ORIGINAL_INCLUDE_CACHE_DIR = disk.include_cache_dir


def test_the_default_cache_dir_is_the_include_backend_under_xdg_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert _ORIGINAL_INCLUDE_CACHE_DIR() == tmp_path / "xdg" / "abi_check" / "include"


def test_an_unreadable_path_has_no_digest(tmp_path: Path) -> None:
    assert disk.file_digest(str(tmp_path / "missing.h")) is None
    assert disk.file_digest(str(tmp_path)) is None  # a directory, not a file


def test_a_missing_directory_is_witnessed_as_missing(tmp_path: Path) -> None:
    assert disk._dir_mtime(str(tmp_path / "absent")) == disk._MISSING_DIR


def test_witness_skips_a_root_on_another_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Windows a root on another drive makes commonpath raise; such a root
    # cannot contain the file, so only the includer's own directory is witnessed.
    def _other_drive(*_args: object) -> str:
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(disk.os.path, "commonpath", _other_drive)
    witnessed = disk.include_witness_dirs(
        [str(tmp_path / "sub" / "pub.h")], [str(tmp_path / "root")]
    )
    assert witnessed == [str(tmp_path / "sub")]


@pytest.mark.parametrize(
    "doc",
    [
        "not json",
        {
            "schema": "some-other-schema/9",
            "key": "KEY",
            "includes": [],
            "files": [],
            "dirs": [],
        },
        {"schema": disk.INCLUDE_CACHE_SCHEMA, "key": "KEY", "files": [], "dirs": []},
    ],
    ids=["unparseable", "foreign-schema", "missing-includes"],
)
def test_a_damaged_or_foreign_entry_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: object
) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(disk, "include_cache_dir", lambda: root)
    text = doc if isinstance(doc, str) else json.dumps(doc).replace("KEY", "k1")
    (root / "k1.json").write_text(text)
    assert disk.load_cached_includes("k1") is None


def test_a_closure_with_an_unreadable_file_is_not_stored(
    tmp_path: Path, cache_root: Path
) -> None:
    disk.store_cached_includes("k2", [str(tmp_path / "gone.h")], [str(tmp_path)])
    assert not (cache_root / "k2.json").exists()


def test_a_failed_entry_write_does_not_fail_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = tmp_path / "h.h"
    header.write_text("void f();\n")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # a file where the cache directory should be
    monkeypatch.setattr(disk, "include_cache_dir", lambda: blocker)
    # Must not raise: the cache is an optimisation, never a dependency.
    disk.store_cached_includes("k3", [str(header)], [str(tmp_path)])
    assert disk.load_cached_includes("k3") is None


def test_an_unreadable_header_is_probed_and_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_root: Path
) -> None:
    from abicheck.buildsource.build_evidence import CompileUnit

    missing = tmp_path / "gone.h"
    unit = CompileUnit(
        id=header_node_id(str(missing)),
        source=str(missing),
        argv=["-I" + str(tmp_path), str(missing)],
        language="CXX",
    )
    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    runner = (
        StrictProcessRunner()
        .expect(argv_contains=[str(missing)], returns=proc(stdout=f"gone.o: {missing}"))
        .install(monkeypatch, igw.deadline, "run_bounded")
    )

    found, diags = disk.cached_include_pass(
        [str(missing)],
        [unit],
        clang_bin="clang++",
        language="CXX",
        search_dirs=[str(tmp_path)],
    )

    assert diags == []
    assert found == {header_node_id(str(missing)): [str(missing)]}
    assert list(cache_root.iterdir()) == []
    runner.assert_exhausted()
    runner.assert_call_count(1)


def test_a_header_whose_own_path_cannot_be_resolved_keeps_its_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path as _Path

    from abicheck.buildsource.header_graph import _without_self

    def _cannot_resolve(self: _Path, *_args: object, **_kwargs: object) -> _Path:
        raise OSError("resolve failed")

    monkeypatch.setattr(_Path, "resolve", _cannot_resolve)
    header = str(tmp_path / "h.h")
    other = str(tmp_path / "o.h")
    # Neither side can be resolved, so no entry is recognised as the header's
    # own and the closure is kept whole.
    assert _without_self(header, [header, other]) == [header, other]
