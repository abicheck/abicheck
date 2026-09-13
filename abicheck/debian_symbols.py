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

"""Debian symbols file generation, parsing, validation, and diffing.

Implements the ``dpkg-gensymbols(1)`` symbols file format used by Debian/Ubuntu
packaging for fine-grained shared library dependency tracking.

Format reference:
  https://manpages.debian.org/unstable/dpkg-dev/dpkg-gensymbols.1.en.html

A symbols file has the structure::

    libfoo.so.1 libfoo1 #MINVER#
     _ZN3foo3barEv@Base 1.0
     (c++)"foo::bar()@Base" 1.0
     (arch=amd64)_ZN3foo3bazEv@Base 1.0

Limitations:
  - ``#include`` directives and ``#PACKAGE#`` substitution are not supported.
  - ``(regex)`` and ``(symver)`` pattern-matching tags are not evaluated.
  - ``(arch=...)`` tags are parsed but not filtered (no ``--arch`` option yet).
"""

from __future__ import annotations

import logging
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .demangle import demangle
from .elf_metadata import ElfMetadata, ElfSymbol, SymbolType, parse_elf_metadata

_log = logging.getLogger(__name__)

# Maximum symbols file size we are willing to read (50 MiB).
_MAX_SYMBOLS_FILE_BYTES = 50 * 1024 * 1024

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Regex for parsing optional tags like (c++), (arch=amd64), (symver), etc.
_TAG_RE = re.compile(r"^\(([^)]+)\)")


@dataclass
class DebianSymbolEntry:
    """One symbol line in a Debian symbols file."""

    name: str  # symbol name (mangled or demangled with quotes)
    version_node: str  # "Base" or a version node like "LIBFOO_1.0"
    min_version: str  # minimum package version where this symbol appeared
    # Raw tag groups exactly as parsed, e.g. [["c++", "optional"], ["arch=amd64"]].
    # Each inner list is one parenthesised group; pipe-separated values stay together.
    tag_groups: list[list[str]] = field(default_factory=list)

    @property
    def tags(self) -> list[str]:
        """Flat list of all individual tag values (convenience accessor)."""
        return [t for group in self.tag_groups for t in group]

    @property
    def is_cpp(self) -> bool:
        return "c++" in self.tags

    @property
    def is_optional(self) -> bool:
        return "optional" in self.tags

    def format_line(self) -> str:
        """Format as a Debian symbols file line (without leading space).

        Preserves the original tag grouping so that ``(c++|optional)``
        round-trips correctly instead of being split into ``(c++)(optional)``.
        """
        tag_prefix = "".join("(" + "|".join(group) + ")" for group in self.tag_groups)
        if self.is_cpp:
            return f'{tag_prefix}"{self.name}@{self.version_node}" {self.min_version}'
        return f"{tag_prefix}{self.name}@{self.version_node} {self.min_version}"


@dataclass
class DebianSymbolsFile:
    """Parsed Debian symbols file."""

    library: str  # SONAME, e.g. "libfoo.so.1"
    package: str  # package name, e.g. "libfoo1"
    min_version: str  # #MINVER# or a version string
    symbols: list[DebianSymbolEntry] = field(default_factory=list)

    def format(self) -> str:
        """Format the complete Debian symbols file."""
        lines = [f"{self.library} {self.package} {self.min_version}"]
        for sym in sorted(self.symbols, key=lambda s: s.format_line()):
            lines.append(f" {sym.format_line()}")
        return "\n".join(lines) + "\n"


@dataclass
class ValidationResult:
    """Result of validating a symbols file against a binary."""

    library: str
    missing: list[DebianSymbolEntry] = field(default_factory=list)
    new_symbols: list[str] = field(default_factory=list)  # "name@version_node"

    @property
    def passed(self) -> bool:
        """Validation passes when no required symbols are missing."""
        return len(self.missing) == 0


@dataclass
class SymbolsDiff:
    """Diff between two Debian symbols files."""

    added: list[DebianSymbolEntry] = field(default_factory=list)
    removed: list[DebianSymbolEntry] = field(default_factory=list)
    version_changed: list[tuple[DebianSymbolEntry, DebianSymbolEntry]] = field(
        default_factory=list,
    )  # (old, new)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_symbols_file(text: str) -> DebianSymbolsFile:
    """Parse a Debian symbols file from its text content.

    Raises ``ValueError`` on malformed input.
    """
    lines = text.splitlines()
    if not lines:
        raise ValueError("Empty symbols file")

    # First line: "libfoo.so.1 libfoo1 #MINVER#"
    header_parts = lines[0].split(None, 2)
    if len(header_parts) < 3:
        raise ValueError(f"Malformed header line: {lines[0]!r}")

    result = DebianSymbolsFile(
        library=header_parts[0],
        package=header_parts[1],
        min_version=header_parts[2],
    )

    for lineno, line in enumerate(lines[1:], start=2):
        if not line or not line.startswith(" "):
            continue  # skip blank or non-symbol lines
        entry = _parse_symbol_line(line.strip(), lineno)
        if entry is not None:
            result.symbols.append(entry)

    return result


