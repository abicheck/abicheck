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

"""P0.3: derive an L2 header-AST :class:`CompileContext` from L3 build evidence.

abicheck's L2 header-AST parsing (CastXML / direct-Clang) has historically run
with only user-supplied ``--gcc-options``/``compile:`` context, or nothing —
never the real build's own ``CompileUnit`` facts (``buildsource/adapters/
compile_db.py``, ``cmake_file_api.py``, ``ninja.py``, ``bazel.py``,
``make.py``), even when that L3 evidence is already available for a run (a
``--sources``/``--build-info`` pack, or inline collection). abicheck already
*detects* the resulting drift as an advisory finding
(``ChangeKind.HEADER_PARSE_CONTEXT_DRIFT`` / ``HEADER_BUILD_CONTEXT_MISMATCH``)
but never closed the loop by applying the evidence automatically. This module
is that missing seam: it turns a set of headers plus a collected
:class:`~abicheck.buildsource.build_evidence.BuildEvidence` into a single,
agreed-upon :class:`~abicheck.compile_context.CompileContext` (standard,
defines/undefines, ordered include search paths, sysroot, target triple, and
ABI-relevant flags such as ``-fPIC``/``-fno-omit-frame-pointer``), reusing the
already-extracted ``CompileUnit`` facts rather than re-parsing
``compile_commands.json`` from scratch.

**Which ``CompileUnit``s are "relevant" to a header?** A ``CompileUnit``
compiles a translation unit (a ``.c``/``.cpp`` source), not a header, so there
is no direct header→TU edge in the model. This mirrors the identical problem
``build_context.py`` (ADR-020a's ``-p``/``--compile-db`` for a *raw*
``compile_commands.json``) already solved for a single header: a lightweight,
best-effort scan of each candidate TU's own source text for an ``#include`` of
the header (by path-suffix match, falling back to a bare filename match) —
:func:`_compile_unit_references_header` below is that same heuristic, applied
to ``CompileUnit`` (redacted ``source``/``directory`` fields) rather than
``build_context.CompileEntry``.

**Single-context vs. ambiguous (P0.3's own scope boundary, per the plan).**
Once every ``CompileUnit`` that references at least one of the requested
headers is found, they are grouped by their *effective ABI-relevant context*
— the exact tuple of fields the plan names in point 1 (language, standard,
target triple, defines/undefines, ordered include paths, sysroot, ABI-relevant
flags). Mirrors the discipline the "toolchain-identity" / ``lang_explicit``
precedent in AGENTS.md's "Known gaps" section establishes: *resolve once,
thread explicitly, fail closed on ambiguity* — never a new, different "just
pick one" heuristic.

- **Zero matching units**: no evidence to apply. Returns an empty resolution;
  the caller's existing (pre-P0.3) behavior is unchanged, so the
  ``header_parse_context_drift``/``header_build_context_mismatch`` advisory
  findings keep firing exactly as before (backward compatible default).
- **Exactly one distinct effective context**: applied automatically — the
  common real-world case this PR implements as a genuine working slice.
- **Two or more distinct effective contexts** (e.g. two TUs compiling the same
  public header under a different ``-std=``/``-fPIC``/target triple/macro
  set): fails closed with :class:`~abicheck.errors.HeaderCompileContextAmbiguousError`
  rather than silently choosing one TU's context over another's — full
  multi-context snapshot support (representing *both* contexts in one
  ``AbiSnapshot``) is out of scope for this pass; see the module's own PR
  description / AGENTS.md follow-up note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..compile_context import CompileContext
from ..errors import HeaderCompileContextAmbiguousError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .build_evidence import BuildEvidence, CompileUnit

__all__ = [
    "HeaderCompileContextResolution",
    "resolve_header_compile_context",
]


def _resolve_cu_relative_path(raw: str, directory: str) -> Path:
    """Expand a redacted/relative ``CompileUnit`` path field for reading.

    Mirrors ``build_evidence._resolved_object``'s treatment of ``output``:
    ``CompileUnit`` string fields are normalized *for persistence* (a
    home-rooted path redacted to ``~/...``, ADR-032 D7; a relative path from
    an adapter like Ninja/Make/Bazel resolved against the unit's own
    ``directory``) — not guaranteed directly openable.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute() and directory:
        path = Path(directory).expanduser() / path
    return path


