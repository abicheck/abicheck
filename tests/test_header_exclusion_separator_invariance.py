"""`--exclude-header` matching does not depend on path separators.

Known gap "An `--exclude-header` pattern spelled as a path matches nothing on
Windows" (2026-09-16). The match rule (`model.header_exclusion_record.
glob_header_matches`) runs on two kinds of path: the live header operand,
spelled by the host running abicheck, and a stored snapshot's
`source_header`, spelled by the host that *dumped* it. `fnmatch` normalizes
separators only on Windows, so the answer depended on which OS produced the
path and which OS read it. These tests state the rule's contract as
invariants over generated spellings, simulating both hosts.
"""

from __future__ import annotations

import itertools
import ntpath
import posixpath
from fnmatch import fnmatch
from unittest import mock

import pytest

from abicheck.model.header_exclusion_record import glob_header_matches

_PATHS = (
    "/opt/include/fftw/fftw3.h",
    "/opt/include/fftw/offload/fftw3_omp.h",
    "/opt/include/mkl.h",
    "include/foo.h",
    "foo.h",
    "/a/b/c/d.hpp",
)
_PATTERNS = (
    "fftw3.h",
    "fftw/*",
    "**/fftw/*",
    "include/foo.h",
    "/opt/include/mkl.h",
    "fftw/offload/*",
    "*.hpp",
    "b/c/*",
    "nothing.h",
    "opt/*",
)
_HOSTS = {"posix": posixpath.normcase, "windows": ntpath.normcase}


def _win(spelling: str) -> str:
    return spelling.replace("/", "\\")


def _previous_rule(path: str, patterns: list[str]) -> bool:
    """The rule as it stood before separator-independence -- the oracle for
    "a POSIX path and POSIX pattern are decided exactly as before"."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return any(
        fnmatch(name, p) or fnmatch(path, p) or fnmatch(path, f"*/{p}")
        for p in patterns
    )


def _on(host: str, path: str, patterns: list[str]) -> bool:
    # `fnmatch` reads `os.path.normcase` at call time, so swapping it is
    # exactly the difference between the two hosts.
    with mock.patch("os.path.normcase", _HOSTS[host]):
        return glob_header_matches(path, patterns)


def _table(host: str, spell) -> dict[tuple[str, str], bool]:  # type: ignore[no-untyped-def]
    """Every path x pattern answer on *host* under one patch."""
    with mock.patch("os.path.normcase", _HOSTS[host]):
        return {
            (path, pat): glob_header_matches(*spell(path, pat))
            for path, pat in itertools.product(_PATHS, _PATTERNS)
        }


_SPELLINGS = {
    "posix/posix": lambda p, q: (p, [q]),
    "win/posix": lambda p, q: (_win(p), [q]),
    "posix/win": lambda p, q: (p, [_win(q)]),
    "win/win": lambda p, q: (_win(p), [_win(q)]),
}


@pytest.mark.parametrize("host", sorted(_HOSTS))
def test_answer_is_independent_of_separator_spelling_and_host(host: str) -> None:
    """For every path x pattern, the four separator spellings agree on each
    simulated host, and agree with the POSIX host's POSIX-spelled answer."""
    reference = _table("posix", _SPELLINGS["posix/posix"])
    disagreements = [
        (host, name, key, got, reference[key])
        for name, spell in _SPELLINGS.items()
        for key, got in _table(host, spell).items()
        if got != reference[key]
    ]
    assert not disagreements, disagreements[:5]


def test_posix_spellings_are_decided_exactly_as_before() -> None:
    now = _table("posix", _SPELLINGS["posix/posix"])
    changed = [
        key for key, got in now.items() if got != _previous_rule(key[0], [key[1]])
    ]
    assert not changed, changed


def test_the_enumeration_is_not_vacuous() -> None:
    answers = set(_table("posix", _SPELLINGS["posix/posix"]).values())
    assert answers == {True, False}


@pytest.mark.parametrize(
    ("stored", "pattern", "expected"),
    [
        # A snapshot dumped on Windows, read on Linux: the reported case.
        (r"C:\opt\include\fftw\fftw3.h", "fftw/*", True),
        (r"C:\opt\include\fftw\fftw3.h", "include/fftw/fftw3.h", True),
        (r"C:\opt\include\mkl.h", "fftw/*", False),
        # A Windows user's backslash pattern against a Linux-dumped snapshot.
        ("/opt/include/fftw/fftw3.h", r"fftw\*", True),
        ("/opt/include/mkl.h", r"fftw\*", False),
    ],
)
def test_cross_host_snapshot_paths(stored: str, pattern: str, expected: bool) -> None:
    for host in _HOSTS:
        assert _on(host, stored, [pattern]) is expected, host


def test_empty_inputs_match_nothing() -> None:
    assert glob_header_matches(None, ["*"]) is False
    assert glob_header_matches("", ["*"]) is False
    assert glob_header_matches("/a/b.h", []) is False
