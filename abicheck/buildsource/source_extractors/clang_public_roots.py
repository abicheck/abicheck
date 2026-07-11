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

"""Public-header-root equivalence + path classification for the clang extractor.

Split from ``clang.py`` (leaf module — must not import from its sibling). L4
replay parses the source checkout/build tree, but release/package validation
commonly passes public-header roots from an *installed* package: those are
different absolute trees even when they hold the same public headers, so pure
segment matching would classify every AST declaration as non-public. The helpers
here recognize that an include path *mirrors* an installed public-header root and
promote it to an equivalent public root for the TU (used both for classification
and for the per-TU cache key).

Pure: no clang invocation, only filesystem sampling of include trees.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from ..build_evidence import CompileUnit
from ._argv import (
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
    unredact_home,
)

_PUBLIC_ROOT_SAMPLE_LIMIT = 128
_PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES = 2
_PUBLIC_FILE_ROOT_SUFFIX_LIMIT = 6
_PUBLIC_HEADER_SUFFIXES = (
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".h++",
    ".inc",
    ".inl",
    ".ipp",
    ".tcc",
    ".tpp",
)


def _file_segments(path: str) -> tuple[str, ...]:
    posix = path.replace("\\", "/")
    return tuple(p for p in PurePosixPath(posix).parts if p not in ("/", ".", ""))


def _matches_exact_public_header(
    header: str, exact_header_segs: list[tuple[str, ...]]
) -> bool:
    header_segs = _file_segments(header)
    return any(
        len(header_segs) >= len(root) and header_segs[-len(root) :] == root
        for root in exact_header_segs
    )


def _header_samples(root: str) -> tuple[bool, list[Path]]:
    """Relative header names under an existing public root, bounded for speed.

    Package/public roots often point at an installed SDK include tree while the
    compile unit reads the corresponding build-tree include directory. A small
    deterministic sample is enough to recognize that equivalence without hashing
    or scanning the entire SDK for every TU.
    """
    p = Path(unredact_home(root)).expanduser()
    if p.is_file() and _looks_like_public_header(p):
        return True, _path_suffixes(p, _PUBLIC_FILE_ROOT_SUFFIX_LIMIT)
    if not p.is_dir():
        return False, []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames.sort()
        for filename in sorted(filenames):
            child = Path(dirpath) / filename
            if not _looks_like_public_header(child):
                continue
            out.append(child.relative_to(p))
            if len(out) >= _PUBLIC_ROOT_SAMPLE_LIMIT:
                return False, out
    return False, out


def _path_suffixes(path: Path, limit: int) -> list[Path]:
    parts = tuple(part for part in path.parts if part not in (path.anchor, "/", ""))
    return [Path(*parts[-n:]) for n in range(min(limit, len(parts)), 0, -1)]


def _looks_like_public_header(path: Path) -> bool:
    return path.suffix.lower() in _PUBLIC_HEADER_SUFFIXES or not path.suffix


def _compile_unit_include_dir(raw_inc: str, compile_unit: CompileUnit) -> Path:
    inc = Path(unredact_home(raw_inc)).expanduser()
    if inc.is_absolute():
        return inc
    directory = Path(unredact_home(compile_unit.directory or ".")).expanduser()
    return directory / inc


def _compile_unit_include_roots(
    compile_unit: CompileUnit, compiler_binary: str | None = None
) -> list[tuple[str, Path]]:
    roots = [
        (raw, _compile_unit_include_dir(raw, compile_unit))
        for raw in compile_unit.include_paths
    ]
    roots.extend(
        (raw, _compile_unit_include_dir(raw, compile_unit))
        for raw in compile_unit.system_include_paths
    )
    cc_bin = pick_compiler_binary(compile_unit, compiler_binary)
    cc_id = "msvc" if is_msvc_mode(cc_bin) else "gnu"
    replay_flags = replay_extra_flags(compile_unit, [], cc_id)
    i = 0
    while i < len(replay_flags):
        tok = replay_flags[i]
        raw: str | None = None
        if tok in {"-iquote", "-idirafter"} and i + 1 < len(replay_flags):
            raw = replay_flags[i + 1]
            i += 2
        elif tok.startswith("-iquote") and len(tok) > len("-iquote"):
            raw = tok[len("-iquote") :]
            i += 1
        elif tok.startswith("-idirafter") and len(tok) > len("-idirafter"):
            raw = tok[len("-idirafter") :]
            i += 1
        elif cc_id == "msvc" and tok in {"/I", "-I"} and i + 1 < len(replay_flags):
            raw = replay_flags[i + 1]
            i += 2
        elif (
            cc_id == "msvc"
            and len(tok) > 2
            and (tok.startswith("/I") or tok.startswith("-I"))
        ):
            raw = tok[2:]
            i += 1
        else:
            i += 1
        if raw:
            roots.append((raw, _compile_unit_include_dir(raw, compile_unit)))
    return roots


def _root_spelling(raw_inc: str, resolved_inc: Path, rel: Path | None) -> str:
    base = _include_spelling_base(raw_inc, resolved_inc)
    if rel is not None:
        return str(base / rel)
    return _dir_spelling(base)


def _include_spelling_base(raw_inc: str, resolved_inc: Path) -> Path:
    raw_path = Path(raw_inc)
    raw_unredacted = Path(unredact_home(raw_inc)).expanduser()
    return (
        resolved_inc
        if raw_path.is_absolute() or raw_unredacted.is_absolute()
        else raw_path
    )


def _dir_spelling(path: Path) -> str:
    spelling = str(path)
    return (
        spelling
        if path.is_absolute() or spelling.endswith(("/", "\\"))
        else spelling + "/"
    )


def _can_promote_whole_root(raw_inc: str, matched: list[Path]) -> bool:
    raw_path = Path(raw_inc)
    # A dot include root has no useful public path segments (`./` is dropped by
    # provenance); keep matched files instead of a whole-root marker.
    if str(raw_path) in {"", "."}:
        return False
    return len(matched) >= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES


def _is_dot_include_root(raw_inc: str) -> bool:
    return str(Path(raw_inc)) in {"", "."}


def _is_full_single_header_mirror(samples: list[Path], matched: list[Path]) -> bool:
    return len(samples) == 1 and len(matched) == 1


def _strip_leading_sample_dir(samples: list[Path]) -> list[Path]:
    stripped: list[Path] = []
    for rel in samples:
        parts = rel.parts
        if len(parts) <= 1:
            continue
        stripped.append(Path(*parts[1:]))
    return stripped


def _mirror_dir_candidate(
    raw_inc: str, inc: Path, prefix: Path | None, *, for_cache: bool
) -> str:
    if prefix is None:
        return str(inc) if for_cache else _root_spelling(raw_inc, inc, None)
    if for_cache:
        return str(inc / prefix)
    return _dir_spelling(_include_spelling_base(raw_inc, inc) / prefix)


def _public_root_samples(
    public_header_roots: list[str],
) -> dict[str, tuple[bool, list[Path], list[Path]]]:
    """Sampled headers per public root: root -> (is_file_root, root suffixes, samples)."""
    samples_by_root: dict[str, tuple[bool, list[Path], list[Path]]] = {}
    for root in public_header_roots:
        is_file_root, samples = _header_samples(root)
        if samples:
            root_path = Path(unredact_home(root)).expanduser()
            samples_by_root[root] = (
                is_file_root,
                []
                if is_file_root
                else _path_suffixes(root_path, _PUBLIC_FILE_ROOT_SUFFIX_LIMIT),
                samples,
            )
    return samples_by_root


def _prefixed_or_stripped_match(
    raw_inc: str,
    inc: Path,
    samples: list[Path],
    root_prefixes: list[Path],
    matched: list[Path],
) -> tuple[list[Path], Path | None]:
    """Retry sample matching under a root-suffix prefix, then with the leading sample dir stripped."""
    for candidate_prefix in root_prefixes:
        prefixed = [candidate_prefix / rel for rel in samples]
        prefixed_matched = [rel for rel in prefixed if (inc / rel).is_file()]
        if _can_promote_whole_root(
            raw_inc, prefixed_matched
        ) or _is_full_single_header_mirror(samples, prefixed_matched):
            return prefixed_matched, candidate_prefix
    if not _can_promote_whole_root(raw_inc, matched):
        stripped = _strip_leading_sample_dir(samples)
        stripped_matched = [rel for rel in stripped if (inc / rel).is_file()]
        if _can_promote_whole_root(
            raw_inc, stripped_matched
        ) or _is_full_single_header_mirror(samples, stripped_matched):
            return stripped_matched, None
    return matched, None


def _mirror_candidates(
    raw_inc: str,
    inc: Path,
    samples: list[Path],
    matched: list[Path],
    prefix: Path | None,
    *,
    is_file_root: bool,
    for_cache: bool,
) -> list[str]:
    """Equivalent-root spellings to record for one include dir mirroring one public root."""
    if for_cache:
        return [str(inc / rel) for rel in matched]
    if is_file_root or (
        _is_dot_include_root(raw_inc)
        and len(matched) >= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES
    ):
        return [_root_spelling(raw_inc, inc, rel) for rel in matched]
    if _is_full_single_header_mirror(samples, matched):
        return [_root_spelling(raw_inc, inc, matched[0])]
    if not _can_promote_whole_root(raw_inc, matched):
        return []
    if matched:
        return [_mirror_dir_candidate(raw_inc, inc, prefix, for_cache=for_cache)]
    return []


def _equivalent_public_roots_for_unit(
    public_header_roots: list[str],
    compile_unit: CompileUnit,
    *,
    for_cache: bool = False,
    compiler_binary: str | None = None,
) -> list[str]:
    """Add build include dirs that mirror an installed public-header root.

    L4 replay parses the source checkout/build tree, but release/package
    validation commonly passes `-H` roots from an extracted package. Those paths
    are different absolute trees even when they contain the same public headers,
    so pure segment matching classifies every AST declaration as non-public.
    When an include path contains the same relative public headers as an existing
    public root, treat that include path as an equivalent public root for this TU.
    """
    roots = list(public_header_roots)
    seen = {unredact_home(r) for r in roots}
    samples_by_root = _public_root_samples(public_header_roots)
    if not samples_by_root:
        return roots

    for raw_inc, inc in _compile_unit_include_roots(compile_unit, compiler_binary):
        whole_root = str(inc) if for_cache else _root_spelling(raw_inc, inc, None)
        if not inc.is_dir() or whole_root in seen:
            continue
        for is_file_root, root_prefixes, samples in samples_by_root.values():
            matched = [rel for rel in samples if (inc / rel).is_file()]
            if is_file_root and matched:
                matched = matched[:1]
            prefix: Path | None = None
            if not is_file_root and root_prefixes:
                matched, prefix = _prefixed_or_stripped_match(
                    raw_inc, inc, samples, root_prefixes, matched
                )
            for candidate in _mirror_candidates(
                raw_inc,
                inc,
                samples,
                matched,
                prefix,
                is_file_root=is_file_root,
                for_cache=for_cache,
            ):
                if candidate not in seen:
                    roots.append(candidate)
                    seen.add(candidate)
    return roots