#: Matches ``#include "..."``/``#include <...>`` whose argument *contains* the
#: header's bare filename — narrowed further below by checking the header's
#: full (resolved) path is a suffix of the matched include argument, the same
#: two-stage match ``build_context._header_included_by_tu`` uses to cut down
#: false positives from an unrelated header sharing a filename.
def _include_pattern(header_name: str) -> re.Pattern[str]:
    return re.compile(rf'#\s*include\s*[<"]([^>"]*{re.escape(header_name)})[>"]')


def _compile_unit_references_header(cu: CompileUnit, header_resolved: Path) -> bool:
    """Best-effort: does *cu*'s own source text ``#include`` *header_resolved*?"""
    header_name = header_resolved.name
    if not header_name:
        return False
    src_path = _resolve_cu_relative_path(cu.source, cu.directory)
    try:
        text = src_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if header_name not in text:
        return False
    for m in _include_pattern(header_name).finditer(text):
        include_arg = m.group(1)
        if include_arg == header_name or str(header_resolved).endswith(include_arg):
            return True
    return False


def _matching_compile_units(
    compile_units: Sequence[CompileUnit], headers: Sequence[Path]
) -> list[CompileUnit]:
    """Every ``CompileUnit`` that ``#include``s at least one of *headers*."""
    matched: list[CompileUnit] = []
    seen_ids: set[str] = set()
    resolved_headers = [h.resolve() for h in headers]
    for cu in compile_units:
        if cu.id in seen_ids:
            continue
        if any(_compile_unit_references_header(cu, h) for h in resolved_headers):
            matched.append(cu)
            seen_ids.add(cu.id)
    return matched


@dataclass(frozen=True)
class _EffectiveContextSignature:
    """The ABI-relevant fields the plan's point 1 + point 3 name, normalized.

    Equality/hashing over this tuple is the "agree" test: two ``CompileUnit``s
    that reference the same header must produce an *identical* signature for
    P0.3 to apply a single context automatically. Deliberately exact (order-
    preserving for include paths / ABI-relevant flags, since search order and
    flag repetition can themselves be ABI-relevant) rather than a looser
    semantic-equivalence check — a stricter "agree" test can only ever turn a
    would-be single-context case into a (still-safe) ambiguous one, never the
    reverse, so this stays on the conservative, fail-closed side.
    """

    language: str
    standard: str
    target_triple: str
    sysroot: str
    defines: tuple[tuple[str, str], ...]
    undefines: tuple[str, ...]
    include_paths: tuple[str, ...]
    system_include_paths: tuple[str, ...]
    abi_relevant_flags: tuple[str, ...]

    @classmethod
    def of(cls, cu: CompileUnit) -> _EffectiveContextSignature:
        return cls(
            language=cu.language,
            standard=cu.standard,
            target_triple=cu.target_triple,
            sysroot=cu.sysroot or "",
            defines=tuple(sorted(cu.defines.items())),
            undefines=tuple(sorted(cu.undefines)),
            include_paths=tuple(cu.include_paths),
            system_include_paths=tuple(cu.system_include_paths),
            abi_relevant_flags=tuple(cu.abi_relevant_flags),
        )


def _context_flags(cu: CompileUnit) -> list[str]:
    """Render one ``CompileUnit``'s context as literal castxml/clang argv tokens.

    Mirrors ``BuildContext.to_castxml_flags()`` (ADR-020a's ``-p``/
    ``--compile-db`` -> castxml-flags path in ``build_context.py``) field for
    field, so a ``CompileUnit``-derived context and a raw-``compile_commands.
    json``-derived one produce the same shape of literal tokens — deliberately
    not importing/reusing that dataclass directly to keep this module a
    self-contained ``buildsource``-internal leaf with no dependency on the
    top-level ``build_context`` module's larger response-file/redaction
    machinery, which a ``CompileUnit`` (already fully resolved + redacted by
    its adapter) has no use for.
    """
    flags: list[str] = []
    if cu.standard and "++" in cu.standard:
        flags.append(f"-std={cu.standard}")
    if cu.target_triple:
        flags.append(f"--target={cu.target_triple}")
    if cu.sysroot:
        flags.append(f"--sysroot={_resolve_cu_relative_path(cu.sysroot, cu.directory)}")
    for macro, value in sorted(cu.defines.items()):
        flags.append(f"-D{macro}={value}" if value else f"-D{macro}")
    for macro in sorted(cu.undefines):
        flags.append(f"-U{macro}")
    for inc in cu.include_paths:
        flags.extend(["-I", str(_resolve_cu_relative_path(inc, cu.directory))])
    for inc in cu.system_include_paths:
        flags.extend(["-isystem", str(_resolve_cu_relative_path(inc, cu.directory))])
    flags.extend(cu.abi_relevant_flags)
    return flags


