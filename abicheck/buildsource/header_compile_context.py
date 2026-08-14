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
:func:`_cu_references_any_header` below is that same heuristic, applied
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

import os
import re
import shlex
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from .._compiler_options import explicit_language_standard
from ..compile_context import CompileContext
from ..errors import HeaderCompileContextAmbiguousError
from ..header_utils import iter_directory_headers
from .build_query import PRUNED_HEADER_DIR_SEGMENTS

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
#:
#: Cached: the same header name (`Path.name`) recurs across every compile
#: unit a multi-TU build's headers get matched against, and compiling the
#: same pattern once per (unit, header) pair — the shape this was called in
#: before the read/scan refactor below — was pure repeated overhead for an
#: identical regex.
@cache
def _include_pattern(header_name: str) -> re.Pattern[str]:
    return re.compile(rf'#\s*include\s*[<"]([^>"]*{re.escape(header_name)})[>"]')


def _cu_references_any_header(
    cu: CompileUnit, headers_resolved: Sequence[Path]
) -> bool:
    """Best-effort: does *cu*'s own source text ``#include`` any of *headers_resolved*?

    Reads *cu*'s source text exactly once and tests it against every
    candidate header, rather than re-reading and re-scanning the same file
    once per header (the shape :func:`_matching_compile_units` used to drive
    this in, an O(units * headers) file-read cost for what is inherently one
    read per unit).
    """
    src_path = _resolve_cu_relative_path(cu.source, cu.directory)
    try:
        text = src_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for header_resolved in headers_resolved:
        header_name = header_resolved.name
        if not header_name or header_name not in text:
            continue
        for m in _include_pattern(header_name).finditer(text):
            include_arg = m.group(1)
            if include_arg == header_name or str(header_resolved).endswith(include_arg):
                return True
    return False


def _expand_header_directories(headers: Sequence[Path]) -> list[Path]:
    """Expand any directory-shaped entry in *headers* into its real header files.

    ``InputSpec.headers``/``-H`` may name a whole directory rather than an
    individual header file, and the normal L2 path already expands such an
    entry (``service_scan.expand_header_inputs``) into its actual header
    files before parsing -- without this, matching against the raw directory
    path itself finds no ``#include "<dirname>"``-shaped text in any TU
    source, so no compile unit is ever matched and this whole seam silently
    no-ops for a directory input.

    Deliberately *not* a call to ``service_scan.expand_header_inputs``
    itself: that function lives in the CLI/service import-cycle-allowlisted
    cluster, and (transitively, via ``scan_engine`` -> ``buildsource.
    l2_seed``) importing it from here -- a `buildsource/` leaf module
    `l2_seed.py` itself calls into -- closes a real import cycle
    (``header_compile_context`` -> ``service_scan`` -> ``scan_engine`` ->
    ``buildsource.l2_seed`` -> ``header_compile_context``), exactly what
    AGENTS.md's "What NOT to do" asks a change to avoid rather than
    resolve by extending ``IMPORT_CYCLE_ALLOWLIST``. Reusing
    ``header_utils.iter_directory_headers`` directly instead (the same
    walk ``expand_header_inputs`` itself delegates to, filtered by the
    identical ``HEADER_SUFFIXES``/pruned-segment set) keeps the expanded
    header set identical to what L2 actually parses, without the cycle.

    Best-effort, matching this module's own contract: a header path that
    doesn't exist, isn't a file/directory, or is an empty header directory
    is silently dropped rather than raised -- ``expand_header_inputs``
    itself raises ``ValidationError`` for those cases (real user-facing
    validation for an explicit ``-H``), but this function only ever
    degrades to "no evidence to apply" for the P0.3 seam, never surfaces a
    new failure mode for a header the seam merely couldn't expand.
    """
    out: list[Path] = []
    for h in headers:
        if h.is_dir():
            out.extend(iter_directory_headers(h, PRUNED_HEADER_DIR_SEGMENTS))
        elif h.is_file():
            out.append(h)
        # Neither a file nor a directory (missing, broken symlink, ...): best
        # effort, drop it -- matches `_cu_references_any_header`'s own
        # silent-skip-on-unreadable-input contract for the source side.
    return out


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
        if _cu_references_any_header(cu, resolved_headers):
            matched.append(cu)
            seen_ids.add(cu.id)
    return matched


