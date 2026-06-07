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

"""Build-context capture from compile_commands.json (ADR-020a).

Parses a JSON Compilation Database (Clang standard) to extract the exact
compiler flags, defines, include paths, and language standard used to build
each translation unit.  This eliminates "header parse drift" — the most
common source of ABI tool inaccuracy — by binding header AST extraction
to the real build context.

Usage::

    from abicheck.build_context import load_compile_db, build_context_for_header

    db = load_compile_db(Path("build/compile_commands.json"))
    ctx = build_context_for_header(db, Path("include/foo.h"))
    # ctx.defines, ctx.include_paths, ctx.language_standard, ...
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .errors import ValidationError

_logger = logging.getLogger(__name__)

# Flags that take a following argument (next token is the value).
_FLAGS_WITH_ARG = frozenset({
    "-I", "-isystem", "-include", "-isysroot", "--sysroot",
    "-target", "--target", "-x", "-std", "-MF", "-MQ", "-MT",
    "-o", "-c",
})

# Regex for combined -Dfoo=bar or -Dfoo
_DEFINE_RE = re.compile(r"^-D(.+?)(?:=(.*))?$")
_UNDEF_RE = re.compile(r"^-U(.+)$")
_INCLUDE_RE = re.compile(r"^-I(.+)$")
_ISYSTEM_RE = re.compile(r"^-isystem(.+)$")
_STD_RE = re.compile(r"^-std=(.+)$")
_TARGET_RE = re.compile(r"^--?target=(.+)$")
_SYSROOT_RE = re.compile(r"^--sysroot=(.+)$")
_VISIBILITY_RE = re.compile(r"^-fvisibility=(.+)$")


@dataclass
class CompileEntry:
    """One entry from compile_commands.json."""

    file: Path
    directory: Path
    arguments: list[str]

    @classmethod
    def from_dict(cls, raw: dict[str, object], db_dir: Path) -> CompileEntry:
        """Parse a single compile_commands.json entry.

        Handles both ``arguments`` (JSON array) and ``command`` (shell string)
        forms as specified by the Clang compilation database standard.
        """
        directory = Path(str(raw.get("directory", db_dir)))
        file_str = str(raw.get("file", ""))
        file_path = Path(file_str)
        if not file_path.is_absolute():
            file_path = directory / file_path

        if "arguments" in raw:
            args_raw = raw["arguments"]
            if isinstance(args_raw, list):
                arguments = [str(a) for a in args_raw]
            else:
                arguments = shlex.split(str(args_raw), posix=os.name != "nt")
        elif "command" in raw:
            arguments = shlex.split(str(raw["command"]), posix=os.name != "nt")
        else:
            arguments = []

        return cls(file=file_path.resolve(), directory=directory, arguments=arguments)


@dataclass
class BuildContext:
    """Compilation context derived from compile_commands.json (ADR-020a).

    Captures the exact flags that were used to compile one or more TUs,
    enabling deterministic header parsing via CastXML.
    """

    defines: dict[str, str | None] = field(default_factory=dict)
    undefines: set[str] = field(default_factory=set)
    include_paths: list[Path] = field(default_factory=list)
    system_includes: list[Path] = field(default_factory=list)
    language_standard: str | None = None
    target_triple: str | None = None
    sysroot: Path | None = None
    extra_flags: list[str] = field(default_factory=list)
    compile_db_path: Path | None = None

    # Conflict tracking (populated by union fallback)
    define_conflicts: dict[str, list[str]] = field(default_factory=dict)
    standard_variants: list[str] = field(default_factory=list)

    def to_castxml_flags(self) -> list[str]:
        """Convert this build context to CastXML-compatible flags.

        Returns a list of command-line arguments suitable for passing to
        CastXML (or any Clang-compatible frontend).
        """
        flags: list[str] = []

        if self.language_standard:
            flags.append(f"-std={self.language_standard}")

        if self.target_triple:
            flags.append(f"--target={self.target_triple}")

        if self.sysroot:
            flags.append(f"--sysroot={self.sysroot}")

        for macro, value in sorted(self.defines.items()):
            if value is not None:
                flags.append(f"-D{macro}={value}")
            else:
                flags.append(f"-D{macro}")

        for macro in sorted(self.undefines):
            flags.append(f"-U{macro}")

        for inc in self.include_paths:
            flags.extend(["-I", str(inc)])

        for inc in self.system_includes:
            flags.extend(["-isystem", str(inc)])

        flags.extend(self.extra_flags)
        return flags

    @property
    def has_conflicts(self) -> bool:
        """Return True if define or standard conflicts were detected."""
        return bool(self.define_conflicts) or len(self.standard_variants) > 1


def _entry_matches_filter(entry: CompileEntry, pattern: str) -> bool:
    """Test if a compile entry matches a source_filter glob pattern.

    Tests the pattern against the absolute path and also against the
    path relative to the entry's compilation directory, so that relative
    patterns like ``src/libfoo/**`` work as documented.
    """
    abs_str = str(entry.file)
    if fnmatch(abs_str, pattern):
        return True
    # Also test against relative path (from the entry's build directory)
    try:
        rel_str = str(entry.file.relative_to(entry.directory))
    except ValueError:
        # file is not under directory — try CWD-relative too
        try:
            rel_str = str(entry.file.relative_to(Path.cwd()))
        except ValueError:
            return False
    return fnmatch(rel_str, pattern)


def load_compile_db(path: Path) -> list[CompileEntry]:
    """Load and parse a compile_commands.json file.

    Args:
        path: Path to compile_commands.json (file) or a build directory
              containing compile_commands.json.

    Returns:
        List of parsed compile entries.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid JSON or has wrong structure.
    """
    if path.is_dir():
        path = path / "compile_commands.json"

    if not path.exists():
        raise ValidationError(
            f"Compilation database not found: {path}. "
            "Ensure -p points to a directory containing compile_commands.json "
            "or to the file itself."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Invalid JSON in compilation database {path}: {exc}"
        ) from exc

    if not isinstance(raw, list):
        raise ValidationError(
            f"compile_commands.json must be a JSON array, got {type(raw).__name__}"
        )

    db_dir = path.parent
    entries: list[CompileEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _logger.warning("Skipping non-object entry at index %d", i)
            continue
        try:
            entries.append(CompileEntry.from_dict(item, db_dir))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            _logger.warning("Skipping malformed entry at index %d: %s", i, exc)

    _logger.info("Loaded %d compile entries from %s", len(entries), path)
    return entries


_ABI_EXTRA_PREFIXES = (
    "-fabi-version=", "-fpack-struct=",
    "-fms-extensions", "-fno-exceptions",
    "-fno-rtti", "-fexceptions", "-frtti",
)


def _resolve_path(raw: str, directory: Path) -> Path:
    """Resolve a path string relative to *directory* if not absolute."""
    p = Path(raw)
    if not p.is_absolute():
        p = directory / p
    return p


def _try_consume_include(arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext) -> int:
    """Handle -I (combined and separate forms). Returns new index."""
    m = _INCLUDE_RE.match(arg)
    if m:
        ctx.include_paths.append(_resolve_path(m.group(1), directory))
        return i + 1
    if arg == "-I" and i + 1 < len(arguments):
        ctx.include_paths.append(_resolve_path(arguments[i + 1], directory))
        return i + 2
    return i  # no match — caller must advance


def _try_consume_isystem(arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext) -> int:
    """Handle -isystem (combined and separate forms). Returns new index."""
    m = _ISYSTEM_RE.match(arg)
    if m:
        ctx.system_includes.append(_resolve_path(m.group(1), directory))
        return i + 1
    if arg == "-isystem" and i + 1 < len(arguments):
        ctx.system_includes.append(_resolve_path(arguments[i + 1], directory))
        return i + 2
    return i  # no match — caller must advance


def _try_consume_target(arg: str, arguments: list[str], i: int, ctx: BuildContext) -> int:
    """Handle --target= and -target (combined and separate forms). Returns new index."""
    m = _TARGET_RE.match(arg)
    if m:
        ctx.target_triple = m.group(1)
        return i + 1
    if arg in ("-target", "--target") and i + 1 < len(arguments):
        ctx.target_triple = arguments[i + 1]
        return i + 2
    return i  # no match — caller must advance


def _try_consume_sysroot(arg: str, arguments: list[str], i: int, directory: Path, ctx: BuildContext) -> int:
    """Handle --sysroot=, --sysroot, and -isysroot forms. Returns new index.

    Relative sysroot paths are resolved against *directory* (the compile
    entry's working directory), matching the behaviour of -I and -isystem.
    Absolute paths are stored as-is.
    """
    m = _SYSROOT_RE.match(arg)
    if m:
        ctx.sysroot = _resolve_path(m.group(1), directory)
        return i + 1
    if arg in ("--sysroot", "-isysroot") and i + 1 < len(arguments):
        ctx.sysroot = _resolve_path(arguments[i + 1], directory)
        return i + 2
    return i  # no match — caller must advance


def _is_abi_extra_flag(arg: str) -> bool:
    """Return True for ABI-relevant flags that should be forwarded as extra_flags."""
    return bool(_VISIBILITY_RE.match(arg)) or arg.startswith(_ABI_EXTRA_PREFIXES)


def _try_consume_define_undef(arg: str, arguments: list[str], i: int, ctx: BuildContext) -> int:
    """Handle -Dmacro[=value], -D macro[=value], -Umacro, -U macro.

    Handles both combined forms (``-DNAME=V``, ``-UNAME``) and split forms
    (``-D NAME=V``, ``-U NAME``).  Returns the new argument index after
    consuming this flag (and its value token if applicable), or *i* unchanged
    if the argument was not a define/undef flag.
    """
    m = _DEFINE_RE.match(arg)
    if m:
        ctx.defines[m.group(1)] = m.group(2)  # None if no =value
        return i + 1
    if arg == "-D" and i + 1 < len(arguments):
        # Split form: -D NAME[=VALUE]
        name, _, value = arguments[i + 1].partition("=")
        ctx.defines[name] = value if value else None
        return i + 2
    m = _UNDEF_RE.match(arg)
    if m:
        ctx.undefines.add(m.group(1))
        return i + 1
    if arg == "-U" and i + 1 < len(arguments):
        # Split form: -U NAME
        ctx.undefines.add(arguments[i + 1])
        return i + 2
    return i  # no match — caller must advance


def _consume_std_extra(arg: str, ctx: BuildContext) -> bool:
    """Handle -std=xxx and ABI-relevant extra flags. Returns True if consumed."""
    m = _STD_RE.match(arg)
    if m:
        ctx.language_standard = m.group(1)
        return True
    if _is_abi_extra_flag(arg):
        ctx.extra_flags.append(arg)
        return True
    return False


def _extract_flags(arguments: list[str], directory: Path) -> BuildContext:
    """Extract ABI-relevant flags from a compiler argument list.

    Parses -D, -U, -I, -isystem, -std=, --target=, --sysroot=, and
    other ABI-affecting flags.  Paths are resolved relative to the
    entry's working directory.
    """
    ctx = BuildContext()
    i = 0
    while i < len(arguments):
        arg = arguments[i]

        new_i = _try_consume_define_undef(arg, arguments, i, ctx)
        if new_i != i:
            i = new_i
            continue

        # -I and -isystem (combined and separate)
        new_i = _try_consume_include(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        new_i = _try_consume_isystem(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        if _consume_std_extra(arg, ctx):
            i += 1
            continue

        # --target=xxx or -target xxx (combined and separate)
        new_i = _try_consume_target(arg, arguments, i, ctx)
        if new_i != i:
            i = new_i
            continue

        # --sysroot=, --sysroot, -isysroot (combined and separate)
        new_i = _try_consume_sysroot(arg, arguments, i, directory, ctx)
        if new_i != i:
            i = new_i
            continue

        # Skip flags we don't care about (those that take a value token)
        i += 2 if (arg in _FLAGS_WITH_ARG and i + 1 < len(arguments)) else 1

    return ctx


def _header_included_by_tu(
    header_path: Path, entry: CompileEntry,
) -> bool:
    """Check if a TU's source file likely includes the given header.

    Uses a lightweight scan of the source file for #include directives
    that match the header path suffix (not just filename) to reduce
    false positives from unrelated headers with the same name.
    """
    try:
        source_content = entry.file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    header_name = header_path.name
    # First pass: quick check for the filename in any #include
    if header_name not in source_content:
        return False
    # Match #include "..." or #include <...> containing the header filename.
    # We check the matched path suffix against the actual header path to
    # reduce false positives from unrelated headers with the same name.
    pattern = re.compile(
        rf'#\s*include\s*[<"]([^>"]*{re.escape(header_name)})[>"]'
    )
    for m in pattern.finditer(source_content):
        include_arg = m.group(1)
        # Check if the include argument is a suffix of the header path
        if str(header_path).endswith(include_arg):
            return True
        # Also accept bare filename match as fallback
        if include_arg == header_name:
            return True
    return False


def build_context_for_header(
    entries: list[CompileEntry],
    header_path: Path,
    source_filter: str | None = None,
) -> BuildContext:
    """Find the best TU for a header and derive its build context (ADR-020a).

    Strategy:
    1. Filter entries by source_filter glob if specified
    2. Find TUs that include the header (by scanning source files)
    3. If found, use the first matching TU's flags
    4. If not found, fall back to union strategy

    Args:
        entries: Parsed compile database entries.
        header_path: The public header to match.
        source_filter: Optional glob pattern to filter source files
                       (e.g., "src/libfoo/**").

    Returns:
        BuildContext with flags appropriate for parsing the header.
    """
    header_resolved = header_path.resolve()

    # Filter entries
    filtered = entries
    if source_filter:
        filtered = [
            e for e in entries
            if _entry_matches_filter(e, source_filter)
        ]
        if not filtered:
            _logger.warning(
                "No compile entries match filter %r; using all entries",
                source_filter,
            )
            filtered = entries

    # Phase 1: Find TUs that directly include this header
    matching_entries: list[CompileEntry] = []
    for entry in filtered:
        if _header_included_by_tu(header_resolved, entry):
            matching_entries.append(entry)

    if matching_entries:
        if len(matching_entries) > 1:
            _logger.info(
                "Header %s included by %d TUs; using first match: %s",
                header_path.name,
                len(matching_entries),
                matching_entries[0].file.name,
            )
        entry = matching_entries[0]
        ctx = _extract_flags(entry.arguments, entry.directory)
        ctx.compile_db_path = entry.directory / "compile_commands.json"
        return ctx

    # Phase 2: Union fallback
    _logger.debug(
        "Header %s not matched to any TU; using union fallback",
        header_path.name,
    )
    return build_context_union_fallback(filtered)


def _std_sort_key(std: str) -> tuple[int, int]:
    """Numeric sort key for C/C++ standard strings.

    Maps standard names to (language, version) tuples for correct ordering.
    Handles draft names like c++2a, c++2b, c++2c (→ 20, 23, 26).
    """
    # Extract the numeric/draft suffix after the last occurrence of c/c++/gnu/gnu++
    m = re.search(r"(\d+[a-z]?)$", std)
    if not m:
        return (0, 0)
    suffix = m.group(1)
    is_cpp = "c++" in std or "gnu++" in std

    # Map draft names to release numbers
    draft_map = {"2a": 20, "2b": 23, "2c": 26}
    if suffix in draft_map:
        version = draft_map[suffix]
    elif suffix.isdigit():
        version = int(suffix)
    else:
        version = 0

    return (1 if is_cpp else 0, version)


def _filter_entries_by_glob(entries: list[CompileEntry], source_filter: str | None) -> list[CompileEntry]:
    """Return entries matching *source_filter* glob, or all entries if no match."""
    if not source_filter:
        return entries
    filtered = [e for e in entries if _entry_matches_filter(e, source_filter)]
    return filtered if filtered else entries


def _merge_defines_from_contexts(
    contexts: list[BuildContext],
) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """Merge define maps from multiple contexts; track per-macro conflicts.

    Returns:
        (merged_defines, define_conflicts) — conflicts maps macro name to list of
        distinct seen values (including the first).
    """
    merged: dict[str, str | None] = {}
    conflicts: dict[str, list[str]] = {}
    for ctx in contexts:
        for macro, value in ctx.defines.items():
            val_str = value if value is not None else "(defined)"
            if macro in merged:
                existing = merged[macro]
                existing_str = existing if existing is not None else "(defined)"
                if existing_str != val_str:
                    if macro not in conflicts:
                        conflicts[macro] = [existing_str]
                    conflicts[macro].append(val_str)
            else:
                merged[macro] = value
    if conflicts:
        for macro, values in conflicts.items():
            _logger.warning(
                "Macro %s has conflicting values across TUs: %s; "
                "using first value",
                macro,
                ", ".join(sorted(set(values))),
            )
    return merged, conflicts


def _merge_path_list(contexts: list[BuildContext], attr: str) -> list[Path]:
    """Merge a list[Path] attribute from multiple contexts, deduplicating by resolved path."""
    seen: set[str] = set()
    merged: list[Path] = []
    for ctx in contexts:
        for p in getattr(ctx, attr):
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                merged.append(p)
    return merged


def _pick_best_standard(contexts: list[BuildContext]) -> tuple[str | None, list[str]]:
    """Choose the highest language standard from all contexts.

    Returns:
        (lang_std, sorted_unique_standards) — lang_std may be None if none found.
    """
    raw = [ctx.language_standard for ctx in contexts if ctx.language_standard]
    standards = sorted(set(raw))
    if not standards:
        return None, standards
    cpp_stds = [s for s in standards if "c++" in s or "gnu++" in s]
    c_stds = [s for s in standards if s not in cpp_stds]
    if cpp_stds:
        return max(cpp_stds, key=_std_sort_key), standards
    return max(c_stds, key=_std_sort_key), standards


def _pick_target_sysroot(
    contexts: list[BuildContext],
) -> tuple[str | None, Path | None]:
    """Return the single consistent target triple and sysroot, warning on conflicts."""
    targets = {ctx.target_triple for ctx in contexts if ctx.target_triple}
    sysroots = {str(ctx.sysroot) for ctx in contexts if ctx.sysroot}

    target: str | None = None
    if len(targets) > 1:
        _logger.warning(
            "Conflicting target triples: %s; use --gcc-options to override",
            ", ".join(sorted(targets)),
        )
    elif targets:
        target = next(iter(targets))

    sysroot: Path | None = None
    if len(sysroots) > 1:
        _logger.warning(
            "Conflicting sysroots: %s; use --sysroot to override",
            ", ".join(sorted(sysroots)),
        )
    elif sysroots:
        sysroot = Path(next(iter(sysroots)))

    return target, sysroot


def _merge_extra_flags(contexts: list[BuildContext]) -> list[str]:
    """Merge extra_flags from all contexts, preserving order and deduplicating."""
    seen: set[str] = set()
    merged: list[str] = []
    for ctx in contexts:
        for f in ctx.extra_flags:
            if f not in seen:
                seen.add(f)
                merged.append(f)
    return merged


def build_context_union_fallback(
    entries: list[CompileEntry],
    source_filter: str | None = None,
) -> BuildContext:
    """Union strategy: merge flags from all TUs (ADR-020a fallback).

    Used when a header cannot be matched to a specific TU.  Unions
    defines and include paths, warns on conflicts.

    Args:
        entries: Parsed compile database entries.
        source_filter: Optional glob pattern to filter source files.

    Returns:
        BuildContext with merged flags.
    """
    filtered = _filter_entries_by_glob(entries, source_filter)
    if not filtered:
        return BuildContext()

    contexts = [_extract_flags(e.arguments, e.directory) for e in filtered]

    merged_defines, define_conflicts = _merge_defines_from_contexts(contexts)
    merged_undefines: set[str] = set()
    for ctx in contexts:
        merged_undefines |= ctx.undefines

    merged_includes = _merge_path_list(contexts, "include_paths")
    merged_sys_includes = _merge_path_list(contexts, "system_includes")
    lang_std, standards = _pick_best_standard(contexts)
    target, sysroot = _pick_target_sysroot(contexts)
    merged_extra = _merge_extra_flags(contexts)

    return BuildContext(
        defines=merged_defines,
        undefines=merged_undefines,
        include_paths=merged_includes,
        system_includes=merged_sys_includes,
        language_standard=lang_std,
        target_triple=target,
        sysroot=sysroot,
        extra_flags=merged_extra,
        compile_db_path=filtered[0].directory / "compile_commands.json",
        define_conflicts=define_conflicts,
        standard_variants=standards,
    )
