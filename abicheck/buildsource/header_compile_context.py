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
from .adapters.base import (
    _is_msvc_command,
    msvc_driver_token,
    split_operand_survivor,
)
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
    #: True when the caller's *explicit* context pins the compiler-selector
    #: dimension via EITHER ``gcc_path`` OR ``gcc_prefix`` (P2 review,
    #: ``discussion_r3788...`` follow-up, fresh evidence) -- the field name
    #: is kept as ``gcc_path`` (matching ``_EffectiveContextSignature.
    #: gcc_path``, the dimension it masks) even though it now also answers
    #: for ``gcc_prefix``. ``service_input_resolution._merge_l3_compile_
    #: context`` already treats ``gcc_path``/``gcc_prefix`` as one mutually
    #: exclusive compiler selector (its own "one logical compiler-selector,
    #: not two independent ones" comment) -- when a caller supplies only
    #: ``gcc_prefix`` and matched units name different clang-cl drivers, that
    #: merge step correctly discards every derived path in favor of the
    #: explicit prefix, so the ambiguity-signature masking above must treat
    #: the dimension as already resolved too. Checking only ``gcc_path is
    #: not None`` left a caller who pinned solely ``gcc_prefix`` with this
    #: still ``False``, so ``_EffectiveContextSignature.of`` compared the
    #: matched units' real, differing ``gcc_path`` values and raised
    #: ``HeaderCompileContextAmbiguousError`` before the merge step ever got
    #: a chance to apply the caller's already-resolved selection.
    gcc_path: bool = False
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
            elif tok in ("-D", "/D") and i + 1 < n:
                defines.add(tokens[i + 1].split("=", 1)[0])
                i += 1
            elif tok.startswith(("-D", "/D")) and len(tok) > 2:
                defines.add(tok[2:].split("=", 1)[0])
            elif tok in ("-U", "/U") and i + 1 < n:
                undefines.add(tokens[i + 1])
                i += 1
            elif tok.startswith(("-U", "/U")) and len(tok) > 2:
                undefines.add(tok[2:])
            i += 1
        return cls(
            standard=explicit_language_standard(
                explicit.gcc_options, explicit.gcc_option_tokens
            )
            is not None,
            target_triple=target_triple,
            sysroot=sysroot,
            gcc_path=explicit.gcc_path is not None or explicit.gcc_prefix is not None,
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
#: one. ``-std=`` (standard) is included too: a compile unit could in
#: principle carry both a structured ``standard`` and a raw ``-std=...``
#: survivor in ``abi_relevant_flags`` (the adapter's own extraction and its
#: structured-field derivation are independent passes over the same argv),
#: and the identical spelling-divergence risk applies. Unlike
#: :class:`_ExplicitPin`'s masking below (which only excludes a dimension the
#: *caller* explicitly pinned), this exclusion is unconditional -- it never
#: depends on whether an ``explicit`` context was even given, since the raw
#: flag is redundant with its structured field regardless. This holds for
#: ``-std=`` specifically because it is *always* captured into
#: ``CompileUnit.standard`` whenever present: ``build_context.py``'s
#: ``_consume_std_extra`` matches every ``-std=...`` token unconditionally
#: (``_STD_RE``) and assigns ``ctx.language_standard`` from it, with no path
#: that leaves a real ``-std=`` token unconsumed -- so masking it can never
#: hide a disagreement the structured field doesn't already carry.
#:
#: **MSVC's ``/std:`` does NOT get the same unconditional treatment (review
#: finding) -- it is masked only when ``CompileUnit.standard`` genuinely
#: agrees with that specific ``/std:`` token's own value.** Nothing in this
#: codebase parses ``/std:`` into ``CompileUnit.standard`` at all:
#: ``abicheck/buildsource/adapters/base.py``'s ``_add_generic_flag_option``
#: says so explicitly ("MSVC ``/std:`` is not parsed into cu.standard") and
#: only normalizes it into a *separate* ``BuildOption``, itself gated on
#: ``if not cu.standard``. So for a typical MSVC compile unit ``cu.standard``
#: is empty and the raw ``/std:c++17``/``/std:c++20`` token in
#: ``abi_relevant_flags`` is the *only* place the standard is recorded at
#: all -- masking it unconditionally would collapse two MSVC units that
#: genuinely disagree on their language standard into one signature and
#: silently apply the first unit's standard, instead of raising
#: ``HeaderCompileContextAmbiguousError``. A second, later-found case (P2
#: review, ``discussion_r3787584574``) means a merely-non-empty
#: ``cu.standard`` is *still* not sufficient: ``clang-cl`` accepts BOTH
#: ``-std=`` and ``/std:`` on one command line and honors the LATER,
#: MSVC-style ``/std:`` — but ``build_context.py``'s unconditional ``-std=``
#: capture has no notion of that precedence, so a unit like ``clang-cl
#: -std=c++17 /std:c++20`` gets ``cu.standard == "c++17"`` (from ``-std=``,
#: not from ``/std:``) while the real, honored standard is ``c++20``. See
#: :func:`_is_structured_field_flag`'s ``cu_standard`` parameter and
#: :func:`_msvc_std_flag_matches_captured_standard`, which compares each
#: ``/std:`` token's own value against ``cu_standard`` rather than trusting
#: mere presence. A third case (P2 review, ``discussion_r3787672845``) found
#: that even *agreeing* values do not make ``-std=``/``/std:`` interchangeable
#: on clang-cl: ``clang-cl`` ignores a bare ``-std=`` (warns "unknown argument
#: ignored") and relies on ``/std:`` alone to set the dialect, so a unit like
#: ``clang-cl -std=c++20 /std:c++20`` must still retain ``/std:`` in the
#: rendered command even though both spellings agree. See
#: :func:`_is_structured_field_flag`'s ``msvc`` parameter, which -- for a
#: compile unit detected as MSVC/clang-cl-dialect
#: (``adapters.base._is_msvc_command`` on ``cu.argv``) -- never masks
#: ``/std:`` at all, regardless of value agreement.
#: The bare, separate-operand switch spellings (whose own following argv
#: token is the operand, captured structurally instead — this same set is
#: reused verbatim by ``_context_flags``, via :func:`_is_structured_field_flag`,
#: to exclude the identical raw survivors from the final *rendered* command,
#: not only from this ambiguity-signature comparison). Matched by *exact*
#: token equality, not by prefix:
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
#: ``=``) — safe to match by prefix since each is already a fixed, literal
#: lead-in with no sibling flag sharing it: no real clang/gcc flag begins
#: with ``--target=``, ``--sysroot=``, or ``-std=`` other than the flag
#: itself (checked against a real ``clang --help``/``clang -cc1 --help`` for
#: the exact same reason as the exact-match set above — unlike the bare
#: ``-target``/``--sysroot`` switches, none of these combined forms collide
#: with an unrelated flag). Each of these is *always* fully redundant with
#: its structured field once present (see the module-level comment above for
#: ``-std=``'s own justification), so unconditional prefix-masking is safe
#: for all three. ``/std:`` is deliberately **not** in this tuple — see
#: ``_STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES`` below.
_STRUCTURED_FIELD_FLAG_PREFIXES = (
    "--target=",
    "--sysroot=",
    "-std=",
)

#: Combined-form spellings that are redundant with a structured field only
#: *conditionally*, per compile unit — currently just MSVC's ``/std:``,
#: which is never parsed into ``CompileUnit.standard`` (see the module-level
#: comment above ``_STRUCTURED_FIELD_EXACT_FLAGS`` for the full reasoning).
#: Masking a flag in this tuple requires the caller to separately confirm
#: the corresponding structured field is genuinely populated *from that
#: exact token* for that compile unit (``_is_structured_field_flag``'s
#: ``cu_standard`` — see its docstring for why a merely-non-empty
#: ``cu.standard`` is not sufficient).
_STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES = ("/std:",)


def _msvc_std_flag_matches_captured_standard(
    flag: str, prefix: str, cu_standard: str
) -> bool:
    """Does *flag* (an ``/std:<value>`` token starting with *prefix*) spell
    the *same* standard already captured in *cu_standard*?

    P2 review finding (``discussion_r3787584574``): ``clang-cl`` accepts
    BOTH GCC/Clang's ``-std=`` and MSVC's ``/std:`` on one command line, and
    per real ``clang-cl`` semantics the LATER, MSVC-style ``/std:`` wins --
    confirmed empirically (``clang-cl -std=c++17 /std:c++20`` compiles under
    C++20, ``-std=`` ignored). ``build_context.py``'s ``_consume_std_extra``/
    ``_STD_RE`` unconditionally captures any ``-std=...`` token into
    ``cu.standard`` with no notion of a later, overriding ``/std:`` on the
    same argv -- so ``bool(cu.standard)`` being true proves only that *some*
    token populated the field, not that ``/std:`` itself is what did it, or
    that the two agree.

    **Superseded for an MSVC/clang-cl compile unit (P2 review,
    ``discussion_r3787672845``, fresh evidence): this function's own
    "values agree, so the raw ``/std:`` survivor is redundant" reasoning
    does not hold there either.** ``clang-cl -std=c++20 /std:c++20`` has
    *agreeing* values, but that does not make the two spellings
    interchangeable -- confirmed empirically: ``clang-cl /?`` documents
    ``/std:<value>`` as "Set language version," while compiling with a bare
    ``-std=c++20`` and no ``/std:`` at all produces ``warning: unknown
    argument ignored`` and remains at clang-cl's *default* dialect, not
    C++20. So dropping ``/std:`` and keeping only the structurally-rendered
    ``-std=`` (this function's caller, :func:`_is_structured_field_flag`,
    always renders ``-std={cu.standard}`` from the structured field
    regardless of what happens to the raw ``/std:`` survivor) silently
    changes the dialect L2 actually replays under, even when the two
    tokens' values happen to match. This function is therefore only
    reached, via :func:`_is_structured_field_flag`, for a compile unit
    *not* detected as MSVC/clang-cl-style (see that function's own ``msvc``
    parameter) -- kept as a conservative fallback for the unusual case of a
    ``/std:``-shaped token surviving ``extract_abi_relevant_flags`` on an
    argv :func:`~abicheck.buildsource.adapters.base._is_msvc_command`
    didn't recognize as MSVC-dialect, rather than deleted outright, since a
    real MSVC/clang-cl unit never reaches this value-comparison path
    anymore.
    """
    if not cu_standard:
        return False
    token_value = flag[len(prefix) :]
    return token_value.strip().casefold() == cu_standard.strip().casefold()


def _is_structured_field_flag(flag: str, *, cu_standard: str, msvc: bool) -> bool:
    """Whether *flag* is fully represented by a structured
    ``target_triple``/``sysroot``/``standard`` field already, per the sets
    above.

    ``cu_standard`` must be ``cu.standard`` (the actual string, not merely
    its truthiness) for the compile unit *flag* came from -- it gates only
    the conditional ``/std:`` prefix
    (``_STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES``) when *msvc* is
    ``False``, via :func:`_msvc_std_flag_matches_captured_standard`: a
    ``/std:`` token is redundant with the structured ``standard`` field
    only when that field's *value* genuinely came from (or agrees with)
    this exact ``/std:`` token -- not merely whenever the field happens to
    be non-empty, since a co-present ``-std=`` on the same compile unit
    (``clang-cl``'s dual ``-std=``/``/std:`` support) can populate
    ``cu.standard`` from a *different* token entirely, one that a real
    ``clang-cl`` doesn't even honor once ``/std:`` is also present.

    ``msvc`` (P2 review, ``discussion_r3787672845``, fresh evidence): ``True``
    when the compile unit *flag* came from was detected as an MSVC/clang-cl
    dialect command (:func:`~abicheck.buildsource.adapters.base.
    _is_msvc_command` on ``cu.argv``). For such a unit, ``/std:`` is
    **never** masked, regardless of whether its own value agrees with
    ``cu_standard`` -- see :func:`_msvc_std_flag_matches_captured_standard`'s
    own updated docstring for why value-agreement does not make ``-std=``
    and ``/std:`` interchangeable on clang-cl: clang-cl ignores a bare
    ``-std=`` entirely and relies on ``/std:`` alone to set the dialect, so
    dropping ``/std:`` breaks the real compile even when the two spellings
    happen to agree. Only a compile unit *not* detected as MSVC-dialect
    still uses the value-comparison fallback above. The other prefixes/
    exact flags are unconditionally redundant and ignore both arguments.
    """
    if flag in _STRUCTURED_FIELD_EXACT_FLAGS or flag.startswith(
        _STRUCTURED_FIELD_FLAG_PREFIXES
    ):
        return True
    for prefix in _STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES:
        if flag.startswith(prefix):
            if msvc:
                return False
            return _msvc_std_flag_matches_captured_standard(flag, prefix, cu_standard)
    return False


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
    verbatim: see ``_STRUCTURED_FIELD_FLAG_PREFIXES`` and
    :func:`_mask_pinned_abi_flags` — a raw flag already fully represented by
    ``target_triple``/``sysroot``/``standard`` is excluded unconditionally,
    so two compile units that agree on the structured value but spell the
    flag that produced it differently (``--target=X`` vs. ``-target X``)
    compare equal here instead of falsely disagreeing, whether or not the
    caller pinned anything. A raw ``-D<macro>``/``/D<macro>`` survivor whose
    macro *is* pinned (``pin.defines``) is excluded too (Finding 1,
    ``discussion_r3787772663``) — genuinely conditional on ``pin``, unlike
    the structured-field exclusion above.

    This tuple compares ``cu.abi_relevant_flags`` entries verbatim as
    strings, never through :func:`~abicheck.buildsource.source_extractors.
    _argv.split_operand_survivor` — which is exactly why
    ``adapters.base.extract_abi_relevant_flags`` must itself encode two
    argv shapes that mean the same thing identically (P2 review,
    "Canonicalize equivalent cc1 survivor spellings", fresh evidence): a
    ``-target-abi``/``-target-cpu``/``-target-feature``/
    ``-target-linker-version`` survivor captured bare (direct ``-cc1``) vs.
    ``-Xclang``-wrapped (an ordinary driver invocation) used to encode as
    two visually different strings for the same value, which made this
    signature spuriously disagree between two otherwise-identical compile
    units — see that function's own docstring for the full history.
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
    gcc_path: str

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
                _mask_pinned_abi_flags(
                    cu.abi_relevant_flags,
                    pin,
                    cu_standard=cu.standard,
                    msvc=_is_msvc_command(cu.argv),
                )
            ),
            gcc_path="" if pin.gcc_path else (_derived_gcc_path(cu) or ""),
        )