def _explicit_pin_tokens(explicit: CompileContext | None) -> list[str]:
    """Flatten one explicit :class:`CompileContext`'s free-form + repeatable
    options into a single token list, for presence-only scanning below.

    Mirrors how both header command builders (``dumper_ast_config.py``)
    combine the two fields (``gcc_options`` shlex-split, then
    ``gcc_option_tokens`` verbatim) -- deliberately best-effort: a malformed
    ``gcc_options`` string degrades to "no tokens from it" rather than
    raising, since this is only used to *widen* what's accepted (Finding 3),
    never to narrow it.
    """
    if explicit is None:
        return []
    tokens: list[str] = []
    if explicit.gcc_options:
        try:
            tokens.extend(shlex.split(explicit.gcc_options, posix=os.name != "nt"))
        except ValueError:
            pass
    tokens.extend(explicit.gcc_option_tokens)
    return tokens


@dataclass(frozen=True)
class _ExplicitPin:
    """Which ABI-relevant *dimensions* an explicit :class:`CompileContext`
    already resolves (Finding 3).

    A caller who already pinned a field via ``--gcc-options``/
    ``--gcc-option`` (or the structured ``sysroot`` field) does not need the
    L3 evidence to agree on that field too -- ``resolve_header_compile_
    context`` masks a pinned field out of the multi-unit ambiguity
    comparison below, so a genuine disagreement on a *different*,
    unpinned field still fails closed (only the pinned dimension is
    excused, per field, not "any explicit override excuses every
    disagreement").

    Presence-only, not value-resolving: this deliberately does not attempt
    to compute what the *effective* value of a pinned field ends up being
    (that's ``dataclasses.replace``/last-flag-wins compiler semantics once
    the derived and explicit contexts are actually merged and rendered,
    handled by ``service_input_resolution._merge_l3_compile_context``) --
    only whether the caller stated an opinion on it at all.
    """

    standard: bool = False
    target_triple: bool = False
    sysroot: bool = False
    defines: frozenset[str] = field(default_factory=frozenset)
    undefines: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, explicit: CompileContext | None) -> _ExplicitPin:
        if explicit is None:
            return cls()
        tokens = _explicit_pin_tokens(explicit)
        target_triple = False
        sysroot = explicit.sysroot is not None
        defines: set[str] = set()
        undefines: set[str] = set()
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok in ("-target", "--target") or tok.startswith("--target="):
                target_triple = True
            elif tok in ("--sysroot", "-isysroot") or tok.startswith("--sysroot="):
                sysroot = True
            elif tok == "-D" and i + 1 < n:
                defines.add(tokens[i + 1].split("=", 1)[0])
                i += 1
            elif tok.startswith("-D") and len(tok) > 2:
                defines.add(tok[2:].split("=", 1)[0])
            elif tok == "-U" and i + 1 < n:
                undefines.add(tokens[i + 1])
                i += 1
            elif tok.startswith("-U") and len(tok) > 2:
                undefines.add(tok[2:])
            i += 1
        return cls(
            standard=explicit_language_standard(
                explicit.gcc_options, explicit.gcc_option_tokens
            )
            is not None,
            target_triple=target_triple,
            sysroot=sysroot,
            defines=frozenset(defines),
            undefines=frozenset(undefines),
        )


