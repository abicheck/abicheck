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

"""Collection layer for build-context reconciliation (ADR-039).

The reconciler in :mod:`abicheck.diff_reconcile` clears a context-free
header-parse false positive using two pieces of build evidence on the snapshot:
``build_context_defines`` (the build's active ``-D`` set) and
``conditional_fields`` (the ``{type: {field: {guard, type, …}}}`` registry of
``#if defined(GUARD)``-guarded record fields). This module *produces* both:

* :func:`defines_from_flags` / :func:`defines_from_compile_db` harvest the active
  macro set from compile flags / a ``compile_commands.json``;
* :func:`scan_conditional_fields` scans public-header **source** for record
  fields wrapped in a *single positive* ``#ifdef GUARD`` / ``#if defined(GUARD)``
  region, recording each field's guard and declaration — even the ones a
  context-free castxml parse pruned.

It is a **best-effort, conservative** scanner, not a C preprocessor: it records a
field only when the pattern is unambiguous (a single positive guard, a simple
member declaration directly inside a ``struct``/``class``/``union`` body).
A simple negative ``#ifndef GUARD`` field *is* recorded (with ``negative: True``)
so the reconciler can drop it from a build that defines GUARD — the context-free
parse observes it, but the real build prunes it. Compound (``&&`` / ``||`` /
``!`` / ``#if <expr>``) and nested guards are deliberately *not* recorded — a
missed field just means no reconciliation (safe), while the reconciler's own
declaration check (ADR-039) guards against a mis-scan clearing a real change. A
guard whose macro the header itself ``#undef``s (before
the guarded region, without a later ``#define``) is likewise skipped: the build
really evaluates it inactive, so the field is genuinely pruned. A classic
file-level ``#ifndef H`` / ``#define H`` **include guard** is treated as a
transparent wrapper (it is always taken on first include), so guarded fields in
the near-universal include-guarded header are still recorded. Because the scan is
per-file and does **not** follow ``#include``s, a guarded field appearing after
any ``#include`` is marked ``ambiguous`` — an included file could ``#undef`` the
guard the real build sees, which this text scan cannot know (Codex review #498);
the reconciler then keeps that field's type rather than risk a wrong clear. Only
self-contained headers (no preceding include) stay reconcilable. A **forced
include** (``-include`` / ``-imacros`` on the command line or in a compile-DB
entry) is preprocessed before the header and is treated the same way — all
scanned fields become ``ambiguous`` (see :func:`collect_build_context`). A
leading ``#pragma once`` before a classic ``#ifndef``/``#define`` wrapper is
neutral and does not defeat include-guard recognition. Pure-stdlib and
side-effect-free.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from ._compiler_options import split_gcc_options

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .model import AbiSnapshot

# ── active-define extraction ─────────────────────────────────────────────────


def _macro_name(body: str) -> str | None:
    name = body.split("=", 1)[0].strip()
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else None


def _iter_define_tokens(flags: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(op, raw_body)`` for every ``-D``/``-U`` flag in *flags*, in
    order — the shared low-level walker behind both :func:`defines_from_flags`
    (nets these down to an active *set*, dropping order and value, and
    additionally filters to valid bare-identifier macro names) and
    :func:`ordered_macro_ops` (ADR-050 profile field — keeps every token
    unfiltered, including function-like macros like ``-DAPI(x)=x``: a
    non-identifier body is real macro content that can change the parsed
    AST and must still distinguish two extraction profiles, even though it
    is not a name `defines_from_flags`'s reconciler consumer can act on —
    Codex review, PR #624). ``op`` is ``"D"`` or ``"U"``; *raw_body* is the
    un-stripped operand text (e.g. ``"FOO=1"`` for ``-DFOO=1``).
    """
    tokens = list(flags)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        body: str | None = None
        op = "D"
        if tok in ("-D", "-U") and i + 1 < len(tokens):
            body, op = tokens[i + 1], tok[1]
            i += 1
        elif tok.startswith("-D") and len(tok) > 2:
            body, op = tok[2:], "D"
        elif tok.startswith("-U") and len(tok) > 2:
            body, op = tok[2:], "U"
        if body is not None:
            yield op, body
        i += 1


def defines_from_flags(
    flags: Iterable[str], initial: set[str] | None = None
) -> set[str]:
    """The **net active** macro set for one ordered flag list.

    Processes ``-D`` and ``-U`` in order (later wins): ``-DNAME`` / ``-DNAME=val``
    / ``-D NAME`` add a macro, ``-UNAME`` / ``-U NAME`` remove it. Honouring
    ``-U`` and flag order matters — ``-DKEEP -UKEEP`` yields an *inactive* KEEP
    (Codex review #498). Values are dropped; reconciliation keys on presence.

    *initial* seeds the active set with macros already in effect (e.g. the
    compile-DB intersection); the flags are applied **on top**, so a user
    ``-UKEEP`` overrides a database ``-DKEEP`` — the real parse applies the
    extra flags after the compile-DB options (Codex review #498). The passed set
    is not mutated.
    """
    active: set[str] = set(initial) if initial else set()
    for op, body in _iter_define_tokens(flags):
        name = _macro_name(body)
        if name is None:  # function-like/malformed body -- not a plain macro name
            continue
        if op == "D":
            active.add(name)
        else:
            active.discard(name)
    return active


def ordered_macro_ops(flags: Iterable[str]) -> list[tuple[str, str]]:
    """Ordered ``(op, name_or_nameval)`` pairs for ADR-050's ``macro_ops``
    profile field (``abicheck.comparability.compute_extraction_contract``) —
    the same ``-D``/``-U`` tokens :func:`defines_from_flags` nets down to a
    *set*, but preserving both ORDER and VALUE: ``macro_ops=[("D","FOO=1")]``
    must fingerprint differently from ``[("D","FOO=2")]``, and
    ``-DKEEP -UKEEP`` must fingerprint differently from a lone ``-DKEEP``
    even though both net to the same *active* set.
    """
    return list(_iter_define_tokens(flags))


def pass_through_flags_from_tokens(flags: Iterable[str]) -> list[str | Path]:
    """Ordered ADR-050 ``pass_through_flags``
    (``abicheck.comparability.compute_extraction_contract``) from a compiler
    flag stream. Recognizes only ``-include <path>`` today (its
    ``--compiler-option=-include --compiler-option=<path>`` CLI form already flattens
    to this same two-token shape) — the one currently-known must-handle
    repeatable, order-sensitive frontend flag (Codex review, PR #624); other
    flags are not yet classified and are simply omitted, not mis-hashed. The
    path operand becomes a ``Path`` so ``compute_extraction_contract``
    content-hashes it instead of hashing a checkout-root-dependent literal
    string.
    """
    tokens = list(flags)
    result: list[str | Path] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "-include" and i + 1 < len(tokens):
            result.append(tokens[i])
            result.append(Path(tokens[i + 1]))
            i += 2
            continue
        i += 1
    return result


