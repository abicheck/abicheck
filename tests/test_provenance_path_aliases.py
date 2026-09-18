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

"""Alias-aware public-header path matching (symlinked ``-H``/``--header``
roots).

The defect these pin: a parser records a declaration's *canonical* path
while the user names the same include tree through a symlink (``-H
/localdisk/.../inc`` vs. a castxml source location under
``/mnt/cached_oses/.../inc``). Matching only the lexical spelling classified
every declaration non-public, producing false ``exported_not_public``
findings.

The fix is an *alias* model, not a resolve-everything one, and these tests
state both halves as invariants:

* **liveness** -- any valid spelling of a declaration's path may meet any
  valid spelling of a declared root, in either direction;
* **safety** -- the lexical spelling is never lost, a foreign-platform
  rooted string is never reinterpreted, a non-existent path is never
  required to exist, and nothing a symlink points at widens ownership
  beyond the tree that was actually declared.

The platform-shape cases are written against the pure helpers with
``os.name`` mocked, so both platforms' rules are exercised on either
platform (real symlink creation on Windows needs a privilege CI may not
have -- only the filesystem cases skip there, never the path logic).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.extract.dependency_header_roots import is_dependency_header
from abicheck.extract.path_aliases import (
    _is_native_absolute_spelling,
    canonical_spelling,
    clear_path_alias_caches,
    include_root_alias_segments,
    path_alias_spellings,
    public_root_alias_segments,
    segments as _segments,
    source_header_alias_segments,
)
from abicheck.model import ScopeOrigin
from abicheck.provenance import build_public_set, classify_origin

# A symlink is the mechanism under test; Windows only permits creating one
# with SeCreateSymbolicLinkPrivilege (or developer mode). Only the real-
# filesystem cases are gated -- every pure path-alias rule below runs
# everywhere.
_symlinks = pytest.mark.skipif(
    os.name == "nt",
    reason="creating a symlink on Windows needs a privilege CI may lack",
)


@pytest.fixture(autouse=True)
def _fresh_alias_caches() -> None:
    """The alias caches sample the filesystem once per distinct path string;
    these tests build and rebuild trees at fresh ``tmp_path``s, but clearing
    keeps them independent of each other's observations regardless."""
    clear_path_alias_caches()


def _classify(
    source: str, *, headers: list[str] = [], dirs: list[str] = []
) -> ScopeOrigin:
    header_segs, dir_segs, have = build_public_set(headers, dirs)
    return classify_origin(source, header_segs, dir_segs, have_public_set=have)


def _linked_tree(tmp_path: Path) -> tuple[Path, Path]:
    """``real/include/pub.h`` plus a ``link`` -> ``real`` directory symlink.

    Returns ``(real_include, link_include)``: two spellings of one tree.
    """
    real = tmp_path / "real" / "include"
    real.mkdir(parents=True)
    (real / "pub.h").write_text("int foo(void);\n")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    return real, tmp_path / "link" / "include"


# --------------------------------------------------------------------------
# 1-5, 13: real-filesystem alias matching
# --------------------------------------------------------------------------


@_symlinks
def test_absolute_symlink_directory_root_matches_canonical_child(
    tmp_path: Path,
) -> None:
    """(1) ``-H <symlink>/include`` against a canonical source location."""
    real, link = _linked_tree(tmp_path)
    assert _classify(str(real / "pub.h"), dirs=[str(link)]) is ScopeOrigin.PUBLIC_HEADER


@_symlinks
def test_absolute_symlink_file_root_matches_canonical_file(tmp_path: Path) -> None:
    """(2) A symlinked *file* root, parser reporting the canonical file."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "pub.h").write_text("int foo(void);\n")
    link = tmp_path / "link.h"
    link.symlink_to(real / "pub.h")
    # A basename match would answer this trivially, so use a root whose
    # basename differs from the canonical one: only a real alias can match.
    assert (
        _classify(str(real / "pub.h"), headers=[str(link)]) is ScopeOrigin.PUBLIC_HEADER
    )


@_symlinks
def test_relative_symlink_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(3) A *relative* ``-H`` root reached through a symlink."""
    real, _ = _linked_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert (
        _classify(str(real / "pub.h"), dirs=["link/include"])
        is ScopeOrigin.PUBLIC_HEADER
    )