@dataclass(frozen=True)
class HeaderCompileContextResolution:
    """Outcome of :func:`resolve_header_compile_context`.

    ``context`` is ``None`` whenever there was nothing to apply (no L3
    evidence, or no ``CompileUnit`` references any of the requested headers)
    — a plain, silent degrade to the pre-P0.3 behavior, never an error.
    ``matched_unit_count`` is the number of distinct ``CompileUnit``s found to
    reference the requested headers, always > 0 when ``context`` is not
    ``None`` (used by callers to decide whether to stamp
    ``AbiSnapshot.parsed_with_build_context``).
    """

    context: CompileContext | None = None
    matched_unit_count: int = 0

    @property
    def matched(self) -> bool:
        return self.context is not None


_EMPTY_RESOLUTION = HeaderCompileContextResolution()


def resolve_header_compile_context(
    build_evidence: BuildEvidence | None,
    headers: Sequence[Path],
) -> HeaderCompileContextResolution:
    """Resolve a single L2 :class:`CompileContext` from L3 ``CompileUnit`` facts.

    Best-effort and additive: returns an empty (``context=None``) resolution
    whenever there is nothing to apply — no build evidence, no compile units,
    or no header the given ``CompileUnit``s reference — rather than raising,
    so a caller with no L3 evidence (or a header the build evidence simply
    doesn't cover) sees the exact same behavior as before this module existed.

    Raises :class:`~abicheck.errors.HeaderCompileContextAmbiguousError` when
    two or more of the matched ``CompileUnit``s disagree on an ABI-relevant
    field — the one case this function refuses to guess at (see the module
    docstring's "single-context vs. ambiguous" section).
    """
    if build_evidence is None or not build_evidence.compile_units or not headers:
        return _EMPTY_RESOLUTION
    resolved_headers = [Path(h) for h in headers]
    matched = _matching_compile_units(build_evidence.compile_units, resolved_headers)
    if not matched:
        return _EMPTY_RESOLUTION

    by_signature: dict[_EffectiveContextSignature, list[CompileUnit]] = {}
    for cu in matched:
        by_signature.setdefault(_EffectiveContextSignature.of(cu), []).append(cu)

    if len(by_signature) > 1:
        raise HeaderCompileContextAmbiguousError(
            _ambiguity_message(headers, by_signature)
        )

    ((_sig, units),) = by_signature.items()
    flags = _context_flags(units[0])
    context = CompileContext(gcc_option_tokens=tuple(flags))
    return HeaderCompileContextResolution(
        context=context, matched_unit_count=len(matched)
    )


def _ambiguity_message(
    headers: Sequence[Path],
    by_signature: dict[_EffectiveContextSignature, list[CompileUnit]],
) -> str:
    header_names = ", ".join(sorted({h.name for h in headers}))
    lines = [
        f"Public header(s) [{header_names}] are compiled under "
        f"{len(by_signature)} materially different, ABI-relevant compile "
        "contexts across the available L3 build evidence (differing "
        "-std=/target/defines/include-search-order/sysroot/ABI-relevant "
        "flags); abicheck cannot pick one context over another without "
        "guessing. Narrow the input (--compile-db-filter / a project "
        "compile: block / --gcc-options pinning the ambiguous field(s)) or "
        "compare a header per contract at a time. Conflicting translation "
        "units:",
    ]
    for sig, units in sorted(by_signature.items(), key=lambda kv: kv[0].standard):
        sample = units[0]
        lines.append(
            f"  - {sample.source or sample.id!r}: std={sig.standard or '(default)'} "
            f"target={sig.target_triple or '(default)'} "
            f"abi_flags={list(sig.abi_relevant_flags)}"
        )
    return "\n".join(lines)