def resolve_pass_through_paths(
    items: Iterable[str | Path], search_dirs: Iterable[Path] = ()
) -> list[str | Path]:
    """Best-effort real-filesystem resolution of each ``Path``-valued
    element of :func:`pass_through_flags_from_tokens`'s output, run by the
    caller (``dumper.dump()``) that actually knows the ``-I`` search
    directories used for this extraction (Codex review, PR #624 follow-up).

    A bare ``Path(operand)`` for a forced-include flag (e.g.
    ``-include config.h``) can name a file that only exists relative to the
    frontend's own include search path, not abicheck's CWD — left
    unresolved, ``compute_extraction_contract`` would either raise on a
    perfectly valid dump (no such CWD file) or silently content-hash an
    unrelated CWD file that happens to share the name. *search_dirs* (the
    ``-I`` directories, in order) plus CWD are tried as candidate resolution
    roots, mirroring basic ``-I`` precedence. When the operand cannot be
    resolved anywhere known, the raw flag text is kept as a plain ``str``
    instead — opaque, hashed as literal text, the same treatment
    ``compute_extraction_contract`` already gives any unclassified flag —
    rather than risk either failure mode above. System default include
    directories, sysroot, and any ``-I``/``-isystem`` passed as raw text
    inside ``gcc_options``/``gcc_option_tokens`` (as opposed to the
    dedicated ``extra_includes`` param) are *not* modeled: those cases
    degrade safely to the same opaque ``str`` fallback rather than crash,
    but full resolution needs real compiler search-path evidence (the
    depfile mechanism, ADR-050 D1's still-deferred
    ``depfile_resolved_paths``/``generated_driver_path``).
    """
    roots = [*search_dirs, Path.cwd()]
    resolved: list[str | Path] = []
    for item in items:
        if not isinstance(item, Path):
            resolved.append(item)
            continue
        if item.is_absolute():
            resolved.append(item if item.is_file() else str(item))
            continue
        found = next((root / item for root in roots if (root / item).is_file()), None)
        resolved.append(found if found is not None else str(item))
    return resolved


def _split_command(command: object) -> list[str]:
    """``shlex.split`` a compile ``command`` string, tolerating malformed input.

    A raw command with an unbalanced quote raises ``ValueError``; the collector
    must never abort a dump over one bad entry, so return ``[]`` in that case."""
    if not isinstance(command, str):
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _compile_entry_matches(entry: dict[str, object], pattern: str) -> bool:
    """Whether a raw compile-DB *entry*'s ``file`` matches the filter *pattern*.

    Mirrors ``build_context`` end-to-end: a **relative** ``file`` is first resolved
    against the entry's ``directory`` (``build_context.CompileEntry`` stores
    ``directory / file``), then the pattern is tested against the absolute path,
    the directory-relative path, and the CWD-relative path — so an absolute filter
    matches a relative-``file`` entry and a relative ``src/libfoo/**`` filter
    matches an absolute-``file`` entry (Codex review #498). Without resolving the
    relative ``file`` first, an absolute filter would always miss, the collector
    would fall back to all entries, and the guard macro defined only by the
    selected TU would be intersected away.

    Routed through ``build_context.source_matches_filter`` rather than keeping
    its own copy of those rules: three layers now narrow a compile database by
    this same filter, and a filter that selects different translation units in
    one layer than another is exactly the disagreement this predicate exists to
    prevent (see that function's own docstring). Only the raw-dict field
    extraction is this function's business."""
    from .build_context import source_matches_filter

    file = entry.get("file")
    if not isinstance(file, str):
        return False
    directory = entry.get("directory")
    return source_matches_filter(
        file, directory if isinstance(directory, str) else None, pattern
    )