@_symlinks
def test_canonical_root_matches_symlinked_declaration_spelling(tmp_path: Path) -> None:
    """(4) The reverse direction: canonical root, symlinked source path."""
    real, link = _linked_tree(tmp_path)
    assert _classify(str(link / "pub.h"), dirs=[str(real)]) is ScopeOrigin.PUBLIC_HEADER


def test_non_symlink_absolute_root_is_unchanged(tmp_path: Path) -> None:
    """(5) With no symlink anywhere, the root contributes exactly one
    spelling -- its own -- and matching is what it always was."""
    real = tmp_path / "include"
    real.mkdir()
    (real / "pub.h").write_text("int foo(void);\n")
    assert public_root_alias_segments(str(real)) == [_segments(str(real))]
    assert _classify(str(real / "pub.h"), dirs=[str(real)]) is ScopeOrigin.PUBLIC_HEADER


def test_missing_local_path_falls_back_to_lexical_matching(tmp_path: Path) -> None:
    """(6) Nothing here exists on disk; matching still works lexically and
    no filesystem error escapes."""
    root = tmp_path / "never" / "created" / "include"
    assert path_alias_spellings(str(root)) == (str(root),)
    assert _classify(str(root / "pub.h"), dirs=[str(root)]) is ScopeOrigin.PUBLIC_HEADER


@_symlinks
def test_symlinked_public_root_does_not_make_other_declarations_public(
    tmp_path: Path,
) -> None:
    """(13) A canonical alias widens matching to the *same* tree only."""
    real, link = _linked_tree(tmp_path)
    other = tmp_path / "real" / "vendor"
    other.mkdir()
    (other / "dep.h").write_text("int dep(void);\n")
    assert (
        _classify(str(other / "dep.h"), dirs=[str(link)]) is ScopeOrigin.PRIVATE_HEADER
    )
    assert (
        _classify("/usr/include/stdio.h", dirs=[str(link)]) is ScopeOrigin.SYSTEM_HEADER
    )


# --------------------------------------------------------------------------
# 7-12: the pre-existing root-shape rules survive aliasing
# --------------------------------------------------------------------------


def test_file_root_parent_widening_stays_limited(tmp_path: Path) -> None:
    """(7) A *file* root widens to its own directory and no further."""
    pkg = tmp_path / "proj" / "include" / "mylib"
    pkg.mkdir(parents=True)
    (pkg / "api.h").write_text("int foo(void);\n")
    sibling = tmp_path / "proj" / "include" / "other"
    sibling.mkdir()
    (sibling / "other.h").write_text("int bar(void);\n")
    root = str(pkg / "api.h")
    assert is_dependency_header(str(sibling / "other.h"), [root]) is False
    # ...but it is not *public*: only the declared file's own directory is.
    assert (
        _classify(str(sibling / "other.h"), headers=[root])
        is ScopeOrigin.PRIVATE_HEADER
    )
    assert _classify(str(pkg / "api.h"), headers=[root]) is ScopeOrigin.PUBLIC_HEADER


def test_directory_root_does_not_widen_to_parent(tmp_path: Path) -> None:
    """(8) A directory root is used as given -- its parent stays outside."""
    inc = tmp_path / "proj" / "include"
    sub = inc / "mylib"
    sub.mkdir(parents=True)
    outside = tmp_path / "proj" / "private"
    outside.mkdir()
    assert _classify(str(sub / "api.h"), dirs=[str(sub)]) is ScopeOrigin.PUBLIC_HEADER
    assert (
        _classify(str(outside / "p.h"), dirs=[str(sub)]) is ScopeOrigin.PRIVATE_HEADER
    )


