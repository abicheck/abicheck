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
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# ── active-define extraction ─────────────────────────────────────────────────


def _macro_name(body: str) -> str | None:
    name = body.split("=", 1)[0].strip()
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else None


def defines_from_flags(flags: Iterable[str], initial: set[str] | None = None) -> set[str]:
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
    tokens = list(flags)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        name: str | None = None
        add = True
        if tok in ("-D", "-U") and i + 1 < len(tokens):
            name = _macro_name(tokens[i + 1])
            add = tok == "-D"
            i += 1
        elif tok.startswith("-D") and len(tok) > 2:
            name = _macro_name(tok[2:])
        elif tok.startswith("-U") and len(tok) > 2:
            name = _macro_name(tok[2:])
            add = False
        if name is not None:
            active.add(name) if add else active.discard(name)
        i += 1
    return active


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
    selected TU would be intersected away."""
    file = entry.get("file")
    if not isinstance(file, str):
        return False
    directory = entry.get("directory")
    path = Path(file)
    if not path.is_absolute() and isinstance(directory, str):
        path = Path(directory) / path  # resolve like build_context.CompileEntry
    if fnmatch(str(path), pattern):
        return True
    if isinstance(directory, str):
        try:
            return fnmatch(str(path.relative_to(directory)), pattern)
        except ValueError:
            pass  # file not under directory — fall through to CWD-relative
    try:
        return fnmatch(str(path.relative_to(Path.cwd())), pattern)
    except ValueError:
        return False


def defines_from_compile_db(path: str | Path, source_filter: str | None = None) -> set[str]:
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


def _parse_field(decl: str) -> tuple[str, str, bool, int | None, bool, bool, bool] | None:
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

    __slots__ = ("kind", "name", "depth", "access", "qualified", "field_index", "recorded")

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
    include_guard = _include_guard_macro(lines)
    registry: dict[str, dict[str, dict[str, object]]] = {}
    # scope_stack: every open namespace/record body, outermost first. The
    # innermost record (if any) owns the fields on the current line; its
    # ``qualified`` name keys the registry.
    scope_stack: list[_Scope] = []
    # guard_stack entries: the positive macro for a simple ``#ifdef`` region,
    # ``None`` for an opaque region we must not record fields in
    # (negative/compound/else), or the ``_INCLUDE_GUARD`` sentinel for a
    # transparent file include guard (ignored when deciding to record).
    guard_stack: list[object] = []
    # Macros the header itself ``#undef``s (and has not since ``#define``d). A
    # guard whose macro is header-locally undefined is *inactive* in the real
    # build even if the compile DB defines it, so a field under it must not be
    # recorded as reconcilable — the build really pruned it (Codex review #498).
    locally_undefined: set[str] = set()
    # Macros ``#undef``'d or ``#define``'d **inside a branch the scanner cannot
    # evaluate** (a non-transparent conditional). Such an operation only fires when
    # its enclosing condition is active under the build's defines — which the
    # context-free scan does not know — so a field guarded by such a macro cannot be
    # resolved by the simple ``macro ∈ defines`` test. It is recorded with
    # ``ambiguous: True`` and the reconciler refuses to reconcile its type at all,
    # rather than risk adding back / pruning the wrong field (Codex review #498).
    conditionally_touched: set[str] = set()
    # Whether an ``#include`` has appeared so far. A scanned header cannot see what
    # an included file does to a macro — an ``#include "config.h"`` that ``#undef``s
    # the guard would make the real build prune a field this text scan still
    # records. So a guarded field *after* any include is marked ``ambiguous`` (its
    # guard state is unprovable without a preprocessor), and the reconciler keeps
    # its type rather than risk a wrong clear (Codex review #498, P1). Self-contained
    # headers (no preceding include) stay reconcilable.
    saw_include = False
    brace_depth = 0
    # pending: (kind, name, keyword) awaiting its opening brace. kind is "ns" or
    # "rec"; keyword is the record keyword (or "" for a namespace).
    pending: tuple[str, str | None, str] | None = None

    def _innermost_record() -> _Scope | None:
        return scope_stack[-1] if scope_stack and scope_stack[-1].kind == "rec" else None

    def _only_transparent() -> bool:
        # True when nothing but the (transparent) file include guard is open —
        # i.e. we are effectively at file top level.
        return all(g is _INCLUDE_GUARD for g in guard_stack)

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if _INCLUDE.match(line):
                saw_include = True  # subsequent guards may be altered by the include
                continue
            ifdef = _IFDEF.match(line)
            if_defined = None if ifdef else _IF_DEFINED.match(line)
            ifndef_m = _IFNDEF.match(line)
            if ifdef:
                guard_stack.append(ifdef.group(1))
            elif if_defined:
                guard_stack.append(if_defined.group(1) or if_defined.group(2))
            elif (
                ifndef_m is not None
                and ifndef_m.group(1) == include_guard
                and _INCLUDE_GUARD not in guard_stack
            ):
                guard_stack.append(_INCLUDE_GUARD)  # transparent file guard
            elif ifndef_m is not None:
                guard_stack.append(_NegGuard(ifndef_m.group(1)))  # simple #ifndef
            elif _PP_OPEN_OTHER.match(line):
                guard_stack.append(None)  # compound #if <expr>: not recordable
            elif _ELSE_ELIF.match(line):
                if guard_stack:
                    guard_stack[-1] = None  # the else/elif branch is not the guard
            elif _ENDIF.match(line):
                if guard_stack:
                    guard_stack.pop()
            elif (um := _UNDEF.match(line)) is not None:
                if _only_transparent():
                    # A **top-level** ``#undef`` genuinely undefines the macro for
                    # every build, so a later guard on it is never active — mark it
                    # header-locally undefined (its guarded fields are not recorded).
                    locally_undefined.add(um.group(1))
                else:
                    # An ``#undef`` **inside a branch we cannot evaluate** fires only
                    # when its condition is active under the build's defines. We
                    # cannot tell statically, so the macro is *ambiguous* — a field
                    # guarded by it must not be reconciled either way (Codex #498):
                    # ignoring the undef wrongly adds back a pruned field when the
                    # branch is active; honouring it wrongly prunes a present field
                    # when the branch is inactive.
                    conditionally_touched.add(um.group(1))
            elif (dm := _DEFINE.match(line)) is not None:
                if _only_transparent():
                    # A **top-level** ``#define`` reactivates a locally-undefined
                    # guard (it is defined for every build).
                    locally_undefined.discard(dm.group(1))
                else:
                    # A conditional ``#define`` makes the macro's state build-context
                    # dependent for the same reason — mark it ambiguous. (It does not
                    # reactivate a top-level ``#undef``; a build skipping the branch
                    # keeps the macro undefined.)
                    conditionally_touched.add(dm.group(1))
            continue

        rec_here = _innermost_record()

        # An access label (``public:``) alone on a line switches the current
        # access for the enclosing record body.
        if rec_here is not None and brace_depth == rec_here.depth + 1:
            am = _ACCESS_LABEL.match(line)
            if am:
                rec_here.access = am.group(1)
                continue

        # A namespace or record *definition* may open on this line (``struct
        # Name {`` / ``namespace api {``) or name first and brace next. A ``;``
        # before any ``{`` marks a forward declaration / variable — not a body
        # opener — so it is ignored.
        if pending is None:
            nsm = _NAMESPACE_OPEN.match(line)
            rm = None if nsm else _RECORD_OPEN.search(line)
            if nsm:
                rest = line[nsm.end() :]
                brace = rest.find("{")
                semi = rest.find(";")  # a namespace alias (``namespace x = y;``)
                if (brace != -1 and (semi == -1 or brace < semi)) or (
                    brace == -1 and semi == -1
                ):
                    pending = ("ns", nsm.group(1), "")
            elif rm:
                rest = line[rm.end() :]
                brace = rest.find("{")
                semi = rest.find(";")
                if (brace != -1 and (semi == -1 or brace < semi)) or (
                    brace == -1 and semi == -1
                ):
                    pending = ("rec", rm.group(2), rm.group(1))

        # Record a field: exactly one active guard — a positive ``#ifdef`` or a
        # negative ``#ifndef`` — with no opaque region and the transparent include
        # guard ignored, directly in a recordable body (not an anonymous namespace).
        positive = [g for g in guard_stack if isinstance(g, str)]
        negatives = [g for g in guard_stack if isinstance(g, _NegGuard)]
        has_opaque = any(g is None for g in guard_stack)
        guard: str | None = None
        is_negative = False
        if not has_opaque and len(positive) + len(negatives) == 1:
            if positive:
                guard = positive[0]
            else:
                guard, is_negative = negatives[0].macro, True
        if (
            rec_here is not None
            and rec_here.qualified is not None
            and brace_depth == rec_here.depth + 1
        ):
            fm = _FIELD.match(line)
            parsed = _parse_field(fm.group("decl")) if fm else None
            # Count **every** member-looking declaration in source order — not just
            # the ones ``_parse_field`` can decode. Array members (``int t[4];``),
            # default-initialised members (``int x = 0;``), and methods all advance
            # the position, so a guarded field *before* them is never wrongly marked
            # terminal (Codex review #498, P1). A line starting with ``}`` (the
            # record's own close) is excluded so a genuinely-last field keeps
            # ``is_last``. Over-counting a non-layout member only *suppresses* a
            # reconciliation (safe); under-counting could hide a real reorder.
            is_memberish = bool(line) and (line[0].isalpha() or line[0] == "_") and line.endswith(";")
            if parsed is not None or is_memberish:
                pos = rec_here.field_index
                rec_here.field_index += 1
                if parsed is not None and guard is not None and guard not in locally_undefined:
                    name, type_str, is_bitfield, bits, is_const, is_volatile, is_mutable = parsed
                    entry: dict[str, object] = {
                        "guard": guard,
                        "type": type_str,
                        "is_bitfield": is_bitfield,
                        "bitfield_bits": bits,
                        "access": rec_here.access,
                        "is_const": is_const,
                        "is_volatile": is_volatile,
                        "is_mutable": is_mutable,
                    }
                    if is_negative:
                        # An ``#ifndef GUARD`` field: observed context-free, but
                        # pruned by a build that *defines* GUARD.
                        entry["negative"] = True
                    if guard in conditionally_touched or saw_include:
                        # The guard macro is ``#undef``/``#define``d inside a branch
                        # we cannot evaluate, **or** an earlier ``#include`` may have
                        # altered it (the text scan cannot follow includes) → its
                        # state is unprovable. Flag the field so the reconciler keeps
                        # (never reconciles) findings on this type (Codex review #498).
                        entry["ambiguous"] = True
                    registry.setdefault(rec_here.qualified, {})[name] = entry
                    rec_here.recorded.append((entry, pos))

        for ch in line:
            if ch == "{":
                if pending is not None:
                    kind, sc_name, keyword = pending
                    if kind == "rec":
                        qualified = _record_qualified_name(scope_stack, sc_name or "")
                        sc = _Scope("rec", sc_name, brace_depth, qualified)
                        sc.access = "private" if keyword == "class" else "public"
                        scope_stack.append(sc)
                    else:
                        scope_stack.append(_Scope("ns", sc_name, brace_depth, None))
                    pending = None
                brace_depth += 1
            elif ch == ";" and pending is not None:
                # A ``;`` reached before the pending opener's ``{`` terminates a
                # *split* forward declaration (``struct S`` then ``;`` on the next
                # line). Clear ``pending`` so a later ``struct T { … }`` opens its
                # own scope instead of being keyed to ``S`` — otherwise ``T``'s
                # guarded fields would be misattributed to ``S`` and could
                # reconcile away a real ``S`` field change (Codex review #498).
                pending = None
            elif ch == "}":
                brace_depth = max(0, brace_depth - 1)
                if scope_stack and brace_depth == scope_stack[-1].depth:
                    closing = scope_stack.pop()
                    # Now that every member is counted, stamp ``is_last`` on the
                    # record's recorded guarded fields: True iff the field is the
                    # final data member in source order (Codex review #498). A
                    # reconciled field must be terminal so re-adding / pruning it
                    # cannot reorder a sibling.
                    last_index = closing.field_index - 1
                    for rec_entry, member_pos in closing.recorded:
                        rec_entry["is_last"] = member_pos == last_index

    return {rec: fields for rec, fields in registry.items() if fields}


def _has_forced_include(tokens: Iterable[str]) -> bool:
    """Whether *tokens* carry a forced-include flag (``-include`` / ``-imacros``).

    These preprocess a file before the translation unit, injecting macro state the
    per-file scan cannot see, so guarded fields must be treated as ``ambiguous``
    (Codex review #498)."""
    return any(t.startswith(("-include", "-imacros")) for t in tokens)


def _compile_db_has_forced_include(path: str | Path, source_filter: str | None) -> bool:
    """Whether any (filtered) compile-DB command carries a forced-include flag.

    Mirrors :func:`defines_from_compile_db`'s read/filter so a ``-include`` in the
    real build's command line — not just the user's ``--gcc-option`` — makes the
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
        compile_db is not None and _compile_db_has_forced_include(compile_db, source_filter)
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