def _mask_pinned_abi_flags(
    flags: Sequence[str], pin: _ExplicitPin, *, cu_standard: str, msvc: bool
) -> list[str]:
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
    of ``pin``).

    *pin* additionally gates one genuinely conditional exclusion (P2 review,
    ``discussion_r3787772663``): a raw ``-D<macro>[=value]``/``/D<macro>``
    survivor whose macro name the caller already pinned via *pin.defines*
    (Finding 1). Unlike the structured target/sysroot/standard fields above,
    a pinned *macro* has no dedicated structured field of its own to compare
    by instead -- ``.of()`` already drops the pinned key out of ``cu.defines``
    itself (the dict comprehension filters ``k not in pin.defines``), but
    this function used to leave the *raw* survivor untouched, so two units
    disagreeing only on a macro the caller explicitly pinned (e.g.
    ``-D_GLIBCXX_USE_CXX11_ABI=0`` vs. ``=1``, both pinned to ``=1`` via an
    explicit ``--gcc-options``) still raised
    ``HeaderCompileContextAmbiguousError`` despite the documented override.
    See :func:`_pinned_define_macro`.

    ``cu_standard`` (``cu.standard`` itself, not merely its truthiness, for
    the compile unit *flags* came from) is passed straight through to
    ``_is_structured_field_flag`` and is genuinely conditional, unlike
    *pin*: it gates only the MSVC ``/std:`` prefix, which — unlike
    ``-std=`` — is never parsed into ``CompileUnit.standard``, so it is
    redundant with the structured field only when that field's *value*
    genuinely agrees with this specific ``/std:`` token (see
    ``_STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES``'s own docstring and
    :func:`_msvc_std_flag_matches_captured_standard` — a co-present
    ``-std=`` on the same unit, e.g. ``clang-cl``'s dual support, can
    populate ``cu.standard`` from a *different*, non-``/std:`` token that a
    real ``clang-cl`` doesn't even honor once ``/std:`` is also present, so
    a merely-non-empty ``cu.standard`` is not enough to mask ``/std:``).

    ``msvc`` (P2 review, ``discussion_r3787672845``) is passed straight
    through to ``_is_structured_field_flag`` too: for a compile unit
    detected as MSVC/clang-cl-dialect, ``/std:`` is never masked here
    either, regardless of whether its value agrees with ``cu_standard`` —
    see that function's own updated docstring.
    """
    return [
        f
        for f in flags
        if not _is_structured_field_flag(f, cu_standard=cu_standard, msvc=msvc)
        and _pinned_define_macro(f) not in pin.defines
    ]


def _pinned_define_macro(flag: str) -> str | None:
    """The macro name *flag* defines, if it is a ``-D``/``/D`` token.

    ``None`` for every other flag shape -- including a bare ``-D``/``/D``
    with no macro name (``len(flag) <= 2``), which is not a real token
    :func:`~abicheck.buildsource.adapters.base.extract_abi_relevant_flags`
    ever emits on its own. Mirrors the identical ``-D``/``/D`` recognition
    :meth:`_ExplicitPin.of` already uses when scanning *explicit*'s own
    tokens for a pinned macro name, so a flag this function names is
    recognized as "the same macro" the caller pinned regardless of which of
    the two spellings (or which value) it carries.
    """
    if flag.startswith(("-D", "/D")) and len(flag) > 2:
        return flag[2:].split("=", 1)[0]
    return None


#: ``CompileUnit.standard``'s two language families, as returned by
#: :func:`_derived_standard_language_family`/:func:`_forced_language_family`.
_LANG_FAMILY_C = "c"
_LANG_FAMILY_CXX = "c++"


def _derived_standard_language_family(standard: str) -> str | None:
    """Which language family (C vs. C++) *standard* (a ``CompileUnit.
    standard`` value, e.g. ``"c17"``/``"c++20"``/``"gnu++17"``/``"gnu11"``)
    belongs to, or ``None`` for an empty/unrecognized value.

    A GNU-dialect C++ standard is spelled ``"gnu++NN"`` -- not
    ``"gnuc++NN"`` -- so a plain ``"c++"``-prefix check after stripping a
    ``"gnu"`` prefix would wrongly read it as C (Codex review, caught by
    this function's own unit test). Checking for the ``"++"`` marker
    anywhere in the value instead correctly covers both ``"c++20"`` and
    ``"gnu++17"`` while still reading ``"c17"``/``"gnu11"`` as C, mirroring
    how a real compiler treats ``-std=gnu++17`` as a C++ standard and
    ``-std=gnu11`` as a C standard.
    """
    if not standard:
        return None
    return _LANG_FAMILY_CXX if "++" in standard else _LANG_FAMILY_C


def _cu_language_family(language: str) -> str | None:
    """Which language family (C vs. C++) a ``CompileUnit.language`` value
    names, or ``None`` for an unrecognized/empty value.

    ``CompileUnit.language`` is populated by ``adapters.base.detect_language``/
    ``effective_language`` as one of ``"C"``/``"CXX"``/``"OBJC"``/
    ``"OBJCXX"``/``"CUDA"``/``""`` -- a normalized token independent of
    whether ``cu.standard`` happens to be populated at all (unlike
    :func:`_derived_standard_language_family`, which reads the *standard*
    string and returns ``None`` whenever it's empty, e.g. a compile unit
    with no explicit ``-std=``). Only ``"C"``/``"CXX"`` map to a recognized
    family here; every other value returns ``None``, the same
    "no family, no opinion" contract the standard-derived sibling uses for
    its own empty/unrecognized case.
    """
    if language == "C":
        return _LANG_FAMILY_C
    if language == "CXX":
        return _LANG_FAMILY_CXX
    return None


def _forced_language_family(lang: str | None, *, lang_explicit: bool) -> str | None:
    """Which language family the caller *explicitly* forced for this parse,
    if any (Codex review, ``discussion_r3787398644``).

    Mirrors ``dumper._resolve_force_cpp``'s own "an explicit ``--lang
    c++``/``cpp`` always wins" contract and the ``lang_explicit`` convention
    AGENTS.md's "Known gaps" section establishes (the ``--lang c++``/
    ``lang_explicit`` toolchain-identity precedent this module's own
    docstring already points to): only a genuinely explicit request pins a
    family here. ``lang_explicit=False`` (the default -- includes Click's
    own non-explicit ``"c++"`` default value) returns ``None``, a no-op,
    so an auto-detected/default-language parse is completely unaffected by
    this function and keeps forwarding every derived ``-std=`` exactly as
    before.
    """
    if not lang_explicit or not lang:
        return None
    return _LANG_FAMILY_CXX if lang.upper() in ("C++", "CPP") else _LANG_FAMILY_C


def _standard_conflicts_with_forced_language(
    standard: str, forced_language: str | None
) -> bool:
    """Whether *standard*'s own language family disagrees with *forced_language*.

    ``False`` whenever nothing was explicitly forced (``forced_language is
    None``) or *standard* has no recognizable family of its own (empty) --
    the pre-existing, unconditional behavior in both cases. Only a genuine,
    resolved disagreement (a C-family derived standard while C++ was
    explicitly forced, or vice versa) returns ``True``.
    """
    if forced_language is None:
        return False
    derived_family = _derived_standard_language_family(standard)
    return derived_family is not None and derived_family != forced_language


def _context_flags(cu: CompileUnit, *, forced_language: str | None = None) -> list[str]:
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

    The trailing ``cu.abi_relevant_flags`` pass-through excludes any flag
    :func:`_is_structured_field_flag` already recognizes as fully represented
    by the ``-std=``/``--target=``/``--sysroot=`` tokens this function just
    rendered a few lines above from the structured ``cu.standard``/
    ``cu.target_triple``/``cu.sysroot`` fields — the *same* predicate
    :func:`_mask_pinned_abi_flags` already uses to keep the ambiguity-
    signature *comparison* from seeing these as independent dimensions
    (``_EffectiveContextSignature.of``). Sharing one predicate between "does
    this raw flag disagree with another unit's" and "should this raw flag be
    rendered at all" matters beyond staying DRY: a real compiler applies
    last-flag-wins semantics to a repeated ``-target``/``--sysroot``/``-std``
    family switch, so appending the *raw*, unmodified survivor after the
    already-correct structured rendering doesn't just duplicate it, it
    silently overrides it. Concretely, a ``CompileDbAdapter``-sourced unit
    resolving its structured ``sysroot`` to an absolute path while a
    differently-spelled raw survivor (``-isysroot sdk``, still relative to
    the compile unit's own ``directory``, never abicheck's) remained in
    ``cu.abi_relevant_flags`` used to render
    ``--sysroot=<abs>`` ... ``-isysroot sdk`` in that order — the trailing,
    uncorrected relative flag then won on last-flag-wins semantics, so the
    header was parsed against a sysroot relative to abicheck's own current
    directory rather than the compile unit's. Excluding every structured-
    field-covered raw flag from the rendered tail closes that: only a flag
    genuinely independent of every structured field this function already
    renders (``-fPIC``, ``-fno-omit-frame-pointer``, ``-target-abi``, ...)
    survives into the final command.

    *forced_language* (``discussion_r3787398644``, Codex review): the
    language family (``"c"``/``"c++"``) the caller *explicitly* requested
    for this parse, or ``None`` (the default, a complete no-op) when
    nothing was explicitly forced. A matched compile unit's own
    ``cu.standard`` can genuinely disagree in family with an explicitly
    forced language -- e.g. the matched unit is plain C (``standard=
    "c17"``) while the caller passed ``DumpRequest(lang="c++",
    lang_explicit=True)`` to force a C++ parse of the same header(s). Since
    a real compiler rejects a C-family ``-std=`` in C++ mode outright
    (confirmed: Clang aborts with "invalid argument '-std=c17' not allowed
    with 'C++'"), forwarding the matched unit's derived standard verbatim
    in that case breaks a supported, explicit language override rather than
    merely being redundant with it. Per the finding's own suggested
    resolution -- "omit a derived standard whose language family conflicts
    with the explicitly selected mode" -- this only ever *drops* the
    conflicting derived ``-std=`` token; it never synthesizes a translated
    equivalent (no ``c17`` -> ``c++17`` guessing), matching this module's
    existing fail-closed-over-guessing discipline. Every other field this
    function renders (target triple, sysroot, defines/undefines, include
    paths, other ABI-relevant flags) is untouched by *forced_language* --
    only the one field whose family can actually conflict with a forced
    language is ever omitted.

    **Preserve an explicit ``--driver-mode=cl`` (P2 review, "Preserve
    explicit CL driver mode during replay", fresh evidence).** A compile
    unit can select MSVC/CL dialect via ``--driver-mode=cl`` on a
    *generically-named* driver (``clang --driver-mode=cl /std:c++20 /c
    t.cpp``) rather than via a CL-style binary name
    (``clang-cl``/``dpcpp-cl``) -- :func:`_derived_gcc_path` then has no
    ``clang-cl``-shaped token to record (:func:`~abicheck.buildsource.
    adapters.base.msvc_driver_token` falls back to the bare ``argv[0]``,
    ``"clang"``), and neither header command builder
    (``dumper_ast_config._build_castxml_command``/
    ``_build_clang_header_command``) infers CL mode from a plain ``clang``
    binary name the way it does from ``clang-cl``'s own self-selecting
    basename. Without ``--driver-mode=cl`` carried into the rendered
    tokens, the reconstructed command invokes GNU-mode clang with the
    retained ``/std:c++20`` survivor -- which GNU-mode clang treats as a
    missing input file, not a language flag (confirmed empirically: the
    original CL-mode command succeeds, the reconstructed GNU-mode one
    fails). Mirrors the identical, already-established precedent in L4
    replay's own command builder (``source_extractors.clang.
    _clang_context_args``: ``if msvc: cmd.append("--driver-mode=cl")``) --
    unconditional whenever the
    compile unit is MSVC/clang-cl-dialect
    (:func:`~abicheck.buildsource.adapters.base._is_msvc_command`),
    regardless of whether the resolved driver token's own basename already
    implies CL mode. Harmless when it does (a real ``clang-cl`` invocation
    already defaults to CL mode from its own basename; explicitly
    reasserting ``--driver-mode=cl`` on top is a no-op), and load-bearing
    exactly when it doesn't -- so this is emitted regardless of what
    :func:`_derived_gcc_path` resolves for *cu*, not conditioned on its
    result.
    """
    flags: list[str] = []
    msvc = _is_msvc_command(cu.argv)
    if msvc:
        flags.append("--driver-mode=cl")
    if cu.standard and not _standard_conflicts_with_forced_language(
        cu.standard, forced_language
    ):
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
    for f in cu.abi_relevant_flags:
        if _is_structured_field_flag(f, cu_standard=cu.standard, msvc=msvc):
            continue
        flags.extend(_split_operand_survivor(f))
    return flags