def test_bare_system_root_stays_protected() -> None:
    """(9) A flat file root in ``/usr/include`` does not widen to the bare
    system prefix, so unrelated system headers stay dependencies."""
    assert is_dependency_header("/usr/include/stdio.h", ["/usr/include/zlib.h"]) is True
    assert is_dependency_header("/usr/include/zlib.h", ["/usr/include/zlib.h"]) is False


def test_project_subdirectory_under_system_prefix_still_matches() -> None:
    """(10) ``/usr/include/mylib`` is a project directory, not a system one."""
    assert (
        is_dependency_header(
            "/usr/include/mylib/detail.h", ["/usr/include/mylib/api.h"]
        )
        is False
    )
    assert (
        _classify("/usr/include/mylib/api.h", dirs=["/usr/include/mylib"])
        is ScopeOrigin.PUBLIC_HEADER
    )


def test_unrelated_trees_sharing_an_include_segment_do_not_match(
    tmp_path: Path,
) -> None:
    """(11) Segment matching, not string-prefix matching: a shared generic
    ``include`` component is not shared ownership."""
    mine = tmp_path / "mine" / "include"
    theirs = tmp_path / "theirs" / "include"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)
    assert (
        _classify(str(theirs / "dep.h"), dirs=[str(mine)]) is ScopeOrigin.PRIVATE_HEADER
    )


def test_trailing_separator_and_dot_components_normalize(tmp_path: Path) -> None:
    """(12) ``inc/``, ``inc/.`` and ``a/./inc`` all name one root."""
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "pub.h").write_text("int foo(void);\n")
    for spelling in (f"{inc}/", f"{inc}/.", str(inc).replace("/inc", "/./inc")):
        assert (
            _classify(str(inc / "pub.h"), dirs=[spelling]) is ScopeOrigin.PUBLIC_HEADER
        ), spelling
    assert _classify(f"{inc}/./pub.h", dirs=[str(inc)]) is ScopeOrigin.PUBLIC_HEADER


# --------------------------------------------------------------------------
# 14-18: platform shape, exercised on either platform
# --------------------------------------------------------------------------


@pytest.fixture
def _as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")
    clear_path_alias_caches()


@pytest.fixture
def _as_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    clear_path_alias_caches()


def test_posix_absolute_path_on_windows_is_not_drive_anchored(
    _as_windows: None,
) -> None:
    """(14) The regression ``_absolutize_header_root`` was written for: a
    POSIX-rooted string on Windows is never resolved, so it can never pick
    up the current drive."""
    posix = "/usr/include/mylib/api.h"
    assert _is_native_absolute_spelling(posix) is False
    assert canonical_spelling(posix) is None
    assert path_alias_spellings(posix) == (posix,)
    assert public_root_alias_segments(posix) == [_segments(posix)]


@pytest.mark.parametrize("drive", ["C:\\proj\\include", "D:/proj/include"])
def test_windows_drive_paths_are_native_only_on_windows(
    drive: str, _as_windows: None
) -> None:
    """(15) A drive-rooted path is the native absolute syntax on Windows
    (resolution is then merely gated on the path existing, which it does
    not here, so matching stays lexical)."""
    assert _is_native_absolute_spelling(drive) is True
    assert path_alias_spellings(drive) == (drive,)


@pytest.mark.parametrize("unc", ["\\\\server\\share\\inc", "//server/share/inc"])
def test_unc_paths_are_recognized_as_windows_rooted(
    unc: str, _as_windows: None
) -> None:
    """(16) A UNC spelling is Windows-rooted -- and, crucially, is never
    mistaken for a POSIX absolute path just because it starts with a
    separator."""
    assert _is_native_absolute_spelling(unc) is True
    assert path_alias_spellings(unc) == (unc,)


@pytest.mark.parametrize("unc", ["\\\\server\\share\\inc", "//server/share/inc"])
def test_unc_paths_are_not_resolved_on_posix(unc: str, _as_posix: None) -> None:
    """(16, POSIX half) On POSIX a UNC spelling is foreign syntax and is
    preserved lexically rather than reinterpreted as a rooted local path."""
    assert _is_native_absolute_spelling(unc) is False
    assert canonical_spelling(unc) is None