#: Raw ``abi_relevant_flags`` prefixes that are just alternate spellings of a
#: field this signature already compares *structurally* (``target_triple``/
#: ``sysroot``/``standard``). ``CompileUnit.abi_relevant_flags`` is captured
#: from raw argv by ``buildsource.adapters.base.extract_abi_relevant_flags``
#: (a prefix match over ``ABI_RELEVANT_FLAG_PREFIXES``) independently of the
#: adapter's own structured-field derivation, so two compile units resolving
#: to the *identical* structured value (e.g. ``target_triple ==
#: "aarch64-linux-gnu"`` on both) can still carry differently-spelled raw
#: survivors of the same flag (a complete single-token ``--target=X`` on one
#: unit, a split two-token ``-target X`` on another — the same ambiguity
#: already named in ``source_extractors._argv.STRUCTURED_TOOLCHAIN_FLAG_
#: PREFIXES``, which this list mirrors for the identical reason: those raw
#: flags "must NOT be carried through" as their own signal once a structured
#: field already represents them). Comparing the raw duplicates here as well
#: would raise a false ``HeaderCompileContextAmbiguousError`` even though the
#: two units' *effective* contexts genuinely agree. Excluding them from the
#: signature is safe in the same "only ever turns ambiguous into single-
#: context" direction ``_EffectiveContextSignature``'s own docstring already
#: relies on: a raw flag excluded here is still compared, just via its
#: structured field instead, so no real disagreement can be hidden by this
#: exclusion — only a spelling-only non-disagreement stops being flagged as
#: one. ``-std=``/``/std:`` (standard) is included too: a compile unit could
#: in principle carry both a structured ``standard`` and a raw ``-std=...``
#: survivor in ``abi_relevant_flags`` (the adapter's own extraction and its
#: structured-field derivation are independent passes over the same argv),
#: and the identical spelling-divergence risk applies. Unlike
#: :class:`_ExplicitPin`'s masking below (which only excludes a dimension the
#: *caller* explicitly pinned), this exclusion is unconditional -- it never
#: depends on whether an ``explicit`` context was even given, since the raw
#: flag is redundant with its structured field regardless.
#: The bare, separate-operand switch spellings (whose own following argv
#: token is the operand, captured structurally instead — see
#: ``_DANGLING_OPERAND_FLAGS`` below for the identical set used the same way
#: in ``_context_flags``). Matched by *exact* token equality, not by prefix:
#: a real ``clang -cc1``/driver invocation has several distinct flags that
#: merely start with the same characters and are NOT represented by any
#: structured field here — ``-target-abi``, ``-target-cpu``,
#: ``-target-feature``, ``-target-linker-version``,
#: ``-target-sdk-version=<value>`` (confirmed via a real ``clang -cc1
#: --help``) all begin with ``-target`` but carry independent ABI-relevant
#: information ``CompileUnit.target_triple`` does not capture at all. A
#: bare-prefix ``startswith("-target")`` match (the historical form of this
#: check) silently masked those too, collapsing two compile units that
#: genuinely disagree on e.g. ``-target-sdk-version=`` into one signature
#: instead of raising ``HeaderCompileContextAmbiguousError`` (Codex review).
_STRUCTURED_FIELD_EXACT_FLAGS = frozenset(
    {
        "-target",
        "--target",
        "--sysroot",
        "-isysroot",
    }
)

#: The complete, single-token combined-form spellings (operand attached via
#: ``=``/``:``) — safe to match by prefix since each is already a fixed,
#: literal lead-in with no sibling flag sharing it: no real clang/gcc/MSVC
#: flag begins with ``--target=``, ``--sysroot=``, ``-std=``, or ``/std:``
#: other than the flag itself (checked against a real ``clang --help``/
#: ``clang -cc1 --help`` for the exact same reason as the exact-match set
#: above — unlike the bare ``-target``/``--sysroot`` switches, none of these
#: combined forms collide with an unrelated flag).
_STRUCTURED_FIELD_FLAG_PREFIXES = (
    "--target=",
    "--sysroot=",
    "-std=",
    "/std:",
)


