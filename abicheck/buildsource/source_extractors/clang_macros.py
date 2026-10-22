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
"""Preprocessor-macro extraction for the clang source extractor (ADR-030 D6):
``macros_from_preprocessor`` parses ``clang -E -dD`` output into public-header
macro entities. Pure: operates on the already-captured text."""

from __future__ import annotations

import re
from pathlib import Path

from ...header_conditionals import _include_guard_macro, _strip_comments
from ..model import LayerConfidence
from ..source_abi import (
    SourceEntity,
)
from .clang_node_predicates import _ClassifyContext, _location
from .clang_nodes import (
    _entity_names,
    _entity_ownership,
    _hash,
)

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8). Bump on ANY recipe change so a
#: stale ``--cache-dir`` never reuses an old dump. 0.8: also folds in the
#: resolved compiler override (Codex review). 0.9: the reconstructed command now
#: appends ``-fsycl-host-only`` for a SYCL-enabled Intel oneAPI invocation
#: (Codex review) -- a pre-0.9 cache entry for the same TU was built without
#: that flag and must not be reused as-is. 0.10/0.11: a real build's own
#: recorded ``-fsycl-host-only``/``-fsycl-device-only`` (Intel-only SYCL
#: pass-selector flags carried through via ``abi_relevant_flags``) on a
#: non-Intel invoked binary now degrades that one TU (a clear
#: SourceExtractionError) instead of failing on the raw "unknown argument".
#: 0.10 first shipped this only for ``-fsycl-device-only``, silently
#: STRIPPING a recorded ``-fsycl-host-only`` instead -- confirmed against a
#: real clang install (Codex review) that this was itself wrong: stock
#: clang's bare ``-fsycl`` defines ``__SYCL_DEVICE_ONLY__`` (device context
#: by default), so dropping just the host-only selector silently replayed
#: an explicitly host-pinned TU as device code. 0.11 raises for both
#: selectors symmetrically; a bare, unselected ``-fsycl`` (ambiguous, no
#: explicit pin either way) is left carried through as-is, matching the L2
#: header-AST backend's own established "accepted approximation" for that
#: shape (see ``needs_sycl_host_only``'s own docstring). 0.12:
#: ``compile_invocation._carry_abi_relevant_flags`` (shared by castxml/clang) no longer
#: dedups a repeated flag WITHIN ``abi_relevant_flags`` itself -- a real,
#: layered build config can legitimately record the same toggle-style flag
#: more than once (e.g. ``-fno-sycl -fsycl -fno-sycl``), and the old dedup
#: silently dropped every repeat, corrupting last-flag-wins state for any
#: such pair, not just SYCL (Codex review). 0.13: ``_stamp_fact_set_and_
#: coverage`` now stamps a real ``compiler_family`` (``"intel-llvm"`` for
#: Intel's oneAPI fork, ``"clang"`` unchanged otherwise) instead of always
#: the ``default_fact_set`` default -- a cache entry produced under 0.12 or
#: earlier carries the stale ``"clang"`` label baked into its persisted
#: ``fact_set`` and, on a cache HIT, is returned as-is by
#: ``source_replay._replay_cache_lookup`` without ever re-running
#: ``_stamp_fact_set_and_coverage`` (Codex review, PR #756: a persistent
#: ``--source-abi-cache-dir``/``ABICHECK_L4_CACHE_DIR`` reused across an
#: abicheck upgrade would otherwise silently keep serving the old label
#: forever, since ``compute_tu_cache_key`` doesn't fold ``compiler_family``
#: in on its own -- only the extractor version invalidates it).


#: A ``-E`` line marker: ``# <line> "<file>" [flags]`` — sets the current file.
_LINE_MARKER_RE = re.compile(r'^#\s+\d+\s+"([^"]*)"')


#: A C identifier (a macro name).
_MACRO_NAME_RE = re.compile(r"[A-Za-z_]\w*")


def _parse_define(rest: str) -> tuple[str, str] | None:
    """Parse the text after ``#define `` into ``(name, normalized-value)``.

    Keeps the function-like parameter list as part of the value (``(a,b) body``),
    so a change to either the parameters or the body reads as a value change.
    """
    m = _MACRO_NAME_RE.match(rest)
    if not m:
        return None
    name = m.group(0)
    i = m.end()
    params = ""
    if i < len(rest) and rest[i] == "(":  # function-like macro
        depth = 0
        j = i
        while j < len(rest):
            if rest[j] == "(":
                depth += 1
            elif rest[j] == ")":
                depth -= 1
            j += 1
            if depth == 0:
                break
        params = rest[i:j]
        i = j
    body = rest[i:].strip()
    value = re.sub(r"\s+", " ", f"{params} {body}".strip())
    return name, value