def _parse_symbol_line(line: str, lineno: int) -> DebianSymbolEntry | None:
    """Parse a single symbol line (already stripped of leading whitespace).

    Handles forms like::

        _ZN3foo3barEv@Base 1.0
        (c++)"foo::bar()@Base" 1.0
        (c++|optional)"foo::bar()@Base" 1.0
        (arch=amd64)_ZN3foo3barEv@Base 1.0
    """
    tag_groups: list[list[str]] = []
    rest = line

    # Extract leading tags: (tag1)(tag2)...
    # Each parenthesised group is stored as a list so pipe-separated tags
    # (e.g. "c++|optional") round-trip correctly.
    while rest.startswith("("):
        m = _TAG_RE.match(rest)
        if not m:
            break
        tag_content = m.group(1)
        group = [t.strip() for t in tag_content.split("|")]
        tag_groups.append(group)
        rest = rest[m.end() :]

    flat_tags = [t for g in tag_groups for t in g]
    is_cpp = "c++" in flat_tags

    if is_cpp:
        # C++ form: "demangled_name@VersionNode" min_version
        if not rest.startswith('"'):
            _log.warning("Line %d: expected quoted C++ symbol: %s", lineno, line)
            return None
        # Find closing quote — use rfind to handle names containing quotes
        # (e.g. operator"" _foo).  The closing quote is the last '"' in the
        # line, followed by the min_version string.
        end_quote = rest.rfind('"')
        if end_quote <= 0:
            _log.warning("Line %d: unterminated quote: %s", lineno, line)
            return None
        quoted = rest[1:end_quote]
        remainder = rest[end_quote + 1 :].strip()
        # quoted = "foo::bar()@Base"
        at_idx = quoted.rfind("@")
        if at_idx == -1:
            _log.warning("Line %d: missing @version in quoted symbol: %s", lineno, line)
            return None
        name = quoted[:at_idx]
        version_node = quoted[at_idx + 1 :]
        min_version = remainder if remainder else ""
    else:
        # Non-C++ form: mangled_name@VersionNode min_version
        parts = rest.split(None, 1)
        if not parts:
            return None
        sym_ver = parts[0]
        min_version = parts[1] if len(parts) > 1 else ""
        at_idx = sym_ver.rfind("@")
        if at_idx == -1:
            _log.warning("Line %d: missing @version: %s", lineno, line)
            return None
        name = sym_ver[:at_idx]
        version_node = sym_ver[at_idx + 1 :]

    return DebianSymbolEntry(
        name=name,
        version_node=version_node,
        min_version=min_version.strip(),
        tag_groups=tag_groups,
    )


