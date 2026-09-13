# Copyright 2026 Nikolay Petrov
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

"""ABICC XML descriptor parsing.

Implements:
- ABICC XML descriptor parsing (defusedxml, XXE-safe)
- ``CompatDescriptor`` dataclass matching ABICC's -old/-new descriptor format
- ``parse_descriptor()`` — validates required fields, supports multi-value tags

Both descriptor shapes ABICC accepts are parsed. A well-formed document:

    <descriptor>
      <version>2025.0</version>
      <headers>/usr/include/foo</headers>
      <libs>/usr/lib/libfoo.so</libs>
    </descriptor>

and ABICC's own rootless *fragment*, which is what real pipelines
(Intel MKL's among them) actually ship -- sibling elements with no wrapper:

    <version>2025.0</version>
    <headers>/usr/include/foo</headers>
    <libs>/usr/lib/libfoo.so</libs>

Until ``_parse_descriptor_root`` handled the second shape, this docstring
showed it as the "typical" descriptor while the parser rejected it with a
document-level XML error -- so the example a reader was most likely to copy
was the one guaranteed not to work.

Extended form (multiple headers/libs), valid under either shape:
    <version>2025.0</version>
    <headers>/usr/include/foo</headers>
    <headers>/usr/include/foo/detail</headers>
    <libs>/usr/lib/libfoo.so</libs>
    <libs>/usr/lib/libfoo_extra.so</libs>

``<headers>`` and ``<libs>`` accept a **directory** as well as a file; see
:func:`~abicheck.compat._helpers.expand_descriptor_headers`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Type-only: defusedxml parses into stdlib ``Element`` objects; the alias
# never constructs or parses anything, so it introduces no unsafe parser.
from xml.etree.ElementTree import Element  # noqa: S405

import defusedxml.ElementTree as ET

from ..errors import ValidationError

log = logging.getLogger(__name__)


#: Descriptor elements this parser reads and acts on.
_SUPPORTED_ELEMENTS: frozenset[str] = frozenset(
    {
        "version",
        "headers",
        "libs",
        "include_paths",
        "add_include_paths",
        "gcc_options",
        "defines",
        "skip_headers",
        "skip_including",
        "skip_namespaces",
        "skip_constants",
        "skip_symbols",
        "skip_types",
    }
)

#: Elements ABICC defines that this parser recognises but does **not** act
#: on. Listed explicitly so they produce a "not applied" warning naming the
#: element, rather than the silent drop every unread element used to get: a
#: descriptor's ``<skip_libs>`` that quietly does nothing is worse than one
#: that is rejected, because the run still produces a confident verdict
#: computed over a surface the descriptor said to narrow.
_KNOWN_UNAPPLIED_ELEMENTS: frozenset[str] = frozenset(
    {
        "skip_libs",
        "search_headers",
        "search_libs",
        "tools",
        "cross_prefix",
        "include_preamble",
        "libs_depend",
        "opencl",
    }
)


@dataclass
class CompatDescriptor:
    """Parsed ABICC XML descriptor.

    Beyond ``version``/``headers``/``libs``, the elements below carry the
    *narrowing* rules a real descriptor relies on. They were parsed by
    nothing before -- ``parse_descriptor`` read three elements and dropped
    every other child silently -- which meant a descriptor's declared skips
    had no effect at all while the run still reported a confident verdict.
    Intel MKL's descriptors carry 16 such skips, all load-bearing.
    """

    version: str
    headers: list[Path]
    libs: list[Path]
    path: Path = field(default_factory=lambda: Path("."))
    #: ``<skip_headers>``/``<skip_including>`` -- header names or paths to
    #: exclude from the parsed surface. Matched the same way ``-skip-headers
    #: FILE`` entries are (basename or full path).
    skip_headers: list[str] = field(default_factory=list)
    #: ``<skip_namespaces>`` -- C++ namespaces whose declarations are
    #: internal.
    skip_namespaces: list[str] = field(default_factory=list)
    #: ``<skip_constants>`` -- macro/constant names to exclude.
    skip_constants: list[str] = field(default_factory=list)
    #: ``<skip_symbols>`` / ``<skip_types>`` -- exact names to exclude.
    skip_symbols: list[str] = field(default_factory=list)
    skip_types: list[str] = field(default_factory=list)
    #: ``<include_paths>``/``<add_include_paths>`` -- include search roots,
    #: and ``<defines>``/``<gcc_options>`` -- extra compile flags. Without
    #: these a ``<headers>`` directory that relies on the descriptor's own
    #: include roots cannot parse at all.
    include_paths: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    gcc_options: list[str] = field(default_factory=list)


def parse_descriptor(path: Path, *, relpath: str | None = None) -> CompatDescriptor:
    """Parse an ABICC XML descriptor file.

    Args:
        path: Path to the descriptor XML file.
        relpath: Optional relative path prefix to prepend to all relative paths
            in the descriptor. Replaces ``{RELPATH}`` macros in paths if present,
            or is used as a base directory for resolving relative paths.

    Returns:
        Populated CompatDescriptor.

    Raises:
        ValueError: If required fields (version, libs) are missing or empty.
        FileNotFoundError: If the descriptor file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Descriptor not found: {path}")
    if not path.is_file():
        raise ValidationError(f"Descriptor path is not a regular file: {path}")

    root = _parse_descriptor_root(path)
    base = path.parent

    def _get_all(tag: str) -> list[str]:
        # findall() — direct children only; avoids capturing nested tags
        # (root.iter() would recurse into sub-elements, silently picking up
        # nested <version> or <libs> inside other tags)
        vals = [
            el.text.strip() for el in root.findall(tag) if el.text and el.text.strip()
        ]
        # Replace {RELPATH} macros if relpath is provided (ABICC feature)
        if relpath:
            vals = [v.replace("{RELPATH}", relpath) for v in vals]
        return vals

    version_vals = _get_all("version")
    if not version_vals:
        raise ValidationError(f"Descriptor {path}: missing <version> element")
    version = version_vals[0]

    # When relpath is provided, use it as the base for resolving relative paths
    # that don't explicitly use {RELPATH} macros (which are already substituted
    # by _get_all above).
    resolve_base = Path(relpath) if relpath else base

    lib_strs = _get_all("libs")
    if not lib_strs:
        raise ValidationError(f"Descriptor {path}: missing <libs> element")
    libs = [_resolve(s, resolve_base) for s in lib_strs]

    header_strs = _get_all("headers")
    headers = [_resolve(s, resolve_base) for s in header_strs]

    _warn_unread_elements(root, path)

    log.debug(
        "Parsed descriptor %s: version=%s, %d lib(s), %d header dir(s)",
        path,
        version,
        len(libs),
        len(headers),
    )

    return CompatDescriptor(
        version=version,
        headers=headers,
        libs=libs,
        path=path,
        # ABICC accepts both one-per-element and whitespace/newline-separated
        # lists inside a single element; `_get_tokens` handles both so a
        # descriptor written either way behaves identically.
        skip_headers=_get_tokens(_get_all, "skip_headers")
        + _get_tokens(_get_all, "skip_including"),
        skip_namespaces=_get_tokens(_get_all, "skip_namespaces"),
        skip_constants=_get_tokens(_get_all, "skip_constants"),
        skip_symbols=_get_tokens(_get_all, "skip_symbols"),
        skip_types=_get_tokens(_get_all, "skip_types"),
        include_paths=[
            _resolve(t, resolve_base)
            for t in _get_tokens(_get_all, "include_paths")
            + _get_tokens(_get_all, "add_include_paths")
        ],
        defines=_get_tokens(_get_all, "defines"),
        gcc_options=_get_tokens(_get_all, "gcc_options"),
    )