@pytest.mark.parametrize("win", ["C:\\proj\\include\\api.h", "C:/proj/include/api.h"])
def test_windows_looking_paths_on_posix_are_preserved_lexically(
    win: str, _as_posix: None
) -> None:
    """(17) ``C:\\...`` is syntactically *relative* to POSIX; resolving it
    would invent a directory named ``C:``. It stays lexical."""
    assert _is_native_absolute_spelling(win) is False
    assert canonical_spelling(win) is None
    assert path_alias_spellings(win) == (win,)


@pytest.mark.parametrize(
    ("root", "source"),
    [
        ("/build/agent/work/toolkit/inc", "/build/agent/work/toolkit/inc/foo.h"),
        ("C:\\ci\\toolkit\\inc", "C:\\ci\\toolkit\\inc\\foo.h"),
        ("\\\\build\\share\\toolkit\\inc", "\\\\build\\share\\toolkit\\inc\\foo.h"),
    ],
)
def test_stored_cross_machine_paths_classify_without_filesystem_access(
    root: str, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(18) None of these exist here. Classification must not consult the
    filesystem for an answer it can reach lexically, and must not fail when
    it does consult it."""
    calls: list[str] = []
    real_exists = Path.exists

    def _counting_exists(self: Path, *a: object, **k: object) -> bool:
        calls.append(str(self))
        return real_exists(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "exists", _counting_exists)
    assert _classify(source, dirs=[root]) is ScopeOrigin.PUBLIC_HEADER
    assert all(not real_exists(Path(c)) for c in calls)


# --------------------------------------------------------------------------
# Invariants over generated inputs, not just the reported repro
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        ".",
        "..",
        "include",
        "include/api.h",
        "/",
        "/usr/include",
        "/a/b/c.h",
        "//server/share/x",
        "\\\\server\\share\\x",
        "C:\\x\\y",
        "c:/x/y",
        "relative/../thing.h",
        "/spaces in/the path/api.h",
        "/tricky:name/api.h",
    ],
)
def test_lexical_spelling_is_always_the_first_alias(raw: str) -> None:
    """The safety invariant that makes every other change additive: no
    input ever loses its own spelling."""
    assert path_alias_spellings(raw)[0] == raw
    lexical = _segments(raw)
    if not lexical:
        # A spelling with no segments at all (``""``, ``"."``, ``"/"``)
        # carries no matching power, so it is dropped rather than led with.
        return
    assert source_header_alias_segments(raw)[0] == lexical
    assert public_root_alias_segments(raw)[0] == lexical


@pytest.mark.parametrize(
    "raw",
    [
        "/a/b/c.h",
        "include/api.h",
        "/usr/include/mylib/api.h",
        "C:\\x\\y.h",
        "//server/share/x.h",
    ],
)
def test_aliasing_never_shrinks_the_matched_set(raw: str) -> None:
    """Every alias set contains the lexical segments, so a classification
    that matched before an alias existed still matches after."""
    lexical = _segments(raw)
    if not lexical:
        pytest.skip("no segments")
    assert lexical in list(source_header_alias_segments(raw))
    assert lexical in public_root_alias_segments(raw)


def test_include_root_aliases_exclude_the_bare_relative_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generic ``-I include`` must not become the one-segment root that
    matches inside ``/usr/include/...``; the public variant keeps the raw
    relative spelling, the include variant deliberately does not."""
    (tmp_path / "include").mkdir()
    monkeypatch.chdir(tmp_path)
    assert ("include",) not in include_root_alias_segments("include")
    assert ("include",) in public_root_alias_segments("include")


@_symlinks
def test_resolution_failure_does_not_fail_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink loop (or any other OSError) degrades to lexical matching
    rather than propagating out of classification."""
    loop = tmp_path / "loop"
    loop.symlink_to(tmp_path / "loop")
    clear_path_alias_caches()
    assert canonical_spelling(str(loop)) is None
    assert _classify(str(loop / "api.h"), dirs=[str(loop)]) is ScopeOrigin.PUBLIC_HEADER