def defines_from_compile_db(
    path: str | Path, source_filter: str | None = None
) -> set[str]:
    """Macros **reliably active** across a ``compile_commands.json``.

    Each entry's net active set is computed with :func:`defines_from_flags`
    (reading ``arguments`` or a shlex-split ``command``); the result is their
    **intersection** — a macro is trusted only when *every* compile command
    defines it. Unioning would let an unrelated translation unit's ``-DKEEP``
    mark ``KEEP`` active for a header it never configures (Codex review #498);
    the intersection is conservative, so an ambiguous macro is simply not trusted
    and its guarded fields are never reconciled.

    *source_filter* (from ``--compile-db-filter``) narrows the entries to those
    whose source file matches, **before** intersecting — so the collector
    harvests the same build context the filtered header parse used, and a guard
    defined only by the selected TU is not dropped (Codex review #498). An empty
    match falls back to all entries (matching ``build_context``).

    A **build directory** is normalised to ``<dir>/compile_commands.json``
    (matching ``build_context.load_compile_db``), so the documented ``dump …
    -p build/`` form works. Empty on any read/parse error or when no command
    carries a define — the collector never aborts a dump.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "compile_commands.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    entries = [e for e in data if isinstance(e, dict)]
    if source_filter:
        matched = [e for e in entries if _compile_entry_matches(e, source_filter)]
        if matched:  # empty match → keep all (conservative, like build_context)
            entries = matched
    per_command: list[set[str]] = []
    for entry in entries:
        args = entry.get("arguments")
        if not isinstance(args, list):
            args = _split_command(entry.get("command"))
        per_command.append(defines_from_flags(str(a) for a in args))
    if not per_command:
        return set()
    return set.intersection(*per_command)


# ── conditional-field source scan ────────────────────────────────────────────

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"//[^\n]*")
_RECORD_OPEN = re.compile(r"\b(struct|class|union)\s+([A-Za-z_]\w*)\b")
# A ``namespace`` opener: a named (possibly ``a::b``) or anonymous namespace. Must
# be anchored so ``using namespace std;`` is not mistaken for a scope opener.
_NAMESPACE_OPEN = re.compile(r"^namespace\b\s*([A-Za-z_][\w:]*)?")
_ACCESS_LABEL = re.compile(r"^(public|private|protected)\s*:\s*$")
_IFDEF = re.compile(r"^#\s*ifdef\s+([A-Za-z_]\w*)\s*$")
_IF_DEFINED = re.compile(
    r"^#\s*if\s+defined\s*(?:\(\s*([A-Za-z_]\w*)\s*\)|\s+([A-Za-z_]\w*))\s*$"
)
_PP_OPEN_OTHER = re.compile(r"^#\s*if(n?def|)\b")
_ELSE_ELIF = re.compile(r"^#\s*(else|elif)\b")
_ENDIF = re.compile(r"^#\s*endif\b")
_IFNDEF = re.compile(r"^#\s*ifndef\s+([A-Za-z_]\w*)\s*$")
_UNDEF = re.compile(r"^#\s*undef\s+([A-Za-z_]\w*)")
_DEFINE = re.compile(r"^#\s*define\s+([A-Za-z_]\w*)")
_INCLUDE = re.compile(r"^#\s*include\b")
_PRAGMA_ONCE = re.compile(r"^#\s*pragma\s+once\b")
_FIELD = re.compile(r"^(?P<decl>[A-Za-z_][\w:<>,\s\*&]*?[\w\*&])\s*;\s*$")

#: Sentinel guard-stack entry for a file-level ``#ifndef H`` / ``#define H``
#: include guard — a *transparent* wrapper that neither records nor blocks a
#: field (unlike an opaque ``None`` region). See :func:`_include_guard_macro`.
_INCLUDE_GUARD = object()


class _NegGuard:
    """Guard-stack entry for a simple ``#ifndef MACRO`` (negative) region.

    A field under a single such guard *is* observed by the context-free parse
    (the macro is undefined ⇒ ``#ifndef`` true), but the real build prunes it
    when the macro is defined — so it is recorded with ``negative: True`` and the
    reconciler drops it from a side whose defines contain the macro."""

    __slots__ = ("macro",)

    def __init__(self, macro: str) -> None:
        self.macro = macro


def _strip_comments(source: str) -> str:
    return _COMMENT_LINE.sub("", _COMMENT_BLOCK.sub("", source))


def _include_guard_macro(lines: list[str]) -> str | None:
    """The macro of a classic file include guard, or ``None``.

    Recognises the ubiquitous ``#ifndef H`` immediately followed by ``#define H``
    as the first two directives, with no code in between. Such a guard is always
    taken on first include (``H`` undefined ⇒ true), so it must be treated as a
    *transparent* wrapper rather than an opaque ``#ifndef`` region — otherwise the
    scanner would refuse to record every guarded field in an include-guarded
    header, which is nearly all of them (Codex review #498)."""
    first: str | None = None
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if not s.startswith("#"):
            return None  # code before the guard → not a whole-file include guard
        if first is None:
            if _PRAGMA_ONCE.match(s):
                continue  # a leading ``#pragma once`` is neutral — skip and keep probing
            m = _IFNDEF.match(s)
            if not m:
                return None
            first = m.group(1)
            continue
        dm = _DEFINE.match(s)
        return first if (dm and dm.group(1) == first) else None
    return None


def _parse_field(
    decl: str,
) -> tuple[str, str, bool, int | None, bool, bool, bool] | None:
    """Parse a member declaration into ``(name, type, is_bitfield, bits, const, volatile, mutable)``.

    Returns ``None`` for anything that is not a plain single-name member — a
    function/method (has ``(``), a nested aggregate, an assignment, etc. The
    ``const``/``volatile``/``mutable`` qualifiers are lifted out of the type
    string into structured bits so the registry's declaration matches the
    model's :class:`~abicheck.model.TypeField`, which stores them separately
    from ``type`` (Codex review #498, P2)."""
    if "(" in decl or "=" in decl or "{" in decl:
        return None
    bits: int | None = None
    is_bitfield = False
    # Bit-field: a trailing ``: N`` (avoid access labels / ``::``).
    core = decl
    bm = re.search(r":\s*(\d+)\s*$", decl)
    if bm and "::" not in decl[: bm.start()]:
        bits = int(bm.group(1))
        is_bitfield = True
        core = decl[: bm.start()].strip()
    m = re.match(r"^(?P<type>.+?)[\s\*&]+(?P<name>[A-Za-z_]\w*)$", core)
    if not m:
        return None
    name = m.group("name")
    # Re-attach pointer/reference sigils to the type by taking everything before
    # the name in the original core string.
    type_str = core[: core.rfind(name)].strip()
    type_str = " ".join(type_str.split())
    # Lift leading cv/mutable qualifiers into structured bits, matching the model.
    tokens = type_str.split()
    is_const = "const" in tokens
    is_volatile = "volatile" in tokens
    is_mutable = "mutable" in tokens
    type_str = " ".join(t for t in tokens if t not in ("const", "volatile", "mutable"))
    if not type_str or name in (
        "struct",
        "class",
        "union",
        "public",
        "private",
        "protected",
        "return",
    ):
        return None
    return name, type_str, is_bitfield, bits, is_const, is_volatile, is_mutable


def _record_qualified_name(scope_stack: list[_Scope], name: str) -> str | None:
    """The ``ns::Outer::Name`` qualified name for a record opening in *scope_stack*.

    ``None`` when any enclosing scope is an **anonymous** namespace (its records
    have internal linkage / no stable public name, so they are never recorded).
    Namespaces *and* enclosing records both contribute a qualifier segment, so
    the key matches the qualified spelling a DWARF snapshot uses for
    ``RecordType.name`` (``dwarf_metadata`` walks the same scope prefix)."""
    parts: list[str] = []
    for sc in scope_stack:
        if sc.name is None:
            return None  # anonymous namespace → unrecordable
        parts.append(sc.name)
    parts.append(name)
    return "::".join(parts)


class _Scope:
    """One open ``{}`` scope: a namespace or a record body."""

    __slots__ = (
        "kind",
        "name",
        "depth",
        "access",
        "qualified",
        "field_index",
        "recorded",
    )

    def __init__(
        self, kind: str, name: str | None, depth: int, qualified: str | None
    ) -> None:
        self.kind = kind  # "ns" | "rec"
        self.name = name  # bare name; None for an anonymous namespace
        self.depth = depth  # brace_depth just below this scope's body
        # Current C++ access (records only); default from the keyword.
        self.access = "public"
        self.qualified = qualified  # records only; None if unrecordable
        # Source-order member counter and the recorded guarded entries (with the
        # member index they sit at) — used to stamp ``is_last`` on close so the
        # reconciler can prove a reconciled field is terminal (Codex review #498).
        self.field_index = 0
        self.recorded: list[tuple[dict[str, object], int]] = []


class _ScanState:
    """Mutable state threaded through one :func:`scan_conditional_fields` pass."""

    __slots__ = (
        "include_guard",
        "registry",
        "scope_stack",
        "guard_stack",
        "locally_undefined",
        "conditionally_touched",
        "saw_include",
        "brace_depth",
        "pending",
    )

    def __init__(self, include_guard: str | None) -> None:
        self.include_guard = include_guard
        self.registry: dict[str, dict[str, dict[str, object]]] = {}
        # scope_stack: every open namespace/record body, outermost first. The
        # innermost record (if any) owns the fields on the current line; its
        # ``qualified`` name keys the registry.
        self.scope_stack: list[_Scope] = []
        # guard_stack entries: the positive macro for a simple ``#ifdef`` region,
        # ``None`` for an opaque region we must not record fields in
        # (negative/compound/else), or the ``_INCLUDE_GUARD`` sentinel for a
        # transparent file include guard (ignored when deciding to record).
        self.guard_stack: list[object] = []
        # Macros the header itself ``#undef``s (and has not since ``#define``d). A
        # guard whose macro is header-locally undefined is *inactive* in the real
        # build even if the compile DB defines it, so a field under it must not be
        # recorded as reconcilable — the build really pruned it (Codex review #498).
        self.locally_undefined: set[str] = set()
        # Macros ``#undef``'d or ``#define``'d **inside a branch the scanner cannot
        # evaluate** (a non-transparent conditional). Such an operation only fires when
        # its enclosing condition is active under the build's defines — which the
        # context-free scan does not know — so a field guarded by such a macro cannot be
        # resolved by the simple ``macro ∈ defines`` test. It is recorded with
        # ``ambiguous: True`` and the reconciler refuses to reconcile its type at all,
        # rather than risk adding back / pruning the wrong field (Codex review #498).
        self.conditionally_touched: set[str] = set()
        # Whether an ``#include`` has appeared so far. A scanned header cannot see what
        # an included file does to a macro — an ``#include "config.h"`` that ``#undef``s
        # the guard would make the real build prune a field this text scan still
        # records. So a guarded field *after* any include is marked ``ambiguous`` (its
        # guard state is unprovable without a preprocessor), and the reconciler keeps
        # its type rather than risk a wrong clear (Codex review #498, P1). Self-contained
        # headers (no preceding include) stay reconcilable.
        self.saw_include = False
        self.brace_depth = 0
        # pending: (kind, name, keyword) awaiting its opening brace. kind is "ns" or
        # "rec"; keyword is the record keyword (or "" for a namespace).
        self.pending: tuple[str, str | None, str] | None = None

    def innermost_record(self) -> _Scope | None:
        """The innermost open scope when it is a record body, else ``None``."""
        if self.scope_stack and self.scope_stack[-1].kind == "rec":
            return self.scope_stack[-1]
        return None

    def only_transparent(self) -> bool:
        """True when nothing but the (transparent) file include guard is open — i.e. we are effectively at file top level."""
        return all(g is _INCLUDE_GUARD for g in self.guard_stack)


def _note_macro_touch(state: _ScanState, macro: str, *, define: bool) -> None:
    """Track a header-local ``#undef``/``#define`` of *macro* at the current conditional depth."""
    if state.only_transparent():
        if define:
            # A **top-level** ``#define`` reactivates a locally-undefined
            # guard (it is defined for every build).
            state.locally_undefined.discard(macro)
        else:
            # A **top-level** ``#undef`` genuinely undefines the macro for
            # every build, so a later guard on it is never active — mark it
            # header-locally undefined (its guarded fields are not recorded).
            state.locally_undefined.add(macro)
        return
    # An ``#undef`` **inside a branch we cannot evaluate** fires only
    # when its condition is active under the build's defines. We
    # cannot tell statically, so the macro is *ambiguous* — a field
    # guarded by it must not be reconciled either way (Codex #498):
    # ignoring the undef wrongly adds back a pruned field when the
    # branch is active; honouring it wrongly prunes a present field
    # when the branch is inactive. A conditional ``#define`` makes the
    # macro's state build-context dependent for the same reason. (It does
    # not reactivate a top-level ``#undef``; a build skipping the branch
    # keeps the macro undefined.)
    state.conditionally_touched.add(macro)


def _push_ifndef(state: _ScanState, macro: str) -> None:
    """Push an ``#ifndef`` *macro* region: the transparent file include guard or a negative guard."""
    if macro == state.include_guard and _INCLUDE_GUARD not in state.guard_stack:
        state.guard_stack.append(_INCLUDE_GUARD)  # transparent file guard
    else:
        state.guard_stack.append(_NegGuard(macro))  # simple #ifndef


def _track_conditional_directive(state: _ScanState, line: str) -> bool:
    """Push/pop the guard stack for a ``#if*``/``#else``/``#elif``/``#endif`` *line*; True when it was one."""
    ifdef = _IFDEF.match(line)
    if_defined = None if ifdef else _IF_DEFINED.match(line)
    ifndef_m = _IFNDEF.match(line)
    if ifdef:
        state.guard_stack.append(ifdef.group(1))
    elif if_defined:
        state.guard_stack.append(if_defined.group(1) or if_defined.group(2))
    elif ifndef_m is not None:
        _push_ifndef(state, ifndef_m.group(1))
    elif _PP_OPEN_OTHER.match(line):
        state.guard_stack.append(None)  # compound #if <expr>: not recordable
    elif _ELSE_ELIF.match(line):
        if state.guard_stack:
            state.guard_stack[-1] = None  # the else/elif branch is not the guard
    elif _ENDIF.match(line):
        if state.guard_stack:
            state.guard_stack.pop()
    else:
        return False
    return True


def _track_directive(state: _ScanState, line: str) -> None:
    """Update *state* for one preprocessor-directive *line* (any other directive is ignored)."""
    if _INCLUDE.match(line):
        state.saw_include = True  # subsequent guards may be altered by the include
        return
    if _track_conditional_directive(state, line):
        return
    if (um := _UNDEF.match(line)) is not None:
        _note_macro_touch(state, um.group(1), define=False)
    elif (dm := _DEFINE.match(line)) is not None:
        _note_macro_touch(state, dm.group(1), define=True)


def _active_guard(guard_stack: list[object]) -> tuple[str | None, bool]:
    """The single recordable guard as ``(macro, is_negative)``, or ``(None, False)``.

    Recordable means exactly one active guard — a positive ``#ifdef`` or a
    negative ``#ifndef`` — with no opaque region and the transparent include
    guard ignored."""
    positive = [g for g in guard_stack if isinstance(g, str)]
    negatives = [g for g in guard_stack if isinstance(g, _NegGuard)]
    has_opaque = any(g is None for g in guard_stack)
    if has_opaque or len(positive) + len(negatives) != 1:
        return None, False
    if positive:
        return positive[0], False
    return negatives[0].macro, True


def _body_opens_first(rest: str) -> bool:
    """Whether *rest* (the text after an opener's name) reaches ``{`` before any ``;``.

    A ``;`` before any ``{`` marks a forward declaration / variable / namespace
    alias (``namespace x = y;``) — not a body opener — so it is ignored. When
    neither appears, the brace may follow on a later line (the opener stays
    pending)."""
    brace = rest.find("{")
    semi = rest.find(";")
    return (brace != -1 and (semi == -1 or brace < semi)) or (
        brace == -1 and semi == -1
    )


def _detect_scope_opener(line: str) -> tuple[str, str | None, str] | None:
    """A ``(kind, name, keyword)`` namespace/record opener on *line*, or ``None``.

    A namespace or record *definition* may open on this line (``struct
    Name {`` / ``namespace api {``) or name first and brace next. kind is "ns"
    or "rec"; keyword is the record keyword (or "" for a namespace)."""
    nsm = _NAMESPACE_OPEN.match(line)
    rm = None if nsm else _RECORD_OPEN.search(line)
    if nsm and _body_opens_first(line[nsm.end() :]):
        return ("ns", nsm.group(1), "")
    if rm and _body_opens_first(line[rm.end() :]):
        return ("rec", rm.group(2), rm.group(1))
    return None


def _guarded_field_entry(
    state: _ScanState,
    parsed: tuple[str, str, bool, int | None, bool, bool, bool],
    guard: str,
    is_negative: bool,
    access: str,
) -> dict[str, object]:
    """The registry entry for one *parsed* field recorded under *guard*."""
    _name, type_str, is_bitfield, bits, is_const, is_volatile, is_mutable = parsed
    entry: dict[str, object] = {
        "guard": guard,
        "type": type_str,
        "is_bitfield": is_bitfield,
        "bitfield_bits": bits,
        "access": access,
        "is_const": is_const,
        "is_volatile": is_volatile,
        "is_mutable": is_mutable,
    }
    if is_negative:
        # An ``#ifndef GUARD`` field: observed context-free, but
        # pruned by a build that *defines* GUARD.
        entry["negative"] = True
    if guard in state.conditionally_touched or state.saw_include:
        # The guard macro is ``#undef``/``#define``d inside a branch
        # we cannot evaluate, **or** an earlier ``#include`` may have
        # altered it (the text scan cannot follow includes) → its
        # state is unprovable. Flag the field so the reconciler keeps
        # (never reconciles) findings on this type (Codex review #498).
        entry["ambiguous"] = True
    return entry


def _capture_guarded_field(state: _ScanState, rec: _Scope, line: str) -> None:
    """Record a field of *rec* declared on *line* under a single recordable guard.

    Records a field only when exactly one active guard — a positive ``#ifdef``
    or a negative ``#ifndef`` — is open, with no opaque region and the
    transparent include guard ignored, directly in a recordable body (not an
    anonymous namespace)."""
    qualified = rec.qualified
    if qualified is None:
        return  # anonymous-namespace record — its fields are never recorded
    fm = _FIELD.match(line)
    parsed = _parse_field(fm.group("decl")) if fm else None
    # Count **every** member-looking declaration in source order — not just
    # the ones ``_parse_field`` can decode. Array members (``int t[4];``),
    # default-initialised members (``int x = 0;``), methods, attributed members,
    # nested declarations, and multiline declarations whose final line is only
    # ``;`` all advance the conservative proof position. Access labels have
    # already been consumed by the caller. A line starting with ``}`` is a scope
    # close, not a following member. False positives only suppress reconciliation;
    # false negatives could incorrectly claim that the guarded field is terminal.
    is_memberish = bool(line) and not line.startswith("}") and line.endswith(";")
    if parsed is None and not is_memberish:
        return
    pos = rec.field_index
    rec.field_index += 1
    guard, is_negative = _active_guard(state.guard_stack)
    if parsed is None or guard is None or guard in state.locally_undefined:
        return
    entry = _guarded_field_entry(state, parsed, guard, is_negative, rec.access)
    state.registry.setdefault(qualified, {})[parsed[0]] = entry
    rec.recorded.append((entry, pos))


def _open_pending_scope(
    state: _ScanState, pending: tuple[str, str | None, str]
) -> None:
    """Push the *pending* namespace/record opener as a newly opened scope."""
    kind, sc_name, keyword = pending
    if kind == "rec":
        qualified = _record_qualified_name(state.scope_stack, sc_name or "")
        sc = _Scope("rec", sc_name, state.brace_depth, qualified)
        sc.access = "private" if keyword == "class" else "public"
        state.scope_stack.append(sc)
    else:
        state.scope_stack.append(_Scope("ns", sc_name, state.brace_depth, None))


def _close_scope(state: _ScanState) -> None:
    """Pop the innermost scope and stamp ``is_last`` on its recorded guarded fields."""
    closing = state.scope_stack.pop()
    # Now that every member is counted, stamp ``is_last`` on the
    # record's recorded guarded fields: True iff the field is the
    # final data member in source order (Codex review #498). A
    # reconciled field must be terminal so re-adding / pruning it
    # cannot reorder a sibling.
    last_index = closing.field_index - 1
    for rec_entry, member_pos in closing.recorded:
        rec_entry["is_last"] = member_pos == last_index


def _track_braces(state: _ScanState, line: str) -> None:
    """Advance ``brace_depth`` across *line*, opening/closing scopes as braces pass."""
    for ch in line:
        if ch == "{":
            if state.pending is not None:
                _open_pending_scope(state, state.pending)
                state.pending = None
            state.brace_depth += 1
        elif ch == ";" and state.pending is not None:
            # A ``;`` reached before the pending opener's ``{`` terminates a
            # *split* forward declaration (``struct S`` then ``;`` on the next
            # line). Clear ``pending`` so a later ``struct T { … }`` opens its
            # own scope instead of being keyed to ``S`` — otherwise ``T``'s
            # guarded fields would be misattributed to ``S`` and could
            # reconcile away a real ``S`` field change (Codex review #498).
            state.pending = None
        elif ch == "}":
            state.brace_depth = max(0, state.brace_depth - 1)
            if state.scope_stack and state.brace_depth == state.scope_stack[-1].depth:
                _close_scope(state)


def scan_conditional_fields(source: str) -> dict[str, dict[str, dict[str, object]]]:
    """Scan header *source* for record fields under a single positive ``#ifdef``.

    Returns ``{record: {field: {"guard": macro, "type": t, "is_bitfield": b,
    "bitfield_bits": n, "access": a, "is_const": c, "is_volatile": v,
    "is_mutable": m, "is_last": bool}}}`` (plus ``"negative": True`` for an
    ``#ifndef`` field and ``"ambiguous": True`` when the guard macro is
    ``#undef``/``#define``d inside an unevaluable branch). ``is_last`` records
    whether the field is the final data member of its record in source order — the
    reconciler only clears a presence finding for a *terminal* field, so re-adding
    or pruning it cannot reorder a sibling. Each record is keyed by its
    **namespace/class-qualified** name (``api::S``). Qualifying keeps two
    same-named records in different namespaces distinct (no conflation) and skips
    anonymous-namespace records; the reconciler matches this key *exactly* against
    ``RecordType.name`` (Codex review #498). Best-effort and conservative — see
    the module docstring for what is deliberately not recorded.
    """
    src = _strip_comments(source)
    lines = src.splitlines()
    state = _ScanState(_include_guard_macro(lines))
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            _track_directive(state, line)
            continue
        rec_here = state.innermost_record()
        if rec_here is not None and state.brace_depth == rec_here.depth + 1:
            # An access label (``public:``) alone on a line switches the current
            # access for the enclosing record body.
            am = _ACCESS_LABEL.match(line)
            if am:
                rec_here.access = am.group(1)
                continue
            _capture_guarded_field(state, rec_here, line)
        if state.pending is None:
            state.pending = _detect_scope_opener(line)
        _track_braces(state, line)
    return {rec: fields for rec, fields in state.registry.items() if fields}


def _has_forced_include(tokens: Iterable[str]) -> bool:
    """Whether *tokens* carry a forced-include flag (``-include`` / ``-imacros``).

    These preprocess a file before the translation unit, injecting macro state the
    per-file scan cannot see, so guarded fields must be treated as ``ambiguous``
    (Codex review #498)."""
    return any(t.startswith(("-include", "-imacros")) for t in tokens)


def _compile_db_has_forced_include(path: str | Path, source_filter: str | None) -> bool:
    """Whether any (filtered) compile-DB command carries a forced-include flag.

    Mirrors :func:`defines_from_compile_db`'s read/filter so a ``-include`` in the
    real build's command line — not just the user's ``--compiler-option`` — makes the
    scanned guards ``ambiguous`` (Codex review #498). Best-effort: any read/parse
    error is treated as *no* forced include (the collector never aborts a dump)."""
    p = Path(path)
    if p.is_dir():
        p = p / "compile_commands.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, list):
        return False
    entries = [e for e in data if isinstance(e, dict)]
    if source_filter:
        matched = [e for e in entries if _compile_entry_matches(e, source_filter)]
        if matched:
            entries = matched
    for entry in entries:
        args = entry.get("arguments")
        if not isinstance(args, list):
            args = _split_command(entry.get("command"))
        if _has_forced_include(str(a) for a in args):
            return True
    return False


def collect_build_context(
    header_paths: Iterable[str | Path],
    compile_db: str | Path | None,
    *,
    extra_flags: Iterable[str] = (),
    source_filter: str | None = None,
) -> tuple[set[str], dict[str, dict[str, dict[str, object]]]]:
    """Collect ``(build_context_defines, conditional_fields)`` for a dump.

    The active define set is the compile-DB intersection with ``extra_flags``
    applied **on top** (in order), not unioned: the real parse passes the user's
    global flags *after* the compile-DB options, so a ``-UKEEP`` extra flag must
    override a database ``-DKEEP`` rather than be re-added by it (Codex review
    #498). *source_filter* (``--compile-db-filter``) narrows the compile-DB
    entries to those the filtered header parse used, so a guard defined only by
    the selected TU is harvested. The registry comes from scanning each readable
    header path. Never raises — an unreadable header is skipped so a dump is
    never aborted by the (optional) collection layer.
    """
    db_defines = (
        defines_from_compile_db(compile_db, source_filter)
        if compile_db is not None
        else set()
    )
    extra = list(extra_flags)
    defines: set[str] = defines_from_flags(extra, initial=db_defines)

    # A forced include (``-include foo.h`` / ``-imacros foo.h``) is preprocessed
    # *before* the public header, so it can ``#undef``/``#define`` a guard macro the
    # per-file scan never sees — exactly like an in-source ``#include`` (Codex
    # review #498). Its macro state is unknown here, so mark every scanned guarded
    # field ``ambiguous`` and let the reconciler keep those types.
    forced_include = _has_forced_include(extra) or (
        compile_db is not None
        and _compile_db_has_forced_include(compile_db, source_filter)
    )

    registry: dict[str, dict[str, dict[str, object]]] = {}
    for path in header_paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rec, fields in scan_conditional_fields(text).items():
            if forced_include:
                for entry in fields.values():
                    entry["ambiguous"] = True
            registry.setdefault(rec, {}).update(fields)
    return defines, registry


# ── shared entry points (CLI dump and the typed input-resolution pipeline) ──
#
# `user_define_flags`/`attach_build_context` used to be private to
# `cli_dump_helpers.py` (the ELF `dump` CLI's own `perform_elf_dump`). PR C
# (CLI cleanup phase two, typed dump/scan convergence) moved them here, a
# dependency-free leaf module already owning `collect_build_context`, so
# `service_input_resolution._resolve_side_snapshot_impl` (the typed pipeline
# `compare`'s implicit-dump operand and `dump`'s typed `run_dump_request` API
# share) can reach the identical ADR-039 collection logic without importing
# `cli_dump_helpers.py` (a CLI-presentation-layer module, and an import a
# service module must not take). `cli_dump_helpers.py` keeps calling these
# under their original names via a re-export, so `perform_elf_dump` and every
# existing test needed no changes.


def user_define_flags(
    gcc_option_tokens: tuple[str, ...], user_gcc_options: str | None
) -> list[str]:
    """The user's *global* define-affecting flags for the ADR-039 collector.

    Combines the ``-D``/``-U`` in the ``--gcc-options`` string with the repeatable
    ``--compiler-option`` tokens, **in the same order the real dump applies them** —
    ``dumper._castxml_cmd`` appends ``gcc_options`` first, then
    ``gcc_option_tokens`` (see ``dumper.py``), so the collector must too (Codex
    review #498). Order is significant because ``defines_from_flags`` honours
    ``-D``/``-U`` sequence: a composed ``-DKEEP`` followed by ``--compiler-option=-UKEEP`` must leave
    ``KEEP`` *inactive* on both the parse and the harvest, else the reconciler
    would add back a field the real parse pruned. These flags are applied on top
    of the compile-DB intersection, so a user ``-UKEEP`` also overrides a database
    ``-DKEEP``. **The auto-derived / L3→L2-folded build context is deliberately
    excluded (it must not be unioned snapshot-wide)** — a caller must pass only
    its own pre-fold, explicitly-given flags here, never a resolved
    ``CompileContext``'s derived tokens (see the ninth finding in the root
    ``AGENTS.md``'s L3→L2-fold entry for why: unioning would mark one TU's
    ``-D`` active for every scanned header).

    A malformed ``--gcc-options`` (e.g. an unbalanced quote) must not abort the
    dump — ``split_gcc_options`` errors are swallowed and only the tokens are
    used (CodeRabbit review). ``split_gcc_options`` (not a bare
    ``shlex.split``) so this collector tokenizes ``user_gcc_options``
    identically to the real header-AST parse on every platform — a plain
    POSIX ``shlex.split`` would silently disagree with the Windows tokenizer
    on an unquoted Windows path (e.g. combining ``-IC:\\sdk\\ -UKEEP`` into
    one token instead of two), which could make the harvested define set
    diverge from what the real parse actually saw (Codex review, PR #774
    follow-up)."""
    flags: list[str] = []
    if user_gcc_options:
        try:
            flags += split_gcc_options(user_gcc_options)
        except ValueError:
            pass  # bad optional define flags are skipped, not fatal
    flags += list(gcc_option_tokens)
    return flags


def compile_db_from_build_info(
    build_info: Path | None,
    headers: tuple[Path, ...],
) -> Path | None:
    """The L2 compile database ``--build-info`` names, when it names one.

    ``--build-info``'s operand is "a build dir, a compile_commands.json, or a
    pre-captured pack" -- the first two are exactly what the removed
    ``-p/--build-dir`` (and its ``--compile-db`` alias) took, so the database
    that drives deterministic header parsing is read back off the one
    remaining build-context flag rather than a second spelling of the same
    path.

    ``None`` without headers: a compile database only feeds the header AST,
    and a headerless ``--build-info`` run is an ordinary L3-only dump, not the
    usage error the dedicated flag used to raise ("requires -H/--header").

    ``None`` for a *file* that is not a compile database, too. ``--build-info``
    also accepts a Bazel aquery/cquery jsonproto, which
    ``buildsource.inline._maybe_collect_bazel_build_info`` routes through the
    Bazel adapter -- but only if it gets that far. Returning any file here
    handed such a run to ``load_compile_db()``, which rejects a JSON object
    with "compile_commands.json must be a JSON array" long before the adapter
    is reached, so `--build-info aquery.json -H api.h` failed outright
    (Codex review). ``sniff_build_info_format`` is the same content-based
    classifier that dispatch uses, so the two cannot disagree about what a
    given file is.
    """
    from .buildsource.inline import sniff_build_info_format

    if build_info is None or not headers:
        return None
    if build_info.is_file():
        return build_info if sniff_build_info_format(build_info) == "compile_db" else None
    db = build_info / "compile_commands.json"
    # Sniffed for the same reason: a build directory holding a non-array
    # compile_commands.json is the identical mis-route one level down.
    return db if db.is_file() and sniff_build_info_format(db) == "compile_db" else None


def compile_db_for_filter_scope_check(
    build_info: Path | None,
    sources: Path | None,
    headers: tuple[Path, ...],
) -> Path | None:
    """The compile database ``compile_db_filter_scope_error`` should check
    against -- covering both ways the L3->L2 fold can resolve one, not only
    an explicit ``--build-info`` (Codex review, fresh evidence).

    ``compile_db_from_build_info`` alone under-covers this: when no
    ``--build-info`` is given but ``--sources`` names a tree containing an
    auto-discovered ``compile_commands.json``
    (``buildsource.inline._autodiscover_compile_db`` -- the identical P4
    strategy ``seed_includes_and_fold_compile_context``'s own fold and
    ``embed_build_source``'s L3 collection *both* already use to find one
    from ``sources`` alone), the fold still narrows the L2 header parse by
    ``compile_db_filter`` while the L3 embed collects the same
    auto-discovered database unfiltered -- the exact filtered-L2/
    unfiltered-L3 mismatch this whole scope guard exists to reject, just
    reached without ``--build-info`` at all. Reproduced directly: a real
    ``g++``-compiled two-TU project resolved with ``sources`` only (no
    ``build_info``) and a filter narrowing the L2 parse to one TU still
    embedded both TUs' compile units as L3 evidence.

    A second, distinct under-coverage (Codex review, fresh evidence): even
    with an explicit ``--build-info <dir>`` given, ``compile_db_from_
    build_info`` only ever checks ``<build_info>/compile_commands.json``
    directly -- it has no notion of a conventional out-of-tree build
    directory (``<build_info>/build/compile_commands.json``,
    ``<build_info>/cmake-build-debug/compile_commands.json``, ...). The
    real fold's own ``--build-info`` resolution
    (``buildsource.inline._compile_db_at``, for a directory delegating to
    ``_find_compile_db_in_dir``) searches those conventional hint
    subdirectories -- explicitly "so ``--build-info <dir>`` honours the
    same contract as ``--sources`` auto-discovery" per that function's own
    docstring -- so a ``--build-info`` naming a project root whose database
    lives one level down resolves for the fold but not for this scope
    check. Reproduced directly: a real two-TU project with its
    ``compile_commands.json`` moved into a ``build/`` subdirectory,
    ``--build-info`` pointed at the project root -- the fold correctly
    found and filtered by the nested database (L3 compile units: 2, L2
    narrowed to the filtered TU), and the guard never fired.

    A third under-coverage (Codex review, fresh evidence): the fold's own
    ``resolve_header_compile_context``/``filter_units_by_source`` narrows
    *whatever* ``BuildEvidence.compile_units`` a ``--build-info`` resolves
    to -- it is not specific to a literal ``compile_commands.json``. A
    ``--build-info`` naming a pre-captured ``collect`` pack directory
    (``is_pack_dir``), a Flow-2 ``abicheck_inputs/`` pack
    (``inputs_pack.is_inputs_pack`` -- a ninth finding, Codex review, fresh
    evidence, closing the identical gap this paragraph's own ``--sources``
    sibling below already covers, missed here originally since the pack
    recognition and the Bazel-jsonproto recognition were added in the same
    pass and only ``is_pack_dir`` was carried over), or a Bazel
    ``aquery``/``cquery`` jsonproto (routed through their own adapters in
    ``buildsource.inline._maybe_collect_bazel_build_info``/pack loading, not
    ``load_compile_db()``) resolves compile units the identical way a
    literal compile database does, and the L3 embed collects that same
    ``BuildEvidence`` with no filter either way -- so the mismatch this
    guard exists to reject reproduces for those three shapes too, just never
    detected because none is a ``compile_commands.json`` file
    ``compile_db_from_build_info`` recognizes. ``sniff_build_info_format``
    is the same cheap, execution-free classifier ``compile_db_from_
    build_info`` already uses to tell the Bazel shapes apart -- checking it
    here costs nothing and cannot disagree with what dispatch itself does
    with the same path.

    Deliberately **not** folded into ``compile_db_from_build_info`` itself
    -- that function's result also drives ``dump``'s own legacy ``-p``
    auto-match (``cli.py``'s ``effective_compile_db``), which is
    intentionally ``--build-info``-only (direct child only, no subdirectory
    search, and only ever a real ``compile_commands.json`` -- a pack/Bazel
    jsonproto was never a valid ``-p`` operand) and widening it here would
    widen that unrelated, separately reviewed mechanism too.

    A fourth under-coverage (Codex review, fresh evidence): the third
    under-coverage's fix only checked a *pack* named by ``--build-info``,
    but ``buildsource.l2_seed._l2_seed_pack_inputs`` recognizes a
    ``--sources`` pack (a classic ``BuildSourcePack`` or a Flow-2
    ``abicheck_inputs/`` directory) the identical way -- carrying its own
    normalized ``BuildEvidence`` into L2 seeding -- whenever *no*
    ``--build-info`` was given at all (an explicit ``--build-info`` always
    wins L3, matching ``_l2_seed_pack_inputs``'s own ``if build_info is
    None:`` gate on that assignment). A ``--sources`` naming such a pack,
    with no ``--build-info``, therefore reproduced the identical mismatch:
    this function's fallback resolution (``compile_db_from_build_info``
    then ``_autodiscover_compile_db``) only ever looks for a literal
    ``compile_commands.json`` inside *sources*, which a pack directory does
    not carry at its root. Fixed by recognizing a ``sources`` pack via the
    same two functions ``_l2_seed_pack_inputs`` itself uses (``is_pack_dir``
    / ``inputs_pack.is_inputs_pack``), gated on ``build_info is None`` to
    match that function's own precedence exactly.

    Still not covered, and not attempted here: a ``--sources`` tree with no
    discoverable ``compile_commands.json`` at all, resolved instead through
    the zero-config *inferred* build-system query (cmake/make/bazel). Unlike
    every case above, telling whether that combination would actually
    resolve multiple compile units means running the build system's own
    query -- the exact side effect this cheap, read-only scope check is
    built to avoid paying twice per invocation. See this repository's
    ``AGENTS.md`` "Known gaps" for that residual, documented rather than
    guessed at.

    ``None`` when neither source names usable build evidence, or when
    *headers* is empty (mirrors ``compile_db_from_build_info``: no L2
    header parse to narrow means nothing for the filter to be inconsistent
    with).
    """
    if not headers:
        return None
    explicit = compile_db_from_build_info(build_info, headers)
    if explicit is not None:
        return explicit
    if build_info is not None:
        from .buildsource.inline import sniff_build_info_format

        if build_info.is_dir():
            from .buildsource.inline import _find_compile_db_in_dir, is_pack_dir
            from .buildsource.inputs_pack import is_inputs_pack

            nested = _find_compile_db_in_dir(build_info)
            if nested is not None:
                return nested
            # Codex review, fresh evidence (a ninth finding): a --build-info
            # naming a Flow-2 abicheck_inputs/ pack is recognized by
            # _l2_seed_pack_inputs (is_pack_dir OR _is_inputs_pack_dir) and
            # by embed_build_source's own bi_is_inputs check -- this branch
            # previously checked only is_pack_dir, missing the Flow-2 shape
            # its own --sources sibling below already covers.
            if is_pack_dir(build_info) or is_inputs_pack(build_info):
                return build_info
        elif build_info.is_file() and sniff_build_info_format(build_info) in (
            "bazel_aquery",
            "bazel_cquery",
        ):
            return build_info
        # Codex review, fresh evidence (an eighth finding): an explicit
        # --build-info that resolves to none of the above must NOT fall
        # through to sources auto-discovery. buildsource.inline.
        # _resolve_compile_db's own `explicit_input_missed` logic returns
        # None as soon as a given --build-info misses -- deliberately,
        # per its own comment, "surface that miss rather than masking it
        # with a stale auto-discovered DB ... checked BEFORE
        # auto-discovery" -- so neither the real L2 fold nor the L3 embed
        # ever falls back to a sources-discovered database in this case.
        # Falling back here (the pre-fix behavior) produced a false
        # positive: rejecting a --compile-db-filter combination the real
        # resolvers wouldn't actually apply it to either side of.
        return None
    # Codex review, fresh evidence: a --sources pack (classic BuildSourcePack or
    # Flow-2 abicheck_inputs/) carries its own normalized BuildEvidence, which
    # buildsource.l2_seed._l2_seed_pack_inputs folds into L2 seeding exactly
    # like a --build-info pack -- but only when no --build-info was given at
    # all (an explicit --build-info always wins L3, mirroring
    # _l2_seed_pack_inputs's own `if build_info is None:` gate on the base_build
    # assignment). Recognized the identical way _l2_seed_pack_inputs does
    # (is_pack_dir / inputs_pack.is_inputs_pack), not via the compile-
    # database-only sniff this function otherwise uses.
    if build_info is None and sources is not None and sources.is_dir():
        from .buildsource.inline import is_pack_dir
        from .buildsource.inputs_pack import is_inputs_pack

        if is_pack_dir(sources) or is_inputs_pack(sources):
            return sources
    from .buildsource.inline import _autodiscover_compile_db

    return _autodiscover_compile_db(sources)


def compile_db_filter_scope_error(
    compile_db_filter: str | None,
    compile_db_path: Path | None,
    collect_mode: str,
) -> str | None:
    """Refuse a filter that would scope L2 but silently not L3.

    ``--compile-db-filter`` parameterizes the **header parse** only: the
    filtered subset is what ``_resolve_build_context_flags`` proves a match
    against and what the AST is built from. L3 collection reads the database
    through ``embed_build_source``'s own adapter, which has no filter of its
    own -- so on a monorepo database the embedded build facts cover every
    translation unit while the headers cover the requested subset, and the
    resulting snapshot carries unrelated build evidence that can produce
    false differences.

    The removed ``-p``/``--compile-db`` spelling refused exactly this: its
    ``resolve_compile_db_l3_reuse`` declined to promote the header database
    to L3 whenever a filter was active, and said so. Folding the operand into
    ``--build-info`` removed that decision -- ``--build-info`` *is* the L3
    selector now, so there is nothing left to decline -- which turned a loud
    refusal into a silent unfiltered collection (Codex review). This restores
    the refusal as a usage error, since the honest alternative (threading the
    filter through ``embed_build_source`` into the compile-DB adapter) is a
    change to ``buildsource/`` with its own evidence gates, not something to
    infer from one review round.

    ``None`` when the combination cannot arise: no filter, no resolvable
    build evidence for *compile_db_path* to name (see
    ``compile_db_for_filter_scope_check``, this function's real caller --
    since ADR/Codex review it resolves a pack directory and a Bazel
    aquery/cquery jsonproto here too, not only a literal
    ``compile_commands.json``, so this parameter no longer names a compile
    database specifically despite its name), or ``collect_mode == "off"``,
    where no build evidence is embedded for the filter to be inconsistent
    with.
    """
    if not compile_db_filter or compile_db_path is None or collect_mode == "off":
        return None
    return (
        "--compile-db-filter scopes the L2 header parse only; the L3 build "
        "evidence embedded from the same --build-info source is collected "
        "unfiltered, so the snapshot would carry build facts for "
        "translation units the filter excludes. Pass a pre-filtered "
        "compile_commands.json as --build-info (then --compile-db-filter is "
        "unnecessary), or drop --compile-db-filter to accept the whole "
        "database on both layers."
    )


def attach_build_context(
    snap: AbiSnapshot,
    compile_db: str | Path,
    headers: list[Path],
    extra_flags: list[str],
    source_filter: str | None = None,
) -> None:
    """ADR-039 collection layer: harvest the build's active ``-D`` set and scan the
    public headers for ``#ifdef``-guarded record fields, attaching both to *snap*.

    Best-effort and additive — a plain context-free dump (no compile DB) never
    reaches here, and an empty harvest leaves the snapshot's defaults untouched, so
    the pass is a safe no-op unless real build evidence is found. *source_filter*
    (``--compile-db-filter``) selects the same compile-DB entries the header parse
    used."""
    bc_defines, bc_conditional = collect_build_context(
        headers, compile_db, extra_flags=extra_flags, source_filter=source_filter
    )
    if bc_defines:
        snap.build_context_defines = bc_defines
    if bc_conditional:
        snap.conditional_fields = bc_conditional


def expand_collector_headers(headers: Iterable[Path]) -> list[Path]:
    """The header *files* the ADR-039 collector should scan, best-effort.

    A caller's header list may still hold a directory entry -- exactly what
    ``service.resolve_input``/``_dump_elf`` expands internally before parsing,
    for the native-binary dispatch. The collector reads each entry with
    ``Path(path).read_text(...)``, which raises ``OSError`` for a directory and
    is silently skipped, so passing the raw, unexpanded list leaves
    ``conditional_fields`` empty for the common directory-header-input case
    even though headers were genuinely parsed.

    Deliberately **not** ``header_utils.expand_header_inputs``: that is the
    strict, *raising* expander, correct only where every entry is known to
    exist because a live header-AST parse is about to read it. A side resolved
    from an already-serialized JSON snapshot carries ``headers`` for provenance
    tagging alone -- entries that need not exist on this filesystem at all --
    and the strict expander raises ``ValidationError`` for those (confirmed
    against a real snapshot-file compare). This mirrors
    :func:`collect_build_context`'s own "an unreadable header is skipped, never
    abort the dump" contract instead: anything that is neither a file nor a
    directory here is dropped, never raised on.
    """
    from .buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
    from .header_utils import iter_directory_headers

    out: list[Path] = []
    for header in headers:
        if header.is_file():
            out.append(header)
        elif header.is_dir():
            out.extend(iter_directory_headers(header, PRUNED_HEADER_DIR_SEGMENTS))
    return out


def attach_build_context_for_parsed_headers(
    snap: AbiSnapshot,
    headers: Iterable[Path],
    *,
    live_elf_parse: bool,
    compile_db: Path | None = None,
    build_info: Path | None = None,
    user_gcc_option_tokens: Iterable[str] = (),
    user_gcc_options: str | None = None,
    source_filter: str | None = None,
) -> bool:
    """Run the ADR-039 collector for one resolved input, when it applies.

    The one shared implementation of "should this input's snapshot carry
    build-context evidence, and from which compile database" -- CLI cleanup
    phase two, PR 3A (dump/scan resolver convergence). Two resolvers reach the
    collector today -- the typed pipeline's
    :func:`~abicheck.service_input_resolution._resolve_side_snapshot_impl` and
    :func:`~abicheck.scan_engine._build_new_snapshot`; three until ADR-063
    Track 1 deleted the dead ``perform_elf_dump``. Before this helper each
    gate was hand-written, which is how ``scan`` ended up as the one candidate
    resolver that never collected ADR-039 evidence at all while a ``dump``
    baseline it was compared against always did.

    Returns whether the collector actually ran. Every gate is a real one, and
    each is recorded here rather than at three call sites:

    * A compile database must be resolvable -- either supplied directly as
      *compile_db* (the ELF ``dump`` CLI already resolved one) or named by
      *build_info* (:func:`compile_db_from_build_info`; a pack or a Bazel
      jsonproto routes through a different adapter and yields ``None``).
      *compile_db* wins when both are given.
    * At least one header must survive :func:`expand_collector_headers`. The
      collector has nothing to scan otherwise -- a headerless ``--build-info``
      run is an ordinary L3-only dump, and ``--depth binary`` clears the header
      list outright.
    * ``snap.from_headers`` must be true -- a ``--dwarf-only`` run explicitly
      ignores ``-H``, so a database matching the *requested* headers is not
      evidence about a snapshot that never parsed them. This is the same gate
      the sibling ``AbiSnapshot.parsed_with_build_context`` stamp already
      applies at all three of these call sites.
    * *live_elf_parse* must be true -- the caller's own answer to "did a live
      **ELF** header parse actually produce this snapshot", which only it can
      give because only it knows its own dispatch. Two distinct things are
      folded into it deliberately, since each call site establishes them
      differently:

      - *ELF*: the ADR-039 collector is an established ELF-only mechanism (the
        PE/Mach-O ``dump`` path never calls it), so a non-ELF snapshot must not
        gain build-context evidence here and then silently disagree with the
        native path. The ELF ``dump`` CLI knows this from its own
        ``binary_fmt == "elf"`` dispatch; the typed pipeline and ``scan`` read
        ``snap.elf is not None`` instead -- the model's own *post*-resolution
        signal, deliberately not a pre-resolution format sniff, because a GNU
        ld linker script (the conventional development ``libfoo.so`` symlink)
        sniffs as neither ELF nor anything else yet resolves to a genuine ELF
        snapshot once followed.
      - *live*: ``snap.elf is not None`` alone is **not** sufficient for a
        caller whose input may be an already-serialized snapshot. A *loaded*
        JSON snapshot round-trips its ``elf``/``from_headers`` fields exactly
        as saved, so the identical signal fires for an input that never touched
        the header parser; running the collector there would scan the *current*
        filesystem and overwrite the loaded snapshot's own recorded evidence
        with something unrelated. The typed pipeline and ``scan`` both add
        ``service.sniff_text_format(path) != "json"``, the exact predicate
        ``resolve_input`` itself branches on.

    *user_gcc_option_tokens*/*user_gcc_options* must be the caller's **own,
    pre-fold** explicit flags -- never the P0.3 L3->L2 fold's derived result.
    See :func:`user_define_flags`' own docstring: the auto-derived,
    per-header-matched build context must not be unioned snapshot-wide, which
    is exactly what feeding the folded tokens back in would do.
    """
    if not live_elf_parse or not snap.from_headers:
        return False
    collector_headers = expand_collector_headers(headers)
    if not collector_headers:
        return False
    compile_db_path = compile_db or compile_db_from_build_info(
        build_info, tuple(collector_headers)
    )
    if compile_db_path is None:
        return False
    attach_build_context(
        snap,
        compile_db_path,
        collector_headers,
        user_define_flags(tuple(user_gcc_option_tokens), user_gcc_options),
        source_filter=source_filter,
    )
    return True