def load_symbols_file(path: Path) -> DebianSymbolsFile:
    """Load and parse a Debian symbols file from disk.

    Verifies the target is a regular file and enforces a size limit to
    prevent memory exhaustion from malicious input.

    The regular-file check uses ``path.stat()`` *before* ``open()`` —
    intentional tradeoff: this creates a small TOCTOU window, but avoids
    blocking indefinitely on FIFOs or device nodes.  By contrast,
    ``parse_elf_metadata`` uses ``fstat()`` *after* ``open()`` which is
    TOCTOU-safe but would hang on a FIFO.  For a text symbols file the
    pre-open stat is the safer choice.
    """
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Not a regular file: {path}")
    if st.st_size > _MAX_SYMBOLS_FILE_BYTES:
        raise ValueError(
            f"Symbols file too large ({st.st_size} bytes, "
            f"limit {_MAX_SYMBOLS_FILE_BYTES}): {path}"
        )
    return parse_symbols_file(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _symbol_version_node(sym: ElfSymbol) -> str:
    """Determine the version node for a symbol.

    Returns the ELF version tag (e.g. "LIBFOO_1.0") if present,
    otherwise "Base" (the Debian convention for unversioned symbols).
    """
    if sym.version:
        return sym.version
    return "Base"


def generate_symbols_file(
    elf_meta: ElfMetadata,
    *,
    package: str = "",
    version: str = "#MINVER#",
    use_cpp: bool = True,
) -> DebianSymbolsFile:
    """Generate a Debian symbols file from ELF metadata.

    Args:
        elf_meta: Parsed ELF metadata (from ``parse_elf_metadata``).
        package: Debian package name. If empty, derived from SONAME.
        version: Version string for the minimum version field.
        use_cpp: If True, emit C++ symbols in demangled ``(c++)`` form.

    Returns:
        A ``DebianSymbolsFile`` ready to be formatted.
    """
    soname = elf_meta.soname or "UNKNOWN"

    if not package:
        # Derive package name from SONAME: libfoo.so.1 → libfoo1
        package = _soname_to_package(soname)

    result = DebianSymbolsFile(
        library=soname,
        package=package,
        min_version=version,
    )

    for sym in elf_meta.symbols:
        # Skip symbols that are not exported functions or objects
        if sym.sym_type not in (SymbolType.FUNC, SymbolType.OBJECT, SymbolType.IFUNC):
            continue

        ver_node = _symbol_version_node(sym)
        tag_groups: list[list[str]] = []
        name = sym.name

        # Try to demangle C++ symbols
        if use_cpp:
            demangled = demangle(sym.name)
            if demangled is not None:
                tag_groups.append(["c++"])
                name = demangled

        result.symbols.append(
            DebianSymbolEntry(
                name=name,
                version_node=ver_node,
                min_version=version,
                tag_groups=tag_groups,
            )
        )

    return result


def generate_from_binary(
    so_path: Path,
    *,
    package: str = "",
    version: str = "#MINVER#",
    use_cpp: bool = True,
) -> DebianSymbolsFile:
    """Generate a Debian symbols file directly from a shared library binary.

    Convenience wrapper around ``parse_elf_metadata`` + ``generate_symbols_file``.
    """
    elf_meta = parse_elf_metadata(so_path)
    return generate_symbols_file(
        elf_meta,
        package=package,
        version=version,
        use_cpp=use_cpp,
    )


def _soname_to_package(soname: str) -> str:
    """Derive a Debian package name from a SONAME.

    Examples::

        libfoo.so.1   → libfoo1
        libbar.so.2.3 → libbar2
        libfoo.so     → libfoo
    """
    # Strip .so and everything after, then append major version
    base = soname
    so_idx = base.find(".so")
    if so_idx == -1:
        return base

    lib_base = base[:so_idx]
    version_part = base[so_idx + 3 :]  # ".1" or ".2.3" or ""

    if version_part.startswith("."):
        # Extract major version number
        ver_str = version_part[1:]
        dot_idx = ver_str.find(".")
        if dot_idx != -1:
            ver_str = ver_str[:dot_idx]
        return lib_base + ver_str

    return lib_base


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_symbols(
    elf_meta: ElfMetadata,
    symbols_file: DebianSymbolsFile,
) -> ValidationResult:
    """Validate a Debian symbols file against an ELF binary's metadata.

    Checks:
    - Symbols listed in the file but missing from the binary.
    - Symbols exported by the binary but not listed in the file.

    Symbols tagged ``(optional)`` are skipped — they do not cause a failure
    when absent from the binary (per ``dpkg-gensymbols(1)`` semantics).

    Returns a ``ValidationResult``.
    """
    soname = elf_meta.soname or "UNKNOWN"

    # Build set of exported symbols from binary: "name@version_node"
    binary_syms: dict[str, ElfSymbol] = {}
    binary_mangled_set: set[str] = set()
    for sym in elf_meta.symbols:
        if sym.sym_type not in (SymbolType.FUNC, SymbolType.OBJECT, SymbolType.IFUNC):
            continue
        ver_node = _symbol_version_node(sym)
        key = f"{sym.name}@{ver_node}"
        binary_syms[key] = sym
        binary_mangled_set.add(sym.name)

    # Build demangled → list of mangled names for C++ symbol lookup.
    # Multiple mangled names can demangle to the same string (e.g. ABI tags).
    demangled_to_mangled: dict[str, list[str]] = {}
    for sym_name in binary_mangled_set:
        d = demangle(sym_name)
        if d is not None:
            demangled_to_mangled.setdefault(d, []).append(sym_name)

    result = ValidationResult(library=soname)

    # Track which binary symbols are accounted for
    matched_binary_keys: set[str] = set()

    for entry in symbols_file.symbols:
        # (optional) symbols are not required — skip validation
        if entry.is_optional:
            # Still try to match so they don't appear as "new"
            _try_match(entry, binary_syms, demangled_to_mangled, matched_binary_keys)
            continue

        if not _try_match(
            entry, binary_syms, demangled_to_mangled, matched_binary_keys
        ):
            result.missing.append(entry)

    # Find new symbols (in binary but not in symbols file)
    for key in sorted(binary_syms.keys()):
        if key not in matched_binary_keys:
            result.new_symbols.append(key)

    return result


def _try_match(
    entry: DebianSymbolEntry,
    binary_syms: dict[str, ElfSymbol],
    demangled_to_mangled: dict[str, list[str]],
    matched_binary_keys: set[str],
) -> bool:
    """Try to match a symbols-file entry against the binary.  Returns True on match."""
    if entry.is_cpp:
        candidates = demangled_to_mangled.get(entry.name, [])
        for mangled in candidates:
            key = f"{mangled}@{entry.version_node}"
            if key in binary_syms:
                matched_binary_keys.add(key)
                return True
        return False

    key = f"{entry.name}@{entry.version_node}"
    if key in binary_syms:
        matched_binary_keys.add(key)
        return True
    return False


def validate_from_binary(
    so_path: Path,
    symbols_path: Path,
) -> ValidationResult:
    """Validate a symbols file against a binary (convenience wrapper)."""
    elf_meta = parse_elf_metadata(so_path)
    symbols_file = load_symbols_file(symbols_path)
    return validate_symbols(elf_meta, symbols_file)


def format_validation_report(result: ValidationResult) -> str:
    """Format a human-readable validation report."""
    lines = [f"Symbols validation for {result.library}:"]

    lines.append("  MISSING from binary (in symbols file but not exported):")
    if result.missing:
        for entry in result.missing:
            lines.append(f"    {entry.format_line()}")
    else:
        lines.append("    (none)")

    lines.append("  NEW in binary (exported but not in symbols file):")
    if result.new_symbols:
        for sym_key in result.new_symbols:
            lines.append(f"    {sym_key}")
    else:
        lines.append("    (none)")

    n_missing = len(result.missing)
    n_new = len(result.new_symbols)

    if result.passed and n_new == 0:
        lines.append("  Result: PASS")
    elif result.passed:
        lines.append(f"  Result: PASS ({n_new} new symbol{'s' if n_new != 1 else ''})")
    else:
        lines.append(
            f"  Result: FAIL ({n_missing} missing symbol{'s' if n_missing != 1 else ''})"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff_symbols_files(
    old: DebianSymbolsFile,
    new: DebianSymbolsFile,
) -> SymbolsDiff:
    """Compute the diff between two Debian symbols files.

    Identity key includes the version node so that the same symbol name
    under different version nodes (common with ELF symbol versioning)
    is tracked correctly.

    Returns a ``SymbolsDiff`` with added, removed, and version-changed symbols.
    """
    result = SymbolsDiff()

    def _key(entry: DebianSymbolEntry) -> str:
        # Include tags so (arch=amd64)foo@Base and (arch=arm64)foo@Base
        # are tracked as distinct entries.
        tag_str = "".join("(" + "|".join(g) + ")" for g in entry.tag_groups)
        return f"{tag_str}{entry.name}@{entry.version_node}"

    old_by_key: dict[str, DebianSymbolEntry] = {}
    for entry in old.symbols:
        old_by_key[_key(entry)] = entry

    new_by_key: dict[str, DebianSymbolEntry] = {}
    for entry in new.symbols:
        new_by_key[_key(entry)] = entry

    old_keys = set(old_by_key.keys())
    new_keys = set(new_by_key.keys())

    # Removed: in old but not in new
    for k in sorted(old_keys - new_keys):
        result.removed.append(old_by_key[k])

    # Added: in new but not in old
    for k in sorted(new_keys - old_keys):
        result.added.append(new_by_key[k])

    # Version changed: same full key, different min_version
    for k in sorted(old_keys & new_keys):
        old_entry = old_by_key[k]
        new_entry = new_by_key[k]
        if old_entry.min_version != new_entry.min_version:
            result.version_changed.append((old_entry, new_entry))

    return result


def format_diff_report(
    diff: SymbolsDiff,
    old_path: str = "old",
    new_path: str = "new",
) -> str:
    """Format a human-readable diff report."""
    lines = [f"Symbols diff: {old_path} -> {new_path}"]

    lines.append("  ADDED:")
    if diff.added:
        for entry in diff.added:
            lines.append(f"    + {entry.format_line()}")
    else:
        lines.append("    (none)")

    lines.append("  REMOVED:")
    if diff.removed:
        for entry in diff.removed:
            lines.append(f"    - {entry.format_line()}")
    else:
        lines.append("    (none)")

    lines.append("  VERSION CHANGED:")
    if diff.version_changed:
        for old_entry, new_entry in diff.version_changed:
            lines.append(
                f"    {old_entry.name}: {old_entry.min_version} -> {new_entry.min_version}"
            )
    else:
        lines.append("    (none)")

    total = len(diff.added) + len(diff.removed) + len(diff.version_changed)
    lines.append(f"  Total changes: {total}")

    return "\n".join(lines) + "\n"