#: Re-export: the decode used to live here, L2-header-path-specific. It is
#: now shared with L4 source replay (``source_extractors._argv.
#: _carry_abi_relevant_flags``) and lives in ``adapters.base`` -- the module
#: that *produces* the internal encoding in the first place (P2 review,
#: "Decode normalized cc1 flags in every replay path", fresh evidence). Kept
#: as a private alias here rather than updating every call site in this
#: module (and this module's own test suite, which imports the private name
#: directly) to the new public location.
_split_operand_survivor = split_operand_survivor


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
    lang: str | None = None,
    lang_explicit: bool = False,
) -> HeaderCompileContextResolution:
    """Resolve a single L2 :class:`CompileContext` from L3 ``CompileUnit`` facts.

    Best-effort and additive: returns an empty (``context=None``) resolution
    whenever there is nothing to apply — no build evidence, no compile units,
    or no header the given ``CompileUnit``s reference — rather than raising,
    so a caller with no L3 evidence (or a header the build evidence simply
    doesn't cover) sees the exact same behavior as before this module existed.

    *lang*/*lang_explicit* (``discussion_r3787398644``, Codex review): the
    caller's own requested parse language, threaded the same additive,
    default-``None``/``False`` way ``DumpRequest.lang``/``lang_explicit`` are
    threaded through ``resolve_input``/``run_dump`` elsewhere in this codebase
    (see AGENTS.md's "Known gaps" ``--lang c++``/``lang_explicit`` entry) —
    a no-op for every existing caller that doesn't pass them. When
    *lang_explicit* is ``True`` and it disagrees in language *family* with the
    resolved compile unit's own ``standard`` (e.g. the matched unit is C
    (``standard="c17"``) while the caller explicitly forced ``lang="c++"``),
    the conflicting derived ``-std=`` token is omitted from the rendered
    context rather than forwarded verbatim — forwarding it would hand a
    C-family standard flag to a forced-C++ parse, which a real compiler
    rejects outright (see :func:`_context_flags`'s own docstring for the
    confirmed repro). Every other derived field is unaffected.

    **Forced language is applied *before* ambiguity grouping, not only to
    the single already-resolved unit's rendered flags (P2 review,
    ``discussion_r3787672845``, fresh evidence).** When the same header is
    referenced by otherwise-identical C and C++ compile units (e.g. neither
    carries an explicit ``-std=``, so ``cu.standard`` is empty on both and
    :func:`_standard_conflicts_with_forced_language`'s own std-conflict
    check above has nothing to compare), an explicit ``--lang c++`` still
    used to raise :class:`~abicheck.errors.HeaderCompileContextAmbiguousError`
    even though the caller already resolved the ambiguity by naming the
    language explicitly -- ``_EffectiveContextSignature`` groups on
    ``cu.language`` (``"C"`` vs. ``"CXX"``, populated independently of
    ``cu.standard``) before *forced_language* was ever computed, so the two
    units' genuinely different ``language`` fields alone triggered the
    ambiguity error before the caller's own explicit disambiguation could
    apply. Fixed by resolving *forced_language* first and narrowing the
    matched-unit set to units whose own language family
    (:func:`_cu_language_family`) agrees with it *before* signature
    grouping runs, whenever at least one matched unit actually has that
    family -- a unit of the "wrong" family for an explicitly forced parse
    is exactly the ambiguity-in-language case the caller's own explicit
    request already resolved, so excluding it here can only ever turn a
    would-be ambiguous case into a single-context one, never hide a
    genuine disagreement *within* the forced language (two C++ units that
    still disagree on, say, ``target_triple`` still group into two
    signatures and still raise). If *no* matched unit has the forced
    family (the explicit language names something the build evidence
    simply doesn't cover for this header), the full matched set is used
    unfiltered instead, exactly the pre-existing behavior -- narrowing to
    an empty set would silently discard real L3 evidence for a language
    mismatch this function has no way to resolve.

    *explicit* is the caller's own, already-supplied L2 context (``evidence.
    compile`` on the service_input_resolution path) — when given, any
    ABI-relevant dimension it already pins (an explicit ``-std=``/
    ``--target=``/``--sysroot=``/``-isysroot`` or a specific ``-D``/``/D``/
    ``-U``/``/U`` macro; see :class:`_ExplicitPin`) is excluded from the
    multi-unit ambiguity comparison below, since the caller's own value wins
    that dimension regardless of what the matched compile units say
    (Finding 3). Per-field, not per-request: a genuine disagreement on any
    *other*, unpinned dimension still fails closed.

    ``/D``/``/U`` (P2 review, ``discussion_r3788...`` follow-up, fresh
    evidence): :class:`_ExplicitPin`'s own macro-pin scan used to recognize
    only GCC/Clang's ``-D``/``-U`` spelling, even though the raw-flag masking
    it feeds (:func:`_mask_pinned_abi_flags`, via :func:`_pinned_define_macro`)
    already recognized MSVC/clang-cl's ``/D``/``/U`` spelling too -- so a
    clang-cl caller pinning an ABI macro via ``/D_GLIBCXX_USE_CXX11_ABI=1``
    left ``pin.defines`` empty, and two matched units disagreeing only on
    that macro's value stayed spuriously ambiguous despite the documented
    override. ``clang-cl /?`` documents ``/D <macro[=value]>`` as the
    supported define form, mirrored here the same way :class:`_ExplicitPin`
    itself already scans ``-D``/``-U`` for the GCC/Clang spelling.

    ``gcc_path`` (P2 review, ``discussion_r3788...`` follow-up, fresh
    evidence) folds in :func:`_derived_gcc_path`'s own resolved driver
    selector: the compiler itself supplies ABI-relevant built-in macros,
    default include paths, and target defaults, so two otherwise-identical
    grouped units that resolve to *different* clang-cl/MSVC drivers must not
    silently collapse into one signature and pick the first unit's driver --
    that would let the compile-database's own iteration order (unrelated to
    any ABI-relevant fact) change the generated L2 snapshot. Masked to a
    shared placeholder exactly like ``standard``/``target_triple``/
    ``sysroot`` above when the caller's own *explicit* context already pins
    a ``--gcc-path`` OR a ``--gcc-prefix`` (``pin.gcc_path``, which answers
    for either explicit selector field -- see that field's own docstring):
    the caller's own value wins that dimension regardless of what the
    matched units resolve to, so a driver-only disagreement the caller
    already resolved -- via either spelling -- must not raise
    ``HeaderCompileContextAmbiguousError``.

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

    # Resolve the forced language *before* signature grouping (P2 review,
    # discussion_r3787672845): narrow to units whose own language family
    # agrees with an explicitly forced one, whenever at least one such unit
    # exists, so the caller's own explicit disambiguation is applied before
    # -- not after -- the ambiguity check runs. See this function's own
    # docstring for the full reasoning and the fail-open-to-unfiltered
    # fallback when no matched unit has the forced family.
    forced_language = _forced_language_family(lang, lang_explicit=lang_explicit)
    if forced_language is not None:
        language_matched = [
            cu for cu in matched if _cu_language_family(cu.language) == forced_language
        ]
        if language_matched:
            matched = language_matched

    pin = _ExplicitPin.of(explicit)
    by_signature: dict[_EffectiveContextSignature, list[CompileUnit]] = {}
    for cu in matched:
        by_signature.setdefault(_EffectiveContextSignature.of(cu, pin), []).append(cu)

    if len(by_signature) > 1:
        raise HeaderCompileContextAmbiguousError(
            _ambiguity_message(headers, by_signature)
        )

    ((_sig, units),) = by_signature.items()
    sample = units[0]
    flags = _context_flags(sample, forced_language=forced_language)
    context = CompileContext(
        gcc_option_tokens=tuple(flags),
        gcc_path=_derived_gcc_path(sample),
    )
    return HeaderCompileContextResolution(
        context=context, matched_unit_count=len(matched)
    )


def _derived_gcc_path(cu: CompileUnit) -> str | None:
    """The compiler binary to replay *cu*'s derived flags through, if any
    (P1 review, ``discussion_r3787772668``, Finding 3).

    A resolved :class:`CompileContext` previously carried only option
    tokens, never which compiler understands them -- for an MSVC/clang-cl
    compile unit this silently broke the very ``/std:`` survivor
    ``_context_flags``/``_STRUCTURED_FIELD_CONDITIONAL_FLAG_PREFIXES`` are
    already careful to retain (see that set's own docstring): with no
    ``gcc_path`` selected alongside it, the direct-clang L2 backend defaults
    to plain ``clang++`` (``dumper_clang._resolve_clang_bin``'s own
    fallback), and a real ``clang++`` reads ``/std:c++20`` as a missing
    source file, not a language flag (confirmed empirically) -- turning an
    otherwise-working header parse into a hard failure for evidence that
    was, until this point, resolved and applied correctly.

    Returns the driver token this compile unit was itself invoked with --
    only when *cu* is genuinely MSVC/clang-cl-dialect
    (:func:`~abicheck.buildsource.adapters.base._is_msvc_command`). ``None``
    otherwise -- a complete no-op for every non-MSVC unit, unchanged from
    this module's pre-Finding-3 behavior.

    **Not unconditionally ``cu.argv[0]`` (P2 review,
    ``discussion_r3788073756``, fresh evidence): a compiler-cache/launcher
    wrapper commonly precedes the real driver.** For ``sccache clang-cl
    /std:c++20 ...``, ``_is_msvc_command`` correctly recognizes ``clang-cl``
    later in the leading tokens, but ``argv[0]`` is ``sccache`` -- not a
    clang-family binary, so ``dumper_clang._resolve_clang_bin`` rejects it
    and falls back to plain ``clang++``, which cannot parse the retained
    ``/std:`` survivor at all (the exact failure this whole function exists
    to prevent). Uses :func:`~abicheck.buildsource.adapters.base.
    msvc_driver_token` -- the same scan ``_is_msvc_command`` runs, refactored
    to also report which token it matched -- to locate the actual
    ``cl``/``clang-cl``-basename token wherever it sits in argv, falling back
    to ``cu.argv[0]`` (the pre-fix behavior) only for the narrower case where
    MSVC-dialect was detected some other way (a bare ``/c`` marker or an
    explicit ``--driver-mode=cl`` naming no such token) and no more specific
    token exists to prefer.

    Deliberately unconditional on any caller-supplied ``explicit`` context
    -- mirrors ``_context_flags`` itself, which always renders *cu*'s own
    fields regardless of *pin*/*explicit* and leaves "the caller's own
    explicit value wins" to the merge step. ``resolve_header_compile_
    context`` returns only the *derived* half of the context (``derive_l2_
    compile_context``'s own docstring); its one caller,
    ``service_input_resolution._seeded_compile_context``, folds it against
    the caller's own explicit context via ``_merge_l3_compile_context``,
    which now performs the identical "derived leads, explicit wins"
    arbitration for ``gcc_path``/``gcc_prefix`` that it already performs for
    ``sysroot``/``gcc_options`` -- so an explicit ``--gcc-path`` a caller
    already set is never overridden, without this function needing to know
    about *explicit* at all.

    Deliberately does **not** attempt to validate that ``cu.argv[0]`` is
    itself a clang-family binary (``clang-cl`` vs. a real, literal
    ``cl.exe``) -- ``dumper_clang._resolve_clang_bin``/
    ``resolve_source_frontend_clang_bin`` already gate a ``gcc_path``
    override on exactly that (``_is_clang_family_binary``), falling back to
    their own existing default otherwise. Handing them a real ``cl.exe``
    path here is therefore safe (ignored, same as today) rather than
    harmful; only a genuinely clang-family unit (``clang-cl``, Intel's
    ``dpcpp-cl``, ...) is actually honored downstream, which is exactly the
    case this fix closes. A literal ``cl.exe`` compile unit stays an
    inherent, pre-existing limitation of the clang-only L2 header backends
    (neither can shell out to real MSVC to produce a Clang AST) --
    unrelated to, and not widened by, this fix.

    Also does not force ``frontend``/``--ast-frontend`` to ``"clang"``: this
    module's contract is "resolve a CompileContext", not "choose which L2
    backend runs it" -- ``"auto"`` never falls back from castxml on its own
    (``dumper._resolve_header_backend``), so this value only takes effect
    once a caller (explicitly, or via ``ABICHECK_AST_FRONTEND``) already
    selected the clang backend -- exactly the scenario this finding's own
    repro describes (a caller already on the direct-clang path, whose
    resolved compiler was the ONLY missing piece). Forcing the frontend too
    would mean auditing frontend precedence across ``cli.py``/
    ``service_compare_evidence.py``/``cli_resolve.py``/``api_types.py`` this
    PR's prior nine rounds never touched, *and* separately fixing
    ``dumper_ast_config._resolve_compiler_binary``'s own castxml dialect
    detection (which recognizes literal ``cl``/``cl.exe`` but not
    ``clang-cl`` as MSVC-dialect, so an ``"auto"`` resolution landing on
    castxml for this same evidence would still mis-emulate it as a GNU
    compiler) -- out of scope for this pass; see AGENTS.md's "Known gaps"
    entry on this finding for the full reasoning and what a complete fix
    needs.
    """
    if not cu.argv or not _is_msvc_command(cu.argv):
        return None
    driver = msvc_driver_token(cu.argv)
    token = driver if driver is not None else cu.argv[0]
    return _resolve_driver_token(token, cu.directory)


def _resolve_driver_token(token: str, directory: str) -> str:
    """Expand ``~`` and resolve a path-bearing driver *token* against
    *directory* (P2 review, ``discussion_r3788...`` follow-up, fresh
    evidence).

    Mirrors :func:`_resolve_cu_relative_path`'s treatment of every other
    redacted/relative ``CompileUnit`` path field: a compile command naming
    its compiler with a relative path (``../llvm/bin/clang-cl``) or a
    home-redacted one (``~/llvm/bin/clang-cl``, ADR-032 D7) is only
    meaningful relative to the compile unit's own ``directory`` -- not
    abicheck's own current working directory, which is what a bare
    ``shutil.which(token)``/subprocess call in
    ``dumper_clang._resolve_clang_bin`` would otherwise use. Without this,
    such a token was returned verbatim, so a genuinely executable compiler
    (from the real build's own working directory) was reported missing.

    A **bare** PATH name (``clang-cl``, no path separator at all) is left
    unchanged -- resolving it against *directory* would be wrong, since a
    bare name is looked up on ``PATH``, not relative to any directory.

    **Normalized before returning (P2 review, "Normalize resolved driver
    paths before grouping", fresh evidence).** Two matched units in
    different build subdirectories can spell the *same* executable through
    a relative path containing ``..`` -- e.g.
    ``/project/build/a/../../tool/clang-cl`` and
    ``/project/build/b/../../tool/clang-cl`` both name
    ``/project/tool/clang-cl`` once ``..`` segments are collapsed, but
    joining each token onto its own unit ``directory`` alone (the fix this
    function already applied for a prior finding) leaves the two joined
    strings textually different -- resolving *only* the base directory does
    not, by itself, provide a canonical comparison key. Since
    :meth:`_EffectiveContextSignature.of` compares ``gcc_path`` by plain
    string equality, the two textually-different-but-equivalent paths
    grouped into two distinct signatures, raising a spurious
    ``HeaderCompileContextAmbiguousError`` for units that in fact agree on
    every ABI-relevant dimension. ``os.path.normpath`` (not ``Path.resolve()``)
    matches this module's own existing precedent: every other path
    normalization here (:func:`_resolve_cu_relative_path`,
    :func:`_context_flags`'s ``-I``/``-isystem``/``--sysroot`` rendering)
    is a lexical, symlink-blind join -- ``CompileUnit`` path fields are
    already-redacted/relative labels for *display* and *replay-command*
    purposes (a home-rooted path redacted to ``~/...``, ADR-032 D7), not
    guaranteed to exist on abicheck's own filesystem at all (a persisted
    build pack collected on a different machine), so resolving symlinks
    against a path that may not even be present locally would raise or
    silently produce nonsense -- a purely lexical normalization is safe
    either way and is exactly what this signature comparison needs (collapse
    equivalent *spellings*, not resolve real symlink targets).
    """
    import os as _os

    expanded = _os.path.expanduser(token)
    if "/" not in expanded and "\\" not in expanded:
        return expanded
    path = Path(expanded)
    if not path.is_absolute() and directory:
        path = Path(directory).expanduser() / path
    return _os.path.normpath(str(path))


def _ambiguity_message(
    headers: Sequence[Path],
    by_signature: dict[_EffectiveContextSignature, list[CompileUnit]],
) -> str:
    header_names = ", ".join(sorted({h.name for h in headers}))
    lines = [
        f"Public header(s) [{header_names}] are compiled under "
        f"{len(by_signature)} materially different, ABI-relevant compile "
        "contexts across the available L3 build evidence (differing "
        "-std=/target/defines/include-search-order/sysroot/compiler-driver/"
        "ABI-relevant flags); abicheck cannot pick one context over another "
        "without guessing. Narrow the input (--compile-db-filter / a "
        "project compile: block / --gcc-options/--gcc-path pinning the "
        "ambiguous field(s)) or compare a header per contract at a time. "
        "Conflicting translation units:",
    ]
    for sig, units in sorted(by_signature.items(), key=lambda kv: kv[0].standard):
        sample = units[0]
        lines.append(
            f"  - {sample.source or sample.id!r}: std={sig.standard or '(default)'} "
            f"target={sig.target_triple or '(default)'} "
            f"gcc_path={sig.gcc_path or '(default)'} "
            f"abi_flags={list(sig.abi_relevant_flags)}"
        )
    return "\n".join(lines)
