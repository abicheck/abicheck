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

"""Every identifier token the public header set's raw text spells.

ADR-063's 2026-10-01 amendment: the evidence that lets ``--contract public``
close its domain for an export nothing declared. An active-AST parse only
sees the preprocessor branches that were taken, so "the parsed headers do not
declare ``extended``" is not "the headers never mention ``extended``"
(catalog case97: ``#ifdef USE_FEATURE``). Scanning the raw text, every branch
included, answers the second question.

Comments and string/character literals are stripped first, so a name merely
mentioned in prose does not count -- but over-inclusion is always the safe
direction: an extra token only keeps a finding unresolved.

The answer is a ``Fact``: ``present`` with the identifier set, or a
non-present status saying why the scan cannot vouch for completeness:

- no public header set was given;
- a header file could not be read;
- a header uses the ``##`` token-paste operator, so a declared name may be
  assembled by the preprocessor and never appear as one token.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..header_utils import CACHE_HEADER_SUFFIXES
from ..model.fact import Fact

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

_COMMENT_OR_LITERAL = re.compile(
    r"""
    //[^\n]*                       # line comment
    | /\*.*?\*/                    # block comment
    | "(?:\\.|[^"\\\n])*"          # string literal
    | '(?:\\.|[^'\\\n])*'          # character literal
    """,
    re.DOTALL | re.VERBOSE,
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN_PASTE = "##"
_PRODUCER = "public_header_text"


def _strip_comments_and_literals(text: str) -> str:
    return _COMMENT_OR_LITERAL.sub(" ", text)


def _header_files(
    headers: Iterable[Path | str], header_dirs: Iterable[Path | str]
) -> list[Path] | str:
    """The files the public set names, directories expanded, or the first
    named entry that does not exist."""
    files: list[Path] = []
    for entry in headers:
        path = Path(entry)
        if path.is_dir():
            files.extend(_dir_headers(path))
        elif path.is_file():
            files.append(path)
        else:
            return str(entry)
    for entry in header_dirs:
        path = Path(entry)
        if not path.is_dir():
            return str(entry)
        files.extend(_dir_headers(path))
    return files


def _dir_headers(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in CACHE_HEADER_SUFFIXES
    )


def identifiers_in_header_text(text: str) -> frozenset[str] | None:
    """Identifier tokens of one header's text; ``None`` if it token-pastes."""
    code = _strip_comments_and_literals(text)
    if _TOKEN_PASTE in code:
        return None
    return frozenset(_IDENTIFIER.findall(code))


def scan_public_header_identifiers(
    headers: Iterable[Path | str] | None,
    header_dirs: Iterable[Path | str] | None = None,
) -> Fact[frozenset[str] | None]:
    """Every identifier the public header set's text spells, as a ``Fact``.

    ``present`` only when every file was read and none token-pastes; every
    other outcome says why (see the module docstring).
    """
    headers = list(headers or ())
    header_dirs = list(header_dirs or ())
    if not headers and not header_dirs:
        return Fact.not_collected("no public header set", producer=_PRODUCER)
    files = _header_files(headers, header_dirs)
    if isinstance(files, str):
        return Fact.failed(f"public header not found: {files}", producer=_PRODUCER)
    if not files:
        return Fact.not_collected("public header set is empty", producer=_PRODUCER)
    out: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Fact.failed(f"unreadable: {path}: {exc}", producer=_PRODUCER)
        found = identifiers_in_header_text(text)
        if found is None:
            return Fact.unsupported(f"token paste (##) in {path}", producer=_PRODUCER)
        out |= found
    return Fact.present(frozenset(out), producer=_PRODUCER)


def stamp_public_header_identifiers(
    snapshot: AbiSnapshot,
    headers: Iterable[Path | str] | None,
    header_dirs: Iterable[Path | str] | None,
) -> None:
    """Set *snapshot*'s ``public_header_identifiers`` (and its ``Fact``)."""
    fact = scan_public_header_identifiers(headers, header_dirs)
    snapshot.public_header_identifiers_fact = fact
    snapshot.public_header_identifiers = fact.value if fact.is_present else None


__all__ = [
    "identifiers_in_header_text",
    "scan_public_header_identifiers",
    "stamp_public_header_identifiers",
]
