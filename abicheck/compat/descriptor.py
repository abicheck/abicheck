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

Typical ABICC descriptor (old.xml):
    <version>2025.0</version>
    <headers>/usr/include/foo</headers>
    <libs>/usr/lib/libfoo.so</libs>

Extended form (multiple headers/libs):
    <version>2025.0</version>
    <headers>/usr/include/foo</headers>
    <headers>/usr/include/foo/detail</headers>
    <libs>/usr/lib/libfoo.so</libs>
    <libs>/usr/lib/libfoo_extra.so</libs>
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import defusedxml.ElementTree as ET

from ..errors import ValidationError

log = logging.getLogger(__name__)


@dataclass
class CompatDescriptor:
    """Parsed ABICC XML descriptor."""

    version: str
    headers: list[Path]
    libs: list[Path]
    path: Path = field(default_factory=lambda: Path("."))


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

    try:
        tree = ET.parse(str(path))  # defusedxml.ElementTree.parse
    except ET.ParseError as exc:
        raise ValidationError(f"Invalid XML in descriptor {path}: {exc}") from exc

    root = tree.getroot()
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

    log.debug(
        "Parsed descriptor %s: version=%s, %d lib(s), %d header dir(s)",
        path,
        version,
        len(libs),
        len(headers),
    )

    return CompatDescriptor(version=version, headers=headers, libs=libs, path=path)


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
