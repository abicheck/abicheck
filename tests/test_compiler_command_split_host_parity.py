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

"""Every compiler-command call site splits one command text one way.

Bug class ``tooling.platform_dependent_record_separator`` (see
``tests/regressions/manifest_tool_surface.py``): a value that crosses a
host-dependent boundary must be read by one owner, not by N inline copies of
the convention. Before ``abicheck._compiler_options.split_command_line``, the
make recipe, make response-file, ninja command and compiler-recorded command
each spelled ``shlex.split(text, posix=os.name != "nt")``, while the compile-
database path and ``header_conditionals`` spelled a bare ``shlex.split(text)``,
so the same text could tokenize differently between two sites on one host.

The canonical helper dispatches on ``os.name`` by design (the convention of
the host whose build system wrote the text). So two claims are pinned here:

* inputs on which POSIX shell quoting and MSVC argv quoting *agree* (quoted
  values with spaces, ``cl.exe /I"C:\\x y"``, an escaped quote) tokenize
  identically under both ``os.name`` values at every site; and
* an input on which they *disagree* (an unquoted Windows path with
  backslashes) is split by the host convention and pinned per host, so the
  divergence is stated rather than silently absorbed.

Each site must also equal the canonical function under every host, so a
future site cannot quietly reintroduce its own splitter.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from abicheck._compiler_options import split_command_line
from abicheck.build_context import _split_command_line as build_context_split
from abicheck.buildsource.adapters.make import _split_recipe
from abicheck.buildsource.adapters.ninja import _argv_of
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.compiler_record import _collect_command_line
from abicheck.buildsource.redaction import DEFAULT_REDACTION
from abicheck.header_conditionals import _split_command as header_conditionals_split

HOSTS = ("posix", "nt")

#: Shell-form command texts on which POSIX and MSVC quoting produce the same
#: tokens. Each expected list is what both conventions give.
AGREED: list[tuple[str, list[str]]] = [
    ('cc -DMSG="hello world" -c a.c', ["cc", "-DMSG=hello world", "-c", "a.c"]),
    (r'cl.exe /I"C:\x y" /c a.c', ["cl.exe", r"/IC:\x y", "/c", "a.c"]),
    (r'cc -DPATH="C:\a\b" -c a.c', ["cc", r"-DPATH=C:\a\b", "-c", "a.c"]),
    (r"cc -DVERSION=\"1.2\" -c a.c", ["cc", '-DVERSION="1.2"', "-c", "a.c"]),
    ("cc -DOK=1 -c a.c", ["cc", "-DOK=1", "-c", "a.c"]),
]

#: An unquoted Windows path: POSIX shell quoting eats the backslashes, MSVC
#: argv quoting keeps them. The one case where the host convention matters.
HOST_DEPENDENT_TEXT = r"cc -IC:\mypath\include -c a.c"
HOST_DEPENDENT_BY_HOST: dict[str, list[str]] = {
    "posix": ["cc", "-IC:mypathinclude", "-c", "a.c"],
    "nt": ["cc", r"-IC:\mypath\include", "-c", "a.c"],
}


def _ninja_argv(text: str) -> list[str]:
    return _argv_of({"command": text})


def _make_recipe_argv(text: str) -> list[str]:
    # A recipe line carries make's own `@` silent prefix; it must be stripped.
    return _split_recipe("@" + text)


def _compiler_record_argv(text: str) -> list[str]:
    """Drive the compiler-recorded path with one command, capturing its argv."""
    captured: list[list[str]] = []

    def capture(argv: list[str], _red: object) -> SimpleNamespace:
        captured.append(argv)
        return SimpleNamespace(source="")

    class _Section:
        def data(self) -> bytes:
            return b""

    class _Elf:
        def get_section_by_name(self, _name: str) -> _Section:
            return _Section()

    with (
        mock.patch(
            "abicheck.buildsource.compiler_record.parse_gcc_command_line",
            return_value=[text],
        ),
        mock.patch(
            "abicheck.buildsource.compiler_record._command_to_unit", side_effect=capture
        ),
        mock.patch(
            "abicheck.buildsource.compiler_record.derive_build_options",
            return_value=[],
        ),
    ):
        _collect_command_line(_Elf(), BuildEvidence(), DEFAULT_REDACTION)
    assert len(captured) == 1, captured
    return captured[0]


#: Every top-level compile-command splitter the adapters own, as text -> argv.
SITES: dict[str, Callable[[str], list[str]]] = {
    "make_recipe": _make_recipe_argv,
    "ninja_command": _ninja_argv,
    "compiler_record": _compiler_record_argv,
    "compile_db_command": build_context_split,
    "header_conditionals_command": header_conditionals_split,
}


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Set ``os.name`` for the test body; the canonical helper reads it per call."""

    def set_host(name: str) -> None:
        monkeypatch.setattr(os, "name", name)

    return set_host


@pytest.mark.parametrize("site", sorted(SITES))
@pytest.mark.parametrize(("text", "expected"), AGREED)
def test_agreed_input_splits_identically_on_both_hosts(
    site: str, text: str, expected: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    results: dict[str, list[str]] = {}
    for name in HOSTS:
        monkeypatch.setattr(os, "name", name)
        results[name] = SITES[site](text)
    assert results["posix"] == results["nt"] == expected


@pytest.mark.parametrize("site", sorted(SITES))
def test_host_dependent_input_is_pinned_per_host(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in HOSTS:
        monkeypatch.setattr(os, "name", name)
        assert SITES[site](HOST_DEPENDENT_TEXT) == HOST_DEPENDENT_BY_HOST[name], name


@pytest.mark.parametrize("site", sorted(SITES))
@pytest.mark.parametrize("text", [text for text, _ in AGREED] + [HOST_DEPENDENT_TEXT])
@pytest.mark.parametrize("name", HOSTS)
def test_site_equals_canonical_splitter_on_every_host(
    site: str, text: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "name", name)
    expected = split_command_line(text)
    # The make recipe strips its own `@` silent prefix before splitting; the
    # text here has none, so the canonical result is the reference for all sites.
    assert SITES[site](text) == expected


def test_dpcpp_job_lines_split_identically_on_both_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``-###`` job lines a driver prints, parsed by ``dpcpp_jobs``."""
    from abicheck.extract import dpcpp_jobs

    stderr = (
        ' "cc" "-DMSG=hello world" "-c" "a.c"\n'
        ' "cl" "/IC:\\x y" "/c" "b.c"\n'
        "clang version 20.0.0\n"
    )
    expected = [
        ["cc", "-DMSG=hello world", "-c", "a.c"],
        ["cl", r"/IC:\x y", "/c", "b.c"],
    ]
    fake = SimpleNamespace(stderr=stderr)
    results: dict[str, Any] = {}
    for name in HOSTS:
        monkeypatch.setattr(os, "name", name)
        with mock.patch("abicheck.deadline.run_bounded", return_value=fake):
            results[name] = dpcpp_jobs._driver_jobs(["clang"])
    assert results["posix"] == results["nt"] == expected