def _is_structured_field_flag(flag: str) -> bool:
    """Whether *flag* is fully represented by a structured
    ``target_triple``/``sysroot``/``standard`` field already, per the two
    sets above.
    """
    return flag in _STRUCTURED_FIELD_EXACT_FLAGS or flag.startswith(
        _STRUCTURED_FIELD_FLAG_PREFIXES
    )


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

    A field (or, for ``defines``/``undefines``, one specific macro key) that
    *pin* (see :class:`_ExplicitPin`) already resolves for the caller is
    masked to a shared placeholder here (Finding 3) — so two units that
    disagree only on a dimension the caller already pinned no longer read as
    a materially different signature, while a disagreement on any other,
    unpinned dimension still does.

    ``abi_relevant_flags`` here is *also* not ``cu.abi_relevant_flags``
    verbatim, independent of ``pin``: see ``_STRUCTURED_FIELD_FLAG_PREFIXES``
    and :func:`_mask_pinned_abi_flags` — a raw flag already fully represented
    by ``target_triple``/``sysroot``/``standard`` is excluded unconditionally,
    so two compile units that agree on the structured value but spell the
    flag that produced it differently (``--target=X`` vs. ``-target X``)
    compare equal here instead of falsely disagreeing, whether or not the
    caller pinned anything.
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
    def of(
        cls, cu: CompileUnit, pin: _ExplicitPin | None = None
    ) -> _EffectiveContextSignature:
        pin = pin or _ExplicitPin()
        return cls(
            language=cu.language,
            standard="" if pin.standard else cu.standard,
            target_triple="" if pin.target_triple else cu.target_triple,
            sysroot="" if pin.sysroot else (cu.sysroot or ""),
            defines=tuple(
                sorted((k, v) for k, v in cu.defines.items() if k not in pin.defines)
            ),
            undefines=tuple(sorted(u for u in cu.undefines if u not in pin.undefines)),
            include_paths=tuple(cu.include_paths),
            system_include_paths=tuple(cu.system_include_paths),
            abi_relevant_flags=tuple(
                _mask_pinned_abi_flags(cu.abi_relevant_flags, pin)
            ),
        )


def _mask_pinned_abi_flags(flags: Sequence[str], pin: _ExplicitPin) -> list[str]:
    """Drop ``cu.abi_relevant_flags`` entries a structured field already
    covers.

    ``adapters.base.extract_abi_relevant_flags`` records the *same* raw
    ``-std=``/``-target``/``--target=``/``--sysroot``/``-isysroot`` tokens
    into ``CompileUnit.abi_relevant_flags`` that also feed the *structured*
    ``standard``/``target_triple``/``sysroot`` fields
    :meth:`_EffectiveContextSignature.of` already compares directly (see
    ``_STRUCTURED_FIELD_FLAG_PREFIXES``'s own docstring) -- so two units
    agreeing on the structured value but spelling the flag that produced it
    differently (a complete ``--target=X`` vs. a split ``-target X``
    survivor) must not read as disagreeing raw-flag tuples.

    Unconditional, not gated on *pin*: this is a strict superset of what a
    pin-conditioned exclusion would give (Finding 3's own motivating case —
    a std-only disagreement the caller has explicitly pinned via
    ``explicit`` still needing its raw ``-std=c++17``/``-std=c++20``
    survivors excluded, or it would reopen the exact ambiguity the
    structured-field pin-masking in ``.of()`` was meant to close, is one
    instance of the general rule this function already applies regardless
    of ``pin``). *pin* is accepted for a symmetrical call signature with the
    structured-field masking above and is reserved for a future dimension
    (e.g. a pinned macro spelled only as a raw flag) that genuinely needs
    per-pin conditioning rather than the unconditional rule; it plays no
    role in today's target/sysroot/standard exclusion.
    """
    return [f for f in flags if not _is_structured_field_flag(f)]


