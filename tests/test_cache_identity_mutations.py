"""Cache identity is the content a parse can read (ADR-062 D11), for every key.

Bug class ``cache.key_identity_must_equal_parse_input_content``: a header-parse
cache key must change exactly when the bytes a parse could read change -- an
explicit header, any file under an include root whatever its suffix, a file
passed as an include, the directories ``CPATH``-style variables add -- or when
an input's spelling (its path) changes, and must *not* change for a
metadata-only touch. The compiler's built-in system directories enter the key
by spelling only: the system headers a parse read are validated by the
entry's dependency manifest instead (``extract.cache_dep_manifest``; see
``tests/test_cache_dep_manifest_clang.py``), so an edit there must *not*
move the key.

The oracle is defined from the inputs, not from the key code: it walks the
fixture tree with ``os.walk`` and records ``{(role, path, relpath): bytes}``
plus the environment values. A mutation is expected to bust a key iff that
state changes. Every mutation is applied to three keys: the whole-snapshot key
and the clang and castxml header-AST keys.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from abicheck.extract import cache_input_digest
from abicheck.extract.headers.ast_config import _cache_key as ast_cache_key
from abicheck.workflows.dump.cache import api, key as snapshot_key
from tests._dump_cache_request import dump_request

_VCS = {".git", ".hg", ".svn", ".bzr", "__pycache__"}


@dataclass
class Fixture:
    root: Path
    header: Path
    inc: Path
    inc_file: Path
    sysdir: Path
    envdir: Path
    binary: Path
    env_on: bool = False
    renamed: dict[str, Path] = field(default_factory=dict)

    def includes(self) -> list[Path]:
        return [self.renamed.get("inc", self.inc), self.inc_file]


def _build(tmp_path: Path) -> Fixture:
    root = tmp_path / "w"
    inc = root / "inc"
    (inc / "detail").mkdir(parents=True)
    (inc / ".git").mkdir()
    (inc / "api.h").write_text('#include "detail/x.h"\n')
    (inc / "detail" / "x.h").write_text("int x;\n")
    (inc / "detail" / "kinds.def").write_text("K(a)\n")
    (inc / "detail" / "vector").write_text("// no suffix\n")
    (inc / ".git" / "index").write_text("vcs\n")
    inc_file = root / "forced.cfg"
    inc_file.write_text("#define F 1\n")
    sysdir = root / "sys"
    sysdir.mkdir()
    (sysdir / "stdio.h").write_text("int printf(const char*, ...);\n")
    envdir = root / "envinc"
    envdir.mkdir()
    (envdir / "e.h").write_text("int e;\n")
    binary = root / "lib.so"
    binary.write_bytes(b"\x7fELF identity")
    old = 1_600_000_000_000_000_000
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            os.utime(os.path.join(dirpath, f), ns=(old, old))
    return Fixture(root, inc / "api.h", inc, inc_file, sysdir, envdir, binary)


def _oracle_state(fx: Fixture) -> dict[object, object]:
    """Everything a parse can read, from the inputs (``os.walk``)."""
    state: dict[object, object] = {("header", str(fx.header)): fx.header.read_bytes()}
    roots = [("inc", p) for p in fx.includes()]
    state[("sys", str(fx.sysdir))] = True  # spelling only (manifest-validated)
    if fx.env_on:
        roots.append(("env", fx.envdir))
    for role, r in roots:
        if r.is_file():
            state[(role, str(r))] = r.read_bytes()
            continue
        state[(role, str(r), "<root>")] = True
        for dirpath, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if d not in _VCS]
            for f in files:
                p = Path(dirpath, f)
                state[(role, str(r), str(p.relative_to(r)))] = p.read_bytes()
    state["CPATH"] = os.environ.get("CPATH", "")
    return state


def _edit(p: Path) -> None:
    p.write_text(p.read_text() + "// edited\n")


def _touch(p: Path) -> None:
    new = 1_800_000_000_000_000_000
    os.utime(p, ns=(new, new))


def _rename_inc(fx: Fixture, mp: pytest.MonkeyPatch) -> None:
    moved = fx.root / "inc_moved"
    fx.inc.rename(moved)
    fx.header = moved / "api.h"
    fx.renamed["inc"] = moved


def _env(fx: Fixture, mp: pytest.MonkeyPatch) -> None:
    mp.setenv("CPATH", str(fx.envdir))
    fx.env_on = True


Mutation = Callable[[Fixture, pytest.MonkeyPatch], None]

MUTATIONS: dict[str, Mutation] = {
    "explicit header content": lambda fx, mp: _edit(fx.header),
    "explicit header mtime-only touch": lambda fx, mp: _touch(fx.header),
    "transitive .h content": lambda fx, mp: _edit(fx.inc / "detail" / "x.h"),
    "transitive .h mtime-only touch": lambda fx, mp: _touch(fx.inc / "detail" / "x.h"),
    "transitive .def content": lambda fx, mp: _edit(fx.inc / "detail" / "kinds.def"),
    "transitive suffix-less content": lambda fx, mp: _edit(
        fx.inc / "detail" / "vector"
    ),
    "new file under include root": lambda fx, mp: (fx.inc / "new.inl").write_text(
        "x\n"
    ),
    "file-valued include content": lambda fx, mp: _edit(fx.inc_file),
    "file-valued include touch": lambda fx, mp: _touch(fx.inc_file),
    "system header content": lambda fx, mp: _edit(fx.sysdir / "stdio.h"),
    "system header touch": lambda fx, mp: _touch(fx.sysdir / "stdio.h"),
    "identical rewrite": lambda fx, mp: fx.header.write_bytes(fx.header.read_bytes()),
    "vcs metadata under include root": lambda fx, mp: _edit(fx.inc / ".git" / "index"),
    "include root path move": _rename_inc,
    "CPATH set": _env,
}


def _keys(fx: Fixture, mp: pytest.MonkeyPatch) -> dict[str, str]:
    mp.setattr(snapshot_key, "compiler_search_dirs", lambda _cc, cpp: (str(fx.sysdir),))
    req = dump_request(
        path=fx.binary, headers=[fx.header], includes=fx.includes(), lang="c"
    )
    out = {"snapshot": api._cache_key_for(req)}
    for backend in ("clang", "castxml"):
        out[backend] = ast_cache_key(
            [fx.header],
            fx.includes(),
            "cc",
            backend=backend,
            system_includes=(str(fx.sysdir),),
        )
    return out


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.setattr(cache_input_digest, "_FILE_MEMO", {})
    monkeypatch.setattr(cache_input_digest, "_SEARCH_DIR_MEMO", {})


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_key_busts_iff_parse_input_content_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    fx = _build(tmp_path)
    before_state = _oracle_state(fx)
    before = _keys(fx, monkeypatch)
    assert all(before.values())
    MUTATIONS[name](fx, monkeypatch)
    expected_bust = _oracle_state(fx) != before_state
    after = _keys(fx, monkeypatch)
    for which in before:
        busted = before[which] != after[which]
        assert busted == expected_bust, (
            f"{which} key: mutation {name!r} busted={busted}, "
            f"oracle says {expected_bust}"
        )


def test_the_matrix_exercises_both_outcomes(tmp_path: Path) -> None:
    """Guard against a vacuous matrix: the oracle must split the mutations."""
    outcomes = set()
    mp = pytest.MonkeyPatch()
    try:
        for i, (_name, mutate) in enumerate(sorted(MUTATIONS.items())):
            fx = _build(tmp_path / str(i))
            before = _oracle_state(fx)
            mutate(fx, mp)
            outcomes.add(_oracle_state(fx) != before)
            mp.undo()
    finally:
        mp.undo()
    assert outcomes == {True, False}


def test_memo_reuse_never_serves_a_same_stat_rewrite(tmp_path: Path) -> None:
    """A same-size rewrite within the racy window must still re-digest."""
    p = tmp_path / "h.h"
    p.write_text("aaaa")
    d1 = cache_input_digest.file_digest(p)
    st = p.stat()
    p.write_text("bbbb")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert cache_input_digest.file_digest(p) != d1


@pytest.mark.skipif(
    not __import__("shutil").which("gcc"), reason="needs a GNU driver to probe"
)
def test_real_compiler_search_dirs_cover_the_system_headers() -> None:
    dirs = cache_input_digest.compiler_search_dirs("gcc", cpp=False)
    assert any(Path(d, "stdio.h").is_file() for d in dirs), dirs
    # memoized: a second call answers without re-probing
    assert cache_input_digest.compiler_search_dirs("gcc", cpp=False) is dirs