def _resolve(p: str, base: Path) -> Path:
    """Return absolute path; resolve relative paths against the descriptor's directory.

    Path containment check: relative paths must not escape the base directory
    (guards against crafted descriptors with '../../' traversal sequences).
    Absolute paths are accepted as-is (matching ABICC behaviour for system paths).

    On Windows, Unix-style absolute paths (starting with ``/``) are treated as
    absolute to support cross-platform descriptor files written on Linux.
    """
    # Treat Unix absolute paths as absolute even on Windows
    if p.startswith("/"):
        if os.name == "nt":
            # On Windows, Path("/usr/lib/...") is drive-relative, not absolute.
            # Prepend the current drive letter so the result is truly absolute.
            return Path(Path.cwd().drive + p)
        return Path(p)
    resolved = Path(p)
    if not resolved.is_absolute():
        resolved = (base / resolved).resolve()
        # Containment check: resolved path must stay within base directory
        try:
            resolved.relative_to(base.resolve())
        except ValueError:
            raise ValidationError(
                f"Path '{p}' in descriptor escapes the base directory '{base}'. "
                "Use absolute paths for libraries outside the descriptor directory."
            ) from None
    return resolved


#: Synthetic wrapper element used to parse a rootless ABICC descriptor
#: *fragment*. Never appears in a descriptor a user wrote; chosen to match
#: the root element this project's own documentation shows, so an error
#: message naming it is not confusing to a reader who did supply a root.
_SYNTHETIC_ROOT = "descriptor"