#: Bare switch spellings whose operand is a *separate*, following argv token
#: (``-target aarch64-linux-gnu``, ``--sysroot /sdk``, ``-isysroot /sdk``).
#: ``extract_abi_relevant_flags`` (``buildsource/adapters/base.py``) captures
#: only the switch itself for these — its match is a plain prefix check with
#: no lookahead, unlike ``_extract_flags`` (``build_context.py``), which
#: consumes the operand token too and stores the resolved value in
#: ``CompileUnit.target_triple``/``.sysroot``. Forwarding one of these bare
#: tokens verbatim from ``cu.abi_relevant_flags`` therefore never recovers a
#: lost value (the operand was never captured there in the first place) — it
#: only emits a dangling switch that a real compiler either rejects outright
#: or, worse, silently pairs with whatever token happens to follow it in the
#: constructed command. This function already renders the equivalent,
#: complete ``--target=``/``--sysroot=`` combined form from the structured
#: ``cu.target_triple``/``cu.sysroot`` fields a few lines above, so dropping
#: the bare duplicate here loses nothing (finding 1).
_DANGLING_OPERAND_FLAGS = frozenset({"-target", "--sysroot", "-isysroot"})


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
    if cu.standard:
        flags.append(f"-std={cu.standard}")
    if cu.target_triple:
        flags.append(f"--target={cu.target_triple}")
    if cu.sysroot:
        # .as_posix() (not str()/plain f-string interpolation), matching the
        # exact convention both header command builders already use for a
        # sysroot Path (dumper_ast_config.py's `sysroot.as_posix()` in both
        # `_build_castxml_command`/`_build_clang_header_command`) -- a plain
        # `str(WindowsPath(...))` renders native `\`-separated components,
        # producing a `--sysroot=C:\opt\sysroot` token that a castxml/clang
        # invocation does not parse the same way as the forward-slash form
        # every other sysroot-flag rendering in this codebase emits.
        flags.append(
            f"--sysroot={_resolve_cu_relative_path(cu.sysroot, cu.directory).as_posix()}"
        )
    for macro, value in sorted(cu.defines.items()):
        flags.append(f"-D{macro}={value}" if value else f"-D{macro}")
    for macro in sorted(cu.undefines):
        flags.append(f"-U{macro}")
    for inc in cu.include_paths:
        flags.extend(["-I", _resolve_cu_relative_path(inc, cu.directory).as_posix()])
    for inc in cu.system_include_paths:
        flags.extend(
            ["-isystem", _resolve_cu_relative_path(inc, cu.directory).as_posix()]
        )
    flags.extend(f for f in cu.abi_relevant_flags if f not in _DANGLING_OPERAND_FLAGS)
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
    *,
    explicit: CompileContext | None = None,
) -> HeaderCompileContextResolution:
    """Resolve a single L2 :class:`CompileContext` from L3 ``CompileUnit`` facts.

    Best-effort and additive: returns an empty (``context=None``) resolution
    whenever there is nothing to apply — no build evidence, no compile units,
    or no header the given ``CompileUnit``s reference — rather than raising,
    so a caller with no L3 evidence (or a header the build evidence simply
    doesn't cover) sees the exact same behavior as before this module existed.

    *explicit* is the caller's own, already-supplied L2 context (``evidence.
    compile`` on the service_input_resolution path) — when given, any
    ABI-relevant dimension it already pins (an explicit ``-std=``/
    ``--target=``/``--sysroot=``/``-isysroot`` or a specific ``-D``/``-U``
    macro; see :class:`_ExplicitPin`) is excluded from the multi-unit
    ambiguity comparison below, since the caller's own value wins that
    dimension regardless of what the matched compile units say (Finding 3).
    Per-field, not per-request: a genuine disagreement on any *other*,
    unpinned dimension still fails closed.

    Raises :class:`~abicheck.errors.HeaderCompileContextAmbiguousError` when
    two or more of the matched ``CompileUnit``s disagree on an ABI-relevant
    field the caller has not already pinned via *explicit* — the one case
    this function refuses to guess at (see the module docstring's
    "single-context vs. ambiguous" section).
    """
    if build_evidence is None or not build_evidence.compile_units or not headers:
        return _EMPTY_RESOLUTION
    resolved_headers = _expand_header_directories([Path(h) for h in headers])
    matched = _matching_compile_units(build_evidence.compile_units, resolved_headers)
    if not matched:
        return _EMPTY_RESOLUTION

    pin = _ExplicitPin.of(explicit)
    by_signature: dict[_EffectiveContextSignature, list[CompileUnit]] = {}
    for cu in matched:
        by_signature.setdefault(_EffectiveContextSignature.of(cu, pin), []).append(cu)

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