def _is_include_guard(name: str, value: str, file: str) -> bool:
    """Whether ``name`` is the include guard of ``file`` (ADR-030 follow-up #2).

    Include guards (``#ifndef FOO_H`` / ``#define FOO_H``) surface from the
    ``-E -dD`` pass as empty-valued macro entities — harmless but noisy. Both
    checks below require an empty replacement (a guard never expands to
    anything), which keeps a real empty feature flag (e.g.
    ``#define FOO_ENABLED``) from being dropped:

    - **Filename-derived** (cheap, no I/O): the name, with surrounding
      underscores stripped, equals the header's filename-derived token
      including the extension suffix (``foo.h`` → ``FOO_H``; matches
      ``FOO_H``, ``_FOO_H``, ``FOO_H_``, ``__FOO_H__``). Exact match, not a
      substring, so an intentional empty feature macro that merely starts
      with the stem (``FOO_H_FEATURE``) is not dropped.
    - **Structural** (fallback): a project-prefixed guard
      (``MYLIB_FOO_H``, ``CASE47_V1_HPP``) doesn't derive from the filename
      at all, so the preprocessed ``-E -dD`` stream's spelling-only signal
      misses it. Reading the file's own source and checking whether ``name``
      is genuinely its leading ``#ifndef``/``#define`` pair (the standard
      whole-file guard idiom, see :func:`_include_guard_macro`) catches this
      regardless of naming convention. Best-effort: a read failure just skips
      to the false-negative default below.
    """
    if value or not file:
        return False
    base = re.split(r"[\\/]", file)[-1]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", base).upper().strip("_")  # foo.h -> FOO_H
    if stem and name.upper().strip("_") == stem:
        return True
    try:
        text = Path(file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = _strip_comments(text).splitlines()
    return _include_guard_macro(lines) == name


def _unfold_continuations(lines: list[str]) -> list[str]:
    """Join backslash-continued physical lines into single logical lines.

    A multi-line macro (``#define FOO(x) \\`` then its body) is split by
    ``splitlines()``; without unfolding, only the first physical line — usually
    ending in ``\\`` — is captured and the rest of the body is dropped, hiding
    any edit below the first line from ``public_macro_value_changed`` (CodeRabbit
    review). ``#`` line markers never carry a trailing backslash, so they are
    unaffected.
    """
    out: list[str] = []
    pending: str | None = None
    for line in lines:
        chunk = line[:-1] if line.endswith("\\") else line
        pending = chunk if pending is None else pending + " " + chunk.lstrip()
        if not line.endswith("\\"):
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


def macros_from_preprocessor(
    text: str, public_header_roots: list[str]
) -> tuple[list[SourceEntity], list[str]]:
    """Parse ``clang -E -dD`` output into public-header macro entities (ADR-030 D6).

    Pure: tracks the current file from ``#`` line markers, records the final
    definition of each macro (honoring later ``#undef``), and keeps only macros
    whose declaring file is on the public source surface — builtin/command-line
    and system macros carry ``<built-in>``/system files and are filtered out.

    Returns ``(macro entities, every real file the preprocessor read)``. The file
    list feeds the per-TU cache dependency set, so it must contain *all* files
    the preprocessor touched — not just the public macro-declaring ones — or a
    macro-only *private* header (e.g. ``detail/config.h`` whose ``#define`` gates
    an ``#if`` in a public header) would never invalidate the dump: it
    contributes no public macro entity and no clang AST node, so an edit to it
    would otherwise pass cache validation and reuse stale facts (Codex review
    #339, P2; P1 covered only the public ones).
    """
    ctx = _ClassifyContext(public_header_roots)
    current = ""
    defs: dict[str, tuple[str, str]] = {}  # name -> (value, file)
    # Every real file named by a `#` line marker — the complete set the
    # preprocessor read. `<built-in>`/`<command line>`/`<scratch space>`
    # pseudo-files are not real dependencies and are skipped.
    touched: set[str] = set()
    for line in _unfold_continuations(text.splitlines()):
        marker = _LINE_MARKER_RE.match(line)
        if marker:
            current = marker.group(1)
            if current and not current.startswith("<"):
                touched.add(current)
            continue
        if line.startswith("#define "):
            parsed = _parse_define(line[len("#define ") :])
            if parsed:
                defs[parsed[0]] = (parsed[1], current)
        elif line.startswith("#undef "):
            defs.pop(line[len("#undef ") :].strip(), None)

    entities: list[SourceEntity] = []
    for name, (value, file) in sorted(defs.items()):
        visibility, origin, public = ctx.classify(file)
        if not public:
            continue
        if _is_include_guard(name, value, file):
            continue
        entities.append(
            SourceEntity(
                id=_hash("macro", name, value),
                kind="macro",
                qualified_name=name,
                value=value,
                names=_entity_names(name),
                ownership=_entity_ownership(visibility, origin),
                source_location=_location(file, 0, origin),
                visibility=visibility,
                api_relevant=True,
                confidence=LayerConfidence.HIGH,
            )
        )
    return entities, sorted(touched)
