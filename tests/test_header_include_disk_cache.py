"""The header-only ``clang -M`` include pass persists its per-header results.

G31 Phase D: a fresh process used to re-run one ``clang -M`` per top-level
header even when nothing had changed. These tests drive the real
:class:`ClangHeaderIncludeExtractor` through a strict scripted runner in
place of ``deadline.run_bounded``, so every probe is counted and an
unexpected one fails the test.
"""

from __future__ import annotations

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