def _parse_descriptor_root(path: Path) -> Element:
    """Return the element whose direct children hold the descriptor fields.

    ABICC's real descriptors are XML *fragments*: sibling ``<version>``,
    ``<headers>`` and ``<libs>`` elements with no wrapper root. That is not a
    well-formed XML document, so a plain ``ET.parse`` rejects the file
    outright -- which made ``abicheck compat`` unable to consume the
    descriptors of the very tool it is a drop-in replacement for. Intel MKL's
    own CI descriptors are of exactly this shape, and hit exit 6.

    ABICC is tolerant here, so this is: a file that parses as a document is
    used as-is, and only a file that does *not* is retried once wrapped in a
    synthetic root. Parsing stays on ``defusedxml`` in both attempts -- the
    text is wrapped, never the parser bypassed -- so the XXE protections are
    identical on either path.

    A file that fails both attempts is malformed for a reason the wrapper
    cannot explain away (an unclosed tag, a stray ``&``), and the *original*
    document-level error is what is reported: the fragment retry is an
    accommodation, not a diagnosis, and its own error would point at a
    synthetic line number the user's file does not have.
    """
    try:
        return ET.parse(str(path)).getroot()  # defusedxml.ElementTree.parse
    except ET.ParseError as exc:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raise ValidationError(f"Invalid XML in descriptor {path}: {exc}") from exc
        wrapped = (
            f"<{_SYNTHETIC_ROOT}>{_strip_xml_declaration(text)}</{_SYNTHETIC_ROOT}>"
        )
        try:
            root = ET.fromstring(wrapped)  # defusedxml.ElementTree.fromstring
        except ET.ParseError:
            raise ValidationError(f"Invalid XML in descriptor {path}: {exc}") from exc
        log.debug(
            "Descriptor %s parsed as an ABICC fragment (no single root element)",
            path,
        )
        return root


def _strip_xml_declaration(text: str) -> str:
    """*text* without a leading ``<?xml ...?>`` declaration.

    An XML declaration is only legal at the very start of a document, so it
    cannot survive being wrapped in a synthetic root -- a fragment file that
    carries one (some generators emit it even for a fragment) would otherwise
    fail the retry for a reason unrelated to why the first parse failed.
    Only a *leading* declaration is removed, and only one: a ``<?xml`` later
    in the file is a real error and stays one.
    """
    stripped = text.lstrip()
    if not stripped.startswith("<?xml"):
        return text
    end = stripped.find("?>")
    if end == -1:
        return text
    return stripped[end + 2 :]


def _get_tokens(get_all: Callable[[str], list[str]], tag: str) -> list[str]:
    """Whitespace-separated tokens across every *tag* element.

    ABICC descriptors spell a list either way -- one element per value, or
    one element holding a newline-separated block -- and real descriptors mix
    the two. Splitting on whitespace covers both; no supported element's
    values may contain a space (they are header names, namespaces, symbol
    names and compile flags), so this cannot split one value in half.

    The one case it would: a ``<gcc_options>`` value like ``-I /some/path``,
    which splits into two tokens -- which is correct, since that is exactly
    how the two tokens reach a compiler command line anyway.
    """
    out: list[str] = []
    for raw in get_all(tag):
        out.extend(raw.split())
    return out


def _warn_unread_elements(root: Element, path: Path) -> None:
    """Warn about descriptor elements this parser does not act on.

    Every child element outside :data:`_SUPPORTED_ELEMENTS` was silently
    dropped before this existed -- including every ``<skip_*>`` rule, which
    is how a descriptor could declare 16 exclusions, have none of them
    applied, and still produce a confident verdict over the unnarrowed
    surface. A warning is the minimum honest disposition: the run continues
    (rejecting an unknown element outright would break drop-in
    compatibility with descriptors carrying elements newer than this
    parser), but nobody is told a rule was applied when it was not.
    """
    seen: set[str] = set()
    for el in root:
        tag = getattr(el, "tag", None)
        if not isinstance(tag, str) or tag in _SUPPORTED_ELEMENTS or tag in seen:
            continue
        seen.add(tag)
        if tag in _KNOWN_UNAPPLIED_ELEMENTS:
            log.warning(
                "Descriptor %s: <%s> is a recognised ABICC element that "
                "abicheck does not apply; its rule has no effect on this run.",
                path,
                tag,
            )
        else:
            log.warning("Descriptor %s: unknown element <%s> ignored.", path, tag)
